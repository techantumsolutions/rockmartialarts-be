"""
M18-S01 Notification Template Master service.

CRUD for notification_templates with channel, category, placeholders,
provider references, and status. Soft-archive only (no hard delete of
templates that may be referenced by invoice / legacy flows).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.notification_template_models import (
    NotificationTemplateCategory,
    NotificationTemplateCreate,
    NotificationTemplateStatus,
    NotificationTemplateUpdate,
    extract_placeholders_from_body,
    normalize_placeholders,
    slugify_template_name,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_status_service import get_managed_branch_ids_for_user

logger = logging.getLogger(__name__)

COL = "notification_templates"
COL_BRANCHES = "branches"


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").lower()


def _is_super_or_coach(user: Optional[dict]) -> bool:
    r = _role(user)
    return r in ("superadmin", "super_admin", "coach_admin", "coachadmin")


def _is_branch_manager(user: Optional[dict]) -> bool:
    return _role(user) in ("branch_manager", "branchadmin", "branch_admin")


def _global_branch_clause() -> Dict[str, Any]:
    return {
        "$or": [
            {"branch_id": None},
            {"branch_id": ""},
            {"branch_id": {"$exists": False}},
        ]
    }


async def ensure_notification_template_indexes(db=None) -> None:
    database = db if db is not None else get_db()
    if database is None:
        return
    try:
        await database[COL].create_index("id", unique=True)
        await database[COL].create_index([("name", 1), ("channel", 1), ("branch_id", 1)])
        await database[COL].create_index([("status", 1), ("updated_at", -1)])
        await database[COL].create_index([("channel", 1), ("category", 1)])
        await database[COL].create_index("branch_id")
    except Exception:
        logger.exception("Failed ensuring notification template indexes")


async def _branch_name_map(db, branch_ids: List[str]) -> Dict[str, str]:
    ids = [b for b in branch_ids if b]
    if not ids:
        return {}
    rows = await db[COL_BRANCHES].find(
        {"id": {"$in": ids}}, {"id": 1, "name": 1, "code": 1}
    ).to_list(length=len(ids))
    return {
        str(r["id"]): str(r.get("name") or r.get("code") or r["id"])
        for r in rows
        if r.get("id")
    }


async def assert_template_access(
    db,
    current_user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    require_write: bool = False,
    existing_doc: Optional[dict] = None,
) -> List[str]:
    """
    Enforce BM isolation.
    Returns managed branch ids (empty for super/coach = unrestricted).
    """
    if not current_user:
        return []
    if _is_super_or_coach(current_user):
        return []
    if not _is_branch_manager(current_user):
        raise HTTPException(status_code=403, detail="Not allowed")

    managed = await get_managed_branch_ids_for_user(db, current_user)
    if not managed:
        raise HTTPException(status_code=403, detail="No managed branches")

    target_branch = branch_id
    if existing_doc is not None:
        target_branch = existing_doc.get("branch_id") or None

    if require_write:
        # BM cannot create/edit Rock (global) templates
        if not target_branch:
            raise HTTPException(
                status_code=403,
                detail="Branch managers cannot modify Rock Martial Arts default templates",
            )
        if str(target_branch) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
    elif target_branch and str(target_branch) not in {str(b) for b in managed}:
        # Read of another branch's template
        raise HTTPException(status_code=403, detail="Branch out of scope")

    return [str(b) for b in managed]


def enrich_notification_template(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = serialize_doc(doc)
    channel = (out.get("channel") or out.get("type") or "sms").lower()
    if channel not in ("sms", "whatsapp", "email"):
        channel = "sms"
    out["channel"] = channel
    # Legacy field used by old code / invoice seed
    out["type"] = out.get("type") or channel

    status = (out.get("status") or "").lower()
    if not status:
        status = (
            NotificationTemplateStatus.ACTIVE.value
            if out.get("is_active", True) is not False
            else NotificationTemplateStatus.INACTIVE.value
        )
    out["status"] = status
    out["is_active"] = status == NotificationTemplateStatus.ACTIVE.value

    category = (out.get("category") or "").lower()
    if not category:
        name = str(out.get("name") or "").lower()
        if "invoice" in name:
            category = NotificationTemplateCategory.INVOICE.value
        elif "otp" in name:
            category = NotificationTemplateCategory.OTP.value
        elif "remind" in name:
            category = NotificationTemplateCategory.REMINDER.value
        elif "welcome" in name:
            category = NotificationTemplateCategory.WELCOME.value
        elif "payment" in name or "pay" in name:
            category = NotificationTemplateCategory.PAYMENT.value
        else:
            category = NotificationTemplateCategory.CUSTOM.value
    out["category"] = category

    body = str(out.get("body") or "")
    out["placeholders"] = normalize_placeholders(out.get("placeholders"), body=body)
    out["placeholder_keys"] = [p["key"] for p in out["placeholders"]]
    out["display_name"] = out.get("display_name") or str(out.get("name") or "").replace(
        "_", " "
    ).title()
    out["dlt_template_id"] = out.get("dlt_template_id") or None
    out["provider_template_name"] = out.get("provider_template_name") or None
    out["provider_reference"] = (
        out.get("provider_reference")
        or out.get("provider_template_name")
        or out.get("dlt_template_id")
        or None
    )
    out["description"] = out.get("description") or None
    out["branch_id"] = out.get("branch_id") or None
    out["branch_name"] = out.get("branch_name") or None
    out["is_global"] = not bool(out["branch_id"])
    out["scope"] = "global" if out["is_global"] else "branch"
    out["is_default"] = bool(out.get("is_default", False))
    out["subject"] = out.get("subject") or None
    return out


async def resolve_notification_template(
    *,
    name: str,
    channel: str = "sms",
    branch_id: Optional[str] = None,
    category: Optional[str] = None,
    allow_inactive: bool = False,
) -> Dict[str, Any]:
    """
    M18-S03: Resolve branch template first, then Rock Martial Arts global fallback.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_template_indexes(db)

    name_key = slugify_template_name(name)
    ch = (channel or "sms").lower()
    active_clause: Dict[str, Any] = {
        "$or": [
            {"status": NotificationTemplateStatus.ACTIVE.value},
            {
                "status": {"$exists": False},
                "is_active": {"$ne": False},
            },
        ]
    }
    if allow_inactive:
        active_clause = {"status": {"$ne": NotificationTemplateStatus.ARCHIVED.value}}

    async def _find(extra: Dict[str, Any]) -> Optional[dict]:
        q: Dict[str, Any] = {
            "name": name_key,
            "$and": [
                {"$or": [{"channel": ch}, {"type": ch}]},
                active_clause,
            ],
        }
        q.update(extra)
        if category:
            q["category"] = category
        # Prefer is_default then newest
        rows = (
            await db[COL]
            .find(q)
            .sort([("is_default", -1), ("updated_at", -1)])
            .limit(1)
            .to_list(length=1)
        )
        return rows[0] if rows else None

    resolution = "none"
    doc = None
    if branch_id:
        doc = await _find({"branch_id": branch_id})
        if doc:
            resolution = "branch"

    if not doc:
        doc = await _find(_global_branch_clause())
        if doc:
            resolution = "global"

    tpl = enrich_notification_template(doc) if doc else None
    if tpl and tpl.get("branch_id"):
        names = await _branch_name_map(db, [tpl["branch_id"]])
        tpl["branch_name"] = names.get(str(tpl["branch_id"]))

    return {
        "template": tpl,
        "resolution": resolution,
        "name": name_key,
        "channel": ch,
        "branch_id": branch_id,
        "fallback_used": resolution == "global" and bool(branch_id),
    }


