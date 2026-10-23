# Design

A short tour of the five decisions that shape Filing Analyst: how questions are
routed, how the system stays safe, how filings are chunked, how mismatched fiscal
calendars are handled, and how the whole thing is evaluated.

## 1. Routing — "vectorize the fuzzy thing, query the exact thing"

The agent is a function-calling loop with two tools:

- **`query_financials`** — a read-only SQL `SELECT` over `financials.db`, for anything
  quantitative (revenue, margins, segment/geographic revenue, balance-sheet items,
  growth rates derived from rows).
- **`search_filings`** — semantic search over the 10-K text, for narrative (risk
  factors, strategy, competition, segment definitions, management discussion).

The model decides per question which to call, and many questions use both: pull the
figures with SQL, then explain or contextualize them with the filing text. This beats
keyword-based routing because the decision is made with full understanding of the
question, and the two sources have complementary failure modes — SQL is exact but
mute on causes; retrieval is rich but can't be trusted for a precise number.

**Control flow.** The loop runs up to `MAX_ITERS` plan→call→observe cycles. If the
model returns no tool calls, that message is the final answer. If it hits the cap
mid-plan, a final synthesis step runs with tools disabled so the user always gets a
real answer instead of a dangling tool request. `temperature=0` keeps eval runs
reproducible.

**Evidence.** Every tool call is recorded — the exact SQL string and row count, or
the search query and the ranked source list (ticker, fiscal year, section, chunk,
score) — and returned alongside the answer. The UI renders this in a collapsible
panel. The point is that a reader can audit *how* an answer was reached, not just
read a confident paragraph.

## 2. Safety model

**Read-only SQL, enforced at two independent layers.**

1. The connection is opened with SQLite's `file:…?mode=ro` URI (plus `PRAGMA
   query_only=ON`). This is OS-level: the handle physically cannot mutate the file,
   so even a bug in the validator cannot cause a write.
2. A validator rejects anything that isn't a single `SELECT`/`WITH` statement:
   it strips one trailing semicolon, rejects any remaining semicolon (no stacked
   statements), requires a `select`/`with` prefix, and blocks a denylist of
   write/DDL/attach keywords.

Results are capped (`MAX_ROWS`) so a broad query can't blow the context window, and
the executed SQL is echoed back as evidence. Tests assert that writes, DDL, and
multi-statement inputs are rejected, that read-only CTEs and the `REPLACE()` string
function are accepted, and that the read-only connection blocks a write even if the
validator is bypassed.

**Scope guard.** The system prompt limits the agent to NVIDIA/AMD/Intel financials
and filings. Off-topic requests (another company as the sole subject, general
knowledge, coding help) and prompt-injection/role-change attempts are declined in one
sentence, while partially in-scope questions are answered for their in-scope part.
When genuinely unsure, the agent is told to answer rather than refuse.

**Output handling.** The chat UI HTML-escapes all model output before rendering a
limited Markdown subset (headings, bold, lists, tables, code), so model text can
never inject markup into the page.

## 3. Chunking

10-Ks are long inline-XBRL HTML documents. The chunker:

1. **Extracts text** — drops scripts, styles, and the hidden XBRL header, normalizes
   whitespace (filings are full of non-breaking spaces), and emits one text block per
   line.
2. **Breaks on structure** — starts a fresh chunk at every 10-K item heading
   (`Item 1A. Risk Factors`, `Item 7. MD&A`, …) so a chunk never straddles two
   sections.
3. **Packs and size-caps** — fills chunks up to a character budget, carrying a small
   overlap across size boundaries so sentences aren't orphaned, and hard-wraps any
   single oversized block. Tiny fragments are dropped.

Each chunk keeps its **section label and ordinal**, which become the citation anchor
(*"NVDA FY2026 10-K, Item 1A, chunk 148"*). HTML filings have no page numbers, so a
section + ordinal is both more stable and more meaningful than a synthetic page.

**Retrieval.** Exact cosine similarity over a numpy matrix: both the stored vectors
and the query vector are **L2-normalized** (embedding models don't always return unit
vectors, so a raw dot product would be biased by magnitude, not direction). A metadata
pre-filter restricts ranking to the requested company/year when the agent supplies
one. This lives behind a `Retriever` protocol so a vector DB can replace it with no
changes elsewhere — right-sized for ~2,500 chunks, not over-built.

## 4. Fiscal-year handling

NVIDIA's fiscal year ends in **late January**; AMD and Intel end in **late December**.
A naive "FY2025 vs FY2025" comparison across companies would line up the wrong periods.

Decisions:

- **Fiscal-year label = the calendar year the period ends in.** NVIDIA's FY2026 ends
  January 2026; AMD/Intel's FY2025 ends December 2025. So NVDA FY2026 and AMD/INTC
  FY2025 cover roughly the same span.
- **Period end dates are stored explicitly** on every metric and in `fiscal_periods`,
  and surfaced in the SQL schema hint. The agent is instructed to align on
  `period_end`, not the FY number, when comparing "the same" year across companies —
  and it does (see the growth and margin examples, which correctly compare NVDA FY2026
  against AMD/INTC FY2025).
- **Fiscal years are derived from each fact's period end**, not the filer's reported
  `fy` field, because a 10-K restates prior years under its own fiscal year.

## 5. Evaluation methodology

`eval/questions.jsonl` holds 18 questions in three categories: `lookup` (one exact
figure, SQL only), `narrative` (qualitative, from filing text), and `synthesis`
(multi-step and/or cross-company, both tools).

**Gold answers come from the data, not memory.** Each numeric question stores the SQL
that derives its gold value; `make_gold.py` runs those queries against the built
database to produce the answer key. This guarantees the gold numbers match the data
the agent actually queries.

**Three scorers**, chosen per question:

- **numeric** — extracts figures from the answer (handling `$`, `%`, and
  billion/million scale words) and passes if any is within ±2% of the gold value. It
  is loss-aware: a negative gold figure matches a "net loss of \$X" phrasing or a
  parenthesized/negative number, so a loss isn't marked wrong for lacking a minus
  sign, while a *profit* claim against a loss gold still fails.
- **entity** — passes only if the answer names every required entity
  (case-insensitive).
- **judge** — an LLM grades the answer against a written gold answer and returns a
  structured `{score, reason}` (JSON-schema enforced, so there's no prose-parsing);
  `score ≥ 0.7` passes.

`run_eval.py` writes a machine-readable `results/latest.json` (answers + evidence +
verdicts) and a human-readable `results/summary.md` (pass rates by category + a
per-question table). The numeric/entity scorers are unit-tested; the judge is not
(it needs the provider).

**What the eval is and isn't.** It is a sanity-and-regression harness over a small,
curated set — useful for catching breakage and for demonstrating the evidence trail,
not a claim of universal accuracy. The ±2% tolerance, the model-based judge, and
self-written gold answers are all acknowledged limitations. The real guarantee the
system offers is auditability: every answer ships with the SQL and sources behind it.
