"""Ask Filing Analyst from the terminal and watch the agent work.

    python -m app.ask "Which of NVIDIA, AMD, and Intel grew revenue fastest, and why?"

Unlike a plain chatbot, this prints the agent's TRACE as it runs -- the tool it
chose, where the data came from (the SQL database vs. the 10-K filings), the exact
SQL / search query, and the result summary -- then the grounded final answer.
"""
from __future__ import annotations

import argparse
import sys

from app.agent import stream_question

_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def bold(t): return _c("1", t)
def dim(t): return _c("2", t)
def blue(t): return _c("38;5;75", t)
def purple(t): return _c("38;5;141", t)
def green(t): return _c("38;5;78", t)
def gray(t): return _c("38;5;244", t)


def _render(event: dict) -> None:
    kind = event["type"]

    if kind == "plan":
        print(f"\n{dim('plan')}  {event['text']}")

    elif kind == "tool_call":
        tool = event["tool"]
        tag = blue(" SQL ") if tool == "query_financials" else purple(" SEARCH ")
        arrow = gray("→")
        print(f"\n{bold('▸')} {tag} {event.get('label','')}  {arrow}  {gray(event.get('source',''))}")
        if tool == "query_financials":
            for line in (event.get("sql") or "").splitlines() or ["(no query)"]:
                print(f"    {blue(line)}")
        else:
            q = event.get("query", "")
            filt = []
            if event.get("company"): filt.append(event["company"])
            if event.get("year"): filt.append(f"FY{event['year']}")
            suffix = f"  {gray('['+', '.join(filt)+']')}" if filt else ""
            print(f'    {purple(chr(34)+q+chr(34))}{suffix}')

    elif kind == "tool_result":
        tool = event["tool"]
        if tool == "query_financials":
            if event.get("error"):
                print(f"    {_c('31', '✗ ' + event['error'])}")
                return
            n = event.get("row_count", 0)
            cols = ", ".join(event.get("columns", []))
            print(f"    {green('✓')} {n} row{'s' if n != 1 else ''}  {gray('('+cols+')')}")
            for row in event.get("preview", [])[:5]:
                cells = "  ".join(f"{k}={v}" for k, v in row.items())
                print(f"      {gray(cells[:150])}")
        else:
            srcs = event.get("sources", [])
            print(f"    {green('✓')} {len(srcs)} passage{'s' if len(srcs) != 1 else ''}")
            for s in srcs[:5]:
                label = f"{s['ticker']} FY{s['fiscal_year']} · {s.get('section','')} · chunk {s.get('chunk')} · {s.get('score')}"
                print(f"      {gray(label)}")

    elif kind == "synthesizing":
        print(f"\n{dim('synthesizing final answer…')}")

    elif kind == "answer":
        print("\n" + bold("── Answer " + "─" * 60))
        print(event["text"])
        print(bold("─" * 70))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask Filing Analyst (with a visible agent trace).")
    parser.add_argument("question", nargs="+", help="The question to ask.")
    args = parser.parse_args()
    question = " ".join(args.question)

    print(bold("\n┌─ Filing Analyst ────────────────────────────────────────────"))
    print(f"{bold('│')} {question}")
    print(bold("└──────────────────────────────────────────────────────────────"))

    try:
        for event in stream_question(question):
            _render(event)
    except Exception as exc:  # noqa: BLE001 -- CLI surface, show a clean error
        print(f"\n{_c('31', 'Error: ' + str(exc))}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
