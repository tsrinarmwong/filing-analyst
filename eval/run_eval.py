"""Run the agent over the eval set, score each answer, and write results.

    python -m eval.run_eval                 # grade everything (uses the LLM judge)
    python -m eval.run_eval --no-judge      # skip judge-scored questions

Outputs eval/results/latest.json (full answers + evidence + verdicts) and
eval/results/summary.md (a human-readable table + pass rates). Numeric gold
answers are resolved from the DB by make_gold first.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.agent import answer_question
from config import CHAT_MODEL
from eval import make_gold, scorers

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _load_gold() -> list[dict]:
    # Always re-resolve from the DB so gold answers never drift from questions.
    return make_gold.resolve(make_gold.load_questions())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Filing Analyst eval.")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM-judge questions.")
    args = parser.parse_args()
    enable_judge = not args.no_judge

    items = _load_gold()
    results = []
    for item in items:
        print(f"\n=== {item['id']} [{item['category']}] ===\n{item['question']}", flush=True)
        out = answer_question(item["question"])
        answer = out["answer"]
        verdict, detail = scorers.score(item, answer, enable_judge=enable_judge)
        print(f"-> {verdict}  ({detail})\n   {answer[:200]}", flush=True)
        results.append({
            "id": item["id"], "category": item["category"], "scorer": item["scorer"],
            "question": item["question"], "answer": answer,
            "verdict": verdict, "detail": detail,
            "evidence": out.get("evidence", []),
        })

    counts = {"PASS": 0, "FAIL": 0, "UNSCORED": 0}
    for r in results:
        counts[r["verdict"]] += 1
    scored = counts["PASS"] + counts["FAIL"]
    pass_rate = (counts["PASS"] / scored) if scored else 0.0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": CHAT_MODEL,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {**counts, "scored": scored, "pass_rate": round(pass_rate, 4)},
        "results": results,
    }
    (RESULTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2) + "\n")
    _write_summary(payload)

    print("\n================ SUMMARY ================")
    print(f"  {counts['PASS']}/{scored} passed ({pass_rate:.0%}); {counts['UNSCORED']} unscored.")
    fails = [r["id"] for r in results if r["verdict"] == "FAIL"]
    if fails:
        print(f"  FAILED: {', '.join(fails)}")
    print(f"  Wrote {RESULTS_DIR / 'latest.json'} and summary.md")


def _write_summary(payload: dict) -> None:
    c = payload["summary"]
    lines = [
        "# Eval results",
        "",
        f"Model: `{payload['model']}`  ·  {payload['timestamp']}",
        "",
        f"**{c['PASS']}/{c['scored']} passed ({c['pass_rate']:.0%})**"
        + (f"  ·  {c['UNSCORED']} unscored" if c["UNSCORED"] else ""),
        "",
        "By category:",
        "",
        "| Category | Passed |",
        "| --- | --- |",
    ]
    for cat in ("lookup", "narrative", "synthesis"):
        rows = [r for r in payload["results"] if r["category"] == cat]
        p = sum(1 for r in rows if r["verdict"] == "PASS")
        s = sum(1 for r in rows if r["verdict"] in ("PASS", "FAIL"))
        lines.append(f"| {cat} | {p}/{s} |")
    lines += ["", "| Question | Category | Scorer | Verdict | Detail |",
              "| --- | --- | --- | --- | --- |"]
    for r in payload["results"]:
        q = r["question"].replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['category']} | {r['scorer']} | {r['verdict']} | "
                     f"{r['detail'].replace('|', '/')} |")
    (RESULTS_DIR / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
