"""Backends — one module per query language.

Each backend exposes a class with the same shape:

    class Backend:
        name: str          # 'cypher' or 'sql'
        language: str      # markdown fence language ('cypher' / 'sql')
        file_ext: str      # transcript file extension ('cypher' / 'sql')
        planner_system: str  # the planner's system prompt (cache-stable)
        def extract(self, text: str) -> str: ...   # pull query from LLM response
        def safety(self, query: str) -> str | None: ...  # None if ok, else reason
        def run(self, query: str) -> list[dict]: ...     # execute against the store
        def close(self) -> None: ...                     # tear down driver/conn

The REPL loop is mode-agnostic — it takes a list of backends and runs
each question through every backend in turn.
"""

from .cypher import CypherBackend
from .sql    import SQLBackend

__all__ = ["CypherBackend", "SQLBackend"]
