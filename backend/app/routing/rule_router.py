from __future__ import annotations

import json
import regex as re

from ..config import CONFIG_DIR
from ..document.numbered_items import collect_unique_numbered_items
from ..document.semantic_ranges import trim_blocks_for_element
from ..util import empty_usage

DIRECT_SELECTORS = {
    "title", "abstract", "introduction", "goal", "tasks", "defense_statements",
    "chapter", "chapter_conclusions", "conclusion", "bibliography", "appendices", "other",
}
ABBREVIATION_RULE_IDS = {"CORE-4-1", "CORE-4-2", "CORE-4-3", "CORE-12"}


def _load_config() -> dict:
    path = CONFIG_DIR / "rule-routing.json"
    if not path.exists():
        return {"rules": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"rules": {}}


def _unique_blocks(blocks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for block in blocks:
        block_id = str(block.get("id", ""))
        if block_id and block_id not in seen:
            seen.add(block_id)
            result.append(block)
    return result


def _build_fragments(document: dict, map_value: dict) -> list[dict]:
    blocks = document.get("blocks", [])
    block_index = {block.get("id"): index for index, block in enumerate(blocks)}
    fragments: list[dict] = []

    # Direct map fragments. IDs intentionally stay equal to DocumentMap element IDs;
    # the React client and reports can therefore refer to exactly the same semantic ranges.
    for element in map_value.get("elements", []):
        start = block_index.get(element.get("startBlockId"))
        end = block_index.get(element.get("endBlockId"))
        if start is None or end is None or start > end:
            continue
        selected = trim_blocks_for_element(element.get("type", "other"), blocks[start:end + 1])
        if not selected:
            continue
        fragments.append({
            "id": element.get("id"),
            "type": element.get("type", "other"),
            "selector": element.get("type", "other"),
            "label": element.get("label") or element.get("type", "other"),
            "blocks": selected,
            "complete": element.get("state") == "confirmed",
        })

    def by_type(element_type: str) -> list[dict]:
        return [fragment for fragment in fragments if fragment.get("type") == element_type]

    def add_virtual(selector: str, label: str, source: list[dict], selected_blocks: list[dict], complete: bool | None = None) -> None:
        unique = _unique_blocks(selected_blocks)
        if not unique:
            return
        if complete is None:
            complete = bool(source) and all(item.get("complete") for item in source)
        fragments.append({
            "id": f"virtual-{selector}",
            "type": "virtual",
            "selector": selector,
            "label": label,
            "blocks": unique,
            "complete": bool(complete),
        })

    title = by_type("title")
    introduction = by_type("introduction")
    goal = by_type("goal")
    tasks = by_type("tasks")
    defense = by_type("defense_statements")
    chapters = by_type("chapter")
    explicit_conclusions = by_type("chapter_conclusions")
    conclusion = by_type("conclusion")

    derived_conclusions = explicit_conclusions or [
        result for index, chapter in enumerate(chapters)
        if (result := _derive_chapter_conclusion(chapter, index)) is not None
    ]
    existing_ids = {fragment.get("id") for fragment in fragments}
    for item in derived_conclusions:
        if item.get("id") not in existing_ids:
            fragments.append(item)
            existing_ids.add(item.get("id"))

    add_virtual(
        "title_goal",
        "Название и цель",
        [*title, *goal, *introduction],
        [*(_flatten_blocks(title)), *(_flatten_blocks(goal))],
    )

    scientific_source = [*title, *introduction, *goal, *tasks, *defense, *chapters, *conclusion]
    scientific_blocks = [
        *_flatten_blocks(title),
        *_flatten_blocks(introduction),
        *[block for chapter in chapters for block in _select_chapter_summary(chapter.get("blocks", []))],
        *_flatten_blocks(conclusion),
    ]
    add_virtual("scientific_core", "Научное ядро работы", scientific_source, scientific_blocks)

    defense_chapter_blocks = [
        *_flatten_blocks(defense),
        *[block for chapter in chapters for block in _select_chapter_evidence(chapter.get("blocks", []))],
        *_flatten_blocks(derived_conclusions),
    ]
    add_virtual(
        "defense_chapters",
        "Положения, аналоги и соответствующие главы",
        [*defense, *chapters, *derived_conclusions],
        defense_chapter_blocks,
    )

    statements = _split_defense_statements(defense)
    target_chapters = chapters[-len(statements):] if statements and len(chapters) > len(statements) else chapters[:len(statements)]
    for index in range(min(len(statements), len(target_chapters))):
        statement = statements[index]
        chapter = target_chapters[index]
        fragments.append({
            "id": f"virtual-defense-chapter-{index + 1}",
            "type": "virtual",
            "selector": "defense_chapter_matrix",
            "label": f"Положение {index + 1} ↔ {chapter.get('label', '')}",
            "blocks": _unique_blocks([statement, *chapter.get("blocks", [])]),
            "complete": bool(chapter.get("complete") and all(item.get("complete") for item in defense)),
            "resultKind": _infer_result_kind(statement.get("text", "")),
        })

    for item in derived_conclusions:
        if not item.get("selector"):
            item["selector"] = "chapter_conclusions"

    add_virtual(
        "conclusion_global",
        "Цель, задачи, выводы глав и заключение",
        [*title, *goal, *tasks, *defense, *derived_conclusions, *conclusion],
        [
            *_flatten_blocks(title), *_flatten_blocks(goal), *_flatten_blocks(tasks),
            *_flatten_blocks(defense), *_flatten_blocks(derived_conclusions), *_flatten_blocks(conclusion),
        ],
    )

    implementation_re = re.compile(
        r"внедрен|использовани[ея]\s+результат|акт(?:а|ы|ов)?\s+внедрен|репозитор|github|gitlab|"
        r"открыт(?:ое|ый)\s+по|программ(?:ное|ный)\s+средств",
        re.I,
    )
    implementation_blocks = _unique_blocks([
        *[block for block in blocks if implementation_re.search(block.get("text", ""))],
        *[block for chapter in chapters for block in chapter.get("blocks", [])[:2]],
        *_flatten_blocks(conclusion),
    ])
    add_virtual(
        "implementation_context",
        "Внедрение и практическое использование",
        [*introduction, *chapters, *conclusion],
        implementation_blocks,
        bool(implementation_blocks),
    )
    return fragments


def _flatten_blocks(fragments: list[dict]) -> list[dict]:
    return [block for fragment in fragments for block in fragment.get("blocks", [])]


def _derive_chapter_conclusion(chapter: dict, index: int) -> dict | None:
    heading = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s*)?выводы(?:\s+по\s+главе)?\.?$", re.I)
    chapter_blocks = chapter.get("blocks", [])
    start = next((i for i, block in enumerate(chapter_blocks) if heading.match(block.get("text", "").strip())), -1)
    if start < 0:
        return None
    return {
        "id": f"{chapter.get('id')}-conclusions",
        "type": "chapter_conclusions",
        "selector": "chapter_conclusions",
        "label": f"Выводы: {chapter.get('label') or f'глава {index + 1}'}",
        "blocks": chapter_blocks[start:],
        "complete": bool(chapter.get("complete")),
    }


