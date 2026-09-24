from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.modules.replies.schemas import (
    NormalizedInboundMessage,
    ReplyConfidence,
    ReplyEvidenceType,
    ReplyMatchCandidate,
    ReplyMatchResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundMessageCandidate:
    """Outbound message row loaded for reply correlation."""

    message_id: UUID
    campaign_id: UUID
    enrollment_id: UUID
    mailbox_id: UUID
    rfc_message_id: str | None
    provider_message_id: str | None
    provider_thread_id: str | None
    frozen_destination: str
    sent_at: str | None = None


class ReplyMatcher:
    """Conservative reply matching engine.

    Enforces:
    1. RFC Headers First: In-Reply-To or References matching outbound rfc_message_id,
       corroborated by recipient destination matching inbound sender.
    2. Provider Thread Fallback: thread ID match corroborated by recipient destination.
    3. Multi-campaign Isolation:
       - Match attaches strictly to the corroborated outbound campaign/enrollment.
       - If multiple conflicting campaigns match with equal ambiguous evidence,
         mark as UNRESOLVED (do not guess).
    4. Never match by subject or email address alone.
    5. OOO / automated replies are classified and linked, but flagged so they do
       not terminate campaign enrollments.
    """

    @classmethod
    def match(
        cls,
        inbound: NormalizedInboundMessage,
        candidates: list[OutboundMessageCandidate],
    ) -> ReplyMatchResult:
        if not candidates:
            return ReplyMatchResult(
                is_matched=False,
                association_status="UNRESOLVED",
                matched_candidate=None,
                candidates=[],
                classification=inbound.classification,
            )

        clean_sender = (inbound.from_address or "").strip().lower()
        if not clean_sender:
            return ReplyMatchResult(
                is_matched=False,
                association_status="UNRESOLVED",
                matched_candidate=None,
                candidates=[],
                classification=inbound.classification,
            )

        # Filter candidates strictly by corroborated participant
        # Outbound frozen_destination must match inbound sender
        corroborated_candidates = [
            c for c in candidates
            if (c.frozen_destination or "").strip().lower() == clean_sender
        ]

        if not corroborated_candidates:
            # Sender cannot be corroborated with candidate recipient
            logger.debug(
                "Reply matching skipped: sender %s does not match any candidate destination",
                clean_sender,
            )
            return ReplyMatchResult(
                is_matched=False,
                association_status="UNRESOLVED",
                matched_candidate=None,
                candidates=[],
                classification=inbound.classification,
            )

        # 1. Check exact In-Reply-To
        if inbound.in_reply_to:
            target_in_reply_to = inbound.in_reply_to.strip()
            in_reply_matches = [
                c for c in corroborated_candidates
                if c.rfc_message_id and c.rfc_message_id.strip() == target_in_reply_to
            ]
            if len(in_reply_matches) == 1:
                c = in_reply_matches[0]
                matched = ReplyMatchCandidate(
                    outbound_message_id=c.message_id,
                    campaign_id=c.campaign_id,
                    enrollment_id=c.enrollment_id,
                    evidence_type=ReplyEvidenceType.RFC_HEADER_MATCH.value,
                    confidence=ReplyConfidence.HIGH.value,
                )
                return ReplyMatchResult(
                    is_matched=True,
                    association_status="MATCHED",
                    matched_candidate=matched,
                    candidates=[matched],
                    classification=inbound.classification,
                )
            elif len(in_reply_matches) > 1:
                # Multiple outbound messages have same rfc_message_id (rare duplicate)
                # Check if they belong to the same campaign/enrollment
                unique_enrollments = {c.enrollment_id for c in in_reply_matches}
                if len(unique_enrollments) == 1:
                    c = in_reply_matches[0]
                    matched = ReplyMatchCandidate(
                        outbound_message_id=c.message_id,
                        campaign_id=c.campaign_id,
                        enrollment_id=c.enrollment_id,
                        evidence_type=ReplyEvidenceType.RFC_HEADER_MATCH.value,
                        confidence=ReplyConfidence.HIGH.value,
                    )
                    return ReplyMatchResult(
                        is_matched=True,
                        association_status="MATCHED",
                        matched_candidate=matched,
                        candidates=[matched],
                        classification=inbound.classification,
                    )
                else:
                    # Ambiguous across campaigns
                    return cls._create_ambiguous_result(in_reply_matches, inbound.classification)

        # 2. Check References list
        if inbound.references:
            cleaned_refs = {ref.strip() for ref in inbound.references if ref.strip()}
            ref_matches = [
                c for c in corroborated_candidates
                if c.rfc_message_id and c.rfc_message_id.strip() in cleaned_refs
            ]
            if ref_matches:
                unique_enrollments = {c.enrollment_id for c in ref_matches}
                if len(unique_enrollments) == 1:
                    # All references belong to same enrollment; select the most specific/latest
                    c = ref_matches[-1]
                    matched = ReplyMatchCandidate(
                        outbound_message_id=c.message_id,
                        campaign_id=c.campaign_id,
                        enrollment_id=c.enrollment_id,
                        evidence_type=ReplyEvidenceType.RFC_HEADER_MATCH.value,
                        confidence=ReplyConfidence.HIGH.value,
                    )
                    return ReplyMatchResult(
                        is_matched=True,
                        association_status="MATCHED",
                        matched_candidate=matched,
                        candidates=[matched],
                        classification=inbound.classification,
                    )
                else:
                    # Multiple conflicting enrollments matched via references
                    return cls._create_ambiguous_result(ref_matches, inbound.classification)

        # 3. Fallback: Provider Thread ID Corroborated
        if inbound.provider_thread_id:
            target_thread = inbound.provider_thread_id.strip()
            thread_matches = [
                c for c in corroborated_candidates
                if c.provider_thread_id and c.provider_thread_id.strip() == target_thread
            ]
            if len(thread_matches) == 1:
                c = thread_matches[0]
                matched = ReplyMatchCandidate(
                    outbound_message_id=c.message_id,
                    campaign_id=c.campaign_id,
                    enrollment_id=c.enrollment_id,
                    evidence_type=ReplyEvidenceType.PROVIDER_THREAD_CORROBORATED.value,
                    confidence=ReplyConfidence.MEDIUM.value,
                )
                return ReplyMatchResult(
                    is_matched=True,
                    association_status="MATCHED",
                    matched_candidate=matched,
                    candidates=[matched],
                    classification=inbound.classification,
                )
            elif len(thread_matches) > 1:
                unique_enrollments = {c.enrollment_id for c in thread_matches}
                if len(unique_enrollments) == 1:
                    c = thread_matches[-1]
                    matched = ReplyMatchCandidate(
                        outbound_message_id=c.message_id,
                        campaign_id=c.campaign_id,
                        enrollment_id=c.enrollment_id,
                        evidence_type=ReplyEvidenceType.PROVIDER_THREAD_CORROBORATED.value,
                        confidence=ReplyConfidence.MEDIUM.value,
                    )
                    return ReplyMatchResult(
                        is_matched=True,
                        association_status="MATCHED",
                        matched_candidate=matched,
                        candidates=[matched],
                        classification=inbound.classification,
                    )
                else:
                    return cls._create_ambiguous_result(thread_matches, inbound.classification)

        # No conservative match found (never guess by subject or address)
        return ReplyMatchResult(
            is_matched=False,
            association_status="UNRESOLVED",
            matched_candidate=None,
            candidates=[],
            classification=inbound.classification,
        )

    @staticmethod
    def _create_ambiguous_result(
        conflicting_candidates: list[OutboundMessageCandidate],
        classification: str,
    ) -> ReplyMatchResult:
        candidates = [
            ReplyMatchCandidate(
                outbound_message_id=c.message_id,
                campaign_id=c.campaign_id,
                enrollment_id=c.enrollment_id,
                evidence_type=ReplyEvidenceType.AMBIGUOUS_CANDIDATE.value,
                confidence=ReplyConfidence.LOW.value,
            )
            for c in conflicting_candidates
        ]
        return ReplyMatchResult(
            is_matched=False,
            association_status="UNRESOLVED",
            matched_candidate=None,
            candidates=candidates,
            classification=classification,
        )
