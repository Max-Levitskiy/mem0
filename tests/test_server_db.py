"""Regression tests for server/db.py's database-provisioning helper.

Covers the Coolify failure mode: Postgres only runs
docker-entrypoint-initdb.d/init-db.sh (which creates APP_DB_NAME) the first
time its data directory is empty. A redeploy onto a volume that already has
data skips that step silently, so the app's first connection to APP_DB_NAME
fails with "database does not exist". ensure_database_exists() guards
against that instead of assuming the init script always ran.
"""

from unittest.mock import MagicMock, call, patch

import pytest

pytest.importorskip("psycopg", reason="psycopg not installed")

from server.db import ensure_database_exists


def _mock_connect(exists: bool):
    """Return a psycopg.connect mock matching the `with ... as conn:` usage
    in ensure_database_exists(), where conn.execute(...).fetchone() reports
    whether the target database is already present."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"datname": "mem0_app"} if exists else None
    connect = MagicMock()
    connect.return_value.__enter__.return_value = conn
    return connect, conn


class TestEnsureDatabaseExists:
    def test_creates_database_when_missing(self, monkeypatch):
        monkeypatch.setenv("APP_DB_NAME", "mem0_app")
        connect, conn = _mock_connect(exists=False)

        with patch("server.db.psycopg.connect", connect):
            ensure_database_exists()

        create_calls = [c for c in conn.execute.call_args_list if "CREATE DATABASE" in str(c)]
        assert create_calls, f"expected a CREATE DATABASE call, got: {conn.execute.call_args_list}"

    def test_skips_creation_when_already_present(self, monkeypatch):
        monkeypatch.setenv("APP_DB_NAME", "mem0_app")
        connect, conn = _mock_connect(exists=True)

        with patch("server.db.psycopg.connect", connect):
            ensure_database_exists()

        create_calls = [c for c in conn.execute.call_args_list if "CREATE DATABASE" in str(c)]
        assert not create_calls, f"should not create an existing database, got: {conn.execute.call_args_list}"

    def test_connects_to_maintenance_db_with_autocommit(self, monkeypatch):
        monkeypatch.setenv("APP_DB_NAME", "mem0_app")
        monkeypatch.setenv("POSTGRES_HOST", "postgres")
        monkeypatch.setenv("POSTGRES_USER", "myuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "mypassword")
        connect, _ = _mock_connect(exists=True)

        with patch("server.db.psycopg.connect", connect):
            ensure_database_exists()

        assert connect.call_args == call(
            host="postgres",
            port="5432",
            user="myuser",
            password="mypassword",
            dbname="postgres",
            autocommit=True,
        )
