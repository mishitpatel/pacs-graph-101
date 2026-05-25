# Cypher recipes for the PACS graph

This is a teaching ladder of queries you can paste into the Neo4j Browser
at <http://localhost:7474>. Run them in order — each one builds on the
last and demonstrates a different shape of question / side-condition.

> **Reading guide.** Every query has three parts: the **question** in
> English, the **Cypher** itself, and a **why** note that names the
> side-condition flavor (from the taxonomy in your notes).

---

## 0. See what's there

The bootstrap "is anything in here at all?" query. Returns the entire
graph as a sub-graph the browser can render.

```cypher
MATCH (n)-[r]->(m) RETURN n, r, m;
```

**Why:** structural existence only. No side conditions. Same picture as
our vis-network view, but rendered by Neo4j.

---

## 1. Property-and-membership questions

### 1a. List everyone

```cypher
MATCH (p:Person) RETURN p.label AS person ORDER BY person;
```

### 1b. Who is in which group?

```cypher
MATCH (p:Person)-[:member_of]->(g:AccessGroup)
RETURN p.label AS person, collect(g.label) AS groups
ORDER BY person;
```

**Why:** an aggregate (`collect`) that turns many edges per person into a
single row. The output is a *summary* of the graph, not a sub-graph.

### 1c. Which doors does each group grant?

```cypher
MATCH (g:AccessGroup)-[:grants_access_to]->(d:Door)
RETURN g.label AS group, collect(d.label) AS doors
ORDER BY group;
```

---

## 2. The closed-form reasoner, expressed in Cypher

This is the entire `canAccess` from `reasoner.js`, in one query. Compare
side-by-side — same logic, different surface.

```cypher
// Can Alice open the Lab Door right now?
WITH datetime() AS now,
     'alice'    AS person_id,
     'd_lab'    AS door_id
MATCH (p:Person {id: person_id})-[:member_of]->(g:AccessGroup)
                                -[:grants_access_to]->(d:Door {id: door_id})
WHERE EXISTS {
  MATCH (g)-[:active_during]->(s:Schedule)
  WHERE date(now).dayOfWeek IN s.weekdays
    AND time(now).hour     >= s.start_hour
    AND time(now).hour     <  s.end_hour
}
RETURN p.label AS person, g.label AS via_group, d.label AS door;
```

**Why:** **conjunctive query** — four conditions joined by AND.

- structural: `(p)-[:member_of]->(g)-[:grants_access_to]->(d)`
- existence: `EXISTS { ... }` — *some* schedule must satisfy
- property comparison: `dow IN s.weekdays`, `hour >= s.start_hour`, etc.
- context: `datetime()` injected from outside the graph

Empty result = DENY. One or more rows = GRANT (and the row tells you
*which* group justified it).

### 2a. Same question, but explicit about *which* schedule justified it

```cypher
WITH datetime() AS now, 'alice' AS person_id, 'd_lab' AS door_id
MATCH (p:Person {id: person_id})-[:member_of]->(g:AccessGroup)
                                -[:grants_access_to]->(d:Door {id: door_id}),
      (g)-[:active_during]->(s:Schedule)
WHERE date(now).dayOfWeek IN s.weekdays
  AND time(now).hour     >= s.start_hour
  AND time(now).hour     <  s.end_hour
RETURN p.label AS person, g.label AS via_group,
       s.label AS via_schedule, d.label AS door;
```

**Why:** identical decision, but the schedule node is now in the
returned shape — same role as the green path highlight in our viewer.

---

## 3. Side-condition flavors — one query each

### 3a. Temporal — who *would* have access at a specific time?

```cypher
// Who can enter the Lab Door on a Saturday at 03:00?
WITH datetime('2026-05-23T03:00:00') AS when
MATCH (p:Person)-[:member_of]->(g:AccessGroup)
                -[:grants_access_to]->(d:Door {id: 'd_lab'}),
      (g)-[:active_during]->(s:Schedule)
WHERE date(when).dayOfWeek IN s.weekdays
  AND time(when).hour     >= s.start_hour
  AND time(when).hour     <  s.end_hour
RETURN DISTINCT p.label AS person;
```

