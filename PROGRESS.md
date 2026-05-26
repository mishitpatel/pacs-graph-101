# PACS Graph 101 — Learning Progress

> Living checklist. When everything is checked, we delete this file.

## Concepts learned so far
- [x] **Ontology** — classes + predicates + modeling notes (why each choice)
- [x] **Knowledge graph** — instance data conforming to the ontology
- [x] **Topology vs side conditions** — the path existing ≠ the path being valid (the rule has the same shape in any layer that expresses it)
- [x] **Conjunctive queries** — multiple sub-patterns joined by AND
- [x] **The three axes of conditions** — where they attach (node/edge/path/context), what they test, how they compose
- [x] **Side-condition taxonomy** — temporal, state, threshold, cardinality, disjunction, negation, authorization, spatial, probabilistic
- [x] **Cypher** — real query-language expression of everything we built by hand
- [x] **Empty-result-as-assertion** — in declarative graph queries, an empty result often *is* the success case (3d, 3f)
- [x] **Arrow direction in Cypher patterns** — `->` filters matching edges; cross "against the grain" predicates with reversed or undirected arrows
- [x] **Door-centric vs zone-centric questions** — 3a vs 3c teaches why the ontology separates them
- [x] **Temporal graph modeling** — validity windows on edges, append-only revocation, the live-at-`$when` filter pattern
- [x] **Validity time vs transaction time** — what each axis answers and why we picked validity-only for now
- [x] **Re-instatement pattern** — multiple edges of the same predicate between the same nodes with non-overlapping windows; credentials are re-issued, not reactivated
- [x] **GraphRAG agent** — two-call pattern (planner + renderer), safety gates, prompt caching, transcripts
- [x] **Prompt caching** — prefix match, breakpoint placement, silent invalidators, cost picture
- [x] **GraphRAG vs SQL** — honest side-by-side: SQL wins on aggregates/window-functions/single-row lookups; Cypher wins decisively on variable-length paths; temporal queries are roughly tied

## Artifacts built
- [x] `ontology.md` — schema (with temporal-model section)
- [x] `graph.json` — instance data with validity windows
- [x] `graphrag.md` — open-form query explainer
- [x] `docker-compose.yml` + `neo4j/import.py` — Neo4j layer
- [x] `neo4j/cypher.md` — teaching ladder of queries (incl. §6 time-travel)
- [x] `scenarios.md` — auditor-style worked questions
- [x] `agent/agent.py` + `agent/prompts.py` — GraphRAG REPL
- [x] `architecture.html` — full architectural walkthrough
- [x] `README.md`, `CLAUDE.md`, `PROGRESS.md`

## Phase 1 — Neo4j (complete)
- [x] Neo4j Community running locally via Docker
- [x] `graph.json` imported into Neo4j (with Schedule coverage properties)
- [x] Neo4j Browser opened at `localhost:7474`
- [x] Work through `neo4j/cypher.md`:
  - [x] §0 Bootstrap — see whole graph
  - [x] §1 Property and membership — rows-vs-sub-graphs, `collect`/aggregation
  - [x] §2 Reasoner expressed in Cypher — `WITH` for parameters, `EXISTS { }` sub-queries, Cypher temporal types
  - [x] §3 Side-condition flavors — temporal, cardinality, disjunction, negation, variable-length path, integrity
  - [x] §4 Diagnostic deny — three nested existence questions
  - [x] §5 Open-ended prompts — first three answered; 5.4 & 5.5 deferred to Phase 2 (they need graph modifications)
- [x] Fixed bug in 3e (`cypher.md` had `-[*1..6]->` but credentials have no outgoing edges; switched to undirected `-[*1..6]-` with `DISTINCT`)

## Phase 2 — Temporal graph (complete)
- [x] `ontology.md` documents the temporal model — which predicates carry `valid_from`/`valid_to`, validity-time vs transaction-time distinction, append-only revocation, re-instatement pattern
- [x] `graph.json` enriched with the seeded timeline (Alice/Bob/Carol with dates of joining/leaving/promoting/re-instating)
- [x] `neo4j/import.py` carries validity windows into Neo4j as native `date` properties; `MERGE→CREATE` for relationships to allow multiple tenures
- [x] `neo4j/cypher.md` §6 — nine time-travel query patterns (incl. tenure enumeration + gap detection)
- [x] `scenarios.md` — nine auditor-style worked scenarios (incl. re-instatement audit)
- [x] CLAUDE.md updated with the append-only invariant
- [x] Walked through §6 + scenarios.md in Neo4j Browser end-to-end
- [ ] (Deferred) `:denylist` predicate — defer until needed by a concrete scenario
- [ ] (Deferred) Node `status` history — full-bitemporal territory, out of Phase 2 scope

