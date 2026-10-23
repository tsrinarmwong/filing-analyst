"""A prebuilt analytics dashboard over financials.db.

Static, self-contained inline-SVG charts -- no JS chart library, no CDN, no
external requests -- rendered server-side so they are theme-aware (CSS vars) and
every number is verifiable against the DB. Routes:

    GET /dashboard       the HTML page
    GET /api/dashboard   the underlying series as JSON

Colors use the data-viz reference categorical palette (validated for CVD); the
low-contrast slots are mitigated with a legend, direct labels, segment gaps, and
a per-chart data table, so identity is never color-alone.
"""
from __future__ import annotations

import html
import math

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from app import sql_tool

router = APIRouter()

ORDER = ["NVDA", "AMD", "INTC"]
COMPANY_COLOR = {"NVDA": "var(--c1)", "AMD": "var(--c2)", "INTC": "var(--c3)"}
REGION_ORDER = ["United States", "China", "Taiwan", "Singapore", "Other"]
REGION_COLOR = {"United States": "var(--c1)", "China": "var(--c2)",
                "Taiwan": "var(--c3)", "Singapore": "var(--c4)", "Other": "var(--muted)"}


# --- data ---------------------------------------------------------------------
def _rows(sql: str) -> list[dict]:
    out = sql_tool.run_sql(sql)
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["rows"]


def _series(rows: list[dict], numcol: str, scale: float, nd: int) -> list[dict]:
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append({"x": int(r["fiscal_year"]),
                                                "y": round(r[numcol] / scale, nd)})
    return [{"name": t, "points": sorted(by[t], key=lambda p: p["x"])}
            for t in ORDER if t in by]


def _canon_region(member: str) -> str:
    m = member.lower()
    if "united states" in m or m == "us":
        return "United States"
    if "china" in m or "hong kong" in m or m == "cn":
        return "China"
    if "taiwan" in m or m == "tw":
        return "Taiwan"
    if "singapore" in m or m == "sg":
        return "Singapore"
    return "Other"


def build_dashboard_data() -> dict:
    latest = {r["ticker"]: int(r["fy"]) for r in
              _rows("SELECT ticker, MAX(fiscal_year) fy FROM segment_revenue GROUP BY ticker")}
    companies = {r["ticker"]: r["name"] for r in _rows("SELECT ticker, name FROM companies")}

    revenue = _series(_rows(
        "SELECT ticker, fiscal_year, value FROM metrics WHERE metric='revenue'"), "value", 1e9, 2)
    gross_margin = _series(_rows(
        "SELECT g.ticker, g.fiscal_year, 100.0*g.value/r.value AS m "
        "FROM metrics g JOIN metrics r ON g.ticker=r.ticker AND g.fiscal_year=r.fiscal_year "
        "AND r.metric='revenue' WHERE g.metric='gross_profit'"), "m", 1.0, 1)
    rd_intensity = _series(_rows(
        "SELECT d.ticker, d.fiscal_year, 100.0*d.value/r.value AS m "
        "FROM metrics d JOIN metrics r ON d.ticker=r.ticker AND d.fiscal_year=r.fiscal_year "
        "AND r.metric='revenue' WHERE d.metric='rd_expense'"), "m", 1.0, 1)

    # business segments for the latest filing year, only where they form a clean
    # partition (sum ~= revenue). Intel's segment revenue includes large
    # intersegment amounts, so it is omitted rather than charted misleadingly.
    segments, omitted = {}, []
    for t in ORDER:
        fy = latest[t]
        segs = _rows(f"SELECT member, value FROM segment_revenue WHERE ticker='{t}' "
                     f"AND dimension='business_segment' AND fiscal_year={fy} ORDER BY value DESC")
        rev = _rows(f"SELECT value FROM metrics WHERE ticker='{t}' AND metric='revenue' "
                    f"AND fiscal_year={fy}")[0]["value"]
        total = sum(s["value"] for s in segs)
        if segs and total <= 1.05 * rev:
            segments[t] = {"fy": fy, "items": [{"label": s["member"], "value": round(s["value"] / 1e9, 2)}
                                               for s in segs]}
        else:
            omitted.append(t)

    # geography for the latest filing year, canonicalized and shares computed.
    geography = {}
    for t in ORDER:
        fy = latest[t]
        rows = _rows(f"SELECT member, value FROM segment_revenue WHERE ticker='{t}' "
                     f"AND dimension='geography' AND fiscal_year={fy}")
        agg: dict[str, float] = {}
        for r in rows:
            agg[_canon_region(r["member"])] = agg.get(_canon_region(r["member"]), 0.0) + r["value"]
        total = sum(agg.values()) or 1.0
        items = [{"region": reg, "value": round(agg[reg] / 1e9, 2), "share": round(100 * agg[reg] / total, 1)}
                 for reg in REGION_ORDER if reg in agg]
        geography[t] = {"fy": fy, "items": items}

    return {"companies": companies, "order": ORDER, "latest": latest,
            "revenue": revenue, "gross_margin": gross_margin, "rd_intensity": rd_intensity,
            "segments": segments, "segments_omitted": omitted, "geography": geography}


