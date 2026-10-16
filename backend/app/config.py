"""Application configuration.

All settings are environment-driven with safe local defaults. Secrets must never be
committed; see `backend/.env.example`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAPPI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://rappi:rappi@localhost:5432/rappi"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    mock_database_path: str = "mock-orders.sqlite3"

    # Idempotency lease timeout (seconds). A stale `in_progress` key is recovered only by
    # reconciling against the mock service, never by a blind create. See API_CONTRACT §2.2.
    idempotency_lease_seconds: int = 60

    # Planning horizon is fixed at seven days by the assignment.
    planning_horizon_days: int = 7

    # When true, the app fails fast if the database is unreachable at startup.
    fail_fast_on_db_error: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
