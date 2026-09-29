"""Migration 0035 -- app_delete_campaign / app_delete_mailbox -- against a REAL PostgreSQL.

Same throwaway-pgserver approach as test_erasure_real_db.py: the actual SQL runs as
the real `app_api` role with RLS, grants and guard triggers fully enforced. Nothing
touches the production-linked Supabase project and no email is sent.

What is proven here: an ARCHIVED campaign that really sent (attempts, capacity debit,
events, a reply, generations, previews, a planning job) is physically gone, a mailbox
with sending history is physically gone, leads / addresses / suppressions survive,
in-flight and recent sends are refused without changing anything, other tenants are
untouched, and only ADMIN/OWNER may call either command.
"""

# ruff: noqa: E402, E501
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from tests.support.real_pg import throwaway_database
from tests.support.real_seed import World, _insert, seed_world
from tests.test_erasure_real_db import archive, call, count, expect, scalar, sent_world

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def uri():
    with throwaway_database() as value:
        yield value


@pytest.fixture()
def su(uri):
    conn = psycopg.connect(uri, autocommit=True)
    yield conn
    conn.close()


def age_attempts(su, w: World, days: int = 2) -> None:
    """Move every send attempt of the world out of the 25-hour recent-send window."""
    su.execute("SET session_replication_role = replica")
    su.execute(
        """UPDATE public.message_attempts
           SET started_at = now() - make_interval(days => %s),
               completed_at = now() - make_interval(days => %s) + interval '1 minute',
               authorization_deadline = now() - make_interval(days => %s) + interval '5 minutes'
           WHERE workspace_id = %s""",
        [days, days, days, w.ws],
    )
    su.execute("SET session_replication_role = origin")


def add_debit(su, w: World) -> None:
    attempt = scalar(su, "SELECT id FROM public.message_attempts WHERE message_id=%s", w.message1_id)
    su.execute("SET session_replication_role = replica")
    _insert(su, "capacity_debits", workspace_id=w.ws, attempt_id=attempt, reservation_id="r1", unit="EMAIL", quantity=1)
    su.execute("SET session_replication_role = origin")


def add_planning_job(su, w: World) -> None:
    activation, seq = su.execute(
        "SELECT activation_id, activated_sequence_id FROM public.campaigns WHERE id=%s", [w.campaign_id]
    ).fetchone()
    su.execute("SET session_replication_role = replica")
    _insert(
        su, "campaign_planning_jobs", workspace_id=w.ws, campaign_id=w.campaign_id, activation_id=activation,
        sequence_id=seq, phase="ENROLL", state="READY",
    )
    su.execute("SET session_replication_role = origin")


def disconnect(su, w: World) -> None:
    su.execute(
        "UPDATE public.mailboxes SET connection_state='DISCONNECTED', connected_generation=NULL, "
        "provider_account_id='sender@acme.test' WHERE id=%s",
        [w.mailbox_id],
    )


def settled_world(su, *, members: int = 2) -> World:
    """An ARCHIVED, activated campaign with a full send history that is old enough
    to be deleted."""
    w = sent_world(su, members=members)
    add_debit(su, w)
    add_planning_job(su, w)
    archive(su, w)
    age_attempts(su, w)
    return w


CAMPAIGN_TABLES = (
    "campaigns", "campaign_sequences", "sequence_steps", "campaign_audiences", "campaign_audience_members",
    "campaign_enrollments", "campaign_mailboxes", "campaign_settings_versions", "campaign_planning_jobs",
    "messages", "message_attempts", "message_events", "message_generations", "capacity_debits",
    "inbound_messages", "inbound_outreach_links", "personalization_previews",
)


def campaign_rows(su, w: World) -> dict[str, int]:
    out = {}
    for table in CAMPAIGN_TABLES:
        column = "id" if table == "campaigns" else "workspace_id"
        out[table] = count(su, table, f"{column}=%s", w.campaign_id if table == "campaigns" else w.ws)
    return out


