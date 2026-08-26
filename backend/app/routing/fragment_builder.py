from __future__ import annotations

"""Fragment projection built from the canonical Semantic Document.

Fragments are bounded execution views for rule engines. This module does not own
rule metadata or normative decisions.
"""

import regex as re

from ..document.chapter_linker import infer_result_kind
from ..document.semantic_model import build_semantic_document, resolve_statement_chapter_roles
from ..document.semantic_ranges import trim_blocks_for_element

def _unique_blocks(blocks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for block in blocks:
        block_id = str(block.get("id", ""))
        if block_id and block_id not in seen:
            seen.add(block_id)
            result.append(block)
    return result


def build_fragments(document: dict, map_value: dict) -> list[dict]:
    blocks = document.get("blocks", [])
    semantic_document = build_semantic_document(document, map_value)
    block_index = {block.get("id"): index for index, block in enumerate(blocks)}
    fragments: list[dict] = []

    # Direct map fragments. IDs intentionally stay equal to DocumentMap element IDs;
    # the React client and reports can therefore refer to exactly the same semantic ranges.
    for element in map_value.get("elements", []):
        # Goal/tasks/defence can occur twice in combined synopsis+thesis PDFs.
        # The map keeps secondary copies visible for transparency, but routing
        # must use only the canonical main-work copy.
        if element.get("type") in {"goal", "tasks", "defense_statements"} and element.get("canonicalRole") == "secondary_copy":
            continue
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

    def add_virtual(
        selector: str,
        label: str,
        source: list[dict],
        selected_blocks: list[dict],
        complete: bool | None = None,
        **metadata,
    ) -> None:
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
            **metadata,
        })

    title = by_type("title")
    introduction = by_type("introduction")
    goal = by_type("goal")
    tasks = by_type("tasks")
    defense = by_type("defense_statements")
    chapters = by_type("chapter")
    explicit_conclusions = by_type("chapter_conclusions")
    conclusion = by_type("conclusion")

    # Experimental full-document semantic fragment. It mirrors the one-shot
    # structure request: every extracted block is present exactly once, while
    # type/page metadata is added later by the checker message. Abbreviation
    # rules share this selector, so the default grouped semantic pipeline sends
    # all of them in a single LLM request.
    add_virtual(
        "whole_document",
        "Полный документ для проверки аббревиатур",
        [],
        blocks,
        True,
        sourceScannedBlocks=len(blocks),
        semanticContext=(
            f"Передан весь документ: {len(blocks)} блоков. "
            "Для проверки аббревиатур учитывай порядок, тип и страницу каждого BLOCK."
        ),
    )

    conclusion_links = {
        str(relation.targetSectionId): str(relation.sourceSectionId)
        for relation in semantic_document.relations
        if relation.type == 'chapter_conclusion' and relation.targetSectionId and relation.sourceSectionId
    }
    for conclusion_fragment in explicit_conclusions:
        linked_chapter = conclusion_links.get(str(conclusion_fragment.get('id')))
        if linked_chapter:
            conclusion_fragment['chapterId'] = linked_chapter

    derived_conclusions = list(explicit_conclusions)
    explicit_chapter_ids = {item.get("chapterId") for item in explicit_conclusions if item.get("chapterId")}
    # Keep explicit map ranges authoritative, but deterministically derive a
    # missing conclusion for an individual chapter when an actual conclusion
    # heading exists inside that chapter.  A conclusion in one chapter must not
    # disable discovery for all other chapters.
    for index, chapter in enumerate(chapters):
        if chapter.get("id") in explicit_chapter_ids:
            continue
        derived = _derive_chapter_conclusion(chapter, index)
        if derived is not None:
            derived_conclusions.append(derived)
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
    if not scientific_blocks:
        scientific_blocks = [
            block for block in blocks
            if block.get("type") in {"paragraph", "list", "heading"}
            and block.get("type") not in {"bibliography", "toc"}
        ]
    add_virtual("scientific_core", "Научное ядро работы", scientific_source, scientific_blocks, bool(scientific_blocks))

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

    statements = list(semantic_document.defenseStatements)
    if statements and defense:
        add_virtual(
            "defense_statements_complete",
            "Полные положения, выносимые на защиту",
            defense,
            _flatten_blocks(defense),
            all(item.get("complete") for item in defense),
            semanticContext="\n".join(
                f"Положение {index + 1}: {statement.get('text', '')}"
                for index, statement in enumerate(statements)
            ),
        )

    for index, statement in enumerate(statements):
        statement_text = statement.get("text", "")
        roles, mapping_source, mapping_complete = resolve_statement_chapter_roles(semantic_document, index, chapters, statement_text)
        primary_roles = [item for item in roles if item[2] == "primary"]
        # CORE-2-3/CORE-15 ask specifically for the chapter devoted to the
        # proposition. Validation/application chapters remain useful metadata but
        # must not inflate the evidence packet or masquerade as the primary chapter.
        selected_roles = primary_roles or roles
        selected_chapters = [chapter for chapter, _score, _role in selected_roles]
        if not selected_chapters:
            continue
        primary_labels = [chapter.get("label", "") for chapter in selected_chapters]
        chapter_blocks = [
            block
            for chapter in selected_chapters
            for block in chapter.get("blocks", [])
        ]
        role_rows = [
            {
                "chapterId": chapter.get("id"),
                "label": chapter.get("label", ""),
                "role": role,
                "score": round(score, 3),
            }
            for chapter, score, role in roles
        ]
        support_text = "; ".join(
            f"{row['role']}: {row['label']}" for row in role_rows if row["role"] != "primary"
        )
        semantic_context = (
            f"Проверяемое положение {index + 1}: {statement_text}\n"
            f"Основная глава для проверки аналогов/прототипа: {' + '.join(primary_labels)}."
        )
        if support_text:
            semantic_context += f" Дополнительные связи (не основная область CORE-2-3/CORE-15): {support_text}."
        conclusion_blocks: list[dict] = []
        conclusion_complete = True
        missing_conclusion_labels: list[str] = []
        for chapter in selected_chapters:
            chapter_id = chapter.get("id")
            match = next((item for item in derived_conclusions if item.get("chapterId") == chapter_id), None)
            if match is None:
                # Compatibility fallback for old maps where the conclusion range
                # overlapped the tail of the chapter itself.
                chapter_ids = {block.get("id") for block in chapter.get("blocks", [])}
                match = next((item for item in derived_conclusions if any(block.get("id") in chapter_ids for block in item.get("blocks", []))), None)
            if match is not None:
                conclusion_blocks.extend(match.get("blocks", []))
                conclusion_complete = conclusion_complete and bool(match.get("complete"))
                continue

            # A missing dedicated conclusion is useful semantic information rather
            # than a routing failure. Route a short tail of the primary chapter as
            # an explicitly incomplete context. This makes CORE-8-2 return a
            # meaningful uncertain result instead of "required fragment missing".
            tail = chapter.get("blocks", [])[-8:]
            conclusion_blocks.extend(tail)
            conclusion_complete = False
            missing_conclusion_labels.append(str(chapter.get("label") or chapter_id or "глава"))

        if conclusion_blocks:
            context = f"Положение {index + 1}: {statement_text}"
            if missing_conclusion_labels:
                context += (
                    "\nОтдельный диапазон выводов для основной главы не найден в подтверждённой карте: "
                    + "; ".join(missing_conclusion_labels)
                    + ". Переданные блоки являются только хвостом главы; не считать их полноценными выводами."
                )
            fragments.append({
                "id": f"virtual-primary-conclusions-{index + 1}",
                "type": "virtual",
                "selector": "primary_chapter_conclusions",
                "label": f"Выводы основной главы для положения {index + 1}",
                "blocks": _unique_blocks(conclusion_blocks),
                "complete": bool(conclusion_complete and selected_chapters and mapping_complete),
                "semanticContext": context,
            })

        fragments.append({
            "id": f"virtual-defense-chapter-{index + 1}",
            "type": "virtual",
            "selector": "defense_chapter_matrix",
            "label": f"Положение {index + 1} ↔ " + " + ".join(primary_labels),
            "blocks": _unique_blocks(chapter_blocks),
            "complete": bool(
                selected_chapters
                and all(chapter.get("complete") for chapter in selected_chapters)
                and all(item.get("complete") for item in defense)
                and mapping_complete
            ),
            "resultKind": infer_result_kind(statement_text),
            "mappingSource": mapping_source,
            "semanticContext": semantic_context,
            "chapterIds": [chapter.get("id") for chapter in selected_chapters],
            "chapterRoles": role_rows,
            "mappingScores": [round(score, 3) for _chapter, score, _role in selected_roles],
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

    # Implementation/use rules are semantic. Do not pre-decide their scope with
    # a keyword regex: use a bounded structure-based view of every chapter plus
    # introduction/conclusion. This improves recall without adding an LLM request.
    implementation_blocks = _unique_blocks([
        *_flatten_blocks(introduction),
        *[block for chapter in chapters for block in _select_chapter_summary(chapter.get("blocks", []))],
        *_flatten_blocks(conclusion),
    ])
    add_virtual(
        "implementation_context",
        "Внедрение и практическое использование",
        [*introduction, *chapters, *conclusion],
        implementation_blocks,
        bool(implementation_blocks),
        sourceScannedBlocks=len(blocks),
        semanticContext=(
            "Передан широкий структурный контекст введения, всех глав и заключения без "
            "лексического pre-filter. Наличие внедрения/использования определяет semantic engine."
        ),
    )
    return fragments


def _flatten_blocks(fragments: list[dict]) -> list[dict]:
    return [block for fragment in fragments for block in fragment.get("blocks", [])]


def _derive_chapter_conclusion(chapter: dict, index: int) -> dict | None:
    heading = re.compile(
        r"^(?:\d+(?:\.\d+)*\.?\s*)?.{0,120}?\bвыводы(?:\s+(?:к|по)\s+главе(?:\s+\d+)?)?(?:\s*[:—–-]\s*.{1,120})?\.?$",
        re.I,
    )
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


def _structural_chapter_sample(blocks: list[dict], *, limit: int, head: int, tail: int) -> list[dict]:
    """Broad structure-only sampling for generic semantic rules.

    No domain keywords are used here. The final meaning is judged by the LLM;
    Python only keeps headings, boundaries and evenly spaced interior context.
    """
    if len(blocks) <= limit:
        return _unique_blocks(blocks)
    headings = [block for block in blocks if block.get("type") == "heading"]
    interior = blocks[head:max(head, len(blocks) - tail)]
    budget = max(1, limit - head - tail - len(headings))
    step = max(1, len(interior) // budget) if interior else 1
    sampled = interior[::step][:budget]
    return _unique_blocks([*blocks[:head], *headings, *sampled, *blocks[-tail:]])[:limit]


def _select_chapter_summary(blocks: list[dict]) -> list[dict]:
    return _structural_chapter_sample(blocks, limit=45, head=6, tail=12)


def _select_chapter_evidence(blocks: list[dict]) -> list[dict]:
    return _structural_chapter_sample(blocks, limit=55, head=8, tail=14)


# Backward-compatible private name used by regression tests.
_build_fragments = build_fragments
