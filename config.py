"""Central configuration: provider client, model ids, shared embedder, paths.

Everything that talks to the LLM provider funnels through here, so the base URL,
model ids, and the embedding call live in exactly one place. The defaults target
Fireworks (an OpenAI-compatible endpoint), but the project swaps to ANY
OpenAI-compatible provider through environment variables alone -- no code edits:

    LLM_BASE_URL    base URL of the OpenAI-compatible API
    LLM_API_KEY     API key (falls back to FIREWORKS_API_KEY)
    LLM_CHAT_MODEL  function-calling chat model id
    LLM_EMBED_MODEL embedding model id

A local `.env` at the repo root is loaded automatically (it is git-ignored), so
secrets never have to live in the shell profile or on the command line.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import numpy as np
from openai import OpenAI

# --- repo-relative paths ------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"              # downloaded 10-K filings (git-ignored)
DB_PATH = DATA_DIR / "financials.db"    # built from SEC XBRL companyfacts
INDEX_PATH = DATA_DIR / "index" / "filings.pkl"  # vector index (git-ignored)


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE per line). Avoids a python-dotenv dep.

    Existing environment variables win, so an explicit `export` always overrides
    the file. Quotes around values are stripped; blank lines and # comments are
    ignored.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# --- provider configuration ---------------------------------------------------
# Fireworks speaks the OpenAI wire protocol, so we use the OpenAI SDK against its
# base URL rather than pulling in a provider-specific client.
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.fireworks.ai/inference/v1")

# A function-calling chat model and an embedding model. Both are overridable so
# the whole stack can move to another provider by setting env vars. These
# defaults are verified at runtime by the --check preflights (build_index.py,
# check_provider.py) so a retired/renamed id fails fast with a clear message.
CHAT_MODEL = os.environ.get(
    "LLM_CHAT_MODEL", "accounts/fireworks/models/kimi-k3"
)
EMBED_MODEL = os.environ.get(
    "LLM_EMBED_MODEL", "accounts/fireworks/models/qwen3-embedding-8b"
)

# --- corpus metadata (shared by fetch, db build, and the agent schema hint) ---
# fy_end_month documents the fiscal calendars: NVIDIA's fiscal year ends in late
# January (so e.g. "FY2025" closes Jan 2025), while AMD and Intel track the
# calendar year and close in late December. The DB stores every period's exact
# end date; this is the human-readable summary the agent is told about.
COMPANIES = {
    "NVDA": {"name": "NVIDIA Corporation", "fy_end_month": 1,
             "fy_note": "fiscal year ends late January"},
    "AMD": {"name": "Advanced Micro Devices, Inc.", "fy_end_month": 12,
            "fy_note": "fiscal year ends late December"},
    "INTC": {"name": "Intel Corporation", "fy_end_month": 12,
             "fy_note": "fiscal year ends late December (52/53-week)"},
}


def _api_key() -> str:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("FIREWORKS_API_KEY")
    if not key:
        raise RuntimeError(
            "No API key found. Set FIREWORKS_API_KEY (or LLM_API_KEY) in your "
            "environment or in a .env file at the repo root. See .env.example."
        )
    return key


def get_client() -> OpenAI:
    """Return an OpenAI client wired to the configured provider.

    Resilience lives here so the indexer, the agent, and the eval harness all
    inherit it: the SDK retries transient errors (429s included) with
    exponential backoff, and the read timeout is generous because a multi-step
    agent turn can take a while.
    """
    return OpenAI(
        base_url=BASE_URL,
        api_key=_api_key(),
        max_retries=6,
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0),
    )


def embed_texts(client: OpenAI, texts: list[str], batch_size: int = 64) -> np.ndarray:
    """Embed a list of strings, batched to keep request payloads reasonable.

    Shared by the index builder (all chunks) and the query path (one string), so
    the stored vectors and query vectors can never drift onto different embedding
    code. Returns a (len(texts), dim) float32 array in input order.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        # The API preserves input order, but sort by index defensively.
        for item in sorted(resp.data, key=lambda d: d.index):
            vectors.append(item.embedding)
    return np.asarray(vectors, dtype=np.float32)
