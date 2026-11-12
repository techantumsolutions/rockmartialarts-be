import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from models.store_models import (
    StoreCategory,
    StoreCategoryCreate,
    StoreCategoryUpdate,
    StoreOrder,
    StoreOrderCreate,
    StoreOrderItem,
    StorePaymentOrderRequest,
    StoreProduct,
    StoreProductCreate,
    StoreProductUpdate,
    OrderFulfillmentUpdate,
)
from utils.database import get_db
from utils.helpers import serialize_doc

logger = logging.getLogger(__name__)


def calculate_final_price(price: float, offer_percentage: float) -> float:
    pct = max(0.0, min(100.0, float(offer_percentage or 0.0)))
    discount = (price * pct) / 100.0
    return round(max(0.0, price - discount), 2)


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")


class StoreController:
    # -------------------------------------------------------------------------
    # Categories
    # -------------------------------------------------------------------------
    @staticmethod
    async def get_categories(active_only: bool = False) -> List[Dict[str, Any]]:
        db = get_db()
        query: Dict[str, Any] = {}
        if active_only:
            query["is_active"] = True

        cursor = db.store_categories.find(query).sort("display_order", 1)
        categories = await cursor.to_list(length=200)

        # Count products for each category
        result = []
        for cat in categories:
            cat_data = serialize_doc(cat)
            count = await db.store_products.count_documents({
                "category_id": cat["id"],
                "is_active": True
            })
            cat_data["product_count"] = count
            result.append(cat_data)
        return result

    @staticmethod
    async def create_category(payload: StoreCategoryCreate, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can manage store categories")

        db = get_db()
        name = payload.name.strip()
        slug = (payload.slug or slugify(name)).strip()

        # Check existing slug
        existing = await db.store_categories.find_one({"slug": slug})
        if existing:
            slug = f"{slug}-{uuid.uuid4().hex[:4]}"

        category = StoreCategory(
            name=name,
            slug=slug,
            description=payload.description or "",
            display_order=payload.display_order or 0,
            is_active=payload.is_active if payload.is_active is not None else True,
        )
        cat_dict = category.dict()
        await db.store_categories.insert_one(cat_dict)
        return serialize_doc(cat_dict)

    @staticmethod
    async def update_category(category_id: str, payload: StoreCategoryUpdate, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can manage store categories")

        db = get_db()
        category = await db.store_categories.find_one({"id": category_id})
        if not category:
            raise HTTPException(status_code=404, detail="Store category not found")

        update_dict: Dict[str, Any] = {"updated_at": datetime.utcnow()}
        if payload.name is not None:
            update_dict["name"] = payload.name.strip()
        if payload.slug is not None:
            update_dict["slug"] = slugify(payload.slug)
        if payload.description is not None:
            update_dict["description"] = payload.description.strip()
        if payload.display_order is not None:
            update_dict["display_order"] = payload.display_order
        if payload.is_active is not None:
            update_dict["is_active"] = payload.is_active

        await db.store_categories.update_one({"id": category_id}, {"$set": update_dict})

        # If name or slug updated, update matching products
        if "name" in update_dict or "slug" in update_dict:
            prod_updates: Dict[str, Any] = {}
            if "name" in update_dict:
                prod_updates["category_name"] = update_dict["name"]
            if "slug" in update_dict:
                prod_updates["category_slug"] = update_dict["slug"]
            await db.store_products.update_many({"category_id": category_id}, {"$set": prod_updates})

        updated = await db.store_categories.find_one({"id": category_id})
        return serialize_doc(updated)

    @staticmethod
    async def delete_category(category_id: str, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can manage store categories")

        db = get_db()
        # Check if products exist in category
        prod_count = await db.store_products.count_documents({"category_id": category_id})
        if prod_count > 0:
            raise HTTPException(status_code=400, detail=f"Cannot delete category. {prod_count} products are assigned to it.")

        result = await db.store_categories.delete_one({"id": category_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Category not found")
        return {"message": "Category deleted successfully"}

    # -------------------------------------------------------------------------
    # Products
    # -------------------------------------------------------------------------
    @staticmethod
    async def get_products(
        category_id: Optional[str] = None,
        category_slug: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        current_user: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        db = get_db()
        query: Dict[str, Any] = {}

        # Tenant isolation check:
        # If user is a partner admin or partner student, block access
        if current_user:
            role = str(current_user.get("role") or "").lower()
            partner_org = str(current_user.get("partner_org_id") or "").strip()
            if role == "partner_super_admin" or (role == "student" and partner_org):
                raise HTTPException(status_code=403, detail="Store is not available for partner accounts")

            # Non-superadmin callers should only see active products
            if role != "super_admin":
                query["is_active"] = True
        else:
            if is_active is not None:
                query["is_active"] = is_active
            else:
                query["is_active"] = True

        if is_active is not None and (current_user or {}).get("role") == "super_admin":
            query["is_active"] = is_active

        if category_id and category_id != "all":
            query["category_id"] = category_id
        elif category_slug and category_slug != "all":
            query["category_slug"] = category_slug

        if search and search.strip():
            query["title"] = {"$regex": re.escape(search.strip()), "$options": "i"}

        cursor = db.store_products.find(query).sort("created_at", -1)
        products = await cursor.to_list(length=500)
        return [serialize_doc(p) for p in products]

    @staticmethod
    async def get_product(product_id: str, current_user: Optional[dict] = None) -> Dict[str, Any]:
        db = get_db()
        product = await db.store_products.find_one({"id": product_id})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Partner check
        if current_user:
            role = str(current_user.get("role") or "").lower()
            partner_org = str(current_user.get("partner_org_id") or "").strip()
            if role == "partner_super_admin" or (role == "student" and partner_org):
                raise HTTPException(status_code=403, detail="Store is not available for partner accounts")

        return serialize_doc(product)

    @staticmethod
    async def create_product(payload: StoreProductCreate, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can create products")

        db = get_db()
        # Resolve category
        category = await db.store_categories.find_one({
            "$or": [{"id": payload.category_id}, {"slug": payload.category_id}]
        })
        if not category:
            raise HTTPException(status_code=400, detail="Invalid category ID")

        price = float(payload.price or 0.0)
        offer_pct = float(payload.offer_percentage or 0.0)
        final_price = calculate_final_price(price, offer_pct)

        product = StoreProduct(
            title=payload.title.strip(),
            description=(payload.description or "").strip(),
            category_id=category["id"],
            category_slug=category.get("slug", ""),
            category_name=category.get("name", ""),
            price=price,
            offer_percentage=offer_pct,
            final_price=final_price,
            stock=max(0, int(payload.stock or 0)),
            image_url=(payload.image_url or "").strip(),
            is_active=payload.is_active if payload.is_active is not None else True,
        )
        prod_dict = product.dict()
        await db.store_products.insert_one(prod_dict)
        return serialize_doc(prod_dict)

    @staticmethod
    async def update_product(product_id: str, payload: StoreProductUpdate, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can update products")

        db = get_db()
        existing = await db.store_products.find_one({"id": product_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Product not found")

        update_dict: Dict[str, Any] = {"updated_at": datetime.utcnow()}

        if payload.title is not None:
            update_dict["title"] = payload.title.strip()
        if payload.description is not None:
            update_dict["description"] = payload.description.strip()
        if payload.image_url is not None:
            update_dict["image_url"] = payload.image_url.strip()
        if payload.stock is not None:
            update_dict["stock"] = max(0, int(payload.stock))
        if payload.is_active is not None:
            update_dict["is_active"] = payload.is_active

        if payload.category_id is not None:
            cat = await db.store_categories.find_one({
                "$or": [{"id": payload.category_id}, {"slug": payload.category_id}]
            })
            if cat:
                update_dict["category_id"] = cat["id"]
                update_dict["category_slug"] = cat.get("slug", "")
                update_dict["category_name"] = cat.get("name", "")

        price = payload.price if payload.price is not None else existing.get("price", 0.0)
        offer_pct = payload.offer_percentage if payload.offer_percentage is not None else existing.get("offer_percentage", 0.0)
        if payload.price is not None or payload.offer_percentage is not None:
            update_dict["price"] = float(price)
            update_dict["offer_percentage"] = float(offer_pct)
            update_dict["final_price"] = calculate_final_price(float(price), float(offer_pct))

        await db.store_products.update_one({"id": product_id}, {"$set": update_dict})
        updated = await db.store_products.find_one({"id": product_id})
        return serialize_doc(updated)

    @staticmethod
    async def toggle_product_status(product_id: str, is_active: bool, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can update product status")

        db = get_db()
        result = await db.store_products.update_one(
            {"id": product_id},
            {"$set": {"is_active": is_active, "updated_at": datetime.utcnow()}}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")

        updated = await db.store_products.find_one({"id": product_id})
        return serialize_doc(updated)

    @staticmethod
    async def delete_product(product_id: str, current_user: dict) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can delete products")

        db = get_db()
        result = await db.store_products.delete_one({"id": product_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        return {"message": "Product deleted successfully"}

    # -------------------------------------------------------------------------
    # Orders & Purchases
    # -------------------------------------------------------------------------
    @staticmethod
    async def create_payment_order(payload: StorePaymentOrderRequest, current_user: dict) -> Dict[str, Any]:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        role = str(current_user.get("role") or "").lower()
        if role != "student":
            raise HTTPException(status_code=403, detail="Only students can purchase store products")

        partner_org = str(current_user.get("partner_org_id") or "").strip()
        if partner_org:
            raise HTTPException(status_code=403, detail="Store is not available for partner students")

        db = get_db()
        product = await db.store_products.find_one({"id": payload.product_id, "is_active": True})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found or inactive")

        quantity = max(1, int(payload.quantity or 1))
        current_stock = int(product.get("stock", 0))
        if current_stock < quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock. Only {current_stock} item(s) available.")

        unit_final_price = float(product.get("final_price") or product.get("price") or 0.0)
        total_amount = round(unit_final_price * quantity, 2)
        amount_paise = int(round(total_amount * 100))

        student_id = str(current_user.get("id"))
        key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()

        order_id = None
        try:
            from utils.razorpay_client import get_razorpay_client
            client = get_razorpay_client()
            rz_order = client.order.create({
                "amount": amount_paise,
                "currency": "INR",
                "payment_capture": 1,
                "notes": {
                    "product_id": str(payload.product_id)[:40],
                    "quantity": str(quantity),
                    "student_id": student_id[:40],
                    "product_title": str(product.get("title", ""))[:40],
                },
            })
            order_id = rz_order.get("id")
        except Exception as e:
            logger.warning(f"Could not create Razorpay order for store: {e}")

        return {
            "order_id": order_id,
            "amount": amount_paise,
            "currency": "INR",
            "key": key_id,
            "product_title": product.get("title"),
            "total_amount": total_amount,
        }

    @staticmethod
    async def create_order(payload: StoreOrderCreate, current_user: dict) -> Dict[str, Any]:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        role = str(current_user.get("role") or "").lower()
        if role != "student":
            raise HTTPException(status_code=403, detail="Only students can purchase store products")

        partner_org = str(current_user.get("partner_org_id") or "").strip()
        if partner_org:
            raise HTTPException(status_code=403, detail="Store is not available for partner students")

        db = get_db()
        product = await db.store_products.find_one({"id": payload.product_id, "is_active": True})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found or inactive")

        quantity = max(1, int(payload.quantity or 1))
        current_stock = int(product.get("stock", 0))
        if current_stock < quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock. Only {current_stock} item(s) available.")

        # Resolve full student details from db.users
        student_id = str(current_user.get("id"))
        user_doc = await db.users.find_one({"id": student_id})
        student_name = (
            (user_doc or {}).get("full_name")
            or current_user.get("full_name")
            or f"{(user_doc or {}).get('first_name', '')} {(user_doc or {}).get('last_name', '')}".strip()
            or "Student"
        )
        student_email = (user_doc or {}).get("email") or current_user.get("email") or ""
        student_phone = (user_doc or {}).get("phone") or current_user.get("phone") or ""

        branch_id = (user_doc or {}).get("branch_id") or current_user.get("branch_id")
        if not branch_id and (user_doc or {}).get("branch"):
            branch_id = (user_doc.get("branch") or {}).get("branch_id")

        branch_name = "Main Branch"
        if branch_id:
            branch_doc = await db.branches.find_one({"id": branch_id})
            if branch_doc:
                branch_name = (branch_doc.get("branch") or {}).get("name") or branch_doc.get("name") or "Main Branch"

        unit_final_price = float(product.get("final_price") or product.get("price") or 0.0)
        total_amount = round(unit_final_price * quantity, 2)

        order_item = StoreOrderItem(
            product_id=product["id"],
            title=product.get("title", ""),
            category_name=product.get("category_name", ""),
            price=float(product.get("price", 0.0)),
            offer_percentage=float(product.get("offer_percentage", 0.0)),
            final_price=unit_final_price,
            quantity=quantity,
            image_url=product.get("image_url", ""),
        )

        date_code = datetime.utcnow().strftime("%Y%m%d")
        rand_code = uuid.uuid4().hex[:5].upper()
        order_number = f"RMA-ORD-{date_code}-{rand_code}"

        order = StoreOrder(
            order_number=order_number,
            student_id=student_id,
            student_name=student_name,
            student_email=student_email,
            student_phone=student_phone,
            branch_id=branch_id,
            branch_name=branch_name,
            items=[order_item],
            total_amount=total_amount,
            payment_method=payload.payment_method or "razorpay",
            payment_status=payload.payment_status or "paid",
            payment_id=payload.payment_id,
            razorpay_order_id=payload.razorpay_order_id,
            razorpay_signature=payload.razorpay_signature,
            fulfillment_status="pending",
            given_to_student=False,
        )

        order_dict = order.dict()
        await db.store_orders.insert_one(order_dict)

        # Decrement product stock
        await db.store_products.update_one(
            {"id": product["id"]},
            {"$inc": {"stock": -quantity}, "$set": {"updated_at": datetime.utcnow()}}
        )

        return serialize_doc(order_dict)

    @staticmethod
    async def get_student_orders(current_user: dict) -> List[Dict[str, Any]]:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        student_id = str(current_user.get("id"))
        db = get_db()
        cursor = db.store_orders.find({"student_id": student_id}).sort("created_at", -1)
        orders = await cursor.to_list(length=200)
        return [serialize_doc(o) for o in orders]

    @staticmethod
    async def get_admin_orders(
        fulfillment_status: Optional[str] = None,
        search: Optional[str] = None,
        current_user: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can view all store orders")

        db = get_db()
        query: Dict[str, Any] = {}

        if fulfillment_status == "given":
            query["given_to_student"] = True
        elif fulfillment_status == "pending":
            query["given_to_student"] = False

        if search and search.strip():
            s = re.escape(search.strip())
            query["$or"] = [
                {"order_number": {"$regex": s, "$options": "i"}},
                {"student_name": {"$regex": s, "$options": "i"}},
                {"student_email": {"$regex": s, "$options": "i"}},
                {"student_phone": {"$regex": s, "$options": "i"}},
                {"items.title": {"$regex": s, "$options": "i"}},
            ]

        cursor = db.store_orders.find(query).sort("created_at", -1)
        orders = await cursor.to_list(length=500)
        return [serialize_doc(o) for o in orders]

    @staticmethod
    async def update_order_fulfillment(
        order_id: str,
        payload: OrderFulfillmentUpdate,
        current_user: dict,
    ) -> Dict[str, Any]:
        if str((current_user or {}).get("role") or "").lower() != "super_admin":
            raise HTTPException(status_code=403, detail="Only Super Admin can update order fulfillment status")

        db = get_db()
        order = await db.store_orders.find_one({"id": order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        now = datetime.utcnow()
        admin_name = current_user.get("full_name") or current_user.get("email") or "Super Admin"

        update_fields: Dict[str, Any] = {
            "given_to_student": payload.given_to_student,
            "fulfillment_status": "given_to_student" if payload.given_to_student else "pending",
            "updated_at": now,
        }

        if payload.given_to_student:
            update_fields["given_at"] = now
            update_fields["given_by_admin_name"] = admin_name
        else:
            update_fields["given_at"] = None
            update_fields["given_by_admin_name"] = None

        await db.store_orders.update_one({"id": order_id}, {"$set": update_fields})
        updated = await db.store_orders.find_one({"id": order_id})
        return serialize_doc(updated)


# -----------------------------------------------------------------------------
# Database Seeding from website /store page
# -----------------------------------------------------------------------------
DEFAULT_STORE_CATEGORIES = [
    {"name": "Uniforms", "slug": "uniforms", "description": "Karate, TKD, Kung Fu uniforms and gi", "display_order": 1},
    {"name": "Sparring Gear", "slug": "sparring-gear", "description": "Pads, guards, and sparring kits", "display_order": 2},
    {"name": "Boxing", "slug": "boxing", "description": "Boxing gloves, wraps, and training gear", "display_order": 3},
    {"name": "Belts", "slug": "belts", "description": "Solid martial arts color belts and double wraps", "display_order": 4},
    {"name": "Training Weapons", "slug": "weapons", "description": "Bo staffs, swords, kama, and practice weapons", "display_order": 5},
    {"name": "Accessories", "slug": "accessories", "description": "Heavy bags, kicking shields, and accessories", "display_order": 6},
]

DEFAULT_STORE_PRODUCTS = [
    {"title": "Karate uniform", "price": 2500.0, "offer_percentage": 10.0, "category_slug": "uniforms", "image_url": "/assets/img/store/img1.png", "stock": 50, "description": "Premium 100% cotton traditional karate uniform with reinforced stitching for intense kata and sparring."},
    {"title": "Square Blade Kama", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "weapons", "image_url": "/assets/img/store/img7.png", "stock": 30, "description": "Balanced martial arts kama with square blades and solid natural hardwood handles."},
    {"title": "Power Tilt Free Standing Bag", "price": 2500.0, "offer_percentage": 15.0, "category_slug": "accessories", "image_url": "/assets/img/store/img10.png", "stock": 20, "description": "Heavy-duty free-standing punch bag with spring-back rebound system for high-velocity kicks."},
    {"title": "TKD uniform", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "uniforms", "image_url": "/assets/img/store/img3.png", "stock": 45, "description": "Lightweight breathable Taekwondo dobok with V-neck collar and elastic drawstring pants."},
    {"title": "Kung fu uniform", "price": 2500.0, "offer_percentage": 5.0, "category_slug": "uniforms", "image_url": "/assets/img/store/img4.png", "stock": 40, "description": "Traditional Chinese martial arts silk-blend uniform with hand-knotted frog buttons."},
    {"title": "Rattan Bo II Tiger", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "weapons", "image_url": "/assets/img/store/img5.png", "stock": 35, "description": "Durable lightweight tiger-stripe skinned rattan staff engineered for traditional weapon forms."},
    {"title": "Ultra Lite Chest Guard", "price": 2500.0, "offer_percentage": 20.0, "category_slug": "accessories", "image_url": "/assets/img/store/img13.png", "stock": 25, "description": "Reversible red/blue competition chest protector with multi-density shock-absorbing foam."},
    {"title": "Determination Kickboxing Punches", "price": 2500.0, "offer_percentage": 10.0, "category_slug": "weapons", "image_url": "/assets/img/store/img6.png", "stock": 30, "description": "High-impact foam padded martial arts targets for speed, punch accuracy, and precision."},
    {"title": "Course Available Locations", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "uniforms", "image_url": "/assets/img/store/img2.png", "stock": 40, "description": "Official Rock Martial Arts training outfit certified for all branch grading exams."},
    {"title": "Square Blade Kama", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "accessories", "image_url": "/assets/img/store/img11.png", "stock": 25, "description": "Pair of speed training accessories crafted for kobudo weaponry routines."},
    {"title": "Determination Kickboxing Punches", "price": 2500.0, "offer_percentage": 10.0, "category_slug": "boxing", "image_url": "/assets/img/store/img14.png", "stock": 30, "description": "Ergonomic curved focus mitts with wrist support for boxing and kickboxing drills."},
    {"title": "Double Wrap Solid Karate Belt", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "accessories", "image_url": "/assets/img/store/img15.png", "stock": 60, "description": "100% thick cotton double-wrap grading rank belt with 8 rows of precision stitching."},
    {"title": "Uppercut Heavy Bag", "price": 2500.0, "offer_percentage": 15.0, "category_slug": "accessories", "image_url": "/assets/img/store/img12.png", "stock": 15, "description": "Multi-angle body snatcher heavy bag designed for uppercuts, hooks, and body blows."},
    {"title": "Kick-short", "price": 2500.0, "offer_percentage": 5.0, "category_slug": "sparring-gear", "image_url": "/assets/img/store/img16.png", "stock": 40, "description": "Satin kickboxing and Muay Thai shorts featuring high side-slits for unrestricted high kicks."},
    {"title": "Practice Speedchuck", "price": 2500.0, "offer_percentage": 0.0, "category_slug": "weapons", "image_url": "/assets/img/store/img8.png", "stock": 35, "description": "Foam-padded safety training nunchaku with smooth ball-bearing stainless steel swivel chain."},
    {"title": "Dragon Samurai Sword", "price": 2500.0, "offer_percentage": 10.0, "category_slug": "weapons", "image_url": "/assets/img/store/img9.png", "stock": 15, "description": "Decorative ceremonial wooden samurai katana with dragon-embossed tsuba and lacquered saya."},
]


async def seed_store_if_empty(db) -> None:
    """Auto-seeds store categories and products from website store catalog if empty."""
    try:
        # Check categories
        cat_count = await db.store_categories.count_documents({})
        category_map = {}
        if cat_count == 0:
            logger.info("Seeding store categories...")
            for cat_data in DEFAULT_STORE_CATEGORIES:
                cat = StoreCategory(
                    name=cat_data["name"],
                    slug=cat_data["slug"],
                    description=cat_data.get("description", ""),
                    display_order=cat_data.get("display_order", 0),
                    is_active=True,
                )
                cd = cat.dict()
                await db.store_categories.insert_one(cd)
                category_map[cat_data["slug"]] = cd
            logger.info(f"Seeded {len(DEFAULT_STORE_CATEGORIES)} store categories")
        else:
            cats = await db.store_categories.find({}).to_list(length=100)
            for c in cats:
                category_map[c.get("slug")] = c

        # Check products
        prod_count = await db.store_products.count_documents({})
        if prod_count == 0:
            logger.info("Seeding store products...")
            now = datetime.utcnow()
            for prod_data in DEFAULT_STORE_PRODUCTS:
                c_slug = prod_data["category_slug"]
                cat = category_map.get(c_slug)
                cat_id = cat["id"] if cat else str(uuid.uuid4())
                cat_name = cat["name"] if cat else c_slug.capitalize()

                price = prod_data["price"]
                offer_pct = prod_data.get("offer_percentage", 0.0)
                final_price = calculate_final_price(price, offer_pct)

                prod = StoreProduct(
                    title=prod_data["title"],
                    description=prod_data.get("description", ""),
                    category_id=cat_id,
                    category_slug=c_slug,
                    category_name=cat_name,
                    price=price,
                    offer_percentage=offer_pct,
                    final_price=final_price,
                    stock=prod_data.get("stock", 25),
                    image_url=prod_data.get("image_url", ""),
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                await db.store_products.insert_one(prod.dict())
            logger.info(f"Seeded {len(DEFAULT_STORE_PRODUCTS)} store products")
    except Exception as e:
        logger.exception(f"Error seeding store data: {e}")

