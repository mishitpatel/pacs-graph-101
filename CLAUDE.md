# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read `README.md` first, then `PROGRESS.md`, then `ontology.md`

`README.md` has the project tour. `PROGRESS.md` is the living learning checklist — always check it before suggesting next steps; it reflects the current state of the user's journey. `ontology.md` is the contract — including which predicates are temporal (carry `valid_from`/`valid_to`) and which are static. Don't duplicate those here — this file is the agent-specific operating manual.

This is a pedagogical project. The three concepts being taught are **ontology**, **knowledge graph**, and **GraphRAG**. "Graphreg" is not a term — if a transcript mentions it, treat it as a typo for GraphRAG.

## Running and verifying

```bash
python3 -m http.server 8000        # serve the JS viewer at http://localhost:8000/
docker compose up -d               # start Neo4j (Phase 1+) at http://localhost:7474
python3 neo4j/import.py            # load graph.json into Neo4j (idempotent — wipes and re-imports)
python3 agent/agent.py             # GraphRAG REPL (Phase 3) — needs ANTHROPIC_API_KEY
```

ES module + `fetch` require HTTP — never `file://`. No tests; verification is *running it in a browser*.

Neo4j credentials: `neo4j` / `pacsgraph101` (set in `docker-compose.yml`). Bolt URI: `bolt://localhost:7687`. Data volume: `./neo4j_data` (gitignored). `docker compose down -v` + `rm -rf neo4j_data/` for a clean reset.

Two canonical smoke cases for any reasoner/UI change:

- **Bob + Lab Door + weekday 10am** → DENY (`reason`: "no group of this person grants this door")
- **Carol + Server Room Door + Saturday 03:00** → GRANT via Admins on `sch_247`, justifying path highlighted green

Playwright is installed for headless verification when you can't ask the user to look. Drive `http://localhost:8000/`, sample canvas pixels or read `window.__pacs.network` (exposed in `index.html` for debugging).

## Invariants — preserve these when extending

- **Fail-closed schedule semantics.** `reasoner.js::scheduleCovers` returns `false` for unknown schedule IDs. Do not change to fail-open.
- **Diagnostic deny.** `canAccess` returns a `reason` string that distinguishes the three deny modes (no group / no granting group / no covering schedule). The UI surfaces it. Don't collapse this back into a boolean.
- **The ontology is the contract.** Adding a class or predicate is now a four-place edit: `ontology.md` (document it + why), `graph.json` (instance data using it), `reasoner.js` and/or `index.html` (JS consumers), and `neo4j/import.py` / `neo4j/cypher.md` (DB consumers). Don't add unused predicates.
- **Neo4j label vs Cypher relationship type.** Node labels mirror the `class` field exactly (PascalCase, e.g. `:Person`, `:AccessGroup`). Relationship types mirror the `predicate` field exactly (snake_case, e.g. `:member_of`). Both `import.py` and `cypher.md` depend on this — don't rename one without the other.
- **Schedule semantics live in two places now.** `reasoner.js::scheduleCovers` (JS, for the closed-form viewer) and `neo4j/import.py::SCHEDULE_COVERAGE` (data on the Schedule node, for Cypher queries). When changing semantics, update both. The Cypher version uses ISO weekdays (Mon=1..Sun=7); the JS version uses `Date.getDay()` (Sun=0..Sat=6). This divergence is documented in `import.py` and is intentional — don't "fix" it.

## Temporal model (Phase 2)

Policy edges (`holds`, `member_of`, `grants_access_to`, `active_during`) carry optional `valid_from` / `valid_to` properties; infrastructure edges (`controls`, `protects`, `managed_by`) do not. The authoritative list lives in `ontology.md` — consult before changing.

