# PACS Graph 101

A pedagogical project teaching three concepts in order — **ontology**, **knowledge graph**, and **GraphRAG** — using a toy Physical Access Control System (PACS) as the running example.

Everything is built around one question:

> *"Should this credential, at this reader, right now, open this door?"*

The whole stack — schema, instance data, Neo4j queries, temporal model, GraphRAG agent — exists to answer that question and explain *why*.

For an architectural walkthrough with code snippets and diagrams, open `architecture.html` (served via `python3 -m http.server` — see below).

## File tour

```
pacs-graph-101/
├── ontology.md         ← the schema (classes + predicates + why)
├── graph.json          ← the instance (people, badges, groups, doors, ...)
├── docker-compose.yml  ← Neo4j Community service
├── neo4j/
│   ├── import.py       ← loads graph.json into Neo4j (carries validity windows)
│   ├── requirements.txt
│   └── cypher.md       ← teaching ladder of Cypher queries (incl. §6 time-travel)
├── scenarios.md        ← auditor-style worked questions over the temporal graph
├── graphrag.md         ← open-form NL query layer explainer
├── agent/
│   ├── agent.py        ← GraphRAG REPL: NL → Cypher → rows → prose
│   ├── prompts.py      ← planner + renderer system prompts
│   ├── requirements.txt
│   └── transcripts/    ← per-question artifacts saved by the agent (gitignored)
├── phase4/
│   ├── schema.sql      ← SQLite mirror schema (relational shape of the same data)
│   ├── import_sql.py   ← loads graph.json into SQLite
│   └── comparison.md   ← GraphRAG vs SQL: side-by-side queries + honest verdict
├── architecture.html   ← full architectural walkthrough (read this!)
├── .env.example        ← committed template for local secrets
├── PROGRESS.md         ← living learning checklist (resume here)
├── README.md           ← this file
└── CLAUDE.md           ← context for future Claude Code sessions
```

The teaching order is deliberate: **schema → instance → Cypher in a real graph DB → temporal model + auditor scenarios → GraphRAG agent**. Each file is small enough to read top-to-bottom.

### `ontology.md` — the schema

The **vocabulary** of the system. Answers: *what kinds of things exist, and what kinds of relationships can hold between them?*

- **8 classes:** Person, Credential, Reader, Door, Zone, AccessGroup, Schedule, Controller.
- **7 predicates:** `holds`, `member_of`, `grants_access_to`, `active_during`, `controls`, `protects`, `managed_by`.
- **Modeling notes** explaining the *why* — e.g. AccessGroup exists so you don't connect every Person directly to every Door (quadratic edges); Schedule attaches to the group, not the person; Door is the boundary, Zone is the space.

The ontology is a contract. It contains no data and makes no decisions. It tells you what *shape* the data takes and what *shape* the questions can take.

### `graph.json` — the instance

The actual data that conforms to the schema. Two arrays:

- **`nodes`** — Alice/Bob/Carol (people), four credentials, three access groups (Employees / ContractorsDay / Admins), two schedules, three readers/doors/zones, one controller.
- **`edges`** — each with `from`, `to`, `predicate`, and (for policy edges) optional `valid_from` / `valid_to`.

The data is intentionally tiny and hand-crafted so that worked examples land:

- **Alice** — Employees, closed 2026-05-10 (left the company)
- **Bob** — ContractorsDay twice: closed 2026-04-15, then re-instated 2026-04-22 with a new badge
- **Carol** — Employees + Admins (promoted 2026-03-01)

### `graphrag.md` — the open-form query layer

The reasoner-in-Cypher answers *one* closed-form question per query ("can X open Y at time T?"). But operators ask things in English: *"who could have been in the server room after hours last month?"* Plain RAG (chunk-and-embed) will hallucinate — names co-occurring in a log get conflated. The graph already knows the truth.

GraphRAG is the fix: let an LLM *plan a traversal* against the ontology, run it against the graph deterministically, then render the result as prose.

```
NL question  ──►  schema-aware plan  ──►  graph traversal  ──►  grounded answer
   (LLM)              (LLM)                  (deterministic)         (LLM)
```

`graphrag.md` is the conceptual explainer; `agent/` is the implementation.

## Running it

### Architecture walkthrough (static HTML)

```bash
set -a; [ -f .env ] && . ./.env; set +a   # load HTTP_PORT if .env exists
python3 -m http.server "${HTTP_PORT:-8000}"
# then open http://localhost:${HTTP_PORT:-8000}/architecture.html
```