**Why:** point-in-time temporal condition. Parameterizes `when` instead
of using `datetime()`.

### 3b. Cardinality — who is in more than one group?

```cypher
MATCH (p:Person)-[:member_of]->(g:AccessGroup)
WITH p, count(g) AS group_count
WHERE group_count > 1
RETURN p.label AS person, group_count;
```

**Why:** condition on the *count* of matching edges, not on any single
edge. (Carol is in both Employees and Admins.)

### 3c. Disjunction — who can open *any* door in zone Z?

```cypher
MATCH (p:Person)-[:member_of]->(:AccessGroup)
                -[:grants_access_to]->(d:Door)-[:protects]->(z:Zone {id: 'z_lab'})
RETURN DISTINCT p.label AS person, z.label AS zone;
```

**Why:** the door is *any* door protecting the zone — the OR is implicit
in the pattern match. If the lab had two doors, this query would return
people who can open *either*.

### 3d. Negation — who is in a group but currently cannot open any door?

```cypher
MATCH (p:Person)-[:member_of]->(g:AccessGroup)
WHERE NOT EXISTS { (g)-[:grants_access_to]->(:Door) }
RETURN p.label AS person, g.label AS dead_group;
```

**Why:** explicit `NOT EXISTS`. Right now no group is "dead", so the
result is empty — *that's the point*. Negation lets you assert the
*absence* of a pattern. (Try it; it returns no rows.)

### 3e. Path query — reachability from credential to zone

```cypher
// Through any chain of edges (in any direction), what zones can each badge reach?
MATCH path = (c:Credential)-[*1..6]-(z:Zone)
RETURN DISTINCT c.label AS badge, z.label AS zone, length(path) AS hops
ORDER BY badge, hops;
```

**Why:** **variable-length path** (`[*1..6]`) — Cypher's regex-on-edges.
Note the *undirected* form (`-[*1..6]-`, no arrow). Credentials have only
incoming `holds` edges, so a directed forward walk (`-[*1..6]->`) would
return nothing. Undirected matching lets the path traverse `holds`
backward, then everything else forward. `DISTINCT` dedupes when multiple
paths reach the same `(badge, zone)`.

Don't run this on production graphs without a depth cap; small graphs
are fine.

### 3f. Structural — which controllers manage readers that control no door?

```cypher
MATCH (ctrl:Controller)<-[:managed_by]-(r:Reader)
WHERE NOT EXISTS { (r)-[:controls]->(:Door) }
RETURN ctrl.label AS controller, r.label AS orphan_reader;
```

**Why:** integrity check — finds readers wired up to a panel but not
attached to any door. Returns empty for our toy graph (good). On a real
PACS deployment this is a classic "is anything misconfigured?" query.

---

## 4. Diagnostic deny — why was access refused?

The closed-form reasoner returns a diagnostic deny reason. Cypher
expresses the same reasoning as three separate queries:

```cypher
// Step 1: Is the person in any group at all?
MATCH (p:Person {id: 'bob'})
OPTIONAL MATCH (p)-[:member_of]->(g:AccessGroup)
RETURN p.label, collect(g.label) AS groups;
```

```cypher
// Step 2: Of those groups, which (if any) grant this door?
MATCH (p:Person {id: 'bob'})-[:member_of]->(g:AccessGroup)
                            -[:grants_access_to]->(d:Door {id: 'd_lab'})
RETURN g.label AS granting_group, d.label AS door;
```

```cypher
// Step 3: For granting groups, does any active schedule cover NOW?
WITH datetime() AS now
MATCH (p:Person {id: 'bob'})-[:member_of]->(g:AccessGroup)
                            -[:grants_access_to]->(d:Door {id: 'd_lab'}),
      (g)-[:active_during]->(s:Schedule)
RETURN g.label, s.label,
       date(now).dayOfWeek IN s.weekdays
         AND time(now).hour >= s.start_hour
         AND time(now).hour <  s.end_hour                AS schedule_covers;
```

Whichever query *first* returns empty (or all-false in step 3) tells you
which condition failed.

