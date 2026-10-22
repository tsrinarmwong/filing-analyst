"""Build data/financials.db from SEC XBRL data.

Core financials come from the structured `companyfacts` API; segment and
geographic revenue are parsed from each filing's inline XBRL (companyfacts omits
dimensional breakdowns). Design notes baked into the schema:

* Long-format `metrics` table with CANONICAL metric names. Companies tag the same
  line item with different us-gaap elements over time -- e.g. NVIDIA migrated its
  total-revenue tag from RevenueFromContractWithCustomer... to Revenues. We merge
  all candidate tags for a metric and keep, per fiscal year, the value from the
  most recently filed report (handling restatements and tag migrations).

* Fiscal years are derived from each fact's PERIOD END, not the filer's reported
  `fy` field, because a 10-K restates prior years under the filing's own fy. The
  period end is stored explicitly so the agent can reason across the mismatched
  calendars (NVIDIA ends in January; AMD and Intel end in December).

* `segment_revenue` members can nest (e.g. NVIDIA "Data Center" contains
  "Compute" + "Networking") and can include intersegment revenue (Intel), so
  they are stored faithfully and the agent is warned not to blindly sum them.
  Geographic members form a clean partition that does sum to total revenue.

Run:  python -m scripts.build_financials_db
"""
from __future__ import annotations

import os
import re
import sqlite3
import warnings
from datetime import date

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

import config
from scripts.edgar import Edgar, COMPANYFACTS_URL
from scripts.fetch_filings import load_manifest

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# --- canonical metric map -----------------------------------------------------
# (canonical_name, [candidate us-gaap tags, merged], unit, period_type)
METRICS: list[tuple[str, list[str], str, str]] = [
    # income statement (duration)
    ("revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"], "USD", "duration"),
    ("cost_of_revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold"], "USD", "duration"),
    ("gross_profit", ["GrossProfit"], "USD", "duration"),
    ("rd_expense", ["ResearchAndDevelopmentExpense"], "USD", "duration"),
    ("sga_expense", ["SellingGeneralAndAdministrativeExpense"], "USD", "duration"),
    ("operating_income", ["OperatingIncomeLoss"], "USD", "duration"),
    ("net_income", ["NetIncomeLoss"], "USD", "duration"),
    ("income_tax_expense", ["IncomeTaxExpenseBenefit"], "USD", "duration"),
    ("eps_basic", ["EarningsPerShareBasic"], "USD/shares", "duration"),
    ("eps_diluted", ["EarningsPerShareDiluted"], "USD/shares", "duration"),
    ("shares_diluted", ["WeightedAverageNumberOfDilutedSharesOutstanding"], "shares", "duration"),
    ("operating_cash_flow", ["NetCashProvidedByUsedInOperatingActivities"], "USD", "duration"),
    ("capex", ["PaymentsToAcquirePropertyPlantAndEquipment"], "USD", "duration"),
    # balance sheet (instant)
    ("total_assets", ["Assets"], "USD", "instant"),
    ("total_current_assets", ["AssetsCurrent"], "USD", "instant"),
    ("total_liabilities", ["Liabilities"], "USD", "instant"),
    ("total_current_liabilities", ["LiabilitiesCurrent"], "USD", "instant"),
    ("stockholders_equity", ["StockholdersEquity"], "USD", "instant"),
    ("cash_and_equivalents", ["CashAndCashEquivalentsAtCarryingValue"], "USD", "instant"),
    ("inventory", ["InventoryNet"], "USD", "instant"),
    ("goodwill", ["Goodwill"], "USD", "instant"),
    ("long_term_debt", ["LongTermDebtNoncurrent", "LongTermDebt"], "USD", "instant"),
]


def _fiscal_year(period_end: str) -> int:
    """Fiscal-year label = calendar year the period ends in (correct for all
    three companies given their fiscal calendars)."""
    return int(period_end[:4])


def _is_full_year(start: str, end: str) -> bool:
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return 300 <= days <= 400  # 52/53-week fiscal years


def extract_metrics(gaap: dict, keep_years: set[int]) -> dict:
    """Pull curated metrics for one company from its us-gaap facts.

    Returns {(fiscal_year, canonical): row_dict}, keeping one value per metric
    per fiscal year -- the most recently FILED report wins so tag migrations and
    restatements resolve to the latest reported figure.
    """
    chosen: dict[tuple[int, str], dict] = {}
    for canonical, tags, unit, ptype in METRICS:
        for tag in tags:
            node = gaap.get(tag)
            if not node or unit not in node.get("units", {}):
                continue
            label = node.get("label") or canonical
            for f in node["units"][unit]:
                if f.get("fp") != "FY" or not str(f.get("form", "")).startswith("10-K"):
                    continue
                end = f.get("end")
                start = f.get("start")
                if ptype == "duration":
                    if not start or not _is_full_year(start, end):
                        continue
                else:  # instant facts carry no start
                    if start:
                        continue
                fy = _fiscal_year(end)
                if fy not in keep_years:
                    continue
                key = (fy, canonical)
                prev = chosen.get(key)
                if prev is None or f.get("filed", "") > prev["filed"]:
                    chosen[key] = {
                        "fiscal_year": fy, "metric": canonical, "tag": tag,
                        "label": label, "value": f["val"], "unit": unit,
                        "period_start": start, "period_end": end,
                        "period_type": ptype, "form": f.get("form"),
                        "filed": f.get("filed"), "accession": f.get("accn"),
                    }
    return chosen


