# Scenarios — auditor-style worked questions

The PACS graph now carries history (validity windows on policy edges).
This file walks through the questions a real auditor or operator would
actually ask, paired with the Cypher that answers them and the
*motivation* behind each — why a snapshot system gets these wrong, and
what the temporal model unlocks.

Read this after working through `neo4j/cypher.md` §6. Each scenario
assumes the graph has been imported with the seeded timeline from
`graph.json`.

## The seeded timeline (recap)

| Date           | Event                                                                |
| -------------- | -------------------------------------------------------------------- |
| **2026-01-01** | Alice joins → Employees + Badge-001                                  |
| **2026-01-01** | Bob (contractor) joins → ContractorsDay + Badge-002                  |
| **2026-01-15** | Carol joins → Employees + Badge-003                                  |
| **2026-03-01** | Carol promoted — added to Admins                                     |
| **2026-04-15** | Bob's contract ends — ContractorsDay membership + Badge-002 closed   |
| **2026-04-22** | Bob re-instated — *new* ContractorsDay membership + Badge-004 issued |
| **2026-05-10** | Alice leaves — Employees membership + Badge-001 closed               |
| **2026-05-24** | "Today"                                                              |

---

## Scenario 1: "Who had access to the Lab Door on 2026-04-15?"

*The auditor's classic.* A naive snapshot system would answer "Carol"
(the only person with current Lab access). The correct answer at the
time was "Alice and Carol" — Alice's membership wasn't closed until
2026-05-10.

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

→ **Alice, Carol.**

**Why this matters:** if an incident happened at the Lab Door on
2026-04-15, the investigator needs the actual contemporaneous access
list — not today's list. A snapshot graph would let Alice off the hook
or, worse, never even surface her name.

---

## Scenario 2: "When did Alice lose her access?"

```cypher
MATCH (p:Person {id: 'alice'})-[m:member_of]->(g:AccessGroup)
WHERE m.valid_to IS NOT NULL
RETURN g.label AS group, m.valid_from AS joined, m.valid_to AS left;
```

→ Alice was in Employees from **2026-01-01 to 2026-05-10**.

**Why this matters:** answering "when?" requires *retaining* the closed
edge. Destructive revocation loses this information forever. The
append-only model is what makes the question askable.

---

## Scenario 3: "Show me the Lab Door access timeline"

```cypher
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
                -[ga:grants_access_to]->(d:Door {id: 'd_lab'})
WITH p.label AS person,
     m.valid_from AS started,
     coalesce(m.valid_to, date()) AS ended
RETURN person, started, ended,
       duration.inDays(started, ended).days AS days_with_access
ORDER BY started;
```

→
| person | started    | ended      | days_with_access |
| ------ | ---------- | ---------- | ---------------- |
| Alice  | 2026-01-01 | 2026-05-10 | 129              |
| Carol  | 2026-01-15 | (today)    | growing          |

**Why this matters:** "how long?" and "since when?" are reporting-team
questions. Compliance reports often need *durations*, not just current
state.

---

## Scenario 4: "Who has gained access in the last 90 days?"

```cypher
WITH date() AS today
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
WHERE m.valid_from > today - duration({days: 90})
RETURN p.label AS person, g.label AS group, m.valid_from AS since
ORDER BY since DESC;
```

→ Carol's promotion to Admins on **2026-03-01**.

**Why this matters:** weekly "what changed?" review. A snapshot system
can't answer this without joining against a separate audit log; with
validity windows, the graph *is* the audit log.

---

## Scenario 5: "Who lost access this quarter?"

```cypher
WITH date('2026-04-01') AS start, date('2026-06-30') AS end
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
WHERE m.valid_to >= start AND m.valid_to <= end
RETURN p.label AS person, g.label AS group, m.valid_to AS revoked_on
ORDER BY revoked_on;
```

→ Bob (ContractorsDay, 2026-04-15) and Alice (Employees, 2026-05-10).

**Why this matters:** the dual of Scenario 4. Quarterly compliance
reports always ask for both gains *and* losses.

---

## Scenario 6: "Did the policy change recently for the Server Room?"

The Server Room is sensitive — any policy change to it is suspicious.

```cypher
WITH date() AS today
MATCH (g:AccessGroup)-[ga:grants_access_to]->(d:Door {id: 'd_srv'})
WHERE ga.valid_from > today - duration({days: 180})
   OR (ga.valid_to IS NOT NULL AND ga.valid_to > today - duration({days: 180}))
RETURN g.label AS group, ga.valid_from, ga.valid_to;
```

