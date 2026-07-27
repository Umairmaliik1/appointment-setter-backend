"""
Environment variable validator to ensure all required configurations are set.
"""

import logging
import os
import sys
from typing import Dict, List, Tuple

from app.core.config import (
    DATABASE_URL,
    ENVIRONMENT,
    GOOGLE_API_KEY,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    REDIS_URL,
    SECRET_KEY,
    email_settings,
)

# Configure logging
logger = logging.getLogger(__name__)

# Define required and optional environment variables
REQUIRED_ENV_VARS = {
    # Critical for application functionality
    "SECRET_KEY": SECRET_KEY,
    "DATABASE_URL": DATABASE_URL,
    "REDIS_URL": REDIS_URL,
    "LIVEKIT_API_KEY": LIVEKIT_API_KEY,
    "LIVEKIT_API_SECRET": LIVEKIT_API_SECRET,
    "LIVEKIT_URL": LIVEKIT_URL,
}

OPTIONAL_ENV_VARS = {
    # Nice to have but not critical for basic operation
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
    "MAIL_USERNAME": email_settings.MAIL_USERNAME,
    "MAIL_PASSWORD": email_settings.MAIL_PASSWORD,
    "MAIL_SERVER": email_settings.MAIL_SERVER,
    "CORS_ALLOW_ORIGINS": os.environ.get("CORS_ALLOW_ORIGINS", ""),
    "ACCESS_TOKEN_COOKIE_NAME": os.environ.get("ACCESS_TOKEN_COOKIE_NAME", ""),
    "REFRESH_TOKEN_COOKIE_NAME": os.environ.get("REFRESH_TOKEN_COOKIE_NAME", ""),
    "AUTH_COOKIE_DOMAIN": os.environ.get("AUTH_COOKIE_DOMAIN", ""),
    "AUTH_COOKIE_SECURE": os.environ.get("AUTH_COOKIE_SECURE", ""),
    "AUTH_COOKIE_SAMESITE": os.environ.get("AUTH_COOKIE_SAMESITE", ""),
}


def validate_environment_variables(strict: bool = True) -> Tuple[bool, List[str]]:
    """
    Validate that all required environment variables are set.

    Args:
        strict: If True, fail on missing required vars. If False, just warn.

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    # Skip validation in test environment
    if ENVIRONMENT == "test" or os.environ.get("PYTEST_CURRENT_TEST"):
        return True, []

    errors = []
    warnings = []

    # Check required variables
    for var_name, var_value in REQUIRED_ENV_VARS.items():
        if not var_value or var_value.strip() == "":
            errors.append(f"Required environment variable '{var_name}' is not set or empty")

    # Check optional variables
    for var_name, var_value in OPTIONAL_ENV_VARS.items():
        if not var_value or var_value.strip() == "":
            warnings.append(f"Optional environment variable '{var_name}' is not set")

    # Log results
    if errors:
        logger.error("=" * 80)
        logger.error("ENVIRONMENT VARIABLE VALIDATION FAILED")
        logger.error("=" * 80)
        for error in errors:
            logger.error(f"❌ {error}")
        logger.error("=" * 80)
        logger.error("Please set the missing environment variables in your .env file")
        logger.error("=" * 80)

    if warnings:
        logger.warning("=" * 80)
        logger.warning("OPTIONAL ENVIRONMENT VARIABLES MISSING")
        logger.warning("=" * 80)
        for warning in warnings:
            logger.warning(f"⚠️  {warning}")
        logger.warning("=" * 80)
        logger.warning("Application will run with limited functionality")
        logger.warning("=" * 80)

    if not errors and not warnings:
        logger.info("=" * 80)
        logger.info("✅ All environment variables are properly configured")
        logger.info("=" * 80)

    is_valid = len(errors) == 0

    if not is_valid and strict:
        logger.critical("Application cannot start with missing required environment variables")
        logger.critical("Exiting...")
        sys.exit(1)

    return is_valid, errors + warnings


def get_environment_info() -> Dict[str, any]:
    """Get information about the current environment configuration."""
    return {
        "environment": ENVIRONMENT,
        "database_configured": bool(DATABASE_URL),
        "redis_configured": bool(REDIS_URL),
        "livekit_configured": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET and LIVEKIT_URL),
        "google_ai_configured": bool(GOOGLE_API_KEY),
        "email_configured": bool(email_settings.MAIL_USERNAME and email_settings.MAIL_SERVER),
        "secret_key_configured": bool(SECRET_KEY),
    }


def print_environment_summary():
    """Print a summary of environment configuration."""
    info = get_environment_info()

    logger.info("=" * 80)
    logger.info("ENVIRONMENT CONFIGURATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Environment: {info['environment']}")
    logger.info(f"PostgreSQL: {'✅' if info['database_configured'] else '❌'}")
    logger.info(f"Redis: {'✅' if info['redis_configured'] else '❌'}")
    logger.info(f"LiveKit: {'✅' if info['livekit_configured'] else '❌'}")
    logger.info(f"Google AI (Gemini Live): {'✅' if info['google_ai_configured'] else '❌'}")
    logger.info(f"Email Service: {'✅' if info['email_configured'] else '❌'}")
    logger.info(f"Secret Key: {'✅' if info['secret_key_configured'] else '❌'}")
    logger.info("=" * 80)
