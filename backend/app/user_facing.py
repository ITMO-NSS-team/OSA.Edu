from __future__ import annotations

import re
from typing import Any

SHALYTO_ADVICE_URL = "https://is.ifmo.ru/books/universalnye-sovety-zaschischauschimsya.pdf"

# User-facing titles are intentionally short. The normative requirement remains
# untouched in rules-data and in the developer report.
_USER_TITLES = {
    "CORE-1-1": "Оформите положения как формулу изобретения",
    "CORE-1-2": "Укажите цель в каждом положении",
    "CORE-1-3": "Добавьте ограничительную часть для прототипа",
    "CORE-1-4": "Правильно оформляйте начало и конец положения",
    "CORE-1-5": "Уберите сокращения и формулы из положений",
    "CORE-1-6": "Свяжите каждое положение со своей главой",
    "CORE-2-1": "Покажите отличие от лучшего аналога",
    "CORE-2-2": "Покажите научный, а не только инженерный результат",
    "CORE-2-3": "Для каждого положения определите аналоги и прототип",
    "CORE-3-1": "Используйте безличный стиль",
    "CORE-3-2": "Пишите однозначные числительные словами",
    "CORE-3-3": "Начинайте пункты списка с прописной буквы",
    "CORE-3-4": "Завершайте пункты списка точкой",
    "CORE-3-5": "Уберите нежелательные слова и обороты",
    "CORE-3-6": "Не используйте бездоказательные утверждения очевидности",
    "CORE-3-7": "Избегайте восторженных оценок без доказательств",
    "CORE-4-1": "Расшифровывайте сокращения",
    "CORE-4-2": "Не используйте сокращения в заголовках",
    "CORE-4-3": "Поясняйте иностранные сокращения по-русски",
    "CORE-4-4": "Объясняйте непонятные специальные термины",
    "CORE-5-1": "Проверьте шрифт, кегль и интервал",
    "CORE-5-2": "Проверьте выравнивание текста",
    "CORE-5-3": "Начинайте главы с новой страницы",
    "CORE-5-4": "Правильно оформляйте нумерованные заголовки",
    "CORE-5-5": "Различайте тире и дефис",
    "CORE-5-6": "Используйте одинаковые кавычки",
    "CORE-5-7": "Проверьте пробелы в типографике",
    "CORE-6-1": "Название должно описывать результат",
    "CORE-6-2": "Избегайте расплывчатого «повышения качества»",
    "CORE-6-3": "Сократите слишком длинное название",
    "CORE-6-4": "Цель должна соответствовать названию",
    "CORE-7-1": "Сначала дайте ссылку, затем рисунок или таблицу",
    "CORE-7-2": "Правильно размещайте подписи таблиц и рисунков",
    "CORE-7-3": "Подпишите оси графиков и единицы измерения",
    "CORE-7-4": "Правильно нумеруйте формулы",
    "CORE-7-5": "Расшифровывайте обозначения после формулы",
    "CORE-8-1": "Оформляйте выводы по главам нумерованным списком",
    "CORE-8-2": "Сравнивайте результат с прототипом в выводах",
    "CORE-8-3": "Не нумеруйте заголовок «Заключение»",
    "CORE-9-1": "Оформляйте однотипные источники единообразно",
    "CORE-9-2": "Уберите лишние элементы из библиографии",
    "CORE-9-3": "Правильно оформляйте диапазон страниц",
    "CORE-9-4": "Сошлитесь в тексте на каждый источник",
    "CORE-10-1": "Согласуйте слайды и устный доклад",
    "CORE-10-2": "Рассказывайте по слайдам, а не читайте текст",
    "CORE-10-3": "Используйте достаточно слайдов для понятного доклада",
    "CORE-10-4": "Размещайте ссылки на источники прямо на слайдах",
    "CORE-10-5": "Сделайте номера слайдов хорошо читаемыми",
    "CORE-10-6": "Поясняйте жаргон и англицизмы",
    "CORE-10-7": "Объявляйте начало и конец каждого положения",
    "CORE-11-1": "Используйте букву «ё» там, где она произносится",
    "CORE-11-2": "Не начинайте предложения с нежелательных союзов",
    "CORE-11-3": "Не используйте сокращения «т.е.», «т.к.», «т.ч.»",
    "CORE-11-4": "Не пишите «а именно» после двоеточия",
    "CORE-11-5": "Перестройте пояснения со словами «то есть»",
    "CORE-11-6": "Используйте нормативные варианты слов",
    "CORE-12": "Расшифровывайте сокращения в тексте и заголовках",
    "CORE-13": "Поясняйте фрагменты программного кода",
    "CORE-14": "Подтвердите внедрение или использование результатов",
    "CORE-15": "Разбирайте аналоги и прототип в профильной главе",
    "CORE-16": "Не кодируйте смысл только цветом",
    "CORE-17": "Не пишите «см. рис.»",
    "CORE-18": "Ставьте инициалы после фамилии в библиографии",
    "CORE-19": "Правильно ставьте точку в конце текста и заголовков",
    "CORE-20": "Избегайте претенциозных слов о новизне",
}

