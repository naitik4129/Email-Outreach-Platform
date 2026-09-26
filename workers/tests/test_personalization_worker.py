"""Personalization tasks and the scheduler's generation sweep. The runner, model
and database are replaced by fakes: nothing here calls a model, a website or a
database."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import workers.personalization as personalization
from workers.scheduler import SchedulerRuntime

from app.core.config import Settings, reset_settings_cache

WS = str(uuid.uuid4())
CAMPAIGN = str(uuid.uuid4())


def _settings(**overrides) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        supabase_url="https://test-project.supabase.co",
        **overrides,
    )


@pytest.fixture(autouse=True)
def _clean_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


def _summary(**kw):
    base = dict(
        claimed=0,
        exhausted=0,
        generated=0,
        fallback=0,
        failed=0,
        superseded=0,
        deferred=0,
        rejected=0,
        transient_errors=0,
        permanent_errors=0,
        codes={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestGenerateChunkTask:
    def test_is_a_no_op_when_personalization_is_disabled(self) -> None:
        with patch.object(personalization, "build_model") as build_model:
            personalization.generate_chunk.run(WS, CAMPAIGN)
        build_model.assert_not_called()

    def test_runs_one_chunk_with_the_task_id_as_lease_owner_and_closes_the_model(
        self,
    ) -> None:
        settings = _settings(personalization_enabled=True, personalization_model="m")
        model = MagicMock()
        runner = MagicMock()
        runner.run_chunk.return_value = _summary()
        with (
            patch.object(Settings, "current", return_value=settings),
            patch.object(personalization, "build_model", return_value=model),
            patch.object(
                personalization, "build_generation_runner", return_value=runner
            ),
        ):
            personalization.generate_chunk.run(WS, CAMPAIGN)
        call = runner.run_chunk.call_args.kwargs
        assert str(call["workspace_id"]) == WS and str(call["campaign_id"]) == CAMPAIGN
        assert call["lease_owner"]
        model.close.assert_called_once()

    def test_a_full_productive_chunk_requeues_itself_with_ids_only(self) -> None:
        settings = _settings(
            personalization_enabled=True,
            personalization_model="m",
            personalization_chunk_size=2,
        )
        runner = MagicMock()
        runner.run_chunk.return_value = _summary(claimed=2, generated=2)
        with (
            patch.object(Settings, "current", return_value=settings),
            patch.object(personalization, "build_model", return_value=MagicMock()),
            patch.object(
                personalization, "build_generation_runner", return_value=runner
            ),
            patch.object(personalization.generate_chunk, "retry") as retry,
        ):
            personalization.generate_chunk.run(WS, CAMPAIGN)
        kwargs = retry.call_args.kwargs["kwargs"]
        assert kwargs == {"workspace_id": WS, "campaign_id": CAMPAIGN}

    def test_a_deferred_chunk_does_not_spin(self) -> None:
        settings = _settings(
            personalization_enabled=True,
            personalization_model="m",
            personalization_chunk_size=2,
        )
        runner = MagicMock()
        runner.run_chunk.return_value = _summary(claimed=2, deferred=2)
        with (
            patch.object(Settings, "current", return_value=settings),
            patch.object(personalization, "build_model", return_value=MagicMock()),
            patch.object(
                personalization, "build_generation_runner", return_value=runner
            ),
            patch.object(personalization.generate_chunk, "retry") as retry,
        ):
            personalization.generate_chunk.run(WS, CAMPAIGN)
        retry.assert_not_called()  # budget/rate-limit deferrals wait for the sweep

    def test_task_routes_are_the_personalization_queue(self) -> None:
        assert personalization.generate_chunk.queue == "personalization"
        assert personalization.generate_previews.queue == "personalization"


class TestGeneratePreviewsTask:
    def test_disabled_is_a_no_op_and_enabled_runs_the_batch(self) -> None:
        batch = str(uuid.uuid4())
        with patch.object(personalization, "build_model") as build_model:
            personalization.generate_previews.run(WS, CAMPAIGN, batch)
        build_model.assert_not_called()

        settings = _settings(personalization_enabled=True, personalization_model="m")
        runner = MagicMock()
        runner.run_batch.return_value = SimpleNamespace(ok=3, failed=0, codes={})
        with (
            patch.object(Settings, "current", return_value=settings),
            patch.object(personalization, "build_model", return_value=MagicMock()),
            patch.object(personalization, "build_preview_runner", return_value=runner),
        ):
            personalization.generate_previews.run(WS, CAMPAIGN, batch)
        assert str(runner.run_batch.call_args.kwargs["batch_id"]) == batch


class TestSchedulerSweep:
    def _runtime(self, **overrides) -> SchedulerRuntime:
        return SchedulerRuntime(_settings(**overrides))

    def _sweep(self, runtime, found):
        sent = []
        celery = SimpleNamespace(send_task=lambda *a, **k: sent.append((a, k)))
        repo = MagicMock()
        repo.return_value.find_campaigns_with_due_generation.return_value = found
        with (
            patch("workers.scheduler.session_scope", MagicMock()),
            patch("app.modules.scheduler.repository.SchedulerRepository", repo),
            patch("workers.celery_app.celery_app", celery),
        ):
            queued = runtime._dispatch_personalization()
        return queued, sent, repo

    def test_does_nothing_when_disabled(self) -> None:
        queued, sent, repo = self._sweep(
            self._runtime(), [(uuid.uuid4(), uuid.uuid4())]
        )
        assert queued == 0 and sent == [] and not repo.called

    def test_queues_one_task_per_campaign_with_ids_only(self) -> None:
        runtime = self._runtime(personalization_enabled=True, personalization_model="m")
        ws, cid = uuid.uuid4(), uuid.uuid4()
        queued, sent, _ = self._sweep(runtime, [(ws, cid)])
        assert queued == 1
        ((args, kwargs),) = sent
        assert args == ("personalization.generate_chunk",)
        assert kwargs == {
            "kwargs": {"workspace_id": str(ws), "campaign_id": str(cid)},
            "queue": "personalization",
        }

    def test_respects_its_interval(self) -> None:
        runtime = self._runtime(
            personalization_enabled=True,
            personalization_model="m",
            personalization_dispatch_interval_seconds=3600,
        )
        found = [(uuid.uuid4(), uuid.uuid4())]
        first, _, _ = self._sweep(runtime, found)
        second, _, _ = self._sweep(runtime, found)
        assert (first, second) == (1, 0)

    def test_walks_all_campaigns_by_keyset_and_wraps_around(self) -> None:
        runtime = self._runtime(
            personalization_enabled=True,
            personalization_model="m",
            personalization_campaigns_per_run=2,
            personalization_dispatch_interval_seconds=0.01,
        )
        a, b = (uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())
        runtime._personalization_last_run = float("-inf")
        self._sweep(runtime, [a, b])
        assert runtime._personalization_cursor == b[1]  # a full slice: continue after b
        runtime._personalization_last_run = float("-inf")
        self._sweep(runtime, [a])
        assert runtime._personalization_cursor is None  # short slice: wrap around
