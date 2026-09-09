from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_alembic_or_schema_autocreate() -> None:
    backend_sources = list((ROOT / "backend" / "app").rglob("*.py"))
    worker_sources = [
        path
        for path in (ROOT / "workers").rglob("*.py")
        if "tests" not in path.parts
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in backend_sources + worker_sources
    )

    assert "metadata.create_all" not in text
    assert "create_all(" not in text
    assert "al" + "embic" not in text.lower()
