"""The go-live preflight: its environment checks, and its database checks on a
real PostgreSQL both before and after migration 0038."""

# ruff: noqa: E402, E501
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.preflight import Check, database_checks, render, settings_checks

READY_ENV = dict(
    database_url="postgresql+psycopg://u:p@localhost/db",
    redis_url="redis://localhost:6379/0",
    supabase_url="https://x.supabase.co",
    app_env="production",
    sending_worker_enabled=True,
    scheduler_enabled=True,
    unsubscribe_base_url="https://app.example.org",
    unsubscribe_signing_key="k" * 32,
    mailbox_encryption_key="e" * 64,
    platform_operator_emails="founder@corp.io",
    sequence_progression_enabled=True,
)


def checks(**overrides) -> dict[str, Check]:
    settings = Settings(**{**READY_ENV, **overrides})
    return {c.name: c for c in settings_checks(settings)}


class TestSettingsChecks:
    def test_a_ready_environment_has_no_failures(self):
        result = checks()

        assert [c.name for c in result.values() if c.status == "FAIL"] == []

    @pytest.mark.parametrize(
        ("override", "name"),
        [
            ({"sending_worker_enabled": False}, "SENDING_WORKER_ENABLED"),
            ({"scheduler_enabled": False}, "SCHEDULER_ENABLED"),
            ({"unsubscribe_signing_key": "", "sending_worker_enabled": False}, "Unsubscribe link"),
            ({"unsubscribe_base_url": "", "sending_worker_enabled": False}, "Unsubscribe link"),
            ({"unsubscribe_base_url": "not a url", "sending_worker_enabled": False}, "Unsubscribe link"),
            ({"mailbox_encryption_key": ""}, "MAILBOX_ENCRYPTION_KEY"),
            (
                {"platform_operator_emails": "operator@example.com,admin@example.com", "sending_worker_enabled": False},
                "PLATFORM_OPERATOR_EMAILS",
            ),
        ],
    )
    def test_each_blocker_fails(self, override, name):
        assert checks(**override)[name].status == "FAIL"

    def test_follow_ups_off_is_a_warning_not_a_failure(self):
        result = checks(sequence_progression_enabled=False)["SEQUENCE_PROGRESSION_ENABLED"]

        assert result.status == "WARN" and "follow-ups" in result.detail

    def test_a_non_production_environment_is_flagged(self):
        assert checks(app_env="local")["APP_ENV"].status == "WARN"

    def test_one_placeholder_among_real_operators_still_fails(self):
        result = checks(
            platform_operator_emails="founder@corp.io, admin@example.com",
            sending_worker_enabled=False,
        )["PLATFORM_OPERATOR_EMAILS"]

        assert result.status == "FAIL" and "admin@example.com" in result.detail

    def test_the_report_has_one_line_per_check(self):
        result = list(checks().values())

        assert len(render(result).splitlines()) == len(result)


class TestStartupGuards:
    def test_production_with_sending_refuses_placeholder_operators(self):
        with pytest.raises(ValueError, match="PLATFORM_OPERATOR_EMAILS"):
            Settings(**{**READY_ENV, "platform_operator_emails": "operator@example.com"})

    def test_production_without_sending_is_not_taken_down_by_it(self):
        settings = Settings(
            **{
                **READY_ENV,
                "sending_worker_enabled": False,
                "platform_operator_emails": "operator@example.com",
            }
        )

        assert settings.placeholder_operator_emails == ["operator@example.com"]

    def test_real_operators_are_accepted_with_sending(self):
        assert Settings(**READY_ENV).placeholder_operator_emails == []


pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.support.real_pg import apply_range, throwaway_database
from tests.support.real_seed import seed_world


def by_name(connection) -> dict[str, Check]:
    try:
        return {c.name: c for c in database_checks(connection)}
    finally:
        connection.rollback()


def connect_a_mailbox(uri: str) -> None:
    with psycopg.connect(uri, autocommit=True) as su:
        world = seed_world(su, running=False, members=1)
        # Replica mode skips the foreign key to a stored credential this check does not need.
        su.execute("SET session_replication_role = replica")
        su.execute(
            "UPDATE public.mailboxes SET connection_state='CONNECTED', connected_generation=current_connection_generation, provider_account_id='acct-'||id::text WHERE id=%s",
            [world.mailbox_id],
        )
        su.execute("SET session_replication_role = origin")


@pytest.fixture(scope="module")
def database():
    with throwaway_database(up_to=37) as uri:
        connect_a_mailbox(uri)
        yield uri


@pytest.fixture(scope="module")
def engine(database):
    engine = create_engine("postgresql+psycopg://" + database.split("://", 1)[1], future=True)
    yield engine
    engine.dispose()


class TestDatabaseChecks:
    """One database, in the order a real deployment goes: 0037 only, then 0038.
    The tests are numbered because each builds on the state the one before left."""

    def test_a_01_before_0038_the_preflight_reports_what_is_missing(self, engine):
        with engine.connect() as connection:
            result = by_name(connection)

        assert result["Migration 0038"].status == "FAIL"
        assert result["Database grants"].status == "FAIL"
        # Exactly what 0038 adds; the older grants (0005) are already there.
        assert "releasing a hold" in result["Database grants"].detail
        assert "default limit on connect" in result["Database grants"].detail
        assert "setting mailbox limits" not in result["Database grants"].detail
        assert result["Mailbox limits"].status == "FAIL"
        assert "1 mailbox" in result["Mailbox limits"].detail

    def test_a_02_after_0038_everything_it_checks_is_in_place(self, database, engine):
        apply_range(database, 38, 38)

        with engine.connect() as connection:
            result = by_name(connection)

        assert result["Migration 0038"].status == "OK"
        assert result["Database grants"].status == "OK", result["Database grants"].detail
        assert result["Mailbox limits"].status == "OK"
        assert result["Mailboxes"].detail == "1 connected"
        assert result["Safety holds"].status == "OK"

    def test_a_03_rate_control_is_reported_not_assumed(self, engine):
        with engine.connect() as connection:
            result = by_name(connection)

        # A new database has not been bootstrapped by the rate controller, so the
        # preflight must not call it ready.
        assert result["Rate control"].status == "FAIL"
        assert "rate controller" in result["Rate control"].detail

    def test_a_04_a_mailbox_without_a_limit_is_a_failure(self, database, engine):
        connect_a_mailbox(database)  # created after 0038, so it has no limit row

        with engine.connect() as connection:
            result = by_name(connection)

        assert result["Mailbox limits"].status == "FAIL"
        assert "1 mailbox" in result["Mailbox limits"].detail

    def test_a_05_it_changes_nothing(self, database, engine):
        counts = (
            "SELECT (SELECT count(*) FROM public.tenant_rate_policies), "
            "(SELECT count(*) FROM public.mailboxes), "
            "(SELECT count(*) FROM public.safety_holds)"
        )
        with psycopg.connect(database, autocommit=True) as su:
            before = su.execute(counts).fetchone()

        with engine.connect() as connection:
            by_name(connection)

        with psycopg.connect(database, autocommit=True) as su:
            assert su.execute(counts).fetchone() == before

    def test_a_06_the_read_only_guard_is_real(self, engine):
        """Inside the preflight's transaction even a stray write would be refused."""
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(DBAPIError):
                connection.execute(text("CREATE TABLE public.preflight_probe (x int)"))
            connection.rollback()