def _select_chapter_summary(blocks: list[dict]) -> list[dict]:
    keyword = re.compile(r"предложен|разработан|прототип|аналог|эксперимент|результат|вывод|сравнен|превосход|отлича", re.I)
    selected = [*blocks[:4], *[block for block in blocks if keyword.search(block.get("text", ""))], *blocks[-10:]]
    return _unique_blocks(selected)[:45]


def _select_chapter_evidence(blocks: list[dict]) -> list[dict]:
    keyword = re.compile(r"аналог|прототип|базов(?:ый|ая|ое)|сравнен|недостат|отлича|предложен|результат|эксперимент", re.I)
    selected = [*blocks[:3], *[block for block in blocks if keyword.search(block.get("text", ""))], *blocks[-12:]]
    return _unique_blocks(selected)[:55]


def _split_defense_statements(fragments: list[dict]) -> list[dict]:
    blocks = _unique_blocks(_flatten_blocks(fragments))
    statements: list[dict] = []
    for item in collect_unique_numbered_items(blocks):
        source = dict(item.get("source") or {})
        number = item.get("number")
        source["id"] = f"{source.get('id')}-statement-{number}"
        source["location"] = f"{source.get('location', '')}, положение {number}"
        source["text"] = f"{number}. {item.get('text', '')}"
        statements.append(source)
    return statements


def _infer_result_kind(text: str) -> str:
    normalized = re.sub(r"^\s*\d+\.\s*", "", text).lower()
    if re.search(r"^классификаци|систематизаци", normalized):
        return "classification"
    if re.search(r"^бенчмарк|^набор\s+данных|^датасет|протокол\s+оцен", normalized):
        return "benchmark"
    if re.search(r"^программ(?:ный|ная)\s+(?:комплекс|система)|^фреймворк", normalized):
        return "software"
    if re.search(r"^модел", normalized):
        return "model"
    if re.search(r"^метод|^алгоритм|^технолог", normalized):
        return "method"
    return "other"


