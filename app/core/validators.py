"""
Shared validation functions for schemas and services.
"""

# Service type constants
VALID_SERVICE_TYPES = ["Home Services", "Plumbing", "Electrician", "Painter", "Carpenter", "Maids", "Healthcare", "Scholarly Help"]


def validate_service_type(service_type: str) -> str:
    """
    Validate service type.

    Args:
        service_type: Service type to validate

    Returns:
        Validated service type

    Raises:
        ValueError: If service type is not valid
    """
    if service_type not in VALID_SERVICE_TYPES:
        raise ValueError(f"Service type must be one of: {VALID_SERVICE_TYPES}")
    return service_type


def validate_password(password: str, field_name: str = "Password") -> str:
    """
    Validate password strength.

    Args:
        password: Password to validate
        field_name: Field name for error messages (default: "Password")

    Returns:
        Validated password

    Raises:
        ValueError: If password doesn't meet requirements
    """
    if len(password) < 8:
        raise ValueError(f"{field_name} must be at least 8 characters long")
    if len(password) > 72:
        raise ValueError(f"{field_name} cannot be longer than 72 characters")
    if not any(c.isupper() for c in password):
        raise ValueError(f"{field_name} must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise ValueError(f"{field_name} must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        raise ValueError(f"{field_name} must contain at least one digit")
    return password
