from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .metadata import classify_source_type
from .normalize_references import classify_reference, normalize_text

REF_HEADING_RE = re.compile(
    r"""
    ^\s*
    (?:\d+(?:\.\d+)*[.)]?\s*)?
    (?:
        список\s+(?:(?:использованных|использованной|использованых)\s+)?(?:источников|литературы)
        (?:\s+и\s+литературы)?
      | библиографический\s+список
      | библиография
      | литература
      | bibliography
      | references
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

STOP_HEADING_RE = re.compile(
    r"""
    ^\s*
    (?:\d+(?:\.\d+)*[.)]?\s*)?
    (?:
        приложени[ея]
      | appendix
      | (?:[A-ZА-Я]\s+)?дополнительные\s+материалы
      | [A-ZА-Я]\s{2,}\S+
      | [A-ZА-Я]\.\s+(?:полные\s+таблицы|сводка\s+по|вспомогательные\s+факты|доказательство|детали\s+реализации|дополнительные\s+экспериментальные\s+результаты)
      | [A-ZА-Я]\s+(?:вспомогательные\s+факты|доказательство|детали\s+реализации|дополнительные\s+экспериментальные\s+результаты)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_references(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if REF_HEADING_RE.match(line)]
    if matches:
        start = matches[-1]
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if STOP_HEADING_RE.match(lines[i]):
                end = i
                break
        return "heading", "\n".join(lines[start:end]).strip() + "\n"

    start = max(0, int(len(lines) * 0.80))
    return "fallback_tail", "\n".join(lines[start:]).strip() + "\n"


def _has_reference_heading(text: str) -> bool:
    return any(REF_HEADING_RE.match(line) for line in text.splitlines())


def _select_page_text(sorted_text: str, native_text: str, prefer_native_order: bool) -> tuple[str, bool]:
    """Keep a multi-column bibliography in the PDF's native reading order.

    PyMuPDF's coordinate sorting can place a short continuation block from the
    right column beside the ``References`` heading.  The heading then stops
    being a standalone line, extraction falls back to the last 20% of the
    document, and the bibliography loses both its beginning and its final
    column continuation.  When native order preserves the heading and sorted
    order does not, use native order for that page and the remaining pages.
    """
    if not prefer_native_order and _has_reference_heading(native_text) and not _has_reference_heading(sorted_text):
        prefer_native_order = True
    return (native_text if prefer_native_order else sorted_text), prefer_native_order


def pdf_text(pdf_bytes: bytes) -> tuple[str, list[str]]:
    import pymupdf

    warnings: list[str] = []
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        if document.needs_pass:
            raise RuntimeError("PDF защищён паролем и не может быть прочитан без пароля.")
        page_texts: list[str] = []
        empty_pages: list[int] = []
        prefer_native_order = False
        for index, page in enumerate(document):
            sorted_text = page.get_text("text", sort=True) or ""
            native_text = page.get_text("text", sort=False) or ""
            text, prefer_native_order = _select_page_text(sorted_text, native_text, prefer_native_order)
            if not text.strip():
                empty_pages.append(index + 1)
            page_texts.append(text)
    finally:
        document.close()

    if empty_pages:
        warnings.append(
            "В PDF нет текстового слоя на страницах: "
            + ", ".join(map(str, empty_pages[:20]))
            + ("…" if len(empty_pages) > 20 else "")
            + ". Для них может потребоваться OCR."
        )
    return "\n".join(page_texts), warnings


def extract_reference_records(pdf_bytes: bytes, filename: str) -> tuple[list[dict[str, Any]], str, list[str]]:
    text, warnings = pdf_text(pdf_bytes)
    mode, bibliography = extract_references(text)
    normalized = normalize_text(bibliography)
    thesis = Path(filename).stem or "document"
    records: list[dict[str, Any]] = []
    for item in normalized:
        reference = str(item.get("reference") or "").strip()
        if not reference:
            continue
        kind = classify_reference(reference)
        records.append(
            {
                "thesis": thesis,
                "number": str(item.get("number") or len(records) + 1),
                "kind": kind,
                "source_type": classify_source_type(reference, kind),
                "reference": reference,
            }
        )

    if mode == "fallback_tail":
        warnings.append(
            "Заголовок списка литературы не найден: использован хвост документа. "
            "Проверьте, что в результаты не попал посторонний текст."
        )
    if not records:
        warnings.append("Не удалось выделить отдельные библиографические записи.")
    return records, mode, warnings