# --- SVG helpers --------------------------------------------------------------
def _nice(x: float) -> float:
    if x <= 0:
        return 1.0
    exp = math.floor(math.log10(x))
    f = x / 10 ** exp
    nf = 1 if f <= 1 else 2 if f <= 2 else 2.5 if f <= 2.5 else 5 if f <= 5 else 10
    return nf * 10 ** exp


def _axis(maxv: float):
    step = _nice(maxv / 4) if maxv > 0 else 1
    ceil = step * math.ceil(maxv / step) if maxv > 0 else step
    ticks, v = [], 0.0
    while v <= ceil + 1e-9:
        ticks.append(round(v, 6))
        v += step
    return ceil, ticks


def _fmtnum(v: float) -> str:
    return f"{v:g}"


def _line_chart(series: list[dict], suffix: str = "") -> str:
    W, H, L, R, T, B = 540, 300, 46, 60, 34, 30
    pw, ph = W - L - R, H - T - B
    xs = sorted({p["x"] for s in series for p in s["points"]})
    if not xs:
        return '<svg viewBox="0 0 540 300"></svg>'
    xmin, xmax = xs[0], xs[-1]
    maxy = max((p["y"] for s in series for p in s["points"]), default=1)
    ceil, ticks = _axis(maxy)

    def X(x): return L + (0 if xmax == xmin else (x - xmin) / (xmax - xmin)) * pw
    def Y(y): return T + ph - (y / ceil) * ph

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">']
    for tk in ticks:  # horizontal gridlines + y labels
        y = Y(tk)
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{L-8}" y="{y+3:.1f}" class="ylab">{_fmtnum(tk)}</text>')
    for x in xs:  # x labels
        out.append(f'<text x="{X(x):.1f}" y="{H-10}" class="xlab">FY{x}</text>')
    for s in series:  # one line per series
        color = COMPANY_COLOR.get(s["name"], "var(--c1)")
        pts = " ".join(f"{X(p['x']):.1f},{Y(p['y']):.1f}" for p in s["points"])
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
        for p in s["points"]:
            cx, cy = X(p["x"]), Y(p["y"])
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.2" fill="{color}" '
                       f'stroke="var(--surface)" stroke-width="1.5"><title>{s["name"]} FY{p["x"]}: '
                       f'{p["y"]}{suffix}</title></circle>')
        last = s["points"][-1]
        lx, ly = X(last["x"]) + 7, Y(last["y"]) + 3.5
        # text wears an ink token (not the series color); the colored dot just
        # left of it carries identity, so low-contrast slots stay readable.
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="endlab">'
                   f'{s["name"]} {_fmtnum(last["y"])}{suffix}</text>')
    out.append("</svg>")
    return "".join(out)


def _hbar_chart(items: list[dict], color: str, suffix: str = "") -> str:
    W, rowh, L, R, T = 460, 30, 150, 54, 10
    H = T * 2 + rowh * len(items)
    maxv = max((it["value"] for it in items), default=1)
    ceil = _axis(maxv)[0]
    pw = W - L - R
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">']
    for i, it in enumerate(items):
        y = T + i * rowh
        bw = (it["value"] / ceil) * pw
        out.append(f'<text x="{L-10}" y="{y+rowh/2+4:.1f}" class="catlab">{html.escape(it["label"])}</text>')
        out.append(f'<rect x="{L}" y="{y+5:.1f}" width="{bw:.1f}" height="{rowh-12}" rx="3" fill="{color}">'
                   f'<title>{html.escape(it["label"])}: {it["value"]}{suffix}</title></rect>')
        out.append(f'<text x="{L+bw+6:.1f}" y="{y+rowh/2+4:.1f}" class="vallab">{it["value"]}{suffix}</text>')
    out.append("</svg>")
    return "".join(out)


