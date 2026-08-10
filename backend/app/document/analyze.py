from __future__ import annotations

from typing import Any
import regex as re

from .title import extract_best_title

ALLOWED_BLOCK_TYPES = {
    "paragraph", "heading", "title", "list", "caption", "table", "formula",
    "code", "bibliography", "toc", "figure",
}


def analyze_document(
    text: str,
    pages: list[dict],
    source_format: str,
    warnings: list[str] | None = None,
    *,
    raw_blocks: list[dict] | None = None,
    style_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable document contract used by the rest of OSA.Edu.

    Older callers may still pass only page text. Rich extractors can additionally pass
    page-level blocks (``pages[*].blocks``) or ``raw_blocks`` for DOCX. Extra layout
    metadata is preserved without changing existing public fields.
    """
    blocks = _build_blocks(text, pages, raw_blocks)
    fields = _extract_fields(blocks)
    value: dict[str, Any] = {
        "text": text,
        "pages": pages,
        "blocks": blocks,
        "warnings": warnings or [],
        "fields": fields,
        "sourceFormat": source_format,
        "styleStats": style_stats or {},
        "blockModelVersion": 3,
    }
    if pages:
        value["detectedPages"] = len(pages)
    return value


def _build_blocks(text: str, pages: list[dict], raw_blocks: list[dict] | None = None) -> list[dict]:
    if raw_blocks is not None:
        return _rich_blocks(raw_blocks, None)
    if pages and any(isinstance(page.get("blocks"), list) for page in pages):
        result: list[dict] = []
        for page in pages:
            number = int(page.get("number") or page.get("page") or 0)
            result.extend(_rich_blocks(page.get("blocks") or [], number or None))
        return _reindex_orders(result)
    if not pages:
        return _paragraphs_to_blocks(text, None, "Документ")
    result: list[dict] = []
    for page in pages:
        number = int(page.get("number") or page.get("page") or 0)
        result.extend(_paragraphs_to_blocks(page.get("text", ""), number or None, f"Страница {number}"))
    return _reindex_orders(result)


def _reindex_orders(blocks: list[dict]) -> list[dict]:
    for order, block in enumerate(blocks):
        block["order"] = order
    return blocks


def _rich_blocks(raw_blocks: list[dict], forced_page: int | None) -> list[dict]:
    result: list[dict] = []
    page_counters: dict[int | None, int] = {}
    for raw in raw_blocks:
        text = re.sub(r"[ \t]+", " ", str(raw.get("text") or "")).strip()
        if len(text) < 2:
            continue
        page = forced_page if forced_page is not None else raw.get("page")
        try:
            page = int(page) if page is not None else None
        except (TypeError, ValueError):
            page = None
        page_counters[page] = page_counters.get(page, 0) + 1
        block_id = f"p{page}-b{page_counters[page]}" if page else f"doc-b{page_counters[page]}"
        block_type = str(raw.get("type") or "").strip().lower()
        if block_type not in ALLOWED_BLOCK_TYPES:
            block_type = _classify_block(text, raw)
        item: dict[str, Any] = {
            "id": block_id,
            **({"page": page} if page else {}),
            "location": f"Страница {page}" if page else "Документ",
            "type": block_type,
            "text": text,
        }
        for key in (
            "bbox", "fontName", "fontSize", "bold", "boldRatio", "italic", "italicRatio",
            "mathRatio", "alignment", "style", "level", "pageBreakBefore", "sourceOrder",
            "rawText", "logicalText", "lines", "lineCount", "textMap", "layoutRole",
            "sourceBlockIds", "pageWidth", "pageHeight", "charCount",
        ):
            if raw.get(key) is not None:
                item[key] = raw.get(key)
        result.append(item)
    return _reindex_orders(result)


def _paragraphs_to_blocks(text: str, page: int | None, location: str) -> list[dict]:
    chunks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        chunks.extend(_split_long_paragraph(paragraph.strip()))
    chunks = [re.sub(r"[ \t]+", " ", x).strip() for x in chunks]
    chunks = [x for x in chunks if len(x) >= 2]
    blocks = [
        {
            "id": f"{'p' + str(page) if page else 'doc'}-b{index + 1}",
            **({"page": page} if page else {}),
            "location": location,
            "type": _classify_block(value),
            "text": value,
            "order": index,
        }
        for index, value in enumerate(chunks)
    ]
    return blocks


def _split_long_paragraph(value: str) -> list[str]:
    if len(value) <= 2400:
        return [value]
    sentences = re.split(r"(?<=[.!?])\s+(?=[А-ЯA-ZЁ0-9])", value)
    result: list[str] = []
    current = ""
    for sentence in sentences:
        combined = f"{current} {sentence}".strip()
        if current and len(combined) > 1800:
            result.append(current)
            current = sentence
        else:
            current = combined
    if current:
        result.append(current)
    return result


def _classify_block(text: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    compact = re.sub(r"\s+", " ", text).strip()
    style = str(metadata.get("style") or "")
    level = metadata.get("level")
    font_size = float(metadata.get("fontSize") or 0)
    body_size = float(metadata.get("bodyFontSize") or 0)
    bold = bool(metadata.get("bold"))

    if metadata.get("table"):
        return "table"
    if metadata.get("code") or re.match(r"^\s*(?:def |class |import |from \w+ import|function |#include|SELECT\s+.+FROM|</?\w+>)", compact, re.I):
        return "code"
    if re.search(r"(?:\.\s*){5,}\s*\d+\s*$", compact) or style.lower() in {"toc", "оглавление"}:
        return "toc"
    if re.match(r"^(?:рис(?:унок)?|таблица|график|листинг|figure|table)\s*\.?\s*\d+(?:\.\d+)*\s*(?:[.\-–—:]|$)", compact, re.I):
        return "caption"
    if level == 0 or style.lower() in {"title", "название"}:
        return "title"
    if level is not None or _looks_like_heading(compact, font_size, body_size, bold):
        return "heading"
    if re.match(r"^(?:\[?\d{1,3}[\].)]\s+)\S", compact) and re.search(r"(?:doi|isbn|https?://|//|\bpp?\.\s*\d|№\s*\d|vol\.|изд-во|С\.\s*\d+[-–]\d+)", compact, re.I):
        return "bibliography"
    if metadata.get("listitem") or re.match(r"^(?:\d{1,3}[.)]|[-–—•*])\s+", compact):
        return "list"
    if re.search(r"[=≈≤≥∑∫√±∞∈∉⊂×·]|\\(?:frac|sum|int|begin|alpha|beta)", compact) and len(compact) < 700:
        return "formula"
    return "paragraph"


def _looks_like_heading(text: str, font_size: float, body_size: float, bold: bool) -> bool:
    if _is_named_heading(text):
        return True
    if len(text) > 190:
        return False
    if re.match(r"^(?:глава\s+)?\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+\p{L}", text, re.I):
        return True
    ratio = font_size / body_size if body_size else 0
    return bool(len(text.split()) <= 18 and (ratio >= 1.18 or (bold and ratio >= 1.02)))


def _is_named_heading(text: str) -> bool:
    if len(text) > 190:
        return False
    return bool(
        re.match(
            r"^(?:введение|заключение|реферат|аннотация|synopsis|abstract|"
            r"выводы(?:\s+по\s+главе)?|список\s+(?:использованных\s+)?(?:источников|литературы|сокращений)|"
            r"словарь\s+терминов|научная\s+новизна|цель|задачи(?:\s+исследования)?|"
            r"положения,?\s+выносимые\s+на\s+защиту|оглавление|содержание|"
            r"приложение(?:\s+[А-ЯA-Z\d]+)?)\.?$",
            text,
            re.I,
        )
        or (text == text.upper() and re.search(r"\p{L}", text) and len(re.split(r"\s+", text)) <= 12)
    )


def _extract_fields(blocks: list[dict]) -> dict[str, Any]:
    first = [b for b in blocks if not b.get("page") or b.get("page") == 1][:30]
    title = extract_best_title(first, blocks)
    goal = next((b for b in blocks if re.search(r"(?<![\p{L}\p{N}_])цель(?:ю)?\s+(?:(?:диссертационной\s+)?работы|исследования)?\s*(?:является|состоит|заключается|–|-|:)", b.get("text", ""), re.I)), None)
    tasks = _extract_following_list(blocks, re.compile(r"задач(?:и|ами|ей)?\s+(?:работы|исследования)|для достижения.*цели", re.I), 12)
    defense = _extract_section(blocks, re.compile(r"положени[яй],?\s+выносимые\s+на\s+защиту", re.I), 18)
    chapter_headings = [
        block for block in blocks
        if block.get("type") == "heading"
        and re.match(r"^глава\s+\d+\b", block.get("text", ""), re.I)
    ]
    conclusion_headings = [b for b in blocks if b.get("type") == "heading" and re.match(r"^(?:выводы|заключение|итоги)", b.get("text", ""), re.I)]
    bibliography_start = next((i for i, b in enumerate(blocks) if b.get("type") == "heading" and re.search(r"список.*(?:литератур|источник)", b.get("text", ""), re.I)), -1)
    source = blocks[bibliography_start + 1:] if bibliography_start >= 0 else blocks
    bibliography_blocks = [b for b in source if b.get("type") == "bibliography" or re.match(r"^\s*\d+[.)]\s+", b.get("text", ""))]
    return {
        "title": title,
        "goal": goal,
        "tasks": tasks,
        "defenseStatements": defense,
        "chapterHeadings": chapter_headings,
        "conclusionHeadings": conclusion_headings,
        "bibliographyBlocks": bibliography_blocks,
    }


def _extract_following_list(blocks: list[dict], heading_pattern, limit: int) -> list[dict]:
    index = next((i for i, b in enumerate(blocks) if heading_pattern.search(b.get("text", ""))), -1)
    if index < 0:
        return []
    selected: list[dict] = []
    for block in blocks[index:index + limit]:
        if block is not blocks[index] and block.get("type") == "heading":
            break
        if block.get("type") == "list" or re.search(r"(?<![\p{L}\p{N}_])задач", block.get("text", ""), re.I):
            selected.append(block)
    return selected


def _extract_section(blocks: list[dict], heading_pattern, limit: int) -> list[dict]:
    index = next((i for i, b in enumerate(blocks) if heading_pattern.search(b.get("text", ""))), -1)
    if index < 0:
        return []
    selected: list[dict] = []
    for block in blocks[index + 1:index + 1 + limit]:
        if block.get("type") == "heading" and not re.search(r"положени", block.get("text", ""), re.I):
            break
        selected.append(block)
    return [b for b in selected if b.get("type") == "list" or re.search(r"(?:метод|алгоритм|модель|технолог|комплекс|система|классификац|бенчмарк)", b.get("text", ""), re.I)]
