"""Engine pool selection. Engines are built but never connected, so no database is
touched."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import Settings
from app.db.session import get_engine, reset_engine_cache

URL = "postgresql+psycopg://u:p@db.invalid:5432/postgres"


@pytest.fixture(autouse=True)
def _fresh_engines():
    reset_engine_cache()
    yield
    reset_engine_cache()


def test_positive_pool_size_keeps_a_bounded_queue_pool() -> None:
    engine = get_engine(URL, 3, 2, 15)
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 3
    assert engine.pool._max_overflow == 2


def test_zero_pool_size_holds_no_idle_connections() -> None:
    # Background processes use this so they occupy a Session-pooler client slot
    # only while working, not for their whole lifetime.
    engine = get_engine(URL, 0, 2, 15)
    assert isinstance(engine.pool, NullPool)


def test_zero_pool_size_still_sets_connect_timeout_and_keepalives(monkeypatch) -> None:
    captured: dict = {}

    def fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(dispose=lambda: None)

    monkeypatch.setattr("app.db.session.create_engine", fake_create_engine)
    get_engine(URL, 0, 0, 15, 7)
    assert captured["poolclass"] is NullPool
    assert "pool_size" not in captured and "max_overflow" not in captured
    assert captured["connect_args"]["connect_timeout"] == 7
    assert captured["connect_args"]["keepalives"] == 1


def test_settings_accept_zero_pool_size_but_not_negative() -> None:
    base = {
        "database_url": "sqlite+pysqlite:///:memory:",
        "redis_url": "redis://x",
        "supabase_url": "https://x.supabase.co",
    }
    assert Settings(**base, db_pool_size=0).db_pool_size == 0
    with pytest.raises(ValueError):
        Settings(**base, db_pool_size=-1)