async def list_notification_templates(
    *,
    channel: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    branch_id: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    include_archived: bool = False,
    scope: Optional[str] = None,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_template_indexes(db)

    managed: List[str] = []
    if current_user and _is_branch_manager(current_user):
        managed = await assert_template_access(db, current_user, require_write=False)

    q: Dict[str, Any] = {}
    and_clauses: List[Dict[str, Any]] = []

    if channel and channel != "all":
        and_clauses.append({"$or": [{"channel": channel}, {"type": channel}]})
    if category and category != "all":
        q["category"] = category
    if status and status != "all":
        if status == NotificationTemplateStatus.ACTIVE.value:
            and_clauses.append(
                {
                    "$or": [
                        {"status": "active"},
                        {
                            "status": {"$exists": False},
                            "is_active": {"$ne": False},
                        },
                    ]
                }
            )
        elif status == NotificationTemplateStatus.INACTIVE.value:
            and_clauses.append(
                {
                    "$or": [
                        {"status": "inactive"},
                        {"status": {"$exists": False}, "is_active": False},
                    ]
                }
            )
        else:
            q["status"] = status
    elif not include_archived:
        q["status"] = {"$ne": NotificationTemplateStatus.ARCHIVED.value}

    # Branch / scope filtering (M18-S03)
    scope_key = (scope or "").lower()
    if managed:
        # BM: own branch templates + Rock global defaults (readable)
        if branch_id and str(branch_id) in managed:
            and_clauses.append(
                {
                    "$or": [
                        {"branch_id": branch_id},
                        _global_branch_clause(),
                    ]
                }
            )
        elif scope_key == "branch":
            and_clauses.append({"branch_id": {"$in": managed}})
        elif scope_key == "global":
            and_clauses.append(_global_branch_clause())
        else:
            and_clauses.append(
                {
                    "$or": [
                        {"branch_id": {"$in": managed}},
                        _global_branch_clause(),
                    ]
                }
            )
    else:
        if branch_id == "__global__" or scope_key == "global":
            and_clauses.append(_global_branch_clause())
        elif scope_key == "branch":
            and_clauses.append(
                {"branch_id": {"$nin": [None, ""]}, "branch_id": {"$exists": True}}
            )
            # fix: use $and properly
            and_clauses[-1] = {
                "$and": [
                    {"branch_id": {"$exists": True}},
                    {"branch_id": {"$nin": [None, ""]}},
                ]
            }
        elif branch_id:
            q["branch_id"] = branch_id

    if search and search.strip():
        term = search.strip()
        and_clauses.append(
            {
                "$or": [
                    {"name": {"$regex": term, "$options": "i"}},
                    {"display_name": {"$regex": term, "$options": "i"}},
                    {"body": {"$regex": term, "$options": "i"}},
                    {"provider_template_name": {"$regex": term, "$options": "i"}},
                    {"dlt_template_id": {"$regex": term, "$options": "i"}},
                ]
            }
        )

    if and_clauses:
        q["$and"] = and_clauses

    skip = max(0, skip)
    limit = max(1, min(limit, 200))
    total = await db[COL].count_documents(q)
    rows = (
        await db[COL]
        .find(q)
        .sort([("branch_id", 1), ("updated_at", -1), ("name", 1)])
        .skip(skip)
        .limit(limit)
        .to_list(length=limit)
    )
    branch_ids = [str(r.get("branch_id")) for r in rows if r.get("branch_id")]
    names = await _branch_name_map(db, branch_ids)
    templates = []
    for r in rows:
        en = enrich_notification_template(r)
        if en and en.get("branch_id"):
            en["branch_name"] = names.get(str(en["branch_id"]))
        if en and managed:
            en["editable"] = bool(
                en.get("branch_id") and str(en["branch_id"]) in managed
            )
        elif en:
            en["editable"] = True
        templates.append(en)
    return {
        "templates": templates,
        "total": total,
        "skip": skip,
        "limit": limit,
        "managed_branch_ids": managed,
    }


async def get_notification_template(
    template_id: str,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": template_id})
    if not doc:
        doc = await db[COL].find_one({"name": template_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    # BM may read global; only own branch templates otherwise
    if current_user and _is_branch_manager(current_user):
        managed = await get_managed_branch_ids_for_user(db, current_user)
        bid = doc.get("branch_id")
        if bid and str(bid) not in {str(b) for b in managed}:
            raise HTTPException(status_code=403, detail="Branch out of scope")
    en = enrich_notification_template(doc)
    if en and en.get("branch_id"):
        names = await _branch_name_map(db, [en["branch_id"]])
        en["branch_name"] = names.get(str(en["branch_id"]))
    if en and current_user and _is_branch_manager(current_user):
        managed = await get_managed_branch_ids_for_user(db, current_user)
        en["editable"] = bool(en.get("branch_id") and str(en["branch_id"]) in managed)
    elif en:
        en["editable"] = True
    return {"template": en}


async def create_notification_template(
    body: NotificationTemplateCreate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await ensure_notification_template_indexes(db)

    branch_id = body.branch_id
    if current_user and _is_branch_manager(current_user):
        managed = await get_managed_branch_ids_for_user(db, current_user)
        if not managed:
            raise HTTPException(status_code=403, detail="No managed branches")
        if not branch_id:
            branch_id = managed[0]
        await assert_template_access(
            db, current_user, branch_id=branch_id, require_write=True
        )
    elif branch_id:
        # validate branch exists for SA
        br = await db[COL_BRANCHES].find_one({"id": branch_id})
        if not br:
            raise HTTPException(status_code=400, detail="Invalid branch_id")

    name = slugify_template_name(body.name)
    dup_q: Dict[str, Any] = {
        "name": name,
        "channel": body.channel.value,
        "status": {"$ne": NotificationTemplateStatus.ARCHIVED.value},
    }
    if branch_id:
        dup_q["branch_id"] = branch_id
    else:
        dup_q["$and"] = [_global_branch_clause()]
    existing = await db[COL].find_one(dup_q)
    if existing:
        scope = f"branch {branch_id}" if branch_id else "Rock default"
        raise HTTPException(
            status_code=409,
            detail=f"Template '{name}' already exists for channel {body.channel.value} ({scope})",
        )

    now = datetime.utcnow()
    placeholders = normalize_placeholders(body.placeholders, body=body.body)
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "display_name": body.display_name or name.replace("_", " ").title(),
        "channel": body.channel.value,
        "type": body.channel.value,  # legacy compat
        "category": body.category.value,
        "status": body.status.value,
        "is_active": body.status == NotificationTemplateStatus.ACTIVE,
        "subject": body.subject,
        "body": body.body,
        "placeholders": placeholders,
        "dlt_template_id": body.dlt_template_id,
        "provider_template_name": body.provider_template_name,
        "provider_reference": body.provider_reference
        or body.provider_template_name
        or body.dlt_template_id,
        "description": body.description,
        "branch_id": branch_id,
        "is_default": bool(body.is_default) if not branch_id else False,
        "created_by": (current_user or {}).get("id"),
        "created_at": now,
        "updated_at": now,
    }
    await db[COL].insert_one(doc)
    en = enrich_notification_template(doc)
    if en and branch_id:
        names = await _branch_name_map(db, [branch_id])
        en["branch_name"] = names.get(str(branch_id))
    return {
        "message": "Template created",
        "template": en,
    }


async def update_notification_template(
    template_id: str,
    body: NotificationTemplateUpdate,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": template_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")

    await assert_template_access(
        db,
        current_user,
        require_write=True,
        existing_doc=doc,
    )

    data = body.model_dump(exclude_unset=True)
    # BM cannot reassign to global or other branch
    if current_user and _is_branch_manager(current_user):
        if data.get("clear_branch_id"):
            raise HTTPException(
                status_code=403,
                detail="Cannot convert branch template to Rock default",
            )
        if "branch_id" in data and data["branch_id"] != doc.get("branch_id"):
            await assert_template_access(
                db, current_user, branch_id=data["branch_id"], require_write=True
            )
    patch: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]

    if "clear_branch_id" in data and data.pop("clear_branch_id"):
        patch["branch_id"] = None

    for key in (
        "display_name",
        "subject",
        "description",
        "dlt_template_id",
        "provider_template_name",
        "provider_reference",
        "branch_id",
        "is_default",
    ):
        if key in data:
            patch[key] = data[key]

    if "channel" in data and data["channel"] is not None:
        ch = data["channel"]
        ch_val = ch.value if hasattr(ch, "value") else str(ch)
        patch["channel"] = ch_val
        patch["type"] = ch_val

    if "category" in data and data["category"] is not None:
        cat = data["category"]
        patch["category"] = cat.value if hasattr(cat, "value") else str(cat)

    if "status" in data and data["status"] is not None:
        st = data["status"]
        st_val = st.value if hasattr(st, "value") else str(st)
        patch["status"] = st_val
        patch["is_active"] = st_val == NotificationTemplateStatus.ACTIVE.value

    body_text = data.get("body") if "body" in data else doc.get("body")
    if "body" in data and data["body"] is not None:
        patch["body"] = data["body"]

    if "placeholders" in data or "body" in data:
        patch["placeholders"] = normalize_placeholders(
            data.get("placeholders") if "placeholders" in data else doc.get("placeholders"),
            body=str(body_text or ""),
        )

    # Keep provider_reference in sync if only one side set
    if "provider_template_name" in patch and "provider_reference" not in data:
        patch["provider_reference"] = (
            patch.get("provider_template_name")
            or patch.get("dlt_template_id")
            or doc.get("provider_reference")
        )
    if "dlt_template_id" in patch and "provider_reference" not in data:
        patch["provider_reference"] = (
            patch.get("provider_reference")
            or patch.get("dlt_template_id")
            or doc.get("provider_template_name")
        )

    await db[COL].update_one({"id": template_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": template_id})
    return {
        "message": "Template updated",
        "template": enrich_notification_template(updated),
    }


async def archive_notification_template(
    template_id: str,
    *,
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Soft-archive — never hard-delete (protects invoice / legacy refs)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    doc = await db[COL].find_one({"id": template_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    await assert_template_access(
        db, current_user, require_write=True, existing_doc=doc
    )
    patch = {
        "status": NotificationTemplateStatus.ARCHIVED.value,
        "is_active": False,
        "updated_at": datetime.utcnow(),
    }
    if current_user and current_user.get("id"):
        patch["updated_by"] = current_user["id"]
    await db[COL].update_one({"id": template_id}, {"$set": patch})
    updated = await db[COL].find_one({"id": template_id})
    return {
        "message": "Template archived",
        "template": enrich_notification_template(updated),
    }


async def preview_notification_template(
    template_id: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Render body with {{placeholders}} for admin preview (no send)."""
    got = await get_notification_template(template_id)
    tpl = got["template"]
    body = str(tpl.get("body") or "")
    subject = str(tpl.get("subject") or "")
    ctx = context or {}
    # Fill missing with sample values from placeholder defs
    for p in tpl.get("placeholders") or []:
        key = p.get("key")
        if key and key not in ctx and p.get("sample"):
            ctx[key] = p["sample"]

    def _render(text: str) -> str:
        out = text
        for key, value in ctx.items():
            out = out.replace(f"{{{{{key}}}}}", str(value))
        return out

    rendered_body = _render(body)
    rendered_subject = _render(subject) if subject else None
    missing = [
        k
        for k in extract_placeholders_from_body(body)
        if f"{{{{{k}}}}}" in rendered_body
    ]
    return {
        "template_id": tpl.get("id"),
        "name": tpl.get("name"),
        "channel": tpl.get("channel"),
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "missing_placeholders": missing,
        "context_used": ctx,
    }


async def clone_rock_template_to_branch(
    *,
    name: str,
    branch_id: str,
    channel: str = "sms",
    current_user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Copy Rock (global) template into a branch override (M18-S03)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    await assert_template_access(
        db, current_user, branch_id=branch_id, require_write=True
    )
    # Force global source
    global_res = await resolve_notification_template(
        name=name, channel=channel, branch_id=None
    )
    src = global_res.get("template")
    if not src or global_res.get("resolution") != "global":
        # try find any global by name ignoring active for clone source
        name_key = slugify_template_name(name)
        src_doc = await db[COL].find_one(
            {
                "name": name_key,
                "$and": [
                    {"$or": [{"channel": channel}, {"type": channel}]},
                    _global_branch_clause(),
                    {"status": {"$ne": NotificationTemplateStatus.ARCHIVED.value}},
                ],
            }
        )
        src = enrich_notification_template(src_doc) if src_doc else None
    if not src:
        raise HTTPException(status_code=404, detail="Rock default template not found")

    # If branch already has override, return it
    existing = await resolve_notification_template(
        name=src["name"], channel=channel, branch_id=branch_id
    )
    if existing.get("resolution") == "branch" and existing.get("template"):
        return {
            "message": "Branch template already exists",
            "template": existing["template"],
            "created": False,
        }

    body = NotificationTemplateCreate(
        name=src["name"],
        display_name=src.get("display_name"),
        channel=src.get("channel") or channel,
        category=src.get("category") or "custom",
        status="draft",
        subject=src.get("subject"),
        body=src.get("body") or " ",
        placeholders=src.get("placeholders"),
        dlt_template_id=src.get("dlt_template_id"),
        provider_template_name=src.get("provider_template_name"),
        provider_reference=src.get("provider_reference"),
        description=f"Branch override of Rock default '{src['name']}'",
        branch_id=branch_id,
        is_default=False,
    )
    created = await create_notification_template(body, current_user=current_user)
    created["created"] = True
    created["message"] = "Cloned Rock default to branch"
    return created


async def list_placeholder_catalog() -> Dict[str, Any]:
    """Common placeholder suggestions for admin UI."""
    from models.notification_template_models import (
        NOTIFICATION_CHANNELS,
        NOTIFICATION_TEMPLATE_CATEGORIES,
        NOTIFICATION_TEMPLATE_STATUSES,
    )

    catalog = [
        {"key": "name", "label": "Recipient name", "sample": "Ravi"},
        {"key": "customer_name", "label": "Customer name", "sample": "Ravi Kumar"},
        {"key": "phone", "label": "Phone", "sample": "9876543210"},
        {"key": "otp", "label": "OTP code", "sample": "123456"},
        {"key": "invoice_number", "label": "Invoice number", "sample": "INV-1001"},
        {"key": "amount", "label": "Amount", "sample": "₹1,500"},
        {"key": "paid_date", "label": "Paid date", "sample": "18-09-2026"},
        {"key": "course_summary", "label": "Course summary", "sample": "Karate — Monthly"},
        {"key": "branch_name", "label": "Branch name", "sample": "Hyderabad"},
        {"key": "payment_ref", "label": "Payment reference", "sample": "pay_xxx"},
        {"key": "invoice_link", "label": "Invoice link", "sample": "https://example.com/invoice"},
        {"key": "event_title", "label": "Event title", "sample": "Sparring Seminar"},
        {"key": "when", "label": "Date/time", "sample": "01-10-2026 10:00"},
        {"key": "venue", "label": "Venue", "sample": "Main Dojo"},
    ]
    return {
        "placeholders": catalog,
        "channels": list(NOTIFICATION_CHANNELS),
        "categories": list(NOTIFICATION_TEMPLATE_CATEGORIES),
        "statuses": list(NOTIFICATION_TEMPLATE_STATUSES),
    }
