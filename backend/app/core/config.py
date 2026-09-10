from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env, resolved from this file's location so it is found
# regardless of the process's working directory (backend/ for local
# pytest/uvicorn, repo root for tooling run from there). Docker containers
# get real env vars injected directly and never rely on this file existing.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


def _split_origins(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [origin.strip() for origin in value.split(",") if origin.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT_ENV, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "Email Outreach Platform"
    api_title: str = "Email Outreach API"
    api_version: str = "0.1.0"

    database_url: str = Field(default="", min_length=1)
    redis_url: str = Field(default="", min_length=1)
    backend_cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    service_name: str = "backend"

    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: int = Field(default=5, ge=1, le=60)
    db_statement_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    scheduler_poll_seconds: float = Field(default=5.0, gt=0, le=300)

    supabase_url: str = Field(default="", min_length=1)
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: str = ""

    @field_validator("log_level", mode="before")
    @classmethod
    def uppercase_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("database_url", mode="after")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Force the psycopg (v3) driver, the only Postgres driver this
        project depends on. Supabase's dashboard copies connection strings as
        plain postgresql:// / postgres://, which SQLAlchemy otherwise resolves
        to the psycopg2 driver by default and which is not installed here.
        """
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        return value

    @model_validator(mode="after")
    def validate_cors(self) -> Settings:
        if not self.cors_origins:
            raise ValueError("At least one CORS origin is required")
        if self.app_env == "production" and "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origins are not allowed in production")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return _split_origins(self.backend_cors_origins)

    @property
    def supabase_jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @classmethod
    def current(cls) -> Settings:
        return get_settings()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
