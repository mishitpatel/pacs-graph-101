# PACS Ontology (the schema)

A Physical Access Control System answers one question millions of times a day:
**"Should this credential, at this reader, right now, open this door?"**

Everything in the ontology exists to answer that question.

## Classes (kinds of things)

| Class          | What it is                                              |
| -------------- | ------------------------------------------------------- |
| `Person`       | A human — employee, contractor, visitor                 |
| `Credential`   | A badge, PIN, mobile key, or biometric template         |
| `Reader`       | The device at a door that reads credentials             |
| `Door`         | A physical opening that can be locked/unlocked          |
| `Zone`         | A space behind one or more doors (Lobby, Lab, etc.)     |
| `AccessGroup`  | A named bundle of permissions (e.g. "Employees")        |
| `Schedule`     | A time window (24x7, BusinessHours)                     |
| `Controller`   | The panel that makes grant/deny decisions for readers   |

## Predicates (kinds of relationships)

| Predicate           | Domain → Range              | Meaning                                  |
| ------------------- | --------------------------- | ---------------------------------------- |
| `holds`             | Person → Credential         | This badge belongs to this person        |
| `member_of`         | Person → AccessGroup        | This person is in this group             |
| `grants_access_to`  | AccessGroup → Door          | This group can open this door            |
| `active_during`     | AccessGroup → Schedule      | This group's access only applies then    |
| `controls`          | Reader → Door               | This reader unlocks this door            |
| `protects`          | Door → Zone                 | Passing this door enters this zone       |
| `managed_by`        | Reader → Controller         | This panel owns this reader              |

## Modeling notes (the "why")

- **AccessGroup is the join table.** Without it, you'd connect every Person directly
  to every Door — quadratic edges. Groups make access manageable.
- **Schedule attaches to the group, not the person.** Same person can be 24x7 via
  one group and 9-5 via another.
- **Zone vs Door**: a door is the boundary; a zone is the space. You grant access
  to a door (the action), but you reason about who's in a zone (the state).
- **Reader vs Door**: a door can have two readers (in/out), or one, or none
  (push-bar exit). Keeping them separate matters.

## Temporal model: edges have validity windows

A snapshot of "who is in what group right now" cannot answer the auditor's
question: *"Who had access to the Server Room at 2 AM on March 14th?"*
If revocation is destructive (`DELETE` the edge), the prior state is gone —
you'd have to replay every audit-log entry from system inception to
reconstruct it. Most legacy PACS get this wrong.

The fix: every **policy edge** carries an optional validity window.

| Property      | Meaning                                                 |
| ------------- | ------------------------------------------------------- |
| `valid_from`  | When the edge became true in the real world             |
| `valid_to`    | When it stopped being true (`null` / absent = still active) |

Revocation is then **append-only**: you *close* an edge by setting
`valid_to = now`, never delete. The edge stays in the graph, queryable
forever. The auditor's "as of" question becomes a filter:

```
WHERE r.valid_from <= $when AND (r.valid_to IS NULL OR r.valid_to > $when)
```

### Which predicates are temporal?

| Predicate           | Temporal? | Why                                              |
| ------------------- | --------- | ------------------------------------------------ |
| `holds`             | ✓         | Badges are issued and revoked                    |
| `member_of`         | ✓         | Group memberships change                         |
| `grants_access_to`  | ✓         | Policy is edited                                 |
| `active_during`     | ✓         | A group's schedule can be reassigned             |
| `controls`          | ✗ static  | Hardware wiring — changes slowly, audit-rarely   |
| `protects`          | ✗ static  | Building layout — same                           |
| `managed_by`        | ✗ static  | Panel topology — same                            |

Static edges *can* be made temporal later; we keep them snapshot-only for
now because nothing in the auditor use case asks "when was this reader
re-cabled?"

### Validity time vs transaction time

What we implement is **validity time only** — when the fact was true *in
the real world*. There's a second axis, **transaction time** — when we
*recorded* the fact in the system. Both together make a system **fully
bitemporal**, capable of answering: *"What did we believe was true on
March 14th, as of our records on March 25th?"* That distinction matters
in finance, insurance, and legal compliance. For PACS audit, validity
time alone is sufficient — and far simpler. The second axis is a future
extension, not a current concern.

### Re-instatement: multiple edges between the same nodes

A validity window represents *one continuous interval of truth*. If a
person leaves and returns, you get **two `member_of` edges between the
same Person and AccessGroup**, with non-overlapping windows:

```
bob —member_of→ grp_con  [2026-01-01 → 2026-04-15]   ← closed
bob —member_of→ grp_con  [2026-04-22 → null      ]   ← open (re-instatement)
```

This is correct and expected. The alternative — reopening the old edge
by clearing its `valid_to` — would erase the gap and produce wrong
answers for "was Bob a member on April 18?"

Credentials are reissued, not transferred: a returning person gets a
**new Credential node** (and a new `holds` edge), not a reactivation of
the old credential. The old credential's history stays in the graph
closed forever.

### Consequence for consumers

- **Neo4j Cypher queries** read `valid_from` / `valid_to` directly. The
  "as of" pattern goes in every audit-style query. Be aware that the
  same `(person, group)` pair may have multiple `member_of` edges;
  Cypher's `MATCH` will iterate all of them, so an "as-of" query
  naturally selects the right one without extra logic.
- **The GraphRAG agent** (`agent/agent.py`) sees this same ontology in
  its planner system prompt. The planner is taught the live-at-`$when`
  filter explicitly in `agent/prompts.py::PLANNER_OUTRO`. When the user
  asks a temporal question, the planner composes the filter into its
  Cypher; for snapshot questions ("right now") it uses `date()` as the
  pivot. The renderer never sees the schema — only the rows.