- **Append-only on policy edges.** Revocation means setting `valid_to = now`, **never `DELETE`**. The closed edge stays queryable; that's what makes auditor scenarios possible. If a script ever wants to delete a policy edge, push back unless it's a clear cleanup of test data.
- **The live-at-`$when` filter is the only new pattern.** `WHERE r.valid_from <= $when AND (r.valid_to IS NULL OR r.valid_to > $when)` — paste it onto every temporal edge in a query. `cypher.md` §6 and `scenarios.md` are full of examples.
- **Reasoner / Neo4j divergence is intentional.** `reasoner.js` ignores validity windows and treats every edge as currently live — it answers "what is the *current* policy?" Neo4j queries use the windows — they answer "what *was* the policy then?" Same data, two interpretations. If extending the JS reasoner later, accept an optional `asOf` parameter rather than forcing temporal behavior on existing callers.
- **Validity time only, not full bitemporal.** We don't store transaction time (when we *recorded* a fact). That's a deliberate scope cut — adding it would double model complexity for a use case (legal/financial provenance) PACS doesn't need.

## GraphRAG agent (Phase 3)

`agent/agent.py` implements the canonical two-call GraphRAG pattern: a planner LLM sees the *schema* (`ontology.md`) and emits Cypher; a renderer LLM sees the *rows* and emits prose. The two never overlap. This is the central safety property — the planner can't hallucinate data because it never sees data; the renderer can't compose queries because it never gets to.

- **Model:** `claude-opus-4-7` with adaptive thinking + `effort: "high"`. Don't downgrade casually — composing correct temporal Cypher needs reasoning, and the renderer's discipline (no hallucinating beyond rows) holds up better at higher effort.
- **Prompt caching is load-bearing.** The planner system prompt (ontology + temporal model + style notes) is cached via `cache_control: {type: "ephemeral"}`. Anything that would shift the prefix (today's date, the question) lives in the *user* message, not the system prompt. If you edit `prompts.py`, keep `PLANNER_INTRO` and `PLANNER_OUTRO` stable across requests — only the embedded `ontology_text` should change between sessions.
- **Read-only enforced in two places.** A regex safety gate (`FORBIDDEN` in `agent.py`) rejects writes before execution. The Neo4j driver session also uses `default_access_mode="r"`. If you ever need a write — don't. Add a separate write-path with explicit user confirmation.
- **Transcripts are gold.** Every turn saves `{question, plan, rows, answer}` to `agent/transcripts/<timestamp>/`. Phase 4 (failure analysis) reads these. Don't disable transcript saving without a Phase 4 plan to replace it.
- **Schema source of truth.** The planner reads `ontology.md` at agent startup. If the ontology changes, restart the agent — the system prompt won't pick up edits mid-session, and stale ontology = stale Cypher.
- **Pedagogical voice.** Comments and prose explain the *why*, not the *what*. Match `ontology.md`'s tone — short prose + tables + "Modeling notes" sections. `graphrag.md` uses worked examples; match that when extending it.
- **No frameworks.** Plain HTML / ES modules / vis-network from CDN. No npm, no bundler, no React.

## CSS load-bearing layout (don't regress)

`index.html` relies on two CSS rules that look incidental but aren't:

```css
html, body { height: 100vh; overflow: hidden; }
main       { grid-template-rows: minmax(0, 1fr); }
```

Without `grid-template-rows: minmax(0, 1fr)`, the aside's scrollable content drives the grid row height past the viewport. `#graph { height: 100% }` inherits the inflated height, the `<canvas>` grows with it, and `network.fit()` ends up centering the view in the now-offscreen middle of the canvas — *blank screen, no errors*. If you touch the layout, verify the graph still renders before claiming the change works.

## Style commitments

- **Terminal-dark palette** (GitHub-dark-ish): `--bg #0e1116`, `--panel #161b22`, `--ink #e6edf3`, `--muted #8b949e`, `--line #30363d`. Class accents in `index.html::CLASS_COLORS` are canonical — reuse them.
- **Each file small enough to read top-to-bottom.** If a file is getting long, split before adding.
