"""Primary application data store (PostgreSQL-backed)."""

from app.services.postgres_store import postgres_store

# Canonical store instance used across the codebase.
store = postgres_store
