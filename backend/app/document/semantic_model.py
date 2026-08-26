from __future__ import annotations

from typing import Any

from ..domain.models import SemanticDocumentModel, SemanticRelationModel, SemanticSectionModel
from .semantic_ranges import trim_blocks_for_element
from .numbered_items import collect_unique_defense_items
from .title import extract_best_title
from .units import canonical_elements


def build_semantic_document(document: dict[str, Any], map_value: dict[str, Any]) -> SemanticDocumentModel:
    """Build the canonical semantic view used by routing.

    The structure LLM already produced sections and (for new maps) semantic
    statement→chapter relations. No extra LLM request is introduced here.
    """
    sections: list[SemanticSectionModel] = []
    for element in map_value.get('elements', []):
        if not isinstance(element, dict) or not element.get('id'):
            continue
        sections.append(SemanticSectionModel(
            id=str(element.get('id')),
            type=str(element.get('type') or 'other'),
            label=str(element.get('label') or element.get('type') or 'other'),
            startBlockId=str(element.get('startBlockId') or ''),
            endBlockId=str(element.get('endBlockId') or ''),
            blockIds=[str(x) for x in element.get('blockIds', []) if x],
            complete=element.get('state') == 'confirmed',
            state=str(element.get('state') or 'ambiguous'),
            canonicalRole=element.get('canonicalRole'),
        ))
    relations: list[SemanticRelationModel] = []
    for raw in map_value.get('relations', []):
        if not isinstance(raw, dict):
            continue
        try:
            relations.append(SemanticRelationModel.model_validate(raw))
        except Exception:
            continue
    block_index = {str(block.get('id')): i for i, block in enumerate(document.get('blocks', []))}
    section_by_id = {section.id: section for section in sections}

    # Structural chapter→conclusion edges are derived once in the semantic graph.
    # This is positional document structure, not a semantic verdict.
    chapters = [section for section in sections if section.type == 'chapter']
    for conclusion in [section for section in sections if section.type == 'chapter_conclusions']:
        conclusion_start = block_index.get(conclusion.startBlockId)
        if conclusion_start is None:
            continue
        preceding = [
            (block_index.get(chapter.startBlockId), chapter)
            for chapter in chapters
            if block_index.get(chapter.startBlockId) is not None and block_index.get(chapter.startBlockId) <= conclusion_start
        ]
        if not preceding:
            continue
        chapter = max(preceding, key=lambda row: row[0])[1]
        relations.append(SemanticRelationModel(
            type='chapter_conclusion',
            sourceSectionId=chapter.id,
            targetSectionId=conclusion.id,
            role='conclusion',
            confidence=1.0,
            state='confirmed' if chapter.complete and conclusion.complete else 'ambiguous',
            source='document_structure',
        ))

    # Individual defense statements are semantic entities reused by routing.
    defense_statements: list[dict[str, Any]] = []
    blocks = document.get('blocks', [])
    canonical_defense = next((
        section for section in sections
        if section.type == 'defense_statements' and section.canonicalRole != 'secondary_copy'
    ), None)
    if canonical_defense:
        start = block_index.get(canonical_defense.startBlockId)
        end = block_index.get(canonical_defense.endBlockId)
        if start is not None and end is not None and start <= end:
            scoped = trim_blocks_for_element('defense_statements', blocks[start:end + 1])
            for index, item in enumerate(collect_unique_defense_items(scoped)):
                source = dict(item.get('source') or {})
                number = item.get('number') or index + 1
                source['id'] = f"{source.get('id')}-statement-{number}"
                source['location'] = f"{source.get('location', '')}, положение {number}"
                source['text'] = f"{number}. {item.get('text', '')}"
                source['statementIndex'] = index
                defense_statements.append(source)

    relation_source = 'llm_document_map' if any(r.source == 'llm_document_map' for r in relations) else 'legacy_fallback'
    return SemanticDocumentModel(
        version=1,
        sections=sections,
        relations=relations,
        defenseStatements=defense_statements,
        relationSource=relation_source,
    )


