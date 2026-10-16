"""Durable local mock purchasing service.

Simulates an external purchase-order API that is **idempotent on an idempotency key**:
repeated creates with the same key return the already-persisted PO and never store a
second one. It exposes deterministic test modes so the backend can exercise failure,
uncertainty, and persisted-result-mismatch paths offline.

Orders are committed to a separate SQLite store, independent of the PostgreSQL mirror.
The execution and validation services call this singleton directly. The same store also
backs the HTTP mock router (`app.mock.router`) for demonstration.
"""

from __future__ import annotations

import threading
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


class MockError(Exception):
    """Base class for mock transport/execution errors."""


class MockFailure(MockError):
    """The mock confirms the create failed (no PO persisted)."""


class MockUncertain(MockError):
    """The create outcome could not be confirmed by the caller."""


class MockUnavailable(MockError):
    """The mock could not be reached (read or create transport failure)."""


@dataclass
class MockPurchaseOrder:
    external_po_id: str
    idempotency_key: str
    sku: str
    supplier_name: str
    quantity: int
    unit_price: Decimal
    total_cost: Decimal
    currency: str
    status: str = "confirmed"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict[str, Any]:
        return {
            "external_po_id": self.external_po_id,
            "idempotency_key": self.idempotency_key,
            "sku": self.sku,
            "supplier_name": self.supplier_name,
            "quantity": self.quantity,
            "unit_price": f"{self.unit_price:f}",
            "total_cost": f"{self.total_cost:f}",
            "currency": self.currency,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


# Test-mode configuration ---------------------------------------------------

CREATE_OK = "ok"
CREATE_FAILURE = "failure"
CREATE_UNCERTAIN = "uncertain"

READ_OK = "ok"
READ_UNAVAILABLE = "unavailable"
READ_MISSING = "missing"

MISMATCH_NONE = "none"
MISMATCH_QUANTITY = "quantity"
MISMATCH_SUPPLIER = "supplier"


class MockPurchasingService:
    def __init__(self, database_path: str | None = None) -> None:
        from app.config import get_settings

        self.database_path = database_path or get_settings().mock_database_path
        self._lock = threading.Lock()
        self.create_mode = CREATE_OK
        self.read_mode = READ_OK
        self.mismatch = MISMATCH_NONE
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS mock_orders (
                idempotency_key TEXT PRIMARY KEY,
                external_po_id TEXT UNIQUE NOT NULL,
                snapshot TEXT NOT NULL
            )""")

    @contextmanager
    def _connect(self):
        conn = None
        try:
            conn = sqlite3.connect(self.database_path, timeout=30)
            with conn:
                yield conn
        except sqlite3.Error as exc:
            # A failed commit may have an uncertain outcome. The backend must
            # persist that state and reconcile the durable key before a retry.
            raise MockUnavailable("Mock order store could not be accessed.") from exc
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _decode(row) -> MockPurchaseOrder | None:
        if row is None:
            return None
        data = json.loads(row[0])
        data["unit_price"] = Decimal(data["unit_price"])
        data["total_cost"] = Decimal(data["total_cost"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        return MockPurchaseOrder(**data)

    def reset(self) -> None:
        """Test-only control; callers must use an isolated test store."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM mock_orders")
            self.create_mode = CREATE_OK
            self.read_mode = READ_OK
            self.mismatch = MISMATCH_NONE

    def configure(
        self,
        *,
        create_mode: str | None = None,
        read_mode: str | None = None,
        mismatch: str | None = None,
    ) -> None:
        with self._lock:
            if create_mode is not None:
                self.create_mode = create_mode
            if read_mode is not None:
                self.read_mode = read_mode
            if mismatch is not None:
                self.mismatch = mismatch

    # -- API ----------------------------------------------------------------

    def create(self, *, idempotency_key: str, payload: dict[str, Any]) -> MockPurchaseOrder:
        with self._lock:
            create_mode, mismatch = self.create_mode, self.mismatch

        # SQLite's write transaction and unique key protect concurrent callers,
        # including separate mock service instances. Commit precedes the response.
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._decode(conn.execute(
                "SELECT snapshot FROM mock_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone())
            if existing is not None:
                return existing
            if create_mode == CREATE_FAILURE:
                raise MockFailure("Mock purchasing service rejected the order.")

            quantity = int(payload["quantity"])
            unit_price = Decimal(str(payload["unit_price"]))
            supplier_name = str(payload["supplier_name"])
            if mismatch == MISMATCH_QUANTITY:
                quantity = max(1, quantity - 300)
            elif mismatch == MISMATCH_SUPPLIER:
                supplier_name = "Unexpected Supplier S.A."
            po = MockPurchaseOrder(
                external_po_id=f"MOCK-PO-{uuid.uuid4()}",
                idempotency_key=idempotency_key,
                sku=str(payload["sku"]), supplier_name=supplier_name,
                quantity=quantity, unit_price=unit_price,
                total_cost=unit_price * quantity, currency=str(payload["currency"]),
            )
            conn.execute("INSERT INTO mock_orders VALUES (?, ?, ?)", (
                idempotency_key, po.external_po_id, json.dumps(po.snapshot()),
            ))
        if create_mode == CREATE_UNCERTAIN:
            raise MockUncertain("Create outcome could not be confirmed.")
        return po

    def get_by_key(self, idempotency_key: str) -> MockPurchaseOrder | None:
        self._raise_if_read_unavailable()
        with self._lock, self._connect() as conn:
            if self.read_mode == READ_MISSING:
                return None
            return self._decode(conn.execute(
                "SELECT snapshot FROM mock_orders WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone())

    def get_by_external_id(self, external_po_id: str) -> MockPurchaseOrder | None:
        self._raise_if_read_unavailable()
        with self._lock, self._connect() as conn:
            if self.read_mode == READ_MISSING:
                return None
            return self._decode(conn.execute(
                "SELECT snapshot FROM mock_orders WHERE external_po_id = ?",
                (external_po_id,),
            ).fetchone())

    # -- internals ----------------------------------------------------------

    def _raise_if_read_unavailable(self) -> None:
        with self._lock:
            read_mode = self.read_mode
        if read_mode == READ_UNAVAILABLE:
            raise MockUnavailable("Mock purchasing service is unreachable.")


_service = MockPurchasingService()


def get_mock_service() -> MockPurchasingService:
    return _service
