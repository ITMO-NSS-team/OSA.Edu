from __future__ import annotations

from typing import Any, Callable

import regex as re


def technical_dissertation(document: dict[str, Any]) -> tuple[bool | None, str | None]:
    """High-confidence artifact applicability check.

    This does not judge the student's content. It only avoids applying a rule
    explicitly scoped to technical-science dissertations to an unmistakable
    master's VKR or another named science branch.
    """
    early: list[str] = []
    for index, block in enumerate(document.get('blocks', [])):
        page = block.get('page')
        if page is None:
            if index >= 50:
                break
        else:
            try:
                if int(page) > 6:
                    continue
            except (TypeError, ValueError):
                if index >= 50:
                    continue
        early.append(str(block.get('text') or ''))
    text = re.sub(r'\s+', ' ', ' '.join(early)).lower().replace('ё', 'е')

    if 'выпускная квалификационная работа' in text or re.search(r'квалификаци\p{L}*\s*:\s*магистратур', text, re.I):
        return False, 'Правило относится к диссертациям по техническим наукам; загруженный документ является магистерской ВКР.'

    match = re.search(r'(?:кандидат(?:а)?|доктор(?:а)?)\s+([^.;:]{2,80}?)\s+наук\b', text, re.I)
    if not match:
        return None, None
    branch = re.sub(r'\s+', ' ', match.group(1)).strip(' —-,:;')
    if 'техническ' in branch:
        return True, None
    return False, 'Правило относится только к диссертациям по техническим наукам; на титульной странице явно указана иная отрасль науки.'


_PREDICATES: dict[str, Callable[[dict[str, Any]], tuple[bool | None, str | None]]] = {
    'technical_dissertation': technical_dissertation,
}


def evaluate_applicability(rule: dict[str, Any], document: dict[str, Any]) -> tuple[bool | None, str | None]:
    spec = rule.get('applicability') or {}
    predicate_name = str(spec.get('predicate') or '').strip()
    if not predicate_name:
        return None, None
    predicate = _PREDICATES.get(predicate_name)
    if predicate is None:
        return None, f'Неизвестный applicability predicate: {predicate_name}.'
    return predicate(document)
