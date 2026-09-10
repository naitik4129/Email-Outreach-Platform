from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    # Disable the .env fallback: this test asserts what happens when nothing
    # supplies DATABASE_URL at all, but a real repo-root .env now legitimately
    # exists for local development and would otherwise supply one.
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_rejects_wildcard_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "*")

    with pytest.raises(ValidationError, match="Wildcard CORS"):
        Settings()
