from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple


class OpenClassification(NamedTuple):
    qualified: bool
    reason: str


# A pixel request cannot prove a person opened the email. Scanners, prefetchers
# and privacy proxies fetch the same URL. So a hit counts as an open only when
# every signal points to a person, and anything unclear is NOT counted
# (default-deny): the alternative is reporting opens nobody made.

_PREFETCH_HEADERS = ("purpose", "sec-purpose", "x-purpose", "x-moz")
_PREFETCH_VALUES = ("prefetch", "preview")

_AUTOMATED_UA = re.compile(
    r"bot\b|crawl|spider|slurp|scan|monitor|check|fetch|preview|"
    r"headless|phantom|puppeteer|playwright|selenium|"
    r"curl/|wget|python|requests|httpx|aiohttp|urllib|java/|okhttp/|go-http|libwww|"
    r"node-fetch|axios|postman|insomnia|"
    r"barracuda|mimecast|proofpoint|ironport|symantec|trend ?micro|forcepoint|"
    r"cisco|fireeye|sophos|zscaler|safelinks|defender|"
    r"slack|facebookexternalhit|twitter|whatsapp|telegram|skype|linkedin|discord",
    re.IGNORECASE,
)

# Positive signatures of real mail clients and browsers. Gmail opens arrive
# through Google's image proxy, so ``GoogleImageProxy`` must qualify.
_HUMAN_UA = re.compile(
    r"^mozilla/|googleimageproxy|thunderbird|microsoft outlook|outlook-|"
    r"apple ?mail|yahoomail|yahoo!? ?mail|airmail|superhuman",
    re.IGNORECASE,
)


def classify_open(
    *,
    user_agent: str | None,
    headers: Mapping[str, str],
    sent_at: datetime | None,
    now: datetime,
    min_delay_seconds: int,
) -> OpenClassification:
    """Decide whether one pixel request looks like a person opening the email.

    ``sent_at`` is None for tokens minted before send time was signed in; those
    cannot prove the fetch was not a delivery-time scan, so they never qualify.
    """
    if sent_at is None:
        return OpenClassification(False, "no_send_time")
    if (now - sent_at).total_seconds() < min_delay_seconds:
        return OpenClassification(False, "too_soon_after_send")

    lowered = {k.lower(): v.lower() for k, v in headers.items()}
    for name in _PREFETCH_HEADERS:
        value = lowered.get(name, "")
        if any(marker in value for marker in _PREFETCH_VALUES):
            return OpenClassification(False, "prefetch_header")

    agent = (user_agent or "").strip()
    if not agent:
        return OpenClassification(False, "missing_user_agent")
    if _AUTOMATED_UA.search(agent):
        return OpenClassification(False, "automated_user_agent")
    if not _HUMAN_UA.search(agent):
        return OpenClassification(False, "unrecognised_user_agent")
    return OpenClassification(True, "human_like")
