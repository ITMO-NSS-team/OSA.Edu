from __future__ import annotations

import hashlib
import os
from typing import Any

import regex as re

from ..defaults import model_definition
from ..llm.client import ask_structured_json, estimate_tokens
from ..util import normalized_quote, now_iso
from .title import extract_best_title

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
        "version": 2,
        "createdAt": now_iso(),
        "provider": provider,
        "model": model,
        "promptHash": _prompt_hash(prompt),
        "status": "partial" if any(x.get("severity") == "warning" for x in parsed["issues"]) else "ready",
        "elements": parsed["elements"],
        "relations": [],
        "issues": parsed["issues"],
        "warnings": parsed["warnings"],
        "usage": response["usage"],
        "extraction": {"totalBlocks": len(blocks), "processedBlocks": len(blocks), "totalBatches": 1, "processedBatches": 1},
        "review": {"required": True, "confirmedByUser": False},
    }


def map_can_be_reused(map_value: dict | None, provider: str, model: str, prompt: str) -> bool:
    return bool(
        map_value and map_value.get("version") == 2 and map_value.get("provider") == provider
        and map_value.get("model") == model and map_value.get("promptHash") == _prompt_hash(prompt)
    )


def refresh_map(document: dict[str, Any], map_value: dict[str, Any]) -> dict[str, Any]:
    blocks = document.get("blocks", [])
    index = {b["id"]: i for i, b in enumerate(blocks)}
    elements = [_materialize_element(x, blocks, index) for x in map_value.get("elements", [])]
    elements = [x for x in elements if x is not None]
    issues = _validate_map(elements, blocks)
    return {
        **map_value,
        "elements": elements,
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
    issues = [*_parse_issues(value, elements), *_validate_map(elements, blocks)]
    return {"elements": elements, "issues": _dedupe_issues(issues), "warnings": warnings}


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
    elif element.get("type") == "goal":
        fallback = _extract_goal(range_text)
    elif element.get("type") == "chapter_conclusions" and _obvious_chapter_conclusions(label, range_blocks):
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
    pattern = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s*)?выводы(?:\s+(?:к|по)\s+главе(?:\s+\d+)?)?\.?$", re.I)
    return any(pattern.match(re.sub(r"\s+", " ", value).strip()) for value in values if value)


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
