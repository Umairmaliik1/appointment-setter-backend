"""
Health check endpoints for monitoring and diagnostics.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, status

from app.core.async_redis import async_redis_client
from app.core.config import ENVIRONMENT, LIVEKIT_URL
from app.services.database import check_database_health

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


# Register both with and without trailing slash to handle Nginx redirects
# This ensures both /api/v1/health and /api/v1/health/ work
@router.get("", status_code=status.HTTP_200_OK)
@router.get("/", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, Any]:
    """
    Basic health check endpoint.
    Returns 200 OK if the service is running.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "AI Phone Scheduler API",
        "version": "1.0.0",
        "environment": ENVIRONMENT,
    }


@router.get("/detailed", status_code=status.HTTP_200_OK)
@router.get("/detailed/", status_code=status.HTTP_200_OK)
async def detailed_health_check() -> Dict[str, Any]:
    """
    Detailed health check that tests external dependencies.
    Returns health status of all components.
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "AI Phone Scheduler API",
        "version": "1.0.0",
        "environment": ENVIRONMENT,
        "components": {},
    }

    overall_healthy = True

    # Check Redis
    try:
        ping_result = await async_redis_client.ping()
        if ping_result:
            health_status["components"]["redis"] = {"status": "healthy", "message": "Connected"}
        else:
            health_status["components"]["redis"] = {"status": "unhealthy", "message": "Ping failed"}
            overall_healthy = False
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        health_status["components"]["redis"] = {"status": "unhealthy", "message": str(e)}
        overall_healthy = False

    # Check PostgreSQL
    try:
        database_healthy = check_database_health()
        if database_healthy:
            health_status["components"]["database"] = {"status": "healthy", "message": "Connected"}
        else:
            health_status["components"]["database"] = {"status": "unhealthy", "message": "Failed to connect to PostgreSQL"}
            overall_healthy = False
    except Exception as e:
        logger.error(f"PostgreSQL health check failed: {e}")
        health_status["components"]["database"] = {"status": "unhealthy", "message": str(e)}
        overall_healthy = False

    # Check LiveKit (basic URL validation)
    try:
        if LIVEKIT_URL:
            health_status["components"]["livekit"] = {"status": "configured", "message": "URL configured"}
        else:
            health_status["components"]["livekit"] = {"status": "not_configured", "message": "URL not set"}
    except Exception as e:
        logger.error(f"LiveKit health check failed: {e}")
        health_status["components"]["livekit"] = {"status": "error", "message": str(e)}

    # Set overall status
    health_status["status"] = "healthy" if overall_healthy else "degraded"

    return health_status


@router.get("/ready", status_code=status.HTTP_200_OK)
@router.get("/ready/", status_code=status.HTTP_200_OK)
async def readiness_check() -> Dict[str, Any]:
    """
    Readiness probe for Kubernetes/container orchestration.
    Returns 200 if the service is ready to accept traffic.
    """
    # Check critical dependencies
    ready = True
    checks = {}

    # Check Redis
    try:
        ping_result = await async_redis_client.ping()
        checks["redis"] = ping_result
        if not ping_result:
            ready = False
    except Exception:
        checks["redis"] = False
        ready = False

    # Check PostgreSQL
    try:
        database_healthy = check_database_health()
        checks["database"] = database_healthy
        if not database_healthy:
            ready = False
    except Exception:
        checks["database"] = False
        ready = False

    if ready:
        return {"status": "ready", "checks": checks}
    else:
        return {"status": "not_ready", "checks": checks}


@router.get("/live", status_code=status.HTTP_200_OK)
@router.get("/live/", status_code=status.HTTP_200_OK)
async def liveness_check() -> Dict[str, str]:
    """
    Liveness probe for Kubernetes/container orchestration.
    Returns 200 if the service is alive (even if dependencies are down).
    """
    return {"status": "alive"}
