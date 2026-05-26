# Phase 4 — GraphRAG vs SQL: the honest comparison

The same PACS data lives in two places now:

- **Neo4j** (graph DB) — populated by `neo4j/import.py`
- **SQLite** (relational DB) — populated by `phase4/import_sql.py`

Same `graph.json` is the source of truth for both. The schemas are
faithful mirrors of each other — every JSON edge predicate becomes
either a Cypher relationship type or a SQL junction table, and policy
edges carry the same `valid_from` / `valid_to` window in both.

This document walks the **same canonical questions** through both
languages side by side and gives an honest verdict on each. The goal
is *not* to make GraphRAG win — the goal is to show **which question
shapes naturally suit which language**.

Spoiler: the result is more balanced than you'd guess. SQL is competitive
or cleaner for ~60% of these questions. Cypher's wins are concentrated
in two specific shapes — variable-length paths and pattern-first queries
where the structure of the question maps directly onto the structure of
the answer.

## The two schemas, side by side

### Cypher (graph)

```
(Person)─[:member_of    {valid_from, valid_to}]→(AccessGroup)─┐
(Person)─[:holds        {valid_from, valid_to}]→(Credential)   │
                                                               ├→[:grants_access_to {valid_from, valid_to}]→(Door)
                                                               └→[:active_during    {valid_from, valid_to}]→(Schedule)
(Door)─[:protects]→(Zone)
(Reader)─[:controls]→(Door)
(Reader)─[:managed_by]→(Controller)
```

### SQL (relational)

```sql
person      (id, label)
credential  (id, label)
access_group(id, label)
schedule    (id, label, weekdays, start_hour, end_hour)
door        (id, label)
zone        (id, label)
reader      (id, label)
controller  (id, label)

holds         (id, person_id, credential_id, valid_from, valid_to)   -- temporal
membership    (id, person_id, group_id,      valid_from, valid_to)   -- temporal
grants_access (id, group_id,  door_id,       valid_from, valid_to)   -- temporal
active_during (id, group_id,  schedule_id,   valid_from, valid_to)   -- temporal
controls      (reader_id, door_id)                                   -- static
protects      (door_id, zone_id)                                     -- static
managed_by    (reader_id, controller_id)                             -- static
```

The temporal junction tables use a synthetic `INTEGER PRIMARY KEY` so
the same `(person_id, group_id)` pair can appear in multiple rows with
non-overlapping windows. That's how re-instatement is modeled — same
shape as multiple Cypher edges between the same nodes.

---

## Q1 — Who currently has access to the Lab Door?

```cypher
WITH date() AS when
MATCH (p:Person)-[m:member_of]->(:AccessGroup)
                -[ga:grants_access_to]->(:Door {id: 'd_lab'})
WHERE (m.valid_from  IS NULL OR m.valid_from  <= when)
  AND (m.valid_to    IS NULL OR m.valid_to    >  when)
  AND (ga.valid_from IS NULL OR ga.valid_from <= when)
  AND (ga.valid_to   IS NULL OR ga.valid_to   >  when)
RETURN DISTINCT p.label AS person;
```

```sql
SELECT DISTINCT p.label AS person
FROM person p
JOIN membership m    ON m.person_id = p.id
JOIN grants_access ga ON ga.group_id = m.group_id
WHERE ga.door_id = 'd_lab'
  AND (m.valid_from  IS NULL OR m.valid_from  <= date('now'))
  AND (m.valid_to    IS NULL OR m.valid_to    >  date('now'))
  AND (ga.valid_from IS NULL OR ga.valid_from <= date('now'))
  AND (ga.valid_to   IS NULL OR ga.valid_to   >  date('now'));
```

**Both return:** Carol.

**Verdict — tied.** Cypher's pattern arrows are slightly more compact;
SQL's explicit JOINs are slightly more verbose. The structural shape
of the query is identical: two relationships joined and filtered by
the same live-at-`when` predicate. If you already know one language,
the other reads naturally.

---

## Q2 — Who had access to the Lab Door on 2026-04-15?