def _stacked_geo(geography: dict) -> str:
    W, rowh, L, R, T = 560, 46, 58, 14, 14
    rows = [(t, geography[t]) for t in ORDER if t in geography]
    H = T * 2 + rowh * len(rows)
    pw = W - L - R
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" class="chart">']
    for i, (t, g) in enumerate(rows):
        y = T + i * rowh
        out.append(f'<text x="{L-10}" y="{y+rowh/2:.1f}" class="catlab">{t}</text>')
        out.append(f'<text x="{L-10}" y="{y+rowh/2+13:.1f}" class="subcat">FY{g["fy"]}</text>')
        x = L
        for it in g["items"]:
            w = (it["share"] / 100.0) * pw
            color = REGION_COLOR.get(it["region"], "var(--muted)")
            out.append(f'<rect x="{x:.1f}" y="{y+6:.1f}" width="{max(w-2,0):.1f}" height="{rowh-20}" rx="2" '
                       f'fill="{color}"><title>{t} · {html.escape(it["region"])}: {it["share"]}% '
                       f'(${it["value"]}B)</title></rect>')
            x += w
    out.append("</svg>")
    return "".join(out)


def _legend(pairs: list[tuple]) -> str:
    chips = "".join(
        f'<span class="lchip"><span class="sw" style="background:{c}"></span>{html.escape(n)}</span>'
        for n, c in pairs)
    return f'<div class="legend">{chips}</div>'


def _table(headers: list[str], rows: list[list]) -> str:
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<details class="data"><summary>Show data</summary>'
            f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>')


# --- page assembly ------------------------------------------------------------
def _trend_table(series: list[dict], suffix: str) -> str:
    years = sorted({p["x"] for s in series for p in s["points"]})
    rows = []
    for s in series:
        m = {p["x"]: p["y"] for p in s["points"]}
        rows.append([s["name"]] + [f"{m[y]}{suffix}" if y in m else "—" for y in years])
    return _table(["Company"] + [f"FY{y}" for y in years], rows)


def _panel(title: str, subtitle: str, body: str) -> str:
    return (f'<section class="panel"><h2>{html.escape(title)}</h2>'
            f'<p class="sub">{subtitle}</p>{body}</section>')


