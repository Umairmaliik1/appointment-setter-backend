"""
Phone number normalization utilities.
Converts phone numbers to E.164 format for consistent storage and lookup.
"""

import re
from typing import Optional


def normalize_phone_number(phone_number: str) -> str:
    """
    Normalize phone number to E.164 format.

    E.164 format: +[country code][number] (e.g., +14155552671)

    Args:
        phone_number: Phone number in various formats

    Returns:
        Normalized phone number in E.164 format

    Raises:
        ValueError: If phone number cannot be normalized
    """
    if not phone_number:
        raise ValueError("Phone number cannot be empty")

    # Remove all non-digit characters except +
    cleaned = re.sub(r"[^\d+]", "", phone_number.strip())

    # If starts with +, keep it; otherwise, assume US (+1)
    if cleaned.startswith("+"):
        normalized = cleaned
    else:
        # If starts with 1 and has 11 digits, add +
        if cleaned.startswith("1") and len(cleaned) == 11:
            normalized = f"+{cleaned}"
        elif len(cleaned) == 10:
            # US number without country code
            normalized = f"+1{cleaned}"
        else:
            # Already has country code, just add +
            normalized = f"+{cleaned}"

    # Validate E.164 format: + followed by 1-15 digits
    if not re.match(r"^\+[1-9]\d{1,14}$", normalized):
        raise ValueError(f"Invalid phone number format: {phone_number} (normalized: {normalized})")

    return normalized


def normalize_phone_number_safe(phone_number: Optional[str]) -> Optional[str]:
    """
    Safely normalize phone number, returning None if invalid.

    Args:
        phone_number: Phone number to normalize

    Returns:
        Normalized phone number or None if invalid
    """
    if not phone_number:
        return None

    try:
        return normalize_phone_number(phone_number)
    except (ValueError, AttributeError):
        return None
