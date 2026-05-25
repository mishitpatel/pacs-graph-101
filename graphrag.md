# GraphRAG over PACS — natural-language queries, graph-shaped answers

A single hand-written Cypher query answers *one* question: "can this person
open this door now?" Operators ask many more — and they ask in English:

> *"Who can enter the server room after hours?"*
> *"Which doors does Alice have access to today?"*
> *"What groups grant access to the lab?"*
> *"Anyone with a contractor badge in the server room — ever?"*

Plain RAG (chunk the docs, embed, retrieve nearest neighbors, ask an LLM)
gets the *vibe* right and the *facts* wrong. It will happily say "Alice can
enter the server room" because the words *Alice* and *server room* co-occur
in some audit log. The graph already knows she can't. **GraphRAG** is the
fix: let the LLM *plan a traversal*, run it against the graph, and answer
from the result.

## The pattern in three steps

```
NL question  ──►  schema-aware plan  ──►  graph traversal  ──►  grounded answer
   (LLM)              (LLM)                  (deterministic)         (LLM)
```

1. **Plan.** Give the LLM the ontology (the *schema*, not the data) and ask
   it to translate the question into a traversal: start node(s), predicates
   to follow, filters, the shape of the answer set.
2. **Execute.** Run the traversal in code. This step is boring on purpose —
   no creativity, no hallucination, just `neighbors(node, predicate)`.
3. **Render.** Hand the result back to the LLM with the original question
   and let it write prose. The LLM is now a translator, not a knower.

## Worked examples

### Q1. "Who can enter the lab during business hours?"

**Plan (LLM output, given the schema):**

```json
{
  "start": { "class": "Door", "match": { "label": "Lab Door" } },
  "walk": [
    { "predicate": "grants_access_to", "direction": "in",
      "filter": { "active_during.id": "sch_biz" } },
    { "predicate": "member_of", "direction": "in" }
  ],
  "return": "Person"
}
```

**Execute:** doors → groups granting (∩ schedule = `sch_biz`) → members.
On our toy graph: `grp_con` grants `d_lab`? No (only Employees do).
Employees grant `d_lab` on `sch_247`, which *includes* business hours, so
Alice and Carol are in. Bob is out (ContractorsDay doesn't grant the lab).

**Render:** "Alice and Carol can enter the Lab during business hours.
Bob's ContractorsDay group doesn't grant the Lab Door."

### Q2. "Anyone in the server room after hours is a red flag — who could it legitimately be?"

**Plan:** start at `Server Room` zone → `protects⁻¹` → door → `grants_access_to⁻¹`
→ group, filter `active_during` ∈ {schedules that cover after-hours} → `member_of⁻¹` → person.

**Execute:** only `grp_adm` grants `d_srv`, on `sch_247`. Members: `carol`.

**Render:** "Carol — she's in the Admins group, which has 24x7 access to
the Server Room. Anyone else triggering that reader after hours is anomalous."

### Q3. "What changes if we add Bob to Admins?"

This one needs a *what-if* — a graph mutation followed by a re-query. The
LLM proposes adding `(bob, member_of, grp_adm)`, the executor diffs the
reachability set, and the renderer reports: "Bob would gain 24x7 access to
the Server Room. He currently has business-hours access to the Front Door
only."

## Why this beats plain RAG here

| Plain RAG | GraphRAG |
| --- | --- |
| Retrieves *text about* policies | Retrieves *the policy itself* |
| Confuses possibility with permission | Permission **is** a path in the graph |
| Can't answer aggregates ("how many", "who else") | Aggregates are a `count` on the result set |
| Hallucinates plausibly-shaped lies | Worst case: returns empty |
| No notion of *time* | `active_during` is a first-class edge |

## Two callers, one graph: hand-written Cypher and GraphRAG

The hand-written Cypher in `neo4j/cypher.md` and the GraphRAG agent
in `agent/agent.py` are sibling consumers of the same graph, not two
halves of a separate concept:

- **Hand-written Cypher** — closed-form questions you already know how
  to ask. "Grant or deny right now?" The traversal is fixed; the inputs
  vary. Fast, deterministic, runs in the Neo4j Browser or via a driver.
- **GraphRAG agent** — open-form questions you'd ask in English.
  "Who, what, when, what-if?" The traversal is *chosen* by an LLM from
  the schema; execution is still deterministic. Slower, runs in the
  operator console.

Same graph. Same ontology. Different *callers*. That is the whole idea:
the ontology is the contract, and every consumer — the dashboard counting
things, the auditor asking a question in English, the on-call engineer
running a Cypher one-liner — walks the same edges.

## What to build next (when you're ready)

- The next phase is the **GraphRAG vs SQL comparison**: a relational
  mirror of the same data and the same canonical questions written in
  both query languages, with an honest side-by-side analysis.
- See `PROGRESS.md` Phase 4 for the plan. The GraphRAG side already
  emits per-question transcripts to `agent/transcripts/`; the SQL side
  is what we need to build to compare against.

**Historical note:** `agent/agent.py` is the implementation of what
this file's first version called "what to build next." The two LLM calls
are strictly separated — the planner sees the schema (`ontology.md`),
the renderer sees the rows. They never overlap. See
`architecture.html` §6 for a deep dive.

Still on the wish list (out of scope for now):

- An event log (`Event` class: actor, reader, time, decision) so the
  graph carries history as well as policy. Then audit questions —
  "show me every denial at the Server Room last week" — become
  traversals too.
