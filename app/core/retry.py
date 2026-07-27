"""
Retry logic utilities for external API calls.
Provides decorators and functions for retrying failed operations.
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Callable, Optional, Tuple, Type

# Configure logging
logger = logging.getLogger(__name__)


def retry_sync(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """
    Decorator for retrying synchronous functions.

    Args:
        max_attempts: Maximum number of attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
        backoff: Multiplier for delay after each retry (default: 2.0)
        exceptions: Tuple of exception types to catch (default: all exceptions)
        on_retry: Optional callback function called on each retry

    Example:
        @retry_sync(max_attempts=3, delay=1.0)
        def my_function():
            # Code that might fail
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(f"Function {func.__name__} failed after {max_attempts} attempts", exc_info=True)
                        raise

                    logger.warning(
                        f"Function {func.__name__} failed (attempt {attempt}/{max_attempts}), "
                        f"retrying in {current_delay}s: {str(e)}"
                    )

                    if on_retry:
                        try:
                            on_retry(attempt, e)
                        except Exception as callback_error:
                            logger.error(f"Retry callback failed: {callback_error}")

                    time.sleep(current_delay)
                    current_delay *= backoff

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def retry_async(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """
    Decorator for retrying asynchronous functions.

    Args:
        max_attempts: Maximum number of attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
        backoff: Multiplier for delay after each retry (default: 2.0)
        exceptions: Tuple of exception types to catch (default: all exceptions)
        on_retry: Optional callback function called on each retry

    Example:
        @retry_async(max_attempts=3, delay=1.0)
        async def my_async_function():
            # Code that might fail
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(f"Async function {func.__name__} failed after {max_attempts} attempts", exc_info=True)
                        raise

                    logger.warning(
                        f"Async function {func.__name__} failed (attempt {attempt}/{max_attempts}), "
                        f"retrying in {current_delay}s: {str(e)}"
                    )

                    if on_retry:
                        try:
                            if asyncio.iscoroutinefunction(on_retry):
                                await on_retry(attempt, e)
                            else:
                                on_retry(attempt, e)
                        except Exception as callback_error:
                            logger.error(f"Retry callback failed: {callback_error}")

                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


class RetryConfig:
    """Configuration for retry behavior."""

    # Twilio API retry configuration
    TWILIO_MAX_ATTEMPTS = 3
    TWILIO_DELAY = 1.0
    TWILIO_BACKOFF = 2.0

    # LiveKit API retry configuration
    LIVEKIT_MAX_ATTEMPTS = 3
    LIVEKIT_DELAY = 1.0
    LIVEKIT_BACKOFF = 2.0

    # Database retry configuration
    DATABASE_MAX_ATTEMPTS = 3
    DATABASE_DELAY = 0.5
    DATABASE_BACKOFF = 1.5



# Convenience decorators with pre-configured settings


def retry_twilio(func: Callable) -> Callable:
    """Retry decorator specifically for Twilio API calls."""
    from twilio.base.exceptions import TwilioException, TwilioRestException

    return (
        retry_async(
            max_attempts=RetryConfig.TWILIO_MAX_ATTEMPTS,
            delay=RetryConfig.TWILIO_DELAY,
            backoff=RetryConfig.TWILIO_BACKOFF,
            exceptions=(TwilioException, TwilioRestException),
        )(func)
        if asyncio.iscoroutinefunction(func)
        else retry_sync(
            max_attempts=RetryConfig.TWILIO_MAX_ATTEMPTS,
            delay=RetryConfig.TWILIO_DELAY,
            backoff=RetryConfig.TWILIO_BACKOFF,
            exceptions=(TwilioException, TwilioRestException),
        )(func)
    )


def retry_livekit(func: Callable) -> Callable:
    """Retry decorator specifically for LiveKit API calls."""
    # LiveKit exceptions
    return (
        retry_async(
            max_attempts=RetryConfig.LIVEKIT_MAX_ATTEMPTS,
            delay=RetryConfig.LIVEKIT_DELAY,
            backoff=RetryConfig.LIVEKIT_BACKOFF,
            exceptions=(Exception,),  # Catch all LiveKit errors
        )(func)
        if asyncio.iscoroutinefunction(func)
        else retry_sync(
            max_attempts=RetryConfig.LIVEKIT_MAX_ATTEMPTS,
            delay=RetryConfig.LIVEKIT_DELAY,
            backoff=RetryConfig.LIVEKIT_BACKOFF,
            exceptions=(Exception,),
        )(func)
    )


def retry_database(func: Callable) -> Callable:
    """Retry decorator specifically for database operations."""
    return (
        retry_async(
            max_attempts=RetryConfig.DATABASE_MAX_ATTEMPTS,
            delay=RetryConfig.DATABASE_DELAY,
            backoff=RetryConfig.DATABASE_BACKOFF,
            exceptions=(Exception,),  # Catch all database errors
        )(func)
        if asyncio.iscoroutinefunction(func)
        else retry_sync(
            max_attempts=RetryConfig.DATABASE_MAX_ATTEMPTS,
            delay=RetryConfig.DATABASE_DELAY,
            backoff=RetryConfig.DATABASE_BACKOFF,
            exceptions=(Exception,),
        )(func)
    )