def render_dashboard() -> str:
    d = build_dashboard_data()
    company_legend = _legend([(t, COMPANY_COLOR[t]) for t in d["order"]])

    revenue = _panel("Revenue", "Total revenue by fiscal year ($B)",
                     company_legend + _line_chart(d["revenue"], "") + _trend_table(d["revenue"], "B"))
    margin = _panel("Gross margin", "Gross profit ÷ revenue by fiscal year (%)",
                    company_legend + _line_chart(d["gross_margin"], "%") + _trend_table(d["gross_margin"], "%"))
    rd = _panel("R&D intensity", "R&D expense ÷ revenue by fiscal year (%)",
                company_legend + _line_chart(d["rd_intensity"], "%") + _trend_table(d["rd_intensity"], "%"))

    seg_bodies = []
    for t in d["order"]:
        if t in d["segments"]:
            g = d["segments"][t]
            seg_bodies.append(f'<div class="mini"><div class="mini-h">{t} · FY{g["fy"]}</div>'
                              + _hbar_chart(g["items"], COMPANY_COLOR[t], "B") + "</div>")
    note = ""
    if d["segments_omitted"]:
        note = (f'<p class="note">{", ".join(d["segments_omitted"])} omitted: reported segment revenue '
                f'includes large intersegment amounts, so it does not form a clean revenue partition.</p>')
    segments = _panel("Segment revenue", "Reportable business segments, latest fiscal year ($B)",
                      f'<div class="minis">{"".join(seg_bodies)}</div>{note}')

    geo_rows = [[t, it["region"], it["value"], f'{it["share"]}%']
                for t in d["order"] if t in d["geography"]
                for it in d["geography"][t]["items"]]
    geography = _panel("Revenue by geography", "Share of revenue by region, latest fiscal year",
                       _legend([(r, REGION_COLOR[r]) for r in REGION_ORDER])
                       + _stacked_geo(d["geography"])
                       + _table(["Company", "Region", "$B", "Share"], geo_rows))

    panels = revenue + margin + rd + segments + geography
    return _PAGE_TEMPLATE.replace("%%PANELS%%", panels)


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Filing Analyst · Dashboard</title>
<style>
  :root {
    --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --axis:#c3c2b7; --line:#e4e7ec;
    --c1:#2a78d6; --c2:#1baf7a; --c3:#eda100; --c4:#008300; --accent:#2563eb;
  }
  @media (prefers-color-scheme: dark) {
    :root { --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
            --grid:#2c2c2a; --axis:#383835; --line:#262b36;
            --c1:#3987e5; --c2:#199e70; --c3:#c98500; --c4:#008300; --accent:#5b9dff; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink);
         font:15px/1.55 system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .top { max-width:1100px; margin:0 auto; padding:22px 22px 6px; display:flex;
         align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; }
  .top h1 { margin:0; font-size:19px; font-weight:650; }
  .top .sub { color:var(--ink2); font-size:13px; margin:4px 0 0; }
  .top a { color:var(--accent); text-decoration:none; font-size:13px; font-weight:600; }
  .grid-wrap { max-width:1100px; margin:0 auto; padding:14px 22px 40px;
               display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:16px; }
  @media (max-width:720px) { .grid-wrap { grid-template-columns:1fr; } }
  .panel { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:16px 18px; }
  .panel h2 { margin:0; font-size:15px; font-weight:650; }
  .panel .sub { margin:3px 0 10px; color:var(--muted); font-size:12px; }
  .chart { width:100%; height:auto; display:block; }
  .grid { stroke:var(--grid); stroke-width:1; }
  .ylab { fill:var(--muted); font-size:10px; text-anchor:end; font-variant-numeric:tabular-nums; }
  .xlab { fill:var(--muted); font-size:10px; text-anchor:middle; }
  .endlab { fill:var(--ink2); font-size:11px; font-weight:650; }
  .catlab { fill:var(--ink2); font-size:11.5px; text-anchor:end; }
  .subcat { fill:var(--muted); font-size:10px; text-anchor:end; }
  .vallab { fill:var(--ink2); font-size:11px; font-variant-numeric:tabular-nums; }
  .legend { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:6px; }
  .lchip { display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--ink2); }
  .sw { width:11px; height:11px; border-radius:3px; display:inline-block; }
  .minis { display:flex; flex-direction:column; gap:10px; }
  .mini-h { font-size:12px; font-weight:600; color:var(--ink2); margin-bottom:2px; }
  .note { margin:8px 0 0; color:var(--muted); font-size:11.5px; font-style:italic; }
  .data { margin-top:10px; font-size:12px; }
  .data summary { cursor:pointer; color:var(--accent); }
  .data table { border-collapse:collapse; margin-top:8px; width:100%; font-size:12px; }
  .data th, .data td { border:1px solid var(--line); padding:4px 8px; text-align:left;
                       font-variant-numeric:tabular-nums; }
  .data th { background:var(--page); }
</style>
</head>
<body>
  <div class="top">
    <div>
      <h1>Filing Analyst · Dashboard</h1>
      <p class="sub">NVIDIA, AMD, and Intel — built directly from <b>financials.db</b> (SEC XBRL).
         Fiscal years end in late January for NVIDIA and late December for AMD/Intel.</p>
    </div>
    <a href="/">← Back to chat</a>
  </div>
  <main class="grid-wrap">%%PANELS%%</main>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    try:
        return render_dashboard()
    except (FileNotFoundError, RuntimeError) as exc:
        return (f"<h1>Dashboard unavailable</h1><p>{html.escape(str(exc))}</p>"
                f"<p>Build the database first: <code>make db</code>.</p>")


@router.get("/api/dashboard")
def dashboard_api() -> JSONResponse:
    try:
        return JSONResponse(build_dashboard_data())
    except (FileNotFoundError, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
