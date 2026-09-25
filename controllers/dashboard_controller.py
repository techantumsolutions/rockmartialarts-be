from fastapi import HTTPException
from typing import Optional, Tuple
from datetime import datetime, timedelta
from utils.database import get_db
from utils.helpers import serialize_doc
from models.user_models import UserRole


def _parse_dashboard_period(
    start_date: Optional[str], end_date: Optional[str]
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Parse YYYY-MM-DD query params into UTC datetimes (inclusive end-of-day for end_date)."""
    if not (start_date and str(start_date).strip()) and not (end_date and str(end_date).strip()):
        return None, None
    try:
        if start_date and str(start_date).strip():
            period_start = datetime.strptime(str(start_date).strip()[:10], "%Y-%m-%d")
        else:
            period_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if end_date and str(end_date).strip():
            period_end = datetime.strptime(str(end_date).strip()[:10], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        else:
            period_end = datetime.utcnow()
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        return period_start, period_end
    except (ValueError, TypeError):
        return None, None


class DashboardController:
    @staticmethod
    async def get_dashboard_stats(
        current_user: dict,
        branch_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """Get comprehensive dashboard statistics"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        stats = {}
        
        # Filter by role and branch
        filter_query = {}
        if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
            filter_query["branch_id"] = current_user["branch_id"]
        elif current_user["role"] == "branch_manager":
            # Branch managers can only see data from their managed branches
            branch_manager_id = current_user.get("id")
            if not branch_manager_id:
                raise HTTPException(status_code=403, detail="Branch manager ID not found")

            # Find all branches managed by this branch manager
            managed_branches = await db.branches.find({"manager_id": branch_manager_id, "is_active": True}).to_list(length=None)

            if not managed_branches:
                # Return empty stats if no branches are managed
                return {"dashboard_stats": {
                    "active_students": 0,
                    "inactive_students": 0,
                    "total_students": 0,
                    "total_users": 0,
                    "active_courses": 0,
                    "monthly_active_users": 0,
                    "active_enrollments": 0,
                    "enrollments_count": 0,
                    "total_revenue": 0,
                    "monthly_revenue": 0,
                    "pending_payments": 0,
                    "renewals_count": 0,
                    "leads_count": 0,
                    "demo_bookings_count": 0,
                    "training_requests_count": 0,
                    "events_count": 0,
                    "events_registrations_count": 0,
                    "partners_count": 0,
                    "today_attendance": 0,
                    "total_coaches": 0,
                    "active_coaches": 0,
                    "students_registered_in_period": 0,
                    "courses_with_enrollments_in_period": 0,
                }}

            # Get all branch IDs managed by this branch manager
            managed_branch_ids = [branch["id"] for branch in managed_branches]
            print(f"Branch manager {branch_manager_id} manages branches for dashboard stats: {managed_branch_ids}")

            # Filter by multiple branches
            filter_query["branch_id"] = {"$in": managed_branch_ids}
        elif branch_id:
            filter_query["branch_id"] = branch_id
        
        try:
            # Active students count - Handle branch manager filtering correctly
            if current_user["role"] == "branch_manager":
                managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])
                if managed_branch_ids:
                    active_students = len(
                        await db.enrollments.distinct(
                            "student_id",
                            {"is_active": True, "branch_id": {"$in": managed_branch_ids}},
                        )
                    )

                    # Total users count for branch manager
                    total_users = await db.users.count_documents({
                        "is_active": True,
                        "branch_id": {"$in": managed_branch_ids}
                    })
                else:
                    active_students = 0
                    total_users = 0
            else:
                branch_filter = filter_query.get("branch_id")
                if branch_filter:
                    active_students = len(
                        await db.enrollments.distinct(
                            "student_id",
                            {"is_active": True, "branch_id": branch_filter},
                        )
                    )
                else:
                    active_students = await db.users.count_documents({
                        "role": "student",
                        "is_active": True,
                    })

                total_users = await db.users.count_documents({
                    "is_active": True,
                    **filter_query
                })

            stats["active_students"] = active_students
            stats["total_users"] = total_users
            
            # Active courses count
            if current_user["role"] == "branch_manager":
                # For branch managers, count courses assigned to their managed branches
                managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])
                if managed_branch_ids:
                    # Get all course IDs from the managed branches
                    course_ids_set = set()
                    for branch in managed_branches:
                        branch_course_ids = branch.get("assignments", {}).get("courses", [])
                        course_ids_set.update(branch_course_ids)

                    if course_ids_set:
                        active_courses = await db.courses.count_documents({
                            "settings.active": True,
                            "id": {"$in": list(course_ids_set)}
                        })
                        total_courses = await db.courses.count_documents({
                            "id": {"$in": list(course_ids_set)}
                        })
                    else:
                        active_courses = 0
                        total_courses = 0
                else:
                    active_courses = 0
                    total_courses = 0
            else:
                # For other roles, count all active courses
                active_courses = await db.courses.count_documents({
                    "settings.active": True,
                    **filter_query
                })
                total_courses = await db.courses.count_documents(filter_query)

            stats["active_courses"] = active_courses
            stats["total_courses"] = total_courses

            # Active coaches count (for branch managers, filter by branch)
            if current_user["role"] == "branch_manager":
                # For branch managers, count coaches assigned to their managed branches
                managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])
                if managed_branch_ids:
                    active_coaches = await db.coaches.count_documents({
                        "is_active": True,
                        "branch_id": {"$in": managed_branch_ids}
                    })
                    total_coaches = await db.coaches.count_documents({
                        "branch_id": {"$in": managed_branch_ids}
                    })
                else:
                    active_coaches = 0
                    total_coaches = 0
            else:
                # For other roles, use existing logic
                active_coaches = await db.coaches.count_documents({
                    "is_active": True,
                    **filter_query
                })
                total_coaches = await db.coaches.count_documents(filter_query)

            stats["active_coaches"] = active_coaches
            stats["total_coaches"] = total_coaches

            # Monthly active users (users who logged in within last 30 days)
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            monthly_active_users = await db.users.count_documents({
                "is_active": True,
                "last_login": {"$gte": thirty_days_ago},
                **filter_query
            })
            stats["monthly_active_users"] = monthly_active_users
            
            # Active enrollments - Handle branch manager filtering correctly
            if current_user["role"] == "branch_manager":
                managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])
                if managed_branch_ids:
                    active_enrollments = await db.enrollments.count_documents({
                        "is_active": True,
                        "branch_id": {"$in": managed_branch_ids}
                    })
                else:
                    active_enrollments = 0
            else:
                active_enrollments = await db.enrollments.count_documents({
                    "is_active": True,
                    **filter_query
                })
            stats["active_enrollments"] = active_enrollments
            
            # Revenue calculation (from payments) - Handle both "completed" and "paid" status
            # For branch managers, we need to get students from enrollments since payments don't have branch_id
            if current_user["role"] == "branch_manager":
                managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])

                if managed_branch_ids:
                    # Get students from enrollments in managed branches
                    enrollment_students = await db.enrollments.find({
                        "is_active": True,
                        "branch_id": {"$in": managed_branch_ids}
                    }).distinct("student_id")

                    # Also get students directly assigned to branches
                    branch_students = await db.users.find({
                        "role": "student",
                        "is_active": True,
                        "branch_id": {"$in": managed_branch_ids}
                    }).to_list(length=None)

                    direct_student_ids = [student["id"] for student in branch_students]
                    all_student_ids = list(set(enrollment_students + direct_student_ids))

                    if all_student_ids:
                        # Calculate total revenue
                        revenue_pipeline = [
                            {"$match": {
                                "payment_status": {"$in": ["completed", "paid"]},
                                "student_id": {"$in": all_student_ids}
                            }},
                            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                        ]
                        revenue_result = await db.payments.aggregate(revenue_pipeline).to_list(length=1)
                        total_revenue = revenue_result[0]["total"] if revenue_result else 0

                        # Calculate monthly revenue
                        current_month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                        monthly_revenue_pipeline = [
                            {
                                "$match": {
                                    "payment_status": {"$in": ["completed", "paid"]},
                                    "payment_date": {"$gte": current_month_start},
                                    "student_id": {"$in": all_student_ids}
                                }
                            },
                            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                        ]
                        monthly_revenue_result = await db.payments.aggregate(monthly_revenue_pipeline).to_list(length=1)
                        monthly_revenue = monthly_revenue_result[0]["total"] if monthly_revenue_result else 0

                        # Count pending payments
                        pending_payments = await db.payments.count_documents({
                            "payment_status": "pending",
                            "student_id": {"$in": all_student_ids}
                        })
                    else:
                        total_revenue = 0
                        monthly_revenue = 0
                        pending_payments = 0
                else:
                    total_revenue = 0
                    monthly_revenue = 0
                    pending_payments = 0
            else:
                # For other roles (super_admin, coach_admin), use branch_id filtering
                revenue_pipeline = [
                    {"$match": {"payment_status": {"$in": ["completed", "paid"]}, **filter_query}},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]
                revenue_result = await db.payments.aggregate(revenue_pipeline).to_list(length=1)
                total_revenue = revenue_result[0]["total"] if revenue_result else 0

                # Monthly revenue
                current_month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                monthly_revenue_pipeline = [
                    {
                        "$match": {
                            "payment_status": {"$in": ["completed", "paid"]},
                            "payment_date": {"$gte": current_month_start},
                            **filter_query
                        }
                    },
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]
                monthly_revenue_result = await db.payments.aggregate(monthly_revenue_pipeline).to_list(length=1)
                monthly_revenue = monthly_revenue_result[0]["total"] if monthly_revenue_result else 0

                # Pending payments
                pending_payments = await db.payments.count_documents({
                    "payment_status": "pending",
                    **filter_query
                })

            stats["total_revenue"] = total_revenue
            stats["monthly_revenue"] = monthly_revenue
            stats["pending_payments"] = pending_payments
            
            # Today's attendance - Handle branch manager filtering correctly
            today = datetime.utcnow().date()
            if current_user["role"] == "branch_manager":
                managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])
                if managed_branch_ids:
                    today_attendance = await db.attendance.count_documents({
                        "attendance_date": {
                            "$gte": datetime.combine(today, datetime.min.time()),
                            "$lt": datetime.combine(today + timedelta(days=1), datetime.min.time())
                        },
                        "branch_id": {"$in": managed_branch_ids}
                    })
                else:
                    today_attendance = 0
            else:
                today_attendance = await db.attendance.count_documents({
                    "attendance_date": {
                        "$gte": datetime.combine(today, datetime.min.time()),
                        "$lt": datetime.combine(today + timedelta(days=1), datetime.min.time())
                    },
                    **filter_query
                })
            stats["today_attendance"] = today_attendance

            # Period-scoped metrics (for time-range filters on the dashboard UI)
            period_start, period_end = _parse_dashboard_period(start_date, end_date)
            if period_start and period_end:
                if current_user["role"] == "branch_manager":
                    managed_branch_ids = filter_query.get("branch_id", {}).get("$in", [])
                    if managed_branch_ids:
                        enr_q = {
                            "branch_id": {"$in": managed_branch_ids},
                            "is_active": True,
                            "$or": [
                                {"created_at": {"$gte": period_start, "$lte": period_end}},
                                {"enrollment_date": {"$gte": period_start, "$lte": period_end}},
                            ],
                        }
                        reg_ids = await db.enrollments.distinct("student_id", enr_q)
                        stats["students_registered_in_period"] = len(reg_ids)
                        course_ids = await db.enrollments.distinct("course_id", enr_q)
                        stats["courses_with_enrollments_in_period"] = len(course_ids)
                    else:
                        stats["students_registered_in_period"] = 0
                        stats["courses_with_enrollments_in_period"] = 0
                else:
                    uq = {
                        "role": "student",
                        "is_active": True,
                        "created_at": {"$gte": period_start, "$lte": period_end},
                    }
                    if filter_query.get("branch_id"):
                        uq["branch_id"] = filter_query["branch_id"]
                    stats["students_registered_in_period"] = await db.users.count_documents(uq)
                    enr_q = {
                        "is_active": True,
                        "$or": [
                            {"created_at": {"$gte": period_start, "$lte": period_end}},
                            {"enrollment_date": {"$gte": period_start, "$lte": period_end}},
                        ],
                    }
                    if filter_query.get("branch_id"):
                        enr_q["branch_id"] = filter_query["branch_id"]
                    course_ids = await db.enrollments.distinct("course_id", enr_q)
                    stats["courses_with_enrollments_in_period"] = len(course_ids)
            else:
                stats["students_registered_in_period"] = stats.get("active_students", 0)
                stats["courses_with_enrollments_in_period"] = stats.get("active_courses", 0)

            # ---- M23-S02 additive metrics (safe defaults) ----
            branch_scope = filter_query.get("branch_id")
            period_start, period_end = _parse_dashboard_period(start_date, end_date)
            date_match = {}
            if period_start and period_end:
                date_match = {"$gte": period_start, "$lte": period_end}

            # Inactive / total students
            try:
                if current_user["role"] == "branch_manager":
                    managed_branch_ids = filter_query.get("branch_id", {}).get("$in", []) or []
                    if managed_branch_ids:
                        active_ids = set(
                            await db.enrollments.distinct(
                                "student_id",
                                {"is_active": True, "branch_id": {"$in": managed_branch_ids}},
                            )
                        )
                        all_ids = set(
                            await db.enrollments.distinct(
                                "student_id",
                                {"branch_id": {"$in": managed_branch_ids}},
                            )
                        )
                        stats["total_students"] = len(all_ids)
                        stats["inactive_students"] = max(0, len(all_ids) - len(active_ids))
                    else:
                        stats["total_students"] = 0
                        stats["inactive_students"] = 0
                else:
                    aq = {"role": "student", "is_active": True}
                    iq = {"role": "student", "is_active": False}
                    tq = {"role": "student"}
                    if branch_scope:
                        aq["branch_id"] = branch_scope
                        iq["branch_id"] = branch_scope
                        tq["branch_id"] = branch_scope
                    stats["total_students"] = await db.users.count_documents(tq)
                    stats["inactive_students"] = await db.users.count_documents(iq)
                    if not stats.get("active_students"):
                        stats["active_students"] = await db.users.count_documents(aq)
            except Exception:
                stats.setdefault("total_students", stats.get("active_students", 0))
                stats.setdefault("inactive_students", 0)

            stats["enrollments_count"] = stats.get("active_enrollments", 0)

            # Renewals (payments marked renewal or type renewal in period)
            try:
                renew_q: dict = {
                    "$or": [
                        {"is_renewal": True},
                        {"payment_type": {"$regex": "renew", "$options": "i"}},
                        {"type": {"$regex": "renew", "$options": "i"}},
                    ],
                    "payment_status": {"$in": ["completed", "paid"]},
                }
                if date_match:
                    renew_q["payment_date"] = date_match
                if current_user["role"] == "branch_manager":
                    managed_branch_ids = filter_query.get("branch_id", {}).get("$in", []) or []
                    if managed_branch_ids:
                        sid = await db.enrollments.distinct(
                            "student_id", {"branch_id": {"$in": managed_branch_ids}}
                        )
                        renew_q["student_id"] = {"$in": sid}
                        stats["renewals_count"] = await db.payments.count_documents(renew_q) if sid else 0
                    else:
                        stats["renewals_count"] = 0
                else:
                    if branch_scope:
                        renew_q["branch_id"] = branch_scope
                    stats["renewals_count"] = await db.payments.count_documents(renew_q)
            except Exception:
                stats["renewals_count"] = 0

            async def _count_collection(name: str, base: dict) -> int:
                try:
                    coll = db[name]
                    q = dict(base)
                    if date_match:
                        q["created_at"] = date_match
                    if current_user["role"] == "branch_manager":
                        managed_branch_ids = filter_query.get("branch_id", {}).get("$in", []) or []
                        if managed_branch_ids:
                            q["branch_id"] = {"$in": managed_branch_ids}
                        else:
                            return 0
                    elif branch_scope:
                        q["branch_id"] = branch_scope
                    return await coll.count_documents(q)
                except Exception:
                    return 0

            stats["leads_count"] = await _count_collection("leads", {})
            stats["demo_bookings_count"] = await _count_collection("demo_bookings", {})
            if stats["demo_bookings_count"] == 0:
                stats["demo_bookings_count"] = await _count_collection("demo_booking", {})
            stats["training_requests_count"] = await _count_collection("training_requests", {})
            stats["events_count"] = await _count_collection("academy_events", {})
            if stats["events_count"] == 0:
                stats["events_count"] = await _count_collection("events", {})
            stats["events_registrations_count"] = await _count_collection(
                "academy_event_registrations", {}
            )
            try:
                pq: dict = {}
                if date_match:
                    pq["created_at"] = date_match
                stats["partners_count"] = await db.collaboration_partners.count_documents(pq)
            except Exception:
                try:
                    stats["partners_count"] = await db.branches.count_documents(
                        {"$or": [{"is_collaboration_partner": True}, {"allows_collaboration": True}]}
                    )
                except Exception:
                    stats["partners_count"] = 0

            return {"dashboard_stats": stats}
            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching dashboard statistics: {str(e)}"
            )

    @staticmethod
    async def get_recent_activities(
        current_user: dict,
        limit: int = 10,
        branch_id: Optional[str] = None,
    ):
        """Get recent activities for dashboard (branch-scoped for BM / optional branch_id)."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()
        
        try:
            enr_filter: dict = {"is_active": True}
            pay_student_ids = None

            if current_user["role"] == "branch_manager":
                branch_manager_id = current_user.get("id")
                managed_branches = await db.branches.find(
                    {"manager_id": branch_manager_id, "is_active": True}
                ).to_list(length=None)
                managed_branch_ids = [b["id"] for b in managed_branches]
                if not managed_branch_ids:
                    return {"recent_enrollments": [], "recent_payments": []}
                enr_filter["branch_id"] = {"$in": managed_branch_ids}
                pay_student_ids = await db.enrollments.distinct(
                    "student_id", {"branch_id": {"$in": managed_branch_ids}}
                )
            elif branch_id:
                enr_filter["branch_id"] = branch_id
                pay_student_ids = await db.enrollments.distinct(
                    "student_id", {"branch_id": branch_id}
                )

            recent_enrollments = await db.enrollments.find(enr_filter).sort(
                "created_at", -1
            ).limit(limit).to_list(length=limit)

            pay_filter: dict = {"payment_status": {"$in": ["completed", "paid"]}}
            if pay_student_ids is not None:
                if not pay_student_ids:
                    recent_payments = []
                else:
                    pay_filter["student_id"] = {"$in": pay_student_ids}
                    recent_payments = await db.payments.find(pay_filter).sort(
                        "payment_date", -1
                    ).limit(limit).to_list(length=limit)
            else:
                recent_payments = await db.payments.find(pay_filter).sort(
                    "payment_date", -1
                ).limit(limit).to_list(length=limit)
            
            return {
                "recent_enrollments": serialize_doc(recent_enrollments),
                "recent_payments": serialize_doc(recent_payments)
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error fetching recent activities: {str(e)}"
            )
