from __future__ import annotations

from typing import Any

import regex as re

from .numbered_items import collect_unique_defense_items, collect_unique_numbered_items

_CANONICAL_TYPES = {"goal", "tasks", "defense_statements"}
_DEFENSE_ANCHOR = re.compile(
    r"(?:положени\p{L}*\s*(?:,\s*)?(?:выносим\p{L}*\s+на\s+защит\p{L}*|на\s+защит\p{L}*)|"
    r"(?:statements?|provisions?)\s+(?:submitted\s+)?(?:for|to)\s+(?:the\s+)?defen[cs]e)",
    re.I,
)
_GOAL_ANCHOR = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s*)?(?:цель(?:ю)?\s+(?:диссертационной\s+)?(?:работы|исследования)|research\s+goal)\b",
    re.I,
)
_TASKS_ANCHOR = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s*)?(?:задач(?:и|и\s+работы|и\s+исследования)|research\s+tasks?)\b",
    re.I,
)
_TASKS_SENTENCE = re.compile(
    r"(?:для\s+достижения\s+(?:поставленной\s+)?цели.{0,120}(?:задач|необходимо\s+(?:решить|выполнить))|"
    r"(?:следующ\p{L}*|поставлен\p{L}*)\s+задач\p{L}*)",
    re.I,
)
_STOP_SECTION = re.compile(
    r"^(?:научн\p{L}*\s+новизн\p{L}*|теоретическ\p{L}*\s+значим\p{L}*|практическ\p{L}*\s+значим\p{L}*|"
    r"положени\p{L}*\s*(?:,\s*)?(?:выносим\p{L}*\s+на\s+защит\p{L}*|на\s+защит\p{L}*)|"
    r"соответств\p{L}*\s+паспорт\p{L}*|структур\p{L}*\s+и\s+объ[её]м|"
    r"scientific\s+novelty|theoretical\s+significance|practical\s+significance|chapter\s+\d+|глава\s+\d+)\b",
    re.I,
)
_NOVELTY = re.compile(r"(?:научн\p{L}*\s+новизн\p{L}*|scientific\s+novelty)", re.I)


def _idx(index: dict[str, int], element: dict[str, Any], field: str = "startBlockId") -> int | None:
    return index.get(str(element.get(field) or ""))


def _text(block: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(block.get("text") or "")).strip()


def _main_intro(elements: list[dict[str, Any]], index: dict[str, int]) -> dict[str, Any] | None:
    intros = [item for item in elements if item.get("type") == "introduction" and _idx(index, item) is not None]
    chapters = [item for item in elements if item.get("type") == "chapter" and _idx(index, item) is not None]
    if not intros:
        return None
    first_chapter = min((_idx(index, item) for item in chapters), default=None)
    before = [item for item in intros if first_chapter is None or (_idx(index, item) or 0) < first_chapter]
    if before:
        # The actual thesis introduction is normally the last introduction before
        # the first chapter.  This correctly separates a synopsis/summary placed
        # at the beginning of a combined PDF from the main dissertation.
        return max(before, key=lambda item: _idx(index, item) or -1)
    return max(intros, key=lambda item: _idx(index, item) or -1)


def _main_bounds(elements: list[dict[str, Any]], index: dict[str, int], block_count: int) -> tuple[int, int] | None:
    intro = _main_intro(elements, index)
    if intro is None:
        return None
    start = _idx(index, intro)
    if start is None:
        return None
    end = _idx(index, intro, "endBlockId")
    if end is None:
        end = start
    # Canonical goal/tasks/defense are expected inside the main introduction.
    return start, min(block_count - 1, end)


def _range_has_defense_anchor(blocks: list[dict[str, Any]]) -> bool:
    return any(_DEFENSE_ANCHOR.search(_text(block)) for block in blocks[:6])


def _element_range(element: dict[str, Any], blocks: list[dict[str, Any]], index: dict[str, int]) -> list[dict[str, Any]]:
    start = _idx(index, element)
    end = _idx(index, element, "endBlockId")
    if start is None or end is None or start > end:
        return []
    return blocks[start:end + 1]