Same query as Q1, with one literal changed: `date()` → `date('2026-04-15')`
in Cypher; `date('now')` → `date('2026-04-15')` in SQL.

**Both return:** Alice, Carol.

**Verdict — tied, and this is the auditor's payoff.** The fact that
*one literal change* answers a fundamentally different question — the
past tense one that a snapshot graph couldn't answer at all — is the
whole win of the temporal model. Both Cypher and SQL benefit equally
from it. Neither language is better here; what matters is that the
schema preserved the closed edges in the first place.

---

## Q3 — When did Alice lose her Employees membership?

```cypher
MATCH (p:Person {id: 'alice'})-[m:member_of]->(g:AccessGroup {id: 'grp_emp'})
WHERE m.valid_to IS NOT NULL
RETURN m.valid_from AS started, m.valid_to AS ended;
```

```sql
SELECT valid_from AS started, valid_to AS ended
FROM membership
WHERE person_id = 'alice' AND group_id = 'grp_emp'
  AND valid_to IS NOT NULL;
```

**Both return:** 2026-01-01 → 2026-05-10.

**Verdict — SQL is cleaner.** This is a *single-table* question — read
properties off one junction row. SQL doesn't need any joins; Cypher
still has to write a `MATCH` pattern. The graph model added a structural
overhead the relational model didn't pay. Point for SQL.

---

## Q4 — How many separate stints has Bob had in ContractorsDay?

```cypher
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup {id: 'grp_con'})
RETURN count(m) AS stints;
```

```sql
SELECT COUNT(*) AS stints
FROM membership
WHERE person_id = 'bob' AND group_id = 'grp_con';
```

**Both return:** 2.

**Verdict — slight edge to SQL.** Both are short. SQL's `COUNT(*)`
reads more naturally than Cypher's `count(m)` (which feels like it's
counting *edges that match a pattern*, not rows). Either way it's
trivial; the difference is aesthetic.

---

## Q5 — When was Bob *not* a contractor? (gap detection)

This is the most interesting question in the set.

```cypher
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup {id: 'grp_con'})
WITH m ORDER BY m.valid_from
WITH collect(m) AS stints
UNWIND range(0, size(stints) - 2) AS i
WITH stints[i] AS prev, stints[i+1] AS next
WHERE prev.valid_to < next.valid_from
RETURN prev.valid_to AS gap_started,
       next.valid_from AS gap_ended,
       duration.inDays(prev.valid_to, next.valid_from).days AS gap_days;
```

```sql
WITH ordered AS (
  SELECT valid_from, valid_to,
         LEAD(valid_from) OVER (ORDER BY valid_from) AS next_from
  FROM membership
  WHERE person_id = 'bob' AND group_id = 'grp_con'
)
SELECT valid_to AS gap_started,
       next_from AS gap_ended,
       CAST(julianday(next_from) - julianday(valid_to) AS INTEGER) AS gap_days
FROM ordered
WHERE valid_to IS NOT NULL
  AND next_from IS NOT NULL
  AND valid_to < next_from;
```

**Both return:** 2026-04-15 → 2026-04-22, 7 days.

**Verdict — clear SQL win.** This is the SQL killer feature: **window
functions**. `LEAD(...) OVER (ORDER BY ...)` is *exactly* the right
abstraction for "compare each row to its neighbor in a sorted sequence."
Cypher has no equivalent; you have to `collect` the rows into a list,
`UNWIND range(0, n-2) AS i`, then index `stints[i]` / `stints[i+1]`
manually. The intent gets lost in the mechanics.

Cypher 5 added some list-comprehension idioms that help, but for
sequential-comparison queries SQL is decisively better.

---

## Q6 — Who could have been with Alice in the Lab on 2026-03-15?

```cypher
WITH date('2026-03-15') AS when, 'alice' AS target
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
                -[ga:grants_access_to]->(:Door {id: 'd_lab'})
WHERE p.id <> target
  AND (m.valid_from  IS NULL OR m.valid_from  <= when)
  AND (m.valid_to    IS NULL OR m.valid_to    >  when)
  AND (ga.valid_from IS NULL OR ga.valid_from <= when)
  AND (ga.valid_to   IS NULL OR ga.valid_to   >  when)
RETURN DISTINCT p.label AS could_have_been_with_alice;
```

