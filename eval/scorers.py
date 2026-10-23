"""Scoring methods for the eval harness.

Three methods, picked per question:
- numeric: does the answer contain a figure within tolerance of the DB-derived gold?
- entity:  does the answer name every required entity?
- judge:   an LLM grades the answer against a gold answer (structured output).

The numeric/entity scorers are pure and unit-tested; the judge needs the provider.
"""
from __future__ import annotations

import json
import re

NUMERIC_REL_TOL = 0.02  # ±2% for rounding / unit differences

_SCALES = {"trillion": 1e12, "billion": 1e9, "million": 1e6,
           "trillions": 1e12, "billions": 1e9, "millions": 1e6}


def extract_numbers(text: str) -> list[float]:
    """Pull candidate numeric values out of free text, applying magnitude words
    ("billion") and treating a trailing % as a plain number ("49.5%" -> 49.5)."""
    text = text.replace("−", "-")  # normalize the Unicode minus sign
    values = []
    for m in re.finditer(r"(-?\$?\s*[\d,]+(?:\.\d+)?)\s*(trillion|billion|million)?",
                         text, re.IGNORECASE):
        raw = m.group(1).replace("$", "").replace(",", "").replace(" ", "")
        if raw in ("", "-", "."):
            continue
        try:
            num = float(raw)
        except ValueError:
            continue
        scale = m.group(2)
        if scale:
            num *= _SCALES[scale.lower()]
        values.append(num)
    return values


def _signals_loss(answer: str) -> bool:
    """True if the text frames a figure as negative (a loss / parenthesized)."""
    low = answer.lower()
    return ("loss" in low or "negative" in low or "−" in answer
            or bool(re.search(r"\(\s*\$?[\d,]", answer)))


def numeric_ok(answer: str, gold: float, tol: float = NUMERIC_REL_TOL) -> bool:
    if gold is None:
        return False
    nums = extract_numbers(answer)
    denom = abs(gold) if gold else 1.0
    if any(abs(c - gold) / denom <= tol for c in nums):
        return True
    # Losses are usually written as a positive magnitude plus the word "loss"
    # (e.g. "net loss of $18.756 billion"). When the gold figure is negative and
    # the answer signals a loss, accept a magnitude match.
    if gold < 0 and _signals_loss(answer):
        return any(abs(abs(c) - abs(gold)) / denom <= tol for c in nums)
    return False


def entity_ok(answer: str, entities: list[str]) -> bool:
    """Pass only if every required entity appears (case-insensitive)."""
    low = answer.lower()
    return all(e.lower() in low for e in entities)


# --- LLM judge ----------------------------------------------------------------
JUDGE_THRESHOLD = 0.7

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number",
                  "description": "Correctness vs the gold answer, 0.0 (wrong) to 1.0 (fully correct)."},
        "reason": {"type": "string", "description": "One sentence justifying the score."},
    },
    "required": ["score", "reason"],
}

_JUDGE_SYSTEM = (
    "You are a strict grader for a financial-analysis QA system. Compare the system "
    "answer to the gold answer. Score 1.0 only if it captures the gold answer's key "
    "facts and figures; give partial credit for partial coverage; 0.0 if it is wrong, "
    "unsupported, or says it could not find the answer. Judge substance, not wording. "
    "Numbers within about 2% are equivalent."
)


def llm_judge(question: str, gold_answer: str, model_answer: str) -> tuple[float, str]:
    """Grade an open-ended answer. Returns (score, reason) via structured output."""
    from config import CHAT_MODEL, get_client
    client = get_client()
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": (
                f"Question:\n{question}\n\nGold answer:\n{gold_answer}\n\n"
                f"System answer:\n{model_answer}")},
        ],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "verdict", "schema": _VERDICT_SCHEMA, "strict": True}},
    )
    data = json.loads(resp.choices[0].message.content)
    return float(data["score"]), data.get("reason", "")


def score(item: dict, answer: str, enable_judge: bool = True) -> tuple[str, str]:
    """Return (verdict, detail) where verdict is PASS / FAIL / UNSCORED."""
    method = item.get("scorer")

    if method == "numeric":
        gold = item.get("gold_numeric")
        tol = item.get("tolerance", NUMERIC_REL_TOL)
        ok = numeric_ok(answer, gold, tol)
        return ("PASS" if ok else "FAIL", f"numeric ~= {gold} (±{tol:.0%})")

    if method == "entity":
        ents = item.get("gold_entities", [])
        ok = entity_ok(answer, ents)
        return ("PASS" if ok else "FAIL", f"entities {ents}")

    if method == "judge":
        if not enable_judge:
            return ("UNSCORED", "judge disabled")
        try:
            s, reason = llm_judge(item["question"], item["gold_answer"], answer)
        except Exception as exc:  # a judge failure shouldn't sink the run
            return ("UNSCORED", f"judge error: {exc}")
        verdict = "PASS" if s >= JUDGE_THRESHOLD else "FAIL"
        return (verdict, f"judge {s:.2f}: {reason[:90]}")

    return ("UNSCORED", f"unknown scorer {method!r}")
