from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path
import os
import ssl
import logging
from contextlib import asynccontextmanager
import asyncio

# Import routes
from routes.reg_checkout_routes import router as reg_checkout_router
from routes.student_performance_routes import router as student_performance_router
from routes import (
    auth_router,
    user_router,
    coach_router,
    branch_router,
    branch_manager_router,
    course_router,
    category_router,
    duration_router,
    location_router,
    state_router,
    city_router,
    branch_public_router,
    public_branch_router,
    public_branch_by_slug_router,
    enrollment_router,
    payment_router,
    request_router,
    event_router,
    search_router,
    email_router,
    dashboard_router,
    settings_router,
    reports_router,
    attendance_router,
    message_router,
    dropdown_settings_router,
    lead_router,
)
from routes.superadmin_routes import router as superadmin_router
from routes.branches_with_courses_routes import router as branches_with_courses_router
from routes.branch_course_routes import router as branch_course_router
from routes.upload_routes import router as upload_router
from routes.cms_routes import router as cms_router
from routes.camp_registration_routes import router as camp_registration_router
from routes.homepage_content_routes import router as homepage_content_router
from routes.achievement_routes import router as achievement_router
from routes.student_testimonial_routes import router as student_testimonial_router
from routes.student_showcase_achievement_routes import router as showcase_achievement_router
from routes.onboarding_routes import router as onboarding_router
from routes.cart_routes import router as cart_router
from routes.discount_rule_routes import router as discount_rule_router
from routes.invoice_routes import router as invoice_router
from routes.billing_routes import router as billing_router
from routes.public_student_id_routes import router as public_student_id_router
from routes.biometric_device_routes import router as biometric_device_router
from routes.kpi_routes import router as kpi_router
from routes.kpi_assessment_routes import (
    periods_router as kpi_periods_router,
    assessments_router as kpi_assessments_router,
)
from routes.kpi_rating_routes import router as kpi_ratings_router
from routes.kpi_ranking_routes import router as kpi_rankings_router
from routes.course_syllabus_routes import router as course_syllabus_router
from routes.training_request_routes import router as training_request_router
from routes.demo_schedule_routes import router as demo_schedule_router
from routes.demo_session_routes import router as demo_session_router
from routes.demo_booking_admin_routes import router as demo_booking_admin_router
from routes.coach_subscription_routes import router as coach_subscription_router
from routes.callback_routes import router as callback_router
from routes.lead_coach_assignment_routes import (
    lead_assignment_router,
    coach_assignment_router,
)
from routes.coach_session_booking_routes import (
    router as coach_session_booking_router,
    lead_session_router,
    coach_me_session_router,
)
from routes.learning_course_routes import router as learning_course_router
from routes.learning_hierarchy_routes import hierarchy_router as learning_hierarchy_router
from routes.learning_subscription_routes import router as learning_subscription_router
from routes.learning_access_routes import router as learning_access_router
from routes.learning_progress_routes import router as learning_progress_router
from routes.academy_event_routes import router as academy_event_router
from routes.academy_event_registration_routes import (
    router as academy_event_registration_router,
)
from routes.academy_event_registration_admin_routes import (
    router as academy_event_registration_admin_router,
)
from routes.academy_event_reminder_routes import (
    router as academy_event_reminder_router,
)
from routes.notification_template_routes import (
    router as notification_template_router,
)
from routes.notification_send_routes import (
    router as notification_send_router,
)
from routes.student_promotion_routes import (
    router as student_promotion_router,
)
from routes.champion_routes import (
    router as champion_router,
)
from routes.collaboration_partner_routes import (
    router as collaboration_partner_router,
)

# Import database utility
from utils.database import db
from controllers.student_performance_controller import ensure_student_performance_indexes

# Reconciliation loop (optional)
from utils.razorpay_reconciliation import ensure_collections_indexes, reconcile_payments_batch

# Load environment variables
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')


