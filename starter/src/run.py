"""Run the Day 06 retrieval benchmark for strategies A, B and C.

The pipeline uses the real embedding adapter and PostgreSQL + pgvector.
No mock-vector or in-memory retrieval fallback is allowed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .chunking import by_legal_structure, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pdf
from .store import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "data" / "corpus" / "13_2023_ND-CP.pdf"
DEFAULT_BENCHMARK = ROOT / "data" / "benchmark" / "questions.json"
DEFAULT_RESULTS = ROOT / "results"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _infer_page_metadata(pages) -> None:
    """Attach legal hierarchy from source text, carrying headings across pages."""
    chapter = article = article_title = None
    for page in pages:
        # Keep the hierarchy at the beginning of the page separately from the
        # hierarchy at its end; a page may contain several Articles.
        page.metadata.update({
            "chapter_at_start": chapter,
            "article_at_start": article,
            "article_title_at_start": article_title,
        })
        for raw in page.text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line:
                continue
            m = re.match(r"^CHƯƠNG\s+([IVXLCDM]+|\d+)\b\s*(.*)$", line, re.I)
            if m:
                chapter = m.group(1).upper()
                article = article_title = None
                continue
            m = re.match(r"^Điều\s+(\d+[a-zA-Z]?)\s*[.:]?\s*(.*)$", line, re.I)
            if m:
                article, article_title = m.group(1), (m.group(2).strip() or None)
        page.metadata.update({
            "chapter": chapter,
            "article": article,
            "article_title": article_title,
            "document_type": "decree",
            "metadata_provenance": "parsed_from_source_text",
        })


def _chunk_pages(pages, strategy: str, size: int, overlap: int):
    chunks = []
    for page in pages:
        if strategy == "A":
            chunks.extend(fixed_size(page, size=size, overlap=overlap))
        else:
            chunks.extend(by_legal_structure(page, max_size=size))
    return chunks


def _embed_and_upsert(store, embedder, strategy: str, chunks, batch_size: int = 64):
    """Embed a strategy once, persist it, and return vectors for safe reuse."""
    started = time.perf_counter()
    store.create_schema()
    all_vectors = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        all_vectors.extend(embedder.embed_documents([chunk.text for chunk in batch]))
    if len(all_vectors) != len(chunks):
        raise RuntimeError(f"{strategy}: embedding count does not match chunk count")
    store.upsert(strategy, chunks, all_vectors)
    return time.perf_counter() - started, all_vectors


def _upsert_existing_vectors(store, strategy: str, chunks, vectors):
    """Persist an identical chunk set under another strategy without API calls."""
    if len(chunks) != len(vectors):
        raise RuntimeError(
            f"{strategy}: cannot reuse vectors; chunk/vector counts differ."
        )
    store.upsert(strategy, chunks, vectors)


def _write_json(path: Path, payload: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _manual_audit_rows(questions, strategy_traces):
    """Prepare human audit sheets; deliberately leave human scores blank."""
    rows = []
    test_questions = [q for q in questions if q.get("split") == "test"]
    for strategy, traces in strategy_traces.items():
        by_id = {trace["question_id"]: trace for trace in traces}
        for question in test_questions:
            trace = by_id.get(question["id"], {})
            for hit in trace.get("top3", []):
                rows.append({
                    "audit_type": "metadata_and_grounding",
                    "strategy": strategy,
                    "question_id": question["id"],
                    "question": question["question"],
                    "rank": hit.get("rank"),
                    "chunk_id": hit.get("chunk_id"),
                    "source": hit.get("source"),
                    "page": hit.get("page"),
                    "score": hit.get("score"),
                    "metadata": json.dumps(hit.get("metadata", {}), ensure_ascii=False),
                    "chunk_text": hit.get("text", ""),
                    "expected_evidence": question.get("evidence_quote", ""),
                    "metadata_correct_0_or_1": "",
                    "grounding_score_0_to_2": "",
                    "review_reason": "",
                })
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run retrieval benchmark A/B/C")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--ocr", action=argparse.BooleanOptionalAction, default=True,
                        help="OCR pages with little/no embedded text (default: enabled)")
    parser.add_argument("--ocr-lang", default="vie+eng",
                        help="Tesseract language data, normally vie+eng")
    parser.add_argument("--ocr-dpi", type=int, default=300)
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--rebuild", action="store_true",
                        help="Re-embed and replace stored chunks for all strategies")
    args = parser.parse_args()

    if not args.corpus.is_file():
        raise FileNotFoundError(
            f"Corpus not found: {args.corpus}. Put the PDF at data/corpus/13_2023_ND-CP.pdf"
        )
    if not args.benchmark.is_file():
        raise FileNotFoundError(
            f"Benchmark not found: {args.benchmark}. Put questions.json at data/benchmark/"
        )
    if args.top_k != 3:
        raise ValueError("This assignment requires Top-3; keep --top-k 3.")
    benchmark = _load_json(args.benchmark)
    questions = benchmark.get("questions", [])
    if len(questions) < 20:
        raise ValueError(f"Expected at least 20 benchmark questions; found {len(questions)}.")
    split_counts = Counter(q.get("split") for q in questions)
    if split_counts.get("dev", 0) < 15 or split_counts.get("test", 0) < 5:
        raise ValueError(f"Need at least 15 dev and 5 test questions; found {dict(split_counts)}")

    pages = load_pdf(
        args.corpus,
        document_id=benchmark.get("source", {}).get("source_id", "corpus"),
        source=benchmark.get("source", {}).get("source_id", args.corpus.name),
        ocr=args.ocr, ocr_lang=args.ocr_lang, ocr_dpi=args.ocr_dpi,
    )
    content_pages = [page for page in pages if page.text.strip()]
    if not content_pages:
        raise ValueError("PDF extraction/OCR returned no text. Check Tesseract and Vietnamese language data.")
    _infer_page_metadata(pages)

    # The embedding provider/model must be configured in .env. It must remain
    # identical for A, B and C.
    embedder = MyEmbedder()
    import os
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing from starter/.env")
    store = PgVectorStore(database_url=database_url, dimensions=embedder.dimensions)
    store.create_schema()

    print(
        f"Embedding provider={getattr(embedder, 'provider', 'unknown')} | "
        f"model={embedder.model_name} | dimensions={embedder.dimensions}"
    )
    print(f"Corpus pages: {len(pages)} | non-empty pages: {len(content_pages)}")

    all_traces = []
    metrics = {}
    # A and B need their own vectors. C is defined as B + metadata filtering,
    # so C must reuse B's vectors rather than calling the embedding API again.
    strategy_vectors = {}
    query_vectors = {}
    experiment = {
        "corpus": str(args.corpus),
        "benchmark": str(args.benchmark),
        "embedding_provider": getattr(embedder, "provider", "local_or_custom"),
        "embedding_model": embedder.model_name,
        "embedding_dimensions": embedder.dimensions,
        "vector_store": "PostgreSQL + pgvector",
        "similarity": "cosine",
        "chunk_size_characters": args.chunk_size,
        "overlap_characters": args.overlap,
        "top_k": 3,
        "content_pages": len(content_pages),
        "strategies": {},
        "notes": [
            "C reuses B chunks and B document vectors; no duplicate document embedding API calls.",
            "Question embeddings are cached and reused across A/B/C.",
            "C applies question.filter only when present; no-filter questions run unfiltered.",
            "Legal metadata (chapter/article) is parsed from source text, never copied from question gold metadata.",
            "Automatic evidence hit requires exact normalized evidence_quote within a chunk on a gold source/page.",
            "Equivalent evidence requires manual confirmation; coherence and grounding are not inferred automatically."
        ],
    }

    for strategy in ("A", "B", "C"):
        chunk_strategy = "A" if strategy == "A" else "B"
        chunks = _chunk_pages(pages, chunk_strategy, args.chunk_size, args.overlap)

        if strategy == "C":
            # C has exactly B's chunks and vectors; only retrieval filtering differs.
            b_chunks = _chunk_pages(pages, "B", args.chunk_size, args.overlap)
            if len(b_chunks) != len(chunks) or any(
                left.chunk_id != right.chunk_id or left.text != right.text
                for left, right in zip(b_chunks, chunks)
            ):
                raise RuntimeError("Strategy C chunks differ from B; vector reuse is unsafe.")
            elapsed_start = time.perf_counter()
            _upsert_existing_vectors(store, "C", chunks, strategy_vectors["B"])
            elapsed = time.perf_counter() - elapsed_start
            strategy_vectors["C"] = strategy_vectors["B"]
        else:
            elapsed, vectors = _embed_and_upsert(store, embedder, strategy, chunks)
            strategy_vectors[strategy] = vectors

        experiment["strategies"][strategy] = {
            "chunking": "fixed_size" if strategy == "A" else "legal_article_clause_aware",
            "chunks": len(chunks),
            "mean_chunk_characters": (
                sum(len(chunk.text) for chunk in chunks) / len(chunks) if chunks else 0
            ),
            "ingest_seconds_including_embedding": round(elapsed, 3),
        }

        strategy_traces = []
        for question in questions:
            question_text = question["question"]
            if question_text not in query_vectors:
                query_vectors[question_text] = embedder.embed_query(question_text)
            query_vector = query_vectors[question_text]
            filters = question.get("filter") if strategy == "C" else None
            hits = store.search(
                strategy=strategy,
                query_vector=query_vector,
                top_k=3,
                filters=filters,
            )
            trace = evaluate_query(question, hits, strategy=strategy)
            trace["applied_filter"] = filters
            trace["filter_applied"] = bool(filters) and strategy == "C"
            strategy_traces.append(trace)
            all_traces.append(trace)
        _write_json(args.results / f"traces_{strategy}.json", strategy_traces)
        metrics[strategy] = summarize(strategy_traces)[strategy]

    _write_json(args.results / "metrics.json", metrics)
    _write_json(args.results / "experiment.json", experiment)

    audit_rows = []
    for strategy in ("A", "B", "C"):
        traces = [t for t in all_traces if t["strategy"] == strategy]
        audit_rows.extend(_manual_audit_rows(questions, {strategy: traces}))
    _write_csv(args.results / "manual_audit.csv", audit_rows)

    # Coherence audit: use the same first 10 non-empty physical pages for all
    # strategies, and leave the human score/reason blank.
    coherence_rows = []
    sampled_pages = [page for page in pages if page.text.strip()][:10]
    for strategy in ("A", "B", "C"):
        chunk_strategy = "A" if strategy == "A" else "B"
        strategy_chunks = _chunk_pages(sampled_pages, chunk_strategy,
                                       args.chunk_size, args.overlap)
        for page in sampled_pages:
            page_chunks = [chunk for chunk in strategy_chunks if chunk.page == page.page]
            for chunk in page_chunks:
                coherence_rows.append({
                    "strategy": strategy,
                    "source": page.source,
                    "page": page.page,
                    "chunk_id": chunk.chunk_id,
                    "chunk_text": chunk.text,
                    "coherence_score_0_to_2": "",
                    "review_reason": "",
                })
    _write_csv(args.results / "coherence_audit.csv", coherence_rows)

    # One combined machine-readable trace file for convenient submission.
    _write_json(args.results / "traces_all.json", all_traces)

    print("\n=== RETRIEVAL METRICS ===")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\nSaved traces, metrics and manual audit files to: {args.results.resolve()}")
    print("Manual audit is intentionally unscored; fill in the CSV after reviewing chunks.")


if __name__ == "__main__":
    main()
