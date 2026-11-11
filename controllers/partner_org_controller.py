"""White-label partner organization APIs."""

from models.partner_org_models import (
    PartnerFeaturePermissionsUpdate,
    PartnerLogin,
    PartnerOrgCreate,
    PartnerOrgUpdateStatus,
    PartnerOrgVerifyPayment,
    PartnerProfileUpdate,
    PartnerSetPassword,
    PartnerValidityUpdate,
)
from utils import partner_org_service as svc


class PartnerOrgController:
    @staticmethod
    async def list_orgs(skip: int = 0, limit: int = 100, include_inactive: bool = True):
        return await svc.list_partner_orgs(
            skip=skip, limit=limit, include_inactive=include_inactive
        )

    @staticmethod
    async def create_offline(body: PartnerOrgCreate, current_user: dict):
        return await svc.create_partner_offline(body, current_user)

    @staticmethod
    async def create_online_order(body: PartnerOrgCreate, current_user: dict):
        return await svc.create_partner_online_order(body, current_user)

    @staticmethod
    async def verify_online(body: PartnerOrgVerifyPayment, current_user: dict):
        return await svc.verify_partner_online_payment(body, current_user)

    @staticmethod
    async def resend_invite(partner_org_id: str, current_user: dict):
        return await svc.resend_partner_invite(partner_org_id, current_user)

    @staticmethod
    async def set_password(body: PartnerSetPassword):
        return await svc.set_partner_password(body)

    @staticmethod
    async def login(slug: str, body: PartnerLogin):
        return await svc.login_partner(slug, body)

    @staticmethod
    async def public_by_slug(slug: str):
        return await svc.get_partner_public_by_slug(slug)

    @staticmethod
    async def get_my_profile(current_user: dict):
        return await svc.get_partner_admin_profile(current_user)

    @staticmethod
    async def update_my_profile(current_user: dict, body: dict):
        return await svc.update_partner_admin_profile(current_user, body)

    @staticmethod
    async def set_active(partner_org_id: str, body: PartnerOrgUpdateStatus, current_user: dict):
        return await svc.set_partner_org_active(partner_org_id, body, current_user)

    @staticmethod
    async def get_me(current_user: dict):
        return await svc.get_partner_me(current_user)

    @staticmethod
    async def update_me(body: PartnerProfileUpdate, current_user: dict):
        return await svc.update_partner_me(body, current_user)

    @staticmethod
    async def update_validity(
        partner_org_id: str, body: PartnerValidityUpdate, current_user: dict
    ):
        return await svc.update_partner_validity(partner_org_id, body, current_user)

    @staticmethod
    async def get_feature_permissions(partner_org_id: str, current_user: dict):
        return await svc.get_partner_feature_permissions(partner_org_id, current_user)

    @staticmethod
    async def patch_feature_permissions(
        partner_org_id: str, body: PartnerFeaturePermissionsUpdate, current_user: dict
    ):
        return await svc.update_partner_feature_permissions(partner_org_id, body, current_user)

    @staticmethod
    async def get_my_feature_permissions(current_user: dict):
        return await svc.get_partner_feature_permissions_for_me(current_user)
