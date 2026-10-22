"""Shared SEC EDGAR client: User-Agent handling, rate limiting, and the handful
of endpoints the fetcher and the DB builder both need.

SEC asks that automated clients send a descriptive User-Agent with contact info
and stay at or under 10 requests/second. We read the UA from SEC_USER_AGENT (an
email is never hardcoded) and throttle every request well under the limit.
"""
from __future__ import annotations

import os
import time

import httpx

import config  # noqa: F401  -- importing config loads the repo-root .env

# ~6-7 requests/second: comfortably under SEC's 10 rps ceiling with headroom for
# jitter. One shared client serializes requests, so this delay is the true rate.
_RATE_DELAY = 0.15

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVES_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. SEC EDGAR requires a descriptive "
            "User-Agent with contact info, e.g.\n"
            '    export SEC_USER_AGENT="Your Name (you@example.com)"\n'
            "or add it to a .env file at the repo root (see .env.example)."
        )
    return ua


class Edgar:
    """A throttled EDGAR HTTP client. Use one instance per run so the rate limit
    is enforced across every request."""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.Client(
            headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            follow_redirects=True,
        )
        self._last = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < _RATE_DELAY:
            time.sleep(_RATE_DELAY - elapsed)
        self._last = time.monotonic()

    def get(self, url: str) -> httpx.Response:
        self._throttle()
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp

    def get_json(self, url: str) -> dict:
        return self.get(url).json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Edgar":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- domain helpers -------------------------------------------------------
    def resolve_cik(self, ticker: str) -> int:
        """Map a ticker symbol to its integer CIK via EDGAR's official table."""
        table = self.get_json(TICKERS_URL)
        want = ticker.upper()
        for row in table.values():
            if row.get("ticker", "").upper() == want:
                return int(row["cik_str"])
        raise ValueError(f"Ticker {ticker!r} not found in SEC company_tickers.json")

    def recent_10ks(self, cik: int, limit: int = 2) -> list[dict]:
        """Return the `limit` most recent 10-K filings (newest first).

        Only exact form '10-K' is kept -- amendments ('10-K/A') are excluded so
        we index the original annual reports. Each dict carries the accession
        number, primary document name, filing date, and period-of-report date
        (the fiscal period end, which we store explicitly downstream).
        """
        data = self.get_json(SUBMISSIONS_URL.format(cik=cik))
        recent = data["filings"]["recent"]
        rows = zip(
            recent["form"],
            recent["accessionNumber"],
            recent["primaryDocument"],
            recent["filingDate"],
            recent["reportDate"],
        )
        tens = [
            {
                "accession": acc,
                "accession_nodash": acc.replace("-", ""),
                "primary_doc": doc,
                "filing_date": filed,
                "report_date": report,  # fiscal period end (YYYY-MM-DD)
            }
            for form, acc, doc, filed, report in rows
            if form == "10-K"
        ]
        tens.sort(key=lambda r: r["filing_date"], reverse=True)
        return tens[:limit]

    def filing_doc_url(self, cik: int, accession_nodash: str, doc: str) -> str:
        return ARCHIVES_DOC_URL.format(cik=cik, acc=accession_nodash, doc=doc)
