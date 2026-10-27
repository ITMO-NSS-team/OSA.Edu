from __future__ import annotations

from typing import Any, Callable

import regex as re


def _early_document_text(document: dict[str, Any]) -> str:
    """Only cover/title metadata, never random references to other theses."""
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
    return re.sub(r'\s+', ' ', ' '.join(early)).lower().replace('ё', 'е')


def _confirmed_context(document: dict[str, Any]) -> str | None:
    """Accept only explicitly confirmed user metadata; never guess a degree."""
    context = document.get('workContext')
    if not isinstance(context, dict) or context.get('confirmed') is not True:
        return None
    kind = str(context.get('educationLevel') or '').lower().strip().replace('-', '_')
    aliases = {
        'bachelor': 'bachelor', 'bachelors': 'bachelor', 'бакалавриат': 'bachelor',
        'master': 'master', 'masters': 'master', 'магистратура': 'master',
        'specialist': 'specialist', 'специалитет': 'specialist',
        'candidate': 'candidate', 'phd': 'candidate', 'кандидатская': 'candidate',
        'doctor': 'doctor', 'doctoral': 'doctor', 'докторская': 'doctor',
    }
    return aliases.get(kind)


def _degree(document: dict[str, Any]) -> tuple[str | None, str | None]:
    level = _confirmed_context(document)
    if level:
        return level, 'Подтверждённый пользователем уровень работы.'
    early = _early_document_text(document)
    # A master's dissertation is a VKR, not a dissertation for a PhD degree.
    if re.search(r'магистерск(?:ая|ой|ую)\s+диссертаци|master[’\']?s?\s+thesis|master\s+dissertation|магистратур', early, re.I):
        return 'master', 'На начальных страницах прямо указан уровень магистратуры.'
    if re.search(r'бакалаврск(?:ая|ой|ую)\s+(?:работ|вкр)|бакалавриат|bachelor[’\']?s?\s+thesis', early, re.I):
        return 'bachelor', 'На начальных страницах прямо указан бакалавриат.'
    if re.search(r'дипломн(?:ая|ой)\s+работа\s+специалиста|специалитет', early, re.I):
        return 'specialist', 'На начальных страницах прямо указан специалитет.'
    # A degree claim is essential: the title "dissertation" alone proves nothing.
    if re.search(r'(?:кандидат(?:а|ской)?\s+(?:технических|физико-математических|[а-я-]+)?\s*наук|диссертаци\w*\s+на\s+соискани\w*\s+учен\w*\s+степен\w*\s+кандидата)', early, re.I):
        return 'candidate', 'На начальных страницах прямо указана степень кандидата наук.'
    if re.search(r'(?:доктор(?:а|ской)?\s+(?:технических|физико-математических|[а-я-]+)?\s*наук|диссертаци\w*\s+на\s+соискани\w*\s+учен\w*\s+степен\w*\s+доктора)', early, re.I):
        return 'doctor', 'На начальных страницах прямо указана степень доктора наук.'
    if re.search(r'выпускн\w*\s+квалификационн\w*\s+работ', early, re.I):
        return 'vkr_unknown', 'Подтверждена ВКР, но её уровень не установлен.'
    return None, None


def scientific_dissertation(document: dict[str, Any]) -> tuple[bool | None, str | None]:
    """Shalyto 4.16: bachelor/master VKR need not have defense statements.

    False means author's *scientific dissertation* advice is inapplicable, not
    that some university-specific VKR regulation cannot impose a requirement.
    """
    degree, reason = _degree(document)
    if degree in {'candidate', 'doctor'}:
        return True, reason
    if degree in {'bachelor', 'master', 'specialist', 'vkr_unknown'}:
        return False, ('Совет Шалыто адресован научной диссертации; для бакалаврской и '
                       'магистерской ВКР положения на защиту не обязательны (п. 4.16). '
                       'Отдельные требования университета необходимо проверять самостоятельно.')
    return None, 'Уровень работы не установлен: нельзя надёжно решить, применим ли совет к научной диссертации.'


def technical_dissertation(document: dict[str, Any]) -> tuple[bool | None, str | None]:
    scientific, reason = scientific_dissertation(document)
    if scientific is False:
        return False, reason
    if scientific is None:
        return None, reason
    early = _early_document_text(document)
    match = re.search(r'(?:кандидат(?:а)?|доктор(?:а)?)\s+([^.;:]{2,80}?)\s+наук\b', early, re.I)
    if not match:
        # The user's degree confirmation does not establish the scientific branch.
        return None, 'На титульных страницах не установлена отрасль науки.'
    branch = re.sub(r'\s+', ' ', match.group(1)).strip(' —-,:;')
    if 'техническ' in branch:
        return True, None
    return False, 'Правило относится только к техническим наукам; явно указана другая отрасль науки.'


_PREDICATES: dict[str, Callable[[dict[str, Any]], tuple[bool | None, str | None]]] = {
    'technical_dissertation': technical_dissertation,
    'scientific_dissertation': scientific_dissertation,
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