## Phase 3 — GraphRAG agent (complete)
- [x] `agent/agent.py`: NL question → Claude (Opus 4.7, adaptive thinking) plans Cypher → execute → Claude renders prose
- [x] Planner sees the *schema* (`ontology.md`) — never sees data
- [x] Renderer sees the *rows* + question — never composes queries
- [x] Safety: regex gate rejects `CREATE`/`MERGE`/`DELETE`/`DETACH`/`SET`/`REMOVE`/`DROP`/`LOAD`/`CALL`/`FOREACH`; Neo4j session is `default_access_mode="r"` for belt-and-braces
- [x] Prompt caching via `cache_control: ephemeral` on the planner system prompt (today's date + question go in user msg to keep cache warm)
- [x] Interactive REPL with ANSI-coloured per-turn breakdown (plan / rows / answer)
- [x] Per-turn transcripts saved to `agent/transcripts/<timestamp>/` (gitignored) — four files each
- [x] `.env` support via python-dotenv (falls back to shell env)
- [x] **First end-to-end run** — user confirmed working with a handful of questions

## Phase 4 — GraphRAG vs SQL comparison (complete)
- [x] **Decision:** chunked-doc RAG would be a strawman comparison — this data never lives that way in reality. The real incumbent is SQL/relational, so we built a SQLite mirror.
- [x] `phase4/schema.sql` — faithful relational mirror: one entity table per class, one junction table per predicate, `valid_from` / `valid_to` columns on policy junctions, INTEGER PK on temporal junctions so re-instatement creates a new row (mirrors Cypher's CREATE-not-MERGE pattern)
- [x] `phase4/import_sql.py` — reads the same `graph.json` and populates SQLite (idempotent)
- [x] `phase4/comparison.md` — nine canonical questions answered in both Cypher and SQL with honest verdicts
- [x] **Honest result:** SQL wins more individual queries than Cypher does (Q3 single-row, Q4 count, Q5 gap detection via `LEAD()`, Q8 aggregates). Cypher's decisive win is Q9 variable-length path matching. Most temporal access queries are roughly tied.
- [x] Extended the agent with multi-backend support (`--mode cypher|sql|both`). Same two-call architecture, swappable backends in `agent/backends/`. Transcripts now have per-backend subfolders so a single question in `--mode both` produces directly diffable artifacts.

## Phase 5 — Modern AI eval (planned)
- [ ] Define a canonical question set (probably 15–25 questions) spanning easy/medium/hard difficulty and the question shapes from `phase4/comparison.md`
- [ ] **Reference answers** — ground truth for each question, derived from the data (`graph.json` + the seed timeline). Each question carries an expected row set and / or expected facts.
- [ ] **Judges** — programmatic correctness check (row-set match where applicable), plus an LLM-judge for prose-quality and intent-match scoring
- [ ] **Eval harness** — `eval/run.py` that batches each question through `--mode both` and produces a structured report: per-question correctness, win/lose/tie per backend, latency, token cost (from cache stats), failure modes
- [ ] **Score breakdown by category** — re-instatement, temporal as-of, variable-length, aggregates, etc. The headline isn't a single number; it's a profile showing where each backend wins.
- [ ] **Stress dimensions** — ambiguous questions, out-of-scope questions ("why was Carol promoted?"), trick questions ("does Alice have access today?" — yes/no depends on which interpretation of "today" she means), adversarial inputs

## Cold-start cheatsheet (for tomorrow)
```bash
# Architecture walkthrough (static HTML)
python3 -m http.server "${HTTP_PORT:-8000}"   # → http://localhost:8000/architecture.html

# Neo4j
docker compose up -d               # → http://localhost:7474 (neo4j / pacsgraph101)
python3 neo4j/import.py            # (only if you reset the DB)
```
- Project tour: `README.md`
- Agent operating notes: `CLAUDE.md`
- Cypher teaching ladder: `neo4j/cypher.md`