```sql
SELECT DISTINCT p.label AS could_have_been_with_alice
FROM person p
JOIN membership m    ON m.person_id = p.id
JOIN grants_access ga ON ga.group_id = m.group_id
WHERE p.id <> 'alice' AND ga.door_id = 'd_lab'
  AND (m.valid_from  IS NULL OR m.valid_from  <= date('2026-03-15'))
  AND (m.valid_to    IS NULL OR m.valid_to    >  date('2026-03-15'))
  AND (ga.valid_from IS NULL OR ga.valid_from <= date('2026-03-15'))
  AND (ga.valid_to   IS NULL OR ga.valid_to   >  date('2026-03-15'));
```

**Both return:** Carol.

**Verdict — tied.** Same shape as Q1+Q2 with one extra filter
(`p.id <> 'alice'`). Both languages handle it identically.

---

## Q7 — Effective access window per (person, door)

The hardest "intersection-of-two-temporal-edges" query. The effective
access window is `max(start) → min(end)` over the two relevant edges.

```cypher
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
                -[ga:grants_access_to]->(d:Door)
WITH p, g, d,
     CASE
       WHEN m.valid_from  IS NULL THEN ga.valid_from
       WHEN ga.valid_from IS NULL THEN m.valid_from
       WHEN m.valid_from >= ga.valid_from THEN m.valid_from
       ELSE ga.valid_from
     END AS access_from,
     CASE
       WHEN m.valid_to  IS NULL THEN ga.valid_to
       WHEN ga.valid_to IS NULL THEN m.valid_to
       WHEN m.valid_to <= ga.valid_to THEN m.valid_to
       ELSE ga.valid_to
     END AS access_to
RETURN p.label AS person, d.label AS door, g.label AS via_group,
       access_from, access_to
ORDER BY person, access_from;
```

```sql
SELECT p.label AS person, d.label AS door, g.label AS via_group,
  CASE
    WHEN m.valid_from  IS NULL THEN ga.valid_from
    WHEN ga.valid_from IS NULL THEN m.valid_from
    WHEN m.valid_from >= ga.valid_from THEN m.valid_from
    ELSE ga.valid_from
  END AS access_from,
  CASE
    WHEN m.valid_to  IS NULL THEN ga.valid_to
    WHEN ga.valid_to IS NULL THEN m.valid_to
    WHEN m.valid_to <= ga.valid_to THEN m.valid_to
    ELSE ga.valid_to
  END AS access_to
FROM person p
JOIN membership m    ON m.person_id = p.id
JOIN access_group g  ON g.id = m.group_id
JOIN grants_access ga ON ga.group_id = g.id
JOIN door d          ON d.id = ga.door_id
ORDER BY person, access_from;
```

**Both return** seven rows (Bob has two rows for Front Door from his
two stints).

**Verdict — tied, both ugly.** Two-argument `max`/`min` over nullable
dates is awkward in both languages. SQLite has a row-wise `MAX(a, b)`
function that's non-standard; standard SQL would need yet more boilerplate.
Cypher has the same problem. The `CASE` is the same in both — copy-paste
between languages literally works for this expression. Honest score:
neither language solved this elegantly.

---

## Q8 — Total membership days per person across all groups

```cypher
MATCH (p:Person)-[m:member_of]->(:AccessGroup)
WITH p,
     m.valid_from AS started,
     coalesce(m.valid_to, date()) AS ended
RETURN p.label AS person,
       sum(duration.inDays(started, ended).days) AS total_days
ORDER BY total_days DESC;
```

```sql
SELECT p.label AS person,
       CAST(SUM(julianday(COALESCE(m.valid_to, date('now')))
                - julianday(m.valid_from)) AS INTEGER) AS total_days
FROM person p
JOIN membership m ON m.person_id = p.id
GROUP BY p.label
ORDER BY total_days DESC;
```

**Both return** a sorted list (Carol > Alice > Bob in days).

