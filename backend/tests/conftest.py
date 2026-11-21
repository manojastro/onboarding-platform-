"""Test harness.

Tests run against real Postgres, never SQLite: the idempotency anchor, the
triggers and the NUMERIC types are part of what is under test. The test
database is dropped, recreated and migrated from zero once per session, so
every run also proves the migrations apply cleanly.
"""

from __future__ import annotations

import os
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://onboarding:onboarding@localhost:55432/onboarding_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before any app module creates the engine

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _recreate_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError as exc:
        pytest.exit(
            f"Postgres is not reachable at {url.render_as_string(hide_password=True)}. "
            f"Start it with `docker compose up -d db`.\n{exc}",
            returncode=2,
        )
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def migrated_db():
    engine.dispose()
    _recreate_database()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    yield
    engine.dispose()


@pytest.fixture
def db(migrated_db):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session(db):
    with SessionLocal() as s:
        yield s


@pytest.fixture
def upload_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "uploads"
    monkeypatch.setattr(settings, "upload_dir", str(path))
    return path


@pytest.fixture
def client(db, upload_dir):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def connection_id(client) -> int:
    response = client.post("/api/connections", json={"name": "Test connection", "destination_type": "mock"})
    assert response.status_code == 201, response.text
    return response.json()["id"]
