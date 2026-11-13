"""Retrieval evaluation: Hit@1 and Evidence Hit@3 with per-question traces."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _gold_sources(question: dict[str, Any]) -> set[str]:
    values = question.get("gold_source_ids")
    if values is None:
        values = [question.get("gold_source_id")]
    return {str(value) for value in values if value not in (None, "")}


def _gold_pages(question: dict[str, Any]) -> set[int]:
    values = question.get("gold_pages")
    if values is None:
        values = [question.get("gold_page")]
    return {int(value) for value in values if value is not None}


def _is_evidence_hit(question: dict[str, Any], hit: dict[str, Any]) -> tuple[bool, str]:
    sources = _gold_sources(question)
    pages = _gold_pages(question)
    source_ok = not sources or str(hit.get("source", "")) in sources
    page_ok = not pages or int(hit.get("page", -1)) in pages
    quote = _normalize(question.get("evidence_quote", ""))
    chunk_text = _normalize(hit.get("text", ""))

    # Exact normalized quote is the automatic evidence check. A hit on the
    # right page but without the full quote is not silently counted as a hit:
    # it remains available for manual confirmation as an equivalent passage.
    quote_ok = bool(quote) and quote in chunk_text
    if source_ok and page_ok and quote_ok:
        return True, "gold_source_page_and_quote_match"
    if source_ok and page_ok:
        return False, "gold_source_page_match_quote_needs_manual_review"
    return False, "gold_source_or_page_mismatch"


def evaluate_query(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    strategy: str | None = None,
) -> dict[str, Any]:
    """Evaluate one question; do not infer coherence or human grounding labels."""
    top3 = hits[:3]
    checks = [_is_evidence_hit(question, hit) for hit in top3]
    hit1 = bool(checks and checks[0][0])
    hit3 = any(ok for ok, _ in checks)

    return {
        "question_id": question.get("id"),
        "split": question.get("split", "unknown"),
        "strategy": strategy,
        "question": question.get("question", ""),
        "expected_answer": question.get("expected_answer", ""),
        "gold": {
            "source_ids": sorted(_gold_sources(question)),
            "pages": sorted(_gold_pages(question)),
            "evidence_quote": question.get("evidence_quote", ""),
        },
        "filter": question.get("filter"),
        "top3": [
            {
                **hit,
                "rank": rank,
                "automatic_evidence_hit": checks[rank - 1][0],
                "evidence_check": checks[rank - 1][1],
            }
            for rank, hit in enumerate(top3, start=1)
        ],
        "hit_at_1": int(hit1),
        "evidence_hit_at_3": int(hit3),
        "manual_review_required": any(
            reason == "gold_source_page_match_quote_needs_manual_review"
            for _, reason in checks
        ),
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate retrieval metrics separately by strategy and dev/test split."""
    output: dict[str, Any] = {}
    strategies = sorted({str(t.get("strategy", "unknown")) for t in traces})
    for strategy in strategies:
        output[strategy] = {}
        for split in ("dev", "test"):
            rows = [
                t
                for t in traces
                if str(t.get("strategy", "unknown")) == strategy
                and str(t.get("split", "unknown")).lower() == split
            ]
            total = len(rows)
            h1 = sum(int(row.get("hit_at_1", 0)) for row in rows)
            h3 = sum(int(row.get("evidence_hit_at_3", 0)) for row in rows)
            output[strategy][split] = {
                "questions": total,
                "hit_at_1": {
                    "count": h1,
                    "total": total,
                    "rate": h1 / total if total else None,
                },
                "evidence_hit_at_3": {
                    "count": h3,
                    "total": total,
                    "rate": h3 / total if total else None,
                },
                "manual_review_required": sum(
                    bool(row.get("manual_review_required")) for row in rows
                ),
            }
    return output
