from __future__ import annotations

import hashlib
import os
from typing import Any

import regex as re

from ..defaults import model_definition
from ..llm.client import ask_structured_json, estimate_tokens
from ..util import normalized_quote, now_iso
from .title import extract_best_title
from .numbered_items import collect_unique_defense_items, collect_unique_numbered_items
from .section_signals import find_defense_heading_span, is_defense_heading, is_section_heading
from .units import canonicalize_document_units

ALLOWED_TYPES = {
    "title", "abstract", "introduction", "goal", "tasks", "defense_statements",
    "chapter", "chapter_conclusions", "conclusion", "bibliography", "appendices", "other",
}

TYPE_LABELS = {
    "title": "Название", "abstract": "Аннотация", "introduction": "Введение", "goal": "Цель",
    "tasks": "Задачи", "defense_statements": "Положения на защиту", "chapter": "Глава",
    "chapter_conclusions": "Выводы по главе", "conclusion": "Заключение",
    "bibliography": "Библиография", "appendices": "Приложения", "other": "Другой фрагмент",
}


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _structure_message(blocks: list[dict[str, Any]]) -> str:
    content = "\n\n".join(
        f"BLOCK {b['id']} | {b.get('location','')}"
        + (f" | page={b['page']}" if b.get("page") else "")
        + f" | type={b.get('type','paragraph')}\n{b.get('text','')}"
        for b in blocks
    )
    return f"DOCUMENT_BLOCKS ({len(blocks)}):\n\n{content}"


