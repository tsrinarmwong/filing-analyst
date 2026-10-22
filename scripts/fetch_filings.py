"""Download the two most recent 10-K filings for each covered company from SEC
EDGAR and cache them under data/raw/.

    python -m scripts.fetch_filings            # fetch (skips already-cached docs)
    python -m scripts.fetch_filings --force    # re-download everything
    python -m scripts.fetch_filings --limit 3  # keep N most recent per company

"Two most recent" is resolved live from EDGAR's submissions feed, so this stays
correct across filing seasons rather than hardcoding fiscal years. A manifest
(data/raw/manifest.json) records every filing's CIK, fiscal year, exact period
end date, accession number, and source URL -- the DB builder and index builder
read it so the pipeline shares one source of truth.
"""
from __future__ import annotations

import argparse
import json
import sys

import config
from scripts.edgar import Edgar

MANIFEST_PATH = config.RAW_DIR / "manifest.json"


def fiscal_year(report_date: str) -> int:
    """Fiscal-year label = the calendar year the period ends in. This is correct
    for all three companies: NVIDIA's FY2025 ends Jan 2025, AMD/Intel's FY2024
    ends Dec 2024."""
    return int(report_date[:4])


def fetch(limit: int = 2, force: bool = False) -> list[dict]:
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    with Edgar() as edgar:
        for ticker, meta in config.COMPANIES.items():
            cik = edgar.resolve_cik(ticker)
            print(f"\n{ticker} ({meta['name']}) -- CIK {cik}", flush=True)
            filings = edgar.recent_10ks(cik, limit=limit)
            if not filings:
                print(f"  WARNING: no 10-K filings found for {ticker}", file=sys.stderr)
                continue

            for f in filings:
                fy = fiscal_year(f["report_date"])
                out_dir = config.RAW_DIR / ticker
                out_dir.mkdir(parents=True, exist_ok=True)
                local = out_dir / f"{ticker}_FY{fy}_10-K.htm"
                url = edgar.filing_doc_url(cik, f["accession_nodash"], f["primary_doc"])

                if local.exists() and not force:
                    print(f"  FY{fy}: cached ({local.name})", flush=True)
                else:
                    print(f"  FY{fy}: downloading {f['primary_doc']} ...", flush=True)
                    resp = edgar.get(url)
                    local.write_bytes(resp.content)
                    print(f"        -> {local.name} ({len(resp.content):,} bytes)", flush=True)

                manifest.append(
                    {
                        "ticker": ticker,
                        "cik": cik,
                        "company": meta["name"],
                        "fiscal_year": fy,
                        "period_end": f["report_date"],
                        "filing_date": f["filing_date"],
                        "accession": f["accession"],
                        "form": "10-K",
                        "source_url": url,
                        "local_path": str(local.relative_to(config.ROOT)),
                    }
                )

    manifest.sort(key=lambda m: (m["ticker"], -m["fiscal_year"]))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote manifest for {len(manifest)} filings -> "
          f"{MANIFEST_PATH.relative_to(config.ROOT)}", flush=True)
    return manifest


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"{MANIFEST_PATH} not found. Run `python -m scripts.fetch_filings` first."
        )
    return json.loads(MANIFEST_PATH.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Download 10-K filings from SEC EDGAR.")
    parser.add_argument("--limit", type=int, default=2,
                        help="How many recent 10-Ks to keep per company (default 2).")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if a cached copy exists.")
    args = parser.parse_args()
    fetch(limit=args.limit, force=args.force)


if __name__ == "__main__":
    main()
