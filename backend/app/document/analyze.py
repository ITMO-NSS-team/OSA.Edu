from __future__ import annotations

from typing import Any
import regex as re

from .title import extract_best_title


def analyze_document(text: str, pages: list[dict], source_format: str, warnings: list[str] | None = None) -> dict[str, Any]:
    blocks = _build_blocks(text, pages)
    fields = _extract_fields(blocks)
    value: dict[str, Any] = {
        "text": text,
        "pages": pages,
        "blocks": blocks,
        "warnings": warnings or [],
        "fields": fields,
        "sourceFormat": source_format,
    }
    if pages:
        value["detectedPages"] = len(pages)
    return value


def _build_blocks(text: str, pages: list[dict]) -> list[dict]:
    if not pages:
        return _paragraphs_to_blocks(text, None, "Документ")
    result: list[dict] = []
    for page in pages:
        number = int(page.get("number") or page.get("page") or 0)
        result.extend(_paragraphs_to_blocks(page.get("text", ""), number or None, f"Страница {number}"))
    return result


def _paragraphs_to_blocks(text: str, page: int | None, location: str) -> list[dict]:
    chunks: list[str] = []
    for paragraph in re.split(r'\n\s*\n', text):
        chunks.extend(_split_long_paragraph(paragraph.strip()))
    chunks = [re.sub(r'[ \t]+', ' ', x).strip() for x in chunks]
    chunks = [x for x in chunks if len(x) >= 2]
    return [
        {
            "id": f"{'p' + str(page) if page else 'doc'}-b{index + 1}",
            **({"page": page} if page else {}),
            "location": location,
            "type": _classify_block(value),
            "text": value,
        }
        for index, value in enumerate(chunks)
    ]


def _split_long_paragraph(value: str) -> list[str]:
    if len(value) <= 2400:
        return [value]
    sentences = re.split(r'(?<=[.!?])\s+(?=[А-ЯA-ZЁ0-9])', value)
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


def _classify_block(text: str) -> str:
    compact = re.sub(r'\s+', ' ', text).strip()
    if re.match(r'^(?:рис(?:унок)?|таблица|график)\s*\d*[.\-–—:]?', compact, re.I):
        return "caption"
    if re.match(r'^(?:глава\s+\d+|\d+(?:\.\d+){1,3}\.?)\s+\p{L}', compact, re.I) or _is_named_heading(compact):
        return "heading"
    if re.match(r'^(?:\d+[.)]|[-–—•])\s+', compact):
        return "list"
    if re.match(r'^(?:\[?\d+\]?\.?\s+)[А-ЯA-ZЁ][^\n]{20,}$', compact) and re.search(r'(?:doi|isbn|https?:|pp?\.|№|vol\.)', compact, re.I):
        return "bibliography"
    if re.search(r'[=≈≤≥∑∫√]|\\(?:frac|sum|begin)', compact) and len(compact) < 700:
        return "formula"
    return "paragraph"


def _is_named_heading(text: str) -> bool:
    if len(text) > 190:
        return False
    return bool(
        re.match(r'^(?:введение|заключение|выводы(?:\s+по\s+главе)?|список\s+(?:использованных\s+)?(?:источников|литературы)|оглавление|содержание|приложение(?:\s+[А-ЯA-Z])?)\.?$', text, re.I)
        or (text == text.upper() and re.search(r'\p{L}', text) and len(re.split(r'\s+', text)) <= 12)
    )


def _extract_fields(blocks: list[dict]) -> dict[str, Any]:
    first = [b for b in blocks if not b.get("page") or b.get("page") == 1][:30]
    title = extract_best_title(first, blocks)
    goal = next((b for b in blocks if re.search(r'(?<![\p{L}\p{N}_])цель(?:ю)?\s+(?:работы|исследования)?\s*(?:является|состоит|заключается|–|-|:)', b.get("text", ""), re.I)), None)
    tasks = _extract_following_list(blocks, re.compile(r'задач(?:и|ами|ей)?\s+(?:работы|исследования)|для достижения.*цели', re.I), 12)
    defense = _extract_section(blocks, re.compile(r'положени[яй],?\s+выносимые\s+на\s+защиту', re.I), 18)
    chapter_headings: list[dict] = []
    for block in blocks:
        if re.match(r'^глава\s+\d+', block.get("text", ""), re.I):
            chapter_headings.append(block)
            continue
        if not re.match(r'^\d+\.\s+\p{L}', block.get("text", "")) or not block.get("page"):
            continue
        page_blocks = [x for x in blocks if x.get("page") == block.get("page")]
        if next((i for i, x in enumerate(page_blocks) if x.get("id") == block.get("id")), 999) <= 2 and len(block.get("text", "")) <= 150:
            chapter_headings.append(block)
    conclusion_headings = [b for b in blocks if b.get("type") == "heading" and re.match(r'^(?:выводы|заключение|итоги)', b.get("text", ""), re.I)]
    bibliography_start = next((i for i, b in enumerate(blocks) if b.get("type") == "heading" and re.search(r'список.*(?:литератур|источник)', b.get("text", ""), re.I)), -1)
    source = blocks[bibliography_start + 1:] if bibliography_start >= 0 else blocks
    bibliography_blocks = [b for b in source if b.get("type") == "bibliography" or re.match(r'^\s*\d+[.)]\s+', b.get("text", ""))]
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
        if block.get("type") == "list" or re.search(r'(?<![\p{L}\p{N}_])задач', block.get("text", ""), re.I):
            selected.append(block)
    return selected


def _extract_section(blocks: list[dict], heading_pattern, limit: int) -> list[dict]:
    index = next((i for i, b in enumerate(blocks) if heading_pattern.search(b.get("text", ""))), -1)
    if index < 0:
        return []
    selected: list[dict] = []
    for block in blocks[index + 1:index + 1 + limit]:
        if block.get("type") == "heading" and not re.search(r'положени', block.get("text", ""), re.I):
            break
        selected.append(block)
    return [b for b in selected if b.get("type") == "list" or re.search(r'(?:метод|алгоритм|модель|технолог|комплекс|система)', b.get("text", ""), re.I)]