async def build_document_map(document: dict[str, Any], *, provider: str, model: str, prompt: str) -> dict[str, Any]:
    blocks = document.get("blocks", [])
    if not blocks:
        raise ValueError("Документ не содержит блоков для построения структуры.")
    user_message = _structure_message(blocks)
    try:
        max_chars = int(os.getenv("STRUCTURE_MAX_INPUT_CHARS", "2500000") or 2_500_000)
    except ValueError:
        max_chars = 2_500_000
    if max_chars <= 0:
        max_chars = 2_500_000
    if len(user_message) > max_chars:
        raise ValueError(
            f"Документ слишком большой для одношагового построения структуры: {len(user_message):,} символов "
            f"при лимите {max_chars:,}. Увеличьте STRUCTURE_MAX_INPUT_CHARS или сократите служебные приложения."
        )
    definition = model_definition(model)
    chars_per_token = _positive_float(os.getenv("LLM_CHARS_PER_TOKEN"), 3.0)
    estimated_tokens = int((len(prompt) + len(user_message) + chars_per_token - 1) // chars_per_token)
    if definition and estimated_tokens > int(definition["contextTokens"] * 0.85):
        raise ValueError(
            f"Оценочный объём запроса — {estimated_tokens:,} токенов, что слишком близко к контекстному лимиту "
            f"модели {definition['label']} ({definition['contextTokens']:,}). Выберите модель с большим контекстом."
        )
    response = await ask_structured_json(
        provider=provider, model=model, system_prompt=prompt, user_message=user_message,
        operation="structure", packets=1, candidates=len(blocks),
    )
    parsed = _parse_structure(response["value"], blocks)
    return {
        "version": 4,
        "createdAt": now_iso(),
        "provider": provider,
        "model": model,
        "promptHash": _prompt_hash(prompt),
        "status": "partial" if any(x.get("severity") == "warning" for x in parsed["issues"]) else "ready",
        "elements": parsed["elements"],
        "relations": parsed["relations"],
        "issues": parsed["issues"],
        "warnings": parsed["warnings"],
        "usage": response["usage"],
        "extraction": {"totalBlocks": len(blocks), "processedBlocks": len(blocks), "totalBatches": 1, "processedBatches": 1},
        "review": {"required": True, "confirmedByUser": False},
    }


def map_can_be_reused(map_value: dict | None, provider: str, model: str, prompt: str) -> bool:
    return bool(
        map_value and map_value.get("version") == 4 and map_value.get("provider") == provider
        and map_value.get("model") == model and map_value.get("promptHash") == _prompt_hash(prompt)
        and (map_value.get("verification") or {}).get("status") in {"confirmed", "corrected"}
    )


def refresh_map(document: dict[str, Any], map_value: dict[str, Any]) -> dict[str, Any]:
    blocks = document.get("blocks", [])
    index = {b["id"]: i for i, b in enumerate(blocks)}
    elements = [_materialize_element(x, blocks, index) for x in map_value.get("elements", [])]
    elements = [x for x in elements if x is not None]
    elements = _stabilize_elements(elements, blocks, index)
    elements, unit_issues = canonicalize_document_units(elements, blocks, index)
    issues = _dedupe_issues([*unit_issues, *_validate_map(elements, blocks)])
    return {
        **map_value,
        "elements": elements,
        "relations": _validate_existing_relations(map_value.get("relations", []), elements),
        "issues": issues,
        "status": "partial" if any(x.get("severity") == "warning" for x in issues) else "ready",
    }


def _parse_structure(value: Any, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    block_index = {b["id"]: i for i, b in enumerate(blocks)}
    raw_sections = value.get("sections", []) if isinstance(value, dict) and isinstance(value.get("sections"), list) else []
    warnings: list[str] = []
    elements: list[dict[str, Any]] = []
    for raw_index, record in enumerate(raw_sections):
        if not isinstance(record, dict):
            continue
        element_type = str(record.get("type", "")).strip()
        if element_type not in ALLOWED_TYPES:
            warnings.append(f"Секция {raw_index + 1} отброшена: неизвестный тип «{element_type}».")
            continue
        start_id = str(record.get("startBlockId", "")).strip()
        end_id = str(record.get("endBlockId", "")).strip()
        start, end = block_index.get(start_id), block_index.get(end_id)
        if start is None or end is None or start > end:
            warnings.append(f"Секция {raw_index + 1} отброшена: некорректные границы {start_id}…{end_id}.")
            continue
        anchors = [
            x for x in (record.get("anchorBlockIds") or [])
            if isinstance(x, str) and x in block_index and start <= block_index[x] <= end
        ][:5]
        element = _materialize_element({
            "id": f"section-{len(elements) + 1}",
            "type": element_type,
            "label": str(record.get("label") or TYPE_LABELS[element_type]).strip(),
            "startBlockId": start_id,
            "endBlockId": end_id,
            "blockIds": anchors,
            "pages": [],
            "text": "",
            "quote": str(record.get("quote") or "").strip(),
            "confidence": _clamp(record.get("confidence")),
            "state": "ambiguous" if record.get("state") == "ambiguous" else "confirmed",
            "source": "llm",
            **({"note": str(record.get("note")).strip()} if str(record.get("note") or "").strip() else {}),
        }, blocks, block_index)
        if element:
            elements.append(element)
    elements.sort(key=lambda x: block_index.get(x["startBlockId"], 0))
    for i, element in enumerate(elements):
        element["id"] = f"section-{i + 1}"
    model_issues = _parse_issues(value, elements)
    elements = _stabilize_elements(elements, blocks, block_index)
    elements, unit_issues = canonicalize_document_units(elements, blocks, block_index)
    model_issues = _drop_resolved_model_issues(model_issues, elements)
    relations = _parse_relations(value, elements)
    issues = [*model_issues, *unit_issues, *_validate_map(elements, blocks)]
    return {"elements": elements, "relations": relations, "issues": _dedupe_issues(issues), "warnings": warnings}



def _parse_relations(value: Any, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_relations = value.get("relations", []) if isinstance(value, dict) and isinstance(value.get("relations"), list) else []
    chapter_by_start = {
        str(item.get("startBlockId")): item
        for item in elements
        if item.get("type") == "chapter" and item.get("startBlockId")
    }
    defense = [item for item in elements if item.get("type") == "defense_statements" and item.get("canonicalRole") != "secondary_copy"]
    source_id = defense[0].get("id") if defense else None
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for raw in raw_relations:
        if not isinstance(raw, dict) or raw.get("type") != "defense_statement_primary_chapter":
            continue
        try:
            statement_index = int(raw.get("statementIndex"))
        except (TypeError, ValueError):
            continue
        if statement_index < 0:
            continue
        target_start = str(raw.get("chapterStartBlockId") or raw.get("targetStartBlockId") or "").strip()
        chapter = chapter_by_start.get(target_start)
        if chapter is None:
            continue
        key = (statement_index, target_start)
        if key in seen:
            continue
        seen.add(key)
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        state = "confirmed" if raw.get("state") == "confirmed" else "ambiguous"
        result.append({
            "type": "defense_statement_primary_chapter",
            "statementIndex": statement_index,
            "sourceSectionId": source_id,
            "targetSectionId": chapter.get("id"),
            "targetStartBlockId": target_start,
            "role": "primary",
            "confidence": confidence,
            "state": state,
            "reason": re.sub(r"\s+", " ", str(raw.get("reason") or "")).strip()[:500],
            "source": "llm_document_map",
        })
    return result


def _validate_existing_relations(relations: Any, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(relations, list):
        return []
    # Existing normalized relations use targetStartBlockId. Re-run them through
    # the same validator shape so edited/confirmed maps cannot retain dangling links.
    payload = {"relations": [
        {
            "type": row.get("type"),
            "statementIndex": row.get("statementIndex"),
            "chapterStartBlockId": row.get("targetStartBlockId") or row.get("chapterStartBlockId"),
            "confidence": row.get("confidence", 0.0),
            "state": row.get("state", "ambiguous"),
            "reason": row.get("reason", ""),
        }
        for row in relations if isinstance(row, dict)
    ]}
    return _parse_relations(payload, elements)

def _materialize_element(element: dict[str, Any], blocks: list[dict[str, Any]], index: dict[str, int]) -> dict[str, Any] | None:
    start, end = index.get(element.get("startBlockId")), index.get(element.get("endBlockId"))
    if start is None or end is None or start > end:
        return None
    range_blocks = blocks[start:end + 1]
    anchors = [x for x in element.get("blockIds", []) if x in index and start <= index[x] <= end]
    anchor_blocks = [blocks[index[x]] for x in anchors]
    first = anchor_blocks[0] if anchor_blocks else range_blocks[0]
    range_text = " ".join(x.get("text", "") for x in range_blocks)
    requested_quote = _exact_quote(str(element.get("quote") or ""), range_blocks)
    fallback = ""
    label = str(element.get("label", "")).strip()
    state = element.get("state")
    if element.get("type") == "title":
        title = extract_best_title(range_blocks, blocks)
        fallback = (title or {}).get("text", "")
        # The structure LLM sometimes returns a generic label such as
        # «Титульная страница». The extracted title is deterministic and is also
        # what downstream title rules use, so keep the map/report consistent.
        if fallback:
            label = fallback
    elif element.get("type") == "chapter":
        # Structure models sometimes collapse a factual heading to merely
        # «Глава 1». Prefer the actual source heading so routing/report labels are
        # stable across runs and remain useful to a reviewer.
        if re.fullmatch(r"(?:глава|chapter)\s+\d+\.?", label, re.I):
            heading_parts=[]
            for block in range_blocks[:3]:
                text=re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
                if not text:
                    continue
                if block.get("type") == "heading" or not heading_parts:
                    heading_parts.append(text)
                else:
                    break
            source_heading=" ".join(heading_parts).strip()
            if len(source_heading) > len(label) + 4 and re.match(r"^(?:глава|chapter)\s+\d+", source_heading, re.I):
                label=source_heading
    elif element.get("type") == "goal":
        fallback = _extract_goal(range_text)
    elif element.get("type") in {"tasks", "defense_statements"} and _obvious_list_section(element.get("type"), range_blocks):
        # When a mapped range contains a complete 1..N list (or a defense bullet
        # list), there is no useful reason to preserve a stale LLM ambiguity.
        state = "confirmed"
    elif element.get("type") == "chapter_conclusions" and _range_has_explicit_conclusion_heading(range_blocks):
        # An exact heading like «3.5 Выводы по главе» is not genuinely
        # ambiguous even when the structure model marks it so.
        state = "confirmed"
    return {
        **element,
        "state": state,
        "blockIds": anchors if anchors else [range_blocks[0]["id"]],
        "pages": list(dict.fromkeys(x.get("page") for x in range_blocks if isinstance(x.get("page"), int))),
        "text": _compact(range_text, 900),
        "quote": _compact(requested_quote or fallback or first.get("text", ""), 500),
        "label": label[:180],
    }


def _obvious_chapter_conclusions(label: str, blocks: list[dict[str, Any]]) -> bool:
    values = [label, *[str(block.get("text") or "") for block in blocks[:3]]]
    return any(is_section_heading("chapter_conclusions", value) for value in values if value)


def _range_has_explicit_conclusion_heading(blocks: list[dict[str, Any]]) -> bool:
    return any(_explicit_conclusion_heading(block) for block in blocks[:5])


def _obvious_list_section(element_type: str, blocks: list[dict[str, Any]]) -> bool:
    if element_type == "defense_statements":
        items = collect_unique_defense_items(blocks)
    else:
        items = collect_unique_numbered_items(blocks)
    if len(items) < 2:
        return False
    numbers = [int(item.get("number") or 0) for item in items]
    return numbers == list(range(1, len(numbers) + 1))


def _explicit_conclusion_heading(block: dict[str, Any]) -> bool:
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    if not text or len(text) > 220:
        return False
    # Require a heading-like block or a very short standalone line.  This avoids
    # turning prose such as «далее сформулированы выводы по главе» into structure.
    if block.get("type") != "heading" and len(text) > 150:
        return False
    return _obvious_chapter_conclusions(text, [block])


def _section_heading_block(block: dict[str, Any], section_type: str) -> bool:
    text = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
    if not text:
        return False
    # Explicit section recovery is deliberately heading-biased.  Some PDF
    # extractors label short standalone headings as paragraphs, so short blocks
    # are allowed; long narrative prose is not.
    if block.get("type") != "heading" and len(text) > 140:
        return False
    return is_section_heading(section_type, text)


def _make_recovered_section(
    section_type: str,
    start: int,
    end: int,
    blocks: list[dict[str, Any]],
    index: dict[str, int],
    *,
    label: str | None = None,
) -> dict[str, Any] | None:
    if start < 0 or end < start or end >= len(blocks):
        return None
    first = blocks[start]
    return _materialize_element({
        "id": f"section-auto-{section_type}-{first.get('id')}",
        "type": section_type,
        "label": label or re.sub(r"\s+", " ", str(first.get("text") or TYPE_LABELS.get(section_type, section_type))).strip(),
        "startBlockId": first.get("id"),
        "endBlockId": blocks[end].get("id"),
        "blockIds": [first.get("id")],
        "pages": [],
        "text": "",
        "quote": str(first.get("text") or ""),
        "confidence": 1.0,
        "state": "confirmed",
        "source": "deterministic",
        "note": "Диапазон восстановлен по явному смысловому маркеру раздела и соседним границам документа.",
    }, blocks, index)


def _recover_major_sections(
    elements: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    index: dict[str, int],
) -> list[dict[str, Any]]:
    """Recover missing explicit top-level sections using canonical semantic aliases.

    This is a safety net around the LLM map, not an alternative parser. Recovery
    requires an explicit heading-like source block and uses already mapped chapters
    to select the main document when a PDF also contains a synopsis/summary.
    """
    result = list(elements)
    chapters = [item for item in result if item.get("type") == "chapter" and index.get(item.get("startBlockId")) is not None]
    chapter_starts = sorted(index[item.get("startBlockId")] for item in chapters)
    first_chapter = chapter_starts[0] if chapter_starts else None
    last_chapter = chapter_starts[-1] if chapter_starts else None

    heading_positions: dict[str, list[int]] = {}
    for section_type in ("introduction", "conclusion", "bibliography", "appendices"):
        heading_positions[section_type] = [
            pos for pos, block in enumerate(blocks) if _section_heading_block(block, section_type)
        ]

    # Main introduction: use the last explicit introduction before the first
    # mapped chapter. This avoids selecting an early synopsis introduction.
    if first_chapter is not None:
        candidates = [pos for pos in heading_positions["introduction"] if pos < first_chapter]
        if candidates:
            start = max(candidates)
            mapped_main_intro = any(
                item.get("type") == "introduction"
                and index.get(item.get("startBlockId")) is not None
                and index.get(item.get("endBlockId")) is not None
                and index[item.get("startBlockId")] <= start <= index[item.get("endBlockId")]
                for item in result
            )
            if not mapped_main_intro:
                auto = _make_recovered_section("introduction", start, max(start, first_chapter - 1), blocks, index)
                if auto:
                    result.append(auto)

    # The remaining global sections should occur after the final main chapter.
    tail_floor = last_chapter if last_chapter is not None else -1

    has_main_conclusion = any(
        item.get("type") == "conclusion"
        and index.get(item.get("startBlockId"), -1) > tail_floor
        for item in result
    )
    if not has_main_conclusion:
        candidates = [pos for pos in heading_positions["conclusion"] if pos > tail_floor]
        if candidates:
            start = min(candidates)
            boundaries = [
                pos for kind in ("bibliography", "appendices")
                for pos in heading_positions[kind] if pos > start
            ]
            end = (min(boundaries) - 1) if boundaries else len(blocks) - 1
            auto = _make_recovered_section("conclusion", start, max(start, end), blocks, index)
            if auto:
                result.append(auto)

    has_main_bibliography = any(
        item.get("type") == "bibliography"
        and index.get(item.get("startBlockId"), -1) > tail_floor
        for item in result
    )
    if not has_main_bibliography:
        candidates = [pos for pos in heading_positions["bibliography"] if pos > tail_floor]
        if candidates:
            start = min(candidates)
            appendix_after = [pos for pos in heading_positions["appendices"] if pos > start]
            end = (min(appendix_after) - 1) if appendix_after else len(blocks) - 1
            auto = _make_recovered_section("bibliography", start, max(start, end), blocks, index)
            if auto:
                result.append(auto)

    has_main_appendices = any(
        item.get("type") == "appendices"
        and index.get(item.get("startBlockId"), -1) > tail_floor
        for item in result
    )
    if not has_main_appendices:
        floor = tail_floor
        bib_starts = [index.get(item.get("startBlockId")) for item in result if item.get("type") == "bibliography"]
        con_starts = [index.get(item.get("startBlockId")) for item in result if item.get("type") == "conclusion"]
        floor = max([floor, *[x for x in bib_starts + con_starts if x is not None]])
        candidates = [pos for pos in heading_positions["appendices"] if pos > floor]
        if candidates:
            start = min(candidates)
            auto = _make_recovered_section("appendices", start, len(blocks) - 1, blocks, index)
            if auto:
                result.append(auto)

    return result


def _stabilize_elements(
    elements: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    index: dict[str, int],
) -> list[dict[str, Any]]:
    """Keep DocumentMap factual and deterministically resolve obvious ranges.

    The structure LLM may mark an unambiguous numbered list as ambiguous or may
    invent a one-block ``chapter_conclusions`` range at the end of a chapter.
    This pass confirms only structurally provable lists, removes unsupported LLM
    conclusion ranges, and adds a conclusion range only when an explicit heading
    exists in the source blocks.  User-created ranges are never removed.
    """
    kept: list[dict[str, Any]] = []
    for element in elements:
        start = index.get(element.get("startBlockId"))
        end = index.get(element.get("endBlockId"))
        if start is None or end is None or start > end:
            continue
        range_blocks = blocks[start:end + 1]
        element = dict(element)
        if element.get("type") in {"tasks", "defense_statements"} and _obvious_list_section(str(element.get("type")), range_blocks):
            element["state"] = "confirmed"
        if (
            element.get("type") == "chapter_conclusions"
            and element.get("source") != "user"
            and not _range_has_explicit_conclusion_heading(range_blocks)
        ):
            # Never materialize a conclusion section that has no explicit source
            # heading. Missing conclusions are handled later as virtual incomplete
            # checking context, not by inventing structure in DocumentMap.
            continue
        kept.append(element)

    kept = _recover_major_sections(kept, blocks, index)
    kept = _normalize_chapter_boundaries(kept, blocks, index)
    chapters = [item for item in kept if item.get("type") == "chapter"]
    conclusions = [item for item in kept if item.get("type") == "chapter_conclusions"]
    existing_ids = {str(item.get("id") or "") for item in kept}
    for chapter in chapters:
        chapter_start = index.get(chapter.get("startBlockId"))
        chapter_end = index.get(chapter.get("endBlockId"))
        if chapter_start is None or chapter_end is None or chapter_start > chapter_end:
            continue
        if any(
            chapter_start <= index.get(item.get("startBlockId"), -1) <= chapter_end
            for item in conclusions
        ):
            continue
        heading_index = next(
            (i for i in range(chapter_start, chapter_end + 1) if _explicit_conclusion_heading(blocks[i])),
            None,
        )
        if heading_index is None:
            continue
        heading = blocks[heading_index]
        candidate_id = f"section-auto-conclusion-{chapter.get('id') or heading.get('id')}"
        if candidate_id in existing_ids:
            continue
        auto = _materialize_element({
            "id": candidate_id,
            "type": "chapter_conclusions",
            "label": re.sub(r"\s+", " ", str(heading.get("text") or "Выводы по главе")).strip(),
            "startBlockId": heading.get("id"),
            "endBlockId": chapter.get("endBlockId"),
            "blockIds": [heading.get("id")],
            "pages": [],
            "text": "",
            "quote": str(heading.get("text") or ""),
            "confidence": 1.0,
            "state": "confirmed",
            "source": "deterministic",
        }, blocks, index)
        if auto:
            kept.append(auto)
            conclusions.append(auto)
            existing_ids.add(candidate_id)

    kept.sort(key=lambda item: (index.get(item.get("startBlockId"), 10**9), 0 if item.get("type") == "chapter" else 1))
    return kept



def _normalize_chapter_boundaries(
    elements: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    index: dict[str, int],
) -> list[dict[str, Any]]:
    """Make chapter ranges factual and contiguous up to the next top-level section.

    Structure models sometimes stop a chapter several pages before its explicit
    ``chapter_conclusions`` heading.  Downstream semantic checks would then call a
    truncated range "complete".  For model/deterministic map elements the source
    document gives a safer boundary: a chapter ends immediately before the next
    chapter or global conclusion/bibliography.  Explicit user-created ranges are
    left untouched.
    """
    top_level = [
        item for item in elements
        if item.get("type") in {"chapter", "conclusion", "bibliography"}
        and index.get(item.get("startBlockId")) is not None
    ]
    top_level.sort(key=lambda item: index[item.get("startBlockId")])
    result: list[dict[str, Any]] = []
    for item in elements:
        if item.get("type") != "chapter" or item.get("source") == "user":
            result.append(item)
            continue
        start = index.get(item.get("startBlockId"))
        if start is None:
            result.append(item)
            continue
        next_start = next((
            index.get(other.get("startBlockId"))
            for other in top_level
            if index.get(other.get("startBlockId"), -1) > start
        ), None)
        if next_start is None:
            result.append(item)
            continue
        target_end = max(start, next_start - 1)
        target_id = blocks[target_end].get("id")
        if not target_id or target_id == item.get("endBlockId"):
            result.append(item)
            continue
        rematerialized = _materialize_element({**item, "endBlockId": target_id}, blocks, index)
        result.append(rematerialized or item)
    return result

def _confirmed_defense_marker(elements: list[dict[str, Any]]) -> bool:
    return any(
        item.get("type") == "defense_statements"
        and item.get("state") == "confirmed"
        and any(find_defense_heading_span(str(item.get(key) or "")) for key in ("label", "text", "quote"))
        for item in elements
    )


def _drop_resolved_model_issues(issues: list[dict[str, Any]], elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): item for item in elements}
    result: list[dict[str, Any]] = []
    for issue in issues:
        refs = [str(value) for value in issue.get("elementIds", []) if value]
        live = [by_id[value] for value in refs if value in by_id]
        message=str(issue.get("message") or "")
        is_ambiguity = "ambig" in str(issue.get("code") or "").lower() or re.search(r"неоднознач", message, re.I)
        if _confirmed_defense_marker(elements) and re.search(r"(?:положени|защит|defen[cs]e).*(?:не\s+подтвержд|маркер|явн)", message, re.I):
            continue
        if refs and not live:
            continue
        if is_ambiguity and live and all(item.get("state") == "confirmed" for item in live):
            continue
        result.append(issue)
    return result

def _exact_quote(value: str, blocks: list[dict[str, Any]]) -> str:
    requested = re.sub(r'\s+', ' ', value).strip()
    if len(requested) < 4:
        return ""
    target = normalized_quote(requested)
    return requested if any(target in normalized_quote(x.get("text", "")) for x in blocks) else ""


def _extract_goal(value: str) -> str:
    normalized = re.sub(r'\s+', ' ', value).strip()
    match = re.search(r'(?:Цель(?:ю)?\s+(?:диссертационной\s+)?работы(?:\s+является|\s*[:.–-])?\s*)([^.]{15,700}\.)', normalized, re.I)
    return match.group(0).strip() if match else ""


def _validate_map(elements: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not elements:
        issues.append({"code": "empty_structure", "severity": "warning", "message": "LLM не вернула ни одного проверяемого диапазона.", "elementIds": []})
    if not any(x.get("type") == "introduction" for x in elements):
        issues.append({"code": "missing_introduction", "severity": "warning", "message": "Не найден крупный диапазон введения.", "elementIds": []})
    if not any(x.get("type") == "chapter" for x in elements):
        issues.append({"code": "missing_chapters", "severity": "warning", "message": "Не найдены главы основной части.", "elementIds": []})
    if not any(x.get("type") == "conclusion" for x in elements):
        issues.append({"code": "missing_conclusion", "severity": "warning", "message": "Не найдено заключение.", "elementIds": []})
    for element_type in ["goal", "tasks", "defense_statements"]:
        if not any(x.get("type") == element_type for x in elements):
            issues.append({"code": f"missing_{element_type}", "severity": "info", "message": f"Не найден отдельный диапазон «{TYPE_LABELS[element_type]}». Проверьте введение вручную.", "elementIds": []})
    block_ids = {x.get("id") for x in blocks}
    for element in elements:
        if element.get("startBlockId") not in block_ids or element.get("endBlockId") not in block_ids:
            issues.append({"code": "invalid_boundaries", "severity": "warning", "message": f"У секции «{element.get('label','')}» недействительные границы.", "elementIds": [element.get("id")]})
        if element.get("state") == "ambiguous":
            issues.append({"code": "ambiguous_section", "severity": "info", "message": f"Секция «{element.get('label','')}» отмечена моделью как неоднозначная.", "elementIds": [element.get("id")]})
    return issues


def _parse_issues(value: Any, elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = value.get("issues", []) if isinstance(value, dict) and isinstance(value.get("issues"), list) else []
    result: list[dict[str, Any]] = []
    for record in raw:
        if not isinstance(record, dict):
            continue
        message = str(record.get("message") or "").strip()
        if not message:
            continue
        indexes = [int(x) for x in (record.get("sectionIndexes") or []) if isinstance(x, (int, float)) and int(x) == x]
        referenced = [elements[i] for i in indexes if 0 <= i < len(elements)]
        code = str(record.get("code") or "model_issue").strip()
        if referenced and ("ambig" in code.lower() or re.search(r"неоднознач", message, re.I)):
            if all(item.get("state") == "confirmed" for item in referenced):
                # Deterministic post-processing may have resolved an LLM ambiguity
                # (e.g. an exact «3.5 Выводы по главе» heading). Do not keep the
                # stale model issue in the report after that resolution.
                continue
        result.append({
            "code": code,
            "severity": "info" if record.get("severity") == "info" else "warning",
            "message": message,
            "elementIds": [item.get("id") for item in referenced],
        })
    return result


def _dedupe_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = f"{item.get('code')}|{item.get('message')}"
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _compact(value: str, limit: int) -> str:
    normalized = re.sub(r'\s+', ' ', value).strip()
    return normalized if len(normalized) <= limit else normalized[:limit - 1] + '…'


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _positive_float(value: str | None, fallback: float) -> float:
    try:
        number = float(value or '')
        return number if number > 0 else fallback
    except ValueError:
        return fallback
