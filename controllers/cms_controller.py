from fastapi import HTTPException, status
from datetime import datetime
from typing import Dict, Any
from bson import ObjectId

from models.cms_models import (
    CMSContent, CMSContentUpdate, CMSContentResponse,
    HomepageSection, FooterContent, BrandingSettings, SEOSettings
)
from utils.database import get_db, serialize_doc


class CMSController:
    @staticmethod
    async def get_cms_content(current_user: dict) -> CMSContentResponse:
        """Get CMS content"""
        try:
            db = get_db()
            collection = db.cms_content
            doc = await collection.find_one({})
            if not doc:
                doc = await CMSController._create_default_content()
            return CMSController._to_response(doc)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve CMS content: {str(e)}"
            )


    @staticmethod
    async def get_cms_content_public():
        """Get CMS content for public website (no auth)"""
        try:
            db = get_db()
            collection = db.cms_content
            doc = await collection.find_one({})
            if not doc:
                doc = await CMSController._create_default_content()
            return CMSController._to_response(doc)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve CMS content: {str(e)}"
            )

    @staticmethod
    async def get_branch_testimonials_public(branch_id: str, limit: int = 4):
        """Student testimonials mapped to a branch (from CMS homepage list)."""
        try:
            safe_limit = max(1, min(limit, 20))
            db = get_db()
            collection = db.cms_content
            doc = await collection.find_one({})
            if not doc:
                doc = await CMSController._create_default_content()
            homepage = doc.get("homepage") or {}
            raw = homepage.get("testimonials") or []
            matched = [t for t in raw if t.get("branch_id") == branch_id][:safe_limit]
            return {
                "testimonials": matched,
                "testimonials_title": homepage.get("testimonials_title"),
                "testimonials_subtitle": homepage.get("testimonials_subtitle"),
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve branch testimonials: {str(e)}"
            )

    @staticmethod
    async def update_cms_content(data: CMSContentUpdate, current_user: dict) -> CMSContentResponse:
        """Update CMS content"""
        try:
            db = get_db()
            collection = db.cms_content
            existing = await collection.find_one({})

            update_data = {}
            if data.homepage is not None:
                update_data["homepage"] = data.homepage.dict()
            if data.footer is not None:
                update_data["footer"] = data.footer.dict()
            if data.branding is not None:
                from utils.platform_branding import normalize_platform_branding_patch

                existing_branding = (existing or {}).get("branding") if existing else {}
                if not isinstance(existing_branding, dict):
                    existing_branding = {}
                update_data["branding"] = normalize_platform_branding_patch(
                    {**existing_branding, **data.branding.dict(exclude_unset=True)}
                )
            if data.page_seo is not None:
                update_data["page_seo"] = {k: v.dict() for k, v in data.page_seo.items()}

            update_data["updated_at"] = datetime.utcnow()

            if existing:
                await collection.update_one(
                    {"_id": existing["_id"]},
                    {"$set": update_data}
                )
                updated = await collection.find_one({"_id": existing["_id"]})
            else:
                update_data["created_at"] = datetime.utcnow()
                if "homepage" not in update_data:
                    update_data["homepage"] = HomepageSection().dict()
                if "footer" not in update_data:
                    update_data["footer"] = FooterContent().dict()
                if "branding" not in update_data:
                    update_data["branding"] = BrandingSettings().dict()
                if "page_seo" not in update_data:
                    update_data["page_seo"] = {}
                result = await collection.insert_one(update_data)
                updated = await collection.find_one({"_id": result.inserted_id})

            # Keep invoice system_name aligned with platform legal/display name
            if "branding" in update_data:
                try:
                    branding = update_data["branding"] or {}
                    await db.system_settings.update_one(
                        {},
                        {
                            "$set": {
                                "system_name": branding.get("legal_name")
                                or branding.get("platform_name")
                                or "Rock Martial Arts Academy",
                                "updated_at": datetime.utcnow(),
                            }
                        },
                        upsert=True,
                    )
                except Exception:
                    pass

            return CMSController._to_response(updated)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update CMS content: {str(e)}"
            )

    @staticmethod
    async def upload_branding_image(field: str, image_url: str, current_user: dict) -> CMSContentResponse:
        """Update a specific branding image (navbar_logo, footer_logo, favicon)"""
        try:
            if field not in ["navbar_logo", "footer_logo", "favicon", "site_loader_image"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid branding field"
                )
            db = get_db()
            collection = db.cms_content
            existing = await collection.find_one({})
            if not existing:
                existing = await CMSController._create_default_content()
            
            await collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {f"branding.{field}": image_url, "updated_at": datetime.utcnow()}}
            )
            updated = await collection.find_one({"_id": existing["_id"]})
            return CMSController._to_response(updated)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update branding image: {str(e)}"
            )

    @staticmethod
    async def _create_default_content() -> Dict[str, Any]:
        db = get_db()
        collection = db.cms_content
        default = {
            "homepage": HomepageSection().dict(),
            "footer": FooterContent().dict(),
            "branding": BrandingSettings().dict(),
            "page_seo": {},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        result = await collection.insert_one(default)
        return await collection.find_one({"_id": result.inserted_id})

    @staticmethod
    def _to_response(doc: Dict[str, Any]) -> CMSContentResponse:
        from utils.platform_branding import branding_from_cms_doc

        serialized = serialize_doc(doc)
        homepage_data = serialized.get("homepage", {})
        footer_data = serialized.get("footer", {})
        branding_data = branding_from_cms_doc(serialized)
        page_seo_data = serialized.get("page_seo", {})

        seo_dict = {}
        for k, v in page_seo_data.items():
            if isinstance(v, dict):
                seo_dict[k] = SEOSettings(**v)

        return CMSContentResponse(
            id=serialized.get("id", ""),
            homepage=HomepageSection(**homepage_data) if homepage_data else HomepageSection(),
            footer=FooterContent(**footer_data) if footer_data else FooterContent(),
            branding=BrandingSettings(**branding_data) if branding_data else BrandingSettings(),
            page_seo=seo_dict,
            created_at=serialized.get("created_at"),
            updated_at=serialized.get("updated_at"),
        )

    @staticmethod
    async def get_platform_branding_public():
        from utils.platform_branding import get_platform_branding

        return {"branding": await get_platform_branding()}

    @staticmethod
    async def update_platform_branding(data: dict, current_user: dict):
        from utils.platform_branding import save_platform_branding

        branding = await save_platform_branding(data or {})
        return {"message": "Platform branding updated", "branding": branding}