---

## 6. Time-travel queries (Phase 2)

Policy edges now carry `valid_from` and `valid_to`. Revocation closes an
edge by setting `valid_to`; the edge stays in the graph. Auditor-style
"as of" questions become a filter, applied wherever a temporal edge
appears in the pattern.

### The "live at $when" pattern

Memorize this. It's the single addition that distinguishes a temporal
query from a snapshot query:

```
WHERE r.valid_from <= $when
  AND (r.valid_to IS NULL OR r.valid_to > $when)
```

If `valid_from` is null, the edge is treated as always-valid; if
`valid_to` is null, the edge is still in force.

### 6a. Who had membership in Employees on a given date?

```cypher
WITH date('2026-04-01') AS when
MATCH (p:Person)-[m:member_of]->(g:AccessGroup {id: 'grp_emp'})
WHERE (m.valid_from IS NULL OR m.valid_from <= when)
  AND (m.valid_to   IS NULL OR m.valid_to   >  when)
RETURN p.label AS person, m.valid_from, m.valid_to
ORDER BY person;
```

**Expected:** Alice (still a member then) and Carol. If you change `when`
to `date('2026-05-15')`, Alice drops off — her `valid_to` is `2026-05-10`.

### 6b. Who *currently* has access to the Lab Door?

The same pattern with `when = date()` (today):

```cypher
WITH date() AS when
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
                -[ga:grants_access_to]->(d:Door {id: 'd_lab'})
WHERE (m.valid_from  IS NULL OR m.valid_from  <= when)
  AND (m.valid_to    IS NULL OR m.valid_to    >  when)
  AND (ga.valid_from IS NULL OR ga.valid_from <= when)
  AND (ga.valid_to   IS NULL OR ga.valid_to   >  when)
RETURN DISTINCT p.label AS person;
```

**Expected today (2026-05-24):** Carol only. Alice's `member_of` was
closed 2026-05-10. (Compare to §6c.)

### 6c. Who had Lab Door access on 2026-04-15?

Same query, different `when`:

```cypher
WITH date('2026-04-15') AS when
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
                -[ga:grants_access_to]->(d:Door {id: 'd_lab'})
WHERE (m.valid_from  IS NULL OR m.valid_from  <= when)
  AND (m.valid_to    IS NULL OR m.valid_to    >  when)
  AND (ga.valid_from IS NULL OR ga.valid_from <= when)
  AND (ga.valid_to   IS NULL OR ga.valid_to   >  when)
RETURN DISTINCT p.label AS person;
```

**Expected:** Alice **and** Carol. This is the auditor's question — the
same data, asked of a *past* moment, gives a different answer than today.
A snapshot graph (no `valid_to`) couldn't tell you Alice was a member
then; the temporal model can.

### 6d. When did Alice lose her Employees membership?

```cypher
MATCH (p:Person {id: 'alice'})-[m:member_of]->(g:AccessGroup {id: 'grp_emp'})
WHERE m.valid_to IS NOT NULL
RETURN m.valid_from AS started, m.valid_to AS ended;
```

**Expected:** `2026-01-01 → 2026-05-10`. The closed edge is still in the
graph; that's why we can answer this question at all.

### 6e. Lab Door access timeline — who, when, how long?

```cypher
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
                -[ga:grants_access_to]->(d:Door {id: 'd_lab'})
WITH p.label AS person, m.valid_from AS started,
     coalesce(m.valid_to, date('9999-12-31')) AS ended
RETURN person, started, ended,
       duration.inDays(started, ended).days AS days
ORDER BY started;
```

**Why:** `coalesce(x, fallback)` substitutes a sentinel date for still-
active rows so durations can be computed. `duration.inDays(...)` gives
you the elapsed days. This is the building block of access-history
dashboards.

### 6f. Recent policy changes — who gained access in the last 90 days?

```cypher
WITH date() AS today
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
WHERE m.valid_from > today - duration({days: 90})
RETURN p.label AS person, g.label AS group, m.valid_from
ORDER BY m.valid_from DESC;
```

