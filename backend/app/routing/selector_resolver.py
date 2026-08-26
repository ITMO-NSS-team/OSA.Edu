from __future__ import annotations

from typing import Any

from ..domain.models import FragmentModel, RoutedRuleModel

DIRECT_SELECTORS = {
    'title', 'abstract', 'introduction', 'goal', 'tasks', 'defense_statements',
    'chapter', 'chapter_conclusions', 'conclusion', 'bibliography', 'appendices', 'other',
}


def prerequisite_failure(spec: dict | None, fragments: list[dict]) -> str | None:
    if not spec:
        return None
    missing: list[str] = []
    for artifact in spec.get('requiresArtifacts') or []:
        name = str(artifact)
        if not any(item.get('type') == name and item.get('blocks') for item in fragments):
            missing.append(name)
    for selector in spec.get('requiresCompleteSelectors') or []:
        name = str(selector)
        rows = [item for item in fragments if item.get('selector') == name]
        if not rows or not all(bool(item.get('complete')) for item in rows):
            missing.append(name)
    if not missing:
        return None
    labels = ', '.join(dict.fromkeys(missing))
    return f'Для автоматической проверки отсутствуют обязательные подтверждённые артефакты/области: {labels}.'


def fallback_selectors(rule: dict) -> list[str]:
    scope = rule.get('scope')
    if scope == 'title':
        return ['title']
    if scope == 'goal':
        return ['title_goal']
    if scope == 'defense_statements':
        return ['defense_statements']
    if scope == 'chapter':
        return ['chapter']
    if scope == 'bibliography':
        return ['bibliography']
    return ['major_sections']


def fallback_spec(rule: dict) -> dict:
    """Compatibility only for dynamic/legacy rules outside the canonical manifest."""
    engine = str(rule.get('engineKind') or rule.get('mode') or 'semantic')
    if engine == 'candidate':
        return {'strategy': 'candidate', 'candidateFamily': rule.get('candidateFamily'), 'exhaustive': True}
    if engine == 'deterministic' or rule.get('detectorId'):
        return {'strategy': 'deterministic'}
    if engine == 'structural':
        return {'strategy': 'structural'}
    if engine == 'unavailable':
        return {'strategy': 'unavailable', 'reason': 'Надёжная автоматическая проверка недоступна.'}
    if engine == 'manual':
        return {'strategy': 'manual', 'reason': 'Правило требует другого артефакта или ручного наблюдения.'}
    return {'strategy': 'llm', 'selectors': fallback_selectors(rule), 'exhaustive': rule.get('scope') != 'document'}


def expand_selectors(selectors: list[str], fragments: list[dict]) -> list[str]:
    result: list[str] = []
    for selector in selectors:
        if selector == 'defense_chapter_matrix':
            result.extend(item['id'] for item in fragments if item.get('selector') == 'defense_chapter_matrix')
            continue
        if selector == 'major_sections':
            result.extend(item['id'] for item in fragments if item.get('type') in {'introduction', 'chapter', 'conclusion'})
            continue
        if selector == 'chapter_conclusions':
            result.extend(item['id'] for item in fragments if item.get('type') == 'chapter_conclusions' or item.get('selector') == 'chapter_conclusions')
            continue
        if selector == 'primary_chapter_conclusions':
            result.extend(item['id'] for item in fragments if item.get('selector') == 'primary_chapter_conclusions')
            continue
        virtual = next((item for item in fragments if item.get('selector') == selector and item.get('type') == 'virtual'), None)
        if virtual:
            result.append(virtual['id'])
            continue
        if selector in DIRECT_SELECTORS:
            result.extend(item['id'] for item in fragments if item.get('type') == selector)
    return list(dict.fromkeys(result))


def route_rule(rule: dict, fragments: list[dict], spec: dict | None = None, *, explicit: bool = True) -> dict:
    selected_spec = dict(spec or fallback_spec(rule))
    strategy = str(selected_spec.get('strategy') or fallback_spec(rule).get('strategy'))
    selectors = selected_spec.get('selectors') or fallback_selectors(rule)
    fragment_ids = expand_selectors(selectors, fragments) if strategy == 'llm' else []
    by_id = {fragment.get('id'): fragment for fragment in fragments}
    if strategy == 'candidate':
        exhaustive = bool(selected_spec.get('exhaustive', True))
    else:
        exhaustive = bool(selected_spec.get('exhaustive')) and bool(fragment_ids) and all(bool(by_id.get(fid, {}).get('complete')) for fid in fragment_ids)

    routed = {
        'rule': rule,
        'strategy': strategy,
        'fragmentIds': fragment_ids,
        'exhaustive': exhaustive,
        'allowPass': selected_spec.get('allowPass') is not False,
        'reason': selected_spec.get('reason'),
        'explicit': explicit,
    }
    if strategy == 'candidate':
        routed['candidateFamily'] = selected_spec.get('candidateFamily') or rule.get('candidateFamily')
    if selected_spec.get('detectorId') or rule.get('detectorId'):
        routed['detectorId'] = selected_spec.get('detectorId') or rule.get('detectorId')
    # Validate the critical routing boundary while preserving dict compatibility.
    return RoutedRuleModel.model_validate(routed).model_dump(exclude_none=True)


def validate_fragments(fragments: list[dict]) -> list[dict]:
    return [FragmentModel.model_validate(item).model_dump(exclude_none=True) for item in fragments]
