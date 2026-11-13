"""Chunking strategies for Nghị định 13/2023/NĐ-CP."""
from __future__ import annotations
import re
from .models import Chunk, DocumentPage

ARTICLE_RE = re.compile(r"^\s*Điều\s+(\d+[a-zA-Z]?)\s*[.:]?\s*(.*)$", re.I)
CHAPTER_RE = re.compile(r"^\s*Chương\s+([IVXLCDM]+|\d+)\b\s*(.*)$", re.I)
CLAUSE_RE = re.compile(r"^\s*(\d{1,2})\.\s+(.+)$")
POINT_RE = re.compile(r"^\s*([a-zđ])\)\s+(.+)$", re.I)

def _make(page, text, strategy, index, start, end, extra=None):
    metadata = dict(page.metadata or {})
    metadata.update({
        "chunking_strategy": strategy, "chunk_index": index,
        "start_char": start, "end_char": end, "char_count": len(text)
    })
    if extra:
        metadata.update(extra)
    return Chunk(
        chunk_id=f"{page.document_id}-p{page.page}-{strategy}-{index:04d}",
        document_id=page.document_id, source=page.source, page=page.page,
        text=text, metadata=metadata
    )

def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Require size > 0 and 0 <= overlap < size")
    text = page.text or ""
    chunks, start, index = [], 0, 0
    step = size - overlap
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end].strip()
        if piece:
            left = len(text[start:end]) - len(text[start:end].lstrip())
            right = len(text[start:end]) - len(text[start:end].rstrip())
            chunks.append(_make(page, piece, "fixed_size", index,
                                start + left, end - right))
            index += 1
        if end == len(text): break
        start += step
    return chunks

def _split_text(text: str, max_size: int):
    result, start = [], 0
    while start < len(text):
        end = min(start + max_size, len(text))
        if end < len(text):
            boundary = max(text.rfind(" ", start + 1, end),
                           text.rfind("\n", start + 1, end))
            if boundary > start: end = boundary
        result.append(text[start:end].strip())
        start = end
        while start < len(text) and text[start].isspace(): start += 1
    return [x for x in result if x]

def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Paragraph strategy retained as baseline."""
    if max_size <= 0: raise ValueError("max_size must be positive")
    text = page.text or ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, pending, index, cursor = [], "", 0, 0
    for para in paragraphs:
        if len(para) > max_size:
            if pending:
                chunks.append(_make(page,pending,"paragraph",index,0,len(pending))); index += 1; pending=""
            for piece in _split_text(para,max_size):
                chunks.append(_make(page,piece,"paragraph",index,cursor,cursor+len(piece))); index += 1
            cursor += len(para)
            continue
        candidate = para if not pending else pending + "\n\n" + para
        if pending and len(candidate) > max_size:
            chunks.append(_make(page,pending,"paragraph",index,0,len(pending))); index += 1
            pending = para
        else: pending = candidate
        cursor += len(para)
    if pending: chunks.append(_make(page,pending,"paragraph",index,0,len(text)))
    return chunks

def by_legal_structure(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Keep Điều/Khoản/Điểm together where possible; split only oversized units."""
    if max_size <= 0: raise ValueError("max_size must be positive")
    text = page.text or ""
    if not text.strip(): return []
    lines = text.splitlines()
    units, current = [], []
    state = {"chapter": page.metadata.get("chapter_at_start", page.metadata.get("chapter")),
             "article": page.metadata.get("article_at_start", page.metadata.get("article")),
             "article_title": page.metadata.get("article_title_at_start", page.metadata.get("article_title")) ,
             "clause": None, "point": None}
    def flush():
        nonlocal current
        if current:
            units.append(("\n".join(current).strip(), dict(state)))
            current = []
    for line in lines:
        stripped = line.strip()
        if not stripped: 
            if current: current.append("")
            continue
        cm = CHAPTER_RE.match(stripped)
        am = ARTICLE_RE.match(stripped)
        if cm:
            flush(); state["chapter"] = cm.group(1).upper()
            state["article"] = None; state["article_title"] = None
            state["clause"] = None; state["point"] = None
        elif am:
            flush(); state["article"] = am.group(1)
            state["article_title"] = am.group(2).strip() or None
            state["clause"] = None; state["point"] = None
        else:
            cl = CLAUSE_RE.match(stripped)
            pt = POINT_RE.match(stripped)
            if cl: state["clause"] = cl.group(1); state["point"] = None
            elif pt: state["point"] = pt.group(1).lower()
        current.append(line)
    flush()

    chunks, pending, pending_meta, index = [], "", {}, 0
    for unit, meta in units:
        if len(unit) > max_size:
            if pending:
                chunks.append(_make(page,pending,"legal",index,0,len(pending),pending_meta)); index += 1; pending=""
            for piece in _split_text(unit,max_size):
                chunks.append(_make(page,piece,"legal",index,0,len(piece),meta)); index += 1
            continue
        if pending and (len(pending)+len(unit)+2 > max_size or meta.get("article") != pending_meta.get("article")):
            chunks.append(_make(page,pending,"legal",index,0,len(pending),pending_meta)); index += 1
            pending = ""
        if not pending: pending_meta = meta
        pending = unit if not pending else pending + "\n\n" + unit
    if pending: chunks.append(_make(page,pending,"legal",index,0,len(pending),pending_meta))
    return chunks
