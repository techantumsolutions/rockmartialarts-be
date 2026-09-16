"""M05-S01 enrollment cart CRUD and validation."""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import HTTPException

from controllers.payment_controller import PaymentController
from models.cart_models import (
    CartDocument,
    CartItem,
    CartItemCreate,
    CartItemPricing,
    CartItemUpdate,
    CartMultiCourseAddBody,
    CartStatus,
    CartStudentCreate,
    CartStudentLine,
    CartStudentsBulkCreate,
    CartStudentUpdate,
    CartValidationIssue,
)
from utils.cart_duplicates import find_exact_duplicate, find_same_course_at_branch
from models.user_models import UserRole
from utils.cart_helpers import build_cart_public_payload, recompute_cart_totals
from utils.discount_engine import compute_cart_promotions_for_cart
from utils.cart_validation import validate_cart_document
from utils.database import get_db


class CartController:
    @staticmethod
    def _now() -> datetime:
        return datetime.utcnow()

    @staticmethod
    def _sanitize_cart_items(cart: dict) -> Tuple[dict, bool]:
        """Drop items that no longer belong to a student line (M05-S03 isolation)."""
        students = cart.get("students") or []
        valid_lines = {s.get("student_line_id") for s in students if s.get("student_line_id")}
        items = cart.get("items") or []
        cleaned = [i for i in items if i.get("student_line_id") in valid_lines]
        if len(cleaned) == len(items):
            return cart, False
        updated = dict(cart)
        updated["items"] = cleaned
        return updated, True

    @staticmethod
    async def _persist_cart_if_sanitized(cart: dict, changed: bool) -> dict:
        if not changed:
            return cart
        db = get_db()
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"items": cart.get("items") or [], "updated_at": CartController._now()}},
        )
        return cart

    @staticmethod
    async def _pricing_for_line(
        *,
        course_id: str,
        branch_id: str,
        duration_id: str,
        batch_ref: Optional[str],
        optional_student_id: Optional[str],
        beneficiary: Optional[dict],
    ) -> CartItemPricing:
        info = await PaymentController.get_course_payment_info(
            course_id,
            branch_id,
            duration_id,
            batch_ref=batch_ref,
            optional_student_id=optional_student_id,
            admission_fee_beneficiary=beneficiary,
        )
        p = info.pricing
        return CartItemPricing(
            course_fee=float(p.course_fee or 0),
            admission_fee=float(p.admission_fee or 0),
            total_amount=float(p.total_amount or 0),
            currency=p.currency or "INR",
            duration_multiplier=float(p.duration_multiplier or 1.0),
            original_price=p.original_price,
            discount_amount=p.discount_amount,
            is_flat_price=bool(p.is_flat_price),
        )

    @staticmethod
    async def reprice_items(
        items: List[dict],
        *,
        current_user: Optional[dict] = None,
        students_by_line: Optional[dict] = None,
    ) -> Tuple[List[dict], List[CartValidationIssue]]:
        issues: List[CartValidationIssue] = []
        out: List[dict] = []
        for item in items:
            sid = item.get("student_line_id")
            student_id = None
            beneficiary = {"beneficiary_type": "self"}
            if students_by_line and sid in students_by_line:
                student_id = (students_by_line[sid].get("student_id") or "").strip() or None
            fresh = await CartController._pricing_for_line(
                course_id=item["course_id"],
                branch_id=item["branch_id"],
                duration_id=item["duration_id"],
                batch_ref=item.get("batch_ref"),
                optional_student_id=student_id,
                beneficiary=beneficiary,
            )
            stored = item.get("pricing") or {}
            if round(float(stored.get("total_amount") or 0), 2) != round(fresh.total_amount, 2):
                issues.append(
                    CartValidationIssue(
                        code="fee_drift",
                        message="Pricing was refreshed to match current branch and course fees.",
                        item_id=item.get("id"),
                        student_line_id=sid,
                    )
                )
            info = await PaymentController.get_course_payment_info(
                item["course_id"],
                item["branch_id"],
                item["duration_id"],
                batch_ref=item.get("batch_ref"),
                optional_student_id=student_id,
                admission_fee_beneficiary=beneficiary,
            )
            updated = dict(item)
            updated["pricing"] = fresh.dict()
            updated["course_name"] = info.course_name
            updated["branch_name"] = info.branch_name
            updated["category_name"] = info.category_name
            updated["duration_name"] = info.duration
            out.append(updated)
        return out, issues

    @staticmethod
    async def _get_active_cart_query(owner_user_id: Optional[str], guest_token: Optional[str]) -> dict:
        if owner_user_id:
            return {"owner_user_id": owner_user_id, "status": CartStatus.ACTIVE.value}
        if guest_token:
            return {"guest_token": guest_token, "status": CartStatus.ACTIVE.value}
        raise HTTPException(status_code=400, detail="Cart identity required")

    @staticmethod
    async def ensure_cart(
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> dict:
        db = get_db()
        owner_user_id = None
        token = (guest_token or "").strip() or None

        if current_user and current_user.get("role") == UserRole.STUDENT.value:
            owner_user_id = current_user.get("id")
            token = None

        if not owner_user_id and not token:
            token = str(uuid.uuid4())

        query = await CartController._get_active_cart_query(owner_user_id, token)
        cart = await db.carts.find_one(query)
        if not cart:
            doc = CartDocument(
                owner_user_id=owner_user_id,
                guest_token=token if not owner_user_id else None,
            )
            payload = doc.dict()
            await db.carts.insert_one(payload)
            cart = payload
        elif owner_user_id and token:
            await db.carts.update_one(
                {"id": cart["id"]},
                {"$set": {"guest_token": None, "updated_at": CartController._now()}},
            )
            cart["guest_token"] = None

        if owner_user_id and token:
            guest_cart = await db.carts.find_one(
                {"guest_token": token, "status": CartStatus.ACTIVE.value}
            )
            if guest_cart and guest_cart.get("id") != cart.get("id"):
                await CartController._merge_carts(cart, guest_cart)

        return cart

    @staticmethod
    async def _merge_carts(primary: dict, secondary: dict) -> None:
        db = get_db()
        pid = primary["id"]
        students = {s["student_line_id"]: s for s in primary.get("students") or []}
        items = list(primary.get("items") or [])
        for s in secondary.get("students") or []:
            if s.get("student_line_id") not in students:
                students[s["student_line_id"]] = s
        for item in secondary.get("items") or []:
            dup = any(
                i.get("student_line_id") == item.get("student_line_id")
                and i.get("course_id") == item.get("course_id")
                and i.get("branch_id") == item.get("branch_id")
                and i.get("duration_id") == item.get("duration_id")
                and (i.get("batch_ref") or "") == (item.get("batch_ref") or "")
                for i in items
            )
            if not dup:
                items.append(item)
        await db.carts.update_one(
            {"id": pid},
            {
                "$set": {
                    "students": list(students.values()),
                    "items": items,
                    "updated_at": CartController._now(),
                }
            },
        )
        await db.carts.update_one(
            {"id": secondary["id"]},
            {"$set": {"status": CartStatus.ABANDONED.value, "updated_at": CartController._now()}},
        )

    @staticmethod
    async def get_current(
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        cart, changed = CartController._sanitize_cart_items(cart)
        cart = await CartController._persist_cart_if_sanitized(cart, changed)
        return await build_cart_public_payload(cart)

    @staticmethod
    async def add_student(
        body: CartStudentCreate,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        label = (body.label or "").strip()
        if not label:
            raise HTTPException(status_code=400, detail="Student name is required")

        if body.student_id and current_user:
            if current_user.get("role") == UserRole.STUDENT.value and current_user.get("id") != body.student_id:
                raise HTTPException(status_code=403, detail="You can only add your own student profile")

        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        students = list(cart.get("students") or [])
        if body.student_id:
            for s in students:
                if s.get("student_id") == body.student_id:
                    raise HTTPException(status_code=400, detail="This student is already in the cart")
        line = CartStudentLine(
            label=label,
            student_id=body.student_id,
            email=(body.email or "").strip() or None,
            phone=(body.phone or "").strip() or None,
        )
        students.append(line.dict())
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"students": students, "updated_at": CartController._now()}},
        )
        cart["students"] = students
        return await build_cart_public_payload(cart)

    @staticmethod
    async def add_students_bulk(
        body: CartStudentsBulkCreate,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        """Add multiple student lines in one request (M05-S03)."""
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        students = list(cart.get("students") or [])
        existing_student_ids = {s.get("student_id") for s in students if s.get("student_id")}
        added = 0
        skipped: List[dict] = []

        for entry in body.students:
            label = (entry.label or "").strip()
            if not label:
                skipped.append({"label": entry.label, "reason": "Student name is required"})
                continue
            sid = (entry.student_id or "").strip() or None
            if sid:
                if current_user and current_user.get("role") == UserRole.STUDENT.value:
                    if current_user.get("id") != sid:
                        skipped.append({"label": label, "reason": "You can only add your own student profile"})
                        continue
                if sid in existing_student_ids:
                    skipped.append({"label": label, "reason": "This student is already in the cart"})
                    continue
            line = CartStudentLine(
                label=label,
                student_id=sid,
                email=(entry.email or "").strip() or None,
                phone=(entry.phone or "").strip() or None,
            )
            students.append(line.dict())
            if sid:
                existing_student_ids.add(sid)
            added += 1

        if added == 0:
            raise HTTPException(
                status_code=400,
                detail=skipped[0]["reason"] if skipped else "No students could be added",
            )

        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"students": students, "updated_at": CartController._now()}},
        )
        cart["students"] = students
        bulk_summary = {"added": added, "skipped": skipped, "requested": len(body.students)}
        return await build_cart_public_payload(cart, bulk_summary=bulk_summary)

    @staticmethod
    async def update_student(
        student_line_id: str,
        body: CartStudentUpdate,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        students = list(cart.get("students") or [])
        found = False
        for s in students:
            if s.get("student_line_id") == student_line_id:
                if body.label is not None:
                    s["label"] = body.label.strip()
                if body.email is not None:
                    s["email"] = body.email.strip() or None
                if body.phone is not None:
                    s["phone"] = body.phone.strip() or None
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="Student not found in cart")
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"students": students, "updated_at": CartController._now()}},
        )
        cart["students"] = students
        return await build_cart_public_payload(cart)

    @staticmethod
    async def remove_student(
        student_line_id: str,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        students = [s for s in (cart.get("students") or []) if s.get("student_line_id") != student_line_id]
        items = [i for i in (cart.get("items") or []) if i.get("student_line_id") != student_line_id]
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"students": students, "items": items, "updated_at": CartController._now()}},
        )
        cart["students"] = students
        cart["items"] = items
        return await build_cart_public_payload(cart)

    @staticmethod
    async def _build_item_row(
        *,
        student_line_id: str,
        course_id: str,
        branch_id: str,
        duration_id: str,
        batch_ref: Optional[str],
        student_id: Optional[str],
    ) -> dict:
        beneficiary = {"beneficiary_type": "self"}
        pricing = await CartController._pricing_for_line(
            course_id=course_id,
            branch_id=branch_id,
            duration_id=duration_id,
            batch_ref=batch_ref,
            optional_student_id=student_id,
            beneficiary=beneficiary,
        )
        info = await PaymentController.get_course_payment_info(
            course_id,
            branch_id,
            duration_id,
            batch_ref=batch_ref,
            optional_student_id=student_id,
            admission_fee_beneficiary=beneficiary,
        )
        item = CartItem(
            student_line_id=student_line_id,
            course_id=course_id,
            branch_id=branch_id,
            duration_id=duration_id,
            batch_ref=(batch_ref or "").strip() or None,
            course_name=info.course_name,
            branch_name=info.branch_name,
            category_name=info.category_name,
            duration_name=info.duration,
            pricing=pricing,
        )
        return item.dict()

    @staticmethod
    def _assert_item_can_be_added(
        items: List[dict],
        *,
        student_line_id: str,
        course_id: str,
        branch_id: str,
        duration_id: str,
        batch_ref: Optional[str],
        exclude_item_id: Optional[str] = None,
    ) -> None:
        batch_ref = (batch_ref or "").strip() or None
        if find_exact_duplicate(
            items,
            student_line_id=student_line_id,
            course_id=course_id,
            branch_id=branch_id,
            duration_id=duration_id,
            batch_ref=batch_ref,
            exclude_item_id=exclude_item_id,
        ):
            raise HTTPException(status_code=400, detail="This selection is already in the cart")
        existing_cb = find_same_course_at_branch(
            items,
            student_line_id=student_line_id,
            course_id=course_id,
            branch_id=branch_id,
            exclude_item_id=exclude_item_id,
        )
        if existing_cb:
            raise HTTPException(
                status_code=400,
                detail="This course is already in the cart for this student at this branch. "
                "Remove the existing line or change its duration there.",
            )

    @staticmethod
    async def add_item(
        body: CartItemCreate,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        students = {s.get("student_line_id"): s for s in (cart.get("students") or [])}
        if body.student_line_id not in students:
            raise HTTPException(status_code=400, detail="Add a student to the cart first")

        student_row = students[body.student_line_id]
        student_id = (student_row.get("student_id") or "").strip() or None
        items = list(cart.get("items") or [])
        CartController._assert_item_can_be_added(
            items,
            student_line_id=body.student_line_id,
            course_id=body.course_id,
            branch_id=body.branch_id,
            duration_id=body.duration_id,
            batch_ref=body.batch_ref,
        )
        items.append(
            await CartController._build_item_row(
                student_line_id=body.student_line_id,
                course_id=body.course_id,
                branch_id=body.branch_id,
                duration_id=body.duration_id,
                batch_ref=body.batch_ref,
                student_id=student_id,
            )
        )

        issues, items = await validate_cart_document(
            {**cart, "items": items},
            current_user=current_user,
            reprice_check=False,
        )
        blocking = [i for i in issues if i.code not in ("fee_drift",)]
        if blocking:
            raise HTTPException(status_code=400, detail=blocking[0].message)

        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"items": items, "updated_at": CartController._now()}},
        )
        cart["items"] = items
        return await build_cart_public_payload(cart)

    @staticmethod
    async def add_multi_courses(
        body: CartMultiCourseAddBody,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        """Add multiple courses for one student at one branch (M05-S02)."""
        if not body.selections:
            raise HTTPException(status_code=400, detail="Select at least one course")

        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        students = {s.get("student_line_id"): s for s in (cart.get("students") or [])}
        if body.student_line_id not in students:
            raise HTTPException(status_code=400, detail="Student not found in cart")

        student_id = (students[body.student_line_id].get("student_id") or "").strip() or None
        items = list(cart.get("items") or [])
        added = 0
        skipped: List[dict] = []
        seen_in_request: set = set()

        for sel in body.selections:
            cid = (sel.course_id or "").strip()
            did = (sel.duration_id or "").strip()
            if not cid or not did:
                skipped.append({"course_id": cid or None, "reason": "Course and duration are required"})
                continue
            req_key = (body.student_line_id, cid, body.branch_id, did, (sel.batch_ref or "").strip())
            if req_key in seen_in_request:
                skipped.append({"course_id": cid, "reason": "Duplicate in this request"})
                continue
            seen_in_request.add(req_key)

            try:
                CartController._assert_item_can_be_added(
                    items,
                    student_line_id=body.student_line_id,
                    course_id=cid,
                    branch_id=body.branch_id,
                    duration_id=did,
                    batch_ref=sel.batch_ref,
                )
            except HTTPException as exc:
                skipped.append({"course_id": cid, "reason": str(exc.detail)})
                continue

            try:
                row = await CartController._build_item_row(
                    student_line_id=body.student_line_id,
                    course_id=cid,
                    branch_id=body.branch_id,
                    duration_id=did,
                    batch_ref=sel.batch_ref,
                    student_id=student_id,
                )
            except HTTPException as exc:
                skipped.append({"course_id": cid, "reason": str(exc.detail)})
                continue
            except Exception:
                skipped.append({"course_id": cid, "reason": "Could not price this course"})
                continue

            items.append(row)
            added += 1

        if added == 0:
            raise HTTPException(
                status_code=400,
                detail=skipped[0]["reason"] if skipped else "No courses could be added",
            )

        issues, items = await validate_cart_document(
            {**cart, "items": items},
            current_user=current_user,
            reprice_check=False,
        )
        blocking = [i for i in issues if i.code not in ("fee_drift",)]
        if blocking:
            raise HTTPException(status_code=400, detail=blocking[0].message)

        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"items": items, "updated_at": CartController._now()}},
        )
        cart["items"] = items
        bulk_summary = {"added": added, "skipped": skipped, "requested": len(body.selections)}
        return await build_cart_public_payload(cart, bulk_summary=bulk_summary)

    @staticmethod
    async def update_item(
        item_id: str,
        body: CartItemUpdate,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        items = list(cart.get("items") or [])
        students = {s.get("student_line_id"): s for s in (cart.get("students") or [])}
        target = None
        for item in items:
            if item.get("id") == item_id:
                target = item
                break
        if not target:
            raise HTTPException(status_code=404, detail="Cart item not found")

        if body.duration_id is not None:
            target["duration_id"] = body.duration_id
        if body.batch_ref is not None:
            target["batch_ref"] = body.batch_ref.strip() or None

        CartController._assert_item_can_be_added(
            items,
            student_line_id=target["student_line_id"],
            course_id=target["course_id"],
            branch_id=target["branch_id"],
            duration_id=target["duration_id"],
            batch_ref=target.get("batch_ref"),
            exclude_item_id=target.get("id"),
        )

        student_id = (students.get(target["student_line_id"], {}).get("student_id") or "").strip() or None
        pricing = await CartController._pricing_for_line(
            course_id=target["course_id"],
            branch_id=target["branch_id"],
            duration_id=target["duration_id"],
            batch_ref=target.get("batch_ref"),
            optional_student_id=student_id,
            beneficiary={"beneficiary_type": "self"},
        )
        info = await PaymentController.get_course_payment_info(
            target["course_id"],
            target["branch_id"],
            target["duration_id"],
            batch_ref=target.get("batch_ref"),
            optional_student_id=student_id,
            admission_fee_beneficiary={"beneficiary_type": "self"},
        )
        target["pricing"] = pricing.dict()
        target["course_name"] = info.course_name
        target["branch_name"] = info.branch_name
        target["category_name"] = info.category_name
        target["duration_name"] = info.duration

        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"items": items, "updated_at": CartController._now()}},
        )
        cart["items"] = items
        return await build_cart_public_payload(cart)

    @staticmethod
    async def remove_item(
        item_id: str,
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        items = [i for i in (cart.get("items") or []) if i.get("id") != item_id]
        if len(items) == len(cart.get("items") or []):
            raise HTTPException(status_code=404, detail="Cart item not found")
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"items": items, "updated_at": CartController._now()}},
        )
        cart["items"] = items
        return await build_cart_public_payload(cart)

    @staticmethod
    async def validate_current(
        *,
        current_user: Optional[dict],
        guest_token: Optional[str],
    ) -> Dict[str, Any]:
        db = get_db()
        cart = await CartController.ensure_cart(current_user=current_user, guest_token=guest_token)
        issues, items = await validate_cart_document(cart, current_user=current_user, reprice_check=True)
        if items != (cart.get("items") or []):
            await db.carts.update_one(
                {"id": cart["id"]},
                {"$set": {"items": items, "updated_at": CartController._now()}},
            )
            cart["items"] = items
        promo = await compute_cart_promotions_for_cart(cart)
        snapshot = promo.to_snapshot()
        await db.carts.update_one(
            {"id": cart["id"]},
            {"$set": {"discount_snapshot": snapshot, "updated_at": CartController._now()}},
        )
        cart["discount_snapshot"] = snapshot
        return await build_cart_public_payload(cart, validation=issues)
