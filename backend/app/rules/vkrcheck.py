"""VkrCheck profile: one canonical rule for each distinct criterion.

The source describes four formal checks and two semantic checks. Its lexical
introduction check and semantic introduction check ask about the same blocks,
so this profile evaluates those blocks once, semantically. Shalyto profiles
remain unchanged.
"""

from __future__ import annotations

from typing import Any


SOURCE = "polinafominykh/VkrCheck, criteria/"
SOURCES = {
    "VKR-SECTIONS": ("formal_criteria.yaml", 1),
    "VKR-CHAPTERS": ("formal_criteria.yaml", 13),
    "VKR-INTRO-LENGTH": ("formal_criteria.yaml", 11),
    "VKR-REFERENCES": ("formal_criteria.yaml", 12),
    "VKR-INTRO-CONTENT": ("semantic_criteria.yaml", 1),
    "VKR-CHAPTER-ALIGNMENT": ("semantic_criteria.yaml", 39),
}
INTRO_FACT_ITEMS = [
    "actuality", "research_problem", "goal", "tasks_linked_to_goal",
    "practical_significance", "novelty_or_contribution",
]


def _rule(
    rule_id: str, title: str, requirement: str, *, mode: str, scope: str,
    severity: str = "major", required_facts: list[str] | None = None,
    guidance: str = "", selectors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "sourceNumber": rule_id.removeprefix("VKR-"),
        "category": "Проверка ВКР",
        "title": title,
        "requirement": requirement,
        "sourceLabel": SOURCE + SOURCES[rule_id][0],
        "sourceLine": SOURCES[rule_id][1],
        "layer": "vkrcheck",
        "mode": mode,
        "scope": scope,
        "severity": severity,
        "weight": 3 if severity == "major" else 1,
        "dedupKey": "vkrcheck:" + rule_id,
        "engineKind": mode,
        "requiredFacts": required_facts or ["document_text"],
        "routing": {"strategy": "llm", "selectors": selectors or [], "exhaustive": True}
        if mode == "semantic" else {"strategy": "structural"},
        **({"ruleGuidance": guidance} if guidance else {}),
    }


VKR_RULES = [
    _rule("VKR-SECTIONS", "Обязательные разделы",
          "В ВКР должны быть введение, заключение и список источников.",
          mode="structural", scope="document"),
    _rule("VKR-CHAPTERS", "Количество основных глав",
          "Основных глав должно быть от двух до восьми.",
          mode="structural", scope="chapter", severity="minor"),
    _rule("VKR-INTRO-LENGTH", "Объём введения",
          "Введение должно содержать не менее 500 символов извлечённого текста.",
          mode="structural", scope="document", severity="minor"),
    _rule("VKR-REFERENCES", "Количество источников",
          "В списке источников должно быть не менее пяти распознанных записей.",
          mode="structural", scope="bibliography", severity="minor"),
    _rule("VKR-INTRO-CONTENT", "Смысловая полнота введения",
          "Введение объясняет актуальность и проблему, формулирует цель и задачи, "
          "связывает задачи с целью, показывает практическую значимость и для "
          "магистерской ВКР обозначает научную новизну или вклад.",
          mode="semantic", scope="document", required_facts=["document_text", "introduction"],
          selectors=["introduction"],
          guidance="Оценивай смысл, а не наличие ключевых слов. Проверь по отдельности "
          "актуальность, проблему, цель, задачи и их связь с целью, практическую "
          "значимость, научную новизну или исследовательский вклад. Для каждого "
          "элемента верни отдельную запись absenceCheck со статусом found, not_found "
          "или ambiguous. Отсутствие "
          "методов исследования отмечай как рекомендацию, если их требует тип работы. "
          "Для магистерской ВКР PASS возможен только при подтверждении всех общих "
          "элементов и хотя бы одного элемента новизны или вклада. Обязательно "
          "просмотри введение целиком; если часть текста недоступна, отметь ambiguous."),
    _rule("VKR-CHAPTER-ALIGNMENT", "Соответствие главы названию",
          "Содержание каждой основной главы раскрывает её название и связано с "
          "исследовательской темой работы.",
          mode="semantic", scope="chapter", required_facts=["document_text", "chapter"],
          selectors=["chapter"],
          guidance="Для каждой главы сравни её заголовок с текстом именно этой главы. "
          "Укажи конкретное смысловое расхождение, если тема заголовка не раскрыта. "
          "Обычные вводные или обзорные абзацы не являются нарушением, если основная "
          "часть главы раскрывает тему. Приводи точную цитату из главы; при неполном "
          "тексте или неоднозначном заголовке отвечай uncertain."),
]