# A few source requirements are deliberately rewritten for the author-facing
# report because the canonical wording contains terse arrows or internal jargon.
# The normative text itself is not modified.
_USER_REQUIREMENTS = {
    "CORE-3-5": (
        "Не используйте нежелательные слова и обороты: вместо «нужно» пишите «необходимо», "
        "вместо «значит» — «следовательно», вместо «заключается» — «состоит»."
    ),
}

# Short paraphrases only. They are not normative rule text and do not affect the
# checker verdict. Page numbers are 1-based physical pages in the public PDF.
_ADVICE_BY_PREFIX: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (("CORE-1", "CORE-2"), {
        "title": "Покажите защищаемый результат, а не только выполненную работу",
        "summary": "Положение должно ясно показывать результат, его отличие от известного решения и цель изменения.",
        "page": 41,
    }),
    (("CORE-15",), {
        "title": "Разбирайте аналоги и прототип в той главе, где защищается результат",
        "summary": "Профильную главу стоит начинать с постановки задачи, аналогов, выбора прототипа и его недостатков.",
        "page": 42,
    }),
    (("CORE-6",), {
        "title": "Название и цель должны описывать результат",
        "summary": "Название и цель должны быть созвучны и описывать конечный результат, а не процесс выполнения работы.",
        "page": 122,
    }),
    (("CORE-4", "CORE-12"), {
        "title": "Термины и сокращения должны быть понятны читателю",
        "summary": "Сокращения и специальные обозначения нужно расшифровывать и пояснять так, чтобы читателю не приходилось догадываться.",
        "page": 43,
    }),
    (("CORE-8",), {
        "title": "Завершайте главы ясными выводами",
        "summary": "Выводы по главам должны быть оформлены как отдельные результаты; в книге отдельно показан нумерованный формат выводов.",
        "page": 122,
    }),
    (("CORE-7", "CORE-10", "CORE-16"), {
        "title": "Визуальные элементы должны помогать докладу",
        "summary": "Схемы, графики и слайды должны быть читаемыми, связанными с изложением и подробно объяснёнными.",
        "page": 98,
    }),
    (("CORE-9", "CORE-18"), {
        "title": "Список литературы нужно привести к одному стилю",
        "summary": "Оформляйте источники единообразно и используйте корректные обозначения страниц и библиографических элементов.",
        "page": 40,
    }),
    (("CORE-13",), {
        "title": "Перед кодом объясните, что он делает",
        "summary": "Фрагмент кода должен сопровождаться человеческим пояснением его назначения и роли в работе.",
        "page": 132,
    }),
    (("CORE-3", "CORE-11", "CORE-17", "CORE-19", "CORE-20"), {
        "title": "Пишите просто, точно и единообразно",
        "summary": "Редакционные мелочи важны: стиль, списки, знаки препинания и формулировки должны быть аккуратными по всему документу.",
        "page": 122,
    }),
]

