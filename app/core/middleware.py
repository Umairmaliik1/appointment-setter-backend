"""
Middleware for request processing and normalization.
Production-grade trailing slash handling for FastAPI.
"""

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class TrailingSlashMiddleware:
    """
    ASGI Middleware to normalize trailing slashes in API routes.

    Production-grade solution that rewrites paths at the ASGI level to remove
    trailing slashes for API routes. This prevents CORS issues that occur with redirects.

    Strategy:
    - Intercept requests at the ASGI scope level (before Request object creation)
    - For API routes (/api/*), strip trailing slashes
    - Preserve query parameters and all other request data
    - Rewrite the path in-place (no redirects, no CORS issues)
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Only process HTTP requests (including OPTIONS preflight)
        if scope["type"] == "http":
            path = scope["path"]
            original_path = path
            method = scope.get("method", "")

            # Normalize trailing slashes for API routes
            # This must happen BEFORE CORS middleware processes the request
            # CRITICAL: Handle OPTIONS preflight requests correctly
            # Only normalize if path starts with /api/ and has trailing slash
            # Skip root paths and paths that are already normalized
            if path.startswith("/api/") and path != "/api/v1/" and path != "/":
                # Strip trailing slash for API routes (but not root)
                if path.endswith("/") and len(path) > 1:
                    normalized_path = path.rstrip("/")

                    # Prevent infinite loops - only rewrite if actually different
                    if normalized_path != path:
                        # Rewrite the path in the scope before it reaches routing
                        scope["path"] = normalized_path

                        # Preserve query string if present
                        query_string = scope.get("query_string", b"")
                        if query_string:
                            query_str = query_string.decode("utf-8")
                            scope["raw_path"] = f"{normalized_path}?{query_str}".encode("utf-8")
                        else:
                            scope["raw_path"] = normalized_path.encode("utf-8")

                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug(f"Normalized API path: {method} {original_path} -> {normalized_path}")

        # Continue with the request (scope is modified if needed)
        # CORS middleware (added after this) will process OPTIONS and add headers
        await self.app(scope, receive, send)
