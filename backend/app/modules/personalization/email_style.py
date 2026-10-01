"""What a good cold email looks like (ADR-0018).

One place for the craft rules that both reference drafting (reference_validator,
draft_prompt) and per-lead generation (validator, prompt_builder) hold an email
to, so the two never drift apart (AGENTS.md: reuse authoritative logic). Pure and
I/O free.

The rules come from published cold-email research: a first email under about 100
words, short one-idea paragraphs that stay readable on a phone, a low-friction
question as the ask, and follow-ups that each do a different job instead of
repeating the first email.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# A paragraph is one idea of one or two sentences. Longer is a wall of text on a
# phone (two desktop lines become four or five).
MAX_PARAGRAPH_WORDS = 40
# Greeting + at least two more paragraphs once the email says more than a line.
MIN_PARAGRAPHS = 3
MIN_WORDS_FOR_STRUCTURE = 30
# The ask is one of the last three paragraphs (a sign-off or P.S. may follow it).
ASK_WINDOW = 3
# A per-lead email may be a little longer than its reference, never much longer.
LENGTH_SLACK = 1.35
MIN_LENGTH_CEILING = 60

_GENERIC_OPENER_RE = re.compile(
    r"\b(i\s+hope\s+(this\s+(email\s+|message\s+)?)?finds\s+you|"
    r"hope\s+you(?:'|’)?(re|\s+are)\s+(doing\s+)?(well|good|great)|"
    r"i(?:'|’)?m\s+reaching\s+out|i\s+am\s+reaching\s+out|"
    r"i\s+wanted\s+to\s+(reach\s+out|introduce)|"
    r"my\s+name\s+is|allow\s+me\s+to\s+introduce|"
    r"we\s+are\s+a\s+leading|leading\s+provider\s+of)\b",
    re.IGNORECASE,
)

# What each shape code tells the model on the next attempt (codes only, never
# the rejected text).
SHAPE_GUIDANCE: dict[str, str] = {
    "paragraph_too_dense": (
        "Keep every paragraph to one or two short sentences (about 35 words at most). "
        "Split long paragraphs."
    ),
    "too_few_paragraphs": (
        "Use separate short paragraphs: greeting, hook, value, then the ask."
    ),
    "ask_not_question": (
        "End with one short, low-friction question that asks about interest."
    ),
    "generic_opener": (
        "Do not open with pleasantries or an introduction of the sender, such as "
        "'I hope this finds you well', 'I'm reaching out' or 'My name is'. Start "
        "with the reader's situation."
    ),
}


@dataclass(frozen=True)
class StepBrief:
    """The job one email does in a sequence."""

    job: str
    instruction: str
    min_words: int
    max_words: int


_INTRO = StepBrief(
    job="intro",
    instruction=(
        "The first email. Hook: one sentence about the reader's situation or a "
        "problem they likely have (not about you). Value: one or two sentences on "
        "the single outcome you deliver for people like them. Ask: one low-friction "
        "question about interest."
    ),
    min_words=40,
    max_words=100,
)
_NEW_ANGLE = StepBrief(
    job="new_angle",
    instruction=(
        "A follow-up from a different angle than the first email: lead with a "
        "different problem or outcome and ask a different question. Do not "
        "introduce the company again and do not repeat the earlier pitch."
    ),
    min_words=25,
    max_words=80,
)
_PROOF = StepBrief(
    job="proof",
    instruction=(
        "A follow-up that makes it concrete: a short example of how the offer "
        "works or the result it aims for, using only what the objective or company "
        "details say (never invent customers, numbers or results). End with a "
        "question."
    ),
    min_words=25,
    max_words=80,
)
_INSIGHT = StepBrief(
    job="insight",
    instruction=(
        "A follow-up that shares one useful, specific observation about the "
        "reader's problem, ties it to the offer in one line, then asks a light "
        "question."
    ),
    min_words=25,
    max_words=80,
)
_CLOSE = StepBrief(
    job="close",
    instruction=(
        "A short, friendly last note: it may not be a priority right now, restate "
        "the one benefit in a single line, and ask whether to close the loop or "
        "who the right person is."
    ),
    min_words=15,
    max_words=50,
)


def step_brief(position: int, total: int) -> StepBrief:
    """The job of email `position` (1-based) in a sequence of `total` emails.
    Assigned by code, not left to the model, so no two emails make the same
    point."""
    if position <= 1:
        return _INTRO
    if total >= 3 and position >= total:
        return _CLOSE
    follow_up = position - 1
    if follow_up == 1:
        return _NEW_ANGLE
    if follow_up == 2:
        return _PROOF
    return _INSIGHT


def max_words_for_reference(reference_words: int) -> int:
    """Ceiling for a per-lead email, relative to the reference it personalizes."""
    return max(round(reference_words * LENGTH_SLACK), MIN_LENGTH_CEILING)


def shape_codes(paragraphs: Sequence[str]) -> list[str]:
    """Structure problems of an email given as readable paragraphs (merge
    variables already replaced by their fallbacks)."""
    codes: list[str] = []
    if not paragraphs:
        return codes
    words = sum(len(p.split()) for p in paragraphs)
    if any(len(p.split()) > MAX_PARAGRAPH_WORDS for p in paragraphs):
        codes.append("paragraph_too_dense")
    if words >= MIN_WORDS_FOR_STRUCTURE and len(paragraphs) < MIN_PARAGRAPHS:
        codes.append("too_few_paragraphs")
    if "?" not in " ".join(paragraphs[-ASK_WINDOW:]):
        codes.append("ask_not_question")
    if _GENERIC_OPENER_RE.search(" ".join(paragraphs)):
        codes.append("generic_opener")
    return codes


__all__ = [
    "SHAPE_GUIDANCE",
    "StepBrief",
    "max_words_for_reference",
    "shape_codes",
    "step_brief",
]
