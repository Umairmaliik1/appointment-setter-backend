"""
Tests for retry logic utilities.
"""

import pytest

from app.core.retry import RetryConfig, retry_async, retry_sync


def test_retry_sync_success_on_first_attempt():
    """Test that retry_sync works when function succeeds on first attempt."""
    call_count = {"count": 0}

    @retry_sync(max_attempts=3)
    def test_func():
        call_count["count"] += 1
        return "success"

    result = test_func()
    assert result == "success"
    assert call_count["count"] == 1


def test_retry_sync_success_on_retry():
    """Test that retry_sync retries and eventually succeeds."""
    call_count = {"count": 0}

    @retry_sync(max_attempts=3, delay=0.1)
    def test_func():
        call_count["count"] += 1
        if call_count["count"] < 3:
            raise ValueError("Temporary error")
        return "success"

    result = test_func()
    assert result == "success"
    assert call_count["count"] == 3


def test_retry_sync_max_attempts_exceeded():
    """Test that retry_sync raises exception after max attempts."""
    call_count = {"count": 0}

    @retry_sync(max_attempts=3, delay=0.1)
    def test_func():
        call_count["count"] += 1
        raise ValueError("Permanent error")

    with pytest.raises(ValueError):
        test_func()

    assert call_count["count"] == 3


@pytest.mark.asyncio
async def test_retry_async_success_on_first_attempt():
    """Test that retry_async works when function succeeds on first attempt."""
    call_count = {"count": 0}

    @retry_async(max_attempts=3)
    async def test_func():
        call_count["count"] += 1
        return "success"

    result = await test_func()
    assert result == "success"
    assert call_count["count"] == 1


@pytest.mark.asyncio
async def test_retry_async_success_on_retry():
    """Test that retry_async retries and eventually succeeds."""
    call_count = {"count": 0}

    @retry_async(max_attempts=3, delay=0.1)
    async def test_func():
        call_count["count"] += 1
        if call_count["count"] < 3:
            raise ValueError("Temporary error")
        return "success"

    result = await test_func()
    assert result == "success"
    assert call_count["count"] == 3


@pytest.mark.asyncio
async def test_retry_async_max_attempts_exceeded():
    """Test that retry_async raises exception after max attempts."""
    call_count = {"count": 0}

    @retry_async(max_attempts=3, delay=0.1)
    async def test_func():
        call_count["count"] += 1
        raise ValueError("Permanent error")

    with pytest.raises(ValueError):
        await test_func()

    assert call_count["count"] == 3


def test_retry_config_values():
    """Test that retry configuration values are set."""
    assert RetryConfig.TWILIO_MAX_ATTEMPTS > 0
    assert RetryConfig.LIVEKIT_MAX_ATTEMPTS > 0
    assert RetryConfig.DATABASE_MAX_ATTEMPTS > 0