def _configure_dns_resolver() -> None:
    """Use public DNS when /etc/resolv.conf is missing (common on minimal VPS images)."""
    try:
        import dns.resolver

        class _PublicResolver(dns.resolver.Resolver):
            def __init__(self, filename="/etc/resolv.conf", configure=True):
                dns.resolver.BaseResolver.__init__(self)
                self.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]

        dns.resolver.Resolver = _PublicResolver  # type: ignore[misc,assignment]
        dns.resolver.default_resolver = _PublicResolver()
    except Exception:
        pass


_mongo_uri = os.getenv("MONGO_URL") or os.getenv("MONGO_URI") or ""
if "mongodb+srv://" in _mongo_uri:
    _configure_dns_resolver()


async def _payments_reconciliation_loop(mongo_db):
    """
    Lightweight reconciliation loop.
    Enabled via PAYMENT_RECONCILIATION_ENABLED=true.
    """
    enabled = (os.getenv("PAYMENT_RECONCILIATION_ENABLED") or "").strip().lower() in ("1", "true", "yes")
    if not enabled:
        return
    interval_sec = int(os.getenv("PAYMENT_RECONCILIATION_INTERVAL_SEC", "600") or "600")
    interval_sec = max(60, interval_sec)
    lookback_days = int(os.getenv("PAYMENT_RECONCILIATION_LOOKBACK_DAYS", "7") or "7")
    pending_stuck_min = int(os.getenv("PAYMENT_RECONCILIATION_STUCK_MIN", "5") or "5")
    limit = int(os.getenv("PAYMENT_RECONCILIATION_LIMIT", "200") or "200")

    await ensure_collections_indexes(mongo_db)

    # Continuous reconcile with sleep; exceptions are caught to avoid crashing the app.
    while True:
        try:
            await reconcile_payments_batch(
                mongo_db,
                actor="scheduled_job",
                reason="scheduled_reconciliation",
                lookback_days=lookback_days,
                pending_stuck_minutes=pending_stuck_min,
                limit=limit,
            )
        except Exception:
            logging.exception("Scheduled payment reconciliation tick failed")
        await asyncio.sleep(interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    mongo_url = (
        os.getenv("MONGO_URL")
        or os.getenv("MONGO_URI")
        or "mongodb://localhost:27017"
    )
    app.mongodb_client = AsyncIOMotorClient(mongo_url, tlsInsecure=True)
    db_name = os.getenv("DB_NAME", "marshalats")
    app.mongodb = app.mongodb_client.get_database(db_name)
    
    # Initialize the database connection in utils
    from utils.database import init_db
    init_db(app.mongodb)

    from utils.cart_helpers import ensure_cart_indexes
    await ensure_cart_indexes(app.mongodb)
    try:
        from utils.discount_helpers import ensure_discount_rule_indexes
        await ensure_discount_rule_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure discount rule indexes")
    try:
        from utils.cart_fulfillment import ensure_cart_checkout_indexes
        await ensure_cart_checkout_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure cart checkout indexes")
    try:
        from utils.invoice_service import ensure_invoice_indexes
        await ensure_invoice_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure invoice indexes")
    try:
        from utils.invoice_whatsapp_service import ensure_invoice_whatsapp_indexes
        await ensure_invoice_whatsapp_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure invoice WhatsApp delivery indexes")
    try:
        from utils.student_status_service import ensure_student_status_indexes
        await ensure_student_status_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure student status history indexes")
    try:
        from utils.billing_cycle_service import ensure_billing_cycle_indexes
        await ensure_billing_cycle_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure billing cycle indexes")

    # Additive indexes (safe to re-run).
    try:
        from utils.family_accounts import ensure_account_indexes
        await ensure_account_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure family account indexes")
    try:
        await app.mongodb.camp_registrations.create_index(
            "registration_code", unique=True, sparse=True
        )
    except Exception:
        logging.exception("Failed to create camp_registrations.registration_code unique index")
    try:
        # ESSL mapping: enforce unique employee code when set.
        await app.mongodb.users.create_index("essl_user_id", unique=True, sparse=True)
    except Exception:
        logging.exception("Failed to create users.essl_user_id unique index")
    try:
        # Existing admin UI uses `biometric_id` field; use it as the primary ESSL mapping key.
        await app.mongodb.users.create_index("biometric_id", unique=True, sparse=True)
    except Exception:
        logging.exception("Failed to create users.biometric_id unique index")

    # One-time safe normalization: sparse unique indexes still index explicit BSON null / empty string.
    try:
        await app.mongodb.users.update_many(
            {"essl_user_id": {"$type": 10}},
            {"$unset": {"essl_user_id": ""}},
        )
        await app.mongodb.users.update_many(
            {"essl_user_id": ""},
            {"$unset": {"essl_user_id": ""}},
        )
        await app.mongodb.users.update_many(
            {"biometric_id": {"$type": 10}},
            {"$unset": {"biometric_id": ""}},
        )
        await app.mongodb.users.update_many(
            {"biometric_id": ""},
            {"$unset": {"biometric_id": ""}},
        )
    except Exception:
        logging.exception("Failed to normalize null/empty biometric mapping fields on users")

    try:
        await ensure_student_performance_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure student performance dashboard indexes")

    try:
        from utils.geography import ensure_geography_indexes
        await ensure_geography_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure geography indexes")

    try:
        from utils.branch_geography import ensure_branch_indexes
        await ensure_branch_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure branch indexes")

    try:
        from utils.course_hierarchy import ensure_course_hierarchy_indexes
        await ensure_course_hierarchy_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure course hierarchy indexes")

    try:
        from utils.branch_courses import ensure_branch_course_indexes
        await ensure_branch_course_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure branch course indexes")

    try:
        from utils.kpi_service import ensure_kpi_indexes
        await ensure_kpi_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure KPI definition indexes")

    try:
        from utils.kpi_assessment_service import ensure_kpi_assessment_indexes
        await ensure_kpi_assessment_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure KPI assessment indexes")

    try:
        from utils.kpi_rating_service import ensure_kpi_rating_indexes
        await ensure_kpi_rating_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure KPI rating indexes")

    try:
        from utils.kpi_ranking_service import ensure_kpi_ranking_indexes
        await ensure_kpi_ranking_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure KPI ranking indexes")

    try:
        from utils.course_syllabus_service import ensure_course_syllabus_indexes
        await ensure_course_syllabus_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure course syllabus indexes")

    try:
        from utils.training_request_service import ensure_training_request_indexes
        await ensure_training_request_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure training request indexes")

    try:
        from utils.demo_schedule_service import ensure_demo_schedule_indexes
        await ensure_demo_schedule_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure demo schedule indexes")

    try:
        from utils.demo_booking_service import ensure_demo_booking_indexes
        await ensure_demo_booking_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure demo booking indexes")

    try:
        from utils.coach_registration_service import ensure_coach_registration_indexes
        await ensure_coach_registration_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure coach registration indexes")

    try:
        from utils.coach_approval_service import ensure_coach_approval_indexes
        await ensure_coach_approval_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure coach approval indexes")

    try:
        from utils.coach_availability_service import ensure_coach_availability_indexes
        await ensure_coach_availability_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure coach availability indexes")

    try:
        from utils.coach_subscription_service import (
            ensure_coach_subscription_indexes,
            seed_default_plan_if_empty,
        )
        await ensure_coach_subscription_indexes(app.mongodb)
        await seed_default_plan_if_empty(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure coach subscription indexes")

    try:
        from utils.lead_service import ensure_lead_indexes
        await ensure_lead_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure lead indexes")

    try:
        from utils.callback_service import ensure_callback_indexes
        await ensure_callback_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure callback indexes")

    try:
        from utils.lead_coach_assignment_service import (
            ensure_lead_coach_assignment_indexes,
        )
        await ensure_lead_coach_assignment_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure lead coach assignment indexes")

    try:
        from utils.coach_session_booking_service import (
            ensure_coach_session_booking_indexes,
        )
        await ensure_coach_session_booking_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure coach session booking indexes")

    try:
        from utils.learning_course_service import ensure_learning_course_indexes
        await ensure_learning_course_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure learning course indexes")

    try:
        from utils.learning_hierarchy_service import ensure_learning_hierarchy_indexes
        await ensure_learning_hierarchy_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure learning hierarchy indexes")

    try:
        from utils.learning_subscription_service import (
            ensure_learning_subscription_indexes,
        )
        await ensure_learning_subscription_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure learning subscription indexes")

    try:
        from utils.learning_progress_service import ensure_learning_progress_indexes
        await ensure_learning_progress_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure learning progress indexes")

    try:
        from utils.academy_event_service import ensure_academy_event_indexes
        await ensure_academy_event_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure academy event indexes")

    try:
        from utils.academy_event_registration_service import (
            ensure_academy_event_registration_indexes,
        )
        await ensure_academy_event_registration_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure academy event registration indexes")

    try:
        from utils.academy_event_registration_otp_service import (
            ensure_academy_event_registration_otp_indexes,
        )
        await ensure_academy_event_registration_otp_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure academy event registration OTP indexes")

    try:
        from utils.academy_event_reminder_service import (
            ensure_academy_event_reminder_indexes,
        )
        await ensure_academy_event_reminder_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure academy event reminder indexes")

    try:
        from utils.notification_template_service import (
            ensure_notification_template_indexes,
        )
        await ensure_notification_template_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure notification template indexes")

    try:
        from utils.notification_send_service import ensure_notification_send_indexes
        await ensure_notification_send_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure notification send indexes")

    try:
        from utils.student_promotion_service import ensure_student_promotion_indexes
        from utils.student_promotion_student_service import (
            ensure_promotion_event_indexes,
        )
        await ensure_student_promotion_indexes(app.mongodb)
        await ensure_promotion_event_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure student promotion indexes")

    try:
        from utils.champion_service import ensure_champion_indexes
        from utils.champion_achievement_service import (
            ensure_champion_achievement_indexes,
        )
        await ensure_champion_indexes(app.mongodb)
        await ensure_champion_achievement_indexes(app.mongodb)
    except Exception:
        logging.exception("Failed to ensure champion indexes")

    # Start scheduled reconciliation (additive; safe when disabled)
    reconcile_task = asyncio.create_task(_payments_reconciliation_loop(app.mongodb))
    
    yield
    
    # Shutdown
    try:
        reconcile_task.cancel()
    except Exception:
        pass
    app.mongodb_client.close()

# Create FastAPI app
app = FastAPI(
    title="Learning Management System API",
    description="A comprehensive LMS API for managing students, courses, and educational content",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
# Get CORS origins from environment or use default
cors_origins = os.getenv("CORS_ORIGINS", "*")
if cors_origins == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [origin.strip() for origin in cors_origins.split(",")]

# Add specific origins for your deployment
allowed_origins_list = [
    "http://localhost:3022",
    "http://127.0.0.1:3022",
    "https://rockmartialartsacademy.com",
    "http://rockmartialartsacademy.com",
    "*"  # Allow all origins as fallback
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"]
)


@app.middleware("http")
async def disable_http_caching(request: Request, call_next):
    """Always serve fresh API data; prevent stale browser/proxy caches."""
    response = await call_next(request)

    path = request.url.path or ""
    # Apply strict no-store to all API responses and health checks used by dashboards.
    if path.startswith("/api/") or path in {"/health"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Surrogate-Control"] = "no-store"
        # Helps some proxies/CDNs avoid coalescing stale variants.
        response.headers["Vary"] = "Authorization, Cookie, Origin"
    return response

# Include routers
app.include_router(superadmin_router, prefix="/api/superadmin", tags=["Super Admin"])
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(user_router, prefix="/api/users", tags=["Users"])
app.include_router(coach_router, prefix="/api/coaches", tags=["Coaches"])
app.include_router(branch_public_router, prefix="/api/branches", tags=["Public Branches"])
app.include_router(branch_router, prefix="/api/branches", tags=["Branches"])
app.include_router(public_branch_router, prefix="/api/public-branch", tags=["Public Branch by ID"])
app.include_router(public_branch_by_slug_router, prefix="/api/public-branch-by-slug", tags=["Public Branch by Slug"])
app.include_router(branch_manager_router, prefix="/api/branch-managers", tags=["Branch Managers"])
app.include_router(course_router, prefix="/api/courses", tags=["Courses"])
app.include_router(category_router, prefix="/api/categories", tags=["Categories"])
app.include_router(duration_router, prefix="/api/durations", tags=["Durations"])
app.include_router(location_router, prefix="/api/locations", tags=["Locations"])
app.include_router(state_router, prefix="/api/states", tags=["States"])
app.include_router(city_router, prefix="/api/cities", tags=["Cities"])
app.include_router(enrollment_router, prefix="/api/enrollments", tags=["Enrollments"])
app.include_router(payment_router, prefix="/api/payments", tags=["Payments"])
app.include_router(request_router, prefix="/api/requests", tags=["Requests"])
app.include_router(event_router, prefix="/api/events", tags=["Events"])
app.include_router(search_router, prefix="/api/search", tags=["Search"])
app.include_router(email_router, prefix="/api/email", tags=["Email"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])
app.include_router(lead_router, prefix="/api/leads", tags=["Leads"])
app.include_router(lead_assignment_router, prefix="/api/leads", tags=["Lead Coach Assignment"])
app.include_router(lead_session_router, prefix="/api/leads", tags=["Lead Coach Sessions"])
app.include_router(
    coach_assignment_router,
    prefix="/api/lead-coach-assignments",
    tags=["Lead Coach Assignment"],
)
app.include_router(
    coach_session_booking_router,
    prefix="/api/coach-session-bookings",
    tags=["Coach Session Bookings"],
)
app.include_router(
    coach_me_session_router, prefix="/api/coaches", tags=["Coach Session Bookings"]
)
app.include_router(
    learning_course_router,
    prefix="/api/learning-courses",
    tags=["Online Learning"],
)
app.include_router(
    learning_hierarchy_router,
    prefix="/api/learning-courses",
    tags=["Online Learning Hierarchy"],
)
app.include_router(
    learning_subscription_router,
    prefix="/api/learning-subscriptions",
    tags=["Online Learning Subscriptions"],
)
app.include_router(
    learning_access_router,
    prefix="/api/learning-access",
    tags=["Online Learning Access"],
)
app.include_router(
    learning_progress_router,
    prefix="/api/learning-progress",
    tags=["Online Learning Progress"],
)
app.include_router(
    academy_event_router,
    prefix="/api/academy-events",
    tags=["Academy Events"],
)
app.include_router(
    academy_event_registration_router,
    prefix="/api/academy-event-registrations",
    tags=["Academy Event Registrations"],
)
app.include_router(
    academy_event_registration_admin_router,
    prefix="/api/academy-event-registration-admin",
    tags=["Academy Event Registration Admin"],
)
app.include_router(
    academy_event_reminder_router,
    prefix="/api/academy-events",
    tags=["Academy Event Reminders"],
)
app.include_router(
    notification_template_router,
    prefix="/api/notification-templates",
    tags=["Notification Templates"],
)
app.include_router(
    notification_send_router,
    prefix="/api/notifications",
    tags=["Notification Sending"],
)
app.include_router(
    student_promotion_router,
    prefix="/api/student-promotions",
    tags=["Student Promotions"],
)
app.include_router(
    champion_router,
    prefix="/api/champions",
    tags=["Champions"],
)
app.include_router(
    collaboration_partner_router,
    prefix="/api/collaboration-partners",
    tags=["Collaboration Partners"],
)
app.include_router(callback_router, prefix="/api/callbacks", tags=["Callbacks"])
app.include_router(dropdown_settings_router, prefix="/api/dropdown-settings", tags=["Master Data"])
app.include_router(message_router, prefix="/api/messages", tags=["Messages"])
app.include_router(reports_router, prefix="/api/reports", tags=["Reports"])
app.include_router(attendance_router, prefix="/api/attendance", tags=["Attendance"])
app.include_router(branches_with_courses_router, prefix="/api", tags=["Branches with Courses"])
app.include_router(branch_course_router, prefix="/api/branch-courses", tags=["Branch Courses"])
app.include_router(upload_router, prefix="/api/uploads", tags=["Uploads"])
app.include_router(cms_router, prefix="/api/cms", tags=["CMS"])
app.include_router(camp_registration_router, prefix="/api/camp-registrations", tags=["Camp Registrations"])
app.include_router(homepage_content_router, prefix="/api/homepage", tags=["Homepage Content"])
app.include_router(achievement_router, prefix="/api/achievements", tags=["Achievements"])
app.include_router(student_testimonial_router, prefix="/api/testimonials", tags=["Marketing Testimonials"])
app.include_router(
    showcase_achievement_router, prefix="/api/showcase-achievements", tags=["Marketing Achievements"]
)
app.include_router(onboarding_router, prefix="/api/onboarding", tags=["Onboarding"])
app.include_router(cart_router, prefix="/api/carts", tags=["Enrollment Cart"])
app.include_router(discount_rule_router, prefix="/api/discount-rules", tags=["Discount Rules"])
app.include_router(invoice_router, prefix="/api/invoices", tags=["Invoices"])
app.include_router(billing_router, prefix="/api/billing-cycles", tags=["Billing Cycles"])
app.include_router(
    public_student_id_router, prefix="/api/public", tags=["Public Student ID"]
)
app.include_router(
    biometric_device_router, prefix="/api/biometric-devices", tags=["Biometric Devices"]
)
app.include_router(kpi_router, prefix="/api/kpi-definitions", tags=["KPI Definitions"])
app.include_router(
    kpi_periods_router, prefix="/api/kpi-assessment-periods", tags=["KPI Assessment Periods"]
)
app.include_router(
    kpi_assessments_router, prefix="/api/kpi-assessments", tags=["KPI Assessments"]
)
app.include_router(kpi_ratings_router, prefix="/api/kpi-ratings", tags=["KPI Ratings"])
app.include_router(kpi_rankings_router, prefix="/api/kpi-rankings", tags=["KPI Rankings"])
app.include_router(
    course_syllabus_router, prefix="/api/course-syllabi", tags=["Course Syllabi"]
)
app.include_router(
    training_request_router, prefix="/api/training-requests", tags=["Training Requests"]
)
app.include_router(
    demo_schedule_router, prefix="/api/demo-schedules", tags=["Demo Schedules"]
)
app.include_router(
    demo_session_router, prefix="/api/demo-sessions", tags=["Demo Sessions"]
)
app.include_router(
    demo_booking_admin_router, prefix="/api/demo-bookings", tags=["Demo Bookings Admin"]
)
app.include_router(
    coach_subscription_router,
    prefix="/api/coach-subscriptions",
    tags=["Coach Subscriptions"],
)
app.include_router(reg_checkout_router, prefix="/api/reg-checkout", tags=["Registration Checkout"])
app.include_router(student_performance_router, prefix="/api/student", tags=["Student Performance"])

@app.get("/")
async def root():
    return {"message": "Learning Management System API is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": "2024-01-01T00:00:00Z", "version": "updated-coach-auth"}

# Add explicit OPTIONS handler for CORS preflight requests
@app.options("/{full_path:path}")
async def options_handler():
    return {"message": "OK"}

@app.get("/test-coach-auth")
async def test_coach_auth():
    return {"message": "Coach authorization logic has been updated", "timestamp": "2025-09-20"}


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Map bcrypt password-length errors to 401 only on auth login (not OTP/reg-checkout)."""
    msg = str(exc)
    path = request.url.path or ""
    is_login = "/auth/login" in path or path.endswith("/login")
    if (
        is_login
        and "72 bytes" in msg
        and "password" in msg.lower()
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid email or password", "message": "Invalid email or password"},
        )
    return JSONResponse(status_code=400, content={"detail": msg, "message": msg})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return JSON with actual error for 500s so the frontend can show it."""
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logging.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "message": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
