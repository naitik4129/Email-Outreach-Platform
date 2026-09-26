"""Migrations 0026-0029 and the personalization SQL against a REAL PostgreSQL.

Runs on a disposable server (pgserver) -- never the production-linked Supabase
project, and it sends no email and calls no model (a fake model is injected).
Skipped automatically when `pgserver` is not installed (e.g. in CI).

What this proves that the unit tests cannot: the migrations apply cleanly (and
their own post-checks pass), column grants/RLS/triggers behave as documented, the
worker SQL claims/finalizes correctly under concurrency, and tenants cannot see
each other's rows.
"""

# ruff: noqa: E402, E501 -- imports follow importorskip; long lines are SQL.
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import WorkspaceContext
from app.db.context import set_transaction_context
from app.modules.campaigns.message_rendering import compute_content_digest
from app.modules.campaigns.progression import ProgressionService
from app.modules.campaigns.worker_repository import (
    CampaignWorkerRepository,
)
from app.modules.personalization.api_service import (
    PersonalizationApiService,
)
from app.modules.personalization.budget import AllowAllLimiter
from app.modules.personalization.fake_model import FakeModel
from app.modules.personalization.jit import GenerationRunner, RunnerConfig
from app.modules.personalization.previews import PreviewRunner
from app.modules.personalization.repository import GenerationDb
from app.modules.personalization.schemas import (
    ApproveIn,
    PersonalizationConfigIn,
    PreviewCreateIn,
)
from app.modules.personalization.service import PersonalizationService
from app.modules.scheduler.repository import SchedulerRepository
from tests.support.personalization_fakes import FakeResearch
from tests.support.real_pg import apply_range, throwaway_database
from tests.support.real_seed import CONFIG, World, seed_world

pytestmark = pytest.mark.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def uri():
    with throwaway_database() as value:
        yield value


@pytest.fixture(scope="module")
def engine(uri):
    engine = create_engine(
        "postgresql+psycopg://" + uri.split("://", 1)[1], future=True
    )
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )


@pytest.fixture()
def su(uri):
    conn = psycopg.connect(uri, autocommit=True)
    yield conn
    conn.close()


def role_conn(uri: str, role: str, *, user=None, ws=None):
    conn = psycopg.connect(uri)
    conn.execute(f"SET LOCAL ROLE {role}")
    if user is not None:
        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user)])
    if ws is not None:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", [str(ws)])
    return conn


@contextmanager
def acting(uri: str, role: str, *, user=None, ws=None):
    conn = role_conn(uri, role, user=user, ws=ws)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def denied(uri: str, role: str, sql: str, params=(), *, user=None, ws=None, error=None):
    """Assert the statement fails for this role (grant, RLS or constraint)."""
    with acting(uri, role, user=user, ws=ws) as conn:
        with pytest.raises(error or psycopg.errors.InsufficientPrivilege):
            conn.execute(sql, params)


def count_as(uri: str, role: str, table: str, *, user=None, ws=None) -> int:
    with acting(uri, role, user=user, ws=ws) as conn:
        return conn.execute(f"SELECT count(*) FROM public.{table}").fetchone()[0]


def add_generation(
    su, w: World, *, due_in: timedelta = timedelta(minutes=-1), max_attempts=3
) -> uuid.UUID:
    """A PENDING generation for the world's first message (as the planner would
    create it via mark_generation_pending)."""
    gid = uuid.uuid4()
    now = datetime.now(UTC)
    su.execute(
        "UPDATE public.messages SET due_at=%s, anchor_at=%s WHERE id=%s",
        [now + timedelta(minutes=30), now, w.message1_id],
    )
    su.execute(
        """INSERT INTO public.message_generations
           (id, workspace_id, campaign_id, sequence_id, step_id, enrollment_id,
            message_id, state, max_attempts, next_attempt_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING',%s,%s)""",
        [
            gid,
            w.ws,
            w.campaign_id,
            w.sequence_id,
            w.step1,
            w.enrollment_id,
            w.message1_id,
            max_attempts,
            now + due_in,
        ],
    )
    return gid


def one(su, sql: str, *params) -> Any:
    return su.execute(sql, list(params)).fetchone()


def make_runner(session_factory, w: World, model=None, *, max_attempts_cfg=None):
    model = model or FakeModel()
    db = GenerationDb(session_factory, w.ws)
    service = PersonalizationService(model=model, research=FakeResearch(), min_facts=2)
    runner = GenerationRunner(
        db=db,
        service=service,
        rpm=AllowAllLimiter(),
        config=RunnerConfig(
            lease_seconds=300,
            chunk_size=10,
            max_transient_errors=3,
            daily_generation_cap=100,
        ),
    )
    return runner, model


def run_chunk(runner: GenerationRunner, w: World, owner="t"):
    return runner.run_chunk(
        workspace_id=w.ws, campaign_id=w.campaign_id, lease_owner=owner
    )


def worker_session(session_factory, ws) -> Session:
    session = session_factory()
    set_transaction_context(session, workspace_id=ws)
    return session


# ---------------------------------------------------------------------------
# migrations
# ---------------------------------------------------------------------------