# --- segment / geographic revenue from inline XBRL ---------------------------
_REV_TAGS = {"us-gaap:revenuefromcontractwithcustomerexcludingassessedtax", "us-gaap:revenues"}
_SEG_AXIS = "us-gaap:statementbusinesssegmentsaxis"
_PROD_AXIS = "srt:productorserviceaxis"
_GEO_AXES = {"srt:statementgeographicalaxis", "us-gaap:statementgeographicalaxis"}
_CONSOL_AXIS = "srt:consolidationitemsaxis"
_COUNTRY = {
    "US": "United States", "TW": "Taiwan", "CN": "China", "SG": "Singapore",
    "JP": "Japan", "KR": "South Korea", "DE": "Germany", "IE": "Ireland",
    "GB": "United Kingdom", "CA": "Canada", "MX": "Mexico", "MY": "Malaysia",
    "IL": "Israel", "HK": "Hong Kong", "NL": "Netherlands", "FR": "France",
}


def _prettify(member_raw: str) -> str:
    m = member_raw.split(":")[-1]
    m = re.sub(r"Member$", "", m)
    if m.upper() in _COUNTRY:
        return _COUNTRY[m.upper()]
    m = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", m)  # OEMAnd -> OEM And
    m = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", m)      # DataCenter -> Data Center
    return m.strip() or member_raw


def extract_segments(path, period_end: str) -> list[dict]:
    """Parse one filing's inline XBRL for revenue disaggregated by business
    segment, product, or geography (for the filing's own fiscal year)."""
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    contexts: dict[str, dict] = {}
    for c in soup.find_all("xbrli:context"):
        dims = {m.get("dimension", "").lower(): m.get_text(strip=True)
                for m in c.find_all("xbrldi:explicitmember")}
        end = c.find("xbrli:enddate")
        start = c.find("xbrli:startdate")
        contexts[c.get("id")] = {
            "start": start.get_text(strip=True) if start else None,
            "end": end.get_text(strip=True) if end else None,
            "dims": dims,
        }

    rows, seen = [], set()
    for f in soup.find_all("ix:nonfraction"):
        if (f.get("name") or "").lower() not in _REV_TAGS:
            continue
        ctx = contexts.get(f.get("contextref"))
        if not ctx or ctx["end"] != period_end or not ctx["start"]:
            continue
        dims = dict(ctx["dims"])
        consol = dims.pop(_CONSOL_AXIS, None)
        if consol and consol != "us-gaap:OperatingSegmentsMember":
            continue  # drop eliminations / corporate / reconciling items
        has_seg, has_prod = _SEG_AXIS in dims, _PROD_AXIS in dims
        has_geo = bool(_GEO_AXES & dims.keys())
        if has_geo and not has_seg and not has_prod:
            dimension = "geography"
            member_raw = dims[next(a for a in _GEO_AXES if a in dims)]
        elif has_seg and not has_prod and not has_geo:
            dimension, member_raw = "business_segment", dims[_SEG_AXIS]
        elif has_prod and not has_seg and not has_geo:
            dimension, member_raw = "product", dims[_PROD_AXIS]
        else:
            continue
        text = f.get_text(strip=True).replace(",", "")
        if not re.match(r"^\d+(\.\d+)?$", text):
            continue
        value = float(text) * (10 ** int(f.get("scale", "0") or 0))
        if (f.get("sign") or "") == "-":
            value = -value
        key = (dimension, member_raw)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "dimension": dimension, "member": _prettify(member_raw),
            "member_raw": member_raw.split(":")[-1], "value": int(round(value)),
            "unit": "USD",
        })
    return rows


