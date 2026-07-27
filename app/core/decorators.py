"""
Decorators for common patterns across the application.
"""

import functools
import logging
from typing import Any, Callable, Optional, TypeVar

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def handle_router_errors(
    not_found_message: Optional[str] = None,
    operation_name: Optional[str] = None,
):
    """
    Decorator to handle common router error patterns.

    Handles:
    - ValueError -> HTTP 400
    - HTTPException -> re-raise
    - None result -> HTTP 404 (if not_found_message provided)
    - Exception -> HTTP 500

    Args:
        not_found_message: Message to use when result is None (triggers 404)
        operation_name: Name of operation for error messages (defaults to function name)
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            try:
                result = await func(*args, **kwargs)

                # Check for None result if not_found_message is provided
                if not_found_message and result is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_message)

                return result

            except HTTPException:
                # Re-raise HTTPExceptions as-is
                raise
            except ValueError as e:
                # Convert ValueError to HTTP 400
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
            except Exception as e:
                # Log unexpected errors
                logger.error(f"Error in {op_name}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to {op_name.replace('_', ' ')}: {str(e)}",
                )

        return wrapper

    return decorator
