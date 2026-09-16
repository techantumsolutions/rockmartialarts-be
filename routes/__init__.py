# Routes package for Student Management System

from .auth_routes import router as auth_router
from .user_routes import router as user_router
from .coach_routes import router as coach_router
from .branch_routes import router as branch_router
from .branch_manager_routes import router as branch_manager_router
from .course_routes import router as course_router
from .category_routes import router as category_router
from .duration_routes import router as duration_router
from .location_routes import router as location_router
from .state_routes import router as state_router
from .city_routes import router as city_router
from .branch_public_routes import router as branch_public_router
from .public_branch_routes import router as public_branch_router
from .public_branch_by_slug_routes import router as public_branch_by_slug_router
from .enrollment_routes import router as enrollment_router
from .payment_routes import router as payment_router
from .request_routes import router as request_router
from .event_routes import router as event_router
from .search_routes import router as search_router
from .email_routes import router as email_router
from .dashboard_routes import router as dashboard_router
from .settings_routes import router as settings_router
from .reports_routes import router as reports_router
from .attendance_routes import router as attendance_router
from .message_routes import router as message_router
from .dropdown_settings_routes import router as dropdown_settings_router
from .upload_routes import router as upload_router
from .cms_routes import router as cms_router
from .lead_routes import router as lead_router

__all__ = [
    'auth_router',
    'user_router',
    'coach_router',
    'branch_router',
    'branch_manager_router',
    'course_router',
    'category_router',
    'duration_router',
    'location_router',
    'state_router',
    'city_router',
    'branch_public_router',
    'public_branch_router',
    'public_branch_by_slug_router',
    'enrollment_router',
    'payment_router',
    'request_router',
    'event_router',
    'search_router',
    'email_router',
    'dashboard_router',
    'settings_router',
    'reports_router',
    'attendance_router',
    'message_router',
    'dropdown_settings_router',
    'upload_router',
    'cms_router',
    'lead_router'
]
