from __future__ import annotations

from sqlalchemy import text

from app.core.config import Settings
from app.db.session import session_scope


def check_database(settings: Settings) -> dict[str, object]:
    try:
        with session_scope(settings) as session:
            session.execute(
                text("select set_config('statement_timeout', :timeout_ms, true)"),
                {"timeout_ms": str(settings.db_statement_timeout_ms)},
            )
            session.execute(text("select 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}