# --- schema + build -----------------------------------------------------------
SCHEMA = """
CREATE TABLE companies (
    ticker          TEXT PRIMARY KEY,
    cik             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    fiscal_year_end TEXT NOT NULL   -- human note, e.g. "late January"
);

CREATE TABLE fiscal_periods (
    ticker       TEXT NOT NULL REFERENCES companies(ticker),
    fiscal_year  INTEGER NOT NULL,
    period_start TEXT,              -- fiscal year start (YYYY-MM-DD)
    period_end   TEXT NOT NULL,     -- fiscal year end (YYYY-MM-DD)
    has_filing   INTEGER NOT NULL,  -- 1 if the 10-K narrative is in the index
    PRIMARY KEY (ticker, fiscal_year)
);

CREATE TABLE metrics (
    ticker       TEXT NOT NULL REFERENCES companies(ticker),
    fiscal_year  INTEGER NOT NULL,
    metric       TEXT NOT NULL,     -- canonical name, e.g. 'revenue'
    value        REAL NOT NULL,
    unit         TEXT NOT NULL,     -- 'USD', 'USD/shares', or 'shares'
    period_start TEXT,              -- NULL for balance-sheet (instant) metrics
    period_end   TEXT NOT NULL,
    period_type  TEXT NOT NULL,     -- 'duration' or 'instant'
    tag          TEXT NOT NULL,     -- source us-gaap element (provenance)
    label        TEXT,              -- human label from XBRL
    form         TEXT,
    filed        TEXT,
    PRIMARY KEY (ticker, fiscal_year, metric)
);

CREATE TABLE segment_revenue (
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    fiscal_year INTEGER NOT NULL,
    period_end  TEXT NOT NULL,
    dimension   TEXT NOT NULL,      -- 'business_segment' | 'product' | 'geography'
    member      TEXT NOT NULL,      -- readable label, e.g. 'Data Center'
    member_raw  TEXT NOT NULL,      -- original XBRL member local name
    value       REAL NOT NULL,
    unit        TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_year, dimension, member_raw)
);

CREATE INDEX idx_metrics_lookup ON metrics(ticker, metric, fiscal_year);
CREATE INDEX idx_segment_lookup ON segment_revenue(ticker, dimension, fiscal_year);
"""


def build() -> None:
    manifest = load_manifest()
    by_ticker: dict[str, list[dict]] = {}
    for m in manifest:
        by_ticker.setdefault(m["ticker"], []).append(m)

    tmp_path = config.DB_PATH.with_suffix(".db.tmp")
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if tmp_path.exists():
        tmp_path.unlink()

    con = sqlite3.connect(tmp_path)
    try:
        con.executescript(SCHEMA)
        n_metrics = n_segments = 0

        with Edgar() as edgar:
            for ticker, meta in config.COMPANIES.items():
                filings = by_ticker.get(ticker, [])
                if not filings:
                    print(f"  WARNING: no filings in manifest for {ticker}")
                    continue
                cik = filings[0]["cik"]
                covered = {f["fiscal_year"] for f in filings}
                keep_years = covered | {min(covered) - 1}  # +1 prior year for YoY
                print(f"\n{ticker}: CIK {cik}, fiscal years {sorted(keep_years)} "
                      f"(filings: {sorted(covered, reverse=True)})")

                con.execute(
                    "INSERT INTO companies VALUES (?,?,?,?)",
                    (ticker, cik, meta["name"], meta["fy_note"].replace("fiscal year ends ", "")),
                )

                # metrics
                gaap = edgar.get_json(COMPANYFACTS_URL.format(cik=cik))["facts"].get("us-gaap", {})
                chosen = extract_metrics(gaap, keep_years)
                for row in chosen.values():
                    con.execute(
                        "INSERT INTO metrics (ticker, fiscal_year, metric, value, unit, "
                        "period_start, period_end, period_type, tag, label, form, filed) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ticker, row["fiscal_year"], row["metric"], row["value"], row["unit"],
                         row["period_start"], row["period_end"], row["period_type"],
                         row["tag"], row["label"], row["form"], row["filed"]),
                    )
                n_metrics += len(chosen)
                print(f"  metrics: {len(chosen)} rows")

                # fiscal_periods: period end/start per fiscal year, from a duration metric
                period_bounds: dict[int, tuple] = {}
                for (fy, metric), row in chosen.items():
                    if row["period_type"] == "duration" and fy not in period_bounds:
                        period_bounds[fy] = (row["period_start"], row["period_end"])
                for fy in sorted(keep_years):
                    start, end = period_bounds.get(fy, (None, None))
                    if end is None:
                        # instant-only fallback: use any metric's period_end
                        ends = [r["period_end"] for (yy, _), r in chosen.items() if yy == fy]
                        end = ends[0] if ends else f"{fy}-12-31"
                    con.execute(
                        "INSERT INTO fiscal_periods VALUES (?,?,?,?,?)",
                        (ticker, fy, start, end, 1 if fy in covered else 0),
                    )

                # segment / geographic revenue (parsed from each filing)
                for f in filings:
                    path = config.ROOT / f["local_path"]
                    seg_rows = extract_segments(path, f["period_end"])
                    for s in seg_rows:
                        con.execute(
                            "INSERT OR IGNORE INTO segment_revenue VALUES (?,?,?,?,?,?,?,?)",
                            (ticker, f["fiscal_year"], f["period_end"], s["dimension"],
                             s["member"], s["member_raw"], s["value"], s["unit"]),
                        )
                    n_segments += len(seg_rows)
                    print(f"  FY{f['fiscal_year']} segments/geo: {len(seg_rows)} rows")

        con.commit()
    finally:
        con.close()

    os.replace(tmp_path, config.DB_PATH)  # atomic: no partial DB on failure
    print(f"\nBuilt {config.DB_PATH.relative_to(config.ROOT)} "
          f"({n_metrics} metric rows, {n_segments} segment/geo rows).")


if __name__ == "__main__":
    build()
