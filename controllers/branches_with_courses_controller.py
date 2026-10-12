from fastapi import HTTPException
from typing import Optional, List, Dict, Any
from datetime import datetime

from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc

class BranchesWithCoursesController:
    @staticmethod
    async def get_branches_with_courses(
        branch_id: Optional[str] = None,
        status: Optional[str] = None,
        include_inactive: bool = False,
        current_user: dict = None
    ):
        """
        Get branches with their associated courses based on filtering criteria.

        Args:
            branch_id: Filter by specific branch ID, or "all" for all branches
            status: Filter by branch status ("active" or "inactive")
            include_inactive: Include inactive branches when no status filter is applied
            current_user: Current authenticated user

        Returns:
            Dict containing branches with courses, summary statistics, and filter info
        """
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build branch filter query
        branch_filter = {}

        # Apply role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            from utils.student_status_service import get_managed_branch_ids_for_user

            allowed_ids = await get_managed_branch_ids_for_user(db, current_user)
            managed_branches = (
                await db.branches.find({"id": {"$in": allowed_ids}}).to_list(length=None)
                if allowed_ids
                else []
            )

            if not managed_branches:
                return {
                    "message": "No branches assigned to this manager",
                    "branches": [],
                    "total": 0,
                    "summary": {
                        "total_branches": 0,
                        "total_courses": 0,
                        "total_students": 0,
                        "total_coaches": 0
                    },
                    "filters_applied": {
                        "branch_id": branch_id or "all",
                        "status": status or ("all" if include_inactive else "active"),
                        "include_inactive": include_inactive
                    }
                }

            # Get all branch IDs managed by this branch manager
            managed_branch_ids = [branch["id"] for branch in managed_branches]

            # If specific branch_id requested, verify it's managed by this branch manager
            if branch_id and branch_id != "all":
                if branch_id not in managed_branch_ids:
                    raise HTTPException(status_code=403, detail="You don't have permission to access this branch")
                branch_filter["id"] = branch_id
            else:
                # Filter to only managed branches
                branch_filter["id"] = {"$in": managed_branch_ids}
        else:
            # Apply branch_id filter for other roles
            if branch_id and branch_id != "all":
                branch_filter["id"] = branch_id

        # Apply status filter (default to active only unless include_inactive is true)
        if status:
            is_active = status.lower() == "active"
            branch_filter["is_active"] = is_active
        elif not include_inactive:
            # Default behavior: only show active branches unless explicitly requested
            branch_filter["is_active"] = True
        
        # Fetch branches from database
        branches_cursor = db.branches.find(branch_filter)
        branches = await branches_cursor.to_list(length=None)
        
        # If specific branch ID requested but not found
        if branch_id and branch_id != "all" and not branches:
            raise HTTPException(
                status_code=404,
                detail=f"Branch not found with ID: {branch_id}"
            )
        
        # If no branches match the filters
        if not branches:
            return {
                "message": f"No {status} branches found" if status else "No branches found matching criteria",
                "branches": [],
                "total": 0,
                "summary": {
                    "total_branches": 0,
                    "total_courses": 0,
                    "total_students": 0,
                    "total_coaches": 0
                },
                "filters_applied": {
                    "branch_id": branch_id or "all",
                    "status": status or ("all" if include_inactive else "active"),
                    "include_inactive": include_inactive
                }
            }
        
        # Enhance branches with courses and statistics
        enhanced_branches = []
        total_courses = 0
        total_students = 0
        total_coaches = 0
        
        for branch in branches:
            branch_id_current = branch["id"]
            
            # Get courses assigned to this branch
            course_ids = branch.get("assignments", {}).get("courses", [])
            branch_courses = []
            
            if course_ids:
                courses_cursor = db.courses.find({"id": {"$in": course_ids}})
                courses = await courses_cursor.to_list(length=None)
                
                for course in courses:
                    # Serialize and structure course data
                    course_data = serialize_doc(course)
                    
                    # Ensure all required fields are present with defaults
                    structured_course = {
                        "id": course_data.get("id", ""),
                        "title": course_data.get("title", ""),
                        "name": course_data.get("title", ""),  # Use title as name fallback
                        "code": course_data.get("code", ""),
                        "description": course_data.get("description", ""),
                        "difficulty_level": course_data.get("difficulty_level", ""),
                        "pricing": course_data.get("pricing", {
                            "currency": "INR",
                            "amount": 0,
                            "branch_specific_pricing": False
                        }),
                        "student_requirements": course_data.get("student_requirements", {
                            "max_students": 0,
                            "min_age": 0,
                            "max_age": 100,
                            "prerequisites": []
                        }),
                        "settings": course_data.get("settings", {
                            "active": True,
                            "offers_certification": False
                        }),
                        "created_at": course_data.get("created_at", datetime.utcnow().isoformat()),
                        "updated_at": course_data.get("updated_at", datetime.utcnow().isoformat())
                    }
                    branch_courses.append(structured_course)
            
            # Count coaches assigned to this branch
            coach_count = await db.coaches.count_documents({
                "branch_assignments": {"$in": [branch_id_current]},
                "is_active": True
            })
            
            # Count students enrolled in courses at this branch
            student_count = await db.enrollments.count_documents({
                "branch_id": branch_id_current,
                "status": "active"
            })
            
            # Count active courses for this branch
            active_courses = len([c for c in branch_courses if c.get("settings", {}).get("active", True)])
            
            # Serialize branch data
            branch_data = serialize_doc(branch)
            
            # Structure the enhanced branch data
            enhanced_branch = {
                "id": branch_data.get("id", ""),
                "branch": branch_data.get("branch", {}),
                "manager_id": branch_data.get("manager_id", ""),
                "is_active": branch_data.get("is_active", True),
                "operational_details": branch_data.get("operational_details", {}),
                "assignments": branch_data.get("assignments", {}),
                "bank_details": branch_data.get("bank_details", {}),
                "statistics": {
                    "coach_count": coach_count,
                    "student_count": student_count,
                    "course_count": len(branch_courses),
                    "active_courses": active_courses
                },
                "courses": branch_courses,
                "created_at": branch_data.get("created_at", datetime.utcnow().isoformat()),
                "updated_at": branch_data.get("updated_at", datetime.utcnow().isoformat())
            }
            
            enhanced_branches.append(enhanced_branch)
            
            # Update totals
            total_courses += len(branch_courses)
            total_students += student_count
            total_coaches += coach_count
        
        return {
            "message": "Branches with courses retrieved successfully",
            "branches": enhanced_branches,
            "total": len(enhanced_branches),
            "summary": {
                "total_branches": len(enhanced_branches),
                "total_courses": total_courses,
                "total_students": total_students,
                "total_coaches": total_coaches
            },
            "filters_applied": {
                "branch_id": branch_id or "all",
                "status": status or ("all" if include_inactive else "active"),
                "include_inactive": include_inactive
            }
        }

    @staticmethod
    async def get_branch_details_with_courses_and_coaches(
        branch_id: str,
        current_user: dict = None
    ):
        """
        Get detailed branch information including courses and coaches for branch manager dashboard.

        Args:
            branch_id: The specific branch ID to get details for
            current_user: Current authenticated user

        Returns:
            Dict containing detailed branch information with courses and coaches
        """
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Apply role-based access control
        current_role = current_user.get("role")
        if current_role == "branch_manager":
            from utils.student_status_service import get_managed_branch_ids_for_user

            allowed_ids = await get_managed_branch_ids_for_user(db, current_user)
            if branch_id not in allowed_ids:
                raise HTTPException(status_code=403, detail="You don't have permission to access this branch")
            managed_branch = await db.branches.find_one({"id": branch_id})
            if not managed_branch:
                raise HTTPException(status_code=404, detail="Branch not found")
        else:
            # For other roles, just check if branch exists
            managed_branch = await db.branches.find_one({"id": branch_id})
            if not managed_branch:
                raise HTTPException(status_code=404, detail="Branch not found")

        # Get branch details
        branch = managed_branch
        branch_data = serialize_doc(branch)

        # Get courses assigned to this branch
        course_ids = branch.get("assignments", {}).get("courses", [])
        courses = []

        if course_ids:
            courses_cursor = db.courses.find({"id": {"$in": course_ids}, "settings.active": True})
            course_docs = await courses_cursor.to_list(length=None)

            for course in course_docs:
                # Get instructor assignments (coaches assigned to this course at this branch)
                instructors = await db.coaches.find({
                    "assignment_details.courses": course["id"],
                    "branch_id": branch_id,
                    "is_active": True
                }).to_list(length=100)

                # Get student enrollment count for this course at this branch
                enrollment_count = await db.enrollments.count_documents({
                    "course_id": course["id"],
                    "branch_id": branch_id,
                    "is_active": True
                })

                # Create enhanced course object
                enhanced_course = serialize_doc(course)
                enhanced_course.update({
                    "name": course.get("title", course.get("name", "Unknown Course")),
                    "enrolled_students": enrollment_count,
                    "instructor_name": instructors[0].get("full_name", f"{instructors[0].get('first_name', '')} {instructors[0].get('last_name', '')}".strip()) if instructors else None,
                    "instructor_count": len(instructors),
                    "difficulty_level": course.get("difficulty_level", "Beginner")
                })
                courses.append(enhanced_course)

        # Get coaches assigned to this branch
        coaches = await db.coaches.find({
            "branch_id": branch_id,
            "is_active": True
        }).to_list(length=100)

        # Format coaches data
        formatted_coaches = []
        for coach in coaches:
            coach_data = serialize_doc(coach)

            # Safely extract contact info
            contact_info = coach_data.get("contact_info", {})
            if isinstance(contact_info, dict):
                email = contact_info.get("email", "")
                phone = contact_info.get("phone", "")
            else:
                email = ""
                phone = ""

            # Safely extract areas of expertise
            areas_of_expertise = coach_data.get("areas_of_expertise", [])
            if not isinstance(areas_of_expertise, list):
                areas_of_expertise = []

            formatted_coach = {
                "id": coach_data.get("id", ""),
                "full_name": coach_data.get("full_name", "Unknown Coach"),
                "contact_info": {
                    "email": email,
                    "phone": phone
                },
                "areas_of_expertise": areas_of_expertise,
                "is_active": coach_data.get("is_active", True)
            }
            formatted_coaches.append(formatted_coach)

        # Calculate statistics
        student_count = await db.enrollments.count_documents({
            "branch_id": branch_id,
            "is_active": True
        })

        # Get unique student count
        unique_students_pipeline = [
            {"$match": {"branch_id": branch_id, "is_active": True}},
            {"$group": {"_id": "$student_id"}},
            {"$count": "unique_students"}
        ]
        unique_students_result = await db.enrollments.aggregate(unique_students_pipeline).to_list(length=1)
        unique_student_count = unique_students_result[0]["unique_students"] if unique_students_result else 0

        # Calculate monthly revenue (placeholder - would need payment data)
        monthly_revenue = 0

        # Structure the response to match frontend expectations
        branch_details = {
            "id": branch_data.get("id", ""),
            "branch": {
                "name": branch_data.get("branch", {}).get("name", "Unknown Branch"),
                "address": {
                    "street": branch_data.get("branch", {}).get("address", {}).get("line1", "Address not available"),
                    "city": branch_data.get("branch", {}).get("address", {}).get("city", "Unknown City"),
                    "state": branch_data.get("branch", {}).get("address", {}).get("state", "Unknown State"),
                    "postal_code": branch_data.get("branch", {}).get("address", {}).get("pincode", "Unknown"),
                    "country": branch_data.get("branch", {}).get("address", {}).get("country", "Unknown Country")
                },
                "phone": branch_data.get("branch", {}).get("phone", "Phone not available"),
                "email": branch_data.get("branch", {}).get("email", "Email not available"),
                "operating_hours": branch_data.get("operational_details", {}).get("timings", [])
            },
            "is_active": branch_data.get("is_active", True),
            "created_at": branch_data.get("created_at", ""),
            "updated_at": branch_data.get("updated_at", ""),
            "total_students": unique_student_count,
            "total_coaches": len(formatted_coaches),
            "active_courses": len(courses),
            "monthly_revenue": monthly_revenue
        }

        return {
            "branch": branch_details,
            "courses": courses,
            "coaches": formatted_coaches,
            "statistics": {
                "total_students": unique_student_count,
                "total_coaches": len(formatted_coaches),
                "active_courses": len(courses),
                "total_enrollments": student_count,
                "monthly_revenue": monthly_revenue
            }
        }
