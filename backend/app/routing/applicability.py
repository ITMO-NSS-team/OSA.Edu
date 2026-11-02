from __future__ import annotations

from typing import Any, Callable

import regex as re


_PERSON_ROLE = re.compile(
    r'\b(?:научн\w*\s+(?:руководител\w*|консультант\w*)|руководител\w*|'
    r'консультант\w*|рецензент\w*|(?:официальн\w*\s+)?оппонент\w*|'
    r'заведующ\w*\s+кафедр\w*|утверждаю|согласовано)\b', re.I,
)
_WORK_TITLE = re.compile(
    r'^\s*(?:выпускн\w*|вкр|магистерск\w*|бакалаврск\w*|кандидатск\w*|'
    r'докторск\w*|дипломн\w*|(?:автореферат\s+)?диссертаци[яи]|'
    r'(?:master|bachelor)[’\']?s?\s+(?:thesis|dissertation))\b', re.I,
)


def _early_document_text(document: dict[str, Any]) -> str:
    """Title evidence, excluding personnel sections and their continuations.

    PDF blocks may split a role, a person's name and their degree. Once a
    personnel section starts, ignore its continuation until a new work title
    or page, rather than only dropping the line naming the role.
    """
    early: list[str] = []
    personnel_page = object()
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
        for line in str(block.get('text') or '').splitlines():
            if _WORK_TITLE.match(line):
                personnel_page = object()
            if page == personnel_page:
                continue
            role = _PERSON_ROLE.search(line)
            if role:
                line = line[:role.start()]
                personnel_page = page
            early.append(line)
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


_DISSERTATION_CLAIM = re.compile(
    r'\bдиссертаци[яи]\s+на\s+соискание\s+(?:ученой\s+)?степени\s+'
    r'(?P<degree>кандидата|доктора)\b'
    r'(?:\s+(?P<branch>[а-я-]+(?:\s+[а-я-]+){0,3}?)\s+наук\b)?', re.I,
)


def _degree(document: dict[str, Any]) -> tuple[str | None, str | None]:
    level = _confirmed_context(document)
    if level:
        return level, 'Подтверждённый пользователем уровень работы.'
    early = _early_document_text(document)
    # Require a work title or an explicit qualification, never a bare degree
    # attached to a person. Collect all evidence so conflicting titles stay unknown.
    patterns = {
        'master': (r'магистерск(?:ая|ой|ую)\s+(?:диссертаци|(?:выпускн\w*\s+квалификационн\w*\s+)?работ|вкр)|'
                   r'master[’\']?s?\s+thesis|master\s+dissertation|'
                   r'(?:квалификаци\w*|уровень\s+(?:высшего\s+)?образования)\s*[:—–-]?\s*(?:магистр|магистратура)\b'),
        'bachelor': (r'бакалаврск(?:ая|ой|ую)\s+(?:(?:выпускн\w*\s+квалификационн\w*\s+)?работ|вкр)|'
                     r'bachelor[’\']?s?\s+thesis|'
                     r'(?:квалификаци\w*|уровень\s+(?:высшего\s+)?образования)\s*[:—–-]?\s*(?:бакалавр|бакалавриат)\b'),
        'specialist': r'дипломн(?:ая|ой)\s+работа\s+специалиста|специалитет',
        'candidate': r'кандидатск(?:ая|ой|ую)\s+диссертаци',
        'doctor': r'докторск(?:ая|ой|ую)\s+диссертаци',
    }
    levels = {kind for kind, pattern in patterns.items() if re.search(pattern, early, re.I)}
    levels.update('candidate' if match['degree'] == 'кандидата' else 'doctor'
                  for match in _DISSERTATION_CLAIM.finditer(early))
    if len(levels) > 1:
        return None, 'На начальных страницах найдены противоречивые сведения об уровне работы.'
    if levels:
        return levels.pop(), 'На начальных страницах прямо указан уровень самой работы.'
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
    return None, reason or 'Уровень работы не установлен: нельзя надёжно решить, применим ли совет к научной диссертации.'


def technical_dissertation(document: dict[str, Any]) -> tuple[bool | None, str | None]:
    scientific, reason = scientific_dissertation(document)
    if scientific is False:
        return False, reason
    if scientific is None:
        return None, reason
    early = _early_document_text(document)
    degree, _ = _degree(document)
    branches = {match['branch'] for match in _DISSERTATION_CLAIM.finditer(early)
                if match['branch'] and ('candidate' if match['degree'] == 'кандидата' else 'doctor') == degree}
    if len(branches) != 1:
        # The user's degree confirmation does not establish the scientific branch.
        return None, 'На титульных страницах не установлена отрасль науки.'
    branch = branches.pop()
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
