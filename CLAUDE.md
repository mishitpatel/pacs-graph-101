# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read `README.md` first, then `PROGRESS.md`, then `ontology.md`

`README.md` has the project tour. `PROGRESS.md` is the living learning checklist — always check it before suggesting next steps; it reflects the current state of the user's journey. `ontology.md` is the contract — including which predicates are temporal (carry `valid_from`/`valid_to`) and which are static. `architecture.html` is the deep-dive walkthrough of every layer, especially the agent. Don't duplicate any of those here — this file is the agent-specific operating manual.

This is a pedagogical project. The three concepts being taught are **ontology**, **knowledge graph**, and **GraphRAG**. "Graphreg" is not a term — if a transcript mentions it, treat it as a typo for GraphRAG.

## Running and verifying

```bash
docker compose up -d                           # start Neo4j → http://localhost:7474
python3 neo4j/import.py                        # load graph.json (idempotent — wipes and re-imports)
python3 agent/agent.py                         # GraphRAG REPL — needs ANTHROPIC_API_KEY
python3 -m http.server "${HTTP_PORT:-8000}"    # serve architecture.html (port from .env, default 8000)
```

Neo4j credentials: `neo4j` / `pacsgraph101` (set in `docker-compose.yml`). Bolt URI: `bolt://localhost:7687`. Data volume: `./neo4j_data` (gitignored). `docker compose down -v` + `rm -rf neo4j_data/` for a clean reset.

Two canonical smoke cases for the temporal model:

- **"Who currently has Lab access?"** → Carol only (Alice's membership closed 2026-05-10).
- **"Who had Lab access on 2026-04-15?"** → Alice + Carol (Alice's revocation was 25 days later).

If those two questions don't produce those answers via either Cypher or the agent, something is wrong with the import or the validity windows.

Playwright is installed for headless verification of `architecture.html` when you need to check the rendered page without asking the user to look.

## Invariants — preserve these when extending

- **The ontology is the contract.** Adding a class or predicate is a three-place edit: `ontology.md` (document it + why), `graph.json` (use it), `neo4j/import.py` / `neo4j/cypher.md` (consume it). Don't add unused predicates.
- **Neo4j label vs Cypher relationship type.** Node labels mirror the `class` field exactly (PascalCase, e.g. `:Person`, `:AccessGroup`). Relationship types mirror the `predicate` field exactly (snake_case, e.g. `:member_of`). Both `import.py` and `cypher.md` depend on this — don't rename one without the other.
- **Schedule semantics live as node properties.** `neo4j/import.py::SCHEDULE_COVERAGE` sets `weekdays` / `start_hour` / `end_hour` on each Schedule node during import. The Cypher version uses ISO weekdays (Mon=1..Sun=7). When changing schedule semantics, update both `import.py` and any planner prompt sections that describe them.
- **Fail-closed on unknown schedules.** Don't add a default-allow path anywhere.

## Temporal model

Policy edges (`holds`, `member_of`, `grants_access_to`, `active_during`) carry optional `valid_from` / `valid_to` properties; infrastructure edges (`controls`, `protects`, `managed_by`) do not. The authoritative list lives in `ontology.md` — consult before changing.

- **Append-only on policy edges.** Revocation means setting `valid_to = now`, **never `DELETE`**. The closed edge stays queryable; that's what makes auditor scenarios possible. If a script ever wants to delete a policy edge, push back unless it's a clear cleanup of test data.
- **The live-at-`$when` filter is the only new pattern.** `WHERE r.valid_from <= $when AND (r.valid_to IS NULL OR r.valid_to > $when)` — paste it onto every temporal edge in a query. `cypher.md` §6 and `scenarios.md` are full of examples.
- **Re-instatement creates a new edge.** If someone leaves and returns, you get a *second* `member_of` edge with a fresh window. The `import.py` uses `CREATE` (not `MERGE`) for relationships specifically to allow this. Credentials are re-issued (new node), not reactivated.
- **Validity time only, not full bitemporal.** We don't store transaction time (when we *recorded* a fact). Adding it would double the model's complexity for a use case PACS doesn't need.

## GraphRAG agent

`agent/agent.py` implements the canonical two-call GraphRAG pattern: a planner LLM sees the *schema* (`ontology.md`) and emits Cypher; a renderer LLM sees the *rows* and emits prose. The two never overlap. This is the central safety property — the planner can't hallucinate data because it never sees data; the renderer can't compose queries because it never gets to.

- **Model:** `claude-opus-4-7` with adaptive thinking + `effort: "high"`. Don't downgrade casually — composing correct temporal Cypher needs reasoning, and the renderer's discipline (no hallucinating beyond rows) holds up better at higher effort.
- **Prompt caching is load-bearing.** The planner system prompt (ontology + temporal model + style notes) is cached via `cache_control: {type: "ephemeral"}`. Anything that would shift the prefix (today's date, the question) lives in the *user* message, not the system prompt. If you edit `prompts.py`, keep `PLANNER_INTRO` and `PLANNER_OUTRO` stable across requests — only the embedded `ontology_text` should change between sessions.
- **Read-only enforced in two places.** A regex safety gate (`FORBIDDEN` in `agent.py`) rejects writes before execution. The Neo4j driver session also uses `default_access_mode="r"`. If you ever need a write — don't. Add a separate write-path with explicit user confirmation.
- **Transcripts are gold.** Every turn saves `{question, plan, rows, answer}` to `agent/transcripts/<timestamp>/`. The next phase (GraphRAG vs SQL comparison) will read these. Don't disable transcript saving without a replacement plan.
- **Schema source of truth.** The planner reads `ontology.md` at agent startup. If the ontology changes, restart the agent — the system prompt won't pick up edits mid-session, and stale ontology = stale Cypher.
- **No frameworks for the agent.** Plain Python + `anthropic` SDK + `neo4j` driver. No LangChain, no LlamaIndex. The mechanics are the lesson; abstractions hide them.

## Pedagogical voice

Match the existing style across files:

- Comments and prose explain the *why*, not the *what*.
- `ontology.md` tone: short prose + tables + "Modeling notes" sections.
- `cypher.md` and `scenarios.md`: worked examples with "what's new in Cypher here" / "why this matters" notes.
- `architecture.html`: dark/terminal aesthetic with ASCII flow diagrams and code snippets in dedicated panels.

When extending any file, match its established voice rather than inventing a new one.