def resolve_statement_chapter_roles(
    semantic_document: SemanticDocumentModel,
    statement_index: int,
    chapters: list[dict],
    statement_text: str,
) -> tuple[list[tuple[dict, float, str]], str, bool]:
    """Resolve a statement's chapter scope without an additional model call.

    New runs prefer relations emitted by the structure LLM. The local semantic
    linker is retained only for legacy maps that contain no relation for the
    statement. An explicitly ambiguous LLM relation is preserved as incomplete
    rather than overwritten by a heuristic guess.
    """
    chapter_by_id = {str(chapter.get('id')): chapter for chapter in chapters}
    rows = [
        relation for relation in semantic_document.relations
        if relation.type == 'defense_statement_primary_chapter'
        and relation.statementIndex == statement_index
    ]
    if rows:
        resolved: list[tuple[dict, float, str]] = []
        complete = True
        for relation in rows:
            chapter = chapter_by_id.get(str(relation.targetSectionId or ''))
            if chapter is None:
                complete = False
                continue
            resolved.append((chapter, float(relation.confidence), relation.role or 'primary'))
            complete = complete and relation.state == 'confirmed'
        # Do not let a local matcher override a structure-model ambiguity.
        return resolved, 'llm_document_map', bool(resolved and complete)

    from .chapter_linker import statement_chapter_roles

    resolved = statement_chapter_roles(statement_text, chapters)
    return resolved, 'legacy_semantic_fallback', bool(resolved)


def hydrate_legacy_fields(document: dict[str, Any]) -> None:
    """Compatibility adapter from canonical Document Map to legacy checker fields.

    Existing deterministic/structural engines still consume document['fields'].
    Keeping the adapter here makes the dependency direction explicit: map → fields,
    never fields → semantic map.
    """
    map_value = document.get('map')
    blocks = document.get('blocks', [])
    if not map_value:
        return
    index = {block['id']: i for i, block in enumerate(blocks)}

    def elements(element_type: str):
        if element_type in {'goal', 'tasks', 'defense_statements'}:
            return canonical_elements(map_value.get('elements', []), element_type)
        return [item for item in map_value.get('elements', []) if item.get('type') == element_type]

    def element_blocks(element_type: str):
        out: list[dict] = []
        for element in elements(element_type):
            start = index.get(element.get('startBlockId'))
            end = index.get(element.get('endBlockId'))
            if start is not None and end is not None and start <= end:
                out += trim_blocks_for_element(element_type, blocks[start:end + 1])
        return out

    def precise(element_type: str):
        candidates = elements(element_type)
        if not candidates:
            return None
        element = candidates[0]
        start = index.get(element.get('startBlockId'))
        end = index.get(element.get('endBlockId'))
        if start is None or end is None or start > end:
            return None
        source = blocks[start]
        range_blocks = blocks[start:end + 1]
        if element_type == 'title':
            found = extract_best_title(range_blocks, blocks) or document.get('fields', {}).get('title')
            if found:
                return found
            label = str(element.get('label') or '').strip()
            generic = any(token in label.lower() for token in ('титульн', 'title page', 'название работы'))
            return {**source, 'text': label} if label and not generic else None
        quote = str(element.get('quote') or '').strip()
        return {**source, 'text': quote} if quote else source

    fields = document.setdefault('fields', {})
    title = precise('title') or fields.get('title')
    goal = precise('goal') or fields.get('goal')
    tasks = element_blocks('tasks')
    defense = element_blocks('defense_statements')
    bibliography = element_blocks('bibliography')
    chapters = []
    for element in elements('chapter'):
        position = index.get(element.get('startBlockId'))
        if position is not None:
            chapters.append(blocks[position])
    conclusions = []
    for element in [x for x in map_value.get('elements', []) if x.get('type') in {'chapter_conclusions', 'conclusion'}]:
        position = index.get(element.get('startBlockId'))
        if position is not None:
            conclusions.append(blocks[position])
    fields.update({
        'title': title,
        'goal': goal,
        'tasks': tasks or fields.get('tasks', []),
        'defenseStatements': defense or fields.get('defenseStatements', []),
        'chapterHeadings': chapters,
        'conclusionHeadings': [*conclusions, *fields.get('conclusionHeadings', [])],
        'bibliographyBlocks': bibliography or fields.get('bibliographyBlocks', []),
    })
