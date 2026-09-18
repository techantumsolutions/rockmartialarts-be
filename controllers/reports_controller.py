from fastapi import HTTPException
from typing import Optional, Dict, Any, List
import datetime as dt
from utils.database import get_db
from utils.helpers import serialize_doc
from models.user_models import UserRole

class ReportsController:
    @staticmethod
    async def get_financial_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        payment_type: Optional[str] = None,
        payment_method: Optional[str] = None,
        payment_status: Optional[str] = None,
        date_range: Optional[str] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ):
        """Get comprehensive financial reports with enhanced filtering and search"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build filter query based on user role and parameters
        filter_query = {}
        current_role = current_user.get("role")

        if current_role == "coach_admin" and current_user.get("branch_id"):
            # Coach admins can only see payments from their branch
            filter_query["branch_details.branch_id"] = current_user["branch_id"]
        elif current_role == "branch_manager":
            # Branch managers can only see payments from their managed branches
            managed_branches = current_user.get("managed_branches", [])
            branch_assignment = current_user.get("branch_assignment", {})
            managed_branch_id = branch_assignment.get("branch_id")

            if managed_branches:
                # Use managed_branches array if available
                filter_query["branch_details.branch_id"] = {"$in": managed_branches}
            elif managed_branch_id:
                # Fallback to single branch assignment
                filter_query["branch_details.branch_id"] = managed_branch_id
            else:
                # If no branches are managed, return empty result
                filter_query["branch_details.branch_id"] = {"$in": []}
        elif branch_id and branch_id != "all":
            # For superadmin and other roles, allow branch filtering
            filter_query["branch_details.branch_id"] = branch_id

        # Add payment type filter
        if payment_type and payment_type != "all":
            filter_query["payment_type"] = payment_type

        # Add payment method filter
        if payment_method and payment_method != "all":
            filter_query["payment_method"] = payment_method

        # Add payment status filter
        if payment_status and payment_status != "all":
            filter_query["payment_status"] = payment_status

        # Add amount range filter
        if amount_min is not None or amount_max is not None:
            amount_filter = {}
            if amount_min is not None:
                amount_filter["$gte"] = amount_min
            if amount_max is not None:
                amount_filter["$lte"] = amount_max
            filter_query["amount"] = amount_filter

        # Add search filter
        if search:
            search_filter = {
                "$or": [
                    {"transaction_id": {"$regex": search, "$options": "i"}},
                    {"course_details.course_name": {"$regex": search, "$options": "i"}},
                    {"branch_details.branch_name": {"$regex": search, "$options": "i"}},
                    {"notes": {"$regex": search, "$options": "i"}}
                ]
            }
            filter_query = {"$and": [filter_query, search_filter]} if filter_query else search_filter

        # Build date filter
        date_filter = {}
        if date_range and date_range != "all":
            now = dt.datetime.utcnow()

            if date_range == "current-month":
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["payment_date"] = {"$gte": start_date}
            elif date_range == "last-month":
                last_month = now.replace(day=1) - dt.timedelta(days=1)
                start_date = last_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["payment_date"] = {"$gte": start_date, "$lt": end_date}
            elif date_range == "current-quarter":
                quarter_start = now.replace(month=((now.month-1)//3)*3+1, day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["payment_date"] = {"$gte": quarter_start}
            elif date_range == "current-year":
                year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["payment_date"] = {"$gte": year_start}

        # Combine filters
        if date_filter:
            if isinstance(filter_query, dict) and "$and" in filter_query:
                filter_query["$and"].append(date_filter)
            elif filter_query:
                filter_query = {"$and": [filter_query, date_filter]}
            else:
                filter_query = date_filter

        try:
            # Get detailed payment records with pagination
            payment_pipeline = [
                {"$match": filter_query},
                {"$addFields": {
                    "branch_name": {"$ifNull": ["$branch_details.branch_name", "Unknown Branch"]},
                    "course_name": {"$ifNull": ["$course_details.course_name", "Unknown Course"]},
                    "formatted_amount": "$amount",
                    "formatted_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$payment_date"}},
                    "formatted_due_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$due_date"}}
                }},
                {"$sort": {"payment_date": -1, "created_at": -1}},
                {"$skip": skip},
                {"$limit": limit}
            ]

            payments = await db.payments.aggregate(payment_pipeline).to_list(limit)

            # Get total count for pagination
            count_pipeline = [{"$match": filter_query}, {"$count": "total"}]
            count_result = await db.payments.aggregate(count_pipeline).to_list(1)
            total_count = count_result[0]["total"] if count_result else 0

            # Calculate financial analytics
            analytics_filter = filter_query.copy() if isinstance(filter_query, dict) else {}

            # Total revenue by status
            revenue_by_status_pipeline = [
                {"$match": analytics_filter},
                {"$group": {
                    "_id": "$payment_status",
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1}
                }}
            ]
            revenue_by_status = await db.payments.aggregate(revenue_by_status_pipeline).to_list(10)

            # Revenue by payment method
            revenue_by_method_pipeline = [
                {"$match": {**analytics_filter, "payment_status": {"$in": ["paid", "completed"]}}},
                {"$group": {
                    "_id": "$payment_method",
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1}
                }}
            ]
            revenue_by_method = await db.payments.aggregate(revenue_by_method_pipeline).to_list(10)

            # Revenue by payment type
            revenue_by_type_pipeline = [
                {"$match": {**analytics_filter, "payment_status": {"$in": ["paid", "completed"]}}},
                {"$group": {
                    "_id": "$payment_type",
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1}
                }}
            ]
            revenue_by_type = await db.payments.aggregate(revenue_by_type_pipeline).to_list(10)

            # Revenue by branch
            revenue_by_branch_pipeline = [
                {"$match": {**analytics_filter, "payment_status": {"$in": ["paid", "completed"]}}},
                {"$group": {
                    "_id": "$branch_details.branch_id",
                    "branch_name": {"$first": "$branch_details.branch_name"},
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1}
                }},
                {"$sort": {"total_amount": -1}}
            ]
            revenue_by_branch = await db.payments.aggregate(revenue_by_branch_pipeline).to_list(20)

            # Monthly revenue trend
            monthly_revenue_pipeline = [
                {"$match": {**analytics_filter, "payment_status": {"$in": ["paid", "completed"]}}},
                {"$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m", "date": "$payment_date"}},
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1}
                }},
                {"$sort": {"_id": -1}},
                {"$limit": 12}
            ]
            monthly_revenue = await db.payments.aggregate(monthly_revenue_pipeline).to_list(12)

            # Outstanding payments (overdue)
            outstanding_pipeline = [
                {"$match": {
                    **analytics_filter,
                    "payment_status": {"$in": ["pending", "overdue"]},
                    "due_date": {"$lt": dt.datetime.utcnow()}
                }},
                {"$group": {
                    "_id": None,
                    "total_amount": {"$sum": "$amount"},
                    "count": {"$sum": 1}
                }}
            ]
            outstanding_result = await db.payments.aggregate(outstanding_pipeline).to_list(1)
            outstanding = outstanding_result[0] if outstanding_result else {"total_amount": 0, "count": 0}

            # Calculate summary statistics
            total_revenue = sum(item.get("total_amount", 0) for item in revenue_by_status if item["_id"] in ["paid", "completed"])
            total_transactions = sum(item.get("count", 0) for item in revenue_by_status if item["_id"] in ["paid", "completed"])
            pending_amount = sum(item.get("total_amount", 0) for item in revenue_by_status if item["_id"] == "pending")
            average_transaction = total_revenue / total_transactions if total_transactions > 0 else 0

            return {
                "payments": serialize_doc(payments),
                "pagination": {
                    "total": total_count,
                    "skip": skip,
                    "limit": limit,
                    "has_more": skip + len(payments) < total_count
                },
                "analytics": {
                    "revenue_by_status": serialize_doc(revenue_by_status),
                    "revenue_by_method": serialize_doc(revenue_by_method),
                    "revenue_by_type": serialize_doc(revenue_by_type),
                    "revenue_by_branch": serialize_doc(revenue_by_branch),
                    "monthly_revenue": serialize_doc(monthly_revenue),
                    "outstanding_payments": serialize_doc(outstanding)
                },
                "summary": {
                    "total_revenue": total_revenue,
                    "total_transactions": total_transactions,
                    "pending_amount": pending_amount,
                    "outstanding_amount": outstanding.get("total_amount", 0),
                    "outstanding_count": outstanding.get("count", 0),
                    "average_transaction": round(average_transaction, 2)
                },
                "filters_applied": {
                    "branch_id": branch_id,
                    "payment_type": payment_type,
                    "payment_method": payment_method,
                    "payment_status": payment_status,
                    "date_range": date_range,
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                    "search": search
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            print(f"Error generating financial reports: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to generate financial reports: {str(e)}")

    @staticmethod
    async def get_financial_report_filters(current_user: dict):
        """Get available filter options for financial reports"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Get all branches for branch filter
            branches = await db.branches.find(
                {},
                {"id": 1, "branch.name": 1, "name": 1, "branch.code": 1, "code": 1}
            ).to_list(100)

            branch_options = []
            for branch in branches:
                name = branch.get("branch", {}).get("name") or branch.get("name", "Unknown Branch")
                code = branch.get("branch", {}).get("code") or branch.get("code", "")
                display_name = f"{name}" + (f" ({code})" if code else "")
                branch_options.append({
                    "id": branch["id"],
                    "name": display_name
                })

            # Get unique payment types from payments collection
            payment_types_pipeline = [
                {"$group": {"_id": "$payment_type"}},
                {"$match": {"_id": {"$ne": None}}},
                {"$sort": {"_id": 1}}
            ]
            payment_types_result = await db.payments.aggregate(payment_types_pipeline).to_list(20)
            payment_types = [{"id": item["_id"], "name": item["_id"].replace("_", " ").title()} for item in payment_types_result]

            # Get unique payment methods from payments collection
            payment_methods_pipeline = [
                {"$group": {"_id": "$payment_method"}},
                {"$match": {"_id": {"$ne": None}}},
                {"$sort": {"_id": 1}}
            ]
            payment_methods_result = await db.payments.aggregate(payment_methods_pipeline).to_list(20)
            payment_methods = [{"id": item["_id"], "name": item["_id"].replace("_", " ").title()} for item in payment_methods_result]

            # Payment status options
            payment_statuses = [
                {"id": "paid", "name": "Paid"},
                {"id": "pending", "name": "Pending"},
                {"id": "completed", "name": "Completed"},
                {"id": "overdue", "name": "Overdue"},
                {"id": "cancelled", "name": "Cancelled"},
                {"id": "failed", "name": "Failed"}
            ]

            # Date range options
            date_ranges = [
                {"id": "current-month", "name": "Current Month"},
                {"id": "last-month", "name": "Last Month"},
                {"id": "current-quarter", "name": "Current Quarter"},
                {"id": "last-quarter", "name": "Last Quarter"},
                {"id": "current-year", "name": "Current Year"},
                {"id": "last-year", "name": "Last Year"}
            ]

            return {
                "filters": {
                    "branches": branch_options,
                    "payment_types": payment_types,
                    "payment_methods": payment_methods,
                    "payment_statuses": payment_statuses,
                    "date_ranges": date_ranges
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            print(f"Error loading financial report filters: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to load financial report filters: {str(e)}")

    @staticmethod
    async def get_student_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
        start_date: Optional[dt.datetime] = None,
        end_date: Optional[dt.datetime] = None
    ):
        """Get comprehensive student reports with branch-specific filtering for branch managers"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build filter query based on user role
        filter_query = {"role": "student", "is_active": True}
        managed_branch_ids = []

        # Handle branch manager role - filter by managed branches
        if current_user["role"] == "branch_manager":
            managed_branch_ids = current_user.get("managed_branches", [])
            if not managed_branch_ids:
                # If no managed branches found, return empty results
                return {
                    "student_reports": {
                        "enrollment_statistics": [],
                        "attendance_statistics": [],
                        "students_by_branch": []
                    },
                    "generated_at": dt.datetime.utcnow(),
                    "message": "No branches assigned to this branch manager"
                }

            # Filter students by managed branches through enrollments
            # We'll handle this in the aggregation pipelines below

        elif current_user["role"] == "coach_admin" and current_user.get("branch_id"):
            managed_branch_ids = [current_user["branch_id"]]
        elif branch_id and current_user["role"] in ("superadmin", "super_admin"):
            managed_branch_ids = [branch_id] if branch_id else []

        try:
            # Build enrollment filter for branch managers
            enrollment_filter = {"is_active": True}
            if managed_branch_ids:
                enrollment_filter["branch_id"] = {"$in": managed_branch_ids}
            if course_id:
                enrollment_filter["course_id"] = course_id

            # Student enrollment statistics
            enrollment_stats = await db.enrollments.aggregate([
                {"$match": enrollment_filter},
                {"$group": {
                    "_id": "$course_id",
                    "total_students": {"$sum": 1}
                }},
                {"$lookup": {
                    "from": "courses",
                    "localField": "_id",
                    "foreignField": "id",
                    "as": "course_info"
                }},
                {"$unwind": "$course_info"}
            ]).to_list(50)

            # Student attendance statistics with branch filtering
            attendance_filter = {}
            if start_date and end_date:
                attendance_filter["attendance_date"] = {"$gte": start_date, "$lte": end_date}
            if managed_branch_ids:
                attendance_filter["branch_id"] = {"$in": managed_branch_ids}

            attendance_stats = await db.attendance.aggregate([
                {"$match": attendance_filter},
                {"$group": {
                    "_id": "$student_id",
                    "total_classes": {"$sum": 1},
                    "present_classes": {"$sum": {"$cond": [{"$eq": ["$status", "present"]}, 1, 0]}}
                }},
                {"$project": {
                    "attendance_percentage": {
                        "$multiply": [
                            {"$divide": ["$present_classes", "$total_classes"]},
                            100
                        ]
                    }
                }}
            ]).to_list(1000)

            # Active students by branch - use enrollments for accurate branch filtering
            if current_user["role"] == "branch_manager" and managed_branch_ids:
                # For branch managers, get students through enrollments to ensure accurate branch association
                students_by_branch = await db.enrollments.aggregate([
                    {"$match": {"is_active": True, "branch_id": {"$in": managed_branch_ids}}},
                    {"$group": {
                        "_id": "$branch_id",
                        "total_students": {"$addToSet": "$student_id"}
                    }},
                    {"$project": {
                        "_id": 1,
                        "total_students": {"$size": "$total_students"}
                    }},
                    {"$lookup": {
                        "from": "branches",
                        "localField": "_id",
                        "foreignField": "id",
                        "as": "branch_info"
                    }},
                    {"$unwind": "$branch_info"}
                ]).to_list(20)
            else:
                # For superadmin and coach_admin, use the original approach
                students_by_branch = await db.users.aggregate([
                    {"$match": filter_query},
                    {"$group": {
                        "_id": "$branch_id",
                        "total_students": {"$sum": 1}
                    }},
                    {"$lookup": {
                        "from": "branches",
                        "localField": "_id",
                        "foreignField": "id",
                        "as": "branch_info"
                    }},
                    {"$unwind": "$branch_info"}
                ]).to_list(20)

            return {
                "student_reports": {
                    "enrollment_statistics": serialize_doc(enrollment_stats),
                    "attendance_statistics": serialize_doc(attendance_stats),
                    "students_by_branch": serialize_doc(students_by_branch)
                },
                "filters_applied": {
                    "branch_id": branch_id,
                    "course_id": course_id,
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                    "user_role": current_user["role"],
                    "managed_branches": managed_branch_ids if current_user["role"] == "branch_manager" else None
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error generating student reports: {str(e)}")

    @staticmethod
    async def list_student_report_rows(
        current_user: dict,
        *,
        q: Optional[str] = None,
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ):
        """M08-S02 filtered student list for reports UI."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_report_query import query_student_report_rows

        return await query_student_report_rows(
            current_user,
            q=q,
            branch_id=branch_id,
            course_id=course_id,
            is_active=is_active,
            start_date=start_date,
            end_date=end_date,
            skip=skip,
            limit=limit,
        )

    @staticmethod
    async def export_student_reports(
        current_user: dict,
        *,
        format: str = "csv",
        q: Optional[str] = None,
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
        is_active: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """M08-S02 CSV/Excel export with BM branch enforcement."""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        from utils.student_report_query import export_student_report

        return await export_student_report(
            current_user,
            format=format,
            q=q,
            branch_id=branch_id,
            course_id=course_id,
            is_active=is_active,
            start_date=start_date,
            end_date=end_date,
        )

    @staticmethod
    async def get_student_report_filters(current_user: dict):
        """Get available filter options for student reports (branch-specific for branch managers)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Determine which branches the user can access
            managed_branch_ids = []
            if current_user["role"] == "branch_manager":
                managed_branch_ids = current_user.get("managed_branches", [])
                if not managed_branch_ids:
                    return {
                        "filters": {
                            "branches": [],
                            "courses": [],
                            "categories": []
                        },
                        "message": "No branches assigned to this branch manager"
                    }
            elif current_user["role"] == "coach_admin" and current_user.get("branch_id"):
                managed_branch_ids = [current_user["branch_id"]]
            # For superadmin, no branch filtering needed (managed_branch_ids stays empty)

            # Get branches (filtered for branch managers)
            branch_filter = {"is_active": True}
            if managed_branch_ids:
                branch_filter["id"] = {"$in": managed_branch_ids}

            branches = await db.branches.find(branch_filter).to_list(100)
            branch_options = [
                {
                    "id": branch["id"],
                    "name": branch.get("branch", {}).get("name", "Unknown Branch"),
                    "location": f"{branch.get('branch', {}).get('address', {}).get('city', '')}, {branch.get('branch', {}).get('address', {}).get('state', '')}"
                }
                for branch in branches
            ]

            # Get courses (filtered by branch for branch managers)
            course_filter = {"is_active": True}
            if managed_branch_ids:
                # Get courses that are assigned to the managed branches
                course_filter["branch_assignments"] = {"$in": managed_branch_ids}

            courses = await db.courses.find(course_filter).to_list(200)
            course_options = [
                {
                    "id": course["id"],
                    "title": course.get("title", "Unknown Course"),
                    "code": course.get("code", ""),
                    "category": course.get("category", "")
                }
                for course in courses
            ]

            # Get categories from the courses
            categories = list(set([course.get("category", "") for course in courses if course.get("category")]))
            category_options = [{"name": cat} for cat in sorted(categories) if cat]

            return {
                "filters": {
                    "branches": branch_options,
                    "courses": course_options,
                    "categories": category_options
                },
                "user_role": current_user["role"],
                "managed_branches": managed_branch_ids if current_user["role"] == "branch_manager" else None,
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting student report filters: {str(e)}")

    @staticmethod
    async def get_coach_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        start_date: Optional[dt.datetime] = None,
        end_date: Optional[dt.datetime] = None
    ):
        """Get comprehensive coach reports"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build filter query
        filter_query = {"role": "coach", "is_active": True}
        if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
            filter_query["branch_id"] = current_user["branch_id"]
        elif branch_id:
            filter_query["branch_id"] = branch_id

        try:
            # Coach performance statistics
            coach_stats = await db.users.aggregate([
                {"$match": filter_query},
                {"$lookup": {
                    "from": "courses",
                    "localField": "id",
                    "foreignField": "instructor_id",
                    "as": "assigned_courses"
                }},
                {"$project": {
                    "full_name": 1,
                    "email": 1,
                    "branch_id": 1,
                    "total_courses": {"$size": "$assigned_courses"}
                }}
            ]).to_list(100)

            # Coach ratings if available
            coach_ratings = await db.coach_ratings.aggregate([
                {"$group": {
                    "_id": "$coach_id",
                    "average_rating": {"$avg": "$rating"},
                    "total_ratings": {"$sum": 1}
                }},
                {"$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "id",
                    "as": "coach_info"
                }},
                {"$unwind": "$coach_info"}
            ]).to_list(100)

            # Coaches by branch
            coaches_by_branch = await db.users.aggregate([
                {"$match": filter_query},
                {"$group": {
                    "_id": "$branch_id",
                    "total_coaches": {"$sum": 1}
                }},
                {"$lookup": {
                    "from": "branches",
                    "localField": "_id",
                    "foreignField": "id",
                    "as": "branch_info"
                }},
                {"$unwind": "$branch_info"}
            ]).to_list(20)

            return {
                "coach_reports": {
                    "coach_statistics": serialize_doc(coach_stats),
                    "coach_ratings": serialize_doc(coach_ratings),
                    "coaches_by_branch": serialize_doc(coaches_by_branch)
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error generating coach reports: {str(e)}")

    @staticmethod
    async def get_branch_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        metric: Optional[str] = None,
        date_range: Optional[str] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ):
        """Get comprehensive branch reports with filtering and search"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build filter query for branches
        branch_filter = {}
        if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
            branch_filter["id"] = current_user["branch_id"]
        elif branch_id and branch_id != "all":
            branch_filter["id"] = branch_id

        if status and status != "all":
            if status == "active":
                branch_filter["is_active"] = True
            elif status == "inactive":
                branch_filter["is_active"] = False

        # Build date filter for revenue calculations
        date_filter = {}
        if date_range and date_range != "all":
            now = dt.datetime.utcnow()

            if date_range == "current-month":
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["created_at"] = {"$gte": start_date}
            elif date_range == "last-month":
                last_month = now.replace(day=1) - dt.timedelta(days=1)
                start_date = last_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["created_at"] = {"$gte": start_date, "$lt": end_date}
            elif date_range == "current-quarter":
                quarter_start = now.replace(month=((now.month-1)//3)*3+1, day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["created_at"] = {"$gte": quarter_start}
            elif date_range == "current-year":
                year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                date_filter["created_at"] = {"$gte": year_start}

        try:
            # Get comprehensive branch statistics with enrollments and revenue
            branch_pipeline = [
                {"$match": branch_filter},
                # Get enrollments for each branch
                {"$lookup": {
                    "from": "enrollments",
                    "localField": "id",
                    "foreignField": "branch_id",
                    "as": "enrollments"
                }},
                # Get coaches for each branch
                {"$lookup": {
                    "from": "coaches",
                    "localField": "id",
                    "foreignField": "branch_id",
                    "as": "coaches"
                }},
                # Calculate statistics
                {"$addFields": {
                    "total_enrollments": {"$size": "$enrollments"},
                    "active_enrollments": {
                        "$size": {
                            "$filter": {
                                "input": "$enrollments",
                                "cond": {"$eq": ["$$this.is_active", True]}
                            }
                        }
                    },
                    "total_coaches": {"$size": "$coaches"},
                    "active_coaches": {
                        "$size": {
                            "$filter": {
                                "input": "$coaches",
                                "cond": {"$eq": ["$$this.is_active", True]}
                            }
                        }
                    }
                }},
                # Project final fields
                {"$project": {
                    "id": 1,
                    "branch_name": {"$ifNull": ["$branch.name", "$name"]},
                    "branch_code": {"$ifNull": ["$branch.code", "$code"]},
                    "location": {"$ifNull": ["$branch.address.city", "$location"]},
                    "state": {"$ifNull": ["$branch.address.state", "$state"]},
                    "is_active": {"$ifNull": ["$is_active", True]},
                    "total_enrollments": 1,
                    "active_enrollments": 1,
                    "total_coaches": 1,
                    "active_coaches": 1,
                    "created_at": 1,
                    "updated_at": 1
                }},
                {"$skip": skip},
                {"$limit": limit}
            ]

            branches = await db.branches.aggregate(branch_pipeline).to_list(limit)

            # Calculate revenue and performance for each branch
            for branch in branches:
                # Get revenue from enrollments
                revenue_pipeline = [
                    {"$match": {
                        "branch_id": branch["id"],
                        "payment_status": {"$in": ["paid", "completed"]},
                        **date_filter
                    }},
                    {"$group": {
                        "_id": None,
                        "total_revenue": {"$sum": "$fee_amount"},
                        "total_transactions": {"$sum": 1}
                    }}
                ]

                revenue_result = await db.enrollments.aggregate(revenue_pipeline).to_list(1)
                if revenue_result:
                    branch["total_revenue"] = revenue_result[0]["total_revenue"]
                    branch["total_transactions"] = revenue_result[0]["total_transactions"]
                else:
                    branch["total_revenue"] = 0
                    branch["total_transactions"] = 0

                # Calculate performance score based on multiple metrics
                performance_score = 0
                if branch["total_enrollments"] > 0:
                    enrollment_rate = (branch["active_enrollments"] / branch["total_enrollments"]) * 100
                    performance_score += enrollment_rate * 0.4

                if branch["total_coaches"] > 0:
                    coach_utilization = (branch["active_coaches"] / branch["total_coaches"]) * 100
                    performance_score += coach_utilization * 0.3

                # Revenue factor (normalized)
                if branch["total_revenue"] > 0:
                    revenue_factor = min(branch["total_revenue"] / 100000 * 30, 30)  # Max 30 points
                    performance_score += revenue_factor

                branch["performance_score"] = round(performance_score, 1)
                branch["status"] = "active" if branch.get("is_active", True) else "inactive"

            # Get total count for pagination
            total_count = await db.branches.count_documents(branch_filter)

            return {
                "branches": serialize_doc(branches),
                "pagination": {
                    "total": total_count,
                    "skip": skip,
                    "limit": limit,
                    "has_more": skip + len(branches) < total_count
                },
                "summary": {
                    "total_branches": total_count,
                    "active_branches": len([b for b in branches if b.get("is_active", True)]),
                    "total_enrollments": sum(b.get("total_enrollments", 0) for b in branches),
                    "total_revenue": sum(b.get("total_revenue", 0) for b in branches)
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            print(f"Error generating branch reports: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to generate branch reports: {str(e)}")

    @staticmethod
    async def get_branch_report_filters(current_user: dict):
        """Get available filter options for branch reports"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Get all branches for branch filter
            branches = await db.branches.find(
                {},
                {"id": 1, "branch.name": 1, "name": 1, "branch.code": 1, "code": 1}
            ).to_list(100)

            branch_options = []
            for branch in branches:
                name = branch.get("branch", {}).get("name") or branch.get("name", "Unknown Branch")
                code = branch.get("branch", {}).get("code") or branch.get("code", "")
                display_name = f"{name}" + (f" ({code})" if code else "")
                branch_options.append({
                    "id": branch["id"],
                    "name": display_name
                })

            # Performance metrics options
            metrics = [
                {"id": "enrollment", "name": "Enrollment Rate"},
                {"id": "revenue", "name": "Revenue"},
                {"id": "retention", "name": "Student Retention"},
                {"id": "satisfaction", "name": "Satisfaction Score"},
                {"id": "attendance", "name": "Attendance Rate"}
            ]

            # Date range options
            date_ranges = [
                {"id": "current-month", "name": "Current Month"},
                {"id": "last-month", "name": "Last Month"},
                {"id": "current-quarter", "name": "Current Quarter"},
                {"id": "last-quarter", "name": "Last Quarter"},
                {"id": "current-year", "name": "Current Year"},
                {"id": "last-year", "name": "Last Year"}
            ]

            # Status options
            statuses = [
                {"id": "active", "name": "Active"},
                {"id": "inactive", "name": "Inactive"},
                {"id": "under-review", "name": "Under Review"},
                {"id": "expanding", "name": "Expanding"}
            ]

            return {
                "filters": {
                    "branches": branch_options,
                    "metrics": metrics,
                    "date_ranges": date_ranges,
                    "statuses": statuses
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            print(f"Error loading branch report filters: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to load branch report filters: {str(e)}")

    @staticmethod
    async def get_course_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        category_id: Optional[str] = None
    ):
        """Get comprehensive course reports"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        # Build filter query
        filter_query = {"settings.active": True}
        if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
            filter_query["branch_id"] = current_user["branch_id"]
        elif branch_id:
            filter_query["branch_id"] = branch_id

        if category_id:
            filter_query["category_id"] = category_id

        try:
            # Course enrollment statistics
            course_enrollment_stats = await db.courses.aggregate([
                {"$match": filter_query},
                {"$lookup": {
                    "from": "enrollments",
                    "localField": "id",
                    "foreignField": "course_id",
                    "as": "enrollments"
                }},
                {"$lookup": {
                    "from": "categories",
                    "localField": "category_id",
                    "foreignField": "id",
                    "as": "category_info"
                }},
                {"$unwind": {"path": "$category_info", "preserveNullAndEmptyArrays": True}},
                {"$project": {
                    "title": 1,
                    "code": 1,
                    "category_name": "$category_info.name",
                    "total_enrollments": {"$size": "$enrollments"},
                    "active_enrollments": {
                        "$size": {
                            "$filter": {
                                "input": "$enrollments",
                                "cond": {"$eq": ["$$this.is_active", True]}
                            }
                        }
                    }
                }}
            ]).to_list(100)

            # Course completion rates
            course_completion_stats = await db.enrollments.aggregate([
                {"$match": {"is_active": True}},
                {"$group": {
                    "_id": "$course_id",
                    "total_enrollments": {"$sum": 1},
                    "completed_enrollments": {
                        "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                    }
                }},
                {"$project": {
                    "completion_rate": {
                        "$multiply": [
                            {"$divide": ["$completed_enrollments", "$total_enrollments"]},
                            100
                        ]
                    }
                }},
                {"$lookup": {
                    "from": "courses",
                    "localField": "_id",
                    "foreignField": "id",
                    "as": "course_info"
                }},
                {"$unwind": "$course_info"}
            ]).to_list(100)

            return {
                "course_reports": {
                    "course_enrollment_statistics": serialize_doc(course_enrollment_stats),
                    "course_completion_statistics": serialize_doc(course_completion_stats)
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error generating course reports: {str(e)}")

    @staticmethod
    async def get_report_categories():
        """Get available report categories"""
        return {
            "categories": [
                {
                    "id": "student",
                    "name": "Student Reports",
                    "description": "Student enrollment, attendance, and performance reports",
                    "reports_count": 8
                },
                {
                    "id": "master",
                    "name": "Master Reports",
                    "description": "Comprehensive system-wide reports and administrative summaries",
                    "reports_count": 8
                },
                {
                    "id": "course",
                    "name": "Course Reports",
                    "description": "Course enrollment, completion rates, and performance analytics",
                    "reports_count": 8
                },
                {
                    "id": "coach",
                    "name": "Coach Reports",
                    "description": "Coach performance, assignments, ratings, and analytics",
                    "reports_count": 8
                },
                {
                    "id": "branch",
                    "name": "Branch Reports",
                    "description": "Branch-wise analytics, performance, and operational reports",
                    "reports_count": 8
                },
                {
                    "id": "financial",
                    "name": "Financial Reports",
                    "description": "Payment, revenue, and financial analytics reports",
                    "reports_count": 8
                }
            ]
        }

    @staticmethod
    async def get_category_reports(category_id: str):
        """Get reports available for a specific category"""
        category_reports = {
            "student": [
                {"id": "student-enrollment-summary", "name": "Student Enrollment Summary"},
                {"id": "student-attendance-report", "name": "Student Attendance Report"},
                {"id": "student-performance-analysis", "name": "Student Performance Analysis"},
                {"id": "student-payment-history", "name": "Student Payment History"},
                {"id": "student-transfer-requests", "name": "Student Transfer Requests"},
                {"id": "student-course-changes", "name": "Student Course Changes"},
                {"id": "student-complaints-report", "name": "Student Complaints Report"},
                {"id": "student-demographics", "name": "Student Demographics"}
            ],
            "master": [
                {"id": "system-overview-dashboard", "name": "System Overview Dashboard"},
                {"id": "master-enrollment-report", "name": "Master Enrollment Report"},
                {"id": "master-attendance-summary", "name": "Master Attendance Summary"},
                {"id": "master-financial-summary", "name": "Master Financial Summary"},
                {"id": "activity-log-report", "name": "Activity Log Report"},
                {"id": "system-usage-analytics", "name": "System Usage Analytics"},
                {"id": "master-user-report", "name": "Master User Report"},
                {"id": "notification-delivery-report", "name": "Notification Delivery Report"}
            ],
            "course": [
                {"id": "course-enrollment-statistics", "name": "Course Enrollment Statistics"},
                {"id": "course-completion-rates", "name": "Course Completion Rates"},
                {"id": "course-popularity-analysis", "name": "Course Popularity Analysis"},
                {"id": "course-revenue-report", "name": "Course Revenue Report"},
                {"id": "course-category-analysis", "name": "Course Category Analysis"},
                {"id": "course-duration-effectiveness", "name": "Course Duration Effectiveness"},
                {"id": "course-feedback-summary", "name": "Course Feedback Summary"},
                {"id": "course-capacity-utilization", "name": "Course Capacity Utilization"}
            ],
            "coach": [
                {"id": "coach-performance-summary", "name": "Coach Performance Summary"},
                {"id": "coach-student-assignments", "name": "Coach Student Assignments"},
                {"id": "coach-ratings-analysis", "name": "Coach Ratings Analysis"},
                {"id": "coach-attendance-tracking", "name": "Coach Attendance Tracking"},
                {"id": "coach-course-load", "name": "Coach Course Load"},
                {"id": "coach-feedback-report", "name": "Coach Feedback Report"},
                {"id": "coach-productivity-metrics", "name": "Coach Productivity Metrics"},
                {"id": "coach-branch-distribution", "name": "Coach Branch Distribution"}
            ],
            "branch": [
                {"id": "branch-performance-overview", "name": "Branch Performance Overview"},
                {"id": "branch-enrollment-statistics", "name": "Branch Enrollment Statistics"},
                {"id": "branch-revenue-analysis", "name": "Branch Revenue Analysis"},
                {"id": "branch-capacity-utilization", "name": "Branch Capacity Utilization"},
                {"id": "branch-staff-allocation", "name": "Branch Staff Allocation"},
                {"id": "branch-operational-hours", "name": "Branch Operational Hours"},
                {"id": "branch-comparison-report", "name": "Branch Comparison Report"},
                {"id": "branch-growth-trends", "name": "Branch Growth Trends"}
            ],
            "financial": [
                {"id": "revenue-summary-report", "name": "Revenue Summary Report"},
                {"id": "payment-collection-analysis", "name": "Payment Collection Analysis"},
                {"id": "outstanding-dues-report", "name": "Outstanding Dues Report"},
                {"id": "payment-method-analysis", "name": "Payment Method Analysis"},
                {"id": "monthly-financial-summary", "name": "Monthly Financial Summary"},
                {"id": "admission-fee-collection", "name": "Admission Fee Collection"},
                {"id": "course-fee-breakdown", "name": "Course Fee Breakdown"},
                {"id": "refund-and-adjustments", "name": "Refund and Adjustments"}
            ]
        }

        if category_id not in category_reports:
            raise HTTPException(status_code=404, detail="Category not found")

        return {
            "category_id": category_id,
            "reports": category_reports[category_id]
        }

    @staticmethod
    async def get_report_filters(current_user: dict):
        """Get available filter options for reports"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Get available branches
            branch_filter = {"is_active": True}  # Only get active branches
            if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
                branch_filter["id"] = current_user["branch_id"]

            branches_raw = await db.branches.find(branch_filter, {"id": 1, "branch.name": 1}).to_list(100)

            # Format branches for frontend consumption and filter out invalid entries
            branches = []
            for branch in branches_raw:
                if branch.get("id") and branch.get("branch", {}).get("name"):
                    branches.append({
                        "id": branch["id"],
                        "name": branch["branch"]["name"]
                    })

            # Get available courses
            courses_raw = await db.courses.find(
                {"settings.active": True},
                {"id": 1, "title": 1, "code": 1}
            ).to_list(100)

            # Format courses and filter out invalid entries
            courses = []
            for course in courses_raw:
                if course.get("id") and course.get("title"):
                    courses.append({
                        "id": course["id"],
                        "title": course["title"],
                        "code": course.get("code", "")
                    })

            # Get available categories
            categories_raw = await db.categories.find({}, {"id": 1, "name": 1}).to_list(50)

            # Format categories and filter out invalid entries
            categories = []
            for category in categories_raw:
                if category.get("id") and category.get("name"):
                    categories.append({
                        "id": category["id"],
                        "name": category["name"]
                    })

            # Get available payment types
            payment_types = await db.payments.distinct("payment_type")
            # Filter out None/empty payment types
            payment_types = [pt for pt in payment_types if pt and pt.strip()]

            return {
                "filter_options": {
                    "branches": branches,
                    "courses": courses,
                    "categories": categories,
                    "payment_types": payment_types,
                    "date_ranges": [
                        {"id": "current-month", "name": "Current Month"},
                        {"id": "last-month", "name": "Last Month"},
                        {"id": "current-quarter", "name": "Current Quarter"},
                        {"id": "last-quarter", "name": "Last Quarter"},
                        {"id": "current-year", "name": "Current Year"},
                        {"id": "custom", "name": "Custom Range"}
                    ]
                }
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting report filters: {str(e)}")

    @staticmethod
    async def get_master_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        course_id: Optional[str] = None,
        area_of_expertise: Optional[str] = None,
        professional_experience: Optional[str] = None,
        designation_id: Optional[str] = None,
        active_only: bool = True,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ):
        """Get comprehensive master (coach) reports with filtering and search"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Build filter query - Query coaches collection instead of users collection
            filter_query = {}

            # Apply role-based filtering
            if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
                filter_query["branch_id"] = current_user["branch_id"]
            elif branch_id:
                filter_query["branch_id"] = branch_id

            # Apply filters
            if active_only:
                filter_query["is_active"] = True

            if area_of_expertise:
                filter_query["areas_of_expertise"] = {"$in": [area_of_expertise]}

            if professional_experience:
                filter_query["professional_info.professional_experience"] = professional_experience

            if designation_id:
                filter_query["professional_info.designation_id"] = designation_id

            if course_id:
                filter_query["assignment_details.courses"] = {"$in": [course_id]}

            # Apply search filter
            if search and len(search.strip()) >= 2:
                search_pattern = {"$regex": search.strip(), "$options": "i"}
                filter_query["$or"] = [
                    {"full_name": search_pattern},
                    {"contact_info.email": search_pattern},
                    {"contact_info.phone": search_pattern},
                    {"personal_info.first_name": search_pattern},
                    {"personal_info.last_name": search_pattern}
                ]

            # Get total count for pagination - Query coaches collection
            total_count = await db.coaches.count_documents(filter_query)

            # Get coaches with pagination - Query coaches collection
            coaches_cursor = db.coaches.find(filter_query).skip(skip).limit(limit)
            coaches = await coaches_cursor.to_list(length=limit)

            # Process coaches data and join with related information
            processed_coaches = []
            for coach in coaches:
                # Get branch information
                branch_info = None
                if coach.get("branch_id"):
                    branch_doc = await db.branches.find_one({"id": coach["branch_id"]})
                    if branch_doc:
                        branch_info = {
                            "id": branch_doc["id"],
                            "name": branch_doc.get("branch", {}).get("name", branch_doc.get("name", "")),
                            "code": branch_doc.get("branch", {}).get("code", branch_doc.get("code", ""))
                        }

                # Get course information
                course_details = []
                assigned_courses = coach.get("assignment_details", {}).get("courses", [])
                if assigned_courses:
                    courses_cursor = db.courses.find({"id": {"$in": assigned_courses}})
                    courses = await courses_cursor.to_list(length=100)
                    course_details = [
                        {
                            "id": course["id"],
                            "title": course["title"],
                            "code": course.get("code", ""),
                            "difficulty_level": course.get("difficulty_level", "")
                        }
                        for course in courses
                    ]

                # Build processed coach data - Handle coaches collection structure
                processed_coach = {
                    "id": coach["id"],
                    "full_name": coach.get("full_name", ""),
                    "first_name": coach.get("personal_info", {}).get("first_name", coach.get("first_name", "")),
                    "last_name": coach.get("personal_info", {}).get("last_name", coach.get("last_name", "")),
                    "email": coach.get("contact_info", {}).get("email", coach.get("email", "")),
                    "phone": coach.get("contact_info", {}).get("phone", coach.get("phone", "")),
                    "branch": branch_info,
                    "assigned_courses": course_details,
                    "areas_of_expertise": coach.get("areas_of_expertise", []),
                    "professional_experience": coach.get("professional_info", {}).get("professional_experience", ""),
                    "designation": coach.get("professional_info", {}).get("designation_id", ""),
                    "is_active": coach.get("is_active", False),
                    "join_date": coach.get("assignment_details", {}).get("join_date"),
                    "created_at": coach.get("created_at"),
                    "updated_at": coach.get("updated_at")
                }
                processed_coaches.append(processed_coach)

            return {
                "masters": serialize_doc(processed_coaches),
                "pagination": {
                    "total": total_count,
                    "skip": skip,
                    "limit": limit,
                    "has_more": skip + limit < total_count
                },
                "filters_applied": {
                    "branch_id": branch_id,
                    "course_id": course_id,
                    "area_of_expertise": area_of_expertise,
                    "professional_experience": professional_experience,
                    "designation_id": designation_id,
                    "active_only": active_only,
                    "search": search
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting master reports: {str(e)}")

    @staticmethod
    async def get_master_report_filters(current_user: dict):
        """Get available filter options for master reports"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        db = get_db()

        try:
            # Get branches (filtered by user role)
            branch_filter = {}
            if current_user["role"] == "coach_admin" and current_user.get("branch_id"):
                branch_filter["id"] = current_user["branch_id"]

            branches_cursor = db.branches.find(branch_filter)
            branches = await branches_cursor.to_list(length=100)
            branch_options = []
            for branch in branches:
                # Handle both nested and direct branch structures
                if 'branch' in branch and isinstance(branch['branch'], dict):
                    name = branch['branch'].get('name', 'Unknown')
                    code = branch['branch'].get('code', '')
                else:
                    name = branch.get('name', 'Unknown')
                    code = branch.get('code', '')

                display_name = f"{name} ({code})" if code else name
                branch_options.append({"id": branch["id"], "name": display_name})

            # Get courses
            courses_cursor = db.courses.find({"settings.active": True}, {"id": 1, "title": 1, "code": 1})
            courses = await courses_cursor.to_list(length=200)
            course_options = [
                {"id": course["id"], "name": f"{course['title']} ({course.get('code', '')})"}
                for course in courses
            ]

            # Get unique areas of expertise from coaches collection
            areas_pipeline = [
                {"$match": {"areas_of_expertise": {"$exists": True, "$ne": []}}},
                {"$unwind": "$areas_of_expertise"},
                {"$group": {"_id": "$areas_of_expertise"}},
                {"$sort": {"_id": 1}}
            ]
            areas_result = await db.coaches.aggregate(areas_pipeline).to_list(100)
            area_options = [{"id": area["_id"], "name": area["_id"]} for area in areas_result]

            # Get unique professional experience levels from coaches collection
            experience_pipeline = [
                {"$match": {"professional_info.professional_experience": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$professional_info.professional_experience"}},
                {"$sort": {"_id": 1}}
            ]
            experience_result = await db.coaches.aggregate(experience_pipeline).to_list(20)
            experience_options = [{"id": exp["_id"], "name": exp["_id"]} for exp in experience_result]

            # Get unique designations from coaches collection
            designation_pipeline = [
                {"$match": {"professional_info.designation_id": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$professional_info.designation_id"}},
                {"$sort": {"_id": 1}}
            ]
            designation_result = await db.coaches.aggregate(designation_pipeline).to_list(20)
            designation_options = [{"id": des["_id"], "name": des["_id"]} for des in designation_result]

            return {
                "filters": {
                    "branches": branch_options,
                    "courses": course_options,
                    "areas_of_expertise": area_options,
                    "professional_experience": experience_options,
                    "designations": designation_options,
                    "active_status": [
                        {"id": "true", "name": "Active Only"},
                        {"id": "false", "name": "Include Inactive"}
                    ]
                },
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting master report filters: {str(e)}")

    @staticmethod
    async def get_course_reports(
        current_user: dict,
        branch_id: Optional[str] = None,
        category_id: Optional[str] = None,
        difficulty_level: Optional[str] = None,
        active_only: bool = True,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ):
        """Get course reports with filtering and search - matches branches page filtering pattern"""
        try:
            db = get_db()

            # Apply role-based filtering using the same pattern as branches page
            managed_branch_ids = []
            current_role = current_user.get("role")

            if current_role == "branch_manager":
                # Use the same pattern as branches controller
                managed_branch_ids = current_user.get("managed_branches", [])

                if not managed_branch_ids:
                    # Return empty result if no branches are managed (same as branches page)
                    return {
                        "courses": [],
                        "pagination": {"total": 0, "skip": skip, "limit": limit, "has_more": False},
                        "summary": {"total_courses": 0, "active_courses": 0, "total_enrollments": 0, "active_enrollments": 0},
                        "message": "No branches assigned to this manager"
                    }

                # Validate branch_id if provided (same as branches page logic)
                if branch_id and branch_id not in managed_branch_ids:
                    return {
                        "courses": [],
                        "pagination": {"total": 0, "skip": skip, "limit": limit, "has_more": False},
                        "summary": {"total_courses": 0, "active_courses": 0, "total_enrollments": 0, "active_enrollments": 0},
                        "message": "Access denied: Branch not managed by this branch manager"
                    }

                print(f"Branch manager {current_user.get('id')} filtering by managed branches: {managed_branch_ids}")
            elif current_role == "coach_admin" and current_user.get("branch_id"):
                # Coach admin filtering (existing logic)
                managed_branch_ids = [current_user["branch_id"]]

            # Build course filter
            course_filter = {}
            if active_only:
                course_filter["settings.active"] = True
            if category_id:
                course_filter["category_id"] = category_id
            if difficulty_level:
                course_filter["difficulty_level"] = difficulty_level

            # Search filter - handle missing fields gracefully
            if search:
                search_regex = {"$regex": search, "$options": "i"}
                course_filter["$or"] = [
                    {"title": search_regex},
                    {"code": {"$exists": True, "$regex": search, "$options": "i"}},
                    {"description": {"$exists": True, "$regex": search, "$options": "i"}}
                ]

            # Build aggregation pipeline
            pipeline = [
                {"$match": course_filter},
                {
                    "$lookup": {
                        "from": "categories",
                        "localField": "category_id",
                        "foreignField": "id",
                        "as": "category"
                    }
                },
                {
                    "$lookup": {
                        "from": "enrollments",
                        "localField": "id",
                        "foreignField": "course_id",
                        "as": "enrollments"
                    }
                }
            ]

            # Add branch filtering based on user role and permissions
            if current_user["role"] == "branch_manager":
                # For branch managers, filter enrollments to only include their managed branches
                if branch_id:
                    # Specific branch requested
                    pipeline.append({
                        "$addFields": {
                            "enrollments": {
                                "$filter": {
                                    "input": "$enrollments",
                                    "cond": {"$eq": ["$$this.branch_id", branch_id]}
                                }
                            }
                        }
                    })
                else:
                    # Filter by all managed branches
                    pipeline.append({
                        "$addFields": {
                            "enrollments": {
                                "$filter": {
                                    "input": "$enrollments",
                                    "cond": {"$in": ["$$this.branch_id", managed_branch_ids]}
                                }
                            }
                        }
                    })
            elif branch_id:
                # For other roles with specific branch filter
                pipeline.append({
                    "$addFields": {
                        "enrollments": {
                            "$filter": {
                                "input": "$enrollments",
                                "cond": {"$eq": ["$$this.branch_id", branch_id]}
                            }
                        }
                    }
                })

            # Add enrollment statistics
            pipeline.append({
                "$addFields": {
                    "category_name": {"$arrayElemAt": ["$category.name", 0]},
                    "total_enrollments": {"$size": "$enrollments"},
                    "active_enrollments": {
                        "$size": {
                            "$filter": {
                                "input": "$enrollments",
                                "cond": {"$eq": ["$$this.is_active", True]}
                            }
                        }
                    }
                }
            })

            # For branch managers, only show courses that have enrollments in their managed branches
            if current_user["role"] == "branch_manager":
                pipeline.append({
                    "$match": {
                        "total_enrollments": {"$gt": 0}
                    }
                })

            # Add sorting, skip, and limit
            pipeline.extend([
                {"$sort": {"title": 1}},
                {"$skip": skip},
                {"$limit": limit + 1}  # Get one extra to check if there are more
            ])

            # Execute aggregation
            courses_cursor = db.courses.aggregate(pipeline)
            courses = await courses_cursor.to_list(length=limit + 1)

            # Check if there are more results
            has_more = len(courses) > limit
            if has_more:
                courses = courses[:limit]

            # Get total count for pagination
            count_pipeline = [
                {"$match": course_filter},
                {
                    "$lookup": {
                        "from": "enrollments",
                        "localField": "id",
                        "foreignField": "course_id",
                        "as": "enrollments"
                    }
                }
            ]

            # Apply branch filtering for count
            if current_user["role"] == "branch_manager":
                if branch_id:
                    count_pipeline.append({
                        "$addFields": {
                            "enrollments": {
                                "$filter": {
                                    "input": "$enrollments",
                                    "cond": {"$eq": ["$$this.branch_id", branch_id]}
                                }
                            }
                        }
                    })
                else:
                    count_pipeline.append({
                        "$addFields": {
                            "enrollments": {
                                "$filter": {
                                    "input": "$enrollments",
                                    "cond": {"$in": ["$$this.branch_id", managed_branch_ids]}
                                }
                            }
                        }
                    })
                # Only count courses with enrollments in managed branches
                count_pipeline.append({
                    "$match": {
                        "enrollments": {"$ne": []}
                    }
                })
            elif branch_id:
                count_pipeline.append({
                    "$addFields": {
                        "enrollments": {
                            "$filter": {
                                "input": "$enrollments",
                                "cond": {"$eq": ["$$this.branch_id", branch_id]}
                            }
                        }
                    }
                })

            count_pipeline.append({"$count": "total"})

            count_result = await db.courses.aggregate(count_pipeline).to_list(length=1)
            total_count = count_result[0]["total"] if count_result else 0

            # Format course data
            formatted_courses = []
            for course in courses:
                # Get branch-specific enrollment data if branch_id is specified
                branch_enrollments = []
                if branch_id:
                    branch_enrollments = [e for e in course.get("enrollments", []) if e.get("branch_id") == branch_id]
                else:
                    branch_enrollments = course.get("enrollments", [])

                formatted_course = {
                    "id": course.get("id", "unknown"),
                    "title": course.get("title", "Unknown Course"),
                    "code": course.get("code", "N/A"),
                    "description": course.get("description", ""),
                    "difficulty_level": course.get("difficulty_level", ""),
                    "category_name": course.get("category_name", "Unknown"),
                    "pricing": course.get("pricing", {}),
                    "total_enrollments": len(branch_enrollments),
                    "active_enrollments": len([e for e in branch_enrollments if e.get("is_active", False)]),
                    "inactive_enrollments": len([e for e in branch_enrollments if not e.get("is_active", True)]),
                    "is_active": course.get("settings", {}).get("active", False),
                    "offers_certification": course.get("settings", {}).get("offers_certification", False),
                    "created_at": course.get("created_at"),
                    "updated_at": course.get("updated_at")
                }
                formatted_courses.append(formatted_course)

            return {
                "courses": formatted_courses,
                "pagination": {
                    "total": total_count,
                    "skip": skip,
                    "limit": limit,
                    "has_more": has_more
                },
                "summary": {
                    "total_courses": total_count,
                    "active_courses": len([c for c in formatted_courses if c["is_active"]]),
                    "total_enrollments": sum(c["total_enrollments"] for c in formatted_courses),
                    "active_enrollments": sum(c["active_enrollments"] for c in formatted_courses)
                }
            }

        except Exception as e:
            print(f"Error getting course reports: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error getting course reports: {str(e)}")

    @staticmethod
    async def get_course_report_filters(current_user: dict):
        """Get filter options for course reports"""
        try:
            db = get_db()

            # Get branches (filtered by user role)
            branch_filter = {}
            managed_branch_ids = []

            if current_user["role"] == "branch_manager":
                managed_branch_ids = current_user.get("managed_branches", [])
                if not managed_branch_ids:
                    return {
                        "filters": {
                            "branches": [],
                            "categories": [],
                            "difficulty_levels": [],
                            "active_status": [
                                {"id": "true", "name": "Active Only"},
                                {"id": "false", "name": "Include Inactive"}
                            ]
                        },
                        "message": "No branches assigned to this branch manager",
                        "generated_at": dt.datetime.utcnow()
                    }
                branch_filter["id"] = {"$in": managed_branch_ids}
            elif current_user["role"] == "coach_admin" and current_user.get("branch_id"):
                branch_filter["id"] = current_user["branch_id"]

            branches_cursor = db.branches.find(branch_filter)
            branches = await branches_cursor.to_list(length=100)
            branch_options = []
            for branch in branches:
                # Handle both nested and direct branch structures
                if 'branch' in branch and isinstance(branch['branch'], dict):
                    name = branch['branch'].get('name', 'Unknown')
                    code = branch['branch'].get('code', '')
                else:
                    name = branch.get('name', 'Unknown')
                    code = branch.get('code', '')

                display_name = f"{name} ({code})" if code else name
                branch_options.append({"id": branch["id"], "name": display_name})

            # Get categories
            categories_cursor = db.categories.find({"is_active": True}, {"id": 1, "name": 1})
            categories = await categories_cursor.to_list(length=100)
            category_options = [
                {"id": category["id"], "name": category["name"]}
                for category in categories
            ]

            # Get difficulty levels from existing courses
            difficulty_pipeline = [
                {"$match": {"difficulty_level": {"$exists": True, "$ne": ""}}},
                {"$group": {"_id": "$difficulty_level"}},
                {"$sort": {"_id": 1}}
            ]
            difficulty_cursor = db.courses.aggregate(difficulty_pipeline)
            difficulty_levels = await difficulty_cursor.to_list(length=50)
            difficulty_options = [
                {"id": level["_id"], "name": level["_id"]}
                for level in difficulty_levels
            ]

            return {
                "filters": {
                    "branches": branch_options,
                    "categories": category_options,
                    "difficulty_levels": difficulty_options,
                    "active_status": [
                        {"id": "true", "name": "Active Only"},
                        {"id": "false", "name": "Include Inactive"}
                    ]
                },
                "user_role": current_user["role"],
                "managed_branches": managed_branch_ids if current_user["role"] == "branch_manager" else None,
                "generated_at": dt.datetime.utcnow()
            }

        except Exception as e:
            print(f"Error getting course report filters: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error getting course report filters: {str(e)}")
