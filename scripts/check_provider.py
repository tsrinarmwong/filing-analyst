"""Preflight the LLM provider: verify the key, that the chat model supports
function calling, and that the embedding model responds. Fails fast with a clear
message so a stale model id or bad key surfaces in seconds, not mid-build.

    python -m scripts.check_provider
"""
from __future__ import annotations

import sys

import config
from config import CHAT_MODEL, EMBED_MODEL, get_client

_PING_TOOL = [{
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Return pong.",
        "parameters": {"type": "object", "properties": {}},
    },
}]


def main() -> None:
    print(f"Provider base URL: {config.BASE_URL}")
    try:
        client = get_client()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    ok = True

    print(f"\nChat model: {CHAT_MODEL}")
    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": "Call the ping tool."}],
            tools=_PING_TOOL,
            tool_choice="auto",
            max_tokens=64,
            temperature=0,
        )
        msg = resp.choices[0].message
        tooled = bool(msg.tool_calls)
        print(f"  OK: responded (function calling {'exercised' if tooled else 'available'}).")
    except Exception as exc:
        ok = False
        print(f"  FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  Set LLM_CHAT_MODEL to a current function-calling model id.", file=sys.stderr)

    print(f"\nEmbedding model: {EMBED_MODEL}")
    try:
        resp = client.embeddings.create(model=EMBED_MODEL, input=["ping"])
        print(f"  OK: returned a {len(resp.data[0].embedding)}-dim vector.")
    except Exception as exc:
        ok = False
        print(f"  FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  Set LLM_EMBED_MODEL to a current embedding model id.", file=sys.stderr)

    if not ok:
        sys.exit(1)
    print("\nAll provider checks passed.")


if __name__ == "__main__":
    main()
