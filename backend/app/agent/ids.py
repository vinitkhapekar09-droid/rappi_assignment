"""Deterministic identifier helpers used by the workflow."""

from __future__ import annotations

import uuid

# Fixed namespace for derived execution idempotency keys. Changing this value
# changes every derived key, so it must remain stable.
_IDEMPOTENCY_NAMESPACE = uuid.UUID("2f0b7c1e-6a44-5d3a-9c2b-1e9f7a4d6b08")


def derive_execution_idempotency_key(
    case_id: str,
    version_number: int,
    approval_id: str,
    quantity: int | None,
    supplier_offer_id: str | None,
) -> str:
    """Stable idempotency key for one purchase action.

    Re-running the same approved action derives the same key, so a repeated
    request reconciles instead of creating a second purchase order. Callers may
    still supply a client-generated key; this is the deterministic default.
    """
    payload = "|".join(
        str(part)
        for part in (case_id, version_number, approval_id, quantity, supplier_offer_id)
    )
    return str(uuid.uuid5(_IDEMPOTENCY_NAMESPACE, payload))
