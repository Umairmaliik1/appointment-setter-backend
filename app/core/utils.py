"""
Core utility functions for common operations.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.core.cors import get_cors_settings, resolve_allowed_origin


def get_current_timestamp() -> str:
    """
    Get current UTC timestamp in ISO format.

    Returns:
        ISO format timestamp string (e.g., "2024-01-01T12:00:00+00:00")
    """
    return datetime.now(timezone.utc).isoformat()


def add_cors_headers(response: JSONResponse, request: Optional[Request] = None) -> JSONResponse:
    """
    Add CORS headers to a JSONResponse.

    Note: CORS middleware should handle this automatically, but this ensures
    headers are present in exception handlers where middleware might not apply.

    Args:
        response: JSONResponse to add headers to
        request: Optional request used to echo the allowed Origin

    Returns:
        Response with CORS headers added
    """
    allowed_origin = resolve_allowed_origin(request)
    if allowed_origin:
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    _, allow_credentials = get_cors_settings()
    if allow_credentials:
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def add_timestamps(data: Dict[str, Any], include_created: bool = True, include_updated: bool = True) -> Dict[str, Any]:
    """
    Add created_at and/or updated_at timestamps to a dictionary.

    Args:
        data: Dictionary to add timestamps to
        include_created: Whether to add created_at timestamp
        include_updated: Whether to add updated_at timestamp

    Returns:
        Dictionary with timestamps added
    """
    timestamp = get_current_timestamp()
    if include_created:
        data["created_at"] = timestamp
    if include_updated:
        data["updated_at"] = timestamp
    return data


def add_updated_timestamp(update_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add updated_at timestamp to update data dictionary.

    Args:
        update_data: Dictionary to add updated_at to

    Returns:
        Dictionary with updated_at timestamp added
    """
    update_data["updated_at"] = get_current_timestamp()
    return update_data
