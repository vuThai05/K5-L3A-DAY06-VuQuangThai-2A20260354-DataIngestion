"""PDF ingestion with Vietnamese OCR fallback (Tesseract)."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader
from .models import DocumentPage


def normalize_text(text: str) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace("\u00ad", "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip()
             for line in text.split("\n")]
    return "\n".join(lines).strip()


def _ocr_page(pdf_path: Path, page_index: int, lang: str, dpi: int) -> str:
    """Render one physical page with PyMuPDF and OCR it with Tesseract."""
    try:
        import pymupdf
        import pytesseract
        from PIL import Image
        import io
    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies missing. Install pymupdf, pytesseract and Pillow."
        ) from exc

    command = os.getenv("TESSERACT_CMD", "").strip()
    if command:
        pytesseract.pytesseract.tesseract_cmd = command

    with pymupdf.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(image, lang=lang, config="--psm 3")


def load_pdf(
    path: Path,
    document_id: str,
    source: str,
    *,
    ocr: bool = True,
    ocr_lang: str = "vie+eng",
    ocr_dpi: int = 300,
    min_text_chars: int = 35,
) -> list[DocumentPage]:
    """Extract every PDF page; OCR pages with little/no embedded text.

    Requires the Tesseract executable and Vietnamese traineddata (`vie`) for
    Vietnamese OCR. Pages remain 1-indexed physical PDF pages.
    """
    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {pdf_path.name}")

    try:
        reader = PdfReader(str(pdf_path), strict=False)
    except Exception as exc:
        raise ValueError(f"Cannot parse PDF '{pdf_path}': {exc}") from exc

    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ValueError("PDF is password-protected.")
        except Exception as exc:
            raise ValueError(f"Cannot decrypt PDF: {pdf_path}") from exc

    pages: list[DocumentPage] = []
    for page_number, pdf_page in enumerate(reader.pages, start=1):
        embedded_text, extraction_error = "", None
        try:
            embedded_text = normalize_text(pdf_page.extract_text() or "")
        except Exception as exc:
            extraction_error = f"{type(exc).__name__}: {exc}"

        used_ocr = False
        text = embedded_text
        needs_ocr = len(re.sub(r"\s+", "", embedded_text)) < min_text_chars

        if ocr and needs_ocr:
            try:
                text = normalize_text(_ocr_page(
                    pdf_path, page_number - 1, ocr_lang, ocr_dpi
                ))
                used_ocr = True
            except Exception as exc:
                # Preserve page and error details; caller can inspect OCR failures.
                extraction_error = (
                    f"{extraction_error}; " if extraction_error else ""
                ) + f"OCR {type(exc).__name__}: {exc}"
                text = embedded_text

        pages.append(DocumentPage(
            document_id=document_id,
            source=source,
            page=page_number,
            text=text,
            metadata={
                "is_empty": not bool(text.strip()),
                "char_count": len(text),
                "needs_ocr": not bool(text.strip()),
                "ocr_attempted": bool(ocr and needs_ocr),
                "ocr_used": used_ocr,
                "extraction_status": (
                    "ocr_success" if used_ocr and text.strip()
                    else "error" if extraction_error
                    else "success" if text.strip()
                    else "empty"
                ),
                **({"extraction_error": extraction_error} if extraction_error else {}),
            },
        ))
    return pages
