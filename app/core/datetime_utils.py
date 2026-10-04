"""
Datetime utilities for timezone-aware calculations and canonical UTC ISO-8601 formatting.
Ensures every appointment datetime in PostgreSQL and range queries is stored as UTC with 'Z'.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_TIMEZONE = "UTC"



def get_zoneinfo(tz_name: Optional[str]) -> ZoneInfo:
    """Return a ZoneInfo object for the given IANA timezone name with fallback."""
    if not tz_name or not isinstance(tz_name, str):
        return ZoneInfo(DEFAULT_SYSTEM_TIMEZONE)
    try:
        return ZoneInfo(tz_name.strip())
    except ZoneInfoNotFoundError:
        logger.warning(f"Unknown timezone '{tz_name}', falling back to {DEFAULT_SYSTEM_TIMEZONE}")
        return ZoneInfo(DEFAULT_SYSTEM_TIMEZONE)


def parse_to_utc_datetime(
    dt_or_str: Union[datetime, str],
    fallback_tz: Optional[Union[str, ZoneInfo]] = None,
) -> datetime:
    """
    Parse a datetime or ISO string and return a timezone-aware datetime in UTC.

    - If naive datetime/string without offset: localized using fallback_tz (defaulting to America/New_York).
    - If already aware (e.g. +05:00 or Z): converted to UTC.
    """
    tz_obj = fallback_tz if isinstance(fallback_tz, ZoneInfo) else get_zoneinfo(fallback_tz)

    if isinstance(dt_or_str, str):
        s = dt_or_str.strip()
        if not s:
            raise ValueError("Empty datetime string")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    elif isinstance(dt_or_str, datetime):
        dt = dt_or_str
    else:
        raise TypeError(f"Expected datetime or str, got {type(dt_or_str)}")

    if dt.tzinfo is None:
        # Naive datetime: localize with fallback timezone
        dt = dt.replace(tzinfo=tz_obj)

    return dt.astimezone(timezone.utc)


def to_utc_iso_z(
    dt_or_str: Union[datetime, str],
    fallback_tz: Optional[Union[str, ZoneInfo]] = None,
) -> str:
    """
    Convert any datetime or ISO string to canonical UTC ISO-8601 format with 'Z'.
    Example: '2026-10-05T14:30:00Z'
    This guarantees strict chronological ordering in PostgreSQL string index comparisons.
    """
    utc_dt = parse_to_utc_datetime(dt_or_str, fallback_tz=fallback_tz)
    # Strip microseconds for standard clean comparison, or keep if present:
    if utc_dt.microsecond:
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
