"""Seed a tenant into a real (throwaway) PostgreSQL as its superuser.

Runs with `session_replication_role = replica`, which skips FK checks and
ordinary triggers, so a fixture can be built directly in any state (a RUNNING
campaign with a PLANNED message, a FROZEN sequence, ...). NOT NULL and CHECK
constraints still apply, so seeded rows are valid. Test code then acts as
`app_api` / `app_worker_general` / ... through SET LOCAL ROLE, where RLS, grants
and triggers are fully enforced.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

FROZEN = {
    "first_name": "Sarah",
    "last_name": "Johnson",
    "company": "Acme",
    "title": "VP Sales",
    "company_industry": "Software",
    "email": "sarah@acme.test",
    "phone": "+1 415 555 0132",
    "linkedin_url": "https://linkedin.com/in/sarah-johnson",
}
CONFIG = {
    "objective": "Book a demo with SaaS founders",
    "offer": "We help SaaS teams automate manual outbound prospecting",
    "cta": "Would you be open to a quick conversation?",
    "target": "SaaS founders",
    "problem_solved": "Manual outbound work",
    "tone": "friendly",
    "must_mention": [],
    "never_say": ["guaranteed"],
}
REF_SUBJECT = "Quick idea for {{company}}"
REF_BODY = (
    "<p>Hi {{first_name}},</p>"
    "<p>We help SaaS companies improve their outbound process by automating "
    "manual prospecting work.</p>"
    "<p>Would you be open to a quick conversation?</p>"
    "<p>Best,<br>John</p>"
)
FOLLOW_UP_BODY = (
    "<p>Hi {{first_name}},</p><p>Teams like yours often waste hours on manual "
    "list building each week, so we automated the tedious parts of outbound "
    "prospecting for busy sales people.</p>"
    "<p>Would you be open to a quick conversation?</p><p>Best,<br>John</p>"
)


@dataclass
class World:
    ws: uuid.UUID
    owner: uuid.UUID
    manager: uuid.UUID
    member: uuid.UUID
    campaign_id: uuid.UUID
    sequence_id: uuid.UUID
    step1: uuid.UUID
    wait: uuid.UUID
    step2: uuid.UUID
    audience_id: uuid.UUID
    settings_id: uuid.UUID
    mailbox_id: uuid.UUID
    member_ids: list[uuid.UUID] = field(default_factory=list)
    lead_ids: list[uuid.UUID] = field(default_factory=list)
    address_ids: list[uuid.UUID] = field(default_factory=list)
    enrollment_id: uuid.UUID | None = None
    message1_id: uuid.UUID | None = None


def _insert(conn: Any, table: str, **values: Any) -> None:
    cols = list(values)
    placeholders = ", ".join(
        "%s::jsonb"
        if isinstance(values[c], (dict, list)) and c not in _ARRAY_COLS
        else "%s"
        for c in cols
    )
    params = [
        json.dumps(values[c])
        if isinstance(values[c], (dict, list)) and c not in _ARRAY_COLS
        else values[c]
        for c in cols
    ]
    conn.execute(
        f"INSERT INTO public.{table} ({', '.join(cols)}) VALUES ({placeholders})",
        params,
    )


_ARRAY_COLS: set[str] = set()


def seed_world(
    conn: Any,
    *,
    running: bool = True,
    campaign_type: str = "HYPER_PERSONALIZED",
    members: int = 3,
    enroll_first: bool = True,
    config: dict[str, Any] | None = None,
    frozen: dict[str, Any] | None = None,
) -> World:
    """One tenant: three users (OWNER/MANAGER/MEMBER), a mailbox, a campaign with
    a two-email sequence (+wait), a READY audience, and (optionally) an ACTIVE
    enrollment with its first message PLANNED."""
    conn.execute("SET session_replication_role = replica")
    now = datetime.now(UTC)
    w = World(
        ws=uuid.uuid4(),
        owner=uuid.uuid4(),
        manager=uuid.uuid4(),
        member=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        sequence_id=uuid.uuid4(),
        step1=uuid.uuid4(),
        wait=uuid.uuid4(),
        step2=uuid.uuid4(),
        audience_id=uuid.uuid4(),
        settings_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
    )
    _insert(conn, "workspaces", id=w.ws, name="Acme Workspace")
    for user, role in (
        (w.owner, "OWNER"),
        (w.manager, "MANAGER"),
        (w.member, "MEMBER"),
    ):
        conn.execute(
            "INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT DO NOTHING", [user]
        )
        _insert(conn, "profiles", id=user)
        _insert(
            conn,
            "workspace_memberships",
            workspace_id=w.ws,
            user_id=user,
            role_code=role,
        )
    _insert(
        conn,
        "mailboxes",
        id=w.mailbox_id,
        workspace_id=w.ws,
        provider="SMTP",
        original_address="sender@acme.test",
    )
    _insert(
        conn,
        "campaign_settings_versions",
        id=w.settings_id,
        workspace_id=w.ws,
        campaign_id=w.campaign_id,
        revision=1,
        timezone="UTC",
        weekday_set=127,
        window_start_local="09:00",
        window_end_local="17:00",
    )
    campaign: dict[str, Any] = dict(
        id=w.campaign_id,
        workspace_id=w.ws,
        name="Outbound",
        creator_id=w.manager,
        campaign_type=campaign_type,
        draft_sequence_id=w.sequence_id,
        current_settings_id=w.settings_id,
    )
    if running:
        campaign.update(
            status="RUNNING",
            planning_status="READY",
            activation_id=uuid.uuid4(),
            activated_sequence_id=w.sequence_id,
            activated_audience_id=w.audience_id,
            start_at=now - timedelta(hours=1),
        )
    else:
        campaign.update(status="DRAFT", draft_audience_id=w.audience_id)
    _insert(conn, "campaigns", **campaign)
    _insert(
        conn,
        "campaign_sequences",
        id=w.sequence_id,
        workspace_id=w.ws,
        campaign_id=w.campaign_id,
        revision=1,
        status="DRAFT",
        personalization_config=CONFIG if config is None else config,
    )
    for sid, pos, kind, extra in (
        (
            w.step1,
            1,
            "EMAIL",
            {"email_subject": REF_SUBJECT, "email_body_html": REF_BODY},
        ),
        (w.wait, 2, "WAIT", {"wait_duration_minutes": 2880}),
        (
            w.step2,
            3,
            "EMAIL",
            {
                "email_subject": "A different angle for {{company}}",
                "email_body_html": FOLLOW_UP_BODY,
            },
        ),
    ):
        _insert(
            conn,
            "sequence_steps",
            id=sid,
            workspace_id=w.ws,
            sequence_id=w.sequence_id,
            campaign_id=w.campaign_id,
            position=pos,
            kind=kind,
            **extra,
        )
    _insert(
        conn,
        "campaign_audiences",
        id=w.audience_id,
        workspace_id=w.ws,
        campaign_id=w.campaign_id,
        revision=1,
        status="READY",
        completed_at=now,
        source_manifest_digest="a" * 64,
        selection_manifest={"version": 1, "lists": [], "leads": []},
    )
    for i in range(members):
        lead, address, member = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        email = f"lead{i}@target{i}.test"
        _insert(
            conn,
            "recipient_addresses",
            id=address,
            workspace_id=w.ws,
            canonical_address=email,
        )
        _insert(
            conn,
            "leads",
            id=lead,
            workspace_id=w.ws,
            original_address=email,
            canonical_address=email,
            first_name=f"Lead{i}",
            company=f"Target{i}",
        )
        _insert(
            conn,
            "campaign_audience_members",
            id=member,
            workspace_id=w.ws,
            campaign_id=w.campaign_id,
            audience_id=w.audience_id,
            lead_id=lead,
            address_id=address,
            capture_ordinal=i + 1,
            contact_revision=1,
            eligibility_status="ACCEPTED",
            frozen_variables={**(frozen or FROZEN), "first_name": f"Lead{i}"},
        )
        w.member_ids.append(member)
        w.lead_ids.append(lead)
        w.address_ids.append(address)
    if enroll_first:
        w.enrollment_id = uuid.uuid4()
        _insert(
            conn,
            "campaign_enrollments",
            id=w.enrollment_id,
            workspace_id=w.ws,
            campaign_id=w.campaign_id,
            audience_id=w.audience_id,
            audience_member_id=w.member_ids[0],
            sequence_id=w.sequence_id,
            lead_id=w.lead_ids[0],
            address_id=w.address_ids[0],
            frozen_destination="lead0@target0.test",
            state="ACTIVE",
            next_step_id=w.step1,
            next_sequence_position=1,
            assigned_mailbox_id=w.mailbox_id,
            frozen_variables=frozen or FROZEN,
        )
        w.message1_id = uuid.uuid4()
        _insert(
            conn,
            "messages",
            id=w.message1_id,
            workspace_id=w.ws,
            purpose="CAMPAIGN",
            campaign_id=w.campaign_id,
            enrollment_id=w.enrollment_id,
            sequence_id=w.sequence_id,
            step_id=w.step1,
            mailbox_id=w.mailbox_id,
            address_id=w.address_ids[0],
            status="PLANNED",
        )
    conn.execute("SET session_replication_role = origin")
    return w
