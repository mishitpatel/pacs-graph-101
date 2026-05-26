"""
Eval harness — batch a question set through the agent in --mode both,
score row-set correctness, and produce a markdown report + raw JSON.

Usage:
    python3 eval/run.py                       # run all questions in questions.yaml
    python3 eval/run.py --questions q01,q03   # run a subset by id
    python3 eval/run.py --mode cypher         # only one backend
    python3 eval/run.py --max 2               # cap N to limit cost during iteration

Outputs:
    eval/reports/<timestamp>/report.md       — human-readable markdown
    eval/reports/<timestamp>/raw.json        — full per-record JSON (for re-analysis)
    eval/reports/<timestamp>/transcripts/    — per-question agent transcripts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Put the repo root on sys.path so `import agent.agent` and `from eval import …`
# both work. We DON'T add `<repo>/agent` because that would make Python find
# `agent.py` as a top-level module, shadowing the `agent` package.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# YAML is the one external thing — try PyYAML first, fall back to a tiny
# hand-roll if it's not installed. PyYAML is in agent/requirements.txt
# transitively (anthropic depends on it).
try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

import anthropic

import agent.agent as agent_module          # the agent we just refactored
from agent.backends import CypherBackend, SQLBackend

from eval import scorer, reporter           # noqa: E402


# --- Loading ---------------------------------------------------------------

def load_questions(path: Path, only_ids: list[str] | None, limit: int | None) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if only_ids:
        wanted = set(only_ids)
        raw = [q for q in raw if q["id"] in wanted]
    if limit is not None:
        raw = raw[:limit]
    return raw


def make_backends(mode: str):
    if mode == "cypher": return [CypherBackend(_ROOT)]
    if mode == "sql":    return [SQLBackend(_ROOT)]
    return [CypherBackend(_ROOT), SQLBackend(_ROOT)]


# --- Main ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--mode", choices=["cypher", "sql", "both"], default="both")
    parser.add_argument("--questions", default=None,
                        help="Comma-separated question ids (default: all)")
    parser.add_argument("--max", type=int, default=None,
                        help="Cap the number of questions (after id filter)")
    parser.add_argument("--questions-file", default=str(_ROOT / "eval" / "questions.yaml"))
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        return 1

    only_ids = args.questions.split(",") if args.questions else None
    questions = load_questions(Path(args.questions_file), only_ids, args.max)
    if not questions:
        print("No questions matched the filter.", file=sys.stderr)
        return 1

    print(f"Loaded {len(questions)} questions, mode={args.mode}")

    # Output dir for this run
    out_dir = _ROOT / "eval" / "reports" / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    transcripts_dir = out_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    backends = make_backends(args.mode)
    client = anthropic.Anthropic()
    today = datetime.now().date().isoformat()

    records: list[dict] = []
    t_run = time.perf_counter()

    try:
        for i, q in enumerate(questions, 1):
            print(f"\n[{i}/{len(questions)}] {q['id']} — {q['question']}")
            result = agent_module.process_question(
                client, backends, q["question"], today,
                verbose=False, transcript_root=transcripts_dir,
            )

            for summary in result["summaries"]:
                # Score against expected rows
                expected = q.get("expected_rows", [])
                if summary["status"] == "ok":
                    score = scorer.score_rows(expected, summary["rows_data"])
                else:
                    score = {"match": False, "expected_count": len(expected),
                             "actual_count": 0, "missing": [], "extra": []}

                rec = {
                    "question_id":     q["id"],
                    "category":        q["category"],
                    "difficulty":      q["difficulty"],
                    "question":        q["question"],
                    "backend":         summary["backend"],
                    "status":          summary["status"],
                    "row_count":       summary["rows"],
                    "match":           score["match"],
                    "missing":         [list(t) for t in score["missing"]],
                    "extra":           [list(t) for t in score["extra"]],
                    "query":           summary["query"],
                    "answer":          summary["answer"],
                    "planner_usage":   summary["planner_usage"],
                    "renderer_usage":  summary["renderer_usage"],
                    "latency_ms":      summary["latency_ms"],
                    "transcript_folder": str(result["folder"].relative_to(_ROOT)),
                }
                records.append(rec)

                verdict = "✓" if rec["match"] else "✗"
                if summary["status"] != "ok":
                    verdict = f"⚠ {summary['status']}"
                print(f"    {summary['backend']:6s} {verdict}  "
                      f"({rec['row_count']} rows)")

    finally:
        for b in backends:
            try:
                b.close()
            except Exception:
                pass

    elapsed = time.perf_counter() - t_run
    print(f"\nDone in {elapsed:.1f}s.")

    # Write outputs
    (out_dir / "raw.json").write_text(
        json.dumps(records, default=str, indent=2) + "\n", encoding="utf-8"
    )
    report_path = reporter.write_report(records, out_dir, model=agent_module.MODEL)
    print(f"Report: {report_path.relative_to(_ROOT)}")
    print(f"Raw:    {(out_dir / 'raw.json').relative_to(_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
