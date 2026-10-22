"""Read-only text-to-SQL tool over data/financials.db.

The agent writes a SELECT given the schema hint; we enforce read-only at two
independent layers -- an OS-level `mode=ro` connection AND a single-statement
SELECT/WITH validator -- cap the rows we return, and echo the exact SQL back so
every numeric answer is inspectable.

All money values are stored in whole US dollars (e.g. revenue 215938000000).
"""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

import config

# Only these tables are described to the model; internal tables stay hidden.
_TABLES = ["companies", "fiscal_periods", "metrics", "segment_revenue"]

# Cap the rows serialized back to the model so a broad query can't blow the
# context window. The dataset is small, so this is generous.
MAX_ROWS = 200

# Anything that writes, changes structure, or reaches outside the database. We
# intentionally do NOT list REPLACE (REPLACE() is a legitimate string function)
# -- the mode=ro connection blocks writes regardless.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|"
    r"REINDEX|TRIGGER|GRANT|REVOKE|TRUNCATE|UPSERT)\b",
    re.IGNORECASE,
)


def _connect() -> sqlite3.Connection:
    """Open the DB read-only. `mode=ro` is the real enforcement -- the OS handle
    cannot mutate the file even if the validator ever missed something. The
    query_only pragma is a belt-and-braces second guard."""
    if not config.DB_PATH.exists():
        raise FileNotFoundError(
            f"Financials DB not found at {config.DB_PATH}. "
            "Run `python -m scripts.build_financials_db` first."
        )
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def validate(sql: str) -> str | None:
    """Return an error message if `sql` is not a single read-only SELECT/WITH
    query, else None. Pure function -- the unit tests exercise it directly."""
    statement = (sql or "").strip().rstrip(";").strip()
    if not statement:
        return "Empty query."
    if ";" in statement:
        return "Only a single statement is allowed (no semicolons)."
    head = statement.lower()
    # WITH is allowed so read-only CTEs are available for multi-step questions;
    # the forbidden-keyword check below still blocks any writes inside them.
    if not (head.startswith("select") or head.startswith("with")):
        return "Only read-only SELECT (or WITH ... SELECT) queries are allowed."
    if _FORBIDDEN.search(statement):
        return "Query rejected: contains a non-read-only keyword."
    return None


def run_sql(sql: str) -> dict:
    """Execute a validated read-only query. Returns
    {sql, columns, rows, row_count[, note]} on success, or {sql, error}. Always
    echoes the SQL so the answer stays auditable."""
    error = validate(sql)
    if error:
        return {"sql": sql, "error": error}
    try:
        con = _connect()
        try:
            cursor = con.execute(sql)
            columns = [c[0] for c in cursor.description] if cursor.description else []
            fetched = cursor.fetchmany(MAX_ROWS + 1)
        finally:
            con.close()
    except (sqlite3.Error, FileNotFoundError) as exc:
        return {"sql": sql, "error": f"SQL error: {exc}"}

    truncated = len(fetched) > MAX_ROWS
    rows = [dict(r) for r in fetched[:MAX_ROWS]]
    result = {"sql": sql, "columns": columns, "rows": rows, "row_count": len(rows)}
    if truncated:
        result["note"] = f"Result truncated to the first {MAX_ROWS} rows."
    return result


@lru_cache(maxsize=1)
def schema_text() -> str:
    """Render a compact, agent-facing schema description from the live database.

    Cached so every agent turn reuses one string. Includes the fiscal-calendar
    guidance and the segment-nesting caveat the model needs to query correctly.
    """
    con = _connect()
    try:
        lines = [
            "Database: data/financials.db (SQLite, read-only). "
            "All money values are whole US dollars (e.g. revenue 215938000000 = $215.938B).",
            "",
            "Tables:",
        ]
        for table in _TABLES:
            cols = [row["name"] for row in con.execute(f"PRAGMA table_info({table})")]
            lines.append(f"  {table}({', '.join(cols)})")

        lines.append("")
        lines.append("Companies and fiscal calendars:")
        for r in con.execute("SELECT ticker, name, fiscal_year_end FROM companies ORDER BY ticker"):
            lines.append(f"  {r['ticker']} ({r['name']}) -- fiscal year ends {r['fiscal_year_end']}")
        lines.append(
            "  A fiscal-year LABEL is the calendar year the period ENDS in, so NVDA FY2026 "
            "(ends Jan 2026) lines up with AMD/INTC FY2025 (end Dec 2025). When comparing "
            "companies for the 'same' period, align on period_end, not on the FY number."
        )

        metrics = [r["metric"] for r in con.execute(
            "SELECT DISTINCT metric FROM metrics ORDER BY metric")]
        lines.append("")
        lines.append("metrics.metric canonical values (use WHERE metric='<name>'):")
        lines.append("  " + ", ".join(metrics))
        lines.append("  Units: USD, except eps_basic/eps_diluted (USD/shares) and shares_diluted (shares).")
        lines.append("  Not every metric exists for every company: total_liabilities is untagged for "
                     "AMD/INTC (derive as total_assets - stockholders_equity); NVDA has no capex row.")

        lines.append("")
        lines.append("Fiscal years available (has_filing=1 means the 10-K text is also searchable):")
        for r in con.execute(
            "SELECT ticker, GROUP_CONCAT(fiscal_year || CASE has_filing WHEN 1 THEN '*' ELSE '' END, ', ') ys "
            "FROM (SELECT * FROM fiscal_periods ORDER BY fiscal_year) GROUP BY ticker ORDER BY ticker"):
            lines.append(f"  {r['ticker']}: {r['ys']}   (* = filing available)")

        dims = [r["dimension"] for r in con.execute(
            "SELECT DISTINCT dimension FROM segment_revenue ORDER BY dimension")]
        lines.append("")
        lines.append(f"segment_revenue.dimension values: {', '.join(dims)}.")
        lines.append(
            "  Geographic members are a clean partition that sums to total revenue. "
            "business_segment/product members MAY NEST (e.g. NVDA 'Data Center' includes "
            "'Compute'+'Networking') and may include intersegment revenue -- do NOT blindly "
            "SUM them; query the specific member. Segment/geo data covers only has_filing=1 years."
        )
        return "\n".join(lines)
    finally:
        con.close()
