"""Fixtures for the migration test suite (Plan 16 task_16_01 onward).

The migration tests talk to the same throwaway PostgreSQL as the
integration suite and use the same Alembic + DSN fixtures. Rather than
duplicate the throwaway-DB plumbing, re-export the canonical fixtures
defined in ``tests/integration/conftest.py`` so they resolve for tests
collected under ``tests/migrations/`` too.
"""

from __future__ import annotations

from tests.integration.conftest import (
    admin_pg_dsn,
    alembic_config,
    migrations_pg_dsn,
    test_database_url,
)

__all__ = [
    "admin_pg_dsn",
    "alembic_config",
    "migrations_pg_dsn",
    "test_database_url",
]
