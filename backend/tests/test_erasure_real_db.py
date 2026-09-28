"""Migration 0033 -- purge and privacy-erasure commands -- against a REAL PostgreSQL.

Runs the actual SQL as the real `app_api` role (RLS, grants, guard triggers and the
SECURITY DEFINER commands all enforced). A throwaway pgserver; nothing touches the
production-linked Supabase project and no email is sent.

What is proven here: who may call the commands, that another tenant's ids fail,
that never-sent items are physically removed, that sent history is redacted in
place (rows, statuses and counters survive, personal data does not), that an active
suppression survives the erasure of its person, that in-flight work blocks erasure,
and that the immutability guards still hold for every ordinary role.
"""

# ruff: noqa: E402, E501
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from tests.support.real_pg import throwaway_database
from tests.support.real_seed import World, _insert, seed_world

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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def call(uri, fn: str, w: World, target, *, user=None, workspace=None) -> Any:
    """Call a command as app_api for `user` in `workspace` (defaults: the world's owner
    in its own workspace) and commit. Errors surface as psycopg errors."""
    conn = psycopg.connect(uri)
    try:
        conn.execute("SET LOCAL ROLE app_api")
        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user or w.owner)])
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", [str(workspace or w.ws)])
        row = conn.execute(f"SELECT public.{fn}(%s, %s)", [workspace or w.ws, target]).fetchone()
        conn.commit()
        return row[0]
    finally:
        conn.close()


def code(exc: psycopg.Error) -> str:
    return exc.diag.message_primary or ""


def expect(uri, fn: str, w: World, target, want: str, **kw) -> None:
    with pytest.raises(psycopg.Error) as info:
        call(uri, fn, w, target, **kw)
    assert code(info.value) == want, code(info.value)


def scalar(su, sql: str, *params) -> Any:
    return su.execute(sql, list(params)).fetchone()[0]


def sent_world(su, *, members: int = 3) -> World:
    """A RUNNING campaign whose first email was SENT (with rendered content, an
    accepted attempt, an open event with a diagnostic, a stored reply and a
    hyper-personalization preview + generation), and a PLANNED follow-up."""
    w = seed_world(su, running=True, members=members)
    now = datetime.now(UTC)
    su.execute("SET session_replication_role = replica")
    _insert(su, "campaign_mailboxes", workspace_id=w.ws, campaign_id=w.campaign_id, mailbox_id=w.mailbox_id)
    su.execute(
        """UPDATE public.messages SET status='SENT', accepted_at=%s, due_at=%s, anchor_at=%s,
                  rendered_at=%s, content_subject='Quick idea for Target0',
                  content_body_html='<p>Hi Lead0, we help Target0 grow</p>', content_digest=%s,
                  frozen_destination='lead0@target0.test', frozen_sender_address='sender@acme.test',
                  rfc_message_id='<m1@acme.test>'
           WHERE id=%s""",
        [now, now, now, now, "a" * 64, w.message1_id],
    )
    attempt = uuid.uuid4()
    su.execute(
        """INSERT INTO public.message_attempts
           (id, workspace_id, message_id, mailbox_id, ordinal, invocation_owner, credential_generation,
            authorization_deadline, dispatch_generation, evidence_state, started_at, completed_at, provider_request_id)
           VALUES (%s,%s,%s,%s,1,'t',1,%s,1,'ACCEPTED',%s,%s,'req-1')""",
        [attempt, w.ws, w.message1_id, w.mailbox_id, now + timedelta(minutes=5), now, now],
    )
    su.execute(
        """INSERT INTO public.message_events
           (workspace_id, message_id, kind, bounce_type, source, detail, first_occurred_at, last_occurred_at)
           VALUES (%s,%s,'BOUNCED','HARD','test','550 lead0@target0.test does not exist',%s,%s)""",
        [w.ws, w.message1_id, now, now],
    )
    conv, inbound = uuid.uuid4(), uuid.uuid4()
    _insert(su, "conversations", id=conv, workspace_id=w.ws, mailbox_id=w.mailbox_id)
    _insert(
        su, "inbound_messages", id=inbound, workspace_id=w.ws, mailbox_id=w.mailbox_id,
        conversation_id=conv, connection_generation=1, provider_message_id="in-1",
        participants={"from": "lead0@target0.test", "name": "Lead Zero"},
        subject="Re: Quick idea", content_text="Sure, call me on +1 415 555 0132", received_at=now,
    )
    _insert(
        su, "inbound_outreach_links", id=uuid.uuid4(), workspace_id=w.ws, mailbox_id=w.mailbox_id,
        inbound_message_id=inbound, outbound_message_id=w.message1_id, campaign_id=w.campaign_id,
        enrollment_id=w.enrollment_id, evidence_type="IN_REPLY_TO", confidence="HIGH", status="CONFIRMED",
        matched_at=now,
    )
    _insert(
        su, "personalization_previews", id=uuid.uuid4(), workspace_id=w.ws, campaign_id=w.campaign_id,
        batch_id=uuid.uuid4(), audience_member_id=w.member_ids[0], lead_id=w.lead_ids[0], step_id=w.step1,
        config_digest="c" * 64, state="OK", subject="Hello Lead0", body_html="<p>About Target0</p>",
        facts=["runs Target0"], research_summary={"about": "Target0"}, created_by=w.manager,
        expires_at=now + timedelta(days=1), completed_at=now,
    )
    gen = uuid.uuid4()
    _insert(
        su, "message_generations", id=gen, workspace_id=w.ws, campaign_id=w.campaign_id,
        sequence_id=w.sequence_id, step_id=w.step1, enrollment_id=w.enrollment_id, message_id=w.message1_id,
        state="SUCCEEDED", max_attempts=3, next_attempt_at=now, completed_at=now,
        personalization_facts=["runs Target0"], angle="Their hiring push",
    )
    # A follow-up that must be stopped, not sent.
    w.follow_up_id = uuid.uuid4()  # type: ignore[attr-defined]
    _insert(
        su, "messages", id=w.follow_up_id, workspace_id=w.ws, purpose="CAMPAIGN",  # type: ignore[attr-defined]
        campaign_id=w.campaign_id, enrollment_id=w.enrollment_id, sequence_id=w.sequence_id, step_id=w.step2,
        mailbox_id=w.mailbox_id, address_id=w.address_ids[0], status="PLANNED",
    )
    su.execute("SET session_replication_role = origin")
    return w


