# PACS Graph 101

A pedagogical project teaching three concepts in order — **ontology**, **knowledge graph**, and **GraphRAG** — using a toy Physical Access Control System (PACS) as the running example.

Everything is built around one question:

> *"Should this credential, at this reader, right now, open this door?"*

The whole stack — schema, instance data, visualization, reasoner, NL-query layer — exists to answer that question and explain *why*.

## Running it

No build step, no package manager. Just a static site:

```bash
python3 -m http.server 8000
# then open http://localhost:8000/
```

`index.html` uses an ES module import (`reasoner.js`) and `fetch("graph.json")`, both of which require HTTP — opening the file via `file://` will fail with CORS errors.

The only runtime dependency is **vis-network 9.1.9**, loaded from the unpkg CDN. There is no `node_modules`, no bundler, no framework.

## File tour

```
pacs-graph-101/
├── ontology.md         ← the schema (classes + predicates + why)
├── graph.json          ← the instance (3 people, 3 doors, etc.)
├── index.html          ← the viewer + reasoner UI
├── reasoner.js         ← the graph-walking decision engine
├── graphrag.md         ← NL-query layer explainer
├── docker-compose.yml  ← Neo4j Community service for Phase 1+
├── neo4j/
│   ├── import.py       ← loads graph.json into Neo4j (carries validity windows)
│   ├── requirements.txt
│   └── cypher.md       ← teaching ladder of Cypher queries (incl. §6 time-travel)
├── scenarios.md        ← auditor-style worked questions (Phase 2)
├── agent/
│   ├── agent.py        ← GraphRAG REPL (Phase 3): NL → Cypher → rows → prose
│   ├── prompts.py      ← planner + renderer system prompts
│   ├── requirements.txt
│   └── transcripts/    ← per-question artifacts saved by the agent (gitignored)
├── .env.example        ← committed template for local secrets
├── PROGRESS.md         ← living learning checklist (resume here)
├── README.md           ← this file
└── CLAUDE.md           ← context for future Claude Code sessions
```

The teaching order is deliberate: **schema → instance → visualization → reasoner (closed-form decisions) → Cypher in a real graph DB → temporal model + auditor scenarios → GraphRAG (open-form NL queries)**. Each file is small enough to read top-to-bottom.

### `ontology.md` — the schema

The **vocabulary** of the system. Answers: *what kinds of things exist, and what kinds of relationships can hold between them?*

- **8 classes:** Person, Credential, Reader, Door, Zone, AccessGroup, Schedule, Controller.
- **7 predicates:** `holds`, `member_of`, `grants_access_to`, `active_during`, `controls`, `protects`, `managed_by`.
- **Modeling notes** explaining the *why* — e.g. AccessGroup exists so you don't connect every Person directly to every Door (quadratic edges); Schedule attaches to the group, not the person, so the same person can be 24x7 via one group and 9-5 via another; Door is the boundary, Zone is the space.

The ontology is a contract. It contains no data and makes no decisions. It tells you what *shape* the data takes and what *shape* the questions can take.

### `graph.json` — the instance

The actual data that conforms to the schema. Two arrays:

- **`nodes`** — 21 of them: Alice/Bob/Carol (people), three badges, three access groups (Employees / ContractorsDay / Admins), two schedules (24x7 / BusinessHours), three readers, three doors, three zones, one controller panel.
- **`edges`** — 23 of them, each with `from`, `to`, and `predicate`. E.g. `{from: "alice", to: "grp_emp", predicate: "member_of"}` means *Alice is a member of the Employees group*.

The data is intentionally tiny and hand-crafted so that worked examples land:

- **Alice** (Employees) → can open Front Door + Lab Door, 24/7.
- **Bob** (ContractorsDay) → can open Front Door only, business hours only.
- **Carol** (Employees + Admins) → can open Front Door + Lab Door + Server Room, 24/7.

### `index.html` — the viewer + reasoner UI

Three responsibilities in one file:

1. **Render the graph.** Fetch `graph.json`, hand it to vis-network with per-class colors and shapes, let physics settle.
2. **Sidebar.** Class legend, predicate signatures (`member_of · Person → AccessGroup`), and the **"Ask the reasoner"** controls.
3. **Wire the reasoner to the UI.** Pick person + door + time, hit **Decide**: call `canAccess(...)`, show a green GRANT or red DENY verdict with the reason, and on grant highlight the justifying chain on the graph itself (thicken edges, ring nodes in green).

### `reasoner.js` — the decision engine

A small ES module exporting:

- **`buildIndex(graph)`** — builds an adjacency lookup `(fromNode, predicate) → [neighbors]` so traversal is O(1) per hop.
- **`canAccess(graph, personId, doorId, atTime)`** — walks the chain and returns `{decision, path, edges, reason}`.

The rule, in words:

