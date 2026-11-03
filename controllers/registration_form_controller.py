from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

from models.registration_form_models import (
    RegistrationFormCreate,
    RegistrationFormUpdate,
    new_doc_id,
    stamp_created_by,
)
from utils.database import get_db
from utils.helpers import serialize_doc
from utils.student_branch_sync import get_student_assigned_branch_id
from utils.student_content_access import (
    is_super_admin,
    is_branch_manager,
    is_partner_super_admin,
    managed_branch_ids,
    partner_org_id_of,
)
from utils.partner_tenant import partner_branch_ids, stamp_tenant


COLLECTION = "registration_forms"


def _sort_order():
    return [("display_order", 1), ("created_at", -1)]


def _normalize_row(doc: dict) -> dict:
    if not doc:
        return doc
    row = dict(doc)
    pairs = row.get("branch_course_pairs") or []
    row["branch_course_pairs"] = [
        {"branch_id": p.get("branch_id"), "course_id": p.get("course_id")}
        for p in pairs
        if p.get("branch_id") and p.get("course_id")
    ]
    row["branch_ids"] = list(row.get("branch_ids") or [])
    row["course_ids"] = list(row.get("course_ids") or [])
    return row


def _validate_active_with_pdf(status: str, file_url: Optional[str]) -> None:
    if status == "active" and not (file_url or "").strip():
        raise HTTPException(
            status_code=400,
            detail="A PDF must be uploaded before the form can be activated",
        )


def _validate_availability_fields(
    availability_type: str,
    branch_ids: Optional[List[str]],
    course_ids: Optional[List[str]],
    branch_course_pairs: Optional[List[dict]],
) -> None:
    if availability_type == "branch" and not (branch_ids or []):
        raise HTTPException(status_code=400, detail="At least one branch is required")
    if availability_type == "course" and not (course_ids or []):
        raise HTTPException(status_code=400, detail="At least one course is required")
    if availability_type == "branch_course" and not (branch_course_pairs or []):
        raise HTTPException(
            status_code=400,
            detail="At least one branch and course combination is required",
        )


def _assert_branch_manager_availability(
    availability_type: str,
    branch_ids: Optional[List[str]],
    branch_course_pairs: Optional[List[dict]],
    current_user: dict,
) -> None:
    if availability_type == "global":
        raise HTTPException(status_code=403, detail="Branch managers cannot create global forms")
    managed = managed_branch_ids(current_user)
    if availability_type == "branch":
        invalid = [b for b in (branch_ids or []) if b not in managed]
        if invalid:
            raise HTTPException(status_code=403, detail="Invalid branch selection")
    if availability_type == "branch_course":
        for pair in branch_course_pairs or []:
            if pair.get("branch_id") not in managed:
                raise HTTPException(status_code=403, detail="Invalid branch in combination")


def _branch_manager_can_see_form(doc: dict, current_user: dict) -> bool:
    managed = set(managed_branch_ids(current_user))
    if not managed:
        return False
    availability = doc.get("availability_type")
    if availability == "global":
        return False
    if availability == "branch":
        return bool(set(doc.get("branch_ids") or []) & managed)
    if availability == "course":
        return False
    if availability == "branch_course":
        for pair in doc.get("branch_course_pairs") or []:
            if pair.get("branch_id") in managed:
                return True
    return False


async def _partner_owned_branch_ids(current_user: dict) -> List[str]:
    """Branch ids belonging to the partner org (empty list if not a partner)."""
    if not is_partner_super_admin(current_user):
        return []
    db = get_db()
    bids = await partner_branch_ids(db, current_user)
    return list(bids or [])


def _partner_can_see_form(doc: dict, partner_org_id: str, owned_branch_ids: List[str]) -> bool:
    if not doc:
        return False
    if str(doc.get("partner_org_id") or "").strip() == partner_org_id:
        return True
    owned = set(owned_branch_ids or [])
    if not owned:
        return False
    availability = doc.get("availability_type")
    if availability == "global":
        return False
    if availability == "branch":
        return bool(set(doc.get("branch_ids") or []) & owned)
    if availability == "course":
        # Course-only forms without partner stamp are Rock-wide — partners do not see them
        return False
    if availability == "branch_course":
        for pair in doc.get("branch_course_pairs") or []:
            if pair.get("branch_id") in owned:
                return True
    return False


