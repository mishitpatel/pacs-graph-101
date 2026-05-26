"""Cypher backend — plans and executes against Neo4j."""

from __future__ import annotations

import os
import re
from pathlib import Path

from neo4j import GraphDatabase


CYPHER_BLOCK = re.compile(r"```(?:cypher|cypher\s*)?\n?(.*?)```", flags=re.DOTALL)

# `\b ... \b` so `CREATE` doesn't match inside `createdAt`.
# `CALL\s+(?!{)` so `CALL { ... }` (a read-only sub-query) stays allowed.
FORBIDDEN = re.compile(
    r"\b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|"
    r"CALL\s+(?!{)|FOREACH)\b",
    flags=re.IGNORECASE,
)


class CypherBackend:
    name     = "cypher"
    language = "cypher"
    file_ext = "cypher"

    def __init__(self, repo_root: Path):
        import prompts   # agent/ is on sys.path; this resolves to agent/prompts.py
        self.planner_system = prompts.build_cypher_planner_system(
            prompts.read_ontology(repo_root)
        )

        uri      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
        user     = os.environ.get("NEO4J_USER",     "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "pacsgraph101")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.driver.verify_connectivity()

    def extract(self, text: str) -> str:
        matches = CYPHER_BLOCK.findall(text)
        return (matches[0] if matches else text).strip()

    def safety(self, query: str) -> str | None:
        if (m := FORBIDDEN.search(query)):
            return f"forbidden write keyword: {m.group(0)!r}"
        return None

    def run(self, query: str) -> list[dict]:
        # Neo4j enforces read-only at the session level. Belt and braces
        # alongside the regex above.
        with self.driver.session(default_access_mode="r") as session:
            return [record.data() for record in session.run(query)]

    def close(self) -> None:
        self.driver.close()
