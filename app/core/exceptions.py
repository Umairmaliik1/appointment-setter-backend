"""
Custom exception classes for standardized error handling.
"""

from typing import Any, Dict, Optional


class AppException(Exception):
    """Base exception class for application errors."""

    def __init__(self, message: str, error_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.error_code = error_code or "APP_ERROR"
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for API responses."""
        return {"error": self.error_code, "message": self.message, "details": self.details}


class ValidationError(AppException):
    """Exception raised for validation errors."""

    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_code="VALIDATION_ERROR", details={"field": field, **(details or {})})


class NotFoundError(AppException):
    """Exception raised when a resource is not found."""

    def __init__(self, resource: str, identifier: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=f"{resource} with identifier '{identifier}' not found",
            error_code="NOT_FOUND",
            details={"resource": resource, "identifier": identifier, **(details or {})},
        )


class DatabaseError(AppException):
    """Exception raised for database operation errors."""

    def __init__(self, message: str, operation: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_code="DATABASE_ERROR", details={"operation": operation, **(details or {})})


class ExternalServiceError(AppException):
    """Exception raised when external service calls fail."""

    def __init__(self, service: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=f"{service} error: {message}",
            error_code="EXTERNAL_SERVICE_ERROR",
            details={"service": service, **(details or {})},
        )


class TwilioError(ExternalServiceError):
    """Exception raised for Twilio API errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(service="Twilio", message=message, details=details)


class LiveKitError(ExternalServiceError):
    """Exception raised for LiveKit API errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(service="LiveKit", message=message, details=details)


class PostgreSQLError(ExternalServiceError):
    """Exception raised for PostgreSQL errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(service="PostgreSQL", message=message, details=details)


class AuthenticationError(AppException):
    """Exception raised for authentication failures."""

    def __init__(self, message: str = "Authentication failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_code="AUTHENTICATION_ERROR", details=details)


class AuthorizationError(AppException):
    """Exception raised for authorization failures."""

    def __init__(self, message: str = "Access denied", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_code="AUTHORIZATION_ERROR", details=details)


class ConfigurationError(AppException):
    """Exception raised for configuration errors."""

    def __init__(self, message: str, config_key: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message, error_code="CONFIGURATION_ERROR", details={"config_key": config_key, **(details or {})}
        )


class EncryptionError(AppException):
    """Exception raised for encryption/decryption errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_code="ENCRYPTION_ERROR", details=details)
