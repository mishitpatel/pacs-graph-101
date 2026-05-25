"""
Import graph.json into Neo4j.

Mapping from our JSON to Neo4j:

    {id, label, class}        ->  (:Class {id, label})           ← node label = the class
    {from, to, predicate}     ->  (a)-[:predicate]->(b)          ← relationship type = the predicate

We also enrich Schedule nodes with explicit coverage properties so Cypher
queries can evaluate the temporal check natively (instead of pushing the
rule into application code, the way reasoner.js does):

    sch_247:  weekdays=[1..7], start_hour=0,  end_hour=24
    sch_biz:  weekdays=[1..5], start_hour=9,  end_hour=17

Note on weekdays: Neo4j (and ISO 8601) uses 1=Monday..7=Sunday.
JavaScript's Date.getDay() uses 0=Sunday..6=Saturday. We use the Neo4j
convention here so Cypher can do `WHERE dow IN s.weekdays` directly.

Run:
    pip install -r requirements.txt
    python import.py            # uses ../graph.json by default
"""

import json
import pathlib
import sys

from neo4j import GraphDatabase

URI       = "bolt://localhost:7687"
AUTH      = ("neo4j", "pacsgraph101")
GRAPH_FILE = pathlib.Path(__file__).parent.parent / "graph.json"

# Per-schedule coverage rules — the meaning lives here (as data),
# not in JS code. Cypher queries read these properties.
SCHEDULE_COVERAGE = {
    "sch_247": {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hour": 0, "end_hour": 24},
    "sch_biz": {"weekdays": [1, 2, 3, 4, 5],       "start_hour": 9, "end_hour": 17},
}


def main() -> int:
    data = json.loads(GRAPH_FILE.read_text())

    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        driver.verify_connectivity()
        with driver.session() as s:
            # Idempotent import — running this twice is safe.
            # First wipe everything so we don't accumulate stale state on re-runs.
            s.run("MATCH (n) DETACH DELETE n")

            # Nodes: one MERGE per class so the node's :Label matches its class.
            # We can't parameterize a label in Cypher, so we group by class and
            # interpolate the label name into the query string per group.
            by_class: dict[str, list[dict]] = {}
            for n in data["nodes"]:
                by_class.setdefault(n["class"], []).append(n)
            for cls, rows in by_class.items():
                s.run(
                    f"UNWIND $rows AS row "
                    f"MERGE (n:{cls} {{id: row.id}}) "
                    f"SET   n.label = row.label",
                    rows=rows,
                )

            # Schedule enrichment — attach coverage properties.
            for sid, props in SCHEDULE_COVERAGE.items():
                s.run(
                    "MATCH (s:Schedule {id: $id}) "
                    "SET s.weekdays   = $weekdays, "
                    "    s.start_hour = $start_hour, "
                    "    s.end_hour   = $end_hour",
                    id=sid, **props,
                )

            # Edges: relationship *type* can't be parameterized either, so we
            # group by predicate and emit one query per type.
            #
            # Edges with `valid_from` / `valid_to` get those copied through as
            # Neo4j `date` properties so Cypher can filter by `r.valid_from <= $when`
            # natively. Missing fields => null => "always valid" / "still active."
            by_pred: dict[str, list[dict]] = {}
            for e in data["edges"]:
                by_pred.setdefault(e["predicate"], []).append({
                    "from":       e["from"],
                    "to":         e["to"],
                    "valid_from": e.get("valid_from"),
                    "valid_to":   e.get("valid_to"),
                })
            for pred, rows in by_pred.items():
                # CREATE (not MERGE) because the same (from, to, predicate) tuple
                # can legitimately occur multiple times — a person who leaves and
                # returns gets a *second* member_of edge with a fresh validity
                # window, not a reactivation of the old one. MERGE would collapse
                # the two into one and lose the gap. Safe with CREATE because
                # we DETACH DELETE everything at the top of this import.
                s.run(
                    f"UNWIND $rows AS row "
                    f"MATCH (a {{id: row.from}}), (b {{id: row.to}}) "
                    f"CREATE (a)-[r:{pred}]->(b) "
                    f"SET r.valid_from = CASE WHEN row.valid_from IS NOT NULL "
                    f"                       THEN date(row.valid_from) ELSE NULL END, "
                    f"    r.valid_to   = CASE WHEN row.valid_to   IS NOT NULL "
                    f"                       THEN date(row.valid_to)   ELSE NULL END",
                    rows=rows,
                )

            # Sanity check — print what we created.
            result = s.run(
                "MATCH (n) WITH labels(n)[0] AS cls, count(*) AS c "
                "RETURN cls, c ORDER BY cls"
            )
            print("Nodes by class:")
            for r in result:
                print(f"  {r['cls']:14s} {r['c']:>3d}")

            result = s.run(
                "MATCH ()-[r]->() WITH type(r) AS t, count(*) AS c "
                "RETURN t, c ORDER BY t"
            )
            print("Edges by predicate:")
            for r in result:
                print(f"  {r['t']:18s} {r['c']:>3d}")

            # Confirm temporal properties landed.
            result = s.run(
                "MATCH ()-[r]->() WHERE r.valid_from IS NOT NULL "
                "RETURN count(r) AS with_valid_from, "
                "       count(CASE WHEN r.valid_to IS NOT NULL THEN 1 END) AS closed"
            )
            row = result.single()
            print(f"Edges with valid_from: {row['with_valid_from']}  "
                  f"(of those, closed = valid_to set: {row['closed']})")

    print("\nDone. Open http://localhost:7474 (neo4j / pacsgraph101) to explore.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