def test_migrations_apply_on_top_of_existing_data() -> None:
    """0026 is additive: every campaign that already exists becomes STANDARD and
    every sequence has no objective; nothing is rewritten."""
    with throwaway_database(up_to=25) as base:
        with psycopg.connect(base, autocommit=True) as conn:
            ws, user, campaign, seq = (uuid.uuid4() for _ in range(4))
            conn.execute("SET session_replication_role = replica")
            conn.execute(
                "INSERT INTO public.workspaces (id, name) VALUES (%s, 'Old')", [ws]
            )
            conn.execute("INSERT INTO auth.users (id) VALUES (%s)", [user])
            conn.execute("INSERT INTO public.profiles (id) VALUES (%s)", [user])
            conn.execute(
                "INSERT INTO public.campaigns (id, workspace_id, name, creator_id) "
                "VALUES (%s,%s,'Legacy',%s)",
                [campaign, ws, user],
            )
            conn.execute(
                "INSERT INTO public.campaign_sequences (id, workspace_id, campaign_id, revision) "
                "VALUES (%s,%s,%s,1)",
                [seq, ws, campaign],
            )
            conn.execute("SET session_replication_role = origin")
        apply_range(base, 26, 29)  # includes each migration's own post-checks
        with psycopg.connect(base, autocommit=True) as conn:
            assert conn.execute(
                "SELECT campaign_type FROM public.campaigns WHERE id=%s", [campaign]
            ).fetchone() == ("STANDARD",)
            assert conn.execute(
                "SELECT personalization_config FROM public.campaign_sequences WHERE id=%s",
                [seq],
            ).fetchone() == (None,)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename ~ '(personalization|message_generation)'"
                ).fetchall()
            }
            assert tables == {
                "personalization_research_cache",
                "personalization_usage_daily",
                "message_generations",
                "message_generation_attempts",
                "personalization_previews",
                "campaign_personalization_approvals",
            }


def test_every_new_table_has_forced_row_level_security(su) -> None:
    rows = su.execute(
        """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND c.relname ~ '(personalization|message_generation)'
             AND c.relkind='r'"""
    ).fetchall()
    assert len(rows) == 6
    assert all(enabled and forced for _, enabled, forced in rows), rows


# ---------------------------------------------------------------------------
# campaign type / objective (0026)
# ---------------------------------------------------------------------------


