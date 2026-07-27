"""
CORS configuration helpers shared by middleware and exception handlers.
"""

from typing import List, Optional, Tuple

from starlette.requests import Request

from app.core.config import CORS_ALLOW_ORIGINS, ENVIRONMENT

LOCAL_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
]


def get_cors_settings() -> Tuple[List[str], bool]:
    """
    Return (allowed_origins, allow_credentials).

    When CORS_ALLOW_ORIGINS is "*", development uses explicit localhost origins so
    credentialed cross-origin requests (withCredentials) work in the browser.
    """
    raw = (CORS_ALLOW_ORIGINS or "*").strip()
    if raw == "*":
        if ENVIRONMENT == "development":
            return LOCAL_DEV_ORIGINS, True
        return ["*"], False

    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if not origins:
        return LOCAL_DEV_ORIGINS, True
    if len(origins) == 1 and origins[0] == "*":
        return ["*"], False
    return origins, True


def resolve_allowed_origin(request: Optional[Request]) -> Optional[str]:
    """Pick the Access-Control-Allow-Origin value for a response."""
    origins, _ = get_cors_settings()
    if origins == ["*"]:
        return "*"

    request_origin = None
    if request is not None:
        request_origin = request.headers.get("origin")

    if request_origin and request_origin in origins:
        return request_origin

    return origins[0] if len(origins) == 1 else None
