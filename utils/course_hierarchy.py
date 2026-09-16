"""Helpers for Category → optional Subcategory → Course (M02-S03)."""
import logging
import re
from typing import List, Optional, Tuple

from fastapi import HTTPException

from utils.geography import slugify, unique_slug

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def _parent_id(doc: Optional[dict]) -> Optional[str]:
    if not doc:
        return None
    value = (doc.get("parent_category_id") or "").strip()
    return value or None


async def assert_parent_for_subcategory(db, parent_category_id: str, *, exclude_id: Optional[str] = None) -> dict:
    parent_id = (parent_category_id or "").strip()
    if not parent_id:
        raise HTTPException(status_code=400, detail="Parent category is required for a subcategory")
    if exclude_id and parent_id == exclude_id:
        raise HTTPException(status_code=400, detail="Category cannot be its own parent")

    parent = await db.categories.find_one({"id": parent_id})
    if not parent:
        raise HTTPException(status_code=400, detail="Parent category not found")
    if _parent_id(parent):
        raise HTTPException(
            status_code=400,
            detail="Subcategory must belong to a top-level category",
        )
    if parent.get("is_active", True) is False:
        raise HTTPException(status_code=400, detail="Cannot attach a subcategory to an inactive category")
    return parent


async def assert_course_hierarchy(
    db,
    category_id: Optional[str],
    sub_category: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Return (parent_category_id, subcategory_id). Accepts legacy category_id pointing at a subcategory."""
    cat_id = (category_id or "").strip()
    if not cat_id:
        raise HTTPException(status_code=400, detail="Category is required")

    category = await db.categories.find_one({"id": cat_id})
    if not category:
        raise HTTPException(status_code=400, detail="Please select a valid category")

    parent_of_selected = _parent_id(category)
    sub_id = (sub_category or "").strip() or None

    if parent_of_selected:
        parent = await db.categories.find_one({"id": parent_of_selected})
        if not parent:
            raise HTTPException(status_code=400, detail="Selected category's parent was not found")
        if parent.get("is_active", True) is False:
            raise HTTPException(status_code=400, detail="Selected category's parent is inactive")
        if category.get("is_active", True) is False:
            raise HTTPException(status_code=400, detail="Selected subcategory is inactive")
        return parent_of_selected, cat_id

    if category.get("is_active", True) is False:
        raise HTTPException(status_code=400, detail="Selected category is inactive")

    if sub_id:
        child = await db.categories.find_one({"id": sub_id})
        if not child:
            raise HTTPException(status_code=400, detail="Please select a valid subcategory")
        if _parent_id(child) != cat_id:
            raise HTTPException(
                status_code=400,
                detail="Subcategory does not belong to the selected category",
            )
        if child.get("is_active", True) is False:
            raise HTTPException(status_code=400, detail="Selected subcategory is inactive")
        return cat_id, sub_id

    return cat_id, None


async def child_category_ids(db, category_id: str) -> List[str]:
    children = await db.categories.find({"parent_category_id": category_id}).to_list(500)
    return [c["id"] for c in children if c.get("id")]


async def courses_for_category_query(db, category_id: str) -> dict:
    ids = [category_id] + await child_category_ids(db, category_id)
    return {
        "$or": [
            {"category_id": {"$in": ids}},
            {"sub_category": {"$in": ids}},
        ]
    }


def course_count_query(category_id: str) -> dict:
    return {
        "$or": [
            {"category_id": category_id},
            {"sub_category": category_id},
        ]
    }


async def ensure_course_slug(db, title: str, exclude_id: Optional[str] = None, existing_slug: Optional[str] = None) -> str:
    if existing_slug and str(existing_slug).strip():
        slug = str(existing_slug).strip().lower()
        query = {"slug": slug}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        conflict = await db.courses.find_one(query)
        if conflict:
            raise HTTPException(status_code=400, detail="Course slug already exists")
        return slug
    return await unique_slug(db.courses, title or "course", exclude_id=exclude_id)


async def ensure_category_slug(db, name: str, exclude_id: Optional[str] = None, existing_slug: Optional[str] = None) -> str:
    if existing_slug and str(existing_slug).strip():
        slug = str(existing_slug).strip().lower()
        query = {"slug": slug}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        conflict = await db.categories.find_one(query)
        if conflict:
            raise HTTPException(status_code=400, detail="Category slug already exists")
        return slug
    return await unique_slug(db.categories, name or "category", exclude_id=exclude_id)


def name_slug(value: Optional[str]) -> str:
    return slugify(value or "")


async def resolve_public_category(db, slug: str) -> dict:
    """Active category by stored slug, UUID, or name-derived slug. Inactive/missing → 404."""
    value = (slug or "").strip().lower()
    if not value:
        raise HTTPException(status_code=404, detail="Category not found")

    category = await db.categories.find_one({"slug": value, "is_active": True})
    if not category and UUID_RE.match(value):
        category = await db.categories.find_one({"id": value, "is_active": True})
    if not category:
        candidates = await db.categories.find({"is_active": True}).to_list(500)
        for item in candidates:
            stored = (item.get("slug") or "").strip().lower()
            if stored == value or name_slug(item.get("name")) == value:
                category = item
                break
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


def public_category_nav_item(category: dict) -> dict:
    slug = (category.get("slug") or name_slug(category.get("name")) or category.get("id") or "").strip().lower()
    return {
        "id": category.get("id"),
        "name": category.get("name"),
        "slug": slug,
    }


async def ensure_course_hierarchy_indexes(mongo_db) -> None:
    try:
        await mongo_db.categories.create_index(
            "slug", unique=True, sparse=True, name="categories_slug_unique"
        )
    except Exception:
        logging.exception("Failed to create categories.slug unique index")
    try:
        await mongo_db.courses.create_index(
            "slug", unique=True, sparse=True, name="courses_slug_unique"
        )
    except Exception:
        logging.exception("Failed to create courses.slug unique index")