def _make_element(
    element_type: str,
    label: str,
    start: int,
    end: int,
    blocks: list[dict[str, Any]],
    *,
    note: str | None = None,
) -> dict[str, Any]:
    range_blocks = blocks[start:end + 1]
    pages = list(dict.fromkeys(block.get("page") for block in range_blocks if isinstance(block.get("page"), int)))
    first = range_blocks[0]
    text = " ".join(_text(block) for block in range_blocks)
    result: dict[str, Any] = {
        "id": f"section-canonical-{element_type}",
        "type": element_type,
        "label": label,
        "startBlockId": first.get("id"),
        "endBlockId": range_blocks[-1].get("id"),
        "blockIds": [first.get("id")],
        "pages": pages,
        "text": text[:900] + ("…" if len(text) > 900 else ""),
        "quote": _text(first)[:500],
        "confidence": 1.0,
        "state": "confirmed",
        "source": "deterministic",
        "documentUnit": "main_work",
        "canonicalRole": "canonical",
    }
    if note:
        result["note"] = note
    return result


def _find_goal(blocks: list[dict[str, Any]], start: int, end: int) -> tuple[int, int] | None:
    for pos in range(start, end + 1):
        text = _text(blocks[pos])
        if not _GOAL_ANCHOR.search(text):
            continue
        target_end = pos
        # A short heading such as «Цель работы» normally has the content in the
        # next paragraph.  Do not consume the following «Задачи работы» heading.
        if (blocks[pos].get("type") == "heading" or len(text) < 80) and pos + 1 <= end:
            nxt = _text(blocks[pos + 1])
            if nxt and not _TASKS_ANCHOR.search(nxt) and not _STOP_SECTION.search(nxt):
                target_end = pos + 1
        return pos, target_end
    return None


def _list_end(blocks: list[dict[str, Any]], anchor: int, end: int, *, defense: bool = False) -> int:
    pos = anchor
    saw_item = False
    for current in range(anchor + 1, end + 1):
        block = blocks[current]
        text = _text(block)
        if not text:
            continue
        if block.get("type") == "heading" and _STOP_SECTION.search(text):
            break
        if not defense and _STOP_SECTION.search(text):
            break
        if defense and current > anchor + 1 and block.get("type") == "heading":
            break
        if block.get("type") == "list" or re.match(r"^\s*(?:\(?\d{1,2}\)?[.)]|[—–•-])\s+", text):
            saw_item = True
            pos = current
            continue
        # A wrapped continuation paragraph immediately after a list item belongs
        # to the same semantic item.  Bashkova has exactly this form for P1.
        if saw_item and block.get("type") == "paragraph" and current == pos + 1 and not _STOP_SECTION.search(text):
            pos = current
            continue
        if saw_item:
            break
        # Introductory sentence between heading and numbered items is allowed.
        if current <= anchor + 2 and block.get("type") == "paragraph":
            pos = current
            continue
        break
    return pos


def _find_tasks(blocks: list[dict[str, Any]], start: int, end: int) -> tuple[int, int] | None:
    for pos in range(start, end + 1):
        text = _text(blocks[pos])
        if not (_TASKS_ANCHOR.search(text) or _TASKS_SENTENCE.search(text)):
            continue
        target_end = _list_end(blocks, pos, end, defense=False)
        if target_end <= pos:
            continue
        items = collect_unique_numbered_items(blocks[pos:target_end + 1])
        if len(items) >= 2:
            return pos, target_end
    return None


def _find_defense(blocks: list[dict[str, Any]], start: int, end: int) -> tuple[int, int] | None:
    for pos in range(start, end + 1):
        text = _text(blocks[pos])
        if not _DEFENSE_ANCHOR.search(text):
            continue
        target_end = _list_end(blocks, pos, end, defense=True)
        if target_end <= pos:
            continue
        items = collect_unique_defense_items(blocks[pos:target_end + 1])
        if len(items) >= 1:
            return pos, target_end
    return None


def _overlaps(start: int, end: int, bounds: tuple[int, int]) -> bool:
    return start <= bounds[1] and end >= bounds[0]


