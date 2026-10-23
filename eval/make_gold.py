"""Resolve gold numeric answers for the eval set FROM the financials DB.

Every numeric question carries the SQL that derives its gold answer -- we run it
here against the database we built (never hand-type a number from memory) and
write eval/gold.jsonl, the resolved answer key run_eval.py grades against.

    python -m eval.make_gold
"""
from __future__ import annotations

import json
from pathlib import Path

from app.sql_tool import run_sql

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS = EVAL_DIR / "questions.jsonl"
GOLD = EVAL_DIR / "gold.jsonl"


def load_questions() -> list[dict]:
    return [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]


def resolve(items: list[dict]) -> list[dict]:
    resolved = []
    for item in items:
        item = dict(item)
        if item.get("scorer") == "numeric":
            query = item["gold_query"]
            out = run_sql(query)
            if out.get("error") or not out.get("rows"):
                raise SystemExit(f"[{item['id']}] gold query failed: {out.get('error') or 'no rows'}\n  {query}")
            first_row = out["rows"][0]
            value = next(iter(first_row.values()))  # single scalar expected
            item["gold_numeric"] = float(value)
        resolved.append(item)
    return resolved


def main() -> None:
    items = resolve(load_questions())
    with open(GOLD, "w") as fh:
        for item in items:
            fh.write(json.dumps(item) + "\n")

    print(f"Resolved {len(items)} questions -> {GOLD.relative_to(EVAL_DIR.parent)}")
    print("\nComputed numeric gold answers (from the DB):")
    for item in items:
        if item.get("scorer") == "numeric":
            print(f"  {item['id']:11s} {item['gold_numeric']:>18,.2f}  <- {item['question'][:60]}")


if __name__ == "__main__":
    main()