**Verdict — slight edge to SQL.** Aggregation is SQL's home territory —
`GROUP BY` is well-known and `SUM(...)` reads naturally. Cypher's
implicit grouping by un-aggregated columns (the `p` column) works the
same way under the hood, but the syntax is less explicit. Cypher's
`duration.inDays(a, b).days` is a tiny bit cleaner than SQL's
`julianday(b) - julianday(a)` workaround — but the explicit `GROUP BY`
in SQL wins overall.

---

## Q9 — From a credential, what zones can it reach? (variable-length)

```cypher
MATCH path = (c:Credential)-[*1..6]-(z:Zone)
RETURN DISTINCT c.label AS badge, z.label AS zone, length(path) AS hops
ORDER BY badge, hops;
```

```sql
-- Recursive CTE — SQL's only path-traversal hammer.
-- The "any edge in either direction" pattern requires UNIONing every
-- edge type explicitly, in both directions. Painful even for our toy
-- graph; far worse at scale.
WITH RECURSIVE
  edges (from_id, to_id) AS (
    SELECT person_id,    credential_id FROM holds
    UNION ALL SELECT credential_id, person_id    FROM holds
    UNION ALL SELECT person_id,    group_id      FROM membership
    UNION ALL SELECT group_id,     person_id     FROM membership
    UNION ALL SELECT group_id,     door_id       FROM grants_access
    UNION ALL SELECT door_id,      group_id      FROM grants_access
    UNION ALL SELECT group_id,     schedule_id   FROM active_during
    UNION ALL SELECT schedule_id,  group_id      FROM active_during
    UNION ALL SELECT reader_id,    door_id       FROM controls
    UNION ALL SELECT door_id,      reader_id     FROM controls
    UNION ALL SELECT door_id,      zone_id       FROM protects
    UNION ALL SELECT zone_id,      door_id       FROM protects
    UNION ALL SELECT reader_id,    controller_id FROM managed_by
    UNION ALL SELECT controller_id, reader_id    FROM managed_by
  ),
  walk (start_id, current_id, hops, path) AS (
    SELECT id, id, 0, ',' || id || ','
    FROM credential
    UNION ALL
    SELECT w.start_id, e.to_id, w.hops + 1, w.path || e.to_id || ','
    FROM walk w
    JOIN edges e ON e.from_id = w.current_id
    WHERE w.hops < 6
      AND INSTR(w.path, ',' || e.to_id || ',') = 0
  )
SELECT DISTINCT c.label AS badge, z.label AS zone, w.hops
FROM walk w
JOIN credential c ON c.id = w.start_id
JOIN zone z       ON z.id = w.current_id
ORDER BY badge, hops;
```

**Both return** the same reachability rows.

**Verdict — Cypher wins, decisively.** Variable-length path matching
(`[*1..6]`) is *native* to graph DBs and *foreign* to relational ones.
SQL's recursive CTE works, but it requires manually enumerating every
edge type in both directions and tracking visited nodes to prevent
cycles. The complexity grows with each new predicate; Cypher's pattern
doesn't change at all when you add a new edge type — the wildcard
still matches.

At graph scale, the difference is dramatic: graph DB engines index for
this; relational engines do nested-loop joins over the UNION-ALL view.
This is the *one* category where graph DBs have a fundamental advantage
that no amount of SQL cleverness closes.

---

## Tally

| Question | Shape | Winner | Why |
| --- | --- | --- | --- |
| Q1 | Snapshot lookup | **tied** | Same conjunctive filter in both |
| Q2 | Past as-of | **tied** | One literal change in either language |
| Q3 | Single-row property | **SQL** | No structural join needed |
| Q4 | Count rows | **SQL** (slight) | `COUNT(*)` natural; `count(m)` reads as edge-count |
| Q5 | Gap detection | **SQL** | `LEAD()` window function vs Cypher's `collect`/`UNWIND` dance |
| Q6 | Co-presence audit | **tied** | Same shape as Q1 with an extra filter |
| Q7 | Two-edge intersection | **tied** (both ugly) | Two-arg `min`/`max` over nullables is awkward in both |
| Q8 | Aggregate over duration | **SQL** (slight) | `GROUP BY` more explicit; both have date-math quirks |
| Q9 | Variable-length path | **Cypher** | Native `[*1..N]` vs recursive CTE with UNION-ALL enumeration |

