"""
Tests for custom exceptions.
"""

from app.core.exceptions import AppException, AuthenticationError, LiveKitError, NotFoundError, TwilioError, ValidationError


def test_app_exception_basic():
    """Test basic AppException creation."""
    exc = AppException("Test error message")
    assert exc.message == "Test error message"
    assert exc.error_code == "APP_ERROR"
    assert exc.details == {}


def test_app_exception_with_details():
    """Test AppException with custom details."""
    details = {"user_id": "123", "action": "test"}
    exc = AppException("Error", error_code="CUSTOM_ERROR", details=details)

    assert exc.message == "Error"
    assert exc.error_code == "CUSTOM_ERROR"
    assert exc.details == details


def test_app_exception_to_dict():
    """Test converting AppException to dictionary."""
    exc = AppException("Test error", error_code="TEST_ERROR", details={"key": "value"})
    result = exc.to_dict()

    assert result["error"] == "TEST_ERROR"
    assert result["message"] == "Test error"
    assert result["details"] == {"key": "value"}


def test_validation_error():
    """Test ValidationError exception."""
    exc = ValidationError("Invalid email format", field="email")

    assert exc.message == "Invalid email format"
    assert exc.error_code == "VALIDATION_ERROR"
    assert exc.details["field"] == "email"


def test_not_found_error():
    """Test NotFoundError exception."""
    exc = NotFoundError("Tenant", "tenant-123")

    assert "tenant-123" in exc.message
    assert exc.error_code == "NOT_FOUND"
    assert exc.details["resource"] == "Tenant"
    assert exc.details["identifier"] == "tenant-123"


def test_twilio_error():
    """Test TwilioError exception."""
    exc = TwilioError("Failed to send SMS")

    assert "Twilio" in exc.message
    assert exc.error_code == "EXTERNAL_SERVICE_ERROR"
    assert exc.details["service"] == "Twilio"


def test_livekit_error():
    """Test LiveKitError exception."""
    exc = LiveKitError("Room creation failed")

    assert "LiveKit" in exc.message
    assert exc.error_code == "EXTERNAL_SERVICE_ERROR"


def test_authentication_error():
    """Test AuthenticationError exception."""
    exc = AuthenticationError("Invalid credentials")

    assert exc.message == "Invalid credentials"
    assert exc.error_code == "AUTHENTICATION_ERROR"
