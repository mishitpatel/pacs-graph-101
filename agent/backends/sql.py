"""SQL backend — plans and executes against the SQLite mirror at phase4/pacs.db.

Read-only is enforced two ways: the regex below rejects write verbs before
execution, and the SQLite connection is opened with `mode=ro` via the URI
scheme so the driver itself refuses writes. Belt and braces, same pattern
as the Cypher backend.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


SQL_BLOCK = re.compile(r"```(?:sql|sqlite|sqlite\s*)?\n?(.*?)```", flags=re.DOTALL)

FORBIDDEN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE|ATTACH|DETACH|"
    r"VACUUM|REINDEX|PRAGMA|TRUNCATE)\b",
    flags=re.IGNORECASE,
)


class SQLBackend:
    name     = "sql"
    language = "sql"
    file_ext = "sql"

    def __init__(self, repo_root: Path):
        import prompts   # agent/ is on sys.path; this resolves to agent/prompts.py
        self.planner_system = prompts.build_sql_planner_system(
            prompts.read_sql_schema(repo_root)
        )

        db_path = repo_root / "phase4" / "pacs.db"
        if not db_path.exists():
            raise FileNotFoundError(
                f"SQLite mirror not found at {db_path}. "
                f"Build it first with: python3 phase4/import_sql.py"
            )

        # SQLite supports a true read-only connection via URI mode=ro.
        uri = f"file:{db_path}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    def extract(self, text: str) -> str:
        matches = SQL_BLOCK.findall(text)
        return (matches[0] if matches else text).strip()

    def safety(self, query: str) -> str | None:
        if (m := FORBIDDEN.search(query)):
            return f"forbidden write keyword: {m.group(0)!r}"
        return None

    def run(self, query: str) -> list[dict]:
        cursor = self.conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self.conn.close()