# Longest / most specific internal identifiers must come first.
_INTERNAL_REPLACEMENTS = [
    (re.compile(r"\bprototype_disadvantages_inside_chapter\b", re.I), "недостатки прототипа в этой главе"),
    (re.compile(r"\bcomparison_with_prototype_in_chapter_conclusions\b", re.I), "сравнение с прототипом в выводах главы"),
    (re.compile(r"\banalogs_inside_chapter\b", re.I), "аналоги в этой главе"),
    (re.compile(r"\bprototype_inside_chapter\b", re.I), "прототип в этой главе"),
    (re.compile(r"\bprototype_disadvantages\b", re.I), "недостатки прототипа"),
    (re.compile(r"\bmatrix comparison with prototype\b", re.I), "сравнение с прототипом"),
    (re.compile(r"\bcomparison with prototype\b", re.I), "сравнение с прототипом"),
    (re.compile(r"\bfull coverage matrix\b", re.I), "полная проверка назначенных фрагментов"),
    (re.compile(r"\bcoverage matrix\b", re.I), "проверка назначенных фрагментов"),
    (re.compile(r"\bfact[- ]first\b", re.I), "анализ текста"),
    (re.compile(r"\bentity[- ]level consistency\b", re.I), "проверка согласованности"),
    (re.compile(r"\bstatement[- ]scoped\b", re.I), "в пределах конкретного положения"),
    (re.compile(r"\bdocument[- ]grounded\s+candidates?\b", re.I), "подтверждённых по тексту работы оснований"),
    (re.compile(r"\bdocument[- ]grounded\b", re.I), "подтверждённых по тексту работы"),
    (re.compile(r"\bcandidates?\b", re.I), "варианты"),
    (re.compile(r"\bambiguous\b", re.I), "неоднозначными"),
    (re.compile(r"\bnot_found\b", re.I), "не найдены"),
    (re.compile(r"\bfound\b", re.I), "найдены"),
    (re.compile(r"\bverdict\b", re.I), "результат проверки"),
    (re.compile(r"\bevidence\b", re.I), "доказательство"),
    # Bare fact labels can still leak from generated explanations.
    (re.compile(r"\banalogs\b", re.I), "аналоги"),
    (re.compile(r"\bprototype\b", re.I), "прототип"),
]


def user_rule_title(rule: dict[str, Any] | None, fallback: str = "") -> str:
    rule = rule or {}
    rid = str(rule.get("id") or "")
    if rid in _USER_TITLES:
        return _USER_TITLES[rid]
    title = str(rule.get("title") or rule.get("requirement") or fallback or rid).strip()
    return clean_user_text(title)


def user_rule_requirement(rule: dict[str, Any] | None) -> str:
    rule = rule or {}
    rid = str(rule.get("id") or "")
    if rid in _USER_REQUIREMENTS:
        return _USER_REQUIREMENTS[rid]
    return clean_user_text(str(rule.get("requirement") or "").strip())


def clean_user_text(value: str) -> str:
    text = str(value or "")
    text = text.replace("слова«", "слова «").replace("слова‑«", "слова «").replace("слова-«", "слова «")
    # Common implementation wording that is useful in a developer report but
    # unnecessarily exposes internals to the thesis author.
    text = re.sub(r"По карте обозначений\s+Python\s+подтвердил(?:а|и)?", "По карте обозначений подтверждены", text, flags=re.I)
    # Fact-engine explanations use a stable technical preamble. Translate the
    # two common author-visible forms as complete Russian sentences instead of
    # leaking phrases such as ``Fact-first`` / ``проверка по содержанию текста``.
    text = re.sub(
        r"Fact[- ]first:\s*после полного просмотра назначенной области отсутствуют обязательные факты\.?",
        "При проверке текста не удалось подтвердить обязательные элементы.",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"Fact[- ]first:\s*категорический вывод не формируется, потому что часть обязательных фактов неоднозначна\.?",
        "По тексту нельзя сделать однозначный вывод, поскольку часть обязательных элементов остаётся неоднозначной.",
        text,
        flags=re.I,
    )
    for pattern, replacement in _INTERNAL_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    # Avoid fragile arrow glyphs in PDF output and make replacement intent explicit.
    text = re.sub(r"«([^»]+)»\s*[→⇒]\s*«([^»]+)»", r"заменить «\1» на «\2»", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def advice_for_rule(rule_id: str) -> dict[str, Any] | None:
    rid = str(rule_id or "")
    for prefixes, advice in _ADVICE_BY_PREFIX:
        if any(rid == prefix or rid.startswith(prefix + "-") for prefix in prefixes):
            item = dict(advice)
            item["url"] = f"{SHALYTO_ADVICE_URL}#page={item['page']}"
            item["source"] = "А.А. Шалыто, «Универсальные советы защищающимся»"
            return item
    return None


def enrich_rule_for_user(rule: dict[str, Any]) -> dict[str, Any]:
    result = dict(rule)
    result["userTitle"] = user_rule_title(rule)
    result["userRequirement"] = user_rule_requirement(rule)
    advice = advice_for_rule(str(rule.get("id") or ""))
    if advice:
        result["relatedAdvice"] = advice
    return result