def _assert_partner_availability(
    availability_type: str,
    branch_ids: Optional[List[str]],
    course_ids: Optional[List[str]],
    branch_course_pairs: Optional[List[dict]],
    owned_branch_ids: List[str],
) -> None:
    if availability_type == "global":
        raise HTTPException(
            status_code=403,
            detail="Partner admins cannot create global (all Rock) registration forms",
        )
    owned = set(owned_branch_ids or [])
    if availability_type == "branch":
        invalid = [b for b in (branch_ids or []) if b not in owned]
        if invalid:
            raise HTTPException(status_code=403, detail="You can only use your organization's branches")
    if availability_type == "branch_course":
        for pair in branch_course_pairs or []:
            if pair.get("branch_id") not in owned:
                raise HTTPException(status_code=403, detail="Invalid branch in combination")
    # course-only: allowed for partner's own courses (caller stamps partner_org_id)


def _assert_can_modify(doc: Optional[dict], current_user: dict) -> None:
    if not doc:
        raise HTTPException(status_code=404, detail="Registration form not found")
    if is_super_admin(current_user):
        return
    if is_branch_manager(current_user):
        if not _branch_manager_can_see_form(doc, current_user):
            raise HTTPException(status_code=403, detail="Not allowed to modify this form")
        return
    if is_partner_super_admin(current_user):
        # Async ownership check happens in update/delete via _assert_partner_can_modify
        return
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _assert_partner_can_modify(doc: Optional[dict], current_user: dict) -> None:
    if not doc:
        raise HTTPException(status_code=404, detail="Registration form not found")
    if is_super_admin(current_user):
        return
    if is_branch_manager(current_user):
        if not _branch_manager_can_see_form(doc, current_user):
            raise HTTPException(status_code=403, detail="Not allowed to modify this form")
        return
    if is_partner_super_admin(current_user):
        org_id = partner_org_id_of(current_user)
        if not org_id:
            raise HTTPException(status_code=403, detail="Partner organization missing")
        owned = await _partner_owned_branch_ids(current_user)
        if not _partner_can_see_form(doc, org_id, owned):
            raise HTTPException(status_code=403, detail="Not allowed to modify this form")
        return
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _get_student_enrollment_context(student_id: str) -> Tuple[Optional[str], Set[str], Set[str], Set[Tuple[str, str]]]:
    db = get_db()
    branch_id = await get_student_assigned_branch_id(db, student_id)
    enrollments = await db.enrollments.find(
        {"student_id": student_id, "is_active": True},
        {"branch_id": 1, "course_id": 1},
    ).to_list(length=200)

    course_ids: Set[str] = set()
    branch_ids: Set[str] = set()
    pairs: Set[Tuple[str, str]] = set()

    for enr in enrollments:
        cid = enr.get("course_id")
        bid = enr.get("branch_id")
        if cid:
            course_ids.add(str(cid))
        if bid:
            branch_ids.add(str(bid))
        if bid and cid:
            pairs.add((str(bid), str(cid)))

    if branch_id:
        branch_ids.add(str(branch_id))

    return branch_id, course_ids, branch_ids, pairs


def _form_matches_student(
    form: dict,
    course_ids: Set[str],
    branch_ids: Set[str],
    pairs: Set[Tuple[str, str]],
) -> bool:
    if form.get("status") != "active":
        return False
    if not (form.get("file_url") or "").strip():
        return False

    availability = form.get("availability_type") or "global"
    if availability == "global":
        return True
    if availability == "branch":
        return bool(set(form.get("branch_ids") or []) & branch_ids)
    if availability == "course":
        return bool(set(form.get("course_ids") or []) & course_ids)
    if availability == "branch_course":
        form_pairs = {
            (str(p.get("branch_id")), str(p.get("course_id")))
            for p in (form.get("branch_course_pairs") or [])
            if p.get("branch_id") and p.get("course_id")
        }
        return bool(form_pairs & pairs)
    return False


