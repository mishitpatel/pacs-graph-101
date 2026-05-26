"""Markdown report generator for the eval harness."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _money(tokens: int, price_per_million: float) -> float:
    return tokens / 1_000_000 * price_per_million


# Opus 4.7 pricing (per 1M tokens) — from claude-api skill cache 2026-04-15.
PRICE_INPUT_REGULAR        = 5.00
PRICE_INPUT_CACHE_READ     = 0.50    # 0.10×
PRICE_INPUT_CACHE_CREATION = 6.25    # 1.25×
PRICE_OUTPUT               = 25.00


def _cost_for_usage(u: dict | None) -> float:
    if not u:
        return 0.0
    return (
        _money(u["input_tokens"],                PRICE_INPUT_REGULAR)
        + _money(u["cache_creation_input_tokens"], PRICE_INPUT_CACHE_CREATION)
        + _money(u["cache_read_input_tokens"],     PRICE_INPUT_CACHE_READ)
        + _money(u["output_tokens"],               PRICE_OUTPUT)
    )


def _aggregate_usage(per_call_usages: list[dict]) -> dict:
    """Sum a list of usage dicts into a single usage dict."""
    keys = ("input_tokens", "output_tokens",
            "cache_creation_input_tokens", "cache_read_input_tokens")
    out = {k: 0 for k in keys}
    for u in per_call_usages:
        if u is None:
            continue
        for k in keys:
            out[k] += u.get(k, 0) or 0
    return out


def write_report(records: list[dict], out_dir: Path, model: str) -> Path:
    """Write report.md to out_dir. Returns the file path.

    Each record is one (question, backend) pair with:
      - question_id, category, difficulty, question
      - backend (cypher/sql)
      - status (ok / planner_failed / rejected / execution_failed / renderer_failed)
      - match (bool) — was the row-set correct?
      - row_count (int)
      - missing, extra (lists)
      - planner_usage, renderer_usage (dicts)
      - latency_ms: {planner, executor, renderer}
      - query (the emitted query, for cross-referencing)
      - answer (the final prose answer)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.md"

    backends = sorted({r["backend"] for r in records})
    questions = []
    seen_qs = set()
    for r in records:
        if r["question_id"] not in seen_qs:
            questions.append({"id": r["question_id"], "category": r["category"],
                              "difficulty": r["difficulty"], "question": r["question"]})
            seen_qs.add(r["question_id"])

    # Index records by (question, backend) for easy lookup
    idx = {(r["question_id"], r["backend"]): r for r in records}

    lines: list[str] = []

    # --- Header ----------------------------------------------------------------
    lines.append(f"# PACS GraphRAG eval report")
    lines.append("")
    lines.append(f"- **When:**  {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- **Model:** `{model}`")
    lines.append(f"- **Backends:** " + ", ".join(f"`{b}`" for b in backends))
    lines.append(f"- **Questions:** {len(questions)}")
    lines.append("")

    # --- Aggregate per backend -------------------------------------------------
    lines.append("## Aggregate")
    lines.append("")
    headers = ["Backend", "Correct", "Wrong", "Errored", "Cache reads", "Total cost"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for b in backends:
        recs = [idx[(q["id"], b)] for q in questions if (q["id"], b) in idx]
        correct = sum(1 for r in recs if r.get("status") == "ok" and r.get("match"))
        wrong   = sum(1 for r in recs if r.get("status") == "ok" and not r.get("match"))
        errored = sum(1 for r in recs if r.get("status") != "ok")
        total_usage = _aggregate_usage(
            [r.get("planner_usage") for r in recs]
            + [r.get("renderer_usage") for r in recs]
        )
        cost = sum(_cost_for_usage(r.get("planner_usage")) for r in recs) \
             + sum(_cost_for_usage(r.get("renderer_usage")) for r in recs)
        lines.append(
            f"| `{b}` | {correct}/{len(recs)} | {wrong} | {errored} | "
            f"{total_usage['cache_read_input_tokens']:,} | ${cost:.4f} |"
        )
    lines.append("")

    # --- Per-category breakdown ------------------------------------------------
    categories = sorted({q["category"] for q in questions})
    if len(categories) > 1:
        lines.append("## Per-category correctness")
        lines.append("")
        headers = ["Category"] + [f"`{b}`" for b in backends]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join("---" for _ in headers) + "|")
        for cat in categories:
            qs_in_cat = [q for q in questions if q["category"] == cat]
            row = [cat]
            for b in backends:
                recs = [idx[(q["id"], b)] for q in qs_in_cat if (q["id"], b) in idx]
                correct = sum(1 for r in recs if r.get("status") == "ok" and r.get("match"))
                row.append(f"{correct}/{len(recs)}")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # --- Per-question detail ---------------------------------------------------
    lines.append("## Per-question detail")
    lines.append("")
    for q in questions:
        lines.append(f"### {q['id']} — {q['question']}")
        lines.append("")
        lines.append(f"*category: `{q['category']}`, difficulty: `{q['difficulty']}`*")
        lines.append("")
        for b in backends:
            r = idx.get((q["id"], b))
            if r is None:
                continue
            status = r.get("status", "?")
            match  = r.get("match")
            verdict = (
                "✓ correct" if status == "ok" and match else
                "✗ wrong" if status == "ok" and not match else
                f"⚠ {status}"
            )
            latency = r.get("latency_ms", {})
            l_p = latency.get("planner")  or 0
            l_e = latency.get("executor") or 0
            l_r = latency.get("renderer") or 0

            lines.append(f"**`{b}`** — {verdict}  ({r.get('row_count', 0)} rows, "
                         f"plan {l_p:.0f}ms · exec {l_e:.1f}ms · render {l_r:.0f}ms)")
            lines.append("")

            if r.get("query"):
                lines.append(f"```{b}")
                lines.append(r["query"])
                lines.append("```")
                lines.append("")

            if status == "ok":
                if r.get("missing") or r.get("extra"):
                    if r.get("missing"):
                        lines.append(f"- **missing:** {r['missing']}")
                    if r.get("extra"):
                        lines.append(f"- **extra:** {r['extra']}")
                    lines.append("")
                lines.append(f"> {r.get('answer', '').strip()}")
                lines.append("")
            else:
                lines.append(f"- error path: `{status}`")
                lines.append("")

        lines.append("---")
        lines.append("")

    # --- Pricing footnote ------------------------------------------------------
    lines.append("## Cost model")
    lines.append("")
    lines.append(
        f"Opus 4.7 pricing (per 1M tokens): "
        f"regular input ${PRICE_INPUT_REGULAR:.2f}, "
        f"cache-read ${PRICE_INPUT_CACHE_READ:.2f}, "
        f"cache-write ${PRICE_INPUT_CACHE_CREATION:.2f}, "
        f"output ${PRICE_OUTPUT:.2f}."
    )
    lines.append("")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path
