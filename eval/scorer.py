"""Row-set scorer for the eval harness.

Goal: decide whether a backend's rows match the question's `expected_rows`,
even when column names differ between Cypher (often `p.label`) and SQL
(often `label`).

Approach: each row is collapsed to a multiset of its non-null values.
Two rows match iff their value multisets are equal. The overall result
matches iff the multiset of row-multisets is equal between expected
and actual.

This is permissive about column naming but still catches:
  - missing rows (different cardinality)
  - extra rows (same)
  - swapped/wrong values (different multisets)

Edge case: if a row contains the same value in two columns, we lose the
column-pairing information. None of our seeded questions hit that case;
when they do we can switch to a per-row column-matching algorithm.
"""

from __future__ import annotations


def _row_values(row: dict) -> tuple:
    """Sort the non-null values of a row into a canonical tuple.

    Dates from Neo4j come back as `neo4j.time.Date`; from SQLite they
    come back as strings. Coerce everything to str for comparison.
    """
    return tuple(sorted(str(v) for v in row.values() if v is not None))


def _normalize(rows: list) -> list[tuple]:
    """Convert each row (dict or list) into a sorted-tuple value-set."""
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append(_row_values(r))
        elif isinstance(r, (list, tuple)):
            out.append(tuple(sorted(str(v) for v in r if v is not None)))
        else:
            out.append((str(r),))
    return sorted(out)


def score_rows(expected: list, actual: list) -> dict:
    """Return a score dict comparing expected vs actual rows.

        {
          "match":          True | False,
          "expected_count": int,
          "actual_count":   int,
          "missing":        list of expected-rows-not-in-actual,
          "extra":          list of actual-rows-not-in-expected,
        }

    Missing/extra rows are returned in their normalised (sorted-tuple)
    form so they're easy to print in a report.
    """
    e = _normalize(expected)
    a = _normalize(actual)

    # Multiset diff
    e_counts: dict[tuple, int] = {}
    a_counts: dict[tuple, int] = {}
    for t in e: e_counts[t] = e_counts.get(t, 0) + 1
    for t in a: a_counts[t] = a_counts.get(t, 0) + 1

    missing = []
    extra   = []
    for t, n in e_counts.items():
        diff = n - a_counts.get(t, 0)
        if diff > 0:
            missing.extend([t] * diff)
    for t, n in a_counts.items():
        diff = n - e_counts.get(t, 0)
        if diff > 0:
            extra.extend([t] * diff)

    return {
        "match":          not missing and not extra,
        "expected_count": len(e),
        "actual_count":   len(a),
        "missing":        missing,
        "extra":          extra,
    }
