from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Files legitimately allowed to branch on a literal provider name string --
# the provider registry (the single integration point), the adapters
# themselves, and the handful of precondition guards that check "is this
# mailbox even the right provider for this operation" (not provider
# *behavior* branching).
_PROVIDER_BRANCHING_ALLOWED_FILES = {
    "providers/registry.py",
    "providers/gmail.py",
    "providers/microsoft.py",
    "providers/smtp.py",
    "service.py",  # reconnect_gmail/reconnect_microsoft precondition guards
}
_PROVIDER_BRANCH_PATTERN = re.compile(
    r"provider[\w\[\]\"'.]*\s*(==|!=)\s*[\"'](GMAIL|MICROSOFT|SMTP)[\"']"
)


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


def test_no_provider_branching_outside_registry_and_adapters() -> None:
    """Phase 6 abstraction-quality check: application/service code must
    resolve provider behavior through ProviderRegistry, never by matching
    on the provider name string itself (that's exactly the kind of
    scattered per-provider conditional the provider abstraction exists to
    prevent -- see docs/adr/0006-provider-abstraction.md)."""
    mailboxes_dir = ROOT / "backend" / "app" / "modules" / "mailboxes"
    violations: list[str] = []

    for path in mailboxes_dir.rglob("*.py"):
        rel = path.relative_to(mailboxes_dir).as_posix()
        if rel in _PROVIDER_BRANCHING_ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _PROVIDER_BRANCH_PATTERN.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            violations.append(f"{rel}:{line_no}: {match.group(0)}")

    assert not violations, (
        "Found provider-name branching outside the registry/adapters -- "
        "route this through ProviderRegistry instead:\n" + "\n".join(violations)
    )
