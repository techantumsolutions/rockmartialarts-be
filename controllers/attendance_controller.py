from fastapi import HTTPException, Depends
from typing import Optional, List
from datetime import datetime, timedelta, timezone, date as date_cls
from zoneinfo import ZoneInfo
import logging
import uuid
import csv
import io

logger = logging.getLogger(__name__)
COL_ATTENDANCE_AUDIT = "attendance_audit"

# Naive datetimes from Mongo are stored as UTC; display clock times in IST for Indian academy.
_IST = ZoneInfo("Asia/Kolkata")


def _attendance_calendar_day(value) -> Optional[str]:
    """Normalize Mongo attendance_date (datetime, date, or ISO string) to YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date_cls):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        return s[:10] if len(s) >= 10 else s
    return None


def _dt_to_ist_ampm(dt: Optional[datetime]) -> Optional[str]:
    """Format a stored instant as hh:mm AM/PM in Asia/Kolkata (IST)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.astimezone(_IST).strftime("%I:%M %p").strip()
    return s.upper()


def _validate_check_in_out_order(
    check_in: Optional[datetime], check_out: Optional[datetime]
) -> None:
    if check_in is not None and check_out is not None and check_out <= check_in:
        raise HTTPException(
            status_code=400,
            detail="Check-out time must be after check-in time.",
        )

from models.attendance_models import (
    AttendanceCreate, BiometricAttendance, Attendance, AttendanceMethod,
    CoachAttendance, CoachAttendanceCreate, BranchManagerAttendance, BranchManagerAttendanceCreate,
    AttendanceStatus, AttendanceMarkRequest
)
from models.user_models import UserRole
from utils.database import get_db
from utils.helpers import serialize_doc

