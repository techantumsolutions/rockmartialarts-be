from fastapi import HTTPException, Depends
from typing import Optional, List
from datetime import datetime

from models.category_models import CategoryCreate, CategoryUpdate, Category, CategoryResponse
from models.user_models import UserRole
from utils.auth import require_role, get_current_active_user
from utils.unified_auth import require_role_unified, get_current_user_or_superadmin
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.course_hierarchy import (
    assert_parent_for_subcategory,
    course_count_query,
    courses_for_category_query,
    ensure_category_slug,
    name_slug,
    public_category_nav_item,
    resolve_public_category,
)

class CategoryController:
    @staticmethod
    async def create_category(
        category_data: CategoryCreate,
        current_user: dict = None
    ):
        """Create new category (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        db = get_db()
        
        # Check if category code already exists
        existing_category = await db.categories.find_one({"code": category_data.code})
        if existing_category:
            raise HTTPException(status_code=400, detail="Category code already exists")

        payload = category_data.dict()
        parent_id = (payload.get("parent_category_id") or "").strip() or None
        if parent_id:
            await assert_parent_for_subcategory(db, parent_id)
        payload["parent_category_id"] = parent_id
        payload["slug"] = await ensure_category_slug(
            db, payload.get("name") or "category", existing_slug=payload.get("slug")
        )

        category = Category(**payload)
        category_dict = category.dict()
        
        await db.categories.insert_one(category_dict)
        return {"message": "Category created successfully", "category_id": category.id}

    @staticmethod
    async def get_categories(
        parent_id: Optional[str] = None,
        active_only: bool = True,
        include_subcategories: bool = False,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = None
    ):
        """Get categories with optional filtering"""
        db = get_db()
        
        # Build query
        query = {}
        if parent_id is not None:
            query["parent_category_id"] = parent_id
        if active_only:
            query["is_active"] = True
        
        # Get categories with sorting
        categories_cursor = db.categories.find(query).sort("display_order", 1).skip(skip).limit(limit)
        categories = await categories_cursor.to_list(limit)
        
        # Get total count
        total = await db.categories.count_documents(query)
        
        # Enrich categories with additional data
        enriched_categories = []
        for category in categories:
            # Count courses in this category
            course_count = await db.courses.count_documents(course_count_query(category["id"]))
            
            category_response = {
                "id": category["id"],
                "name": category["name"],
                "code": category["code"],
                "description": category.get("description"),
                "parent_category_id": category.get("parent_category_id"),
                "slug": category.get("slug"),
                "is_active": category["is_active"],
                "display_order": category["display_order"],
                "icon_url": category.get("icon_url"),
                "color_code": category.get("color_code"),
                "course_count": course_count,
                "created_at": category["created_at"],
                "updated_at": category["updated_at"]
            }
            
            # Include subcategories if requested
            if include_subcategories:
                subcategories = await db.categories.find({
                    "parent_category_id": category["id"],
                    "is_active": True
                }).sort("display_order", 1).to_list(100)
                
                subcategory_list = []
                for subcat in subcategories:
                    subcat_course_count = await db.courses.count_documents(course_count_query(subcat["id"]))
                    subcategory_list.append({
                        "id": subcat["id"],
                        "name": subcat["name"],
                        "code": subcat["code"],
                        "description": subcat.get("description"),
                        "parent_category_id": subcat.get("parent_category_id"),
                        "slug": subcat.get("slug"),
                        "is_active": subcat["is_active"],
                        "display_order": subcat["display_order"],
                        "icon_url": subcat.get("icon_url"),
                        "color_code": subcat.get("color_code"),
                        "course_count": subcat_course_count,
                        "created_at": subcat["created_at"],
                        "updated_at": subcat["updated_at"]
                    })
                
                category_response["subcategories"] = subcategory_list
            
            enriched_categories.append(category_response)
        
        return {
            "message": f"Retrieved {len(enriched_categories)} categories successfully",
            "categories": enriched_categories,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    async def get_public_categories(
        active_only: bool = True,
        include_subcategories: bool = True,
        skip: int = 0,
        limit: int = 100
    ):
        """Get categories - Public endpoint (no authentication required)"""
        db = get_db()
        
        # Build query for top-level categories only
        query = {"parent_category_id": None}
        if active_only:
            query["is_active"] = True
        
        # Apply pagination
        if limit > 100:
            limit = 100  # Cap at 100 for public endpoint
        
        # Get categories with sorting
        categories_cursor = db.categories.find(query).sort("display_order", 1).skip(skip).limit(limit)
        categories = await categories_cursor.to_list(limit)
        
        # Get total count
        total = await db.categories.count_documents(query)
        
        # Format categories for public consumption
        public_categories = []
        for category in categories:
            # Count courses in this category
            course_count = await db.courses.count_documents(course_count_query(category["id"]))
            
            public_category = {
                "id": category["id"],
                "name": category["name"],
                "code": category["code"],
                "description": category.get("description"),
                "slug": category.get("slug"),
                "icon_url": category.get("icon_url"),
                "color_code": category.get("color_code"),
                "course_count": course_count
            }
            
            # Include subcategories if requested
            if include_subcategories:
                subcategories = await db.categories.find({
                    "parent_category_id": category["id"],
                    "is_active": True
                }).sort("display_order", 1).to_list(100)
                
                subcategory_list = []
                for subcat in subcategories:
                    subcat_course_count = await db.courses.count_documents(course_count_query(subcat["id"]))
                    subcategory_list.append({
                        "id": subcat["id"],
                        "name": subcat["name"],
                        "code": subcat["code"],
                        "description": subcat.get("description"),
                        "slug": subcat.get("slug"),
                        "icon_url": subcat.get("icon_url"),
                        "color_code": subcat.get("color_code"),
                        "course_count": subcat_course_count
                    })
                
                public_category["subcategories"] = subcategory_list
            
            public_categories.append(public_category)
        
        return {
            "message": f"Retrieved {len(public_categories)} categories successfully",
            "categories": public_categories,
            "total": total,
            "skip": skip,
            "limit": limit
        }

    @staticmethod
    def _public_course_card(course: dict) -> dict:
        hero = ((course.get("page_content") or {}).get("hero_section") or {}).get("hero_image")
        media = course.get("media_resources") or {}
        return {
            "id": course.get("id"),
            "title": course.get("title"),
            "code": course.get("code"),
            "slug": course.get("slug") or name_slug(course.get("title") or course.get("code")),
            "description": course.get("description"),
            "difficulty_level": course.get("difficulty_level"),
            "category_id": course.get("category_id"),
            "sub_category": course.get("sub_category"),
            "media_resources": {
                "course_image_url": media.get("course_image_url"),
            },
            "page_content": {
                "hero_section": {"hero_image": hero} if hero else {},
            },
        }

    @staticmethod
    async def get_public_category_nav():
        """Slim top-level categories for website Courses navigation."""
        db = get_db()
        categories = await db.categories.find({
            "parent_category_id": None,
            "is_active": True,
        }).sort("display_order", 1).to_list(100)
        items = [public_category_nav_item(category) for category in categories]
        return {
            "categories": items,
            "total": len(items),
        }

    @staticmethod
    async def get_public_category_by_slug(slug: str):
        """Public category landing payload: category, active subcategories, active courses."""
        db = get_db()
        category = await resolve_public_category(db, slug)
        parent = None
        parent_id = (category.get("parent_category_id") or "").strip() or None
        if parent_id:
            parent_doc = await db.categories.find_one({"id": parent_id, "is_active": True})
            if parent_doc:
                parent = public_category_nav_item(parent_doc)

        subcategories = await db.categories.find({
            "parent_category_id": category["id"],
            "is_active": True,
        }).sort("display_order", 1).to_list(100)

        course_query = await courses_for_category_query(db, category["id"])
        course_query = {"$and": [course_query, {"settings.active": True}]}
        courses = await db.courses.find(course_query).sort("title", 1).to_list(100)

        allowed_ids = {category["id"]}
        allowed_ids.update(sub.get("id") for sub in subcategories if sub.get("id"))
        scoped_courses = []
        for course in courses:
            cid = course.get("category_id")
            sid = course.get("sub_category")
            if cid in allowed_ids or sid in allowed_ids:
                scoped_courses.append(CategoryController._public_course_card(course))

        return {
            "category": {
                **public_category_nav_item(category),
                "description": category.get("description"),
                "parent_category_id": parent_id,
            },
            "parent": parent,
            "subcategories": [public_category_nav_item(sub) for sub in subcategories],
            "courses": scoped_courses,
            "course_count": len(scoped_courses),
        }

    @staticmethod
    async def get_category(
        category_id: str,
        current_user: dict = None
    ):
        """Get single category by ID"""
        db = get_db()
        
        category = await db.categories.find_one({"id": category_id})
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
        
        # Count courses in this category
        course_count = await db.courses.count_documents(course_count_query(category_id))
        
        # Get subcategories
        subcategories = await db.categories.find({
            "parent_category_id": category_id,
            "is_active": True
        }).sort("display_order", 1).to_list(100)
        
        subcategory_list = []
        for subcat in subcategories:
            subcat_course_count = await db.courses.count_documents(course_count_query(subcat["id"]))
            subcategory_list.append({
                "id": subcat["id"],
                "name": subcat["name"],
                "code": subcat["code"],
                "description": subcat.get("description"),
                "slug": subcat.get("slug"),
                "course_count": subcat_course_count
            })
        
        category_response = {
            "id": category["id"],
            "name": category["name"],
            "code": category["code"],
            "description": category.get("description"),
            "parent_category_id": category.get("parent_category_id"),
            "slug": category.get("slug"),
            "is_active": category["is_active"],
            "display_order": category["display_order"],
            "icon_url": category.get("icon_url"),
            "color_code": category.get("color_code"),
            "course_count": course_count,
            "subcategories": subcategory_list,
            "created_at": category["created_at"],
            "updated_at": category["updated_at"]
        }
        
        return category_response

    @staticmethod
    async def update_category(
        category_id: str,
        category_update: CategoryUpdate,
        current_user: dict = None
    ):
        """Update category (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        db = get_db()
        
        # Check if category exists
        existing_category = await db.categories.find_one({"id": category_id})
        if not existing_category:
            raise HTTPException(status_code=404, detail="Category not found")
        
        # Check if new code conflicts with existing categories
        if category_update.code and category_update.code != existing_category["code"]:
            code_conflict = await db.categories.find_one({
                "code": category_update.code,
                "id": {"$ne": category_id}
            })
            if code_conflict:
                raise HTTPException(status_code=400, detail="Category code already exists")
        
        # Validate parent category if provided
        if "parent_category_id" in category_update.dict(exclude_unset=True):
            parent_id = (category_update.parent_category_id or "").strip() or None
            if parent_id:
                await assert_parent_for_subcategory(db, parent_id, exclude_id=category_id)
                child_count = await db.categories.count_documents({"parent_category_id": category_id})
                if child_count > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot convert a category with subcategories into a subcategory",
                    )
            category_update.parent_category_id = parent_id
        
        # Prepare update data
        update_data = {k: v for k, v in category_update.dict(exclude_unset=True).items()}
        if "parent_category_id" in update_data:
            update_data["parent_category_id"] = (update_data.get("parent_category_id") or "").strip() or None
        if update_data.get("slug"):
            update_data["slug"] = await ensure_category_slug(
                db,
                update_data.get("name") or existing_category.get("name") or "category",
                exclude_id=category_id,
                existing_slug=update_data.get("slug"),
            )
        elif not existing_category.get("slug"):
            update_data["slug"] = await ensure_category_slug(
                db,
                update_data.get("name") or existing_category.get("name") or "category",
                exclude_id=category_id,
            )
        update_data["updated_at"] = datetime.utcnow()
        
        # Update category
        result = await db.categories.update_one(
            {"id": category_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Category not found")
        
        return {"message": "Category updated successfully"}

    @staticmethod
    async def delete_category(
        category_id: str,
        current_user: dict = None
    ):
        """Delete category (Super Admin only)"""
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        
        db = get_db()
        
        # Check if category exists
        category = await db.categories.find_one({"id": category_id})
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
        
        # Check if category has courses (including as a subcategory)
        course_count = await db.courses.count_documents(course_count_query(category_id))
        if course_count > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete category. It has {course_count} associated courses. Deactivate it instead."
            )
        
        # Check if category has subcategories
        subcat_count = await db.categories.count_documents({"parent_category_id": category_id})
        if subcat_count > 0:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete category. It has {subcat_count} subcategories."
            )
        
        # Delete category
        result = await db.categories.delete_one({"id": category_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Category not found")
        
        return {"message": "Category deleted successfully"}

    @staticmethod
    async def get_subcategories(
        category_id: str,
        active_only: bool = True,
        current_user: dict = None
    ):
        """List subcategories of a top-level category."""
        db = get_db()
        parent = await db.categories.find_one({"id": category_id})
        if not parent:
            raise HTTPException(status_code=404, detail="Category not found")

        query = {"parent_category_id": category_id}
        if active_only:
            query["is_active"] = True
        children = await db.categories.find(query).sort("name", 1).to_list(200)
        items = []
        for subcat in children:
            items.append({
                "id": subcat["id"],
                "name": subcat["name"],
                "code": subcat["code"],
                "description": subcat.get("description"),
                "parent_category_id": subcat.get("parent_category_id"),
                "slug": subcat.get("slug"),
                "is_active": subcat.get("is_active", True),
                "course_count": await db.courses.count_documents(course_count_query(subcat["id"])),
            })
        return {
            "message": f"Retrieved {len(items)} subcategories successfully",
            "parent": {
                "id": parent["id"],
                "name": parent["name"],
                "code": parent["code"],
            },
            "subcategories": items,
            "total": len(items),
        }

    @staticmethod
    async def get_categories_with_details(
        category_id: Optional[str] = None,
        include_courses: bool = True,
        active_only: bool = True,
        skip: int = 0,
        limit: int = 50
    ):
        """Get category details with associated courses - Public endpoint"""
        db = get_db()

        # Build query
        query = {}
        if category_id:
            query["id"] = category_id
        if active_only:
            query["is_active"] = True

        # Apply pagination
        if limit > 100:
            limit = 100

        # Get categories
        categories_cursor = db.categories.find(query).sort("display_order", 1).skip(skip).limit(limit)
        categories = await categories_cursor.to_list(limit)

        # Get total count
        total = await db.categories.count_documents(query)

        # Enrich categories with course data
        enriched_categories = []
        for category in categories:
            # Get courses in this category
            courses = []
            if include_courses:
                course_query = await courses_for_category_query(db, category["id"])
                if active_only:
                    course_query = {"$and": [course_query, {"settings.active": True}]}

                course_list = await db.courses.find(course_query).to_list(100)

                for course in course_list:
                    # Get available durations for this course
                    durations = await db.durations.find({"is_active": True}).to_list(100)
                    duration_options = []
                    for dur in durations:
                        duration_options.append({
                            "id": dur["id"],
                            "name": dur["name"],
                            "duration_months": dur["duration_months"],
                            "pricing_multiplier": dur.get("pricing_multiplier", 1.0)
                        })

                    course_data = {
                        "id": course["id"],
                        "title": course["title"],
                        "code": course["code"],
                        "difficulty_level": course["difficulty_level"],
                        "pricing": {
                            "currency": course.get("pricing", {}).get("currency", "INR"),
                            "amount": course.get("pricing", {}).get("amount", 0)
                        },
                        "available_durations": duration_options
                    }
                    courses.append(course_data)

            # Get subcategories
            subcategories = await db.categories.find({
                "parent_category_id": category["id"],
                "is_active": True
            }).sort("display_order", 1).to_list(100)

            subcategory_list = []
            for subcat in subcategories:
                subcat_course_count = await db.courses.count_documents(course_count_query(subcat["id"]))
                subcategory_list.append({
                    "id": subcat["id"],
                    "name": subcat["name"],
                    "code": subcat["code"],
                    "course_count": subcat_course_count
                })

            category_data = {
                "id": category["id"],
                "name": category["name"],
                "code": category["code"],
                "description": category.get("description"),
                "icon_url": category.get("icon_url"),
                "color_code": category.get("color_code"),
                "course_count": len(courses),
                "courses": courses,
                "subcategories": subcategory_list
            }

            enriched_categories.append(category_data)

        return {
            "message": f"Retrieved {len(enriched_categories)} categories with details successfully",
            "categories": enriched_categories,
            "total": total
        }

    @staticmethod
    async def get_categories_with_courses_and_durations(
        category_id: Optional[str] = None,
        active_only: bool = True,
        include_locations: bool = False,
        skip: int = 0,
        limit: int = 20
    ):
        """Get categories with their courses and available durations in nested structure - Public endpoint"""
        db = get_db()

        # Build query
        query = {}
        if category_id:
            query["id"] = category_id
        if active_only:
            query["is_active"] = True

        # Apply pagination
        if limit > 50:
            limit = 50

        # Get categories
        categories_cursor = db.categories.find(query).sort("display_order", 1).skip(skip).limit(limit)
        categories = await categories_cursor.to_list(limit)

        # Get total count
        total = await db.categories.count_documents(query)

        # Enrich categories with complete hierarchy
        enriched_categories = []
        for category in categories:
            # Get courses in this category
            course_query = await courses_for_category_query(db, category["id"])
            if active_only:
                course_query = {"$and": [course_query, {"settings.active": True}]}

            course_list = await db.courses.find(course_query).to_list(100)

            courses_data = []
            for course in course_list:
                # Get available durations
                durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)

                duration_list = []
                base_price = course.get("pricing", {}).get("amount", 0)

                for duration in durations:
                    multiplier = duration.get("pricing_multiplier", 1.0)
                    final_price = base_price * multiplier

                    duration_data = {
                        "id": duration["id"],
                        "name": duration["name"],
                        "duration_months": duration["duration_months"],
                        "pricing_multiplier": multiplier,
                        "final_price": final_price
                    }
                    duration_list.append(duration_data)

                # Get locations if requested
                locations_available = []
                if include_locations:
                    # Find branches that offer this course
                    branches = await db.branches.find({
                        "assignments.courses": course["id"],
                        "is_active": True
                    }).to_list(100)

                    location_map = {}
                    for branch in branches:
                        city = branch["branch"]["address"]["city"]
                        if city not in location_map:
                            # Try to find location by city name
                            location = await db.locations.find_one({
                                "name": {"$regex": city, "$options": "i"},
                                "is_active": True
                            })
                            if location:
                                location_map[city] = {
                                    "location_id": location["id"],
                                    "location_name": location["name"],
                                    "branch_count": 1
                                }
                            else:
                                location_map[city] = {
                                    "location_id": None,
                                    "location_name": city,
                                    "branch_count": 1
                                }
                        else:
                            location_map[city]["branch_count"] += 1

                    locations_available = list(location_map.values())

                course_data = {
                    "id": course["id"],
                    "title": course["title"],
                    "code": course["code"],
                    "difficulty_level": course["difficulty_level"],
                    "base_pricing": {
                        "currency": course.get("pricing", {}).get("currency", "INR"),
                        "amount": base_price
                    },
                    "durations": duration_list,
                    "locations_available": locations_available
                }
                courses_data.append(course_data)

            category_data = {
                "id": category["id"],
                "name": category["name"],
                "code": category["code"],
                "description": category.get("description"),
                "course_count": len(courses_data),
                "courses": courses_data
            }

            enriched_categories.append(category_data)

        return {
            "message": f"Retrieved {len(enriched_categories)} categories with complete hierarchy successfully",
            "categories": enriched_categories,
            "total": total
        }

    @staticmethod
    async def get_category_location_hierarchy(
        category_id: str,
        location_id: Optional[str] = None,
        active_only: bool = True,
        include_pricing: bool = True
    ):
        """Get complete hierarchy for a category - courses, locations, branches, and durations - Public endpoint"""
        db = get_db()

        # Get category
        category = await db.categories.find_one({"id": category_id})
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

        # Get courses in this category
        course_query = await courses_for_category_query(db, category_id)
        if active_only:
            course_query = {"$and": [course_query, {"settings.active": True}]}

        courses = await db.courses.find(course_query).to_list(100)

        # Get all durations
        durations = await db.durations.find({"is_active": True}).sort("display_order", 1).to_list(100)

        courses_data = []
        all_prices = []

        for course in courses:
            base_price = course.get("pricing", {}).get("amount", 0)

            # Get durations for this course
            duration_list = []
            for duration in durations:
                multiplier = duration.get("pricing_multiplier", 1.0)
                final_price = base_price * multiplier
                all_prices.append(final_price)

                duration_data = {
                    "id": duration["id"],
                    "name": duration["name"],
                    "duration_months": duration["duration_months"],
                    "pricing_multiplier": multiplier,
                    "final_price": final_price
                }
                duration_list.append(duration_data)

            # Get locations and branches for this course
            branch_query = {"assignments.courses": course["id"]}
            if active_only:
                branch_query["is_active"] = True
            if location_id:
                # Filter by location if specified
                location = await db.locations.find_one({"id": location_id})
                if location:
                    branch_query["branch.address.city"] = {"$regex": location["name"], "$options": "i"}

            branches = await db.branches.find(branch_query).to_list(100)

            # Group branches by location
            location_map = {}
            for branch in branches:
                city = branch["branch"]["address"]["city"]

                # Try to find the location record
                location_record = await db.locations.find_one({
                    "name": {"$regex": city, "$options": "i"},
                    "is_active": True
                })

                location_key = location_record["id"] if location_record else city

                if location_key not in location_map:
                    location_map[location_key] = {
                        "location_id": location_record["id"] if location_record else None,
                        "location_name": location_record["name"] if location_record else city,
                        "location_code": location_record["code"] if location_record else city[:3].upper(),
                        "state": location_record["state"] if location_record else branch["branch"]["address"]["state"],
                        "branches": [],
                        "branch_count": 0
                    }

                branch_data = {
                    "branch_id": branch["id"],
                    "branch_name": branch["branch"]["name"],
                    "branch_code": branch["branch"]["code"],
                    "address": {
                        "area": branch["branch"]["address"]["area"],
                        "city": branch["branch"]["address"]["city"],
                        "pincode": branch["branch"]["address"]["pincode"]
                    },
                    "contact": {
                        "email": branch["branch"]["email"],
                        "phone": branch["branch"]["phone"]
                    },
                    "timings": branch["operational_details"]["timings"]
                }

                location_map[location_key]["branches"].append(branch_data)
                location_map[location_key]["branch_count"] += 1

            course_data = {
                "id": course["id"],
                "title": course["title"],
                "code": course["code"],
                "difficulty_level": course["difficulty_level"],
                "base_pricing": {
                    "currency": course.get("pricing", {}).get("currency", "INR"),
                    "amount": base_price
                },
                "locations": list(location_map.values()),
                "durations": duration_list
            }
            courses_data.append(course_data)

        # Calculate summary statistics
        total_locations = len(set([loc["location_id"] for course in courses_data for loc in course["locations"] if loc["location_id"]]))
        total_branches = sum([loc["branch_count"] for course in courses_data for loc in course["locations"]])

        summary = {
            "total_courses": len(courses_data),
            "total_locations": total_locations,
            "total_branches": total_branches,
            "total_durations": len(durations)
        }

        if include_pricing and all_prices:
            summary["price_range"] = {
                "min": min(all_prices),
                "max": max(all_prices),
                "currency": "INR"
            }

        return {
            "message": "Retrieved complete hierarchy for category successfully",
            "category": {
                "id": category["id"],
                "name": category["name"],
                "code": category["code"],
                "description": category.get("description"),
                "course_count": len(courses_data)
            },
            "courses": courses_data,
            "summary": summary
        }