### Neo4j layer

```bash
# 1. Start Docker Desktop, then:
docker compose up -d

# 2. Install the Python driver and run the importer
pip3 install -r neo4j/requirements.txt
python3 neo4j/import.py
```

When the importer prints `Done.`, open <http://localhost:7474> (login: `neo4j` / `pacsgraph101`) and work through `neo4j/cypher.md` — a teaching ladder of queries that exercise each side-condition flavor (temporal, cardinality, disjunction, negation, variable-length path, integrity) and the time-travel patterns in §6.

To stop the DB: `docker compose down`. To wipe its data: `docker compose down -v` and `rm -rf neo4j_data/`.

### GraphRAG agent

```bash
# 1. Make sure ANTHROPIC_API_KEY is set (in shell env or in .env at the repo root).
cp .env.example .env   # then edit

# 2. Install agent dependencies and run.
pip3 install -r agent/requirements.txt
python3 agent/agent.py                  # interactive, Cypher (default)
python3 agent/agent.py --mode sql       # interactive, SQL (against phase4/pacs.db)
python3 agent/agent.py --mode both      # both backends, side by side
python3 agent/agent.py -q "who has Lab access?" --mode both    # one-shot
```

Same two-call architecture for both backends — a planner LLM sees the *schema* and emits a query, a renderer LLM sees the *rows* and emits prose. The backends are selected by `--mode`; the safety machinery is identical (regex gate + driver-level read-only mode); the renderer is shared.

Each question saves a transcript folder:

```
agent/transcripts/<timestamp>/
├── question.txt              ← shared
├── cypher/
│   ├── plan.cypher
│   ├── rows.json
│   └── answer.txt
└── sql/                      ← only present if --mode sql or both
    ├── plan.sql
    ├── rows.json
    └── answer.txt
```

In `--mode both`, the directly diffable per-backend folders make programmatic comparison trivial — useful when we get to AI-eval-style stress testing of the two backends on the same question set.

### Phase 4 — GraphRAG vs SQL comparison

```bash
python3 phase4/import_sql.py            # build SQLite mirror at phase4/pacs.db
sqlite3 phase4/pacs.db                  # paste queries from phase4/comparison.md
```

The same `graph.json` populates both Neo4j and SQLite. `phase4/comparison.md` walks nine canonical questions through both query languages side-by-side and gives an honest verdict on each — *not* "GraphRAG always wins"; SQL is competitive or cleaner for most of them. The killer Cypher case is variable-length path matching; the killer SQL case is window functions.

### Eval harness (Phase 5)

```bash
python3 eval/run.py                       # all questions, both backends
python3 eval/run.py --questions q01,q03   # subset by id
python3 eval/run.py --mode sql            # SQL-only
python3 eval/run.py --max 3               # cap for cheap iteration
```

`eval/questions.yaml` is the canonical 15-question set (snapshot, temporal-as-of, re-instatement, history, aggregate, variable-length, out-of-scope). Each question carries `expected_rows` for deterministic row-set scoring.

Each run writes `eval/reports/<timestamp>/`:
- `report.md` — aggregate stats, per-category breakdown, per-question detail (query + answer + missing/extra rows), and an Opus-4.7 cost estimate
- `raw.json` — all per-question records (for re-analysis)
- `transcripts/` — per-question per-backend artifacts (gitignored along with `reports/`)

Current canonical result on this question set: **cypher 14/15, sql 14/15**, ~$0.40 / 3 minutes per full run. The one open failure (`q15_carol_promotion_reason`) is a known limitation of row-set scoring for trick questions — motivates the LLM-judge layer in Phase 6.

## Dependencies

| What | Why | How loaded |
| --- | --- | --- |
| **Neo4j 5 Community** | Local graph database | Docker (`docker compose up -d`) |
| **neo4j Python driver** | Talks to Neo4j over Bolt | `pip3 install -r neo4j/requirements.txt` |
| **anthropic SDK** | Claude API for planner + renderer | `pip3 install -r agent/requirements.txt` |
| **python-dotenv** | Loads `.env` into env vars at startup | same |
| **highlight.js** (architecture.html only) | Syntax-highlighting for code snippets | CDN |
| Python 3.x | `http.server` for static docs + import / agent scripts | System |
| Playwright (optional) | Headless browser to verify HTML pages programmatically | `pip install playwright && python3 -m playwright install chromium` |
