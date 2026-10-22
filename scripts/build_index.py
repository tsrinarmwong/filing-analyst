"""Build the vector index: chunk every downloaded 10-K, embed the chunks, and
write a single pickle that the retriever loads at query time.

    python -m scripts.build_index --check   # preflight only (verify key + model)
    python -m scripts.build_index           # full build

A slow, network-heavy job (one embedding request per batch), so it prints live
per-batch progress, retries transient failures with backoff, throttles to stay
polite to the provider, and writes the index atomically -- a failure never
leaves a half-written index behind.
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import time

import numpy as np

import config
from app.chunker import chunk_filing
from config import EMBED_MODEL, get_client
from scripts.fetch_filings import load_manifest

EMBED_BATCH = 64       # chunks per embedding request
THROTTLE_SECONDS = 1.0  # pause between batches to stay well under rate limits
MAX_TRIES = 4          # last-resort retries on top of the SDK's own backoff


def _make_client():
    try:
        return get_client()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def preflight(client) -> int:
    """One tiny embedding request so a bad key or wrong model id fails in seconds,
    not minutes into the run. Returns the embedding dimension."""
    print(f"Preflight: probing embedding model '{EMBED_MODEL}' ...", flush=True)
    try:
        resp = client.embeddings.create(model=EMBED_MODEL, input=["ping"])
        dim = len(resp.data[0].embedding)
    except Exception as exc:
        print(f"ERROR: embedding preflight failed for '{EMBED_MODEL}'.", file=sys.stderr)
        print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  Check LLM_EMBED_MODEL / the provider base URL and key (see .env.example).",
              file=sys.stderr)
        sys.exit(1)
    print(f"Preflight OK: '{EMBED_MODEL}' returned a {dim}-dim vector.", flush=True)
    return dim


def _embed_batch(client, batch, idx, total):
    """Embed one batch with retries + exponential backoff. Aborts the whole run
    (no partial index) if a batch can't be recovered."""
    for attempt in range(1, MAX_TRIES + 1):
        try:
            resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
            return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        except Exception as exc:
            if attempt == MAX_TRIES:
                print(f"ERROR: batch {idx}/{total} failed after {MAX_TRIES} tries: {exc}",
                      file=sys.stderr)
                print("Aborting WITHOUT writing a partial index.", file=sys.stderr)
                sys.exit(1)
            wait = 2 ** attempt
            print(f"  [batch {idx}/{total}] error ({exc}); retrying in {wait}s ...", flush=True)
            time.sleep(wait)


def embed_all(client, texts):
    total = (len(texts) + EMBED_BATCH - 1) // EMBED_BATCH
    print(f"Embedding {len(texts)} chunks in {total} batches of {EMBED_BATCH} ...", flush=True)
    vectors = []
    for b in range(total):
        batch = texts[b * EMBED_BATCH : (b + 1) * EMBED_BATCH]
        vectors.extend(_embed_batch(client, batch, b + 1, total))
        print(f"  [batch {b + 1}/{total}] {len(vectors)}/{len(texts)} embedded", flush=True)
        if b + 1 < total:
            time.sleep(THROTTLE_SECONDS)
    return np.asarray(vectors, dtype=np.float32)


def build(check_only: bool = False) -> None:
    client = _make_client()
    preflight(client)
    if check_only:
        print("--check passed: key + embedding model are reachable. Index not built.")
        return

    manifest = load_manifest()
    texts, metas = [], []
    for f in manifest:
        html = (config.ROOT / f["local_path"]).read_bytes()
        chunks = chunk_filing(html)
        for c in chunks:
            texts.append(c["text"])
            metas.append({
                "text": c["text"],
                "ticker": f["ticker"],
                "fiscal_year": f["fiscal_year"],
                "section": c["section"],
                "ordinal": c["ordinal"],
            })
        print(f"  {f['ticker']} FY{f['fiscal_year']}: {len(chunks)} chunks", flush=True)

    if not texts:
        raise SystemExit("No chunks produced -- did you run `make fetch` first?")

    vectors = embed_all(client, texts)

    config.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.INDEX_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as fh:
        pickle.dump({"vectors": vectors, "metas": metas,
                     "model": EMBED_MODEL, "dim": int(vectors.shape[1])}, fh)
    os.replace(tmp, config.INDEX_PATH)  # atomic
    print(f"\nDone. Indexed {len(texts)} chunks from {len(manifest)} filings -> "
          f"{config.INDEX_PATH.relative_to(config.ROOT)}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the 10-K vector index.")
    parser.add_argument("--check", action="store_true",
                        help="Preflight only: verify key + embedding model, then exit.")
    args = parser.parse_args()
    build(check_only=args.check)