→ Empty in our seeded data — the Server Room policy hasn't changed
since 2026-01-01. **Empty is good news** — the assertion "no recent
policy changes" holds.

**Why this matters:** the same query, run regularly, becomes an alert.
Add a single new grant and it stops being empty — somebody knows.

---

## Scenario 7: "Co-presence — who could have been in the Lab with Alice on 2026-03-15?"

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

→ Carol.

**Why this matters:** incident investigations often ask "who *could*
have been there?" — the answer is the set of people whose access was
valid at that moment. Different from "who actually entered?" (which
needs swipe logs — a future addition).

---

## Scenario 8: "Stale grants — has anyone held a group membership for over 100 days?"

```cypher
WITH date() AS today
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
WHERE m.valid_to IS NULL                           // still active
  AND duration.inDays(m.valid_from, today).days > 100
RETURN p.label AS person, g.label AS group,
       duration.inDays(m.valid_from, today).days AS days_held
ORDER BY days_held DESC;
```

→ Carol/Employees, Carol/Admins (depending on date).

**Why this matters:** privileged-access reviews. Many compliance regimes
require periodic re-attestation of long-held access. This query flags
who needs to be re-attested.

---

## Scenario 9: "Bob came back — how do we audit a re-instatement?"

Bob's contract ended on 2026-04-15. A week later, on 2026-04-22, he was
re-hired. The graph records this as a *second* `member_of` edge — same
`(bob, grp_con)` pair, fresh window — and a *new* Credential node
(badge004), because credentials are re-issued, not reactivated.

### 9a. Was Bob a member on 2026-04-18 (during the gap)?

```cypher
WITH date('2026-04-18') AS when
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup)
WHERE (m.valid_from IS NULL OR m.valid_from <= when)
  AND (m.valid_to   IS NULL OR m.valid_to   >  when)
RETURN g.label AS group;
```

→ **Empty.** During the 7-day gap, Bob had no active membership.

### 9b. Was Bob a member on 2026-05-01 (after re-instatement)?

```cypher
WITH date('2026-05-01') AS when
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup)
WHERE (m.valid_from IS NULL OR m.valid_from <= when)
  AND (m.valid_to   IS NULL OR m.valid_to   >  when)
RETURN g.label AS group;
```

→ **ContractorsDay** (the new tenure).

### 9c. How many separate contracts has Bob had?

```cypher
MATCH (p:Person {id: 'bob'})-[m:member_of]->(g:AccessGroup)
RETURN g.label AS group, count(m) AS stints;
```

→ ContractorsDay: 2.

### 9d. Which credentials has Bob ever held?

```cypher
MATCH (p:Person {id: 'bob'})-[h:holds]->(c:Credential)
RETURN c.label AS credential, h.valid_from, h.valid_to
ORDER BY h.valid_from;
```

→
| credential | valid_from | valid_to    |
| ---------- | ---------- | ----------- |
| Badge-002  | 2026-01-01 | 2026-04-15  |
| Badge-004  | 2026-04-22 | (still)     |

**Why this matters:** in legacy PACS where revocation deletes the
relationship, Bob's re-hiring looks like "Bob has always been a
contractor" — you can't tell there was a gap, you can't tell which
badge was active when, and the original credential's history is gone.
In the temporal model, all four questions above are one-liners. The
data was always there; we just didn't throw it away.

### 9e. List everyone's re-instatements

```cypher
MATCH (p:Person)-[m:member_of]->(g:AccessGroup)
WITH p, g, count(m) AS stints
WHERE stints > 1
RETURN p.label AS person, g.label AS group, stints
ORDER BY stints DESC;
```

→ Bob / ContractorsDay / 2. (No one else has been re-instated in our
seed data.)

**Why this matters:** HR or security policy might require extra scrutiny
for re-hires (background re-check, fresh attestation). A one-line query
surfaces who qualifies.

---

## What the temporal model unlocks (the meta-point)

Every scenario above is **the same model + the same query language +
one extra filter** (the live-at-`$when` pattern). No new tables, no
audit log, no separate history database. The graph absorbs history.

That's the bitemporal payoff in miniature. Now imagine the same for a
medical record, a banking account, or a regulatory filing. Same idea,
same syntax, vastly different problem domains — all unlocked by *not
throwing away the closed edges*.