**Expected:** Carol's promotion to Admins on 2026-03-01 (within 90 days
of 2026-05-24). Pattern generalizes — change the predicate to find
*any* recent policy change.

### 6g. Revocations in a window — who lost access between two dates?

```cypher
WITH date('2026-04-01') AS start, date('2026-05-31') AS end
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
WHERE m.valid_to >= start AND m.valid_to <= end
RETURN p.label AS person, g.label AS group, m.valid_to AS revoked_on
ORDER BY revoked_on;
```

**Expected:** Bob's ContractorsDay (revoked 2026-04-15) and Alice's
Employees (revoked 2026-05-10).

### 6h. Tenure enumeration — counting separate stints

When a person re-joins after leaving, they get a *second* `member_of`
edge (not a reactivation of the old one). Each edge is one tenure.

```cypher
// All of Bob's stints in any group, with durations.
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup)
WITH g.label AS group,
     m.valid_from AS started,
     coalesce(m.valid_to, date()) AS ended,
     m.valid_to IS NULL AS still_active
RETURN group, started, ended, still_active,
       duration.inDays(started, ended).days AS days
ORDER BY started;
```

**Expected:**

| group         | started    | ended      | still_active | days |
| ------------- | ---------- | ---------- | ------------ | ---- |
| ContractorsDay| 2026-01-01 | 2026-04-15 | false        | 104  |
| ContractorsDay| 2026-04-22 | (today)    | true         | 32+  |

**Why this matters:** two rows for the same `(person, group)` pair is
not a bug — it's the record of two separate contracts. A single-edge
model would have collapsed these into one continuous tenure (incorrect)
or overwritten the first with the second (also incorrect, plus loses
history). The append-only model captures the truth.

### 6i. Gap detection — when was Bob *not* a contractor?

```cypher
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup {id: 'grp_con'})
WITH m ORDER BY m.valid_from
WITH collect(m) AS stints
UNWIND range(0, size(stints) - 2) AS i
WITH stints[i] AS prev, stints[i+1] AS next
WHERE prev.valid_to < next.valid_from
RETURN prev.valid_to AS gap_started, next.valid_from AS gap_ended,
       duration.inDays(prev.valid_to, next.valid_from).days AS gap_days;
```

**Expected:**

| gap_started | gap_ended  | gap_days |
| ----------- | ---------- | -------- |
| 2026-04-15  | 2026-04-22 | 7        |

**Why this matters:** investigators sometimes ask the *inverse* question
— not "when did Bob have access?" but "when did he NOT?" The gap query
finds those windows. Pattern works for any predicate; change `grp_con`
to ask about any other tenure.

### What this pattern enables in general

Once you have validity windows on edges, you get for free:

- **As-of queries**: any snapshot query becomes a time-travel query by
  pasting the live-at-$when filter onto every temporal edge match.
- **Change-detection queries**: filter on `valid_from > X` (gains) or
  `valid_to BETWEEN X AND Y` (losses).
- **Durations**: how long was this relationship in force?
- **Overlap analysis**: which two people had access *at the same time*
  during a window? (Useful for "who could have been in the room with
  X when the incident happened" investigations.)

The same data answers all of these — *because we didn't throw the old
edges away.*

## 7. Things to try yourself

When you've worked through everything above, here are open-ended
prompts to explore. No single right answer — the point is to exercise
the conditions.

1. List all people who have 24×7 access *somewhere*.
2. Find any "lonely" nodes (no incoming or outgoing edges).
3. For each zone, how many distinct people can reach it through at
   least one door?
4. If we added an `(:Person {id:'dave'})` with no edges, which query
   would surface them as a misconfiguration?
5. Add a property `status: 'suspended'` to one person and write a query
   that excludes them everywhere — that's the *state* condition flavor.
6. **Temporal:** Re-run §6c with `when = date('2026-02-01')`. Who had
   Lab Door access then? (Hint: only one person — Carol had joined,
   but not yet been promoted.)
7. **Temporal:** Find all (person, group, door) triples that were
   **simultaneously valid** for any 30-day window in 2026. This is the
   "overlap analysis" used in incident investigations.