def archive(su, w: World) -> None:
    su.execute("UPDATE public.campaigns SET status='PAUSED' WHERE id=%s", [w.campaign_id])
    su.execute("UPDATE public.campaigns SET status='ARCHIVED', archived_at=now() WHERE id=%s", [w.campaign_id])


def raw_insert(su, table: str, **values) -> None:
    """Insert a row a real flow would have produced, without re-running its guard."""
    su.execute("SET session_replication_role = replica")
    _insert(su, table, **values)
    su.execute("SET session_replication_role = origin")


def count(su, table: str, where: str, *params) -> int:
    return scalar(su, f"SELECT count(*) FROM public.{table} WHERE {where}", *params)


# ---------------------------------------------------------------------------
# authorization and tenancy
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_only_admin_or_owner_may_call(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        for who in (w.member, w.manager):
            expect(uri, "app_purge_campaign", w, w.campaign_id, "erasure:forbidden", user=who)
        assert count(su, "campaigns", "id=%s", w.campaign_id) == 1

    def test_owner_may_call(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        call(uri, "app_purge_campaign", w, w.campaign_id)
        assert count(su, "campaigns", "id=%s", w.campaign_id) == 0

    def test_another_tenants_ids_are_not_found(self, uri, su) -> None:
        mine = seed_world(su, running=False, enroll_first=False, members=0)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_purge_campaign", other, mine.campaign_id, "erasure:not_found")
        assert count(su, "campaigns", "id=%s", mine.campaign_id) == 1

    def test_workspace_argument_must_match_the_session_workspace(self, uri, su) -> None:
        mine = seed_world(su, running=False, enroll_first=False, members=0)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        # An owner of `other` names `mine`'s workspace in the call.
        expect(uri, "app_purge_campaign", other, mine.campaign_id, "erasure:forbidden",
               user=other.owner, workspace=mine.ws)
        assert count(su, "campaigns", "id=%s", mine.campaign_id) == 1

    def test_no_user_no_access(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        conn = psycopg.connect(uri)
        try:
            conn.execute("SET LOCAL ROLE app_api")
            conn.execute("SELECT set_config('app.workspace_id', %s, true)", [str(w.ws)])
            with pytest.raises(psycopg.Error) as info:
                conn.execute("SELECT public.app_purge_campaign(%s, %s)", [w.ws, w.campaign_id])
            assert code(info.value) == "erasure:forbidden"
        finally:
            conn.close()

    def test_workers_and_helpers_are_not_executable_by_runtime_roles(self, uri) -> None:
        for role, fn in (
            ("app_worker_send", "app_erase_lead(uuid,uuid)"),
            ("app_worker_general", "app_purge_campaign(uuid,uuid)"),
            ("app_api", "app_erase_recipient_records(uuid,uuid,uuid)"),
            ("app_api", "app_erasure_authorize(uuid)"),
        ):
            with psycopg.connect(uri) as conn:
                assert not conn.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')", [role, f"public.{fn}"]
                ).fetchone()[0], (role, fn)


# ---------------------------------------------------------------------------
# guards stay closed for everyone else
# ---------------------------------------------------------------------------


class TestGuardsStayClosed:
    def test_ordinary_roles_still_cannot_rewrite_a_rendered_snapshot(self, uri, su) -> None:
        w = sent_world(su, members=1)
        for role in ("app_worker_general", "app_worker_send", "app_scheduler"):
            with psycopg.connect(uri) as conn:
                conn.execute(f"SET LOCAL ROLE {role}")
                conn.execute("SELECT set_config('app.workspace_id', %s, true)", [str(w.ws)])
                conn.execute("SELECT set_config('app.erasure', 'on', true)")  # forging the flag
                with pytest.raises(psycopg.Error):
                    conn.execute(
                        "UPDATE public.messages SET content_subject='x' WHERE id=%s", [w.message1_id]
                    )

    def test_even_the_migration_owner_is_blocked_outside_the_commands(self, su) -> None:
        w = sent_world(su, members=1)
        su.execute("SELECT set_config('app.erasure', 'on', false)")
        with pytest.raises(psycopg.errors.CheckViolation):
            su.execute("UPDATE public.messages SET content_subject='x' WHERE id=%s", [w.message1_id])
        with pytest.raises(psycopg.errors.CheckViolation):
            su.execute(
                "UPDATE public.recipient_addresses SET canonical_address='z@z.test' WHERE id=%s",
                [w.address_ids[0]],
            )
        su.execute("SELECT set_config('app.erasure', 'off', false)")


def test_app_api_cannot_write_the_erased_marker(uri, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    with psycopg.connect(uri) as conn:
        conn.execute("SET LOCAL ROLE app_api")
        conn.execute("SELECT set_config('app.user_id', %s, true)", [str(w.manager)])
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", [str(w.ws)])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE public.campaigns SET erased_at = now() WHERE id=%s", [w.campaign_id])


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


class TestPurgeCampaign:
    def test_a_draft_is_removed_with_everything_it_owned(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=2)
        _insert(su, "campaign_mailboxes", workspace_id=w.ws, campaign_id=w.campaign_id, mailbox_id=w.mailbox_id)
        out = call(uri, "app_purge_campaign", w, w.campaign_id)
        assert out["deleted"]["steps"] == 3 and out["deleted"]["audience_members"] == 2
        for table, col in (
            ("campaigns", "id"), ("campaign_sequences", "campaign_id"), ("sequence_steps", "campaign_id"),
            ("campaign_audiences", "campaign_id"), ("campaign_audience_members", "campaign_id"),
            ("campaign_settings_versions", "campaign_id"), ("campaign_mailboxes", "campaign_id"),
        ):
            assert count(su, table, f"{col}=%s", w.campaign_id) == 0, table
        # people and mailboxes are workspace assets, not the campaign's
        assert count(su, "leads", "workspace_id=%s", w.ws) == 2
        assert count(su, "mailboxes", "id=%s", w.mailbox_id) == 1
        audit = su.execute(
            "SELECT actor_id, action FROM public.audit_events WHERE target_id=%s AND action='campaign.purge'",
            [w.campaign_id],
        ).fetchone()
        assert audit == (w.owner, "campaign.purge")

    def test_an_archived_draft_can_be_purged_too(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=1)
        _insert(su, "campaign_mailboxes", workspace_id=w.ws, campaign_id=w.campaign_id, mailbox_id=w.mailbox_id)
        su.execute(
            "UPDATE public.campaigns SET status='ARCHIVED', archived_at=now() WHERE id=%s", [w.campaign_id]
        )
        call(uri, "app_purge_campaign", w, w.campaign_id)
        assert count(su, "campaigns", "id=%s", w.campaign_id) == 0

    def test_storage_keys_of_attachments_are_returned_for_cleanup(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        su.execute(
            """INSERT INTO public.campaign_step_attachments
               (workspace_id, campaign_id, sequence_id, step_id, disposition, content_id, storage_key,
                filename, content_type, size_bytes, sha256)
               VALUES (%s,%s,%s,%s,'ATTACHMENT','abcdefgh12','ws/x/file.pdf','file.pdf','application/pdf',10,%s)""",
            [w.ws, w.campaign_id, w.sequence_id, w.step1, "d" * 64],
        )
        out = call(uri, "app_purge_campaign", w, w.campaign_id)
        assert out["storage_keys"] == ["ws/x/file.pdf"]

    def test_a_held_list_gate_is_released_so_the_list_is_not_locked_forever(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        list_id = uuid.uuid4()
        _insert(su, "lead_lists", id=list_id, workspace_id=w.ws, name="L", capture_count=1)
        raw_insert(
            su, "audience_capture_sources", workspace_id=w.ws, campaign_id=w.campaign_id,
            audience_id=w.audience_id, list_id=list_id, captured_list_revision=1, gate_held=True,
        )
        call(uri, "app_purge_campaign", w, w.campaign_id)
        assert scalar(su, "SELECT capture_count FROM public.lead_lists WHERE id=%s", list_id) == 0

    def test_an_activated_campaign_is_refused(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        expect(uri, "app_purge_campaign", w, w.campaign_id, "erasure:state")
        assert count(su, "campaigns", "id=%s", w.campaign_id) == 1

    def test_unknown_id_is_not_found(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_purge_campaign", w, uuid.uuid4(), "erasure:not_found")


class TestEraseCampaign:
    def test_requires_an_archived_activated_campaign(self, uri, su) -> None:
        w = sent_world(su)
        expect(uri, "app_erase_campaign", w, w.campaign_id, "erasure:state")  # RUNNING
        draft = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_erase_campaign", draft, draft.campaign_id, "erasure:state")

    def test_history_is_redacted_in_place_and_counters_survive(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        out = call(uri, "app_erase_campaign", w, w.campaign_id)
        assert out["redacted"]["messages_redacted"] == 1
        assert out["redacted"]["replies_redacted"] == 1

        subject, body, dest, digest, status, sender = su.execute(
            """SELECT content_subject, content_body_html, frozen_destination, content_digest, status,
                      frozen_sender_address FROM public.messages WHERE id=%s""",
            [w.message1_id],
        ).fetchone()
        assert (subject, body) == ("[erased]", "[erased]")
        assert dest == f"erased-{w.enrollment_id}@erased.invalid"
        assert len(digest) == 64
        assert status == "SENT" and sender == "sender@acme.test"  # history and our own identity stay

        # the follow-up that was never sent is stopped, not left to send later
        assert scalar(su, "SELECT status FROM public.messages WHERE id=%s", w.follow_up_id) == "SKIPPED"  # type: ignore[attr-defined]
        state, reason, frozen_dest, frozen_vars = su.execute(
            "SELECT state, stop_reason, frozen_destination, frozen_variables FROM public.campaign_enrollments WHERE id=%s",
            [w.enrollment_id],
        ).fetchone()
        assert (state, reason) == ("STOPPED", "ERASED")
        assert frozen_dest.startswith("erased-") and frozen_vars == {}

        assert scalar(su, "SELECT detail FROM public.message_events WHERE message_id=%s", w.message1_id) is None
        assert scalar(su, "SELECT frozen_variables FROM public.campaign_audience_members WHERE id=%s", w.member_ids[0]) == {}
        subj, body_html, facts, research = su.execute(
            "SELECT subject, body_html, facts, research_summary FROM public.personalization_previews WHERE campaign_id=%s",
            [w.campaign_id],
        ).fetchone()
        assert (subj, body_html, facts, research) == ("[erased]", "[erased]", None, None)
        assert su.execute(
            "SELECT personalization_facts, angle FROM public.message_generations WHERE campaign_id=%s", [w.campaign_id]
        ).fetchone() == (None, None)
        assert su.execute(
            "SELECT participants, subject, content_text FROM public.inbound_messages WHERE workspace_id=%s", [w.ws]
        ).fetchone() == ({}, None, None)

        # counts, structure and audit are intact
        assert count(su, "messages", "campaign_id=%s", w.campaign_id) == 2
        assert count(su, "message_attempts", "workspace_id=%s", w.ws) == 1
        assert scalar(su, "SELECT erased_at IS NOT NULL FROM public.campaigns WHERE id=%s", w.campaign_id)
        assert count(su, "audit_events", "target_id=%s AND action='campaign.erase'", w.campaign_id) == 1

    def test_no_recipient_data_is_left_in_the_campaign(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        call(uri, "app_erase_campaign", w, w.campaign_id)
        needles = ("lead0@target0.test", "Lead Zero", "Target0", "555 0132", "Lead0")
        for table, cols in (
            ("messages", "content_subject, content_body_html, frozen_destination"),
            ("campaign_enrollments", "frozen_destination, frozen_variables::text"),
            ("campaign_audience_members", "frozen_variables::text"),
            ("personalization_previews", "subject, body_html, facts::text, research_summary::text"),
            ("message_generations", "personalization_facts::text, angle"),
            ("inbound_messages", "participants::text, subject, content_text"),
            ("message_events", "detail"),
        ):
            for row in su.execute(f"SELECT {cols} FROM public.{table} WHERE workspace_id=%s", [w.ws]).fetchall():
                blob = " ".join(str(v) for v in row if v is not None)
                for needle in needles:
                    # only the OTHER two leads' data may remain; lead 0's must be gone
                    if needle in ("Lead0",) and table == "campaign_audience_members":
                        continue
                    assert needle not in blob, (table, needle)

    def test_it_is_idempotent(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        call(uri, "app_erase_campaign", w, w.campaign_id)
        call(uri, "app_erase_campaign", w, w.campaign_id)
        assert scalar(su, "SELECT content_subject FROM public.messages WHERE id=%s", w.message1_id) == "[erased]"

    def test_an_inflight_send_blocks_it_and_changes_nothing(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        su.execute("SET session_replication_role = replica")
        su.execute(
            "UPDATE public.messages SET status='SENDING', due_at=now(), anchor_at=now(), rendered_at=now(), content_subject='S', content_body_html='<p>b</p>', content_digest=repeat('a',64), frozen_destination='lead0@target0.test', frozen_sender_address='sender@acme.test' WHERE id=%s", [w.follow_up_id]  # type: ignore[attr-defined]
        )
        su.execute("SET session_replication_role = origin")
        expect(uri, "app_erase_campaign", w, w.campaign_id, "erasure:in_flight")
        assert scalar(su, "SELECT content_subject FROM public.messages WHERE id=%s", w.message1_id) == "Quick idea for Target0"

    def test_an_unresolved_attempt_blocks_it(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        now = datetime.now(UTC)
        su.execute("SET session_replication_role = replica")
        su.execute(
            """INSERT INTO public.message_attempts
               (id, workspace_id, message_id, mailbox_id, ordinal, invocation_owner, credential_generation,
                authorization_deadline, dispatch_generation, evidence_state, started_at)
               VALUES (%s,%s,%s,%s,1,'t',1,%s,1,'UNKNOWN',%s)""",
            [uuid.uuid4(), w.ws, w.follow_up_id, w.mailbox_id, now + timedelta(minutes=5), now],  # type: ignore[attr-defined]
        )
        su.execute("SET session_replication_role = origin")
        expect(uri, "app_erase_campaign", w, w.campaign_id, "erasure:in_flight")

    def test_another_tenant_cannot_erase_it(self, uri, su) -> None:
        w = sent_world(su)
        archive(su, w)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_erase_campaign", other, w.campaign_id, "erasure:not_found")
        assert scalar(su, "SELECT content_subject FROM public.messages WHERE id=%s", w.message1_id) == "Quick idea for Target0"


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------


class TestEraseLead:
    def test_the_person_is_erased_everywhere_and_others_are_untouched(self, uri, su) -> None:
        w = sent_world(su, members=3)
        lead, other_lead = w.lead_ids[0], w.lead_ids[1]
        list_id = uuid.uuid4()
        _insert(su, "lead_lists", id=list_id, workspace_id=w.ws, name="L")
        _insert(su, "lead_list_memberships", workspace_id=w.ws, list_id=list_id, lead_id=lead)
        su.execute("UPDATE public.leads SET phone='+1 415 555 0132', linkedin_url='https://linkedin.com/in/x', custom_fields='{\"k\":\"v\"}' WHERE id=%s", [lead])

        out = call(uri, "app_erase_lead", w, lead)
        assert out["redacted"]["list_memberships_removed"] == 1 and out["redacted"]["suppression_kept"] is False

        row = su.execute(
            """SELECT original_address, canonical_address, first_name, last_name, company, title, phone,
                      linkedin_url, custom_fields, status, archived_at IS NOT NULL, erased_at IS NOT NULL
               FROM public.leads WHERE id=%s""",
            [lead],
        ).fetchone()
        assert row[0] == row[1] == f"erased-{lead}@erased.invalid"
        assert row[2:8] == (None,) * 6 and row[8] == {} and row[9:] == ("ARCHIVED", True, True)
        assert scalar(su, "SELECT canonical_address FROM public.recipient_addresses WHERE id=%s", w.address_ids[0]) == f"erased-{w.address_ids[0]}@erased.invalid"
        assert count(su, "lead_list_memberships", "lead_id=%s", lead) == 0
        assert scalar(su, "SELECT content_subject FROM public.messages WHERE id=%s", w.message1_id) == "[erased]"
        assert scalar(su, "SELECT state FROM public.campaign_enrollments WHERE id=%s", w.enrollment_id) == "STOPPED"

        # the next person in the same workspace is not touched
        assert su.execute("SELECT first_name, canonical_address FROM public.leads WHERE id=%s", [other_lead]).fetchone() == ("Lead1", "lead1@target1.test")
        assert scalar(su, "SELECT canonical_address FROM public.recipient_addresses WHERE id=%s", w.address_ids[1]) == "lead1@target1.test"

    def test_an_active_suppression_keeps_the_address_and_stays_active(self, uri, su) -> None:
        w = sent_world(su, members=2)
        lead, address = w.lead_ids[0], w.address_ids[0]
        _insert(su, "suppressions", workspace_id=w.ws, address_id=address, reason="UNSUBSCRIBE",
                release_audit_id=None)
        out = call(uri, "app_erase_lead", w, lead)
        assert out["redacted"]["suppression_kept"] is True
        # the opt-out record: address row and ACTIVE suppression remain, so re-importing this
        # email later is still blocked
        assert scalar(su, "SELECT canonical_address FROM public.recipient_addresses WHERE id=%s", address) == "lead0@target0.test"
        assert scalar(su, "SELECT status FROM public.suppressions WHERE address_id=%s", address) == "ACTIVE"
        # ...but nothing else about the person survives
        assert scalar(su, "SELECT first_name IS NULL AND canonical_address LIKE 'erased-%%' FROM public.leads WHERE id=%s", lead)
        assert scalar(su, "SELECT content_subject FROM public.messages WHERE id=%s", w.message1_id) == "[erased]"
        assert scalar(su, "SELECT frozen_destination LIKE 'erased-%%' FROM public.campaign_enrollments WHERE id=%s", w.enrollment_id)

    def test_it_is_idempotent(self, uri, su) -> None:
        w = sent_world(su, members=1)
        call(uri, "app_erase_lead", w, w.lead_ids[0])
        call(uri, "app_erase_lead", w, w.lead_ids[0])
        assert scalar(su, "SELECT erased_at IS NOT NULL FROM public.leads WHERE id=%s", w.lead_ids[0])

    def test_an_inflight_send_blocks_it_and_changes_nothing(self, uri, su) -> None:
        w = sent_world(su, members=1)
        su.execute("SET session_replication_role = replica")
        su.execute("UPDATE public.messages SET status='RETRY_SCHEDULED', due_at=now(), anchor_at=now(), next_retry_at=now(), rendered_at=now(), content_subject='S', content_body_html='<p>b</p>', content_digest=repeat('a',64), frozen_destination='lead0@target0.test', frozen_sender_address='sender@acme.test' WHERE id=%s", [w.follow_up_id])  # type: ignore[attr-defined]
        su.execute("SET session_replication_role = origin")
        expect(uri, "app_erase_lead", w, w.lead_ids[0], "erasure:in_flight")
        assert scalar(su, "SELECT first_name FROM public.leads WHERE id=%s", w.lead_ids[0]) == "Lead0"

    def test_a_list_held_by_an_audience_capture_blocks_it_and_changes_nothing(self, uri, su) -> None:
        w = sent_world(su, members=1)
        list_id = uuid.uuid4()
        _insert(su, "lead_lists", id=list_id, workspace_id=w.ws, name="L")
        _insert(su, "lead_list_memberships", workspace_id=w.ws, list_id=list_id, lead_id=w.lead_ids[0])
        su.execute("UPDATE public.lead_lists SET capture_count=1 WHERE id=%s", [list_id])
        expect(uri, "app_erase_lead", w, w.lead_ids[0], "erasure:in_use")
        assert scalar(su, "SELECT first_name FROM public.leads WHERE id=%s", w.lead_ids[0]) == "Lead0"
        assert scalar(su, "SELECT content_subject FROM public.messages WHERE id=%s", w.message1_id) == "Quick idea for Target0"

    def test_another_tenant_cannot_erase_a_lead(self, uri, su) -> None:
        w = sent_world(su, members=1)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_erase_lead", other, w.lead_ids[0], "erasure:not_found")

    def test_a_lead_can_be_reimported_after_erasure(self, uri, su) -> None:
        w = sent_world(su, members=1)
        call(uri, "app_erase_lead", w, w.lead_ids[0])
        # the freed email is usable again (unique keys no longer collide)
        su.execute(
            "INSERT INTO public.leads (workspace_id, original_address, canonical_address) VALUES (%s,'lead0@target0.test','lead0@target0.test')",
            [w.ws],
        )
        su.execute(
            "INSERT INTO public.recipient_addresses (workspace_id, canonical_address) VALUES (%s,'lead0@target0.test')",
            [w.ws],
        )


# ---------------------------------------------------------------------------
# templates, lists, imports, mailboxes
# ---------------------------------------------------------------------------


def make_template(su, w: World, *, archived: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    template, version = uuid.uuid4(), uuid.uuid4()
    su.execute("SET session_replication_role = replica")
    _insert(su, "templates", id=template, workspace_id=w.ws, name="T", current_version_id=version,
            archived_at=datetime.now(UTC) if archived else None)
    _insert(su, "template_versions", id=version, workspace_id=w.ws, template_id=template, revision=1,
            subject="S", body_html="<p>B</p>", content_digest="e" * 64)
    su.execute("SET session_replication_role = origin")
    return template, version


class TestTemplates:
    def test_an_archived_unused_template_is_removed(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        template, _ = make_template(su, w)
        out = call(uri, "app_purge_template", w, template)
        assert out["deleted"]["versions"] == 1
        assert count(su, "templates", "id=%s", template) == 0
        assert count(su, "template_versions", "template_id=%s", template) == 0

    def test_it_must_be_archived_first(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        template, _ = make_template(su, w, archived=False)
        expect(uri, "app_purge_template", w, template, "erasure:state")

    def test_a_template_a_campaign_step_used_stays(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        template, version = make_template(su, w)
        su.execute("UPDATE public.sequence_steps SET source_template_version_id=%s WHERE id=%s", [version, w.step1])
        expect(uri, "app_purge_template", w, template, "erasure:in_use")
        assert count(su, "templates", "id=%s", template) == 1

    def test_another_tenant_cannot_purge_it(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        template, _ = make_template(su, w)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_purge_template", other, template, "erasure:not_found")


class TestLeadLists:
    def _list(self, su, w: World, *, archived=True, capture=0) -> uuid.UUID:
        list_id = uuid.uuid4()
        _insert(su, "lead_lists", id=list_id, workspace_id=w.ws, name="L",
                archived_at=datetime.now(UTC) if archived else None)
        for lead in w.lead_ids:
            _insert(su, "lead_list_memberships", workspace_id=w.ws, list_id=list_id, lead_id=lead)
        if capture:
            su.execute("UPDATE public.lead_lists SET capture_count=%s WHERE id=%s", [capture, list_id])
        return list_id

    def test_an_archived_list_goes_but_its_people_stay(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=2)
        list_id = self._list(su, w)
        out = call(uri, "app_purge_lead_list", w, list_id)
        assert out["deleted"]["memberships"] == 2
        assert count(su, "lead_lists", "id=%s", list_id) == 0
        assert count(su, "leads", "workspace_id=%s", w.ws) == 2

    def test_import_history_keeps_working_without_the_list(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=1)
        list_id = self._list(su, w)
        imp = import_job(su, w, list_id=list_id)
        call(uri, "app_purge_lead_list", w, list_id)
        assert scalar(su, "SELECT list_id FROM public.import_jobs WHERE id=%s", imp) is None

    def test_must_be_archived_and_unheld(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=1)
        expect(uri, "app_purge_lead_list", w, self._list(su, w, archived=False), "erasure:state")
        expect(uri, "app_purge_lead_list", w, self._list(su, w, capture=1), "erasure:in_use")

    def test_a_list_that_built_an_audience_stays(self, uri, su) -> None:
        w = seed_world(su, running=True, enroll_first=False, members=1)
        list_id = self._list(su, w)
        raw_insert(su, "audience_capture_sources", workspace_id=w.ws, campaign_id=w.campaign_id,
                   audience_id=w.audience_id, list_id=list_id, captured_list_revision=1, gate_held=False,
                   released_at=datetime.now(UTC))
        expect(uri, "app_purge_lead_list", w, list_id, "erasure:in_use")
        assert count(su, "lead_lists", "id=%s", list_id) == 1
        assert count(su, "lead_list_memberships", "list_id=%s", list_id) == 1  # rolled back with it


def import_job(su, w: World, *, status="COMPLETED", list_id=None, rows=2) -> uuid.UUID:
    job = uuid.uuid4()
    su.execute("SET session_replication_role = replica")
    _insert(
        su, "import_jobs", id=job, workspace_id=w.ws, initiator_id=w.owner,
        storage_object_key=f"imports/{job}.csv", storage_object_version="1", storage_object_digest="f" * 64,
        import_kind="LEADS", status=status, list_id=list_id, total_rows=rows if status != "PENDING" else None,
        processed_rows=rows if status != "PENDING" else 0, rejected_rows=rows if status != "PENDING" else 0,
    )
    if status != "PENDING":
        for i in range(rows):
            _insert(su, "import_row_results", workspace_id=w.ws, import_id=job, row_number=i + 1,
                    status="REJECTED", validation_reason="bad email", error_artifact_ref=f"errors/{job}.csv")
    su.execute("SET session_replication_role = origin")
    return job


class TestImports:
    def test_a_finished_import_is_removed_and_its_files_are_named(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        job = import_job(su, w)
        out = call(uri, "app_purge_import", w, job)
        assert out["deleted"]["rows"] == 2
        assert sorted(out["storage_keys"]) == sorted([f"imports/{job}.csv", f"errors/{job}.csv"])
        assert count(su, "import_jobs", "id=%s", job) == 0
        assert count(su, "import_row_results", "import_id=%s", job) == 0

    def test_a_running_import_is_refused(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_purge_import", w, import_job(su, w, status="PENDING"), "erasure:state")

    def test_another_tenant_cannot_purge_it(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        job = import_job(su, w)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_purge_import", other, job, "erasure:not_found")


class TestMailboxes:
    def _disconnected(self, su, w: World, *, address="spare@acme.test") -> uuid.UUID:
        mb = uuid.uuid4()
        _insert(su, "mailboxes", id=mb, workspace_id=w.ws, provider="SMTP", original_address=address,
                connection_state="DISCONNECTED", provider_account_id=address)
        return mb

    def test_an_unused_disconnected_mailbox_is_removed(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        mb = self._disconnected(su, w)
        call(uri, "app_purge_mailbox", w, mb)
        assert count(su, "mailboxes", "id=%s", mb) == 0

    def test_a_connected_mailbox_must_be_disconnected_first(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        su.execute("SET session_replication_role = replica")
        su.execute("UPDATE public.mailboxes SET connection_state='CONNECTED', connected_generation=current_connection_generation, provider_account_id='sender@acme.test' WHERE id=%s", [w.mailbox_id])
        su.execute("SET session_replication_role = origin")
        expect(uri, "app_purge_mailbox", w, w.mailbox_id, "erasure:state")

    def test_a_mailbox_with_sending_history_stays_and_nothing_changes(self, uri, su) -> None:
        w = sent_world(su, members=1)
        su.execute("UPDATE public.mailboxes SET connection_state='DISCONNECTED', connected_generation=NULL, provider_account_id='sender@acme.test' WHERE id=%s", [w.mailbox_id])
        expect(uri, "app_purge_mailbox", w, w.mailbox_id, "erasure:in_use")
        assert count(su, "mailboxes", "id=%s", w.mailbox_id) == 1

    def test_another_tenant_cannot_remove_it(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        mb = self._disconnected(su, w)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_purge_mailbox", other, mb, "erasure:not_found")
