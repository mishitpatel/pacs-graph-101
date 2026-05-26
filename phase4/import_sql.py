"""
Import graph.json into a SQLite mirror.

Same source of truth (graph.json), two destinations: Neo4j (graph) and
SQLite (relational). The Phase 4 comparison uses both side-by-side to
show where Cypher and SQL each shine on the same temporal questions.

The mapping is mechanical:

    JSON node {class, id, label}           ->  one row in the matching entity table
    JSON edge {predicate, from, to, ...}   ->  one row in the matching junction table

Validity windows pass through unchanged as DATE columns. Schedule nodes
get their coverage rules (weekdays / start_hour / end_hour) populated
from the same SCHEDULE_COVERAGE dict the Neo4j importer uses.

Run:
    python3 phase4/import_sql.py     # creates phase4/pacs.db
"""

import json
import pathlib
import sqlite3
import sys

REPO_ROOT   = pathlib.Path(__file__).resolve().parent.parent
GRAPH_FILE  = REPO_ROOT / "graph.json"
SCHEMA_FILE = pathlib.Path(__file__).resolve().parent / "schema.sql"
DB_FILE     = pathlib.Path(__file__).resolve().parent / "pacs.db"

# JSON class names -> SQL entity table names
CLASS_TO_TABLE = {
    "Person":      "person",
    "Credential":  "credential",
    "AccessGroup": "access_group",
    "Schedule":    "schedule",
    "Reader":      "reader",
    "Door":        "door",
    "Zone":        "zone",
    "Controller":  "controller",
}

# JSON predicates -> (table, from_col, to_col, is_temporal)
PREDICATE_TO_TABLE = {
    "holds":            ("holds",         "person_id", "credential_id", True),
    "member_of":        ("membership",    "person_id", "group_id",      True),
    "grants_access_to": ("grants_access", "group_id",  "door_id",       True),
    "active_during":    ("active_during", "group_id",  "schedule_id",   True),
    "controls":         ("controls",      "reader_id", "door_id",       False),
    "protects":         ("protects",      "door_id",   "zone_id",       False),
    "managed_by":       ("managed_by",    "reader_id", "controller_id", False),
}

# Same coverage rules the Neo4j importer uses. Stored on Schedule rows.
SCHEDULE_COVERAGE = {
    "sch_247": {"weekdays": [1, 2, 3, 4, 5, 6, 7], "start_hour": 0, "end_hour": 24},
    "sch_biz": {"weekdays": [1, 2, 3, 4, 5],       "start_hour": 9, "end_hour": 17},
}


def main() -> int:
    data = json.loads(GRAPH_FILE.read_text())

    # Fresh DB every run — schema.sql DROPs tables, but a missing file is cleaner.
    if DB_FILE.exists():
        DB_FILE.unlink()

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")           # enforce FKs
    conn.executescript(SCHEMA_FILE.read_text())

    # ---- Entities ----
    by_class: dict[str, list[dict]] = {}
    for n in data["nodes"]:
        by_class.setdefault(n["class"], []).append(n)

    for cls, rows in by_class.items():
        table = CLASS_TO_TABLE[cls]
        if cls == "Schedule":
            # Special-case: enrich with coverage columns.
            for n in rows:
                cov = SCHEDULE_COVERAGE.get(n["id"])
                if cov is None:
                    # Unknown schedule — store defaults; queries will need to handle.
                    cov = {"weekdays": [], "start_hour": 0, "end_hour": 0}
                conn.execute(
                    f"INSERT INTO {table} "
                    f"  (id, label, weekdays, start_hour, end_hour) "
                    f"VALUES (?, ?, ?, ?, ?)",
                    (n["id"], n["label"], json.dumps(cov["weekdays"]),
                     cov["start_hour"], cov["end_hour"])
                )
        else:
            conn.executemany(
                f"INSERT INTO {table} (id, label) VALUES (?, ?)",
                [(n["id"], n["label"]) for n in rows]
            )

    # ---- Edges ----
    for e in data["edges"]:
        table, from_col, to_col, temporal = PREDICATE_TO_TABLE[e["predicate"]]
        if temporal:
            conn.execute(
                f"INSERT INTO {table} ({from_col}, {to_col}, valid_from, valid_to) "
                f"VALUES (?, ?, ?, ?)",
                (e["from"], e["to"], e.get("valid_from"), e.get("valid_to"))
            )
        else:
            conn.execute(
                f"INSERT INTO {table} ({from_col}, {to_col}) VALUES (?, ?)",
                (e["from"], e["to"])
            )

    conn.commit()

    # ---- Sanity check ----
    print("Entity counts:")
    for cls, table in sorted(CLASS_TO_TABLE.items(), key=lambda kv: kv[1]):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:14s} {count:>3d}")

    print("Junction counts:")
    for pred, (table, _f, _t, temporal) in sorted(
        PREDICATE_TO_TABLE.items(), key=lambda kv: kv[1][0]
    ):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        suffix = ""
        if temporal:
            closed = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE valid_to IS NOT NULL"
            ).fetchone()[0]
            suffix = f"  (closed: {closed})"
        print(f"  {table:18s} {count:>3d}{suffix}")

    conn.close()
    print(f"\nDone. Database: {DB_FILE.relative_to(REPO_ROOT)}")
    print(f"Try: sqlite3 {DB_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