## The honest read

If you only counted points, **SQL wins more individual queries than
Cypher does.** That's not the story most GraphRAG demos tell you, and
it's worth sitting with for a moment.

But the points-tally is misleading on two axes:

### Where SQL's wins compound

- **Aggregates, window functions, set-oriented thinking.** Decades of
  query-optimizer work has made these *brilliant* in SQL. If your
  questions are mostly "summarize", "compare-adjacent", "trend over
  time" — SQL is the right tool.
- **Single-table or shallow-join questions.** When the answer lives in
  one or two tables, SQL adds zero structural overhead. Cypher's pattern
  syntax is *built for* multi-hop queries; it's wasted on small ones.
- **Tooling and ecosystem.** SQL has 50 years of optimizers, replication
  schemes, monitoring tools, ORMs, and engineers who know it. Cypher
  is a comparative newcomer. For production operations, this matters.

### Where Cypher's wins compound

- **Variable-length paths.** As soon as a question is "any chain of
  edges," "shortest path," or "reachable within N hops," SQL falls off
  a cliff. Recursive CTEs are linear-cost-of-complexity; Cypher's
  variable-length patterns are constant-cost-of-complexity.
- **Schema evolution.** Adding a new predicate in Neo4j is a `CREATE`
  with no DDL. In SQL it's a new junction table, new indexes, possibly
  migrations to existing tables. Graph schemas absorb new edge kinds
  more gracefully.
- **The structure of the question matches the structure of the answer.**
  When you write `(a)-[:knows]->(b)-[:works_at]->(c)` in Cypher, you're
  drawing a picture of the result. In SQL the same idea is several JOINs
  whose visual layout doesn't reflect the data shape. For pedagogy and
  for ad-hoc exploration, that visual fidelity matters.

### The thing nobody admits

For temporal access control specifically — the use case this whole
project is built around — **SQL is genuinely competitive**, especially
if you set up the validity windows well from day one. The reason most
legacy PACS systems fail at audit is *not* that they're built on SQL;
it's that they use destructive `DELETE` semantics on a relational
schema that *could* have been append-only. The temporal model is the
key insight; the choice of query language is a smaller secondary one.

Where the project's earlier "GraphRAG vs RAG" framing would have been
unfair (chunked-doc RAG is a strawman, as you correctly called out),
the **GraphRAG vs SQL** framing is fair — and it puts both tools in
their honest place.

### When to pick which

| If your application is mostly... | Pick |
| --- | --- |
| Audit, reporting, point-in-time snapshots, aggregates | SQL (relational) |
| Fraud, recommendation, social-graph traversals, "find the path between X and Y" | Graph DB |
| Both (most real systems) | Both — same data, two indexes |

Real PACS at scale would benefit from a graph-style read replica
specifically for *reachability* and *what-if* queries, while keeping
SQL for the audit log and reporting layer. The choice isn't either-or;
it's about layering.

## What this tells us about the GraphRAG agent

Going back to `agent/agent.py`:

The agent currently plans **Cypher**. For the questions it tends to
get (operator-style, often with re-instatement or co-presence dimensions),
that's a reasonable default. But there's no reason in principle the agent
couldn't plan **SQL** instead — same two-call pattern, different system
prompt, different executor.

A useful future extension would be a `--sql` flag on the agent that
plans SQL against this SQLite mirror. Same questions, two query
languages, automatically logged transcripts. That would turn this
hand-written comparison into a continuously-measured one. (Not built
yet — see `PROGRESS.md`.)

---

## Running the SQL side yourself

```bash
# 1. Build / rebuild the SQLite mirror
python3 phase4/import_sql.py

# 2. Open a SQL shell
sqlite3 phase4/pacs.db
.headers on
.mode column

# 3. Paste any of the SQL queries above
```

The `pacs.db` file is gitignored. Re-run `import_sql.py` any time
`graph.json` changes.
