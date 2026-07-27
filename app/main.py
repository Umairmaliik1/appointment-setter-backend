"""
Main FastAPI application.
"""

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.api import api_router
from app.core.config import API_HOST, API_PORT, DEBUG, ENVIRONMENT, LOG_LEVEL
from app.core.cors import get_cors_settings
from app.core.env_validator import print_environment_summary, validate_environment_variables
from app.core.exceptions import (
    AppException,
    AuthenticationError,
    AuthorizationError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.core.middleware import TrailingSlashMiddleware
from app.core.utils import add_cors_headers

# Configure logging
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper()), format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Validate environment variables at import time
validate_environment_variables(strict=True)

# Suppress bcrypt version warning
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="passlib.handlers.bcrypt")
warnings.filterwarnings("ignore", message=".*bcrypt.*")

# Suppress passlib bcrypt logging warnings
logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.ERROR)
logging.getLogger("passlib").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting AI Phone Scheduler API server...")
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info(f"Debug mode: {DEBUG}")
    logger.info("CORS allowed origins: %s", ", ".join(cors_origins))
    logger.info("CORS allow credentials: %s", allow_credentials)

    # Print environment configuration summary
    print_environment_summary()

    # Startup
    # Warm up PostgreSQL connection to prevent slow first queries
    try:
        from app.services.postgres_health import postgres_health

        logger.info("Warming up PostgreSQL connection...")
        is_ready = await postgres_health.warm_up_connection()
        if is_ready:
            revision = await postgres_health.get_migration_revision()
            logger.info("PostgreSQL connection ready (alembic_revision=%s)", revision or "none")
        else:
            logger.warning("PostgreSQL warmup check returned unhealthy")
    except Exception as e:
        logger.warning(f"PostgreSQL warmup skipped: {e}")

    yield

    # Shutdown
    logger.info("Shutting down AI Phone Scheduler API server...")


# Create FastAPI app
# CRITICAL: redirect_slashes=False prevents FastAPI from automatically redirecting
# trailing slashes with 301 responses that lack CORS headers.
# Our TrailingSlashMiddleware handles normalization instead (rewrites paths, no redirects)
app = FastAPI(
    title="AI Phone Scheduler API",
    description="SaaS platform for AI-powered phone appointment scheduling",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if DEBUG else None,
    redoc_url="/redoc" if DEBUG else None,
    redirect_slashes=False,  # Disable automatic redirects - middleware handles normalization
)

# Note: Proxy headers are handled by uvicorn's --proxy-headers flag (see Dockerfile CMD)
# This ensures FastAPI correctly interprets HTTPS behind Nginx reverse proxy

# CRITICAL: Middleware order matters!
# FastAPI/Starlette applies middleware in REVERSE order (last added = outermost)
# So to make CORS the outermost (wrap all responses), add it LAST

# Add CORS middleware FIRST (becomes outermost, wraps all responses)
# FastAPI's CORSMiddleware fully handles OPTIONS preflight requests with all required headers
cors_origins, allow_credentials = get_cors_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, DELETE, OPTIONS, etc.)
    allow_headers=["*"],  # Allows all headers
    expose_headers=["*"],  # Expose all headers to browser
    max_age=3600,  # Cache preflight requests for 1 hour
)

# Add trailing slash normalization middleware AFTER CORS
# This normalizes paths at the ASGI level before routing
# Runs after CORS middleware (inner to CORS)
app.add_middleware(TrailingSlashMiddleware)

# Mount backend static assets under /api-static.
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/api-static", StaticFiles(directory=static_dir), name="api-static")

# Include API router
app.include_router(api_router)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "AI Phone Scheduler API", "version": "1.0.0"}


# Handle Starlette HTTPException (including 404 Not Found)
# FastAPI's 404 errors are raised as StarletteHTTPException
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions (including 404) with CORS headers."""
    response = JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "NOT_FOUND" if exc.status_code == 404 else "HTTP_EXCEPTION",
            "message": exc.detail if isinstance(exc.detail, str) else f"HTTP {exc.status_code} error",
            "details": {},
        },
    )
    return add_cors_headers(response, request)


# Custom exception handlers
# NOTE: CORS middleware should handle headers, but we ensure they're set here too
@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """Handle validation errors."""
    logger.warning(f"Validation error: {exc.message}", extra={"details": exc.details})
    response = JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=exc.to_dict())
    return add_cors_headers(response, request)


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    """Handle not found errors."""
    logger.warning(f"Resource not found: {exc.message}", extra={"details": exc.details})
    response = JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=exc.to_dict())
    return add_cors_headers(response, request)


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError):
    """Handle authentication errors."""
    logger.warning(f"Authentication failed: {exc.message}")
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED, content=exc.to_dict(), headers={"WWW-Authenticate": "Bearer"}
    )
    return add_cors_headers(response, request)


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(request: Request, exc: AuthorizationError):
    """Handle authorization errors."""
    logger.warning(f"Authorization failed: {exc.message}")
    response = JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=exc.to_dict())
    return add_cors_headers(response, request)


@app.exception_handler(ExternalServiceError)
async def external_service_error_handler(request: Request, exc: ExternalServiceError):
    """Handle external service errors."""
    logger.error(f"External service error: {exc.message}", extra={"details": exc.details})
    response = JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content=exc.to_dict())
    return add_cors_headers(response, request)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Handle generic application exceptions."""
    logger.error(f"Application error: {exc.message}", extra={"details": exc.details})
    response = JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=exc.to_dict())
    return add_cors_headers(response, request)


# Global exception handler for uncaught exceptions
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for uncaught exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    response = JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred" if not DEBUG else str(exc),
            "details": {},
        },
    )
    return add_cors_headers(response, request)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=DEBUG,
        log_level=LOG_LEVEL.lower(),
        proxy_headers=True,
        # Trust X-Forwarded-* from the reverse proxy so request.url reflects the
        # public https URL (required for Twilio webhook signature validation).
        forwarded_allow_ips="*",
    )
