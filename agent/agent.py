"""
PACS GraphRAG agent.

A small REPL that turns English questions into Cypher, executes them
against Neo4j, and renders the result in prose. Three artifacts are
saved per turn (question, plan, rows, answer) under agent/transcripts/
so Phase 4 (failure analysis) has something to chew on.

The two LLM calls are kept strictly separate:

    user question
        |
        v
    [Claude #1 — Planner]   sees ontology, NOT data; emits Cypher
        |
        v
    [Safety gate — regex]   read-only verbs only
        |
        v
    [Neo4j read session]    executes; returns rows
        |
        v
    [Claude #2 — Renderer]  sees question + Cypher + rows; emits prose
        |
        v
    user

The planner never sees rows. The renderer never composes queries.
This is the central safety property of GraphRAG.

Run:
    pip install -r requirements.txt
    python3 agent.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import anthropic
from neo4j import GraphDatabase

# Load .env if present — optional convenience for the user.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    # python-dotenv is optional; ANTHROPIC_API_KEY in the shell env works too.
    pass

import prompts


# --- Configuration -----------------------------------------------------------

REPO_ROOT      = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = Path(__file__).resolve().parent / "transcripts"
NEO4J_URI      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.environ.get("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "pacsgraph101")
MODEL          = "claude-opus-4-7"
MAX_TOKENS     = 16000

# Forbidden Cypher keywords — defense in depth alongside the read-only
# Neo4j session below. A driver-level read session would already reject
# writes, but rejecting *before* hitting Neo4j gives clearer errors and
# avoids a round trip.
FORBIDDEN = re.compile(
    r"\b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|"
    r"CALL\s+(?!{)|FOREACH)\b",
    flags=re.IGNORECASE,
)
CYPHER_BLOCK = re.compile(r"```(?:cypher|cypher\s*)?\n?(.*?)```", flags=re.DOTALL)


# --- ANSI helpers (no extra dep) --------------------------------------------

DIM    = "\033[2m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
RED    = "\033[31m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
RESET  = "\033[0m"


def hr(label: str = "") -> str:
    line = "─" * 64
    return f"{DIM}{line}{RESET}" + (f" {label}" if label else "")


# --- Core pipeline -----------------------------------------------------------

def extract_cypher(text: str) -> str:
    """Pull a single Cypher block out of the planner's response."""
    matches = CYPHER_BLOCK.findall(text)
    if not matches:
        # Fall back: maybe it returned bare Cypher.
        return text.strip()
    return matches[0].strip()


def safety_check(cypher: str) -> str | None:
    """Return None if safe, else a human-readable reason."""
    if FORBIDDEN.search(cypher):
        match = FORBIDDEN.search(cypher).group(0)
        return f"query contains forbidden write keyword: {match!r}"
    return None


def call_planner(client: anthropic.Anthropic, planner_system: str,
                 question: str, today: str) -> str:
    """Ask Claude to compose a Cypher query for the question."""
    user_msg = f"TODAY={today}\n\nQuestion: {question}"
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[{
            "type": "text",
            "text": planner_system,
            # Stable system prompt → caches across questions. The volatile
            # bits (today's date, the question) live in the user message
            # below, so the cache prefix never shifts.
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    return "".join(b.text for b in response.content if b.type == "text")


def call_renderer(client: anthropic.Anthropic, question: str,
                  cypher: str, rows: list[dict]) -> str:
    """Ask Claude to translate rows into prose."""
    user_msg = (
        f"Question: {question}\n\n"
        f"Cypher run:\n```cypher\n{cypher}\n```\n\n"
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


def run_cypher(driver, cypher: str) -> list[dict]:
    """Execute a Cypher query against a read-only session.

    Neo4j enforces read-only at the session level — even if the safety
    regex were bypassed, the driver would refuse a write. Belt and braces.
    """
    with driver.session(default_access_mode="r") as session:
        result = session.run(cypher)
        return [record.data() for record in result]


def save_transcript(question: str, cypher: str, rows: list[dict], answer: str) -> Path:
    """Save a per-turn folder with the four artifacts. Returns the path."""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    folder = TRANSCRIPT_DIR / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    folder.mkdir(exist_ok=True)
    (folder / "question.txt").write_text(question + "\n", encoding="utf-8")
    (folder / "plan.cypher").write_text(cypher + "\n",  encoding="utf-8")
    (folder / "rows.json").write_text(json.dumps(rows, default=str, indent=2) + "\n", encoding="utf-8")
    (folder / "answer.txt").write_text(answer + "\n",   encoding="utf-8")
    return folder


# --- REPL --------------------------------------------------------------------

BANNER = f"""\
{BOLD}PACS GraphRAG agent{RESET}
  model:   {MODEL}
  graph:   {NEO4J_URI}
  today:   {{today}}
  saves:   agent/transcripts/

Type a question and press Enter. {DIM}exit{RESET} or Ctrl-D to quit.
"""


def repl() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY is not set in the environment.{RESET}", file=sys.stderr)
        return 1

    today = datetime.now().date().isoformat()
    print(BANNER.format(today=today))

    client = anthropic.Anthropic()
    planner_system = prompts.build_planner_system(prompts.read_ontology(REPO_ROOT))

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        driver.verify_connectivity()
    except Exception as e:
        print(f"{RED}Cannot reach Neo4j at {NEO4J_URI}: {e}{RESET}", file=sys.stderr)
        print("  Try: docker compose up -d", file=sys.stderr)
        return 1

    try:
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

            # 1. Plan
            print(hr("plan"))
            try:
                plan_text = call_planner(client, planner_system, question, today)
            except anthropic.APIError as e:
                print(f"{RED}Planner failed: {e}{RESET}")
                continue
            cypher = extract_cypher(plan_text)
            print(textwrap.indent(cypher, "  "))

            # 2. Safety
            reason = safety_check(cypher)
            if reason:
                print(f"{RED}REJECTED — {reason}{RESET}")
                continue

            # 3. Execute
            print(hr("rows"))
            try:
                rows = run_cypher(driver, cypher)
            except Exception as e:
                # Common: Cypher syntax error or unknown property. Surface it
                # to the user; in Phase 4 we'll measure how often this happens.
                print(f"{RED}Neo4j error: {e}{RESET}")
                save_transcript(question, cypher, [], f"[neo4j error] {e}")
                continue
            for r in rows[:5]:
                print(textwrap.indent(json.dumps(r, default=str), "  "))
            if len(rows) > 5:
                print(f"  {DIM}... ({len(rows) - 5} more){RESET}")
            if not rows:
                print(f"  {DIM}(empty result){RESET}")

            # 4. Render
            print(hr("answer"))
            try:
                answer = call_renderer(client, question, cypher, rows)
            except anthropic.APIError as e:
                print(f"{RED}Renderer failed: {e}{RESET}")
                continue
            print(f"{GREEN}{answer}{RESET}")

            # 5. Save
            folder = save_transcript(question, cypher, rows, answer)
            print(f"{DIM}  saved → {folder.relative_to(REPO_ROOT)}{RESET}")

    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    sys.exit(repl())
