"""Small, dependency-free text helpers shared by the context builder and the
validator. Pure functions only."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{7,}\d(?![\w])")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_WORD_RE = re.compile(r"[a-z][a-z'\-]{3,}")
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?"

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
    }
)
_SKIP_TAGS = frozenset(
    {"script", "style", "head", "title", "noscript", "template", "svg"}
)

STOPWORDS = frozenset(
    """
    about above after again also always among another because been before being
    between both could does doing down during each even every from further have
    having here hers herself himself into itself just like make many might more
    most much must myself need only other ours ourselves over same should some
    such than that their theirs them themselves then there these they this those
    through under until very want well were what when where which while whom
    will with would your yours yourself yourselves hello thanks thank best
    regards
    """.split()
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html_content: str) -> str:
    """Visible text of an HTML fragment with block boundaries kept as newlines."""
    parser = _TextExtractor()
    parser.feed(html_content or "")
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    # Collapse runs of blank lines to a single paragraph break.
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def split_paragraphs(text: str) -> tuple[str, ...]:
    """Split visible text into paragraphs; single newlines stay inside one."""
    return tuple(p.strip() for p in re.split(r"\n\s*\n", text) if p.strip())


def strip_trailing_punct(url: str) -> str:
    return url.rstrip(_TRAILING_PUNCT)


def extract_urls(text: str) -> set[str]:
    return {strip_trailing_punct(m.group(0)) for m in _URL_RE.finditer(text or "")}


def extract_hrefs(html_content: str) -> set[str]:
    return {
        m.group(1).strip()
        for m in _HREF_RE.finditer(html_content or "")
        if m.group(1).strip().lower().startswith(("http://", "https://"))
    }


def extract_emails(text: str) -> set[str]:
    return {m.group(0).lower() for m in _EMAIL_RE.finditer(text or "")}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def extract_phones(text: str) -> set[str]:
    """Digit-only forms of phone-like sequences (so formatting differences match)."""
    scrubbed = _EMAIL_RE.sub(" ", _URL_RE.sub(" ", text or ""))
    return {
        _digits(m.group(0))
        for m in _PHONE_RE.finditer(scrubbed)
        if len(_digits(m.group(0))) >= 8
    }


def extract_numbers(text: str) -> set[str]:
    """Numeric tokens (commas removed), excluding those inside URLs, emails and
    phone numbers, which are checked separately."""
    scrubbed = _EMAIL_RE.sub(" ", _URL_RE.sub(" ", text or ""))
    scrubbed = _PHONE_RE.sub(" ", scrubbed)
    return {m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(scrubbed)}


def significant_tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in STOPWORDS}


def word_shingles(text: str, size: int = 3) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < size:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def containment(new: set[tuple[str, ...]], old: set[tuple[str, ...]]) -> float:
    """Share of `new` that is copied from `old` (1.0 = nothing new). Unlike
    Jaccard it is not diluted when a copied email gets a line appended."""
    if not new or not old:
        return 0.0
    return len(new & old) / len(new)


def find_urls_with_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, url) for every URL in text, with trailing punctuation left
    outside the span."""
    found: list[tuple[int, int, str]] = []
    for m in _URL_RE.finditer(text):
        raw = m.group(0)
        url = strip_trailing_punct(raw)
        found.append((m.start(), m.start() + len(url), url))
    return found