async def list_manage(current_user: dict, status: Optional[str] = None) -> List[Dict[str, Any]]:
    db = get_db()
    q: Dict[str, Any] = {}
    if status in ("active", "inactive"):
        q["status"] = status

    if is_super_admin(current_user):
        pass
    elif is_partner_super_admin(current_user):
        org_id = partner_org_id_of(current_user)
        if not org_id:
            raise HTTPException(status_code=403, detail="Partner organization missing")
        owned = await _partner_owned_branch_ids(current_user)
        # Partner forms: stamped to org OR tied to partner-owned branches (legacy)
        q["$or"] = [
            {"partner_org_id": org_id},
            {"availability_type": "branch", "branch_ids": {"$in": owned}},
            {
                "availability_type": "branch_course",
                "branch_course_pairs.branch_id": {"$in": owned},
            },
        ]
    elif is_branch_manager(current_user):
        managed = managed_branch_ids(current_user)
        q["$or"] = [
            {"availability_type": "branch", "branch_ids": {"$in": managed}},
            {"availability_type": "branch_course", "branch_course_pairs.branch_id": {"$in": managed}},
        ]
    else:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    cur = db[COLLECTION].find(q).sort(_sort_order())
    docs = await cur.to_list(length=500)
    # Extra safety filter for partner (exclude Rock globals that might match empty $in)
    if is_partner_super_admin(current_user):
        org_id = partner_org_id_of(current_user) or ""
        owned = await _partner_owned_branch_ids(current_user)
        docs = [d for d in docs if _partner_can_see_form(d, org_id, owned)]
    return [_normalize_row(serialize_doc(d)) for d in docs]


async def list_for_student(student_id: str) -> List[Dict[str, Any]]:
    db = get_db()
    assigned_branch_id, course_ids, branch_ids, pairs = await _get_student_enrollment_context(student_id)

    # Partner org(s) for this student's branches — partner forms stay tenant-isolated
    student_partner_orgs: Set[str] = set()
    all_branch_ids = set(branch_ids)
    if assigned_branch_id:
        all_branch_ids.add(str(assigned_branch_id))
    if all_branch_ids:
        async for br in db.branches.find(
            {"id": {"$in": list(all_branch_ids)}},
            {"partner_org_id": 1},
        ):
            org = str(br.get("partner_org_id") or "").strip()
            if org:
                student_partner_orgs.add(org)

    cur = db[COLLECTION].find({"status": "active"}).sort(_sort_order())
    docs = await cur.to_list(length=500)

    matched = []
    for doc in docs:
        form_partner = str(doc.get("partner_org_id") or "").strip()
        if form_partner:
            # Partner-created form: only students enrolled under that partner
            if form_partner not in student_partner_orgs:
                continue
        elif student_partner_orgs:
            # Partner-branch students do not receive Rock-global / unstamped Rock forms
            # unless the form is explicitly scoped to their branch (branch / branch_course).
            availability = doc.get("availability_type") or "global"
            if availability == "global" or availability == "course":
                continue
        if _form_matches_student(doc, course_ids, branch_ids, pairs):
            row = _normalize_row(serialize_doc(doc))
            matched.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description"),
                    "file_url": row["file_url"],
                    "display_order": row.get("display_order", 0),
                }
            )
    return matched


