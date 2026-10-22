"""Tests for the structure-aware chunker. All pure -- no network, no files."""
from app import chunker


def test_html_to_text_strips_markup_and_normalizes():
    html = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var a=1;</script></head><body>"
        "<p>Hello&nbsp;&nbsp;world</p><div>Second line</div></body></html>"
    )
    text = chunker.html_to_text(html)
    assert "color:red" not in text and "var a" not in text
    assert "<" not in text and ">" not in text
    assert "Hello world" in text          # collapsed non-breaking spaces
    assert "Second line" in text
    assert text.splitlines() == ["Hello world", "Second line"]  # one block per line


def test_item_headings_become_sections():
    text = (
        "Item 1. Business\n"
        "We design accelerated computing platforms for many markets worldwide.\n"
        "Item 1A. Risk Factors\n"
        "Our business is subject to numerous risks that could materially harm results."
    )
    chunks = chunker.chunk_text(text, max_chars=400, overlap=20)
    sections = [c["section"] for c in chunks]
    assert "Item 1. Business" in sections
    assert "Item 1A. Risk Factors" in sections
    # content lands under the right heading
    risk = next(c for c in chunks if c["section"] == "Item 1A. Risk Factors")
    assert "numerous risks" in risk["text"]


def test_heading_forces_a_boundary():
    # Two items whose combined text easily fits one chunk must still split at the
    # heading so sections don't bleed together.
    text = "Item 7. MD&A\n" + ("alpha " * 10) + "\nItem 8. Financials\n" + ("beta " * 10)
    chunks = chunker.chunk_text(text, max_chars=5000, overlap=20)
    assert len(chunks) == 2
    assert chunks[0]["section"] == "Item 7. MD&A"
    assert chunks[1]["section"] == "Item 8. Financials"
    assert "beta" not in chunks[0]["text"]


def test_size_cap_and_overlap():
    tokens = "".join(f"{i:03d}-" for i in range(400))  # 1600 chars, no whitespace
    max_chars, overlap = 100, 12
    chunks = chunker.chunk_text(tokens, max_chars=max_chars, overlap=overlap)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= max_chars + overlap + 2  # cap respected (+ carried overlap)
    # consecutive chunks share the carried overlap for continuity
    for a, b in zip(chunks, chunks[1:]):
        assert a["text"][-overlap:] == b["text"][:overlap]


def test_ordinals_are_sequential():
    text = "\n".join(f"Paragraph number {i} with enough words to be kept." for i in range(20))
    chunks = chunker.chunk_text(text, max_chars=120, overlap=10)
    assert [c["ordinal"] for c in chunks] == list(range(1, len(chunks) + 1))


def test_tiny_fragments_dropped():
    # A heading with no real body should not become a chunk.
    chunks = chunker.chunk_text("Item 5.", max_chars=400, overlap=20)
    assert chunks == []
