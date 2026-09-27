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

SPLIT_SECTION_LETTER_RE = re.compile(r"^\s*[A-ZА-ЯЁ]\.?\s*$")
SPLIT_SECTION_TITLE_RE = re.compile(
    r"^\s*[A-ZА-ЯЁ][A-Za-zА-Яа-яЁёÀ-ÖØ-öø-ÿ'’\-]+"
    r"(?:\s+(?:[A-ZА-ЯЁ][A-Za-zА-Яа-яЁёÀ-ÖØ-öø-ÿ'’\-]+|and|of|the|и|в|на|для)){0,7}\s*$"
)


def _is_split_section_heading(lines: list[str], index: int) -> bool:
    """Recognize headings whose section letter and title are separate PDF lines."""
    if not SPLIT_SECTION_LETTER_RE.match(lines[index]):
        return False
    next_index = index + 1
    while next_index < len(lines) and not lines[next_index].strip():
        next_index += 1
    return next_index < len(lines) and bool(SPLIT_SECTION_TITLE_RE.match(lines[next_index]))


def extract_references(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    matches = [i for i, line in enumerate(lines) if REF_HEADING_RE.match(line)]
    if matches:
        start = matches[-1]
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if STOP_HEADING_RE.match(lines[i]) or _is_split_section_heading(lines, i):
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


def _indent_positioned_lines(positioned_lines: list[tuple[float, str]], page_width: float) -> str:
    """Restore hanging indents that PyMuPDF drops from native-order text.

    Academic bibliographies commonly place wrapped lines roughly 10 points to
    the right of the first line.  A single leading space is enough for the
    bibliography normalizer to preserve that structural signal.  Column bases
    are calculated independently so a continuation at the top of the right
    column is not mistaken for a new reference.
    """
    midpoint = page_width / 2
    bases: dict[int, float] = {}
    for x0, text in positioned_lines:
        stripped = text.strip()
        if not stripped or stripped.isdigit():
            continue
        column = 0 if x0 < midpoint else 1
        bases[column] = min(bases.get(column, x0), x0)

    rendered: list[str] = []
    for x0, text in positioned_lines:
        column = 0 if x0 < midpoint else 1
        base = bases.get(column, x0)
        prefix = " " if x0 - base >= 3.0 else ""
        rendered.append(prefix + text.rstrip())
    return "\n".join(rendered).rstrip() + "\n"


def _native_page_text(page: Any) -> str:
    """Return native PDF reading order while retaining line x-coordinates."""
    positioned_lines: list[tuple[float, str]] = []
    page_dict = page.get_text("dict", sort=False) or {}
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text") or "") for span in spans)
            if not text.strip():
                continue
            x_positions = [float(span["bbox"][0]) for span in spans if span.get("bbox")]
            x0 = min(x_positions) if x_positions else float(line.get("bbox", [0.0])[0])
            positioned_lines.append((x0, text))
    return _indent_positioned_lines(positioned_lines, float(page.rect.width))


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
            native_text = _native_page_text(page)
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
