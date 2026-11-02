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
        status: Optional[str] = None,
        method: Optional[str] = None,
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

            from utils.partner_tenant import apply_partner_branch_scope

            # Attendance rows are scoped by branch ownership (not partner_org_id stamps).
            filter_query = await apply_partner_branch_scope(
                db, filter_query, current_user, branch_id=branch_id if role != "branch_manager" else None
            )

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
        current_user: dict = None,
        batch_ref: Optional[str] = None,
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
                from utils.student_status_service import get_managed_branch_ids_for_user

                managed_branch_ids = await get_managed_branch_ids_for_user(db, current_user)
                if not managed_branch_ids:
                    if date:
                        return {
                            "students": [],
                            "date": date,
                            "total": 0,
                            "attendance_marked": 0,
                        }
                    return {"students": [], "total": 0}

                # Selected branch only. If branch_id is specified, it must be in the managed branches.
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
            if branch_id and current_user.get("role") != "branch_manager":
                filter_query["branch_id"] = branch_id
            if course_id:
                filter_query["course_id"] = course_id

            from utils.partner_tenant import (
                apply_partner_branch_scope,
                is_partner_super_admin,
                partner_branch_ids,
            )

            partner_bids = await partner_branch_ids(db, current_user)
            if partner_bids is not None and not managed_branch_ids:
                filter_query = await apply_partner_branch_scope(
                    db, filter_query, current_user, branch_id=branch_id
                )

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

                # Build enrollment filter based on role.
                # Require active enrollments so attendance roster matches students page
                # (exclude accounts with only inactive/old enrollments).
                enrollment_filter = {"student_id": {"$in": student_ids}, "is_active": True}
                if managed_branch_ids:
                    if branch_id and str(branch_id) in {str(b) for b in managed_branch_ids}:
                        enrollment_filter["branch_id"] = branch_id
                    else:
                        enrollment_filter["branch_id"] = {"$in": managed_branch_ids}
                elif partner_bids is not None:
                    if branch_id:
                        enrollment_filter["branch_id"] = branch_id
                    else:
                        enrollment_filter["branch_id"] = {"$in": list(partner_bids or [])}
                elif branch_id:
                    enrollment_filter["branch_id"] = branch_id
                # Only apply course filtering when explicitly requested
                if course_id:
                    enrollment_filter["course_id"] = course_id
                if batch_ref and batch_ref != "all":
                    enrollment_filter["$or"] = [
                        {"batch_ref": batch_ref},
                        {"batch_id": batch_ref}
                    ]

                # Get matching enrollments
                # For Rock SA without branch/course filter, show all students even if
                # enrollments collection is incomplete (prevents "empty attendance" UI).
                # Partner SA must always stay within own org enrollments.
                user_role = str(current_user.get("role") or "").lower() if current_user else ""
                must_filter_by_enrollment = bool(
                    managed_branch_ids
                    or partner_bids is not None
                    or branch_id
                    or course_id
                    or (batch_ref and batch_ref != "all")
                )
                # Always load enrollments for course context (but don't always filter student list by them).
                enrollments = await db.enrollments.find(enrollment_filter).to_list(length=None)
                if must_filter_by_enrollment or user_role in [
                    "branch_manager",
                    "coach",
                    "coach_admin",
                    "partner_super_admin",
                ]:
                    enrolled_student_ids = set(
                        str(enrollment.get("student_id"))
                        for enrollment in enrollments
                        if enrollment.get("student_id")
                    )
                    students = [student for student in students if str(student.get("id")) in enrolled_student_ids]

                # Ensure unique students (multiple enrollments do not duplicate a student)
                seen_student_ids = set()
                unique_students = []
                for student in students:
                    sid = str(student.get("id") or "")
                    if sid and sid not in seen_student_ids:
                        seen_student_ids.add(sid)
                        unique_students.append(student)
                students = unique_students

                # Get attendance records for the specific date
                attendance_records = await db.attendance.find(filter_query).to_list(length=None)

                # Create maps of student attendance by student_id and by (student_id, course_id)
                attendance_map = {}
                attendance_by_course = {}
                for record in attendance_records:
                    student_id = str(record.get("student_id") or "")
                    rec_course_id = str(record.get("course_id") or "").strip()
                    if student_id:
                        stored_status = record.get("status")
                        if stored_status:
                            status = stored_status
                        else:
                            status = "present" if record.get("is_present") else "absent"

                        att_obj = {
                            "id": record.get("id"),
                            "attendance_id": record.get("id"),
                            "status": status,
                            "course_id": record.get("course_id"),
                            "batch_ref": record.get("batch_ref") or record.get("batch_id"),
                            "check_in_time": record.get("check_in_time"),
                            "check_out_time": record.get("check_out_time"),
                            "notes": record.get("notes", ""),
                            "marked_by": record.get("marked_by"),
                            "admin_adjusted": record.get("admin_adjusted", False),
                            "attendance_modified_at": record.get("attendance_modified_at"),
                            "attendance_modified_by": record.get("attendance_modified_by"),
                            "correction_reason": record.get("correction_reason"),
                        }
                        if rec_course_id:
                            attendance_by_course[(student_id, rec_course_id)] = att_obj
                        attendance_map[student_id] = att_obj

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
                target_branch_id = None
                if branch_id and (not managed_branch_ids or str(branch_id) in {str(b) for b in managed_branch_ids}):
                    target_branch_id = str(branch_id)
                elif managed_branch_ids and len(managed_branch_ids) == 1:
                    target_branch_id = str(managed_branch_ids[0])

                for student in students:
                    student_id = str(student.get("id"))

                    # Get student's enrollments for course information
                    student_enrollments = [e for e in enrollments if str(e.get("student_id")) == student_id]
                    courses = []

                    for enrollment in student_enrollments:
                        # Get course details
                        course = course_by_id.get(enrollment.get("course_id"))
                        if course:
                            cid = str(course["id"])
                            c_att = attendance_by_course.get((student_id, cid))
                            courses.append({
                                "id": course["id"],
                                "name": course.get("name", course.get("title", "Unknown Course")),
                                "course_id": course["id"],
                                "course_name": course.get("name", course.get("title", "Unknown Course")),
                                "batch_ref": enrollment.get("batch_ref") or (student.get("course") or {}).get("batch_ref"),
                                "branch_id": enrollment.get("branch_id"),
                                "attendance": c_att,
                            })

                    # Match attendance to requested course_id or to the student's course
                    target_course_id = str(course_id) if course_id else (str(courses[0]["id"]) if courses else None)
                    attendance_info = None
                    if target_course_id:
                        attendance_info = attendance_by_course.get((student_id, target_course_id))

                    if not attendance_info:
                        attendance_info = {
                            "id": None,
                            "attendance_id": None,
                            "status": "not_marked",
                            "course_id": target_course_id,
                            "check_in_time": None,
                            "check_out_time": None,
                            "notes": "",
                            "marked_by": None,
                            "admin_adjusted": False,
                            "attendance_modified_at": None,
                            "attendance_modified_by": None,
                            "correction_reason": None,
                        }

                    enrolled_branch_ids = [
                        str(e.get("branch_id")) for e in student_enrollments if e.get("branch_id")
                    ]
                    # Ensure branch_id accurately reflects the authorized/scoped branch
                    student_branch_id = (
                        (target_branch_id if target_branch_id and (target_branch_id in enrolled_branch_ids or not enrolled_branch_ids) else None)
                        or (enrolled_branch_ids[0] if enrolled_branch_ids else None)
                        or (str(student.get("branch_id")) if student.get("branch_id") else None)
                        or (str(managed_branch_ids[0]) if managed_branch_ids else None)
                    )

                    all_branch_ids = list(dict.fromkeys(
                        ([student_branch_id] if student_branch_id else []) + enrolled_branch_ids
                    ))

                    batch_refs = [
                        str(e.get("batch_ref") or e.get("batch_id"))
                        for e in student_enrollments
                        if (e.get("batch_ref") or e.get("batch_id"))
                    ]
                    primary_batch_ref = batch_refs[0] if batch_refs else None

                    student_batch_ref = (
                        (student_enrollments[0].get("batch_ref") if student_enrollments else None)
                        or (student.get("course") or {}).get("batch_ref")
                    )
                    att_by_c = {}
                    for c in courses:
                        cid = str(c.get("id") or c.get("course_id") or "")
                        if cid and (student_id, cid) in attendance_by_course:
                            att_by_c[cid] = attendance_by_course[(student_id, cid)]

                    result_students.append({
                        "id": student_id,
                        "full_name": student.get("full_name", "Unknown"),
                        "email": student.get("email", ""),
                        "branch_id": student_branch_id,
                        "branches": [{"id": bid} for bid in all_branch_ids],
                        "batch_ref": student_batch_ref,
                        "courses": courses,
                        "batch_ref": primary_batch_ref,
                        "batch_refs": batch_refs,
                        "attendance": attendance_info,
                        "attendance_by_course": att_by_c
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

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get student attendance: {str(e)}")

    @staticmethod
    async def get_my_coach_attendance(
        current_user: dict,
        coach_id: Optional[str] = None,
        month: Optional[int] = None,
        year: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status: Optional[str] = None,
    ):
        """Get attendance records and summary for the coach (marked by super admin or branch manager)."""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            # Determine coach id
            target_coach_id = current_user.get("id")
            if current_user.get("role") in ["super_admin", "superadmin", "branch_manager", "coach_admin"] and coach_id:
                target_coach_id = coach_id

            if not target_coach_id:
                raise HTTPException(status_code=400, detail="Coach ID could not be identified")

            # Find coach info
            coach_doc = await db.coaches.find_one({"id": target_coach_id})
            if not coach_doc:
                coach_doc = await db.users.find_one({"id": target_coach_id, "role": "coach"})

            coach_name = "Coach"
            if coach_doc:
                coach_name = coach_doc.get("full_name") or f"{coach_doc.get('first_name', '')} {coach_doc.get('last_name', '')}".strip() or "Coach"

            coach_branch_id = (coach_doc or {}).get("branch_id")
            branch_name = "Main Branch"
            if coach_branch_id:
                branch = await db.branches.find_one({"id": coach_branch_id})
                if branch:
                    branch_name = (branch.get("branch") or {}).get("name") or branch.get("name") or "Main Branch"

            # Build query
            query = {"coach_id": target_coach_id}

            # Month and Year filter or start_date / end_date
            if month and year:
                try:
                    start_dt = datetime(year, month, 1, 0, 0, 0)
                    if month == 12:
                        end_dt = datetime(year + 1, 1, 1, 0, 0, 0)
                    else:
                        end_dt = datetime(year, month + 1, 1, 0, 0, 0)
                    query["attendance_date"] = {"$gte": start_dt, "$lt": end_dt}
                except Exception as e:
                    logger.warning(f"Invalid month/year: {e}")
            elif start_date or end_date:
                date_filter = {}
                if start_date:
                    try:
                        s_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00")).replace(hour=0, minute=0, second=0, microsecond=0)
                        date_filter["$gte"] = s_dt
                    except Exception:
                        pass
                if end_date:
                    try:
                        e_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00")).replace(hour=23, minute=59, second=59, microsecond=999999)
                        date_filter["$lte"] = e_dt
                    except Exception:
                        pass
                if date_filter:
                    query["attendance_date"] = date_filter

            if status and status.lower() != "all":
                query["status"] = status.lower()

            raw_records = await db.coach_attendance.find(query).sort("attendance_date", -1).to_list(length=1000)

            # Gather marked_by IDs to lookup who marked it
            marker_ids = list({r.get("marked_by") for r in raw_records if r.get("marked_by")})
            markers_map = {}
            if marker_ids:
                superadmins = await db.superadmins.find({"id": {"$in": marker_ids}}).to_list(length=None)
                for sa in superadmins:
                    name = sa.get("name") or sa.get("full_name") or sa.get("email") or "Super Admin"
                    markers_map[sa.get("id")] = {"name": name, "role": "Super Admin"}

                bms = await db.branch_managers.find({"id": {"$in": marker_ids}}).to_list(length=None)
                for bm in bms:
                    bm_name = bm.get("full_name") or f"{bm.get('first_name', '')} {bm.get('last_name', '')}".strip() or bm.get("email") or "Branch Manager"
                    markers_map[bm.get("id")] = {"name": bm_name, "role": "Branch Manager"}

                users = await db.users.find({"id": {"$in": marker_ids}}).to_list(length=None)
                for u in users:
                    if u.get("id") not in markers_map:
                        u_name = u.get("full_name") or f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Administrator"
                        role_str = (u.get("role") or "admin").replace("_", " ").title()
                        markers_map[u.get("id")] = {"name": u_name, "role": role_str}

            # Branch cache for records that have their own branch_id
            rec_branch_ids = list({r.get("branch_id") for r in raw_records if r.get("branch_id")})
            rec_branches_map = {}
            if rec_branch_ids:
                branches = await db.branches.find({"id": {"$in": rec_branch_ids}}).to_list(length=None)
                for b in branches:
                    b_name = (b.get("branch") or {}).get("name") or b.get("name") or "Branch"
                    rec_branches_map[b.get("id")] = b_name

            formatted_records = []
            for r in raw_records:
                st = r.get("status")
                if not st:
                    st = "present" if r.get("is_present") else "absent"
                st = str(st).lower()

                marker_info = markers_map.get(r.get("marked_by"), {
                    "name": "Super Admin / Branch Manager" if r.get("marked_by") else "System",
                    "role": "Management"
                })

                r_branch_id = r.get("branch_id")
                r_branch_name = rec_branches_map.get(r_branch_id) or branch_name

                att_date_str = _attendance_calendar_day(r.get("attendance_date"))
                att_date_dt = r.get("attendance_date")
                day_name = att_date_dt.strftime("%A") if isinstance(att_date_dt, (datetime, date_cls)) else ""

                formatted_records.append({
                    "id": str(r.get("id") or r.get("_id")),
                    "attendance_date": att_date_str,
                    "day_name": day_name,
                    "status": st,
                    "is_present": bool(r.get("is_present", st == "present")),
                    "check_in_time": _dt_to_ist_ampm(r.get("check_in_time")),
                    "check_out_time": _dt_to_ist_ampm(r.get("check_out_time")),
                    "raw_check_in": r.get("check_in_time").isoformat() if hasattr(r.get("check_in_time"), "isoformat") else None,
                    "raw_check_out": r.get("check_out_time").isoformat() if hasattr(r.get("check_out_time"), "isoformat") else None,
                    "method": r.get("method") or "manual",
                    "notes": r.get("notes") or "",
                    "marked_by": r.get("marked_by"),
                    "marked_by_name": marker_info.get("name"),
                    "marked_by_role": marker_info.get("role"),
                    "branch_id": r_branch_id,
                    "branch_name": r_branch_name,
                    "created_at": r.get("created_at").isoformat() if hasattr(r.get("created_at"), "isoformat") else None,
                    "updated_at": r.get("updated_at").isoformat() if hasattr(r.get("updated_at"), "isoformat") else None,
                })

            total_days = len(formatted_records)
            present_days = sum(1 for r in formatted_records if r["status"] == "present")
            absent_days = sum(1 for r in formatted_records if r["status"] == "absent")
            attendance_rate = round((present_days / total_days * 100), 1) if total_days > 0 else 0

            return {
                "success": True,
                "coach": {
                    "id": target_coach_id,
                    "name": coach_name,
                    "branch_id": coach_branch_id,
                    "branch_name": branch_name,
                },
                "summary": {
                    "total_days": total_days,
                    "present_days": present_days,
                    "absent_days": absent_days,
                    "attendance_rate": attendance_rate,
                },
                "records": formatted_records,
                "total": total_days,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch coach attendance: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get coach attendance: {str(e)}")

    @staticmethod
    async def get_course_batches(
        course_id: str,
        branch_id: Optional[str] = None,
        current_user: dict = None
    ):
        """Fetch all batches for a specific course at a branch or across all branches."""
        try:
            db = get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database connection not available")

            batches = []
            seen = set()

            from utils.partner_tenant import assert_partner_owns_branch, partner_branch_ids

            partner_bids = await partner_branch_ids(db, current_user)
            if partner_bids is not None and branch_id and branch_id != "all":
                await assert_partner_owns_branch(db, current_user, branch_id)

            branch_query = {}
            if partner_bids is not None:
                branch_query = {"id": {"$in": list(partner_bids or [])}}

            # 1. If branch_id provided, look in that branch first
            if branch_id and branch_id != "all":
                branch = await db.branches.find_one({"id": branch_id})
                if partner_bids is not None and branch and branch.get("id") not in set(partner_bids or []):
                    branch = None
                if branch:
                    sched = branch.get("assignments", {}).get("course_schedule", [])
                    for cs in sched:
                        if str(cs.get("course_id")) == str(course_id):
                            for b in cs.get("batches", []):
                                bid = str(b.get("batch_id") or b.get("id") or "").strip()
                                if bid and bid not in seen:
                                    seen.add(bid)
                                    batches.append(b)

            # 2. If no batches found yet in specified branch (or branch_id is all/none),
            # search across branches (partner-scoped when applicable)
            if not batches:
                all_branches = await db.branches.find(branch_query).to_list(None)
                for br in all_branches:
                    sched = br.get("assignments", {}).get("course_schedule", [])
                    for cs in sched:
                        if str(cs.get("course_id")) == str(course_id):
                            for b in cs.get("batches", []):
                                bid = str(b.get("batch_id") or b.get("id") or "").strip()
                                if bid and bid not in seen:
                                    seen.add(bid)
                                    batches.append(b)

            # 3. Also check student enrollments for any batch_ref
            enr_filter = {"course_id": course_id}
            if branch_id and branch_id != "all":
                enr_filter["branch_id"] = branch_id
            elif partner_bids is not None:
                enr_filter["branch_id"] = {"$in": list(partner_bids or [])}
            enr_batch_refs = await db.enrollments.distinct("batch_ref", enr_filter)
            if enr_batch_refs:
                all_branches_cache = None
                for bref in enr_batch_refs:
                    if not bref:
                        continue
                    bref_str = str(bref).strip()
                    if bref_str and bref_str not in seen:
                        if all_branches_cache is None:
                            all_branches_cache = await db.branches.find(branch_query).to_list(None)
                        found_b = None
                        for br in all_branches_cache:
                            sched = br.get("assignments", {}).get("course_schedule", [])
                            for cs in sched:
                                for b in cs.get("batches", []):
                                    if str(b.get("batch_id") or b.get("id") or "").strip() == bref_str:
                                        found_b = b
                                        break
                                if found_b:
                                    break
                            if found_b:
                                break
                        if found_b:
                            seen.add(bref_str)
                            batches.append(found_b)
                        else:
                            seen.add(bref_str)
                            batches.append({
                                "batch_id": bref_str,
                                "batch_name": f"Batch {bref_str[:8]}",
                                "start_time": "",
                                "end_time": "",
                                "days": []
                            })

            # Format batches cleanly
            formatted = []
            for idx, b in enumerate(batches):
                bid = str(b.get("batch_id") or b.get("id") or f"batch_{idx}").strip()
                bname = str(b.get("batch_name") or b.get("name") or "").strip()
                st = str(b.get("start_time") or b.get("startTime") or "").strip()
                et = str(b.get("end_time") or b.get("endTime") or "").strip()
                days = b.get("days") if isinstance(b.get("days"), list) else []
                time_str = f"{st} - {et}" if (st and et) else (st or et)
                concise_name = bname or f"Batch {idx + 1}"
                short_label = f"{concise_name} ({time_str})" if time_str else concise_name
                day_abbr = ", ".join([str(d)[:3] for d in days]) if days else ""
                full_label = f"{short_label} • {day_abbr}" if day_abbr else short_label

                formatted.append({
                    "batch_id": bid,
                    "batch_ref": bid,
                    "batch_name": bname or None,
                    "name": bname or None,
                    "start_time": st or None,
                    "end_time": et or None,
                    "days": days,
                    "short_label": short_label,
                    "label": short_label,
                    "full_label": full_label,
                })

            return {"batches": formatted, "count": len(formatted), "course_id": course_id}
        except HTTPException:
            raise
        except Exception as e:
            print(f"Error fetching course batches: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch course batches: {str(e)}")


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
                from utils.student_status_service import get_managed_branch_ids_for_user

                managed_branch_ids = await get_managed_branch_ids_for_user(db, current_user)
                if not managed_branch_ids:
                    return {"coaches": [], "total": 0}
                coach_filter["branch_id"] = {"$in": managed_branch_ids}

            if branch_id and current_user.get("role") != "branch_manager":
                coach_filter["branch_id"] = branch_id

            from utils.partner_tenant import (
                assert_partner_owns_branch,
                partner_branch_ids,
                partner_coach_ids,
                partner_org_id_of,
            )

            partner_bids = await partner_branch_ids(db, current_user)
            if partner_bids is not None and not managed_branch_ids:
                if branch_id:
                    await assert_partner_owns_branch(db, current_user, branch_id)
                    coach_filter["branch_id"] = branch_id
                else:
                    org_id = partner_org_id_of(current_user)
                    coach_ids = await partner_coach_ids(db, current_user) or []
                    # Prefer id scope so coaches stamped with partner_org_id are included
                    # even when branch_id field layout differs.
                    if coach_ids:
                        coach_filter["id"] = {"$in": list(coach_ids)}
                        coach_filter.pop("branch_id", None)
                    else:
                        coach_filter["branch_id"] = {"$in": list(partner_bids or [])}
                    if org_id and "id" not in coach_filter:
                        coach_filter = {
                            "is_active": True,
                            "$or": [
                                {"partner_org_id": org_id},
                                {"branch_id": {"$in": list(partner_bids or [])}},
                                {"assigned_branches": {"$in": list(partner_bids or [])}},
                            ],
                        }

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
                elif partner_bids is not None:
                    if branch_id:
                        attendance_filter["branch_id"] = branch_id
                    else:
                        attendance_filter["branch_id"] = {"$in": list(partner_bids or [])}
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
                from utils.student_status_service import get_managed_branch_ids_for_user

                managed_branch_ids = await get_managed_branch_ids_for_user(db, current_user)
                if not managed_branch_ids:
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

                filter_query["branch_id"] = {"$in": managed_branch_ids}

            if branch_id and current_user.get("role") != "branch_manager":
                filter_query["branch_id"] = branch_id

            from utils.partner_tenant import apply_partner_branch_scope, partner_branch_ids

            partner_bids = await partner_branch_ids(db, current_user)
            if partner_bids is not None and not managed_branch_ids:
                filter_query = await apply_partner_branch_scope(
                    db, filter_query, current_user, branch_id=branch_id
                )

            # Determine the date to use for attendance filtering
            if target_date:
                # Use the provided date
                attendance_date = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                next_day = attendance_date + timedelta(days=1)
            else:
                # Use today's date as default
                attendance_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                next_day = attendance_date + timedelta(days=1)

            # Get all students - for Rock SA, show all students regardless of enrollment
            # For branch managers / partner SA, only show students from their branches
            student_filter = {"role": "student", "is_active": True}

            if managed_branch_ids:
                # For branch managers, get students from their branches via active enrollments
                enrollments = await db.enrollments.find(
                    {"branch_id": {"$in": managed_branch_ids}, "is_active": True}
                ).to_list(length=None)
                if enrollments:
                    enrolled_student_ids = list(set(e["student_id"] for e in enrollments))
                    student_filter["id"] = {"$in": enrolled_student_ids}
                else:
                    # If no enrollments exist, show no students for branch managers
                    student_filter["id"] = {"$in": []}
            elif partner_bids is not None:
                scope_bids = [branch_id] if branch_id else list(partner_bids or [])
                enrollments = await db.enrollments.find(
                    {"branch_id": {"$in": scope_bids}, "is_active": True}
                ).to_list(length=None)
                if enrollments:
                    enrolled_student_ids = list(set(e["student_id"] for e in enrollments))
                    student_filter["id"] = {"$in": enrolled_student_ids}
                else:
                    student_filter["id"] = {"$in": []}
            elif branch_id:
                # For specific branch, get students from that branch via active enrollments
                enrollments = await db.enrollments.find(
                    {"branch_id": branch_id, "is_active": True}
                ).to_list(length=None)
                if enrollments:
                    enrolled_student_ids = list(set(e["student_id"] for e in enrollments))
                    student_filter["id"] = {"$in": enrolled_student_ids}
                else:
                    # If no enrollments exist, show no students for specific branch
                    student_filter["id"] = {"$in": []}
            # For Rock SA without branch filter, show all students regardless of enrollment status

            all_students = await db.users.find(student_filter).to_list(length=None)
            # Unique roster (guard against duplicate user docs)
            student_by_id = {}
            for s in all_students:
                sid = s.get("id")
                if sid and sid not in student_by_id:
                    student_by_id[sid] = s
            all_student_ids = set(student_by_id.keys())
            total_students = len(all_student_ids)

            # Get attendance records for the specified date
            attendance_filter = {**filter_query, "attendance_date": {"$gte": attendance_date, "$lt": next_day}}
            attendance_records = await db.attendance.find(attendance_filter).to_list(length=None)

            def _normalize_att_status(record: dict) -> str:
                stored = record.get("status")
                if stored:
                    return str(stored).strip().lower()
                return "present" if record.get("is_present") else "absent"

            # One status per student (present > late > absent). Do NOT treat unmarked as absent —
            # unmarked students stay outside present/absent so cards match the students page.
            student_status_by_id = {}
            _status_rank = {"present": 3, "late": 2, "absent": 1}
            for record in attendance_records:
                sid = record.get("student_id")
                if not sid or sid not in all_student_ids:
                    continue
                st = _normalize_att_status(record)
                if st not in _status_rank:
                    continue
                prev = student_status_by_id.get(sid)
                if prev is None or _status_rank[st] > _status_rank.get(prev, 0):
                    student_status_by_id[sid] = st

            student_present_today = sum(1 for st in student_status_by_id.values() if st == "present")
            student_late_today = sum(1 for st in student_status_by_id.values() if st == "late")
            student_absent_today = sum(1 for st in student_status_by_id.values() if st == "absent")

            # Get coach statistics - try coaches collection first, then users collection
            coach_filter = {"is_active": True}
            if managed_branch_ids:
                coach_filter["branch_id"] = {"$in": managed_branch_ids}
            elif partner_bids is not None:
                from utils.partner_tenant import partner_coach_ids, partner_org_id_of

                org_id = partner_org_id_of(current_user)
                coach_ids = await partner_coach_ids(db, current_user) or []
                if branch_id:
                    coach_filter["branch_id"] = branch_id
                elif coach_ids:
                    coach_filter["id"] = {"$in": list(coach_ids)}
                else:
                    coach_filter = {
                        "is_active": True,
                        "$or": [
                            {"partner_org_id": org_id},
                            {"branch_id": {"$in": list(partner_bids or [])}},
                            {"assigned_branches": {"$in": list(partner_bids or [])}},
                        ],
                    }
            elif branch_id:
                coach_filter["branch_id"] = branch_id

            # Try to get coaches from dedicated coaches collection
            all_coaches = await db.coaches.find(coach_filter).to_list(length=None)

            # If no coaches in coaches collection, try users collection
            if not all_coaches:
                user_coach_filter = {"role": "coach", "is_active": True}
                if managed_branch_ids:
                    user_coach_filter["branch_id"] = {"$in": managed_branch_ids}
                elif partner_bids is not None:
                    user_coach_filter["branch_id"] = (
                        branch_id if branch_id else {"$in": list(partner_bids or [])}
                    )
                elif branch_id:
                    user_coach_filter["branch_id"] = branch_id
                all_coaches = await db.users.find(user_coach_filter).to_list(length=None)

            coach_by_id = {}
            for c in all_coaches:
                cid = c.get("id")
                if cid and cid not in coach_by_id:
                    coach_by_id[cid] = c
            all_coach_ids = set(coach_by_id.keys())
            total_coaches = len(all_coach_ids)

            # Get coach attendance records for the specified date
            coach_attendance_filter = {**filter_query, "attendance_date": {"$gte": attendance_date, "$lt": next_day}}
            coach_attendance_records = await db.coach_attendance.find(coach_attendance_filter).to_list(length=None)

            coach_status_by_id = {}
            for record in coach_attendance_records:
                cid = record.get("coach_id")
                if not cid or cid not in all_coach_ids:
                    continue
                st = _normalize_att_status(record)
                if st not in _status_rank:
                    continue
                prev = coach_status_by_id.get(cid)
                if prev is None or _status_rank[st] > _status_rank.get(prev, 0):
                    coach_status_by_id[cid] = st

            coach_present_today = sum(1 for st in coach_status_by_id.values() if st == "present")
            coach_late_today = sum(1 for st in coach_status_by_id.values() if st == "late")
            coach_absent_today = sum(1 for st in coach_status_by_id.values() if st == "absent")

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

            from utils.partner_tenant import assert_partner_owns_branch, stamp_tenant

            payload = attendance_data.dict()
            await assert_partner_owns_branch(db, current_user, payload.get("branch_id"))

            # Create attendance record
            attendance = Attendance(
                **payload,
                check_in_time=datetime.utcnow(),
                marked_by=current_user["id"]
            )

            doc = attendance.dict()
            stamp_tenant(doc, current_user)
            await db.attendance.insert_one(doc)
            return {"message": "Attendance marked successfully", "attendance_id": attendance.id}

        except HTTPException:
            raise
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
                student_id=student_id,
                coach_id=coach_id,
                course_id=course_id,
                branch_id=branch_id,
                start_date=start_date,
                end_date=end_date,
                current_user=current_user,
                status=status,
                method=method,
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

            from utils.partner_tenant import assert_partner_owns_branch, partner_coach_ids, partner_org_id_of

            if partner_org_id_of(current_user):
                allowed_coaches = set(await partner_coach_ids(db, current_user) or [])
                if coach_id not in allowed_coaches and coach.get("partner_org_id") != partner_org_id_of(current_user):
                    raise HTTPException(status_code=403, detail="Coach is outside your partner organization")
                await assert_partner_owns_branch(db, current_user, coach_branch_id)

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
                from utils.student_status_service import get_managed_branch_ids_for_user

                managed_branch_ids = await get_managed_branch_ids_for_user(db, current_user)
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

            from utils.partner_tenant import partner_coach_ids, partner_org_id_of

            if partner_org_id_of(current_user):
                allowed = set(await partner_coach_ids(db, current_user) or [])
                coach_doc = await db.coaches.find_one({"id": coach_id}) or await db.users.find_one({"id": coach_id})
                if coach_id not in allowed and (coach_doc or {}).get("partner_org_id") != partner_org_id_of(current_user):
                    raise HTTPException(status_code=403, detail="Coach is outside your partner organization")

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

            from utils.partner_tenant import assert_partner_owns_branch, partner_org_id_of

            if partner_org_id_of(current_user):
                bm = await db.branch_managers.find_one({"id": branch_manager_id}) or await db.users.find_one(
                    {"id": branch_manager_id}
                )
                bm_branches = (bm or {}).get("managed_branches") or []
                if (bm or {}).get("branch_id"):
                    bm_branches = list({*bm_branches, (bm or {}).get("branch_id")})
                if not bm_branches:
                    raise HTTPException(status_code=403, detail="Branch manager is outside your partner organization")
                for bid in bm_branches:
                    await assert_partner_owns_branch(db, current_user, bid)

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
