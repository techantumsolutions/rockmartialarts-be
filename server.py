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
