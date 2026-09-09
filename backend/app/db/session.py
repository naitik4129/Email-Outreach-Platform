from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


@lru_cache
def get_engine(
    database_url: str,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int,
) -> Engine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            {
                "pool_size": pool_size,
                "max_overflow": max_overflow,
                "pool_timeout": pool_timeout,
            }
        )
    return create_engine(database_url, **kwargs)


def engine_from_settings(settings: Settings) -> Engine:
    return get_engine(
        settings.database_url,
        settings.db_pool_size,
        settings.db_max_overflow,
        settings.db_pool_timeout_seconds,
    )


def make_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine_from_settings(settings),
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    session = make_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine(settings: Settings) -> None:
    engine_from_settings(settings).dispose()


def reset_engine_cache() -> None:
    get_engine.cache_clear()
