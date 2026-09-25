from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import Retry
from workers import imports as imports_worker

from app.core.errors import AppError
from app.db.context import enter_worker_scope
from app.modules.imports.repository import ImportRepository

WORKSPACE_ID = str(uuid.uuid4())
IMPORT_ID = str(uuid.uuid4())
CSV = "email\na@example.com\nb@example.com\nc@example.com\n"


class _Events:
    """One ordered log shared by the fake session, scope and service."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def names(self) -> list[str]:
        return [entry.split(":")[0] for entry in self.log]


class _FakeSession:
    def __init__(self, events: _Events) -> None:
        self.events = events

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def commit(self) -> None:
        self.events.log.append("commit")

    def rollback(self) -> None:
        self.events.log.append("rollback")

    def begin(self) -> None:  # the old worker called this after an autobegin
        raise AssertionError("session.begin() must not be used")


class _FakeService:
    job: dict[str, Any] | None = None
    finished = False
    download_error: Exception | None = None
    batches: list[list[tuple[int, dict[str, str]]]]
    events: _Events

    def __init__(self, session: _FakeSession, *, settings: Any) -> None:
        pass

    def claim(self, **kwargs: Any) -> dict[str, Any] | None:
        self.events.log.append(f"claim:{kwargs['lease_owner']}")
        return self.job

    def download_and_decode(self, key: str, digest: str) -> str:
        self.events.log.append("download")
        if self.download_error is not None:
            raise self.download_error
        return CSV

    def process_batch(self, **kwargs: Any) -> bool:
        self.events.log.append("process")
        self.batches.append(kwargs["batch"])
        return self.finished

    def fail(self, *, workspace_id: Any, import_id: Any, error: AppError) -> None:
        self.events.log.append(f"fail:{error.code}")


def _job(row_cursor: int = 0) -> dict[str, Any]:
    return {
        "storage_object_key": "workspace/x/imports/a.csv",
        "storage_object_digest": "d" * 64,
        "row_cursor": row_cursor,
    }


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    events = _Events()

    class Service(_FakeService):
        pass

    Service.events = events
    Service.job = _job()
    Service.finished = False
    Service.download_error = None
    Service.batches = []

    monkeypatch.setattr(imports_worker, "SessionLocal", lambda: _FakeSession(events))
    monkeypatch.setattr(imports_worker, "ImportService", Service)
    monkeypatch.setattr(
        imports_worker,
        "enter_worker_scope",
        lambda session, *, workspace_id, role_name: events.log.append(
            f"scope:{role_name}"
        ),
    )

    def retry(**_: Any) -> None:
        events.log.append("retry")
        raise Retry()

    task_self = SimpleNamespace(
        request=SimpleNamespace(id="owner-1", retries=0), retry=retry
    )
    return SimpleNamespace(events=events, service=Service, task_self=task_self)


def _run(harness: SimpleNamespace) -> None:
    # process_chunk.run is the bound original function; call it with our fake self.
    imports_worker.process_chunk.run.__func__(  # type: ignore[attr-defined]
        harness.task_self, WORKSPACE_ID, IMPORT_ID
    )


def test_claim_is_committed_before_work_and_scope_is_reapplied(
    harness: SimpleNamespace,
) -> None:
    harness.service.finished = True

    _run(harness)

    assert harness.events.log == [
        "scope:app_worker_general",
        "claim:owner-1",
        "commit",
        "scope:app_worker_general",
        "download",
        "process",
        "commit",
    ]


def test_unfinished_import_requeues_and_retry_is_not_a_failure(
    harness: SimpleNamespace,
) -> None:
    with pytest.raises(Retry):
        _run(harness)

    assert harness.events.names()[-2:] == ["commit", "retry"]
    assert "fail" not in harness.events.names()
    assert "rollback" not in harness.events.names()


def test_unclaimable_import_is_a_noop(harness: SimpleNamespace) -> None:
    harness.service.job = None

    _run(harness)

    assert harness.events.names() == ["scope", "claim", "rollback"]


def test_app_error_marks_the_job_failed_in_a_fresh_transaction(
    harness: SimpleNamespace,
) -> None:
    harness.service.download_error = AppError(
        "storage_corrupt", "Stored file is corrupt", status_code=422
    )

    _run(harness)

    log = harness.events.log
    failed = log.index("fail:storage_corrupt")
    assert log[failed - 2 : failed] == ["rollback", "scope:app_worker_general"]
    assert log[-1] == "commit"
    assert "retry" not in log


def test_unexpected_error_is_reported_as_internal_error(
    harness: SimpleNamespace,
) -> None:
    harness.service.download_error = RuntimeError("boom")

    _run(harness)

    assert "fail:internal_error" in harness.events.log
    assert harness.events.log[-1] == "commit"


def test_batch_starts_after_the_checkpoint_and_is_bounded(
    harness: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(imports_worker, "BATCH_SIZE", 1)
    harness.service.job = _job(row_cursor=1)

    with pytest.raises(Retry):
        _run(harness)

    assert harness.service.batches == [[(2, {"email": "b@example.com"})]]


# --- pieces the worker relies on -----------------------------------------


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: Any, params: Any = None) -> _CapturingSession:
        self.statements.append(str(statement))
        return self

    def mappings(self) -> _CapturingSession:
        return self

    def first(self) -> None:
        return None


def test_claim_lets_a_retry_continue_its_own_lease() -> None:
    session = _CapturingSession()

    ImportRepository(session).claim_job(  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        import_id=uuid.uuid4(),
        lease_owner="owner-1",
        ttl_seconds=120,
    )

    assert "lease_owner = :lease_owner" in session.statements[0]


def test_worker_scope_sets_workspace_and_role_on_postgres_only() -> None:
    class Bind:
        def __init__(self, name: str) -> None:
            self.dialect = SimpleNamespace(name=name)

    class Session(_CapturingSession):
        def __init__(self, dialect: str) -> None:
            super().__init__()
            self.bind = Bind(dialect)

        def get_bind(self) -> Bind:
            return self.bind

    postgres = Session("postgresql")
    enter_worker_scope(
        postgres,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        role_name="app_worker_general",
    )
    assert "set_config" in postgres.statements[0]
    assert postgres.statements[-1] == "RESET ROLE; SET LOCAL ROLE app_worker_general"

    sqlite = Session("sqlite")
    enter_worker_scope(
        sqlite,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        role_name="app_worker_general",
    )
    assert all("ROLE" not in statement for statement in sqlite.statements)
