"""PostgreSQL database configuration and helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DATABASE_CONNECT_TIMEOUT_SECONDS, DATABASE_POOL_MAX_OVERFLOW, DATABASE_POOL_SIZE, DATABASE_POOL_TIMEOUT_SECONDS, DATABASE_URL


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return singleton SQLAlchemy engine."""
    global _engine
    if _engine is None:
        connect_args = {}
        if DATABASE_URL.startswith("postgresql"):
            connect_args["connect_timeout"] = DATABASE_CONNECT_TIMEOUT_SECONDS

        _engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_size=DATABASE_POOL_SIZE,
            max_overflow=DATABASE_POOL_MAX_OVERFLOW,
            pool_timeout=DATABASE_POOL_TIMEOUT_SECONDS,
            connect_args=connect_args,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return singleton SQLAlchemy session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_health() -> bool:
    """Run lightweight health check query against DB."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_alembic_revision() -> str | None:
    """Return current Alembic migration revision if available."""
    try:
        with get_engine().connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
            if revision is None:
                return None
            return str(revision)
    except Exception:
        return None