```
GRANT iff there exists an AccessGroup G such that
  Person  --member_of-->        G
  G       --grants_access_to--> Door
  G       --active_during-->    Schedule S
  S       covers `atTime`
```

Two opinions are baked in:

- **Fail-closed.** Unknown schedule → deny. Safety default.
- **Diagnostic deny.** The `reason` distinguishes "person is in no group", "no group grants this door", and "groups grant the door but no schedule covers this time". A boolean would have been easier; this is more useful.

### `graphrag.md` — the open-form query layer

`reasoner.js` answers *one* closed-form question ("grant or deny?"). But operators ask things in English: *"who can enter the server room after hours?"* Plain RAG (chunk-and-embed) will hallucinate — *Alice* and *server room* co-occur in a log, so it says she can enter. The graph already knows she can't.

GraphRAG is the fix: let an LLM *plan a traversal* against the ontology, run it against the graph deterministically, then render the result as prose.

```
NL question  ──►  schema-aware plan  ──►  graph traversal  ──►  grounded answer
   (LLM)              (LLM)                  (deterministic)         (LLM)
```

Same graph, same ontology — but a different *caller*. The reasoner runs at the door; GraphRAG runs in the operator console. `graphrag.md` is the explainer with worked examples, not (yet) an implementation.

## How the graph gets drawn

Two ingredients:

**1. The vis-network library.** Take "here are some nodes, here are some edges" and render an interactive force-directed graph on an HTML `<canvas>`. It handles:

- **Force simulation.** Nodes as charged particles that repel each other, edges as springs pulling connected nodes together; iterate until the system stabilizes. This project uses the `barnesHut` solver (a fast approximation good for graphs of this size).
- **Rendering.** Shapes (dots, hexagons, diamonds, boxes, ...), labels, curved edges with arrows, drag/zoom/hover.
- **Layout.** Picks initial positions; physics moves things into a readable arrangement.

Loaded from CDN, no install:

```html
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
```

**2. Per-class color and shape.** Generic blobs would be unreadable. Every class gets a distinct visual identity so the graph is legible at a glance:

```js
const CLASS_COLORS = { Person: "#58a6ff", Credential: "#bc8cff",
                       AccessGroup: "#f0c674", ... };
const CLASS_SHAPES = { Person: "dot", Credential: "diamond",
                       AccessGroup: "hexagon", Door: "box", ... };
```

`index.html` fetches `graph.json`, maps each node through those tables, hands the result to vis-network, and the picture emerges.

## Running the Neo4j layer (Phase 1)

The repo can also import the same graph into Neo4j Community Edition for a real graph DB experience (Cypher queries, Neo4j Browser visualization).

```bash
# 1. Start Docker Desktop, then:
docker compose up -d

# 2. Install the Python driver and run the importer
cd neo4j
pip3 install -r requirements.txt
python3 import.py
```

When the importer prints `Done.`, open <http://localhost:7474> in a browser (login: `neo4j` / `pacsgraph101`) and start working through `neo4j/cypher.md` — a teaching ladder of queries that mirror each side-condition flavor (temporal, cardinality, disjunction, negation, etc.).

To stop the DB: `docker compose down`. To wipe its data: `docker compose down -v` and `rm -rf neo4j_data/`.

## Running the GraphRAG agent (Phase 3)

```bash
# 1. Make sure ANTHROPIC_API_KEY is set (in env or in .env at the repo root).
#    Copy .env.example to .env and fill in if you prefer that route.
cp .env.example .env   # then edit

# 2. Install agent dependencies and run.
pip3 install -r agent/requirements.txt
python3 agent/agent.py
```

You'll get an interactive REPL. Type questions in English; each turn shows the planned Cypher, the rows it returned, and the rendered answer. Every turn is also saved under `agent/transcripts/<timestamp>/` (four files: `question.txt`, `plan.cypher`, `rows.json`, `answer.txt`) so Phase 4 (failure analysis) has comparable artifacts.

The agent enforces read-only Cypher in two places: a regex safety gate before execution, and a `default_access_mode="r"` Neo4j session that the driver itself enforces. Belt and braces.

## Dependencies

| What | Why | How loaded |
| --- | --- | --- |
| **vis-network 9.1.9** | Force-directed graph rendering | CDN (`unpkg`) |
| **Neo4j 5 Community** | Local graph database (Phase 1+) | Docker (`docker compose up -d`) |
| **neo4j-python driver** | Talks to Neo4j over Bolt from `import.py` | `pip3 install -r neo4j/requirements.txt` |
| Python (any 3.x) | `python3 -m http.server` for local dev + import script | System |
| Playwright (optional) | Headless browser to verify UI changes programmatically | `pip install playwright && python3 -m playwright install chromium` |

Everything else is plain web platform: HTML, CSS, ES modules, `fetch()`, `<canvas>`.