class AttendanceController:
    @staticmethod
    async def get_attendance_reports(
        student_id: Optional[str] = None,
        coach_id: Optional[str] = None,
        course_id: Optional[str] = None,
        branch_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        current_user: dict = None,
        limit: Optional[int] = None,
    ):
        """Get attendance reports with filtering and role-based access control"""
        try:
            from utils.attendance_report_query import (
                apply_method_filter,
                apply_status_filter,
                normalize_status_value,
                summarize_records,
            )
            from utils.student_status_service import get_managed_branch_ids_for_user

            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Build filter query
            filter_query = {}
            
            # Apply role-based filtering for branch managers
            role = str((current_user or {}).get("role") or "").lower()
            if role == "branch_manager":
                managed_branch_ids = await get_managed_branch_ids_for_user(db, current_user)
                if not managed_branch_ids:
                    # Fallback to manager_id lookup used historically
                    branch_manager_id = current_user.get("id")
                    managed_branches = await db.branches.find(
                        {"manager_id": branch_manager_id, "is_active": True}
                    ).to_list(length=None)
                    managed_branch_ids = [branch["id"] for branch in (managed_branches or [])]
                if not managed_branch_ids:
                    return {
                        "attendance_records": [],
                        "total": 0,
                        "summary": {
                            "total": 0,
                            "present": 0,
                            "absent": 0,
                            "late": 0,
                            "biometric": 0,
                        },
                    }

                if branch_id:
                    if str(branch_id) not in {str(b) for b in managed_branch_ids}:
                        raise HTTPException(
                            status_code=403,
                            detail="Branch Manager cannot view attendance for other branches",
                        )
                    filter_query["branch_id"] = branch_id
                else:
                    filter_query["branch_id"] = {"$in": managed_branch_ids}

            # Apply additional filters
            if student_id:
                filter_query["student_id"] = student_id
            if coach_id:
                filter_query["coach_id"] = coach_id
            if course_id:
                filter_query["course_id"] = course_id
            if branch_id and role != "branch_manager":
                filter_query["branch_id"] = branch_id

            apply_status_filter(filter_query, status)
            apply_method_filter(filter_query, method)

            # Date range filtering
            if start_date and end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    # Inclusive end-of-day when date-only strings are used
                    if len(end_date) <= 10:
                        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                    filter_query["attendance_date"] = {"$gte": start_dt, "$lte": end_dt}
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format")

            fetch_limit = 1000
            if limit is not None:
                try:
                    fetch_limit = max(1, min(int(limit), 1000))
                except (TypeError, ValueError):
                    fetch_limit = 1000

            # Get attendance records with student and course information
            pipeline = [
                {"$match": filter_query},
                {"$sort": {"attendance_date": -1}},
                {"$limit": fetch_limit},
                {
                    "$lookup": {
                        "from": "users",
                        "localField": "student_id",
                        "foreignField": "id",
                        "as": "student_info"
                    }
                },
                {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$lookup": {
                        "from": "courses",
                        "localField": "course_id",
                        "foreignField": "id",
                        "as": "course_info"
                    }
                },
                {"$unwind": {"path": "$course_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$lookup": {
                        "from": "branches",
                        "localField": "branch_id",
                        "foreignField": "id",
                        "as": "branch_info"
                    }
                },
                {"$unwind": {"path": "$branch_info", "preserveNullAndEmptyArrays": True}},
                {
                    "$project": {
                        "id": 1,
                        "student_id": 1,
                        "student_name": {"$ifNull": ["$student_info.full_name", {"$concat": ["$student_info.first_name", " ", "$student_info.last_name"]}]},
                        "course_id": 1,
                        "course_name": {"$ifNull": ["$course_info.title", "$course_info.name"]},
                        "branch_id": 1,
                        "branch_name": {"$ifNull": ["$branch_info.branch.name", "$branch_info.name"]},
                        "attendance_date": 1,
                        "check_in_time": 1,
                        "check_out_time": 1,
                        "is_present": 1,
                        "status": 1,
                        "method": 1,
                        "notes": 1,
                        "created_at": 1,
                        "admin_adjusted": 1,
                        "attendance_modified_at": 1,
                        "attendance_modified_by": 1,
                        "updated_at": 1,
                    }
                },
                {"$sort": {"attendance_date": -1}}
            ]

            attendance_records = await db.attendance.aggregate(pipeline).to_list(length=fetch_limit)

            # Convert to serializable format
            serialized_records = []
            for record in attendance_records:
                serialized_record = {}
                for key, value in record.items():
                    if key == "_id":
                        continue
                    elif hasattr(value, 'isoformat'):
                        serialized_record[key] = value.isoformat()
                    else:
                        serialized_record[key] = value
                serialized_record["status"] = normalize_status_value(serialized_record)
                serialized_records.append(serialized_record)

            summary = summarize_records(serialized_records)
            return {
                "attendance_records": serialized_records,
                "total": len(serialized_records),
                "summary": summary,
                "filters": {
                    "student_id": student_id,
                    "coach_id": coach_id,
                    "course_id": course_id,
                    "branch_id": branch_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "status": status,
                    "method": method,
                },
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get attendance reports: {str(e)}")

    @staticmethod
    async def get_student_attendance(
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        current_user: dict = None
    ):
        """Get student attendance data with aggregated statistics"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Build base filter for attendance records
            filter_query = {}
            
            # Apply role-based filtering for branch managers
            managed_branch_ids = None
            if current_user and current_user.get("role") == "branch_manager":
                branch_manager_id = current_user.get("id")
                if not branch_manager_id:
                    raise HTTPException(status_code=403, detail="Branch manager ID not found")

                managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
                if not managed_branches:
                    return {"students": [], "total": 0}

                managed_branch_ids = [branch["id"] for branch in managed_branches]
                filter_query["branch_id"] = {"$in": managed_branch_ids}

            # Apply additional filters
            if branch_id and current_user.get("role") != "branch_manager":
                filter_query["branch_id"] = branch_id
            if course_id:
                filter_query["course_id"] = course_id

            # Date filtering - single date takes precedence over date range
            if date:
                try:
                    # Parse single date and create range for the entire day
                    date_dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    start_of_day = date_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_of_day = date_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                    filter_query["attendance_date"] = {"$gte": start_of_day, "$lte": end_of_day}
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format")
            elif start_date and end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    filter_query["attendance_date"] = {"$gte": start_dt, "$lte": end_dt}
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format")

            # If single date is provided, return format compatible with specific endpoints
            if date:
                # Get all students based on role-based filtering
                student_filter = {"role": "student", "is_active": True}
                students = await db.users.find(student_filter).to_list(length=None)

                # Get student IDs for enrollment filtering
                student_ids = [student["id"] for student in students]

                # Build enrollment filter based on role
                enrollment_filter = {"student_id": {"$in": student_ids}}
                if managed_branch_ids:
                    enrollment_filter["branch_id"] = {"$in": managed_branch_ids}
                elif branch_id:
                    enrollment_filter["branch_id"] = branch_id
                # Only apply course filtering when explicitly requested
                if course_id:
                    enrollment_filter["course_id"] = course_id

                # Get matching enrollments
                # For superadmin without any branch/course filter, show all students even if
                # enrollments collection is incomplete (prevents "empty attendance" UI).
                user_role = str(current_user.get("role") or "").lower() if current_user else ""
                must_filter_by_enrollment = bool(managed_branch_ids or branch_id or course_id)
                # Always load enrollments for course context (but don't always filter student list by them).
                enrollments = await db.enrollments.find(enrollment_filter).to_list(length=None)
                if must_filter_by_enrollment or user_role in ["branch_manager", "coach", "coach_admin"]:
                    enrolled_student_ids = set(
                        enrollment.get("student_id")
                        for enrollment in enrollments
                        if enrollment.get("student_id")
                    )
                    students = [student for student in students if student.get("id") in enrolled_student_ids]

                # Get attendance records for the specific date
                attendance_records = await db.attendance.find(filter_query).to_list(length=None)

                # Create a map of student attendance
                attendance_map = {}
                for record in attendance_records:
                    student_id = record.get("student_id")
                    if student_id:
                        # Use stored status if available, otherwise fall back to is_present logic
                        stored_status = record.get("status")
                        if stored_status:
                            status = stored_status
                        else:
                            # Fallback for old records without status field
                            status = "present" if record.get("is_present") else "absent"

                        attendance_map[student_id] = {
                            "id": record.get("id"),
                            "attendance_id": record.get("id"),
                            "status": status,
                            "check_in_time": record.get("check_in_time"),
                            "check_out_time": record.get("check_out_time"),
                            "notes": record.get("notes", ""),
                            "marked_by": record.get("marked_by"),
                            "admin_adjusted": record.get("admin_adjusted", False),
                            "attendance_modified_at": record.get("attendance_modified_at"),
                            "attendance_modified_by": record.get("attendance_modified_by"),
                            "correction_reason": record.get("correction_reason"),
                        }

                # Combine student data with attendance and course information
                # Prefetch courses referenced by the enrollments for this result set.
                course_ids = list(
                    {
                        e.get("course_id")
                        for e in enrollments
                        if e.get("course_id")
                    }
                )
                course_docs = []
                if course_ids:
                    course_docs = await db.courses.find({"id": {"$in": course_ids}}).to_list(length=None)
                course_by_id = {c.get("id"): c for c in course_docs if c.get("id")}

                result_students = []
                for student in students:
                    student_id = student.get("id")
                    attendance_info = attendance_map.get(student_id, {
                        "id": None,
                        "attendance_id": None,
                        "status": "not_marked",
                        "check_in_time": None,
                        "check_out_time": None,
                        "notes": "",
                        "marked_by": None,
                        "admin_adjusted": False,
                        "attendance_modified_at": None,
                        "attendance_modified_by": None,
                        "correction_reason": None,
                    })

                    # Get student's enrollments for course information
                    student_enrollments = [e for e in enrollments if e.get("student_id") == student_id]
                    courses = []

                    for enrollment in student_enrollments:
                        # Get course details
                        course = course_by_id.get(enrollment.get("course_id"))
                        if course:
                            courses.append({
                                "id": course["id"],
                                "name": course.get("name", course.get("title", "Unknown Course")),
                                "course_id": course["id"],
                                "course_name": course.get("name", course.get("title", "Unknown Course"))
                            })

                    result_students.append({
                        "id": student_id,
                        "full_name": student.get("full_name", "Unknown"),
                        "email": student.get("email", ""),
                        "branch_id": student_enrollments[0]["branch_id"] if student_enrollments else None,
                        "courses": courses,
                        "attendance": attendance_info
                    })

                return {
                    "students": result_students,
                    "date": date,
                    "total": len(result_students),
                    "attendance_marked": len([s for s in result_students if s["attendance"]["status"] != "absent"])
                }

            else:
                # Original aggregated statistics format for date ranges or no date filter
                pipeline = [
                    {"$match": filter_query},
                    {
                        "$group": {
                            "_id": "$student_id",
                            "total_sessions": {"$sum": 1},
                            "present_sessions": {"$sum": {"$cond": ["$is_present", 1, 0]}},
                            "last_attendance": {"$max": "$attendance_date"},
                            "branch_ids": {"$addToSet": "$branch_id"},
                            "course_ids": {"$addToSet": "$course_id"}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "users",
                            "localField": "_id",
                            "foreignField": "id",
                            "as": "student_info"
                        }
                    },
                    {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
                    {
                        "$project": {
                            "student_id": "$_id",
                            "student_name": {"$ifNull": ["$student_info.full_name", {"$concat": ["$student_info.first_name", " ", "$student_info.last_name"]}]},
                            "email": "$student_info.email",
                            "phone": "$student_info.phone",
                            "total_sessions": 1,
                            "present_sessions": 1,
                            "attendance_percentage": {
                                "$multiply": [
                                    {"$divide": ["$present_sessions", "$total_sessions"]},
                                    100
                                ]
                            },
                            "last_attendance": 1,
                            "branch_count": {"$size": "$branch_ids"},
                            "course_count": {"$size": "$course_ids"}
                        }
                    },
                    {"$sort": {"attendance_percentage": -1}}
                ]

                student_attendance = await db.attendance.aggregate(pipeline).to_list(length=1000)

                # Convert to serializable format
                serialized_students = []
                for student in student_attendance:
                    serialized_student = {}
                    for key, value in student.items():
                        if key == "_id":
                            continue
                        elif hasattr(value, 'isoformat'):
                            serialized_student[key] = value.isoformat()
                        else:
                            serialized_student[key] = value
                    serialized_students.append(serialized_student)

                return {
                    "students": serialized_students,
                    "total": len(serialized_students)
                }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get student attendance: {str(e)}")

    @staticmethod
    async def get_coach_attendance(
        branch_id: Optional[str] = None,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        current_user: dict = None
    ):
        """Get coach attendance data with unified attendance system"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Build filter query for coaches - use coaches collection instead of users
            coach_filter = {"is_active": True}

            # Apply role-based filtering for branch managers
            managed_branch_ids = None
            if current_user and current_user.get("role") == "branch_manager":
                branch_manager_id = current_user.get("id")
                if not branch_manager_id:
                    raise HTTPException(status_code=403, detail="Branch manager ID not found")

                managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
                if not managed_branches:
                    return {"coaches": [], "total": 0}

                managed_branch_ids = [branch["id"] for branch in managed_branches]
                coach_filter["branch_id"] = {"$in": managed_branch_ids}

            if branch_id and current_user.get("role") != "branch_manager":
                coach_filter["branch_id"] = branch_id

            # Get coaches from the coaches collection (not users collection)
            coaches = await db.coaches.find(coach_filter).to_list(length=1000)

            # If single date is provided, return format compatible with branch manager attendance page
            if date:
                try:
                    # Parse single date and create range for the entire day
                    date_dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    start_of_day = date_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_of_day = date_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format")

                # Build attendance filter for the specific date
                attendance_filter = {
                    "attendance_date": {"$gte": start_of_day, "$lte": end_of_day}
                }

                # Apply branch filtering to attendance records
                if managed_branch_ids:
                    attendance_filter["branch_id"] = {"$in": managed_branch_ids}
                elif branch_id:
                    attendance_filter["branch_id"] = branch_id

                # Get attendance records for the specific date from unified attendance collection
                # Look for coach attendance records (where coach_id is set instead of student_id)
                attendance_records = await db.coach_attendance.find(attendance_filter).to_list(length=1000)

                # Create a map of coach attendance
                attendance_map = {}
                for record in attendance_records:
                    coach_id = record.get("coach_id")
                    if coach_id:
                        # Use stored status if available, otherwise fall back to is_present logic
                        stored_status = record.get("status")
                        if stored_status:
                            status = stored_status
                        else:
                            # Fallback for old records without status field
                            status = "present" if record.get("is_present") else "absent"

                        attendance_map[coach_id] = {
                            "status": status,
                            "check_in_time": record.get("check_in_time"),
                            "check_out_time": record.get("check_out_time"),
                            "notes": record.get("notes", ""),
                            "marked_by": record.get("marked_by")
                        }

                # Combine coach data with attendance information
                result_coaches = []
                for coach in coaches:
                    coach_id = coach.get("id")
                    attendance_info = attendance_map.get(coach_id, {
                        "status": "not_marked",  # Default to not_marked if no record
                        "check_in_time": None,
                        "check_out_time": None,
                        "notes": "",
                        "marked_by": None
                    })

                    # Get branch information
                    branch_name = "Unknown Branch"
                    if coach.get("branch_id"):
                        branch = await db.branches.find_one({"id": coach["branch_id"]})
                        if branch:
                            branch_name = branch.get("branch", {}).get("name") or branch.get("name", "Unknown Branch")

                    # Extract coach information from coaches collection structure
                    coach_name = coach.get("full_name") or f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip()
                    if not coach_name.strip():
                        # Fallback to personal_info if direct fields are empty
                        personal_info = coach.get("personal_info", {})
                        coach_name = f"{personal_info.get('first_name', '')} {personal_info.get('last_name', '')}".strip()

                    # Get contact information
                    email = coach.get("email") or coach.get("contact_info", {}).get("email", "")
                    phone = coach.get("phone") or coach.get("contact_info", {}).get("phone", "")

                    # Get expertise information
                    expertise = coach.get("areas_of_expertise", []) or coach.get("expertise", [])

                    result_coaches.append({
                        "coach_id": coach_id,
                        "id": coach_id,  # For compatibility
                        "coach_name": coach_name or "Unknown Coach",
                        "full_name": coach_name or "Unknown Coach",
                        "email": email,
                        "phone": phone,
                        "branch_id": coach.get("branch_id"),
                        "branch_name": branch_name,
                        "expertise": expertise,
                        "attendance": attendance_info
                    })

                return {
                    "coaches": result_coaches,
                    "date": date,
                    "total": len(result_coaches),
                    "attendance_marked": len([c for c in result_coaches if c["attendance"]["status"] != "not_marked"])
                }

            # For date range queries, return the original format with statistics
            else:
                coach_attendance_data = []
                for coach in coaches:
                    coach_id = coach.get("id")

                    # Build attendance filter
                    attendance_filter = {"coach_id": coach_id}

                    # Date filtering for range queries
                    if start_date and end_date:
                        try:
                            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                            attendance_filter["attendance_date"] = {"$gte": start_dt, "$lte": end_dt}
                        except ValueError:
                            raise HTTPException(status_code=400, detail="Invalid date format")

                    # Get coach attendance records from coach_attendance collection
                    attendance_records = await db.coach_attendance.find(attendance_filter).to_list(length=1000)

                    # Calculate statistics
                    total_days = len(attendance_records)
                    present_days = sum(1 for record in attendance_records if record.get("is_present", False))
                    attendance_percentage = (present_days / total_days * 100) if total_days > 0 else 0

                    # Get latest attendance
                    latest_attendance = None
                    if attendance_records:
                        latest_record = max(attendance_records, key=lambda x: x.get("attendance_date", datetime.min))
                        latest_attendance = latest_record.get("attendance_date")

                    coach_data = {
                        "coach_id": coach_id,
                        "coach_name": coach.get("full_name") or f"{coach.get('first_name', '')} {coach.get('last_name', '')}".strip(),
                        "email": coach.get("email"),
                        "phone": coach.get("phone"),
                        "branch_id": coach.get("branch_id"),
                        "expertise": coach.get("expertise", []),
                        "total_days": total_days,
                        "present_days": present_days,
                        "attendance_percentage": round(attendance_percentage, 2),
                        "last_attendance": latest_attendance.isoformat() if latest_attendance else None
                    }

                    coach_attendance_data.append(coach_data)

                return {
                    "coaches": coach_attendance_data,
                    "total": len(coach_attendance_data)
                }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get coach attendance: {str(e)}")

    @staticmethod
    async def get_attendance_stats(branch_id: Optional[str] = None, date: Optional[str] = None, current_user: dict = None):
        """Get comprehensive attendance statistics with enhanced data aggregation and date filtering"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Build filter query
            filter_query = {}
            managed_branch_ids = None

            # Parse date if provided
            target_date = None
            if date:
                try:
                    target_date = datetime.fromisoformat(date.replace('Z', '+00:00'))
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format")

            # Apply role-based filtering for branch managers
            if current_user and current_user.get("role") == "branch_manager":
                branch_manager_id = current_user.get("id")
                if not branch_manager_id:
                    raise HTTPException(status_code=403, detail="Branch manager ID not found")

                managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
                if not managed_branches:
                    return {
                        "total_students": 0,
                        "total_coaches": 0,
                        "student_present_today": 0,
                        "student_absent_today": 0,
                        "student_late_today": 0,
                        "coach_present_today": 0,
                        "coach_absent_today": 0,
                        "coach_late_today": 0,
                        "overall_attendance_rate": 0,
                        "student_attendance_rate": 0,
                        "coach_attendance_rate": 0
                    }

                managed_branch_ids = [branch["id"] for branch in managed_branches]
                filter_query["branch_id"] = {"$in": managed_branch_ids}

            if branch_id and current_user.get("role") != "branch_manager":
                filter_query["branch_id"] = branch_id

            # Determine the date to use for attendance filtering
            if target_date:
                # Use the provided date
                attendance_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                next_day = attendance_date + timedelta(days=1)
            else:
                # Use today's date as default
                attendance_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                next_day = attendance_date + timedelta(days=1)

            # Get all students - for superadmin, show all students regardless of enrollment
            # For branch managers, only show students from their managed branches
            student_filter = {"role": "student", "is_active": True}

            if managed_branch_ids:
                # For branch managers, get students from their branches via enrollments
                enrollments = await db.enrollments.find({"branch_id": {"$in": managed_branch_ids}}).to_list(length=None)
                if enrollments:
                    enrolled_student_ids = list(set(e["student_id"] for e in enrollments))
                    student_filter["id"] = {"$in": enrolled_student_ids}
                else:
                    # If no enrollments exist, show no students for branch managers
                    student_filter["id"] = {"$in": []}
            elif branch_id:
                # For specific branch, get students from that branch via enrollments
                enrollments = await db.enrollments.find({"branch_id": branch_id}).to_list(length=None)
                if enrollments:
                    enrolled_student_ids = list(set(e["student_id"] for e in enrollments))
                    student_filter["id"] = {"$in": enrolled_student_ids}
                else:
                    # If no enrollments exist, show no students for specific branch
                    student_filter["id"] = {"$in": []}
            # For superadmin without branch filter, show all students regardless of enrollment status

            all_students = await db.users.find(student_filter).to_list(length=None)
            total_students = len(all_students)

            # Get attendance records for the specified date
            attendance_filter = {**filter_query, "attendance_date": {"$gte": attendance_date, "$lt": next_day}}
            attendance_records = await db.attendance.find(attendance_filter).to_list(length=None)

            # Calculate student attendance statistics for the specified date
            student_present_today = len([a for a in attendance_records if a.get("status") == "present" or (a.get("is_present") and not a.get("status"))])
            student_absent_today = len([a for a in attendance_records if a.get("status") == "absent" or (not a.get("is_present") and a.get("status") != "late")])
            student_late_today = len([a for a in attendance_records if a.get("status") == "late"])

            # Calculate students with no attendance record (considered absent)
            students_with_records = set(a.get("student_id") for a in attendance_records if a.get("student_id"))
            all_student_ids = set(s.get("id") for s in all_students if s.get("id"))
            students_without_records = all_student_ids - students_with_records

            # Add students without records to absent count
            student_absent_today += len(students_without_records)

            # Get coach statistics - try coaches collection first, then users collection
            coach_filter = {"is_active": True}
            if managed_branch_ids:
                coach_filter["branch_id"] = {"$in": managed_branch_ids}
            elif branch_id:
                coach_filter["branch_id"] = branch_id

            # Try to get coaches from dedicated coaches collection
            all_coaches = await db.coaches.find(coach_filter).to_list(length=None)

            # If no coaches in coaches collection, try users collection
            if not all_coaches:
                user_coach_filter = {"role": "coach", "is_active": True}
                if managed_branch_ids:
                    user_coach_filter["branch_id"] = {"$in": managed_branch_ids}
                elif branch_id:
                    user_coach_filter["branch_id"] = branch_id
                all_coaches = await db.users.find(user_coach_filter).to_list(length=None)

            total_coaches = len(all_coaches)

            # Get coach attendance records for the specified date
            coach_attendance_filter = {**filter_query, "attendance_date": {"$gte": attendance_date, "$lt": next_day}}
            coach_attendance_records = await db.coach_attendance.find(coach_attendance_filter).to_list(length=None)

            # Calculate coach attendance statistics
            coach_present_today = len([a for a in coach_attendance_records if a.get("status") == "present" or (a.get("is_present") and not a.get("status"))])
            coach_absent_today = len([a for a in coach_attendance_records if a.get("status") == "absent" or (not a.get("is_present") and a.get("status") != "late")])
            coach_late_today = len([a for a in coach_attendance_records if a.get("status") == "late"])

            # Calculate coaches with no attendance record (considered absent)
            coaches_with_records = set(a.get("coach_id") for a in coach_attendance_records if a.get("coach_id"))
            all_coach_ids = set(c.get("id") for c in all_coaches if c.get("id"))
            coaches_without_records = all_coach_ids - coaches_with_records

            # Add coaches without records to absent count
            coach_absent_today += len(coaches_without_records)

            # Calculate attendance rates
            student_attendance_rate = 0
            if total_students > 0:
                student_attendance_rate = ((student_present_today + student_late_today) / total_students) * 100

            coach_attendance_rate = 0
            if total_coaches > 0:
                coach_attendance_rate = ((coach_present_today + coach_late_today) / total_coaches) * 100

            overall_attendance_rate = 0
            total_people = total_students + total_coaches
            if total_people > 0:
                total_present = student_present_today + student_late_today + coach_present_today + coach_late_today
                overall_attendance_rate = (total_present / total_people) * 100

            return {
                "total_students": total_students,
                "total_coaches": total_coaches,
                "student_present_today": student_present_today,
                "student_absent_today": student_absent_today,
                "student_late_today": student_late_today,
                "coach_present_today": coach_present_today,
                "coach_absent_today": coach_absent_today,
                "coach_late_today": coach_late_today,
                "overall_attendance_rate": round(overall_attendance_rate, 1),
                "student_attendance_rate": round(student_attendance_rate, 1),
                "coach_attendance_rate": round(coach_attendance_rate, 1)
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get attendance stats: {str(e)}")

    @staticmethod
    async def mark_manual_attendance(attendance_data: AttendanceCreate, current_user: dict):
        """Manually mark attendance"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Create attendance record
            attendance = Attendance(
                **attendance_data.dict(),
                check_in_time=datetime.utcnow(),
                marked_by=current_user["id"]
            )

            await db.attendance.insert_one(attendance.dict())
            return {"message": "Attendance marked successfully", "attendance_id": attendance.id}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to mark attendance: {str(e)}")

    @staticmethod
    async def biometric_attendance(attendance_data: BiometricAttendance):
        """
        Record attendance from biometric device (M09-S03).
        Delegates to secure ingest pipeline (device registry + mapping + dedupe).
        """
        try:
            from utils.biometric_attendance_ingest import (
                BiometricIngestRequest,
                ingest_events,
                legacy_biometric_to_event,
            )

            event = legacy_biometric_to_event(
                attendance_data.device_id,
                attendance_data.biometric_id,
                attendance_data.timestamp,
            )
            out = await ingest_events(BiometricIngestRequest(events=[event], event=None))
            result = (out.get("results") or [{}])[0]
            if result.get("status") == "processed":
                return {
                    "message": result.get("message") or "Attendance marked successfully",
                    "attendance_id": result.get("attendance_id"),
                    "ingest": result,
                }
            if result.get("status") == "duplicate":
                return {
                    "message": result.get("message") or "Duplicate punch ignored",
                    "attendance_id": result.get("attendance_id"),
                    "ingest": result,
                }
            # unmatched / rejected
            detail = result.get("message") or "Biometric punch could not be applied"
            raise HTTPException(status_code=404, detail=detail)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to record biometric attendance: {str(e)}")

    @staticmethod
    async def export_attendance_reports(
        student_id: Optional[str] = None,
        coach_id: Optional[str] = None,
        course_id: Optional[str] = None,
        branch_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        format: str = "csv",
        status: Optional[str] = None,
        method: Optional[str] = None,
        current_user: dict = None
    ):
        """Export attendance reports (csv or excel)"""
        try:
            from utils.attendance_report_query import build_attendance_export

            attendance_data = await AttendanceController.get_attendance_reports(
                student_id,
                coach_id,
                course_id,
                branch_id,
                start_date,
                end_date,
                status,
                method,
                current_user,
            )
            export = build_attendance_export(
                attendance_data.get("attendance_records") or [],
                format=format,
            )
            export["summary"] = attendance_data.get("summary")
            export["filters"] = attendance_data.get("filters")
            return export

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to export attendance reports: {str(e)}")

    @staticmethod
    async def get_coach_students(coach_id: str, current_user: dict):
        """Get students assigned to a specific coach"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Verify coach access - coaches can only see their own students
            if current_user.get("role") == "coach" and current_user.get("id") != coach_id:
                raise HTTPException(status_code=403, detail="Access denied: Can only view your own students")

            # Get coach information
            coach = await db.coaches.find_one({"id": coach_id, "is_active": True})
            if not coach:
                # Try users collection for coaches
                coach = await db.users.find_one({"id": coach_id, "role": "coach", "is_active": True})
                if not coach:
                    raise HTTPException(status_code=404, detail="Coach not found")

            print(f"🔍 DEBUG: Found coach: {coach.get('id')} - {coach.get('full_name', 'Unknown')}")

            coach_branch_id = coach.get("branch_id")
            print(f"🔍 DEBUG: Coach branch_id: {coach_branch_id}")

            if not coach_branch_id:
                print("⚠️ DEBUG: Coach has no branch_id assigned")
                return {"students": [], "total": 0, "debug_info": "Coach has no branch assignment"}

            # Get courses assigned to this coach
            assigned_courses = []
            if "assignment_details" in coach and coach["assignment_details"]:
                assigned_courses = coach["assignment_details"].get("courses", [])
                print(f"🔍 DEBUG: Coach assigned courses: {assigned_courses}")
            else:
                print("⚠️ DEBUG: Coach has no assignment_details")

            # If no specific course assignments, get all courses in the coach's branch
            if not assigned_courses:
                print(f"🔍 DEBUG: No specific course assignments, getting all courses in branch {coach_branch_id}")
                branch_courses = await db.courses.find({"branch_id": coach_branch_id, "is_active": True}).to_list(length=None)
                assigned_courses = [course["id"] for course in branch_courses]
                print(f"🔍 DEBUG: Found {len(branch_courses)} courses in branch: {assigned_courses}")

            if not assigned_courses:
                print("⚠️ DEBUG: No courses found for this coach")
                return {"students": [], "total": 0, "debug_info": "No courses assigned to coach or branch"}

            # Get students enrolled in these courses
            print(f"🔍 DEBUG: Searching for enrollments with course_ids: {assigned_courses}, branch_id: {coach_branch_id}")
            enrollments = await db.enrollments.find({
                "course_id": {"$in": assigned_courses},
                "branch_id": coach_branch_id,
                "is_active": True
            }).to_list(length=None)

            print(f"🔍 DEBUG: Found {len(enrollments)} enrollments")

            student_ids = [enrollment["student_id"] for enrollment in enrollments]
            print(f"🔍 DEBUG: Student IDs from enrollments: {student_ids}")

            if not student_ids:
                # Try without branch_id filter as fallback
                print("🔍 DEBUG: No students found with branch filter, trying without branch filter...")
                fallback_enrollments = await db.enrollments.find({
                    "course_id": {"$in": assigned_courses},
                    "is_active": True
                }).to_list(length=None)

                print(f"🔍 DEBUG: Fallback found {len(fallback_enrollments)} enrollments")

                if fallback_enrollments:
                    student_ids = [enrollment["student_id"] for enrollment in fallback_enrollments]
                    print(f"🔍 DEBUG: Fallback student IDs: {student_ids}")
                else:
                    return {"students": [], "total": 0, "debug_info": "No enrollments found for assigned courses"}

            # Get student details with enrollment information
            pipeline = [
                {"$match": {"id": {"$in": student_ids}, "is_active": True}},
                {
                    "$lookup": {
                        "from": "enrollments",
                        "let": {"student_id": "$id"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$student_id", "$$student_id"]},
                                            {"$in": ["$course_id", assigned_courses]},
                                            {"$eq": ["$is_active", True]}
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "enrollments"
                    }
                },
                {
                    "$lookup": {
                        "from": "courses",
                        "localField": "enrollments.course_id",
                        "foreignField": "id",
                        "as": "courses"
                    }
                },
                {
                    "$project": {
                        "id": 1,
                        "full_name": {"$ifNull": ["$full_name", {"$concat": ["$first_name", " ", "$last_name"]}]},
                        "email": 1,
                        "phone": 1,
                        "enrollments": 1,
                        "courses": 1,
                        "is_active": 1
                    }
                }
            ]

            students = await db.users.aggregate(pipeline).to_list(length=None)
            print(f"🔍 DEBUG: Found {len(students)} students after aggregation")

            # Serialize the results
            serialized_students = []
            for student in students:
                student_data = serialize_doc(student)
                serialized_students.append(student_data)
                print(f"🔍 DEBUG: Student: {student_data.get('id')} - {student_data.get('full_name', 'Unknown')}")

            result = {
                "students": serialized_students,
                "total": len(serialized_students),
                "coach_id": coach_id,
                "branch_id": coach_branch_id,
                "debug_info": {
                    "assigned_courses": assigned_courses,
                    "enrollments_found": len(enrollments) if 'enrollments' in locals() else 0,
                    "student_ids": student_ids
                }
            }

            print(f"🔍 DEBUG: Final result: {len(serialized_students)} students")
            return result

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get coach students: {str(e)}")

    @staticmethod
    async def mark_coach_attendance(attendance_data: CoachAttendanceCreate, current_user: dict):
        """Mark attendance for a coach using unified attendance system"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Verify permissions
            user_role = current_user.get("role")
            if user_role not in ["super_admin", "superadmin", "branch_manager"]:
                raise HTTPException(status_code=403, detail="Insufficient permissions to mark coach attendance")

            # If branch manager, verify they can mark attendance for this coach
            if user_role == "branch_manager":
                branch_manager_id = current_user.get("id")
                managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)
                managed_branch_ids = [branch["id"] for branch in managed_branches]

                if attendance_data.branch_id not in managed_branch_ids:
                    raise HTTPException(status_code=403, detail="Cannot mark attendance for coaches outside your managed branches")

            # Verify coach exists and is in the specified branch
            coach = await db.coaches.find_one({"id": attendance_data.coach_id, "is_active": True})
            if not coach:
                coach = await db.users.find_one({"id": attendance_data.coach_id, "role": "coach", "is_active": True})
                if not coach:
                    raise HTTPException(status_code=404, detail="Coach not found")

            # Enhanced deduplication: Check if attendance already exists for this exact date
            attendance_date_start = attendance_data.attendance_date.replace(hour=0, minute=0, second=0, microsecond=0)
            attendance_date_end = attendance_data.attendance_date.replace(hour=23, minute=59, second=59, microsecond=999999)

            print(f"🔍 Checking for existing coach attendance: coach_id={attendance_data.coach_id}, date_range={attendance_date_start} to {attendance_date_end}")

            # Find the most recent existing attendance record for this date
            existing = await db.coach_attendance.find_one(
                {
                    "coach_id": attendance_data.coach_id,
                    "branch_id": attendance_data.branch_id,
                    "attendance_date": {
                        "$gte": attendance_date_start,
                        "$lt": attendance_date_end
                    }
                },
                sort=[
                    ("updated_at", -1),  # Most recent update first
                    ("created_at", -1)   # Then most recent creation
                ]
            )

            if existing:
                print(f"📝 Found existing coach attendance record: {existing['id']} - updating instead of creating duplicate")

                # Update existing record (proper upsert functionality)
                # Determine status based on is_present
                status = "present" if attendance_data.is_present else "absent"

                update_data = {
                    "check_in_time": datetime.utcnow() if attendance_data.is_present else None,
                    "check_out_time": None,  # Can be updated later if needed
                    "is_present": attendance_data.is_present,
                    "status": status,  # Set proper status value
                    "notes": attendance_data.notes,
                    "marked_by": current_user["id"],
                    "method": attendance_data.method,
                    "updated_at": datetime.utcnow()  # Track when it was last updated
                }

                result = await db.coach_attendance.update_one(
                    {"id": existing["id"]},
                    {"$set": update_data}
                )

                if result.modified_count > 0:
                    print(f"✅ Successfully updated existing coach attendance record: {existing['id']}")
                    return {"message": "Coach attendance updated successfully", "attendance_id": existing["id"], "action": "updated"}
                else:
                    print(f"⚠️ No changes made to existing coach attendance record: {existing['id']}")
                    return {"message": "Coach attendance already up to date", "attendance_id": existing["id"], "action": "no_change"}
            else:
                print(f"➕ No existing coach attendance found - creating new record")

                # Create coach attendance record
                # Determine status based on is_present
                status = "present" if attendance_data.is_present else "absent"

                coach_attendance = CoachAttendance(
                    **attendance_data.dict(),
                    check_in_time=datetime.utcnow() if attendance_data.is_present else None,
                    status=status,  # Set proper status value
                    marked_by=current_user["id"]
                )

                await db.coach_attendance.insert_one(coach_attendance.dict())
                print(f"✅ Successfully created new coach attendance record: {coach_attendance.id}")
                return {"message": "Coach attendance marked successfully", "attendance_id": coach_attendance.id, "action": "created"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to mark coach attendance: {str(e)}")

    @staticmethod
    async def mark_branch_manager_attendance(attendance_data: BranchManagerAttendanceCreate, current_user: dict):
        """Mark attendance for a branch manager"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Only superadmins can mark branch manager attendance
            user_role = current_user.get("role")
            if user_role not in ["super_admin", "superadmin"]:
                raise HTTPException(status_code=403, detail="Only superadmins can mark branch manager attendance")

            # Verify branch manager exists
            branch_manager = await db.branch_managers.find_one({"id": attendance_data.branch_manager_id, "is_active": True})
            if not branch_manager:
                raise HTTPException(status_code=404, detail="Branch manager not found")

            # Check if attendance already marked for this date
            existing_attendance = await db.branch_manager_attendance.find_one({
                "branch_manager_id": attendance_data.branch_manager_id,
                "attendance_date": {
                    "$gte": attendance_data.attendance_date.replace(hour=0, minute=0, second=0, microsecond=0),
                    "$lt": attendance_data.attendance_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                }
            })

            if existing_attendance:
                raise HTTPException(status_code=400, detail="Attendance already marked for this branch manager today")

            # Create branch manager attendance record
            bm_attendance = BranchManagerAttendance(
                **attendance_data.dict(),
                check_in_time=datetime.utcnow() if attendance_data.is_present else None,
                marked_by=current_user["id"]
            )

            await db.branch_manager_attendance.insert_one(bm_attendance.dict())
            return {"message": "Branch manager attendance marked successfully", "attendance_id": bm_attendance.id}

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to mark branch manager attendance: {str(e)}")

    @staticmethod
    async def get_coach_students_attendance(coach_id: str, date: str, current_user: dict):
        """Get students assigned to a coach with their attendance for a specific date"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Verify coach access
            if current_user.get("role") == "coach" and current_user.get("id") != coach_id:
                raise HTTPException(status_code=403, detail="Access denied: Can only view your own students")

            # Parse the date
            try:
                target_date = datetime.fromisoformat(date.replace('Z', '+00:00'))
                start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format")

            # Get coach information
            coach = await db.coaches.find_one({"id": coach_id, "is_active": True})
            if not coach:
                coach = await db.users.find_one({"id": coach_id, "role": "coach", "is_active": True})
                if not coach:
                    raise HTTPException(status_code=404, detail="Coach not found")

            coach_branch_id = coach.get("branch_id")
            if not coach_branch_id:
                return {"students": [], "attendance": [], "total": 0}

            # Get students in the coach's branch using the same logic as search_students
            # First get all students
            students = await db.users.find({
                "role": "student",
                "is_active": True
            }).to_list(length=None)

            # Get student IDs for enrollment filtering
            student_ids = [student["id"] for student in students]

            # Build enrollment filter (same as search_students)
            enrollment_filter = {"student_id": {"$in": student_ids}}
            if coach_branch_id:
                enrollment_filter["branch_id"] = coach_branch_id

            # Get matching enrollments (don't require is_active=True to match search behavior)
            enrollments = await db.enrollments.find(enrollment_filter).to_list(length=None)

            # Filter students based on enrollment matches (same as search_students)
            enrolled_student_ids = set(enrollment["student_id"] for enrollment in enrollments)
            students = [student for student in students if student["id"] in enrolled_student_ids]

            # Get attendance records for the specific date
            attendance_records = await db.attendance.find({
                "attendance_date": {"$gte": start_of_day, "$lte": end_of_day},
                "branch_id": coach_branch_id
            }).to_list(length=None)

            # Create a map of student attendance
            attendance_map = {}
            for record in attendance_records:
                student_id = record.get("student_id")
                if student_id:
                    # Use stored status if available, otherwise fall back to is_present logic
                    stored_status = record.get("status")
                    if stored_status:
                        status = stored_status
                    else:
                        # Fallback for old records without status field
                        status = "present" if record.get("is_present") else "absent"

                    attendance_map[student_id] = {
                        "status": status,
                        "check_in_time": record.get("check_in_time"),
                        "check_out_time": record.get("check_out_time"),
                        "notes": record.get("notes", ""),
                        "marked_by": record.get("marked_by"),
                        "admin_adjusted": record.get("admin_adjusted", False),
                        "attendance_modified_at": record.get("attendance_modified_at"),
                        "attendance_modified_by": record.get("attendance_modified_by"),
                    }

            # Combine student data with attendance and course information
            result_students = []
            for student in students:
                student_id = student.get("id")
                attendance_info = attendance_map.get(student_id, {
                    "status": "absent",  # Default to absent if no record
                    "check_in_time": None,
                    "check_out_time": None,
                    "notes": "",
                    "marked_by": None,
                    "admin_adjusted": False,
                    "attendance_modified_at": None,
                    "attendance_modified_by": None,
                })

                # Get student's enrollments for course information
                student_enrollments = [e for e in enrollments if e["student_id"] == student_id]
                courses = []

                for enrollment in student_enrollments:
                    # Get course details
                    course = await db.courses.find_one({"id": enrollment["course_id"]})
                    if course:
                        courses.append({
                            "id": course["id"],
                            "name": course.get("name", course.get("title", "Unknown Course")),
                            "course_id": course["id"],
                            "course_name": course.get("name", course.get("title", "Unknown Course"))
                        })

                result_students.append({
                    "id": student_id,
                    "full_name": student.get("full_name", "Unknown"),
                    "email": student.get("email", ""),
                    "branch_id": coach_branch_id,  # Use the coach's branch ID
                    "courses": courses,
                    "attendance": attendance_info
                })

            return {
                "students": result_students,
                "date": date,
                "total": len(result_students),
                "attendance_marked": len([s for s in result_students if s["attendance"]["status"] != "absent"])
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get coach students attendance: {str(e)}")

    @staticmethod
    async def get_branch_manager_students_attendance(branch_manager_id: str, date: str, current_user: dict):
        """Get students in branch manager's branches with their attendance for a specific date"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Verify branch manager access
            if current_user.get("role") == "branch_manager" and current_user.get("id") != branch_manager_id:
                raise HTTPException(status_code=403, detail="Access denied: Can only view your own branch students")

            # Parse the date
            try:
                target_date = datetime.fromisoformat(date.replace('Z', '+00:00'))
                start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format")

            # Get branch manager information and their managed branches
            branch_manager = await db.branch_managers.find_one({"id": branch_manager_id, "is_active": True})
            if not branch_manager:
                raise HTTPException(status_code=404, detail="Branch manager not found")

            # Get the branch assigned to this branch manager (prioritize branch_assignment.branch_id)
            assigned_branch_id = branch_manager.get("branch_assignment", {}).get("branch_id") or branch_manager.get("branch_id")
            if not assigned_branch_id:
                return {"students": [], "date": date, "total": 0, "attendance_marked": 0}

            # Verify the branch exists and is active
            assigned_branch = await db.branches.find_one({"id": assigned_branch_id, "is_active": True})
            if not assigned_branch:
                return {"students": [], "date": date, "total": 0, "attendance_marked": 0}

            managed_branch_ids = [assigned_branch_id]

            # Get all students enrolled in courses in these branches
            # Match coach API behavior - don't require is_active=True for enrollments
            enrollments = await db.enrollments.find({
                "branch_id": {"$in": managed_branch_ids}
            }).to_list(length=1000)

            if not enrollments:
                return {"students": [], "date": date, "total": 0, "attendance_marked": 0}

            # Get student details and their attendance for the specific date
            result_students = []
            for enrollment in enrollments:
                student_id = enrollment.get("student_id")
                course_id = enrollment.get("course_id")
                branch_id = enrollment.get("branch_id")

                # Get student information
                student = await db.users.find_one({"id": student_id, "is_active": True})
                if not student:
                    continue

                # Get course information
                course = await db.courses.find_one({"id": course_id})
                course_name = "Unknown Course"
                if course:
                    # Prioritize title field, then name field
                    course_name = course.get("title") or course.get("name") or course.get("course_name") or "Unknown Course"

                # Get branch information
                branch = await db.branches.find_one({"id": branch_id})
                branch_name = branch.get("branch", {}).get("name", "Unknown Branch") if branch else "Unknown Branch"

                # Get the most recent attendance record for this specific date
                # Sort by updated_at (if exists) then created_at to get the latest update
                attendance_record = await db.attendance.find_one(
                    {
                        "student_id": student_id,
                        "course_id": course_id,
                        "branch_id": branch_id,
                        "attendance_date": {
                            "$gte": start_of_day,
                            "$lt": end_of_day
                        }
                    },
                    sort=[
                        ("updated_at", -1),  # Most recent update first
                        ("created_at", -1)   # Then most recent creation
                    ]
                )

                # Prepare attendance data
                attendance_data = {
                    "status": "not_marked",
                    "check_in_time": None,
                    "check_out_time": None,
                    "notes": "",
                    "marked_by": None,
                    "admin_adjusted": False,
                    "attendance_modified_at": None,
                    "attendance_modified_by": None,
                }

                if attendance_record:
                    mod_at = attendance_record.get("attendance_modified_at") or attendance_record.get("updated_at")
                    attendance_data = {
                        "status": attendance_record.get("status", "present" if attendance_record.get("is_present") else "absent"),
                        "check_in_time": attendance_record.get("check_in_time").isoformat() if attendance_record.get("check_in_time") else None,
                        "check_out_time": attendance_record.get("check_out_time").isoformat() if attendance_record.get("check_out_time") else None,
                        "notes": attendance_record.get("notes", ""),
                        "marked_by": attendance_record.get("marked_by"),
                        "admin_adjusted": bool(attendance_record.get("admin_adjusted", False)),
                        "attendance_modified_at": mod_at.isoformat() if hasattr(mod_at, "isoformat") else None,
                        "attendance_modified_by": attendance_record.get("attendance_modified_by"),
                    }

                student_data = {
                    "student_id": student_id,
                    "student_name": student.get("full_name") or f"{student.get('first_name', '')} {student.get('last_name', '')}".strip(),
                    "email": student.get("email"),
                    "phone": student.get("phone"),
                    "course_id": course_id,
                    "course_name": course_name,
                    "branch_id": branch_id,
                    "branch_name": branch_name,
                    "attendance": attendance_data
                }

                result_students.append(student_data)

            return {
                "students": result_students,
                "date": date,
                "total": len(result_students),
                "attendance_marked": len([s for s in result_students if s["attendance"]["status"] != "not_marked"])
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get branch manager students attendance: {str(e)}")

    @staticmethod
    async def mark_comprehensive_attendance(attendance_request: AttendanceMarkRequest, current_user: dict):
        """Mark attendance for any user type (student, coach, branch_manager)"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            user_role = current_user.get("role")
            user_id = attendance_request.user_id
            user_type = attendance_request.user_type

            # Determine is_present based on status - both PRESENT and LATE are considered present
            is_present = attendance_request.status in [AttendanceStatus.PRESENT, AttendanceStatus.LATE]
            # Coach / branch_manager paths still use this default; student row logic resolves times separately
            check_in_time = attendance_request.check_in_time or (
                datetime.utcnow() if is_present else None
            )

            if user_type == "student":
                # Verify permissions for student attendance
                if user_role not in ["super_admin", "superadmin", "coach", "branch_manager"]:
                    raise HTTPException(status_code=403, detail="Insufficient permissions")

                # Verify course_id is provided for students
                if not attendance_request.course_id:
                    raise HTTPException(status_code=400, detail="Course ID required for student attendance")

                # Enhanced deduplication: Check if attendance already exists for this exact date
                attendance_date_start = attendance_request.attendance_date.replace(hour=0, minute=0, second=0, microsecond=0)
                attendance_date_end = attendance_request.attendance_date.replace(hour=23, minute=59, second=59, microsecond=999999)

                print(f"🔍 Checking for existing attendance: student_id={user_id}, course_id={attendance_request.course_id}, date_range={attendance_date_start} to {attendance_date_end}")

                # Find the most recent existing attendance record for this date
                existing = await db.attendance.find_one(
                    {
                        "student_id": user_id,
                        "course_id": attendance_request.course_id,
                        "branch_id": attendance_request.branch_id,
                        "attendance_date": {
                            "$gte": attendance_date_start,
                            "$lt": attendance_date_end
                        }
                    },
                    sort=[
                        ("updated_at", -1),  # Most recent update first
                        ("created_at", -1)   # Then most recent creation
                    ]
                )

                if existing:
                    print(f"📝 Found existing attendance record: {existing['id']} - updating instead of creating duplicate")

                    if is_present:
                        if attendance_request.check_in_time is not None:
                            new_in = attendance_request.check_in_time
                        else:
                            new_in = existing.get("check_in_time") or datetime.utcnow()
                        if attendance_request.check_out_time is not None:
                            new_out = attendance_request.check_out_time
                        else:
                            new_out = existing.get("check_out_time")
                    else:
                        new_in = None
                        new_out = None

                    _validate_check_in_out_order(new_in, new_out)
                    now_adj = datetime.utcnow()

                    # Update existing record (proper upsert functionality)
                    update_data = {
                        "check_in_time": new_in,
                        "check_out_time": new_out,
                        "is_present": is_present,
                        "status": attendance_request.status.value,  # Store the original status
                        "notes": attendance_request.notes,
                        "marked_by": current_user["id"],
                        "method": AttendanceMethod.MANUAL,
                        "updated_at": now_adj,
                        "admin_adjusted": True,
                        "attendance_modified_at": now_adj,
                        "attendance_modified_by": current_user["id"],
                    }

                    result = await db.attendance.update_one(
                        {"id": existing["id"]},
                        {"$set": update_data}
                    )

                    if result.modified_count > 0:
                        try:
                            await db[COL_ATTENDANCE_AUDIT].insert_one(
                                {
                                    "id": str(uuid.uuid4()),
                                    "attendance_id": existing["id"],
                                    "student_id": user_id,
                                    "course_id": attendance_request.course_id,
                                    "branch_id": attendance_request.branch_id,
                                    "admin_id": current_user["id"],
                                    "action": "student_attendance_update",
                                    "before_check_in": existing.get("check_in_time"),
                                    "before_check_out": existing.get("check_out_time"),
                                    "after_check_in": new_in,
                                    "after_check_out": new_out,
                                    "created_at": now_adj,
                                }
                            )
                        except Exception as exc:
                            logger.warning("attendance audit insert failed: %s", exc)
                        print(f"✅ Successfully updated existing attendance record: {existing['id']}")
                        return {"message": "Student attendance updated successfully", "attendance_id": existing["id"], "action": "updated"}
                    else:
                        print(f"⚠️ No changes made to existing attendance record: {existing['id']}")
                        return {"message": "Student attendance already up to date", "attendance_id": existing["id"], "action": "no_change"}
                else:
                    print(f"➕ No existing attendance found - creating new record")

                    check_in_time = attendance_request.check_in_time or (
                        datetime.utcnow() if is_present else None
                    )
                    check_out_time = attendance_request.check_out_time if is_present else None
                    _validate_check_in_out_order(check_in_time, check_out_time)

                    # Create new attendance record
                    attendance = Attendance(
                        student_id=user_id,
                        course_id=attendance_request.course_id,
                        branch_id=attendance_request.branch_id,
                        attendance_date=attendance_request.attendance_date,
                        check_in_time=check_in_time,
                        check_out_time=check_out_time,
                        method=AttendanceMethod.MANUAL,
                        marked_by=current_user["id"],
                        is_present=is_present,
                        status=attendance_request.status.value,  # Store the original status
                        notes=attendance_request.notes,
                        admin_adjusted=False,
                    )

                    await db.attendance.insert_one(attendance.dict())
                    print(f"✅ Successfully created new attendance record: {attendance.id}")
                    return {"message": "Student attendance marked successfully", "attendance_id": attendance.id, "action": "created"}

            elif user_type == "coach":
                # Enhanced deduplication for coach attendance: Check if attendance already exists for this exact date
                attendance_date_start = attendance_request.attendance_date.replace(hour=0, minute=0, second=0, microsecond=0)
                attendance_date_end = attendance_request.attendance_date.replace(hour=23, minute=59, second=59, microsecond=999999)

                print(f"🔍 Checking for existing coach attendance: coach_id={user_id}, branch_id={attendance_request.branch_id}, date_range={attendance_date_start} to {attendance_date_end}")

                # Find the most recent existing attendance record for this date
                existing = await db.coach_attendance.find_one(
                    {
                        "coach_id": user_id,
                        "branch_id": attendance_request.branch_id,
                        "attendance_date": {
                            "$gte": attendance_date_start,
                            "$lt": attendance_date_end
                        }
                    },
                    sort=[
                        ("updated_at", -1),  # Most recent update first
                        ("created_at", -1)   # Then most recent creation
                    ]
                )

                if existing:
                    print(f"📝 Found existing coach attendance record: {existing['id']} - updating instead of creating duplicate")

                    if is_present:
                        if attendance_request.check_in_time is not None:
                            cin = attendance_request.check_in_time
                        else:
                            cin = existing.get("check_in_time") or datetime.utcnow()
                        if attendance_request.check_out_time is not None:
                            cout = attendance_request.check_out_time
                        else:
                            cout = existing.get("check_out_time")
                    else:
                        cin = None
                        cout = None
                    _validate_check_in_out_order(cin, cout)

                    # Update existing record (proper upsert functionality)
                    update_data = {
                        "check_in_time": cin,
                        "check_out_time": cout,
                        "is_present": is_present,
                        "status": attendance_request.status.value if hasattr(attendance_request.status, 'value') else attendance_request.status,  # Handle both enum and string
                        "notes": attendance_request.notes,
                        "marked_by": current_user["id"],
                        "method": AttendanceMethod.MANUAL,
                        "updated_at": datetime.utcnow()  # Track when it was last updated
                    }

                    result = await db.coach_attendance.update_one(
                        {"id": existing["id"]},
                        {"$set": update_data}
                    )

                    if result.modified_count > 0:
                        print(f"✅ Successfully updated existing coach attendance record: {existing['id']}")
                        return {"message": "Coach attendance updated successfully", "attendance_id": existing["id"], "action": "updated"}
                    else:
                        print(f"⚠️ No changes made to existing coach attendance record: {existing['id']}")
                        return {"message": "Coach attendance already up to date", "attendance_id": existing["id"], "action": "no_change"}
                else:
                    print(f"➕ No existing coach attendance found - creating new record")

                    # Create new coach attendance record
                    cout_new = attendance_request.check_out_time if is_present else None
                    _validate_check_in_out_order(check_in_time, cout_new)
                    coach_attendance = CoachAttendance(
                        coach_id=user_id,
                        branch_id=attendance_request.branch_id,
                        attendance_date=attendance_request.attendance_date,
                        check_in_time=check_in_time,
                        check_out_time=cout_new,
                        method=AttendanceMethod.MANUAL,
                        marked_by=current_user["id"],
                        is_present=is_present,
                        status=attendance_request.status.value if hasattr(attendance_request.status, 'value') else attendance_request.status,  # Handle both enum and string
                        notes=attendance_request.notes
                    )

                    await db.coach_attendance.insert_one(coach_attendance.dict())
                    print(f"✅ Successfully created new coach attendance record: {coach_attendance.id}")
                    return {"message": "Coach attendance marked successfully", "attendance_id": coach_attendance.id, "action": "created"}

            elif user_type == "branch_manager":
                # Use existing branch manager attendance method
                bm_data = BranchManagerAttendanceCreate(
                    branch_manager_id=user_id,
                    branch_id=attendance_request.branch_id,
                    attendance_date=attendance_request.attendance_date,
                    method=AttendanceMethod.MANUAL,
                    is_present=is_present,
                    notes=attendance_request.notes
                )
                return await AttendanceController.mark_branch_manager_attendance(bm_data, current_user)

            else:
                raise HTTPException(status_code=400, detail="Invalid user type")

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to mark attendance: {str(e)}")

    @staticmethod
    async def get_student_my_attendance(
        current_user: dict,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ):
        """Get attendance data for the authenticated student"""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Verify user is a student
            if current_user.get("role") != "student":
                raise HTTPException(status_code=403, detail="Access denied. Student role required.")

            student_id = current_user.get("id")
            if not student_id:
                raise HTTPException(status_code=400, detail="Student ID not found in authentication data")

            # Build date filter
            date_filter = {}
            if start_date:
                try:
                    start_datetime = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    date_filter["$gte"] = start_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid start_date format")

            if end_date:
                try:
                    end_datetime = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    date_filter["$lte"] = end_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid end_date format")

            # If no date range specified, get last 30 days
            if not start_date and not end_date:
                end_datetime = datetime.utcnow()
                start_datetime = end_datetime - timedelta(days=30)
                date_filter = {
                    "$gte": start_datetime.replace(hour=0, minute=0, second=0, microsecond=0),
                    "$lte": end_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                }

            # Build attendance query
            attendance_filter = {
                "student_id": student_id
            }
            if date_filter:
                attendance_filter["attendance_date"] = date_filter

            print(f"🔍 Fetching attendance for student {student_id} with filter: {attendance_filter}")

            # Get attendance records
            attendance_records = await db.attendance.find(attendance_filter).sort("attendance_date", -1).to_list(length=None)

            print(f"📊 Found {len(attendance_records)} attendance records for student {student_id}")

            # Get student's enrollments to get course and branch information
            enrollments = await db.enrollments.find({"student_id": student_id}).to_list(length=None)
            enrollment_map = {enrollment["course_id"]: enrollment for enrollment in enrollments}

            # Get course information
            course_ids = list(set([record.get("course_id") for record in attendance_records if record.get("course_id")]))
            courses = await db.courses.find({"id": {"$in": course_ids}}).to_list(length=None) if course_ids else []
            course_map = {course["id"]: course for course in courses}

            # Get branch information
            branch_ids = list(set([record.get("branch_id") for record in attendance_records if record.get("branch_id")]))
            branches = await db.branches.find({"id": {"$in": branch_ids}}).to_list(length=None) if branch_ids else []
            branch_map = {branch["id"]: branch for branch in branches}

            # Format attendance records
            formatted_records = []
            for record in attendance_records:
                course_id = record.get("course_id")
                branch_id = record.get("branch_id")

                course_info = course_map.get(course_id, {})
                branch_info = branch_map.get(branch_id, {})

                mod_at = record.get("attendance_modified_at") or record.get("updated_at")
                formatted_record = {
                    "id": record.get("id"),
                    "date": _attendance_calendar_day(record.get("attendance_date")),
                    "course": course_info.get("title", course_info.get("name", "Unknown Course")),
                    "course_id": course_id,
                    "branch": branch_info.get("branch", {}).get("name", branch_info.get("name", "Unknown Branch")),
                    "branch_id": branch_id,
                    "status": record.get("status", "absent"),
                    "check_in_time": _dt_to_ist_ampm(record.get("check_in_time")),
                    "check_out_time": _dt_to_ist_ampm(record.get("check_out_time")),
                    "is_present": record.get("is_present", False),
                    "notes": record.get("notes", ""),
                    "admin_adjusted": bool(record.get("admin_adjusted", False)),
                    "attendance_modified_at": mod_at.isoformat() if hasattr(mod_at, "isoformat") else None,
                    "attendance_modified_by": record.get("attendance_modified_by"),
                }
                formatted_records.append(formatted_record)

            # Calculate statistics
            total_classes = len(formatted_records)
            attended = len([r for r in formatted_records if r["status"] in ["present", "late"]])
            absent = len([r for r in formatted_records if r["status"] == "absent"])
            late = len([r for r in formatted_records if r["status"] == "late"])
            percentage = round((attended / total_classes * 100) if total_classes > 0 else 0, 1)

            return {
                "attendance_records": formatted_records,
                "statistics": {
                    "total_classes": total_classes,
                    "attended": attended,
                    "absent": absent,
                    "late": late,
                    "percentage": percentage
                },
                "student_info": {
                    "id": student_id,
                    "name": current_user.get("full_name", "Student"),
                    "email": current_user.get("email", "")
                }
            }

        except Exception as e:
            print(f"❌ Error fetching student attendance: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get student attendance: {str(e)}")
