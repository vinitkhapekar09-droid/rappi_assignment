"""Application error type and stable error codes.

All non-2xx responses use the single envelope defined in `docs/API_CONTRACT.md` §2.1.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """An expected, contract-defined failure with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        http_status: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.details = details or {}

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


# --- Convenience constructors for the known codes (docs/API_CONTRACT.md §2.1) ---


def not_found(message: str, **details: Any) -> AppError:
    return AppError("not_found", 404, message, details)


def invalid_state(message: str, **details: Any) -> AppError:
    return AppError("invalid_state", 409, message, details)


def missing_evidence(message: str, **details: Any) -> AppError:
    return AppError("missing_evidence", 409, message, details)


def recommendation_superseded(message: str, **details: Any) -> AppError:
    return AppError("recommendation_superseded", 409, message, details)


def stale_approval(message: str, **details: Any) -> AppError:
    return AppError("stale_approval", 409, message, details)


def approval_mismatch(message: str, **details: Any) -> AppError:
    return AppError("approval_mismatch", 409, message, details)


def idempotency_conflict(message: str, **details: Any) -> AppError:
    return AppError("idempotency_conflict", 409, message, details)


def execution_in_progress(message: str, **details: Any) -> AppError:
    return AppError("execution_in_progress", 409, message, details)


def execution_failed(message: str, **details: Any) -> AppError:
    return AppError("execution_failed", 502, message, details)


def execution_uncertain(message: str, **details: Any) -> AppError:
    return AppError("execution_uncertain", 503, message, details)


def mock_unavailable(message: str, **details: Any) -> AppError:
    return AppError("mock_unavailable", 502, message, details)
