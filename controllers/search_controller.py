from fastapi import HTTPException
from typing import Optional, List, Dict, Any
from utils.database import get_db
from utils.helpers import serialize_doc
from models.user_models import UserRole
import re

class SearchController:
    @staticmethod
    async def global_search(
        query: str,
        search_type: Optional[str] = None,
        limit: int = 50,
        current_user: dict = None
    ):
        """
        Perform global search across users, coaches, courses, and branches
        
        Args:
            query: Search term
            search_type: Optional filter for specific entity type (users, coaches, courses, branches)
            limit: Maximum results per category
            current_user: Current authenticated user
        """
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        if not query or len(query.strip()) < 2:
            raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")
        
        db = get_db()
        results = {}
        
        # Create case-insensitive regex pattern
        search_pattern = {"$regex": re.escape(query.strip()), "$options": "i"}
        
        # Get current user role for access control
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                current_role = None
        
        # Search Users (Students, Coaches, etc.)
        if not search_type or search_type == "users":
            user_filter = {
                "$or": [
                    {"full_name": search_pattern},
                    {"email": search_pattern},
                    {"phone": search_pattern},
                    {"id": search_pattern}
                ]
            }
            
            # Apply role-based filtering
            if current_role == UserRole.COACH_ADMIN:
                if current_user.get("branch_id"):
                    user_filter["branch_id"] = current_user["branch_id"]
            elif current_role == UserRole.COACH:
                if current_user.get("branch_id"):
                    user_filter["branch_id"] = current_user["branch_id"]
                user_filter["role"] = UserRole.STUDENT.value
            
            users = await db.users.find(user_filter).limit(limit).to_list(length=limit)
            
            # Clean sensitive data
            for user in users:
                user.pop("password", None)
            
            results["users"] = {
                "data": serialize_doc(users),
                "count": len(users),
                "type": "users"
            }
        
        # Search Coaches
        if not search_type or search_type == "coaches":
            coach_filter = {
                "$or": [
                    {"full_name": search_pattern},
                    {"contact_info.email": search_pattern},
                    {"contact_info.phone": search_pattern},
                    {"id": search_pattern},
                    {"areas_of_expertise": search_pattern}
                ],
                "is_active": True
            }
            
            coaches = await db.coaches.find(coach_filter).limit(limit).to_list(length=limit)
            
            # Clean sensitive data
            for coach in coaches:
                if "contact_info" in coach and "password" in coach["contact_info"]:
                    coach["contact_info"].pop("password", None)
            
            results["coaches"] = {
                "data": serialize_doc(coaches),
                "count": len(coaches),
                "type": "coaches"
            }
        
        # Search Courses
        if not search_type or search_type == "courses":
            course_filter = {
                "$or": [
                    {"name": search_pattern},
                    {"description": search_pattern},
                    {"id": search_pattern},
                    {"difficulty_level": search_pattern}
                ],
                "settings.active": True
            }
            
            courses = await db.courses.find(course_filter).limit(limit).to_list(length=limit)
            
            results["courses"] = {
                "data": serialize_doc(courses),
                "count": len(courses),
                "type": "courses"
            }
        
        # Search Branches
        if not search_type or search_type == "branches":
            branch_filter = {
                "$or": [
                    {"branch.name": search_pattern},
                    {"branch.address.street": search_pattern},
                    {"branch.address.city": search_pattern},
                    {"branch.address.state": search_pattern},
                    {"id": search_pattern}
                ],
                "is_active": True
            }
            
            branches = await db.branches.find(branch_filter).limit(limit).to_list(length=limit)
            
            results["branches"] = {
                "data": serialize_doc(branches),
                "count": len(branches),
                "type": "branches"
            }
        
        # Calculate total results
        total_results = sum(category["count"] for category in results.values())
        
        return {
            "query": query,
            "total_results": total_results,
            "results": results,
            "message": f"Found {total_results} results for '{query}'"
        }
    
    @staticmethod
    async def search_users(
        query: str,
        role: Optional[UserRole] = None,
        branch_id: Optional[str] = None,
        limit: int = 50,
        current_user: dict = None
    ):
        """Search specifically in users with additional filters"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        if not query or len(query.strip()) < 2:
            raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")
        
        db = get_db()
        
        # Create search pattern
        search_pattern = {"$regex": re.escape(query.strip()), "$options": "i"}
        
        # Build filter query
        filter_query = {
            "$or": [
                {"full_name": search_pattern},
                {"email": search_pattern},
                {"phone": search_pattern},
                {"id": search_pattern}
            ]
        }
        
        # Apply role filter
        if role:
            filter_query["role"] = role.value
        
        # Apply branch filter
        if branch_id:
            filter_query["branch_id"] = branch_id
        
        # Apply role-based access control
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                current_role = None
        
        if current_role == UserRole.COACH_ADMIN:
            if current_user.get("branch_id"):
                filter_query["branch_id"] = current_user["branch_id"]
        elif current_role == UserRole.COACH:
            if current_user.get("branch_id"):
                filter_query["branch_id"] = current_user["branch_id"]
            filter_query["role"] = UserRole.STUDENT.value
        
        users = await db.users.find(filter_query).limit(limit).to_list(length=limit)
        total_count = await db.users.count_documents(filter_query)
        
        # Clean sensitive data
        for user in users:
            user.pop("password", None)
        
        return {
            "query": query,
            "users": serialize_doc(users),
            "total": total_count,
            "count": len(users),
            "message": f"Found {len(users)} users matching '{query}'"
        }

    @staticmethod
    async def search_coaches(
        query: str,
        area_of_expertise: Optional[str] = None,
        active_only: bool = True,
        limit: int = 50,
        current_user: dict = None
    ):
        """Search specifically in coaches"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        if not query or len(query.strip()) < 2:
            raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")

        db = get_db()

        # Create search pattern
        search_pattern = {"$regex": re.escape(query.strip()), "$options": "i"}

        # Build filter query
        filter_query = {
            "$or": [
                {"full_name": search_pattern},
                {"contact_info.email": search_pattern},
                {"contact_info.phone": search_pattern},
                {"id": search_pattern},
                {"areas_of_expertise": search_pattern}
            ]
        }

        if active_only:
            filter_query["is_active"] = True

        if area_of_expertise:
            filter_query["areas_of_expertise"] = {"$in": [area_of_expertise]}

        coaches = await db.coaches.find(filter_query).limit(limit).to_list(length=limit)
        total_count = await db.coaches.count_documents(filter_query)

        # Clean sensitive data
        for coach in coaches:
            if "contact_info" in coach and "password" in coach["contact_info"]:
                coach["contact_info"].pop("password", None)

        return {
            "query": query,
            "coaches": serialize_doc(coaches),
            "total": total_count,
            "count": len(coaches),
            "message": f"Found {len(coaches)} coaches matching '{query}'"
        }

    @staticmethod
    async def search_courses(
        query: str,
        category_id: Optional[str] = None,
        difficulty_level: Optional[str] = None,
        active_only: bool = True,
        limit: int = 50,
        current_user: dict = None
    ):
        """Search specifically in courses"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        if not query or len(query.strip()) < 2:
            raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")

        db = get_db()

        # Create search pattern
        search_pattern = {"$regex": re.escape(query.strip()), "$options": "i"}

        # Build filter query
        filter_query = {
            "$or": [
                {"name": search_pattern},
                {"description": search_pattern},
                {"id": search_pattern},
                {"difficulty_level": search_pattern}
            ]
        }

        if active_only:
            filter_query["settings.active"] = True

        if category_id:
            filter_query["category_id"] = category_id

        if difficulty_level:
            filter_query["difficulty_level"] = difficulty_level

        courses = await db.courses.find(filter_query).limit(limit).to_list(length=limit)
        total_count = await db.courses.count_documents(filter_query)

        return {
            "query": query,
            "courses": serialize_doc(courses),
            "total": total_count,
            "count": len(courses),
            "message": f"Found {len(courses)} courses matching '{query}'"
        }

    @staticmethod
    async def search_branches(
        query: str,
        active_only: bool = True,
        limit: int = 50,
        current_user: dict = None
    ):
        """Search specifically in branches"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        if not query or len(query.strip()) < 2:
            raise HTTPException(status_code=400, detail="Search query must be at least 2 characters")

        db = get_db()

        # Create search pattern
        search_pattern = {"$regex": re.escape(query.strip()), "$options": "i"}

        # Build filter query
        filter_query = {
            "$or": [
                {"branch.name": search_pattern},
                {"branch.address.street": search_pattern},
                {"branch.address.city": search_pattern},
                {"branch.address.state": search_pattern},
                {"id": search_pattern}
            ]
        }

        if active_only:
            filter_query["is_active"] = True

        branches = await db.branches.find(filter_query).limit(limit).to_list(length=limit)
        total_count = await db.branches.count_documents(filter_query)

        return {
            "query": query,
            "branches": serialize_doc(branches),
            "total": total_count,
            "count": len(branches),
            "message": f"Found {len(branches)} branches matching '{query}'"
        }

    @staticmethod
    async def search_students(
        query: Optional[str] = None,
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = None
    ):
        """
        Comprehensive student search with filtering by branch, course, and activity status
        Integrates with enrollments to provide course and branch relationships
        """
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build base filter for students
        student_filter = {"role": "student"}

        # Apply text search if query provided
        if query and len(query.strip()) >= 2:
            search_pattern = {"$regex": re.escape(query.strip()), "$options": "i"}
            student_filter["$or"] = [
                {"full_name": search_pattern},
                {"first_name": search_pattern},
                {"last_name": search_pattern},
                {"email": search_pattern},
                {"phone": search_pattern},
                {"id": search_pattern}
            ]

        # Apply active status filter
        if is_active is not None:
            student_filter["is_active"] = is_active

        # Apply date range filter (filter by user creation date)
        if start_date or end_date:
            date_filter = {}
            if start_date:
                try:
                    from datetime import datetime
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    date_filter["$gte"] = start_dt
                except (ValueError, AttributeError):
                    pass  # Skip invalid date format

            if end_date:
                try:
                    from datetime import datetime
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    # Add one day to include the end date
                    from datetime import timedelta
                    end_dt = end_dt + timedelta(days=1)
                    date_filter["$lt"] = end_dt
                except (ValueError, AttributeError):
                    pass  # Skip invalid date format

            if date_filter:
                student_filter["created_at"] = date_filter

        # Apply role-based access control
        current_role = current_user.get("role")
        if isinstance(current_role, str):
            try:
                current_role = UserRole(current_role)
            except ValueError:
                current_role = None

        if current_role == UserRole.COACH_ADMIN:
            if current_user.get("branch_id"):
                branch_id = current_user["branch_id"]  # Override branch filter for coach admin
        elif current_role == UserRole.COACH:
            if current_user.get("branch_id"):
                branch_id = current_user["branch_id"]  # Override branch filter for coach
        elif current_role == UserRole.BRANCH_MANAGER:
            # Branch managers can only see students from their managed branches
            managed_branches = current_user.get("managed_branches", [])
            if not managed_branches:
                # If no managed branches, return empty result
                return {
                    "query": query or "",
                    "students": [],
                    "total": 0,
                    "count": 0,
                    "message": "No branches assigned to this manager"
                }
            # For branch managers, we need to filter by enrollments in managed branches
            # We'll handle this filtering after getting the base student list

        # Get students matching the base criteria
        students_cursor = db.users.find(student_filter).skip(skip).limit(limit)
        students = await students_cursor.to_list(length=limit)

        # Get total count for pagination
        total_students = await db.users.count_documents(student_filter)

        # Handle branch manager filtering or regular branch/course filtering
        if current_role == UserRole.BRANCH_MANAGER:
            # For branch managers, filter students by enrollments in managed branches
            managed_branches = current_user.get("managed_branches", [])
            student_ids = [student["id"] for student in students]

            # Build enrollment filter for managed branches
            enrollment_filter = {
                "student_id": {"$in": student_ids},
                "branch_id": {"$in": managed_branches},
                "is_active": True
            }

            # Add additional filters if provided
            if course_id and course_id != "all":
                enrollment_filter["course_id"] = course_id

            # Get matching enrollments
            enrollments = await db.enrollments.find(enrollment_filter).to_list(length=None)

            # Filter students based on enrollment matches
            enrolled_student_ids = set(enrollment["student_id"] for enrollment in enrollments)
            students = [student for student in students if student["id"] in enrolled_student_ids]

        elif branch_id or course_id:
            # Regular branch or course filtering for other roles
            student_ids = [student["id"] for student in students]

            # Build enrollment filter
            enrollment_filter = {"student_id": {"$in": student_ids}, "is_active": True}
            if branch_id and branch_id != "all":
                enrollment_filter["branch_id"] = branch_id
            if course_id and course_id != "all":
                enrollment_filter["course_id"] = course_id

            # Get matching enrollments
            enrollments = await db.enrollments.find(enrollment_filter).to_list(length=None)

            # Filter students based on enrollment matches
            enrolled_student_ids = set(enrollment["student_id"] for enrollment in enrollments)
            students = [student for student in students if student["id"] in enrolled_student_ids]

        # Enrich student data with enrollment information
        enriched_students = []
        for student in students:
            # Get student's enrollments
            student_enrollments = await db.enrollments.find(
                {"student_id": student["id"], "is_active": True}
            ).to_list(length=None)

            # Get course and branch details for each enrollment
            courses = []
            branches = []

            for enrollment in student_enrollments:
                # Get course details
                course = await db.courses.find_one({"id": enrollment["course_id"]})
                if course:
                    b_ref = enrollment.get("batch_ref") or (student.get("course") or {}).get("batch_ref")
                    courses.append({
                        "id": course["id"],
                        "name": course.get("name", course.get("title", "Unknown Course")),
                        "code": course.get("code", ""),
                        "enrollment_date": enrollment["enrollment_date"],
                        "start_date": enrollment["start_date"],
                        "end_date": enrollment["end_date"],
                        "payment_status": enrollment.get("payment_status", "unknown"),
                        "batch_ref": b_ref,
                        "batch_name": enrollment.get("batch_name"),
                        "branch_id": enrollment.get("branch_id"),
                    })

                # Get branch details
                branch = await db.branches.find_one({"id": enrollment["branch_id"]})
                if branch:
                    branch_info = {
                        "id": branch["id"],
                        "name": branch.get("branch", {}).get("name", "Unknown Branch"),
                        "code": branch.get("branch", {}).get("code", ""),
                        "enrollment_date": enrollment["enrollment_date"]
                    }
                    # Avoid duplicate branches
                    if not any(b["id"] == branch_info["id"] for b in branches):
                        branches.append(branch_info)

            # Clean sensitive data
            student_data = student.copy()
            student_data.pop("password", None)

            # Add enriched data
            student_batch_ref = (
                (student_enrollments[0].get("batch_ref") if student_enrollments else None)
                or (student.get("course") or {}).get("batch_ref")
            )
            enriched_student = {
                **student_data,
                "batch_ref": student_batch_ref,
                "courses": courses,
                "branches": branches,
                "total_enrollments": len(student_enrollments),
                "active_enrollments": len([e for e in student_enrollments if e.get("is_active", True)])
            }

            enriched_students.append(enriched_student)

        return {
            "students": serialize_doc(enriched_students),
            "total": len(enriched_students),  # Actual filtered count
            "total_students": total_students,  # Total students before enrollment filtering
            "skip": skip,
            "limit": limit,
            "filters": {
                "query": query,
                "branch_id": branch_id,
                "course_id": course_id,
                "is_active": is_active
            },
            "message": f"Found {len(enriched_students)} students" + (f" matching '{query}'" if query else "")
        }
