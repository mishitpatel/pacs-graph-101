"""
PACS GraphRAG agent — supports Cypher (Neo4j) and SQL (SQLite) backends.

A small REPL that turns English questions into queries, executes them
against a chosen backend, and renders the result in prose. Same two-call
pattern regardless of backend: a planner LLM sees the *schema*, never
the data; a renderer LLM sees the *rows*, never the schema.

Usage:
    python3 agent/agent.py                              # interactive REPL, cypher mode
    python3 agent/agent.py --mode sql                   # interactive, SQL backend
    python3 agent/agent.py --mode both                  # run every question through BOTH backends
    python3 agent/agent.py --question "who has Lab?"    # one-shot, then exit
    python3 agent/agent.py --mode both --question "..." # one-shot, both backends — useful for eval

Transcripts are saved per-question under agent/transcripts/<timestamp>/.
In multi-backend mode each backend gets its own subfolder, so a single
question produces directly diffable artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import anthropic

# Treat the agent/ directory as a flat import root — sibling files like
# `prompts.py` and the `backends/` subpackage become importable as
# `import prompts` / `from backends import …`. This avoids the agent.py
# vs `agent/` package name collision that would otherwise cause a
# circular import.
_AGENT_DIR = Path(__file__).resolve().parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(_AGENT_DIR.parent / ".env")
except ImportError:
    pass

import prompts                                   # noqa: E402
from backends import CypherBackend, SQLBackend   # noqa: E402


# --- Configuration -----------------------------------------------------------

REPO_ROOT      = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = Path(__file__).resolve().parent / "transcripts"
MODEL          = "claude-opus-4-7"
MAX_TOKENS     = 16000


# --- ANSI helpers ------------------------------------------------------------

DIM    = "\033[2m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
RED    = "\033[31m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RESET  = "\033[0m"


def hr(label: str = "") -> str:
    line = "─" * 64
    return f"{DIM}{line}{RESET}" + (f" {label}" if label else "")


def banner_for_backend(name: str) -> str:
    color = {"cypher": CYAN, "sql": MAGENTA}.get(name, BOLD)
    return f"{color}{BOLD}══ {name} ══{RESET}"


# --- Core LLM calls ----------------------------------------------------------

def call_planner(client: anthropic.Anthropic, backend, question: str, today: str) -> str:
    """Ask Claude to compose a query for the question in the backend's language."""
    user_msg = f"TODAY={today}\n\nQuestion: {question}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{
            "type": "text",
            "text": backend.planner_system,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def call_renderer(
    client: anthropic.Anthropic,
    question: str,
    language: str,
    query: str,
    rows: list[dict],
) -> str:
    """Translate rows into prose, language-agnostic."""
    user_msg = (
        f"Question: {question}\n\n"
        f"Query language: {language.upper()}\n"
        f"Query run:\n```{language}\n{query}\n```\n\n"
        f"Rows returned ({len(rows)}):\n{json.dumps(rows, default=str, indent=2)}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{
            "type": "text",
            "text": prompts.RENDERER_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


# --- Per-backend pipeline ----------------------------------------------------

def run_one(
    client: anthropic.Anthropic,
    backend,
    question: str,
    today: str,
    transcript_folder: Path,
    verbose: bool = True,
) -> dict:
    """Run plan→safety→execute→render for one backend. Save artifacts. Return summary."""
    backend_folder = transcript_folder / backend.name
    backend_folder.mkdir(parents=True, exist_ok=True)

    summary = {"backend": backend.name, "status": None, "rows": 0,
               "query": "", "answer": ""}

    if verbose:
        print(banner_for_backend(backend.name))

    # 1. Plan
    if verbose: print(hr("plan"))
    try:
        plan_text = call_planner(client, backend, question, today)
    except anthropic.APIError as e:
        summary["status"] = "planner_failed"
        msg = f"Planner failed: {e}"
        if verbose: print(f"{RED}{msg}{RESET}")
        (backend_folder / "error.txt").write_text(msg + "\n")
        return summary

    query = backend.extract(plan_text)
    summary["query"] = query
    (backend_folder / f"plan.{backend.file_ext}").write_text(query + "\n", encoding="utf-8")
    if verbose: print(textwrap.indent(query, "  "))

    # 2. Safety
    if reason := backend.safety(query):
        summary["status"] = "rejected"
        msg = f"REJECTED — {reason}"
        if verbose: print(f"{RED}{msg}{RESET}")
        (backend_folder / "error.txt").write_text(msg + "\n")
        return summary

    # 3. Execute
    if verbose: print(hr("rows"))
    try:
        rows = backend.run(query)
    except Exception as e:
        summary["status"] = "execution_failed"
        msg = f"{backend.name} error: {e}"
        if verbose: print(f"{RED}{msg}{RESET}")
        (backend_folder / "rows.json").write_text("[]\n")
        (backend_folder / "error.txt").write_text(msg + "\n")
        return summary

    summary["rows"] = len(rows)
    (backend_folder / "rows.json").write_text(
        json.dumps(rows, default=str, indent=2) + "\n", encoding="utf-8"
    )
    if verbose:
        for r in rows[:5]:
            print(textwrap.indent(json.dumps(r, default=str), "  "))
        if len(rows) > 5:
            print(f"  {DIM}... ({len(rows) - 5} more){RESET}")
        if not rows:
            print(f"  {DIM}(empty result){RESET}")

    # 4. Render
    if verbose: print(hr("answer"))
    try:
        answer = call_renderer(client, question, backend.language, query, rows)
    except anthropic.APIError as e:
        summary["status"] = "renderer_failed"
        msg = f"Renderer failed: {e}"
        if verbose: print(f"{RED}{msg}{RESET}")
        (backend_folder / "error.txt").write_text(msg + "\n")
        return summary

    summary["status"] = "ok"
    summary["answer"] = answer
    (backend_folder / "answer.txt").write_text(answer + "\n", encoding="utf-8")
    if verbose: print(f"{GREEN}{answer}{RESET}")

    return summary


# --- REPL --------------------------------------------------------------------

def process_question(
    client: anthropic.Anthropic,
    backends: list,
    question: str,
    today: str,
    verbose: bool = True,
) -> Path:
    """Run a question through every active backend; save a shared transcript folder."""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    folder = TRANSCRIPT_DIR / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    folder.mkdir(exist_ok=True)
    (folder / "question.txt").write_text(question + "\n", encoding="utf-8")

    for backend in backends:
        run_one(client, backend, question, today, folder, verbose=verbose)

    if verbose:
        print(f"{DIM}  saved → {folder.relative_to(REPO_ROOT)}{RESET}")
    return folder


def repl(backends: list, today: str) -> int:
    """Interactive loop. Quit with `exit`, Ctrl-D, or Ctrl-C."""
    client = anthropic.Anthropic()

    names = ", ".join(b.name for b in backends)
    print(f"""\
{BOLD}PACS GraphRAG agent{RESET}
  model:    {MODEL}
  backends: {names}
  today:    {today}
  saves:    agent/transcripts/

Type a question and press Enter. {DIM}exit{RESET} or Ctrl-D to quit.
""")

    while True:
        try:
            question = input(f"\n{CYAN}?{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit", ":q"):
            break
        process_question(client, backends, question, today, verbose=True)

    return 0


# --- Backend selection -------------------------------------------------------

def make_backends(mode: str) -> list:
    """Build the requested backend instances. Each may take seconds to connect."""
    if mode == "cypher":
        return [CypherBackend(REPO_ROOT)]
    if mode == "sql":
        return [SQLBackend(REPO_ROOT)]
    if mode == "both":
        return [CypherBackend(REPO_ROOT), SQLBackend(REPO_ROOT)]
    raise ValueError(f"unknown mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--mode", choices=["cypher", "sql", "both"], default="cypher",
        help="Which backend(s) to run questions through (default: cypher)",
    )
    parser.add_argument(
        "--question", "-q", default=None,
        help="Run one question and exit (instead of starting the REPL)",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY is not set in the environment.{RESET}",
              file=sys.stderr)
        return 1

    try:
        backends = make_backends(args.mode)
    except Exception as e:
        print(f"{RED}Failed to initialise backends: {e}{RESET}", file=sys.stderr)
        return 1

    today = datetime.now().date().isoformat()

    try:
        if args.question:
            client = anthropic.Anthropic()
            process_question(client, backends, args.question, today, verbose=True)
            return 0
        return repl(backends, today)
    finally:
        for b in backends:
            try:
                b.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
