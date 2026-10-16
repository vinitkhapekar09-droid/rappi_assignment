"""Pytest fixtures.

Offline domain tests need no database. API/scenario tests use a real PostgreSQL database
(the same schema the migration produces) so generated columns, partial unique indexes,
composite foreign keys, and row locking are exercised for real.
"""

from __future__ import annotations

import os
import uuid
import tempfile

import pytest

os.environ["RAPPI_MOCK_DATABASE_PATH"] = tempfile.mkdtemp(prefix="rappi-tests-") + "/mock.sqlite3"

# Configure the test database before any application module reads settings.
TEST_DATABASE_URL = os.environ.get(
    "RAPPI_TEST_DATABASE_URL",
    "postgresql+psycopg://rappi:rappi@localhost:55432/rappi_test",
)
os.environ["RAPPI_DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("RAPPI_IDEMPOTENCY_LEASE_SECONDS", "60")
os.environ["AGENT_LLM_PROVIDER"] = "offline"  # CI must never call a live provider.
os.environ["AGENT_OFFLINE"] = "false"  # Individual mocked online tests may opt in.

_ADMIN_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/rappi"
_TEST_DB_NAME = TEST_DATABASE_URL.rsplit("/", 1)[1].split("?")[0]


def _database_available() -> bool:
    try:
        import psycopg
    except Exception:  # pragma: no cover
        return False
    try:
        with psycopg.connect(_ADMIN_URL.replace("postgresql+psycopg", "postgresql"), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


DB_AVAILABLE = _database_available()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="PostgreSQL test database is not available; set RAPPI_TEST_DATABASE_URL.",
)


def _ensure_test_database() -> None:
    import psycopg

    admin = _ADMIN_URL.replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _prepared_database():
    if not DB_AVAILABLE:
        yield
        return
    _ensure_test_database()
    _run_migrations()
    yield


@pytest.fixture()
def db_session():
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def _truncate_all() -> None:
    from sqlalchemy import text

    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        session.execute(
            text(
                "TRUNCATE purchasing_cases, case_evidence, incoming_purchase_orders, "
                "supplier_offers, recommendation_versions, constraint_checks, approvals, "
                "purchase_orders, purchase_order_line_items, execution_attempts, "
                "validation_results, idempotency_keys RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_state():
    if DB_AVAILABLE:
        _truncate_all()
    from app.mock import get_mock_service

    get_mock_service().reset()
    yield
    get_mock_service().reset()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# --- Scenario helpers ------------------------------------------------------


def create_case(client, **overrides) -> dict:
    body = {
        "sku": "SKU-1234",
        "product_name": "Agua Embotellada 1L",
        "currency": "MXN",
        "volume_unit": "liter",
        "initial_recommended_quantity": 800,
        "planning_horizon_start": "2026-10-16",
    }
    body.update(overrides)
    resp = client.post("/api/v1/cases", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def standard_evidence(**overrides) -> dict:
    body = {
        "usable_inventory": 200,
        "forecast_demand": 1000,
        "safety_stock": 100,
        "budget_amount": "15000.00",
        "storage_capacity_units": "1000.0",
        "storage_required_per_unit": "1.0",
        "evidence_as_of": "2026-10-15T11:00:00Z",
        "incoming_orders": [
            {
                "external_ref": "PO-IN-1",
                "quantity": 100,
                "expected_arrival_date": "2026-10-18",
                "status": "confirmed",
            }
        ],
        "supplier_offers": [
            {
                "supplier_name": "Distribuidora Norte",
                "unit_price": "12.50",
                "currency": "MXN",
                "moq": 500,
                "lead_time_days": 3,
                "is_available": True,
            }
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture()
def standard_case(client) -> dict:
    return create_case(client)


def new_idempotency_key() -> str:
    return str(uuid.uuid4())