# ---------------------------------------------------------------------------
# authorization and tenancy
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_only_admin_or_owner_may_delete(self, uri, su) -> None:
        w = settled_world(su, members=1)
        for who in (w.member, w.manager):
            expect(uri, "app_delete_campaign", w, w.campaign_id, "erasure:forbidden", user=who)
            expect(uri, "app_delete_mailbox", w, w.mailbox_id, "erasure:forbidden", user=who)
        assert count(su, "campaigns", "id=%s", w.campaign_id) == 1

    def test_another_tenants_ids_are_not_found(self, uri, su) -> None:
        mine = settled_world(su, members=1)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_delete_campaign", other, mine.campaign_id, "erasure:not_found")
        expect(uri, "app_delete_mailbox", other, mine.mailbox_id, "erasure:not_found")
        assert count(su, "campaigns", "id=%s", mine.campaign_id) == 1

    def test_naming_another_workspace_is_forbidden(self, uri, su) -> None:
        mine = settled_world(su, members=1)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_delete_campaign", other, mine.campaign_id, "erasure:forbidden",
               user=other.owner, workspace=mine.ws)

    def test_workers_and_helper_are_not_executable(self, uri) -> None:
        with psycopg.connect(uri) as conn:
            for role in ("app_worker_send", "app_worker_general", "anon", "authenticated", "service_role"):
                for fn in ("app_delete_campaign(uuid,uuid)", "app_delete_mailbox(uuid,uuid)"):
                    assert not conn.execute(
                        "SELECT has_function_privilege(%s, %s, 'EXECUTE')", [role, f"public.{fn}"]
                    ).fetchone()[0], (role, fn)
            assert not conn.execute(
                "SELECT has_function_privilege('app_api', 'public.app_delete_send_history(uuid,uuid[],uuid[])', 'EXECUTE')"
            ).fetchone()[0]


    def test_the_erasure_role_can_run_the_identity_helpers(self, uri) -> None:
        """Migration 0036: without EXECUTE on these, every command fails with
        'permission denied for function app_current_workspace_role' on a database
        whose migration user is not a superuser."""
        with psycopg.connect(uri) as conn:
            for fn in ("app_current_user_id()", "app_current_workspace_id()",
                       "app_current_workspace_role()", "app_has_permission(text)"):
                assert conn.execute(
                    "SELECT has_function_privilege('app_erasure', %s, 'EXECUTE')", [f"public.{fn}"]
                ).fetchone()[0], fn
            assert not conn.execute(
                "SELECT pg_has_role('app_api', 'app_foundation_reader', 'MEMBER')"
            ).fetchone()[0]


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