def _fallback_selectors(rule: dict) -> list[str]:
    scope = rule.get("scope")
    if scope == "title":
        return ["title"]
    if scope == "goal":
        return ["title_goal"]
    if scope == "defense_statements":
        return ["defense_statements"]
    if scope == "chapter":
        return ["chapter"]
    if scope == "bibliography":
        return ["bibliography"]
    return ["major_sections"]


def _fallback_spec(rule: dict) -> dict:
    if rule.get("detectorId") or rule.get("mode") == "deterministic":
        return {"strategy": "deterministic"}
    if rule.get("mode") == "structural":
        return {"strategy": "structural"}
    if rule.get("mode") == "manual" or rule.get("scope") in {"presentation", "defense", "process"}:
        return {"strategy": "manual", "reason": "Правило требует другого артефакта или ручного наблюдения."}
    return {
        "strategy": "llm",
        "selectors": _fallback_selectors(rule),
        "exhaustive": rule.get("scope") != "document",
    }


def _expand_selectors(selectors: list[str], fragments: list[dict]) -> list[str]:
    result: list[str] = []
    for selector in selectors:
        if selector == "defense_chapter_matrix":
            result.extend(item["id"] for item in fragments if item.get("selector") == "defense_chapter_matrix")
            continue
        if selector == "major_sections":
            result.extend(item["id"] for item in fragments if item.get("type") in {"introduction", "chapter", "conclusion"})
            continue
        if selector == "chapter_conclusions":
            result.extend(item["id"] for item in fragments if item.get("type") == "chapter_conclusions" or item.get("selector") == "chapter_conclusions")
            continue
        virtual = next((item for item in fragments if item.get("selector") == selector and item.get("type") == "virtual"), None)
        if virtual:
            result.append(virtual["id"])
            continue
        if selector in DIRECT_SELECTORS:
            result.extend(item["id"] for item in fragments if item.get("type") == selector)
    return list(dict.fromkeys(result))


def _route_rule(rule: dict, fragments: list[dict], explicit_spec: dict | None) -> dict:
    spec = dict(explicit_spec or _fallback_spec(rule))
    strategy = spec.get("strategy", _fallback_spec(rule).get("strategy"))

    # This safety decision existed in the stabilized OSA.Edu branch before migration:
    # the four abbreviation checks remain deliberately uncertain until a reliable detector exists.
    if rule.get("id") in ABBREVIATION_RULE_IDS:
        strategy = "structural"
        spec["reason"] = "Автоматическая проверка аббревиатур временно отключена из-за недостаточной надёжности."

    selectors = spec.get("selectors") or _fallback_selectors(rule)
    fragment_ids = _expand_selectors(selectors, fragments) if strategy == "llm" else []
    by_id = {fragment.get("id"): fragment for fragment in fragments}
    exhaustive = bool(spec.get("exhaustive")) and bool(fragment_ids) and all(bool(by_id.get(fid, {}).get("complete")) for fid in fragment_ids)

    routed = {
        "rule": rule,
        "strategy": strategy,
        "fragmentIds": fragment_ids,
        "exhaustive": exhaustive,
        "allowPass": spec.get("allowPass") is not False,
        "reason": spec.get("reason"),
        "explicit": explicit_spec is not None,
    }
    # Compatibility extension: if a routing profile explicitly overrides a detector,
    # the checker honours it without changing the public API contract.
    if spec.get("detectorId") or rule.get("detectorId"):
        routed["detectorId"] = spec.get("detectorId") or rule.get("detectorId")
    return routed


async def build_routing(*, document: dict, map_value: dict, rules: list[dict]) -> dict:
    fragments = _build_fragments(document, map_value)
    config = _load_config()
    specs = config.get("rules") or {}
    routed = [_route_rule(rule, fragments, specs.get(rule.get("id"))) for rule in rules]
    explicit_rules = sum(1 for item in routed if item.get("explicit"))
    fallback_rules = len(routed) - explicit_rules
    strategy = "explicit-map" if fallback_rules == 0 else "scope-fallback" if explicit_rules == 0 else "mixed"
    return {
        "fragments": fragments,
        "routed": routed,
        "strategy": strategy,
        "explicitRules": explicit_rules,
        "fallbackRules": fallback_rules,
        "usage": empty_usage(),
    }