async def create(data: RegistrationFormCreate, current_user: dict) -> Dict[str, Any]:
    if (
        not is_super_admin(current_user)
        and not is_branch_manager(current_user)
        and not is_partner_super_admin(current_user)
    ):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    pairs = [p.dict() for p in (data.branch_course_pairs or [])]
    _validate_active_with_pdf(data.status, data.file_url)
    _validate_availability_fields(
        data.availability_type,
        data.branch_ids,
        data.course_ids,
        pairs,
    )

    if is_branch_manager(current_user):
        if data.availability_type == "course":
            raise HTTPException(status_code=403, detail="Branch managers cannot create course-only forms")
        _assert_branch_manager_availability(
            data.availability_type,
            data.branch_ids,
            pairs,
            current_user,
        )

    owned_partner_branches: List[str] = []
    if is_partner_super_admin(current_user):
        owned_partner_branches = await _partner_owned_branch_ids(current_user)
        _assert_partner_availability(
            data.availability_type,
            data.branch_ids,
            data.course_ids,
            pairs,
            owned_partner_branches,
        )

    now = datetime.utcnow()
    doc = {
        "id": new_doc_id(),
        "name": data.name.strip(),
        "description": (data.description or "").strip() or None,
        "file_url": (data.file_url or "").strip() or None,
        "status": data.status,
        "display_order": data.display_order,
        "availability_type": data.availability_type,
        "branch_ids": list(data.branch_ids or []),
        "course_ids": list(data.course_ids or []),
        "branch_course_pairs": pairs,
        "created_by": stamp_created_by(current_user),
        "created_at": now,
        "updated_at": now,
    }
    stamp_tenant(doc, current_user)
    await get_db()[COLLECTION].insert_one(doc)
    return _normalize_row(serialize_doc(doc))


async def update(form_id: str, data: RegistrationFormUpdate, current_user: dict) -> Dict[str, Any]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": form_id})
    await _assert_partner_can_modify(doc, current_user)

    payload = data.dict(exclude_unset=True)
    if "branch_course_pairs" in payload and payload["branch_course_pairs"] is not None:
        payload["branch_course_pairs"] = [
            {"branch_id": p["branch_id"], "course_id": p["course_id"]}
            for p in payload["branch_course_pairs"]
        ]

    merged = {**doc, **payload}
    if is_branch_manager(current_user):
        availability = merged.get("availability_type") or "global"
        if availability == "course":
            raise HTTPException(status_code=403, detail="Branch managers cannot manage course-only forms")
        _assert_branch_manager_availability(
            availability,
            merged.get("branch_ids"),
            merged.get("branch_course_pairs"),
            current_user,
        )

    if is_partner_super_admin(current_user):
        owned = await _partner_owned_branch_ids(current_user)
        _assert_partner_availability(
            merged.get("availability_type") or "branch",
            merged.get("branch_ids"),
            merged.get("course_ids"),
            merged.get("branch_course_pairs"),
            owned,
        )

    _validate_active_with_pdf(merged.get("status", "inactive"), merged.get("file_url"))
    _validate_availability_fields(
        merged.get("availability_type") or "global",
        merged.get("branch_ids"),
        merged.get("course_ids"),
        merged.get("branch_course_pairs"),
    )

    update_fields: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    for k, v in payload.items():
        if k == "name" and isinstance(v, str):
            update_fields[k] = v.strip()
        elif k == "description":
            update_fields[k] = ((v or "").strip() or None) if v is not None else doc.get("description")
        elif k == "file_url":
            update_fields[k] = ((v or "").strip() or None) if v is not None else doc.get("file_url")
        else:
            update_fields[k] = v

    # Keep partner stamp stable on edits
    if is_partner_super_admin(current_user):
        org_id = partner_org_id_of(current_user)
        if org_id:
            update_fields["partner_org_id"] = org_id

    await db[COLLECTION].update_one({"id": form_id}, {"$set": update_fields})
    updated = await db[COLLECTION].find_one({"id": form_id})
    return _normalize_row(serialize_doc(updated))


async def delete_form(form_id: str, current_user: dict) -> Dict[str, str]:
    db = get_db()
    doc = await db[COLLECTION].find_one({"id": form_id})
    await _assert_partner_can_modify(doc, current_user)
    await db[COLLECTION].delete_one({"id": form_id})
    return {"message": "Registration form deleted"}
