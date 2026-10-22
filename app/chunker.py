"""Structure-aware chunking of 10-K filings.

10-Ks are long HTML documents organized by numbered items (Item 1 Business,
Item 1A Risk Factors, Item 7 MD&A, ...). We extract readable text, break on those
item headings so each chunk stays within one coherent section, then pack and
size-cap. Each chunk carries its section label and an ordinal so an answer can
cite "NVDA FY2026 10-K, Item 7, chunk 4" -- a stable anchor, since HTML filings
have no page numbers.

Kept isolated from embeddings and the index format so the chunking strategy stays
swappable. The HTML step and the text step are separate functions, so the
packing logic is unit-tested on plain text without any network or files.
"""
from __future__ import annotations

import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# A line that begins a 10-K item section, e.g. "Item 1A. Risk Factors" or
# "ITEM 7. MANAGEMENT'S DISCUSSION". Anchored at line start, item number + an
# optional letter suffix.
_HEADING_RE = re.compile(r"^item\s+(\d+[a-z]?)\b[\.\)\s:-]*(.*)$", re.IGNORECASE)

DEFAULT_MAX_CHARS = 1400
DEFAULT_OVERLAP = 150


def html_to_text(html: bytes | str) -> str:
    """Extract readable text from a filing's HTML.

    Drops scripts/styles and the hidden inline-XBRL header, normalizes
    whitespace (filings are full of non-breaking spaces), and returns one text
    block per line so the chunker can reason about structure.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for header in soup.find_all("ix:header"):  # hidden XBRL context/unit block
        header.decompose()
    raw = soup.get_text("\n")
    lines = []
    for line in raw.split("\n"):
        line = re.sub(r"[ \t ​]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _section_label(number: str, title: str) -> str:
    """Build a tidy section label like 'Item 1A. Risk Factors' (title truncated)."""
    number = number.upper()
    title = re.sub(r"\s+", " ", title).strip(" .:-")
    label = f"Item {number}"
    if title and len(title) <= 60 and not title[0].isdigit():
        label = f"{label}. {title}"
    return label


def _split_long(line: str, max_chars: int) -> list[str]:
    """Hard-wrap a single oversized line (e.g. an un-delimited paragraph)."""
    return [line[i : i + max_chars] for i in range(0, len(line), max_chars)]


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS,
               overlap: int = DEFAULT_OVERLAP) -> list[dict]:
    """Split filing text into {section, ordinal, text} chunks.

    Rules: start a fresh chunk at every item heading; otherwise pack lines up to
    max_chars, carrying `overlap` characters across size boundaries so sentences
    aren't orphaned. Tiny fragments are dropped.
    """
    chunks: list[dict] = []
    section = "Front matter"
    buf: list[str] = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        body = "\n".join(buf).strip()
        if len(body) >= 40:  # drop heading-only / noise fragments
            chunks.append({"section": section, "text": body})
        buf, buf_len = [], 0

    for line in text.split("\n"):
        heading = _HEADING_RE.match(line)
        if heading and len(line) <= 100:
            flush()
            section = _section_label(heading.group(1), heading.group(2))
            buf, buf_len = [line], len(line)
            continue

        for piece in ([line] if len(line) <= max_chars else _split_long(line, max_chars)):
            if buf and buf_len + len(piece) + 1 > max_chars:
                tail = "\n".join(buf)[-overlap:]  # carry context across the boundary
                flush()
                buf, buf_len = [tail], len(tail)
            buf.append(piece)
            buf_len += len(piece) + 1

    flush()
    for i, chunk in enumerate(chunks, start=1):
        chunk["ordinal"] = i
    return chunks


def chunk_filing(html: bytes | str, max_chars: int = DEFAULT_MAX_CHARS,
                 overlap: int = DEFAULT_OVERLAP) -> list[dict]:
    """Convenience: HTML bytes -> structure-aware chunks."""
    return chunk_text(html_to_text(html), max_chars=max_chars, overlap=overlap)
