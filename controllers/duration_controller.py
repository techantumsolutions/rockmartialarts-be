from fastapi import HTTPException, Depends
from typing import Optional, List
from datetime import datetime

from models.duration_models import DurationCreate, DurationUpdate, Duration, DurationResponse
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin
from utils.database import get_db
from utils.helpers import serialize_doc

class DurationController:
    @staticmethod
    async def create_duration(
        duration_data: DurationCreate,
        current_user: dict = None
    ):
        """Create new duration (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        db = get_db()
        
        # Check if duration code already exists
        existing_duration = await db.durations.find_one({"code": duration_data.code})
        if existing_duration:
            raise HTTPException(status_code=400, detail="Duration code already exists")
        
        # Validate duration values
        if duration_data.duration_months <= 0:
            raise HTTPException(status_code=400, detail="Duration months must be greater than 0")
        
        if duration_data.duration_days is not None and duration_data.duration_days < 0:
            raise HTTPException(status_code=400, detail="Duration days cannot be negative")
        
        if duration_data.pricing_multiplier <= 0:
            raise HTTPException(status_code=400, detail="Pricing multiplier must be greater than 0")
        
        duration = Duration(**duration_data.dict())
        duration_dict = duration.dict()
        
        await db.durations.insert_one(duration_dict)
        return {"message": "Duration created successfully", "duration_id": duration.id}

    @staticmethod
    async def get_durations(
        active_only: bool = True,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = None
    ):
        """Get durations with optional filtering"""
        db = get_db()
        
        # Build query
        query = {}
        if active_only:
            query["is_active"] = True
        
        # Get durations with sorting
        durations_cursor = db.durations.find(query).sort("display_order", 1).skip(skip).limit(limit)
        durations = await durations_cursor.to_list(limit)
        
        # Get total count
        total = await db.durations.count_documents(query)
        
        # Enrich durations with additional data
        enriched_durations = []
        for duration in durations:
            # Count enrollments using this duration (if we have a duration field in enrollments)
            # For now, we'll set it to 0 as the enrollment model doesn't have duration_id
            enrollment_count = 0
            
            duration_response = {
                "id": duration["id"],
                "name": duration["name"],
                "code": duration["code"],
                "duration_months": duration["duration_months"],
                "duration_days": duration.get("duration_days"),
                "description": duration.get("description"),
                "is_active": duration["is_active"],
                "display_order": duration["display_order"],
                "pricing_multiplier": duration["pricing_multiplier"],
                "enrollment_count": enrollment_count,
                "created_at": duration["created_at"],
                "updated_at": duration["updated_at"]
            }
            
            enriched_durations.append(duration_response)
        
        return {
            "message": f"Retrieved {len(enriched_durations)} durations successfully",
            "durations": enriched_durations,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    async def get_public_durations(
        active_only: bool = True,
        skip: int = 0,
        limit: int = 100
    ):
        """Get durations - Public endpoint (no authentication required)"""
        db = get_db()
        
        # Build query
        query = {}
        if active_only:
            query["is_active"] = True
        
        # Apply pagination
        if limit > 100:
            limit = 100  # Cap at 100 for public endpoint
        
        # Get durations with sorting
        durations_cursor = db.durations.find(query).sort("display_order", 1).skip(skip).limit(limit)
        durations = await durations_cursor.to_list(limit)
        
        # Get total count
        total = await db.durations.count_documents(query)
        
        # Format durations for public consumption
        public_durations = []
        for duration in durations:
            public_duration = {
                "id": duration["id"],
                "name": duration["name"],
                "code": duration["code"],
                "duration_months": duration["duration_months"],
                "duration_days": duration.get("duration_days"),
                "description": duration.get("description"),
                "pricing_multiplier": duration["pricing_multiplier"]
            }
            
            public_durations.append(public_duration)
        
        return {
            "message": f"Retrieved {len(public_durations)} durations successfully",
            "durations": public_durations,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    async def get_duration(
        duration_id: str,
        current_user: dict = None
    ):
        """Get single duration by ID"""
        db = get_db()
        
        duration = await db.durations.find_one({"id": duration_id})
        if not duration:
            raise HTTPException(status_code=404, detail="Duration not found")
        
        # Count enrollments using this duration
        enrollment_count = 0
        
        duration_response = {
            "id": duration["id"],
            "name": duration["name"],
            "code": duration["code"],
            "duration_months": duration["duration_months"],
            "duration_days": duration.get("duration_days"),
            "description": duration.get("description"),
            "is_active": duration["is_active"],
            "display_order": duration["display_order"],
            "pricing_multiplier": duration["pricing_multiplier"],
            "enrollment_count": enrollment_count,
            "created_at": duration["created_at"],
            "updated_at": duration["updated_at"]
        }
        
        return duration_response

    @staticmethod
    async def update_duration(
        duration_id: str,
        duration_update: DurationUpdate,
        current_user: dict = None
    ):
        """Update duration (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        db = get_db()
        
        # Check if duration exists
        existing_duration = await db.durations.find_one({"id": duration_id})
        if not existing_duration:
            raise HTTPException(status_code=404, detail="Duration not found")
        
        # Check if new code conflicts with existing durations
        if duration_update.code and duration_update.code != existing_duration["code"]:
            code_conflict = await db.durations.find_one({
                "code": duration_update.code,
                "id": {"$ne": duration_id}
            })
            if code_conflict:
                raise HTTPException(status_code=400, detail="Duration code already exists")
        
        # Validate duration values
        if duration_update.duration_months is not None and duration_update.duration_months <= 0:
            raise HTTPException(status_code=400, detail="Duration months must be greater than 0")
        
        if duration_update.duration_days is not None and duration_update.duration_days < 0:
            raise HTTPException(status_code=400, detail="Duration days cannot be negative")
        
        if duration_update.pricing_multiplier is not None and duration_update.pricing_multiplier <= 0:
            raise HTTPException(status_code=400, detail="Pricing multiplier must be greater than 0")
        
        # Prepare update data
        update_data = {k: v for k, v in duration_update.dict().items() if v is not None}
        update_data["updated_at"] = datetime.utcnow()
        
        # Update duration
        result = await db.durations.update_one(
            {"id": duration_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Duration not found")
        
        return {"message": "Duration updated successfully"}

    @staticmethod
    async def delete_duration(
        duration_id: str,
        current_user: dict = None
    ):
        """Delete duration (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        db = get_db()
        
        # Check if duration exists
        duration = await db.durations.find_one({"id": duration_id})
        if not duration:
            raise HTTPException(status_code=404, detail="Duration not found")
        
        # Check if duration is being used in any enrollments
        # Note: This would need to be implemented based on how duration is stored in enrollments
        # For now, we'll allow deletion but in production you might want to check usage
        
        # Delete duration
        result = await db.durations.delete_one({"id": duration_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Duration not found")
        
        return {"message": "Duration deleted successfully"}

    @staticmethod
    async def get_durations_by_course(
        course_id: str,
        active_only: bool = True,
        include_pricing: bool = True,
        branch_id: Optional[str] = None,
    ):
        """Get available durations for a specific course - Public endpoint"""
        db = get_db()

        # Verify course exists
        course = await db.courses.find_one({"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Get all active durations
        query = {}
        if active_only:
            query["is_active"] = True

        durations = await db.durations.find(query).sort("display_order", 1).to_list(100)

        # Optional branch context: branch/course tenure fees first; batch schedule only for enabled flags
        branch_batch = None
        branch_pricing_for_course: dict = {}
        if branch_id:
            from controllers.payment_controller import (
                _course_branch_pricing_map,
                _course_fee_per_duration_map,
            )

            branch = await db.branches.find_one({"id": branch_id})
            if not branch:
                raise HTTPException(status_code=404, detail="Branch not found")

            branch_pricing_all = _course_branch_pricing_map(course)
            bp_entry = branch_pricing_all.get(branch_id)
            if isinstance(bp_entry, dict):
                branch_pricing_for_course = bp_entry

            assignments = branch.get("assignments", {}) or {}
            course_schedule = assignments.get("course_schedule", []) or []
            for entry in course_schedule:
                if str(entry.get("course_id", "")) != str(course_id):
                    continue
                batches = entry.get("batches", []) or []
                if batches:
                    branch_batch = batches[0]
                break

        # Enrich durations with pricing calculations
        enriched_durations = []
        base_price = course.get("pricing", {}).get("amount", 0)
        currency = course.get("pricing", {}).get("currency", "INR")
        if branch_id:
            course_fee_per_duration = _course_fee_per_duration_map(course)
        else:
            course_fee_per_duration = course.get("fee_per_duration", {}) or {}
        course_pricing_type_per_duration = course.get("pricing_type_per_duration", {}) or {}

        for duration in durations:
            multiplier = duration.get("pricing_multiplier", 1.0)
            duration_id = duration.get("id")
            duration_code = duration.get("code")

            key_candidates = []
            if duration_id:
                key_candidates.append(str(duration_id))
            if duration_code:
                key_candidates.append(str(duration_code))

            duration_enabled = True
            price_raw = None
            # Match payment_controller: fee_per_duration cells are package totals unless marked monthly.
            pricing_type = "flat"
            used_branch_config = False

            # Super-admin batch fees are the live price source. Course-level branch_pricing
            # often still holds leftover amounts (₹1500) that are no longer edited in admin.
            if branch_batch:
                batch_fee_per_duration = branch_batch.get("fee_per_duration", {}) or {}
                batch_pricing_type_per_duration = (
                    branch_batch.get("pricing_type_per_duration", {}) or {}
                )
                batch_enabled_per_duration = (
                    branch_batch.get("enabled_per_duration", {}) or {}
                )

                for key in key_candidates:
                    if key in batch_enabled_per_duration:
                        duration_enabled = bool(batch_enabled_per_duration.get(key))
                        break

                for key in key_candidates:
                    if key in batch_fee_per_duration:
                        price_raw = batch_fee_per_duration.get(key)
                        pt = batch_pricing_type_per_duration.get(key)
                        pricing_type = pt if pt is not None and str(pt).strip() else "flat"
                        used_branch_config = True
                        break

            if price_raw is None and branch_pricing_for_course:
                for key in key_candidates:
                    if key in branch_pricing_for_course and branch_pricing_for_course[key] is not None:
                        price_raw = branch_pricing_for_course.get(key)
                        used_branch_config = True
                        break

            if price_raw is None:
                for key in key_candidates:
                    if key in course_fee_per_duration:
                        price_raw = course_fee_per_duration.get(key)
                        pt = course_pricing_type_per_duration.get(key)
                        pricing_type = pt if pt is not None and str(pt).strip() else "flat"
                        break

            calculated_price = None
            if include_pricing:
                if price_raw is not None:
                    try:
                        parsed = float(price_raw)
                    except (TypeError, ValueError):
                        parsed = None

                    if parsed is not None and parsed > 0:
                        if str(pricing_type).lower() == "flat":
                            calculated_price = parsed
                        else:
                            calculated_price = parsed * float(duration.get("duration_months", 0) or 0)
                elif not branch_batch:
                    calculated_price = base_price * multiplier

            if branch_batch and used_branch_config:
                if not duration_enabled:
                    continue
                if include_pricing and (calculated_price is None or calculated_price <= 0):
                    continue

            duration_data = {
                "id": duration["id"],
                "name": duration["name"],
                "code": duration["code"],
                "duration_months": duration["duration_months"],
                "duration_days": duration.get("duration_days"),
                "pricing_multiplier": multiplier,
                "calculated_price": calculated_price,
                "description": duration.get("description"),
                "is_enabled": duration_enabled,
            }
            enriched_durations.append(duration_data)

        return {
            "message": f"Retrieved {len(enriched_durations)} durations for course successfully",
            "course": {
                "id": course["id"],
                "title": course["title"],
                "base_price": base_price,
                "currency": currency
            },
            "durations": enriched_durations,
            "total": len(enriched_durations)
        }

    @staticmethod
    async def get_durations_by_location_course(
        location_id: str,
        course_id: str,
        include_pricing: bool = True,
        include_branches: bool = False
    ):
        """Get durations based on location and course combination - Public endpoint"""
        db = get_db()

        # Verify location exists
        location = await db.locations.find_one({"id": location_id})
        if not location:
            raise HTTPException(status_code=404, detail="Location not found")

        # Verify course exists
        course = await db.courses.find_one({"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        # Check if course is available at this location
        branches = await db.branches.find({
            "branch.address.city": {"$regex": location["name"], "$options": "i"},
            "assignments.courses": course_id,
            "is_active": True
        }).to_list(100)

        if not branches:
            return {
                "message": "Course not available at this location",
                "location": {
                    "id": location["id"],
                    "name": location["name"],
                    "code": location["code"]
                },
                "course": {
                    "id": course["id"],
                    "title": course["title"]
                },
                "durations": [],
                "total": 0
            }

        # Get all active durations
        durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)

        # Enrich durations with pricing and branch availability
        enriched_durations = []
        base_price = course.get("pricing", {}).get("amount", 0)

        for duration in durations:
            multiplier = duration.get("pricing_multiplier", 1.0)
            final_price = base_price * multiplier if include_pricing else None

            # Get branch availability if requested
            available_at_branches = []
            if include_branches:
                for branch in branches:
                    available_at_branches.append({
                        "branch_id": branch["id"],
                        "branch_name": branch["branch"]["name"],
                        "availability": "available"  # Could be enhanced with actual availability logic
                    })

            duration_data = {
                "id": duration["id"],
                "name": duration["name"],
                "code": duration["code"],
                "duration_months": duration["duration_months"],
                "duration_days": duration.get("duration_days"),
                "pricing_multiplier": multiplier,
                "final_price": final_price,
                "available_at_branches": available_at_branches
            }
            enriched_durations.append(duration_data)

        return {
            "message": f"Retrieved {len(enriched_durations)} durations for location-course combination successfully",
            "location": {
                "id": location["id"],
                "name": location["name"],
                "code": location["code"]
            },
            "course": {
                "id": course["id"],
                "title": course["title"],
                "base_price": base_price
            },
            "durations": enriched_durations,
            "total": len(enriched_durations)
        }