class TestDeleteCampaign:
    def test_an_archived_campaign_with_sent_history_is_completely_removed(self, uri, su) -> None:
        w = settled_world(su)
        before = campaign_rows(su, w)
        assert all(before[t] > 0 for t in CAMPAIGN_TABLES), before
        out = call(uri, "app_delete_campaign", w, w.campaign_id)
        assert out["deleted"]["messages"] == 2 and out["deleted"]["replies"] == 1
        assert campaign_rows(su, w) == dict.fromkeys(CAMPAIGN_TABLES, 0)
        assert count(su, "conversations", "workspace_id=%s", w.ws) == 0
        assert count(su, "unsubscribe_tokens", "workspace_id=%s AND message_id IS NOT NULL", w.ws) == 0
        audit = su.execute(
            "SELECT actor_id, action FROM public.audit_events WHERE target_id=%s AND action='campaign.delete'",
            [w.campaign_id],
        ).fetchone()
        assert audit == (w.owner, "campaign.delete")

    def test_leads_addresses_mailboxes_and_suppressions_survive(self, uri, su) -> None:
        w = settled_world(su, members=2)
        _insert(su, "suppressions", workspace_id=w.ws, address_id=w.address_ids[0], reason="UNSUBSCRIBE",
                release_audit_id=None)
        call(uri, "app_delete_campaign", w, w.campaign_id)
        assert count(su, "leads", "workspace_id=%s", w.ws) == 2
        assert count(su, "recipient_addresses", "workspace_id=%s", w.ws) == 2
        assert count(su, "mailboxes", "id=%s", w.mailbox_id) == 1
        assert scalar(su, "SELECT status FROM public.suppressions WHERE address_id=%s", w.address_ids[0]) == "ACTIVE"
        # the person's identity is intact, unlike an erase
        assert scalar(su, "SELECT first_name FROM public.leads WHERE id=%s", w.lead_ids[0]) == "Lead0"

    def test_a_draft_campaign_is_deleted_too(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=1)
        call(uri, "app_delete_campaign", w, w.campaign_id)
        assert count(su, "campaigns", "id=%s", w.campaign_id) == 0
        assert count(su, "leads", "workspace_id=%s", w.ws) == 1

    def test_a_running_campaign_must_be_archived_first(self, uri, su) -> None:
        w = sent_world(su, members=1)
        age_attempts(su, w)
        expect(uri, "app_delete_campaign", w, w.campaign_id, "erasure:state")
        assert count(su, "messages", "campaign_id=%s", w.campaign_id) == 2

    def test_an_inflight_send_blocks_it_and_changes_nothing(self, uri, su) -> None:
        w = settled_world(su, members=1)
        su.execute("SET session_replication_role = replica")
        su.execute("UPDATE public.messages SET status='RETRY_SCHEDULED', due_at=now(), anchor_at=now(), next_retry_at=now(), rendered_at=now(), content_subject='S', content_body_html='<p>b</p>', content_digest=repeat('a',64), frozen_destination='lead0@target0.test', frozen_sender_address='sender@acme.test' WHERE id=%s", [w.follow_up_id])  # type: ignore[attr-defined]
        su.execute("SET session_replication_role = origin")
        before = campaign_rows(su, w)
        expect(uri, "app_delete_campaign", w, w.campaign_id, "erasure:in_flight")
        assert campaign_rows(su, w) == before

    def test_a_send_in_the_last_25_hours_blocks_it_and_changes_nothing(self, uri, su) -> None:
        w = sent_world(su, members=1)
        add_debit(su, w)
        archive(su, w)  # attempts were just created, so still recent
        before = campaign_rows(su, w)
        expect(uri, "app_delete_campaign", w, w.campaign_id, "erasure:recent_sends")
        assert campaign_rows(su, w) == before

    def test_another_tenants_data_is_untouched(self, uri, su) -> None:
        w = settled_world(su)
        other = settled_world(su)
        other_before = campaign_rows(su, other)
        call(uri, "app_delete_campaign", w, w.campaign_id)
        assert campaign_rows(su, other) == other_before

    def test_a_second_call_after_success_is_not_found(self, uri, su) -> None:
        w = settled_world(su, members=1)
        call(uri, "app_delete_campaign", w, w.campaign_id)
        expect(uri, "app_delete_campaign", w, w.campaign_id, "erasure:not_found")

    def test_attachment_storage_keys_are_returned_for_cleanup(self, uri, su) -> None:
        w = settled_world(su, members=1)
        su.execute("SET session_replication_role = replica")
        su.execute(
            """INSERT INTO public.campaign_step_attachments
               (workspace_id, campaign_id, sequence_id, step_id, disposition, content_id, storage_key,
                filename, content_type, size_bytes, sha256)
               VALUES (%s,%s,%s,%s,'ATTACHMENT','abcdef123456','ws/a.pdf','f.pdf','application/pdf',10,%s)""",
            [w.ws, w.campaign_id, w.sequence_id, w.step1, "b" * 64],
        )
        su.execute("SET session_replication_role = origin")
        out = call(uri, "app_delete_campaign", w, w.campaign_id)
        assert out["storage_keys"] == ["ws/a.pdf"]
        assert count(su, "campaign_step_attachments", "workspace_id=%s", w.ws) == 0


# ---------------------------------------------------------------------------
# mailboxes
# ---------------------------------------------------------------------------