class TestCampaignTypeAndObjective:
    def test_default_check_and_creation_through_the_api_role(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        for kind, expected in (
            ("HYPER_PERSONALIZED", "HYPER_PERSONALIZED"),
            (None, "STANDARD"),
        ):
            cid = uuid.uuid4()
            with acting(uri, "app_api", user=w.member, ws=w.ws) as conn:
                if kind:
                    conn.execute(
                        "INSERT INTO campaigns (id, workspace_id, name, creator_id, status, campaign_type) "
                        "VALUES (%s,%s,'n',%s,'DRAFT',%s)",
                        [cid, w.ws, w.member, kind],
                    )
                else:
                    conn.execute(
                        "INSERT INTO campaigns (id, workspace_id, name, creator_id, status) "
                        "VALUES (%s,%s,'n',%s,'DRAFT')",
                        [cid, w.ws, w.member],
                    )
                assert conn.execute(
                    "SELECT campaign_type FROM campaigns WHERE id=%s", [cid]
                ).fetchone() == (expected,)
        denied(
            uri,
            "app_api",
            "INSERT INTO campaigns (id, workspace_id, name, creator_id, status, campaign_type) "
            "VALUES (%s,%s,'n',%s,'DRAFT','AI_OUTREACH')",
            [uuid.uuid4(), w.ws, w.member],
            user=w.member,
            ws=w.ws,
            error=psycopg.errors.CheckViolation,
        )

    @pytest.mark.parametrize(
        "role",
        [
            "app_api",
            "app_worker_general",
            "app_worker_send",
            "app_worker_sync",
            "app_scheduler",
            "app_integrity_guard",
            "app_outbox_relay",
        ],
    )
    def test_campaign_type_is_immutable_for_every_runtime_role(
        self, uri, su, role
    ) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        denied(
            uri,
            role,
            "UPDATE campaigns SET campaign_type='STANDARD' WHERE id=%s",
            [w.campaign_id],
            user=w.manager,
            ws=w.ws,
        )
        denied(
            uri,
            role,
            "UPDATE campaigns SET campaign_type='HYPER_PERSONALIZED' WHERE id=%s",
            [w.campaign_id],
            user=w.manager,
            ws=w.ws,
        )

    def test_a_member_can_save_the_objective_while_draft(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        with acting(uri, "app_api", user=w.member, ws=w.ws) as conn:
            conn.execute(
                "UPDATE campaign_sequences SET personalization_config=%s::jsonb WHERE id=%s",
                ['{"objective":"x","offer":"y","cta":"z"}', w.sequence_id],
            )
            assert conn.execute(
                "SELECT personalization_config->>'cta' FROM campaign_sequences WHERE id=%s",
                [w.sequence_id],
            ).fetchone() == ("z",)

    @pytest.mark.parametrize(
        "value", ['"a string"', "[1,2]", '{"k":"' + "x" * 17000 + '"}']
    )
    def test_objective_must_be_a_bounded_json_object(self, uri, su, value) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        denied(
            uri,
            "app_api",
            "UPDATE campaign_sequences SET personalization_config=%s::jsonb WHERE id=%s",
            [value, w.sequence_id],
            user=w.member,
            ws=w.ws,
            error=psycopg.errors.CheckViolation,
        )

    def test_objective_is_frozen_with_the_sequence(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        su.execute(
            "UPDATE campaign_sequences SET status='FROZEN', frozen_at=now(), "
            "content_digest=%s WHERE id=%s",
            ["a" * 64, w.sequence_id],
        )
        denied(
            uri,
            "app_api",
            "UPDATE campaign_sequences SET personalization_config='{}'::jsonb WHERE id=%s",
            [w.sequence_id],
            user=w.member,
            ws=w.ws,
            error=psycopg.errors.CheckViolation,
        )

    def test_a_viewer_style_role_without_draft_permission_cannot_edit(
        self, uri, su
    ) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        outsider = uuid.uuid4()
        su.execute("INSERT INTO auth.users (id) VALUES (%s)", [outsider])
        su.execute("INSERT INTO public.profiles (id) VALUES (%s)", [outsider])
        with acting(uri, "app_api", user=outsider, ws=w.ws) as conn:
            rows = conn.execute(
                "UPDATE campaign_sequences SET personalization_config='{}'::jsonb "
                "WHERE id=%s RETURNING id",
                [w.sequence_id],
            ).fetchall()
            assert rows == []  # RLS hides the row: no membership, no write


# ---------------------------------------------------------------------------
# tenant isolation and grants (0027-0029)
# ---------------------------------------------------------------------------


class TestIsolationAndGrants:
    def _fill(self, su, w: World) -> uuid.UUID:
        gid = add_generation(su, w)
        batch = uuid.uuid4()
        su.execute(
            """INSERT INTO public.personalization_previews
               (workspace_id, campaign_id, batch_id, audience_member_id, lead_id, step_id,
                config_digest, created_by, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now() + interval '7 days')""",
            [
                w.ws,
                w.campaign_id,
                batch,
                w.member_ids[0],
                w.lead_ids[0],
                w.step1,
                "b" * 64,
                w.member,
            ],
        )
        su.execute(
            """INSERT INTO public.campaign_personalization_approvals
               (workspace_id, campaign_id, config_digest, batch_id, approved_by)
               VALUES (%s,%s,%s,%s,%s)""",
            [w.ws, w.campaign_id, "b" * 64, batch, w.manager],
        )
        su.execute(
            """INSERT INTO public.personalization_research_cache
               (workspace_id, source, url_hash, normalized_url, status, extracted_text, expires_at)
               VALUES (%s,'WEBSITE',%s,'https://a.test/','OK','some text', now() + interval '1 day')""",
            [w.ws, "c" * 64],
        )
        su.execute(
            """INSERT INTO public.personalization_usage_daily
                      (workspace_id, usage_day, kind, units) VALUES (%s, current_date, 'GENERATION', 1)""",
            [w.ws],
        )
        return gid

    def test_other_tenants_see_none_of_it_as_api_or_worker(self, uri, su) -> None:
        a = seed_world(su)
        b = seed_world(su)
        self._fill(su, a)
        for table in (
            "message_generations",
            "personalization_previews",
            "campaign_personalization_approvals",
            "personalization_usage_daily",
        ):
            assert count_as(uri, "app_api", table, user=a.manager, ws=a.ws) == 1, table
            assert count_as(uri, "app_api", table, user=b.manager, ws=b.ws) == 0, table
        for table in (
            "message_generations",
            "message_generation_attempts",
            "personalization_previews",
            "personalization_research_cache",
            "personalization_usage_daily",
        ):
            assert count_as(uri, "app_worker_general", table, ws=b.ws) == 0, table
        assert (
            count_as(
                uri, "app_worker_general", "personalization_research_cache", ws=a.ws
            )
            == 1
        )

    def test_a_user_cannot_use_another_tenants_workspace_context(self, uri, su) -> None:
        a = seed_world(su)
        b = seed_world(su)
        self._fill(su, a)
        # b's manager claims a's workspace id: not a member, so nothing is visible.
        assert (
            count_as(
                uri, "app_api", "personalization_previews", user=b.manager, ws=a.ws
            )
            == 0
        )
        denied(
            uri,
            "app_api",
            "INSERT INTO campaign_personalization_approvals "
            "(workspace_id, campaign_id, config_digest, batch_id, approved_by) "
            "VALUES (%s,%s,%s,%s,%s)",
            [a.ws, a.campaign_id, "d" * 64, uuid.uuid4(), b.manager],
            user=b.manager,
            ws=a.ws,
        )

    def test_roles_that_must_not_touch_these_tables_cannot(self, uri, su) -> None:
        w = seed_world(su)
        self._fill(su, w)
        for role in ("app_worker_send", "app_worker_sync", "app_outbox_relay"):
            for table in (
                "message_generations",
                "message_generation_attempts",
                "personalization_research_cache",
                "personalization_usage_daily",
                "personalization_previews",
                "campaign_personalization_approvals",
            ):
                denied(uri, role, f"SELECT 1 FROM public.{table}", ws=w.ws)
        # The API never sees fetched third-party text, and never writes jobs.
        denied(
            uri,
            "app_api",
            "SELECT 1 FROM public.personalization_research_cache",
            user=w.manager,
            ws=w.ws,
        )
        denied(
            uri,
            "app_api",
            "INSERT INTO message_generations (workspace_id, campaign_id, sequence_id, step_id,"
            " enrollment_id, message_id, max_attempts, next_attempt_at) VALUES "
            "(%s,%s,%s,%s,%s,%s,3,now())",
            [
                w.ws,
                w.campaign_id,
                w.sequence_id,
                w.step1,
                w.enrollment_id,
                w.message1_id,
            ],
            user=w.manager,
            ws=w.ws,
        )

    def test_scheduler_discovery_is_read_only_and_limited_to_generations(
        self, uri, su
    ) -> None:
        w = seed_world(su)
        add_generation(su, w)
        with acting(uri, "app_scheduler") as conn:
            assert (
                conn.execute("SELECT count(*) FROM message_generations").fetchone()[0]
                >= 1
            )
        denied(uri, "app_scheduler", "UPDATE message_generations SET state='FAILED'")
        denied(uri, "app_scheduler", "SELECT 1 FROM personalization_research_cache")
        denied(uri, "app_scheduler", "SELECT 1 FROM personalization_previews")

    def test_worker_has_no_delete_on_jobs_or_approvals_and_only_expired_cache_rows(
        self, uri, su
    ) -> None:
        w = seed_world(su)
        self._fill(su, w)
        denied(uri, "app_worker_general", "DELETE FROM message_generations", ws=w.ws)
        denied(
            uri,
            "app_worker_general",
            "DELETE FROM campaign_personalization_approvals",
            ws=w.ws,
        )
        with acting(uri, "app_worker_general", ws=w.ws) as conn:
            # The RLS policy only exposes EXPIRED cache rows to DELETE.
            assert (
                conn.execute(
                    "DELETE FROM personalization_research_cache RETURNING id"
                ).fetchall()
                == []
            )


class TestApprovalsAndPreviews:
    def _preview(self, conn, w: World, *, created_by, batch=None, state=None):
        # `state` is deliberately not in the API role's INSERT grant: a new sample
        # is always PENDING, and a user cannot forge a finished one.
        columns = "workspace_id, campaign_id, batch_id, audience_member_id, lead_id, step_id, config_digest, created_by, expires_at"
        values = [
            w.ws,
            w.campaign_id,
            batch or uuid.uuid4(),
            w.member_ids[0],
            w.lead_ids[0],
            w.step1,
            "e" * 64,
            created_by,
        ]
        marks = "%s,%s,%s,%s,%s,%s,%s,%s, now() + interval '7 days'"
        if state is not None:
            columns += ", state"
            marks += ", %s"
            values.append(state)
        conn.execute(
            f"INSERT INTO personalization_previews ({columns}) VALUES ({marks})", values
        )

    def test_a_member_can_request_samples_only_as_themselves_and_pending(
        self, uri, su
    ) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        with acting(uri, "app_api", user=w.member, ws=w.ws) as conn:
            self._preview(conn, w, created_by=w.member)
        with acting(uri, "app_api", user=w.member, ws=w.ws) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                self._preview(conn, w, created_by=w.manager)  # impersonation
        with acting(uri, "app_api", user=w.member, ws=w.ws) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                self._preview(
                    conn, w, created_by=w.member, state="OK"
                )  # cannot forge results

    def test_samples_cannot_be_requested_once_the_campaign_is_running(
        self, uri, su
    ) -> None:
        w = seed_world(su)  # RUNNING
        with acting(uri, "app_api", user=w.manager, ws=w.ws) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                self._preview(conn, w, created_by=w.manager)

    def test_only_a_manager_can_approve_and_only_as_themselves(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        sql = (
            "INSERT INTO campaign_personalization_approvals "
            "(workspace_id, campaign_id, config_digest, batch_id, approved_by) "
            "VALUES (%s,%s,%s,%s,%s)"
        )
        args = [w.ws, w.campaign_id, "f" * 64, uuid.uuid4()]
        denied(uri, "app_api", sql, [*args, w.member], user=w.member, ws=w.ws)  # MEMBER
        denied(
            uri, "app_api", sql, [*args, w.owner], user=w.manager, ws=w.ws
        )  # not self
        with acting(uri, "app_api", user=w.manager, ws=w.ws) as conn:
            conn.execute(sql, [*args, w.manager])
            conn.execute(
                sql
                + " ON CONFLICT (workspace_id, campaign_id, config_digest) DO NOTHING",
                [*args, w.manager],
            )  # retry is a no-op

    def test_approvals_are_immutable_and_unique_per_digest(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        su.execute(
            """INSERT INTO campaign_personalization_approvals
                      (workspace_id, campaign_id, config_digest, batch_id, approved_by)
                      VALUES (%s,%s,%s,%s,%s)""",
            [w.ws, w.campaign_id, "1" * 64, uuid.uuid4(), w.manager],
        )
        denied(
            uri,
            "app_api",
            "UPDATE campaign_personalization_approvals SET config_digest=%s",
            ["2" * 64],
            user=w.manager,
            ws=w.ws,
        )
        denied(
            uri,
            "app_api",
            "DELETE FROM campaign_personalization_approvals",
            user=w.manager,
            ws=w.ws,
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            su.execute(
                """INSERT INTO campaign_personalization_approvals
                          (workspace_id, campaign_id, config_digest, batch_id, approved_by)
                          VALUES (%s,%s,%s,%s,%s)""",
                [w.ws, w.campaign_id, "1" * 64, uuid.uuid4(), w.manager],
            )

    def test_no_approval_once_the_campaign_left_draft(self, uri, su) -> None:
        w = seed_world(su)
        denied(
            uri,
            "app_api",
            "INSERT INTO campaign_personalization_approvals "
            "(workspace_id, campaign_id, config_digest, batch_id, approved_by) "
            "VALUES (%s,%s,%s,%s,%s)",
            [w.ws, w.campaign_id, "9" * 64, uuid.uuid4(), w.manager],
            user=w.manager,
            ws=w.ws,
        )

    def test_preview_result_shape_is_enforced_by_the_database(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False)
        with acting(uri, "app_api", user=w.member, ws=w.ws) as conn:
            self._preview(conn, w, created_by=w.member)
            conn.commit()
        # A worker cannot mark a preview OK without a subject and body.
        with acting(uri, "app_worker_general", ws=w.ws) as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "UPDATE personalization_previews SET state='OK', completed_at=now()"
                )


class TestAttemptLedger:
    def test_an_attempt_leaves_reserved_once_and_is_then_immutable(
        self, uri, su
    ) -> None:
        w = seed_world(su)
        gid = add_generation(su, w)
        su.execute(
            "INSERT INTO message_generation_attempts (workspace_id, generation_id, attempt_no) "
            "VALUES (%s,%s,1)",
            [w.ws, gid],
        )
        with acting(uri, "app_worker_general", ws=w.ws) as conn:
            conn.execute(
                "UPDATE message_generation_attempts SET outcome='ACCEPTED', finished_at=now()"
            )
            conn.commit()
        with acting(uri, "app_worker_general", ws=w.ws) as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "UPDATE message_generation_attempts SET outcome='REJECTED_VALIDATION'"
                )
        # No content or lead data columns exist on the ledger.
        cols = {
            r[0]
            for r in su.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='message_generation_attempts'"
            ).fetchall()
        }
        assert not {"content", "subject", "body", "body_html", "email"} & cols

    def test_generation_constraints(self, su) -> None:
        w = seed_world(su)
        gid = add_generation(su, w)
        with pytest.raises(psycopg.errors.CheckViolation):  # attempts beyond the budget
            su.execute(
                "UPDATE message_generations SET attempt_count=9 WHERE id=%s", [gid]
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # FAILED needs a code
            su.execute(
                "UPDATE message_generations SET state='FAILED', completed_at=now() WHERE id=%s",
                [gid],
            )
        with pytest.raises(psycopg.errors.CheckViolation):  # lease only while PENDING
            su.execute(
                "UPDATE message_generations SET state='SUPERSEDED', completed_at=now(), "
                "lease_owner='x', lease_expires_at=now() WHERE id=%s",
                [gid],
            )
        with pytest.raises(psycopg.errors.UniqueViolation):  # one job per message
            add_generation(su, w)


# ---------------------------------------------------------------------------
# worker SQL
# ---------------------------------------------------------------------------


class TestPlannerSql:
    def test_mark_generation_pending_keeps_the_message_planned_and_is_idempotent(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        due = datetime.now(UTC) + timedelta(hours=3)
        kwargs = dict(
            workspace_id=w.ws,
            campaign_id=w.campaign_id,
            sequence_id=w.sequence_id,
            step_id=w.step1,
            enrollment_id=w.enrollment_id,
            message_id=w.message1_id,
            due_at=due,
            anchor_at=datetime.now(UTC),
            next_attempt_at=due - timedelta(hours=1),
            max_attempts=3,
        )
        for expected in (True, False):  # a replayed chunk creates no second job
            session = worker_session(session_factory, w.ws)
            assert (
                CampaignWorkerRepository(session).mark_generation_pending(**kwargs)
                is expected
            )
            session.commit()
            session.close()
        status, due_at, rendered = one(
            su,
            "SELECT status, due_at, rendered_at FROM messages WHERE id=%s",
            w.message1_id,
        )
        assert status == "PLANNED" and rendered is None and due_at is not None
        assert one(
            su,
            "SELECT count(*) FROM message_generations WHERE message_id=%s",
            w.message1_id,
        ) == (1,)

    def test_render_chunk_query_variants(self, session_factory, su) -> None:
        w = seed_world(su)
        session = worker_session(session_factory, w.ws)
        repo = CampaignWorkerRepository(session)
        standard = repo.fetch_planned_messages_chunk(
            workspace_id=w.ws, campaign_id=w.campaign_id, limit=10
        )
        assert [str(r["id"]) for r in standard] == [str(w.message1_id)]
        hyper = repo.fetch_planned_messages_chunk(
            workspace_id=w.ws, campaign_id=w.campaign_id, limit=10, hyper=True
        )
        row = hyper[0]
        assert str(row["enrollment_id"]) == str(w.enrollment_id)
        assert str(row["step_id"]) == str(w.step1)
        session.rollback()
        session.close()
        # Once a job exists the hyper variant no longer returns it (so the chunked
        # RENDER loop terminates) although the message is still PLANNED.
        add_generation(su, w)
        session = worker_session(session_factory, w.ws)
        repo = CampaignWorkerRepository(session)
        assert (
            repo.fetch_planned_messages_chunk(
                workspace_id=w.ws, campaign_id=w.campaign_id, limit=10, hyper=True
            )
            == []
        )
        assert (
            len(
                repo.fetch_planned_messages_chunk(
                    workspace_id=w.ws, campaign_id=w.campaign_id, limit=10
                )
            )
            == 1
        )
        session.close()

    def test_contexts_expose_the_campaign_type(self, session_factory, su) -> None:
        w = seed_world(su)
        session = worker_session(session_factory, w.ws)
        repo = CampaignWorkerRepository(session)
        activation = repo.get_activation_context(
            workspace_id=w.ws,
            campaign_id=w.campaign_id,
            activation_id=one(
                su, "SELECT activation_id FROM campaigns WHERE id=%s", w.campaign_id
            )[0],
        )
        progression = repo.get_progression_context(
            workspace_id=w.ws, campaign_id=w.campaign_id
        )
        assert activation["campaign_type"] == "HYPER_PERSONALIZED"
        assert progression["campaign_type"] == "HYPER_PERSONALIZED"
        session.close()

    def test_progression_creates_the_follow_up_as_a_pending_generation(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        accepted = datetime.now(UTC) - timedelta(hours=1)
        su.execute("SET session_replication_role = replica")
        su.execute(
            "UPDATE messages SET status='SENT', accepted_at=%s, due_at=%s, anchor_at=%s, "
            "rendered_at=now(), content_subject='s', content_body_html='<p>b</p>', "
            "content_digest=%s, frozen_destination='lead0@target0.test', "
            "frozen_sender_address='sender@acme.test' WHERE id=%s",
            [accepted, accepted, accepted, "a" * 64, w.message1_id],
        )
        su.execute("SET session_replication_role = origin")
        session = worker_session(session_factory, w.ws)
        service = ProgressionService(
            CampaignWorkerRepository(session),
            is_suppressed=lambda *_: False,
            personalization_lead_seconds=1800,
            personalization_max_attempts=4,
        )
        summary = service.advance_batch(
            workspace_id=w.ws, campaign_id=w.campaign_id, limit=10
        )
        session.commit()
        session.close()
        assert summary.advanced == 1
        status, content, due, anchor = one(
            su,
            "SELECT status, content_subject, due_at, anchor_at FROM messages "
            "WHERE enrollment_id=%s AND step_id=%s",
            w.enrollment_id,
            w.step2,
        )
        assert status == "PLANNED" and content is None  # not rendered, not sendable
        assert abs((anchor - accepted).total_seconds()) < 1
        assert due > anchor  # accepted + 2 days, projected into the window
        gen = one(
            su,
            "SELECT state, max_attempts, next_attempt_at, %s::timestamptz - next_attempt_at "
            "FROM message_generations WHERE step_id=%s",
            due,
            w.step2,
        )
        assert gen[0] == "PENDING" and gen[1] == 4
        assert gen[3] == timedelta(seconds=1800)
        assert one(
            su,
            "SELECT next_step_id FROM campaign_enrollments WHERE id=%s",
            w.enrollment_id,
        ) == (w.step2,)


class TestGenerationSql:
    def test_end_to_end_generation_writes_an_immutable_send_ready_snapshot(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        gid = add_generation(su, w)
        runner, model = make_runner(session_factory, w)
        summary = run_chunk(runner, w)
        assert summary.generated == 1 and model.calls == 1

        row = one(
            su,
            """SELECT status, renderer_version, content_subject, content_body_html,
                                content_digest, frozen_destination, frozen_sender_address,
                                rendered_at, due_at FROM messages WHERE id=%s""",
            w.message1_id,
        )
        status, version, subject, body, digest, dest, sender, rendered_at, due_at = row
        assert status == "SCHEDULED" and version == 2 and rendered_at is not None
        assert digest == compute_content_digest(
            subject, body, 2
        )  # what the send gate recomputes
        assert dest == "lead0@target0.test" and sender == "sender@acme.test"
        assert 9 <= due_at.astimezone(UTC).hour < 17 and due_at >= datetime.now(
            UTC
        ) - timedelta(minutes=1)
        assert "<p>" in body
        gen = one(
            su,
            "SELECT state, lease_owner, provider, model, prompt_version, fallback_used, "
            "angle, jsonb_array_length(personalization_facts), completed_at IS NOT NULL "
            "FROM message_generations WHERE id=%s",
            gid,
        )
        assert gen == (
            "SUCCEEDED",
            None,
            "fake",
            "fake-model",
            "hp-1",
            False,
            "fake personalization",
            1,
            True,
        )
        assert one(
            su,
            "SELECT outcome, input_tokens, output_tokens FROM message_generation_attempts "
            "WHERE generation_id=%s",
            gid,
        ) == ("ACCEPTED", 100, 80)
        assert one(
            su,
            "SELECT units, input_tokens FROM personalization_usage_daily "
            "WHERE workspace_id=%s AND kind='GENERATION'",
            w.ws,
        ) == (1, 100)

    def test_snapshot_cannot_be_rewritten_after_generation(
        self, uri, session_factory, su
    ) -> None:
        w = seed_world(su)
        add_generation(su, w)
        runner, _ = make_runner(session_factory, w)
        run_chunk(runner, w)
        denied(
            uri,
            "app_worker_general",
            "UPDATE messages SET content_subject='tampered' WHERE id=%s",
            [w.message1_id],
            ws=w.ws,
            error=psycopg.errors.CheckViolation,
        )

    def test_two_workers_racing_generate_exactly_once(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        add_generation(su, w)
        model = FakeModel()
        calls = []
        gate = threading.Barrier(2)
        original = model.generate

        def slow_generate(request):
            calls.append(1)
            threading.Event().wait(0.4)  # hold the lease while the other worker claims
            return original(request)

        model.generate = slow_generate  # type: ignore[method-assign]
        results = []

        def worker(name):
            runner, _ = make_runner(session_factory, w, model)
            gate.wait()
            results.append(run_chunk(runner, w, owner=name))

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(calls) == 1  # the lease serialized them: one billed model call
        assert sum(r.generated for r in results) == 1
        assert one(
            su,
            "SELECT state FROM message_generations WHERE message_id=%s",
            w.message1_id,
        ) == ("SUCCEEDED",)
        attempts = one(
            su,
            "SELECT count(*) FROM message_generation_attempts WHERE workspace_id=%s",
            w.ws,
        )[0]
        assert attempts == 1  # only the winner ever reserved an attempt

    def test_reply_during_generation_leaves_the_message_untouched(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        add_generation(su, w)
        model = FakeModel()
        original = model.default_output

        def reply_lands(request):
            su.execute("SET session_replication_role = replica")
            su.execute(
                "UPDATE campaign_enrollments SET state='STOPPED', stop_reason='REPLIED', "
                "next_step_id=NULL, next_sequence_position=NULL WHERE id=%s",
                [w.enrollment_id],
            )
            su.execute("SET session_replication_role = origin")
            return original(request)

        model._script = [reply_lands]
        runner, _ = make_runner(session_factory, w, model)
        summary = run_chunk(runner, w)
        assert summary.superseded == 1 and summary.generated == 0
        assert one(
            su,
            "SELECT status, content_subject FROM messages WHERE id=%s",
            w.message1_id,
        ) == ("PLANNED", None)
        assert one(
            su,
            "SELECT state FROM message_generations WHERE message_id=%s",
            w.message1_id,
        ) == ("SUPERSEDED",)
        assert one(
            su,
            "SELECT units FROM personalization_usage_daily WHERE workspace_id=%s "
            "AND kind='GENERATION'",
            w.ws,
        ) == (0,)  # refunded

    def test_exhausted_attempts_fail_the_message_and_never_send(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        gid = add_generation(su, w, max_attempts=2)
        su.execute(
            "UPDATE message_generations SET attempt_count=2, last_failure_codes="
            "ARRAY['unsupported_number'], lease_owner='dead', "
            "lease_expires_at=now() - interval '1 hour' WHERE id=%s",
            [gid],
        )
        su.execute(
            "INSERT INTO message_generation_attempts (workspace_id, generation_id, attempt_no)"
            " VALUES (%s,%s,1)",
            [w.ws, gid],
        )
        runner, model = make_runner(session_factory, w)
        summary = run_chunk(runner, w)
        assert summary.exhausted == 1 and model.calls == 0
        assert one(
            su,
            "SELECT status, terminal_reason, content_subject FROM messages WHERE id=%s",
            w.message1_id,
        ) == ("FAILED", "personalization_failed:attempts_exhausted", None)
        assert one(
            su, "SELECT state, failure_code FROM message_generations WHERE id=%s", gid
        ) == ("FAILED", "attempts_exhausted")
        assert one(
            su,
            "SELECT outcome FROM message_generation_attempts WHERE generation_id=%s",
            gid,
        ) == ("ABANDONED",)

    def test_a_paused_campaign_is_not_claimed(self, session_factory, su) -> None:
        w = seed_world(su)
        add_generation(su, w)
        su.execute(
            "UPDATE campaigns SET status='PAUSED', previous_status='RUNNING' WHERE id=%s",
            [w.campaign_id],
        )
        runner, model = make_runner(session_factory, w)
        assert run_chunk(runner, w).claimed == 0 and model.calls == 0

    def test_daily_cap_is_atomic_under_concurrency(self, session_factory, su) -> None:
        w = seed_world(su)
        granted = []
        gate = threading.Barrier(8)

        def reserve():
            gate.wait()
            with GenerationDb(session_factory, w.ws).transaction() as repo:
                granted.append(
                    repo.reserve_usage(
                        workspace_id=w.ws,
                        day=datetime.now(UTC).date(),
                        kind="GENERATION",
                        cap=5,
                    )
                )

        threads = [threading.Thread(target=reserve) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sum(granted) == 5
        assert one(
            su,
            "SELECT units FROM personalization_usage_daily WHERE workspace_id=%s",
            w.ws,
        ) == (5,)

    def test_previous_email_and_facts_feed_the_follow_up(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        gid = add_generation(su, w)
        su.execute("SET session_replication_role = replica")
        su.execute(
            "UPDATE messages SET status='SENT', accepted_at=now() - interval '2 days', "
            "rendered_at=now(), content_subject='First subject', "
            "content_body_html='<p>First body</p>', content_digest=%s, "
            "frozen_destination='lead0@target0.test', frozen_sender_address='s@a.test' "
            "WHERE id=%s",
            ["a" * 64, w.message1_id],
        )
        su.execute(
            "UPDATE message_generations SET state='SUCCEEDED', completed_at=now(), "
            "angle='the angle', personalization_facts=%s::jsonb WHERE id=%s",
            ['[{"id":"F1","source":"LEAD","text":"Company: Acme"}]', gid],
        )
        su.execute("SET session_replication_role = origin")
        with GenerationDb(session_factory, w.ws).transaction() as repo:
            previous = repo.load_previous_email(
                workspace_id=w.ws, enrollment_id=w.enrollment_id, before_position=3
            )
        assert previous["content_subject"] == "First subject"
        assert previous["angle"] == "the angle"
        assert previous["personalization_facts"][0]["text"] == "Company: Acme"


class TestResearchCacheSql:
    def test_cache_is_per_workspace_expires_and_purges_only_expired(
        self, session_factory, su
    ) -> None:
        a, b = seed_world(su), seed_world(su)
        with GenerationDb(session_factory, a.ws).transaction() as repo:
            repo.upsert_cache(
                workspace_id=a.ws,
                source="WEBSITE",
                url_hash="1" * 64,
                normalized_url="https://x.test/",
                status="OK",
                extracted_text="hello",
                content_sha256=None,
                http_status=None,
                ttl_seconds=3600,
            )
            repo.upsert_cache(
                workspace_id=a.ws,
                source="WEBSITE",
                url_hash="1" * 64,
                normalized_url="https://x.test/",
                status="BLOCKED",
                extracted_text=None,
                content_sha256=None,
                http_status=None,
                ttl_seconds=3600,
            )  # upsert replaces the row
        with GenerationDb(session_factory, a.ws).transaction() as repo:
            hit = repo.get_cache(workspace_id=a.ws, source="WEBSITE", url_hash="1" * 64)
            assert hit["status"] == "BLOCKED" and hit["extracted_text"] is None
        with GenerationDb(session_factory, b.ws).transaction() as repo:
            assert (
                repo.get_cache(workspace_id=b.ws, source="WEBSITE", url_hash="1" * 64)
                is None
            )
        su.execute(
            "UPDATE personalization_research_cache SET fetched_at=now() - interval '2 hours', "
            "expires_at=now() - interval '1 hour' WHERE workspace_id=%s",
            [a.ws],
        )
        with GenerationDb(session_factory, a.ws).transaction() as repo:
            assert (
                repo.get_cache(workspace_id=a.ws, source="WEBSITE", url_hash="1" * 64)
                is None
            )
            assert repo.purge_expired(workspace_id=a.ws) == 1

    def test_a_reserved_fetch_budget_unit_is_per_kind_and_capped(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        day = datetime.now(UTC).date()
        with GenerationDb(session_factory, w.ws).transaction() as repo:
            assert [
                repo.reserve_usage(workspace_id=w.ws, day=day, kind="FETCH", cap=2)
                for _ in range(3)
            ] == [True, True, False]


class TestSchedulerDiscovery:
    def _find(self, session_factory):
        session = session_factory()
        try:
            return SchedulerRepository(session).find_campaigns_with_due_generation(
                after_id=None, limit=1000
            )
        finally:
            session.rollback()
            session.close()

    def test_lists_only_running_campaigns_with_due_unleased_jobs(
        self, session_factory, su
    ) -> None:
        due, later, paused, leased = (seed_world(su) for _ in range(4))
        add_generation(su, due)
        add_generation(su, later, due_in=timedelta(hours=2))
        add_generation(su, paused)
        su.execute(
            "UPDATE campaigns SET status='PAUSED', previous_status='RUNNING' WHERE id=%s",
            [paused.campaign_id],
        )
        gid = add_generation(su, leased)
        su.execute(
            "UPDATE message_generations SET lease_owner='w', "
            "lease_expires_at=now() + interval '5 minutes', attempt_count=1 WHERE id=%s",
            [gid],
        )
        found = {(str(ws), str(cid)) for ws, cid in self._find(session_factory)}
        assert (str(due.ws), str(due.campaign_id)) in found
        for other in (later, paused, leased):
            assert (str(other.ws), str(other.campaign_id)) not in found

    def test_exhausted_unleased_jobs_are_surfaced_so_they_get_failed(
        self, session_factory, su
    ) -> None:
        w = seed_world(su)
        gid = add_generation(su, w, due_in=timedelta(hours=5), max_attempts=2)
        su.execute("UPDATE message_generations SET attempt_count=2 WHERE id=%s", [gid])
        assert (str(w.ws), str(w.campaign_id)) in {
            (str(a), str(b)) for a, b in self._find(session_factory)
        }


# ---------------------------------------------------------------------------
# API service against RLS: objective -> samples -> approval
# ---------------------------------------------------------------------------


def test_objective_samples_and_approval_flow_under_row_level_security(
    uri, session_factory, su
) -> None:
    from unittest.mock import MagicMock, patch

    from app.core.config import Settings

    w = seed_world(su, running=False, enroll_first=False, config={})
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://x",
        supabase_url="https://x.supabase.co",
        personalization_enabled=True,
        personalization_model="test-model",
        personalization_daily_preview_cap=50,
    )

    def api(
        role: str, user: uuid.UUID
    ) -> tuple[PersonalizationApiService, Session, WorkspaceContext]:
        session = session_factory()
        session.execute(text("SET LOCAL ROLE app_api"))
        set_transaction_context(session, user_id=user, workspace_id=w.ws)
        context = WorkspaceContext(workspace_id=w.ws, user_id=user, role_code=role)
        return PersonalizationApiService(session, settings), session, context

    # 1. a member saves the objective
    service, session, ctx = api("MEMBER", w.member)
    state = service.put_config(
        ctx, w.campaign_id, PersonalizationConfigIn(config=CONFIG)
    )
    session.commit()
    session.close()
    assert state.config is not None and state.approval.status == "NONE"

    # 2. a member requests samples for two leads (worker dispatch is captured)
    batch = uuid.uuid4()
    service, session, ctx = api("MEMBER", w.member)
    producer = MagicMock()
    with patch("app.services.task_dispatch.get_task_producer", return_value=producer):
        out = service.create_previews(
            ctx,
            w.campaign_id,
            PreviewCreateIn(batch_id=batch, audience_member_ids=w.member_ids[:3]),
        )
    session.close()
    assert len(out.items) == 3 * 2 and not out.complete
    producer.send_task.assert_called_once()
    digest = out.current_digest

    # 4. the worker generates the samples (fake model, real SQL)
    db = GenerationDb(session_factory, w.ws)
    runner = PreviewRunner(
        db=db,
        service=PersonalizationService(
            model=FakeModel(), research=FakeResearch(), min_facts=2
        ),
        rpm=AllowAllLimiter(),
        max_attempts=2,
        daily_preview_cap=50,
    )
    summary = runner.run_batch(
        workspace_id=w.ws, campaign_id=w.campaign_id, batch_id=batch
    )
    assert summary.ok == 6 and summary.failed == 0

    # 5. the generated content is readable through the API, per tenant
    service, session, ctx = api("MEMBER", w.member)
    latest = service.latest_batch(ctx, w.campaign_id)
    session.close()
    assert latest is not None and latest.all_ok and not latest.stale
    assert all(i.subject and i.body_html and "<p>" in i.body_html for i in latest.items)
    assert {i.step_position for i in latest.items} == {1, 3}

    # 5b. a member cannot approve: the route denies it first, and even a direct
    # service call is refused by row-level security on the insert
    service, session, ctx = api("MEMBER", w.member)
    with pytest.raises(DBAPIError):
        service.approve(
            ctx, w.campaign_id, ApproveIn(batch_id=batch, config_digest=digest)
        )
    session.rollback()
    session.close()

    # 6. a manager approves; the approval is bound to the digest
    service, session, ctx = api("MANAGER", w.manager)
    approved = service.approve(
        ctx, w.campaign_id, ApproveIn(batch_id=batch, config_digest=digest)
    )
    session.commit()
    session.close()
    assert approved.approval.status == "APPROVED"

    # 7. editing the objective makes it stale without any write
    service, session, ctx = api("MEMBER", w.member)
    changed = {**CONFIG, "cta": "Reply with a time that suits you"}
    edited = service.put_config(
        ctx, w.campaign_id, PersonalizationConfigIn(config=changed)
    )
    session.commit()
    session.close()
    assert edited.approval.status == "STALE" and edited.config_digest != digest

    # 8. another tenant cannot see any of it
    other = seed_world(su, running=False, enroll_first=False)
    session = session_factory()
    session.execute(text("SET LOCAL ROLE app_api"))
    set_transaction_context(session, user_id=other.manager, workspace_id=other.ws)
    stranger = PersonalizationApiService(session, settings)
    octx = WorkspaceContext(
        workspace_id=other.ws, user_id=other.manager, role_code="MANAGER"
    )
    from app.core.errors import AppError

    with pytest.raises(AppError) as info:
        stranger.get_batch(octx, w.campaign_id, batch)  # someone else's campaign id
    assert info.value.status_code == 404
    session.close()