def canonicalize_document_units(
    elements: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    index: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate the main thesis from synopsis/front matter and pick canonical fields.

    Combined PDF files often contain a synopsis/summary with duplicated goal,
    tasks and defence statements before the actual dissertation.  Downstream
    semantic checks must use one coherent unit: the introduction and chapters of
    the main work.  This pass keeps secondary copies visible in DocumentMap but
    marks them as non-canonical; missing canonical goal/tasks/defence ranges are
    recovered deterministically from explicit anchors inside the main introduction.

    It also enforces a production safety invariant: ``Scientific Novelty`` is not
    silently promoted to ``defense_statements`` when the source text contains no
    explicit defence-statements anchor.
    """
    if not blocks or not elements:
        return elements, []
    bounds = _main_bounds(elements, index, len(blocks))
    issues: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []

    for original in elements:
        item = dict(original)
        start = _idx(index, item)
        end = _idx(index, item, "endBlockId")
        if start is None or end is None:
            prepared.append(item)
            continue
        in_main_intro = bool(bounds and _overlaps(start, end, bounds))
        if bounds and item.get("type") in _CANONICAL_TYPES:
            item["documentUnit"] = "main_work" if in_main_intro else "secondary_front_matter"
            item["canonicalRole"] = "candidate" if in_main_intro else "secondary_copy"

        if item.get("type") == "defense_statements" and item.get("source") != "user":
            range_blocks = _element_range(item, blocks, index)
            support = _range_has_defense_anchor(range_blocks)
            label_blob = f"{item.get('label','')} {item.get('quote','')} {item.get('note','')}"
            if not support:
                # This specifically prevents the Filin regression where Scientific
                # Novelty was invented as defence statements.  Production mode
                # prefers a truthful missing section over a semantically similar one.
                issues.append({
                    "code": "unsupported_defense_statements",
                    "severity": "warning",
                    "message": (
                        "Диапазон «Положения на защиту» отброшен: в исходных блоках нет явного маркера "
                        "положений, выносимых на защиту. Похожий раздел научной новизны не подменяет положения."
                        if _NOVELTY.search(label_blob)
                        else "Диапазон «Положения на защиту» не подтверждён явным маркером исходного документа и требует ручной проверки."
                    ),
                    "elementIds": [item.get("id")],
                })
                continue
        prepared.append(item)

    if not bounds:
        return prepared, issues

    # Prefer already mapped elements that live inside the main introduction.
    canonical: dict[str, dict[str, Any]] = {}
    for element_type in _CANONICAL_TYPES:
        candidates = [
            item for item in prepared
            if item.get("type") == element_type
            and _idx(index, item) is not None
            and _idx(index, item, "endBlockId") is not None
            and _overlaps(_idx(index, item) or 0, _idx(index, item, "endBlockId") or 0, bounds)
        ]
        if candidates:
            canonical[element_type] = max(candidates, key=lambda item: float(item.get("confidence") or 0.0))

    # Recover explicit canonical sections from the main introduction when the
    # structure model selected only a synopsis copy.
    finders = {
        "goal": (_find_goal, "Цель работы"),
        "tasks": (_find_tasks, "Задачи работы"),
        "defense_statements": (_find_defense, "Положения, выносимые на защиту"),
    }
    for element_type, (finder, label) in finders.items():
        if element_type in canonical:
            continue
        found = finder(blocks, bounds[0], bounds[1])
        if found is None:
            continue
        start, end = found
        auto = _make_element(
            element_type, label, start, end, blocks,
            note="Канонический диапазон восстановлен детерминированно внутри основного введения.",
        )
        prepared.append(auto)
        canonical[element_type] = auto
        issues.append({
            "code": f"canonical_{element_type}_recovered",
            "severity": "info",
            "message": f"Основной диапазон «{label}» восстановлен внутри введения основной работы; ранняя копия считается вторичной.",
            "elementIds": [auto.get("id")],
        })

    canonical_ids = {str(item.get("id")) for item in canonical.values()}
    result: list[dict[str, Any]] = []
    for item in prepared:
        item = dict(item)
        if item.get("type") in _CANONICAL_TYPES:
            if str(item.get("id")) in canonical_ids:
                item["documentUnit"] = "main_work"
                item["canonicalRole"] = "canonical"
                item["state"] = "confirmed" if item.get("state") != "ambiguous" else item.get("state")
            else:
                item["canonicalRole"] = "secondary_copy"
        result.append(item)

    # Inform the report without turning harmless duplicated synopsis fields into
    # an ambiguity that would poison semantic completeness.
    for element_type, chosen in canonical.items():
        secondary = [item for item in result if item.get("type") == element_type and item.get("canonicalRole") == "secondary_copy"]
        if secondary:
            issues.append({
                "code": f"secondary_{element_type}_copy",
                "severity": "info",
                "message": f"Найдена вторичная копия «{chosen.get('label') or element_type}» вне основной работы; для проверки используется диапазон из основного введения.",
                "elementIds": [str(item.get("id")) for item in secondary],
            })

    result.sort(key=lambda item: (_idx(index, item) if _idx(index, item) is not None else 10**9, 0 if item.get("canonicalRole") == "canonical" else 1))
    return result, issues


def canonical_elements(elements: list[dict[str, Any]], element_type: str) -> list[dict[str, Any]]:
    matching = [item for item in elements if item.get("type") == element_type]
    preferred = [item for item in matching if item.get("canonicalRole") == "canonical"]
    return preferred or [item for item in matching if item.get("canonicalRole") != "secondary_copy"] or matching