class TestDeleteMailbox:
    def test_a_mailbox_with_sending_history_is_completely_removed(self, uri, su) -> None:
        w = settled_world(su)
        disconnect(su, w)
        su.execute("SET session_replication_role = replica")
        su.execute("UPDATE public.campaign_enrollments SET assigned_mailbox_id=%s WHERE id=%s",
                   [w.mailbox_id, w.enrollment_id])
        su.execute("SET session_replication_role = origin")
        out = call(uri, "app_delete_mailbox", w, w.mailbox_id)
        assert out["deleted"]["messages"] == 2
        for table in ("mailboxes", "mailbox_connections", "messages", "message_attempts", "message_events",
                      "capacity_debits", "inbound_messages", "inbound_outreach_links", "conversations",
                      "campaign_mailboxes"):
            column = "id" if table == "mailboxes" else "workspace_id"
            assert count(su, table, f"{column}=%s", w.mailbox_id if table == "mailboxes" else w.ws) == 0, table
        audit = su.execute(
            "SELECT actor_id FROM public.audit_events WHERE target_id=%s AND action='mailbox.delete'",
            [w.mailbox_id],
        ).fetchone()
        assert audit == (w.owner,)

    def test_the_archived_campaign_and_its_people_survive(self, uri, su) -> None:
        w = settled_world(su)
        disconnect(su, w)
        su.execute("SET session_replication_role = replica")
        su.execute("UPDATE public.campaign_enrollments SET assigned_mailbox_id=%s WHERE id=%s",
                   [w.mailbox_id, w.enrollment_id])
        su.execute("SET session_replication_role = origin")
        call(uri, "app_delete_mailbox", w, w.mailbox_id)
        assert scalar(su, "SELECT status FROM public.campaigns WHERE id=%s", w.campaign_id) == "ARCHIVED"
        assert count(su, "campaign_enrollments", "campaign_id=%s", w.campaign_id) == 1
        assert scalar(su, "SELECT assigned_mailbox_id IS NULL FROM public.campaign_enrollments WHERE id=%s", w.enrollment_id)
        assert count(su, "sequence_steps", "campaign_id=%s", w.campaign_id) > 0
        assert count(su, "leads", "workspace_id=%s", w.ws) == 2

    def test_suppressions_survive(self, uri, su) -> None:
        w = settled_world(su, members=1)
        disconnect(su, w)
        _insert(su, "suppressions", workspace_id=w.ws, address_id=w.address_ids[0], reason="UNSUBSCRIBE",
                release_audit_id=None)
        call(uri, "app_delete_mailbox", w, w.mailbox_id)
        assert scalar(su, "SELECT status FROM public.suppressions WHERE address_id=%s", w.address_ids[0]) == "ACTIVE"

    def test_an_unused_disconnected_mailbox_is_removed(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        mb = uuid.uuid4()
        _insert(su, "mailboxes", id=mb, workspace_id=w.ws, provider="SMTP", original_address="spare@acme.test",
                connection_state="DISCONNECTED", provider_account_id="spare@acme.test")
        call(uri, "app_delete_mailbox", w, mb)
        assert count(su, "mailboxes", "id=%s", mb) == 0
        assert count(su, "mailboxes", "id=%s", w.mailbox_id) == 1

    def test_other_mailboxes_and_their_history_are_untouched(self, uri, su) -> None:
        w = settled_world(su)
        other = settled_world(su)
        disconnect(su, w)
        call(uri, "app_delete_mailbox", w, w.mailbox_id)
        assert count(su, "messages", "mailbox_id=%s", other.mailbox_id) == 2
        assert count(su, "mailboxes", "id=%s", other.mailbox_id) == 1

    def test_a_connected_mailbox_must_be_disconnected_first(self, uri, su) -> None:
        w = settled_world(su, members=1)
        expect(uri, "app_delete_mailbox", w, w.mailbox_id, "erasure:state")
        assert count(su, "mailboxes", "id=%s", w.mailbox_id) == 1

    def test_a_mailbox_used_by_a_live_campaign_is_refused(self, uri, su) -> None:
        w = sent_world(su, members=1)  # campaign is RUNNING
        age_attempts(su, w)
        disconnect(su, w)
        expect(uri, "app_delete_mailbox", w, w.mailbox_id, "erasure:in_use")
        assert count(su, "messages", "mailbox_id=%s", w.mailbox_id) == 2

    def test_a_recent_send_blocks_it_and_changes_nothing(self, uri, su) -> None:
        w = sent_world(su, members=1)
        archive(su, w)
        disconnect(su, w)
        expect(uri, "app_delete_mailbox", w, w.mailbox_id, "erasure:recent_sends")
        assert count(su, "messages", "mailbox_id=%s", w.mailbox_id) == 2

    def test_an_active_safety_hold_blocks_it(self, uri, su) -> None:
        w = settled_world(su, members=1)
        disconnect(su, w)
        su.execute("SET session_replication_role = replica")
        _insert(su, "safety_holds", workspace_id=w.ws, source_work_identity="t1", target_kind="MAILBOX",
                target_mailbox_id=w.mailbox_id, status="ACTIVE", reason="complaint spike")
        su.execute("SET session_replication_role = origin")
        expect(uri, "app_delete_mailbox", w, w.mailbox_id, "erasure:in_use")
        assert count(su, "mailboxes", "id=%s", w.mailbox_id) == 1
