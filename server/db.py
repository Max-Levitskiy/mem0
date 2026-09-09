import os

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _connection_params() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "user": os.environ.get("POSTGRES_USER", "postgres"),
        "password": os.environ.get("POSTGRES_PASSWORD", "postgres"),
    }


def _build_database_url() -> str:
    params = _connection_params()
    db = os.environ.get("APP_DB_NAME", "mem0_app")
    return f"postgresql+psycopg://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{db}"


def ensure_database_exists() -> None:
    """Create APP_DB_NAME if it doesn't exist yet.

    Postgres only runs docker-entrypoint-initdb.d scripts (which create this
    database — see server/init-db.sh) the first time its data directory is
    empty. A redeploy onto a volume that already has data — e.g. a reused
    volume on platforms like Coolify — skips that step silently, so the first
    real connection fails with "database does not exist". Guard against that
    here instead of assuming the init script always ran.
    """
    params = _connection_params()
    db = os.environ.get("APP_DB_NAME", "mem0_app")
    with psycopg.connect(**params, dbname="postgres", autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))


engine = create_engine(_build_database_url(), pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a SQLAlchemy session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
