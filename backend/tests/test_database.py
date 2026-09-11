from __future__ import annotations

from sqlalchemy import text

from app.core.config import Settings
from app.db import session as db_session
from app.db.session import session_scope


def test_session_scope_rolls_back_on_exception() -> None:
    settings = Settings()

    try:
        with session_scope(settings) as session:
            session.execute(text("create table phase0_probe (id integer primary key)"))
            session.execute(text("insert into phase0_probe (id) values (1)"))
            raise RuntimeError("rollback")
    except RuntimeError:
        pass

    with session_scope(settings) as session:
        result = session.execute(text("select count(*) from phase0_probe")).scalar_one()

    assert result == 0


def test_reset_engine_cache_disposes_created_engines() -> None:
    disposed: list[str] = []

    class StubEngine:
        def dispose(self) -> None:
            disposed.append("disposed")

    db_session._created_engines.extend([StubEngine(), StubEngine()])  # type: ignore[list-item]

    db_session.reset_engine_cache()

    assert disposed == ["disposed", "disposed"]
    assert db_session._created_engines == []

