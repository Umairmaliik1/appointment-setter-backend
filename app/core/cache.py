"""
Caching utilities for frequently accessed data.
Uses Redis with TTL for cache-aside pattern.
"""

import json
import logging
from typing import Any, Dict, Optional

from app.core.async_redis import async_redis_client

# Configure logging
logger = logging.getLogger(__name__)

# Cache TTLs (in seconds)
TENANT_CACHE_TTL = 300  # 5 minutes
TWILIO_INTEGRATION_CACHE_TTL = 300  # 5 minutes
AGENT_CACHE_TTL = 180  # 3 minutes


async def get_cached_tenant(tenant_id: str) -> Optional[Dict[str, Any]]:
    """
    Get tenant from cache, or None if not cached.

    Cache key: cache:tenant:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:tenant:{tenant_id}"
        cached_data = await async_redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
    except Exception as e:
        logger.warning(f"Error reading tenant cache for {tenant_id}: {e}")
        return None


async def set_cached_tenant(tenant_id: str, tenant_data: Dict[str, Any]) -> None:
    """
    Store tenant in cache.

    Cache key: cache:tenant:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:tenant:{tenant_id}"
        await async_redis_client.setex(cache_key, TENANT_CACHE_TTL, json.dumps(tenant_data))
    except Exception as e:
        logger.warning(f"Error writing tenant cache for {tenant_id}: {e}")


async def invalidate_tenant_cache(tenant_id: str) -> None:
    """
    Invalidate tenant cache (delete from cache).

    Also invalidates related caches (business_config, agent_settings).
    """
    try:
        cache_keys = [
            f"cache:tenant:{tenant_id}",
            f"cache:business_config:{tenant_id}",
            f"cache:agent_settings:{tenant_id}",
        ]
        for key in cache_keys:
            await async_redis_client.delete(key)
    except Exception as e:
        logger.warning(f"Error invalidating tenant cache for {tenant_id}: {e}")


async def get_cached_business_config(tenant_id: str) -> Optional[Dict[str, Any]]:
    """
    Get business config from cache, or None if not cached.

    Cache key: cache:business_config:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:business_config:{tenant_id}"
        cached_data = await async_redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
    except Exception as e:
        logger.warning(f"Error reading business config cache for {tenant_id}: {e}")
        return None


async def set_cached_business_config(tenant_id: str, business_config: Dict[str, Any]) -> None:
    """
    Store business config in cache.

    Cache key: cache:business_config:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:business_config:{tenant_id}"
        await async_redis_client.setex(cache_key, TENANT_CACHE_TTL, json.dumps(business_config))
    except Exception as e:
        logger.warning(f"Error writing business config cache for {tenant_id}: {e}")


async def get_cached_agent_settings(tenant_id: str) -> Optional[Dict[str, Any]]:
    """
    Get agent settings from cache, or None if not cached.

    Cache key: cache:agent_settings:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:agent_settings:{tenant_id}"
        cached_data = await async_redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
    except Exception as e:
        logger.warning(f"Error reading agent settings cache for {tenant_id}: {e}")
        return None


async def set_cached_agent_settings(tenant_id: str, agent_settings: Dict[str, Any]) -> None:
    """
    Store agent settings in cache.

    Cache key: cache:agent_settings:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:agent_settings:{tenant_id}"
        await async_redis_client.setex(cache_key, TENANT_CACHE_TTL, json.dumps(agent_settings))
    except Exception as e:
        logger.warning(f"Error writing agent settings cache for {tenant_id}: {e}")


async def get_cached_twilio_integration(tenant_id: str) -> Optional[Dict[str, Any]]:
    """
    Get Twilio integration from cache, or None if not cached.

    Note: This does NOT decrypt auth_token. Decryption should be done after cache retrieval.

    Cache key: cache:twilio_integration:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:twilio_integration:{tenant_id}"
        cached_data = await async_redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)
        return None
    except Exception as e:
        logger.warning(f"Error reading Twilio integration cache for {tenant_id}: {e}")
        return None


async def set_cached_twilio_integration(tenant_id: str, integration_data: Dict[str, Any]) -> None:
    """
    Store Twilio integration in cache.

    Note: Store encrypted auth_token as-is. Decryption happens on retrieval.

    Cache key: cache:twilio_integration:{tenant_id}
    TTL: 5 minutes
    """
    try:
        cache_key = f"cache:twilio_integration:{tenant_id}"
        await async_redis_client.setex(cache_key, TWILIO_INTEGRATION_CACHE_TTL, json.dumps(integration_data))
    except Exception as e:
        logger.warning(f"Error writing Twilio integration cache for {tenant_id}: {e}")


async def invalidate_twilio_integration_cache(tenant_id: str) -> None:
    """
    Invalidate Twilio integration cache (delete from cache).
    """
    try:
        cache_key = f"cache:twilio_integration:{tenant_id}"
        await async_redis_client.delete(cache_key)
    except Exception as e:
        logger.warning(f"Error invalidating Twilio integration cache for {tenant_id}: {e}")
