"""PostgreSQL readiness helper service."""

from app.services.database import check_database_health, get_alembic_revision


class PostgresHealth:
    """Provides startup/readiness DB checks."""

    async def warm_up_connection(self) -> bool:
        return check_database_health()

    async def check_connection(self) -> bool:
        return check_database_health()

    async def get_migration_revision(self) -> str | None:
        return get_alembic_revision()


postgres_health = PostgresHealth()
