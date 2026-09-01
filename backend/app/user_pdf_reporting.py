from __future__ import annotations

from collections import Counter, defaultdict
import re
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .pdf_reporting import _register_fonts, _safe
from .user_facing import advice_for_rule, clean_user_text, user_rule_requirement, user_rule_title

_USER_STATUS = {
    "violation": ("Нужно исправить", colors.HexColor("#9B2C2C")),
    "uncertain": ("Требует проверки", colors.HexColor("#8A6B16")),
    "pass": ("Проверено", colors.HexColor("#2F6B3C")),
    "not_checked": ("Не удалось проверить", colors.HexColor("#626970")),
    "not_applicable": ("Не проверялось", colors.HexColor("#626970")),
}

_TYPE_LABELS = {
    "title": "Название",
    "abstract": "Реферат",
    "introduction": "Введение",
    "goal": "Цель",
    "tasks": "Задачи",
    "defense_statements": "Положения на защиту",
    "chapter": "Глава",
    "chapter_conclusions": "Выводы по главе",
    "conclusion": "Заключение",
    "bibliography": "Список литературы",
    "appendix": "Приложение",
}

_FACT_LABELS = {
    "analogs": "аналоги",
    "prototype": "прототип",
    "prototype_disadvantages": "недостатки прототипа",
    "analogs_inside_chapter": "аналоги в главе",
    "prototype_inside_chapter": "прототип в главе",
    "prototype_disadvantages_inside_chapter": "недостатки прототипа в главе",
    "comparison_with_prototype_in_chapter_conclusions": "сравнение с прототипом в выводах",
}

_CATEGORY_OVERRIDES = {
    "CORE-12": "Сокращения и термины",
    "CORE-13": "Код и дополнительные материалы",
    "CORE-14": "Внедрение результатов",
    "CORE-15": "Научное содержание и прототип",
    "CORE-16": "Рисунки, таблицы и визуальное оформление",
    "CORE-17": "Язык и стиль",
    "CORE-18": "Список литературы",
    "CORE-19": "Оформление текста",
    "CORE-20": "Язык и стиль",
}

_CATEGORY_RENAMES = {
    "Положения, выносимые на защиту (ядро диссертации)": "Положения на защиту",
    "Научная новизна vs инженерия": "Научное содержание и прототип",
    "Язык и стиль": "Язык и стиль",
    "Аббревиатуры и термины": "Сокращения и термины",
    "Оформление текста": "Оформление текста",
    "Названия": "Название и цель",
    "Таблицы, рисунки, формулы": "Рисунки, таблицы и формулы",
    "Выводы и заключение": "Выводы и заключение",
    "Список литературы": "Список литературы",
    "Презентация": "Презентация и доклад",
    "Специальные случаи": "Язык и стиль",
    "Дополнительные требования": "Другие требования",
}

_CATEGORY_ORDER = [
    "Положения на защиту",
    "Научное содержание и прототип",
    "Название и цель",
    "Выводы и заключение",
    "Сокращения и термины",
    "Язык и стиль",
    "Оформление текста",
    "Рисунки, таблицы и формулы",
    "Список литературы",
    "Код и дополнительные материалы",
    "Внедрение результатов",
    "Рисунки, таблицы и визуальное оформление",
    "Презентация и доклад",
    "Другие требования",
]

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3, None: 4, "": 4}
_MAX_USER_EVIDENCE = 3
_MAX_USER_QUOTE = 900
_MAX_USER_TERMS = 12


def report_to_user_pdf(
    name: str,
    report: dict[str, Any],
    *,
    profile: str | None = None,
    generated_at: str | None = None,
) -> bytes:
    """Create a concise report for the thesis author.

    This renderer intentionally does not expose engine names, prompt hashes,
    provider traces, request IDs, fact matrices, internal block IDs or the
    experimental numeric score. The full technical renderer remains available
    separately as the developer report.
    """

    regular_font, bold_font = _register_fonts()
    styles = _styles(regular_font, bold_font)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Отчёт проверки - {name}",
        author="OSA.Edu",
        subject="Пользовательский отчёт автоматизированной проверки ВКР",
    )

    catalog = {str(item.get("id")): item for item in report.get("ruleCatalog", []) or []}
    results = list(report.get("ruleResults", []) or [])

    story: list[Any] = []
    story.extend(_cover(name, report, profile, generated_at, catalog, results, styles))
    story.extend(_structure_section(report.get("documentMap"), styles))
    story.extend(_issues_section(results, catalog, styles))
    story.extend(_manual_review_section(results, catalog, styles))
    story.extend(_passed_section(results, catalog, styles))
    story.extend(_not_checked_section(results, catalog, styles))

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#6B7178"))
        canvas.setFont(regular_font, 8)
        canvas.drawString(doc.leftMargin, 9 * mm, "OSA.Edu — отчёт проверки")
        canvas.drawRightString(A4[0] - doc.rightMargin, 9 * mm, f"Страница {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


def _cover(
    name: str,
    report: dict[str, Any],
    profile: str | None,
    generated_at: str | None,
    catalog: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    counts = report.get("counts", {}) or {}
    coverage = round(float(report.get("coverage", 0) or 0) * 100)
    health = report.get("reportHealth") or {}

    if health.get("status") == "technical_incomplete":
        status_label = "Проверка завершена не полностью"
    elif int(counts.get("violation", 0) or 0) > 0:
        status_label = "Требует исправлений"
    elif int(counts.get("uncertain", 0) or 0) > 0:
        status_label = "Требует ручной проверки"
    else:
        status_label = "Проверка завершена"

    metric_rows = [[
        _metric(str(counts.get("violation", 0)), "нужно исправить", styles),
        _metric(str(counts.get("uncertain", 0)), "проверить вручную", styles),
        _metric(str(counts.get("pass", 0)), "выполнено", styles),
        _metric(str(counts.get("notChecked", 0)), "не удалось проверить", styles),
    ]]
    metrics = Table(metric_rows, colWidths=[43.5 * mm] * 4)
    metrics.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9DDE1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E5E8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    attention = _attention_rows(results, catalog)
    result: list[Any] = [
        Paragraph("OSA.Edu", styles["eyebrow"]),
        Paragraph("Проверка диссертации", styles["title"]),
        Paragraph(_safe(name), styles["document_name"]),
    ]
    if generated_at:
        result.append(Paragraph(f"Сформирован: {_safe(generated_at)}", styles["muted"]))
    result.extend([
        Spacer(1, 5 * mm),
        Table([
            [_p("Статус", styles["metric_label"]), _p(status_label, styles["metric_value"])],
            [_p("Автоматически проверено", styles["metric_label"]), _p(f"{coverage}% применимых правил", styles["metric_value"])],
            [_p("Профиль", styles["metric_label"]), _p("Ядро" if profile == "core" else "Полный набор" if profile == "full" else "—", styles["metric_value"])],
        ], colWidths=[55 * mm, 119 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F6F7")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9DDE1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E5E8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])),
        Spacer(1, 4 * mm),
        metrics,
        Spacer(1, 5 * mm),
    ])

    if attention:
        result.extend([
            Paragraph("Что требует внимания", styles["section"]),
            Paragraph("Сначала удобно исправить группы с наибольшим числом подтверждённых замечаний.", styles["muted"]),
        ])
        rows = [[_p("Раздел", styles["table_head"]), _p("Замечания", styles["table_head"]), _p("Проверить", styles["table_head"])]]
        for category, violations, uncertain in attention[:8]:
            rows.append([
                _p(category, styles["table_cell"]),
                _p(str(violations) if violations else "—", styles["table_cell_center"]),
                _p(str(uncertain) if uncertain else "—", styles["table_cell_center"]),
            ])
        table = LongTable(rows, colWidths=[118 * mm, 28 * mm, 28 * mm], repeatRows=1)
        table.setStyle(_table_style())
        result.extend([table, Spacer(1, 4 * mm)])

    result.append(Table([[_p(
        "Отчёт фиксирует нарушения и спорные места по выбранным правилам. Сначала исправляйте подтверждённые нарушения, затем просматривайте пункты, требующие ручной проверки. Для краткости длинные цитаты могут быть сокращены; полный текст доказательств доступен в отчёте для разработчика. Техническая диагностика доступна только в отчёте для разработчика.",
        styles["notice"],
    )]], colWidths=[174 * mm], style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E6")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E4C982")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ])))
    return result


def _attention_rows(results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> list[tuple[str, int, int]]:
    grouped: dict[str, Counter] = defaultdict(Counter)
    for result in results:
        status = str(result.get("status") or "")
        if status not in {"violation", "uncertain"}:
            continue
        rule = catalog.get(str(result.get("ruleId"))) or {}
        grouped[_user_category(result, rule)][status] += 1
    rows = [(category, counts["violation"], counts["uncertain"]) for category, counts in grouped.items()]
    order = {name: idx for idx, name in enumerate(_CATEGORY_ORDER)}
    return sorted(rows, key=lambda row: (-row[1], -row[2], order.get(row[0], 999), row[0]))


def _structure_section(document_map: dict[str, Any] | None, styles: dict[str, ParagraphStyle]) -> list[Any]:
    if not document_map:
        return []
    elements = document_map.get("elements", []) or []
    important = {"introduction", "goal", "tasks", "defense_statements", "chapter", "chapter_conclusions", "conclusion", "bibliography", "appendix"}
    displayed = [item for item in elements if item.get("type") in important and item.get("canonicalRole") != "secondary_copy"]
    if not displayed and not document_map.get("issues"):
        return []

    result: list[Any] = [
        Spacer(1, 7 * mm),
        Paragraph("Как система поняла структуру работы", styles["section"]),
    ]
    if displayed:
        rows = [[_p("Раздел", styles["table_head"]), _p("Название", styles["table_head"]), _p("Страницы", styles["table_head"])]]
        for item in displayed:
            pages = item.get("pages", []) or []
            pages_text = _pages_range(pages)
            rows.append([
                _p(_TYPE_LABELS.get(str(item.get("type")), str(item.get("type", ""))), styles["table_cell"]),
                _p(str(item.get("label", "")), styles["table_cell"]),
                _p(pages_text or "—", styles["table_cell_center"]),
            ])
        table = LongTable(rows, colWidths=[38 * mm, 112 * mm, 24 * mm], repeatRows=1)
        table.setStyle(_table_style())
        result.extend([table, Spacer(1, 2 * mm)])

    issues = [str(item.get("message") or "").strip() for item in (document_map.get("issues", []) or []) if str(item.get("message") or "").strip()]
    if issues:
        result.append(Paragraph("Что стоит проверить в структуре", styles["subsection"]))
        for message in issues[:5]:
            result.append(Paragraph(f"• {_safe(message)}", styles["body"]))
        if len(issues) > 5:
            result.append(Paragraph(f"Ещё замечаний к структуре: {len(issues) - 5}. Полный список — в отчёте для разработчика.", styles["muted"]))
    return result


def _issues_section(results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]], styles: dict[str, ParagraphStyle]) -> list[Any]:
    violations = [result for result in results if result.get("status") == "violation"]
    if not violations:
        return []
    grouped = _group_results(violations, catalog)
    story: list[Any] = [Spacer(1, 7 * mm), Paragraph("Что нужно исправить", styles["section"])]
    for category, items in grouped:
        story.extend([Spacer(1, 3 * mm), Paragraph(category, styles["category"]), HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D9DDE1"), spaceAfter=2 * mm)])
        for result, rule in items:
            story.extend(_user_rule_card(result, rule, styles))
    return story


def _manual_review_section(results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]], styles: dict[str, ParagraphStyle]) -> list[Any]:
    uncertain = [result for result in results if result.get("status") == "uncertain"]
    if not uncertain:
        return []
    grouped = _group_results(uncertain, catalog)
    story: list[Any] = [
        Spacer(1, 7 * mm),
        Paragraph("Требует ручной проверки", styles["section"]),
        Paragraph("Эти пункты система не смогла подтвердить или опровергнуть достаточно однозначно.", styles["muted"]),
    ]
    for category, items in grouped:
        story.extend([Spacer(1, 3 * mm), Paragraph(category, styles["category"]), HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D9DDE1"), spaceAfter=2 * mm)])
        for result, rule in items:
            story.extend(_user_rule_card(result, rule, styles))
    return story


def _passed_section(results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]], styles: dict[str, ParagraphStyle]) -> list[Any]:
    passed = [(result, catalog.get(str(result.get("ruleId"))) or {}) for result in results if result.get("status") == "pass"]
    if not passed:
        return []
    passed.sort(key=lambda pair: _rule_sort_key(pair[0], pair[1]))
    cells: list[Any] = []
    for result, rule in passed:
        title = user_rule_title(rule, str(result.get("explanation") or result.get("ruleId") or ""))
        cells.append(Paragraph(f"✓ {_safe(title)} <font color='#777777'>({_safe(str(result.get('ruleId') or ''))})</font>", styles["passed_item"]))
    rows = []
    for idx in range(0, len(cells), 2):
        rows.append([cells[idx], cells[idx + 1] if idx + 1 < len(cells) else ""])
    table = Table(rows, colWidths=[86 * mm, 86 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [
        Spacer(1, 7 * mm),
        Paragraph("Что проверено успешно", styles["section"]),
        Paragraph("Ниже перечислены требования, по которым система не нашла подтверждённых нарушений.", styles["muted"]),
        table,
    ]


def _not_checked_section(results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]], styles: dict[str, ParagraphStyle]) -> list[Any]:
    not_checked = [(r, catalog.get(str(r.get("ruleId"))) or {}) for r in results if r.get("status") == "not_checked"]
    not_applicable = [(r, catalog.get(str(r.get("ruleId"))) or {}) for r in results if r.get("status") == "not_applicable"]
    if not not_checked and not not_applicable:
        return []

    story: list[Any] = [Spacer(1, 7 * mm)]
    intro = "По текущему PDF нельзя надёжно проверить:" if not_checked else "Часть требований не относится к предоставленному набору материалов."
    story.append(KeepTogether([
        Paragraph("Что не проверялось", styles["section"]),
        Paragraph(intro, styles["body"]),
    ]))
    if not_checked:
        for result, rule in not_checked:
            title = user_rule_title(rule, str(result.get("ruleId") or ""))
            explanation = str(result.get("explanation") or "").strip()
            text = f"• <b>{_safe(title)}</b>"
            if explanation:
                text += f" — {_safe(explanation)}"
            story.append(Paragraph(text, styles["body"]))

    if not_applicable:
        grouped = Counter(_user_category(r, rule) for r, rule in not_applicable)
        presentation_count = grouped.pop("Презентация и доклад", 0)
        if presentation_count:
            story.extend([
                Spacer(1, 2 * mm),
                Paragraph(
                    f"<b>Презентация и доклад.</b> {presentation_count} требований не проверялись: для них нужна презентация и/или запись выступления.",
                    styles["body"],
                ),
            ])
        for category, count in sorted(grouped.items(), key=lambda item: (_CATEGORY_ORDER.index(item[0]) if item[0] in _CATEGORY_ORDER else 999, item[0])):
            story.append(Paragraph(f"• {_safe(category)}: неприменимых к предоставленному документу требований — {count}.", styles["body"]))
    return story


def _group_results(results: list[dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> list[tuple[str, list[tuple[dict[str, Any], dict[str, Any]]]]]:
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for result in results:
        rule = catalog.get(str(result.get("ruleId"))) or {}
        grouped[_user_category(result, rule)].append((result, rule))
    order = {name: idx for idx, name in enumerate(_CATEGORY_ORDER)}
    output = []
    for category in sorted(grouped, key=lambda name: (order.get(name, 999), name)):
        grouped[category].sort(key=lambda pair: _rule_sort_key(pair[0], pair[1]))
        output.append((category, grouped[category]))
    return output


def _rule_sort_key(result: dict[str, Any], rule: dict[str, Any]) -> tuple[Any, ...]:
    severity = result.get("severity") or rule.get("severity")
    rid = str(result.get("ruleId") or rule.get("id") or "")
    return (_SEVERITY_ORDER.get(str(severity) if severity is not None else None, 4), _natural_rule_key(rid))


def _natural_rule_key(value: str) -> tuple[Any, ...]:
    import re
    parts = re.split(r"(\d+)", value)
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _user_category(result: dict[str, Any], rule: dict[str, Any]) -> str:
    rid = str(result.get("ruleId") or rule.get("id") or "")
    if rid in _CATEGORY_OVERRIDES:
        return _CATEGORY_OVERRIDES[rid]
    category = str(rule.get("category") or "Другие требования")
    return _CATEGORY_RENAMES.get(category, category or "Другие требования")


def _user_rule_card(result: dict[str, Any], rule: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    status = str(result.get("status") or "not_checked")
    label, color = _USER_STATUS.get(status, (status, colors.HexColor("#626970")))
    rid = str(result.get("ruleId") or rule.get("id") or "")
    title = user_rule_title(rule, str(result.get("explanation") or rid))

    badge = Table([[Paragraph(_safe(label), styles["badge"])]], colWidths=[37 * mm], rowHeights=[9 * mm])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    heading = Table([[badge, Paragraph(f"{_safe(title)} <font color='#777777'>({_safe(rid)})</font>", styles["rule_title"])]], colWidths=[39 * mm, 135 * mm])
    heading.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9DDE1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    body: list[Any] = [heading, Spacer(1, 2 * mm)]
    requirement = user_rule_requirement(rule)
    if requirement and requirement != title:
        body.append(_label_value("Правило", requirement, styles))

    explanation = clean_user_text(str(result.get("explanation") or "").strip())
    if explanation:
        body.append(_label_value("Нарушение" if status == "violation" else "Результат проверки", explanation, styles))

    where = _where_text(result)
    if where:
        body.append(_label_value("Где", where, styles))

    term_text = _term_findings_text(result)
    if term_text:
        body.append(_label_value("Обозначения", term_text, styles))

    fact_text = _fact_matrix_text(result)
    if fact_text and status == "uncertain":
        body.append(_label_value("Что неоднозначно", fact_text, styles))

    evidence = list(result.get("evidence") or [])
    if evidence:
        body.append(Paragraph("Примеры", styles["subsection"]))
        for item in evidence[:_MAX_USER_EVIDENCE]:
            body.append(_compact_evidence(item, styles))
        if len(evidence) > _MAX_USER_EVIDENCE:
            body.append(Paragraph(f"Ещё примеров: {len(evidence) - _MAX_USER_EVIDENCE}. Полный список — в отчёте для разработчика.", styles["muted"]))

    fix = clean_user_text(str(result.get("fix") or "").strip())
    if fix:
        body.append(_label_value("Как исправить", fix, styles))
    elif status == "uncertain":
        body.append(_label_value("Как проверить", "Просмотреть указанный фрагмент вручную и при необходимости уточнить формулировку.", styles))

    advice = rule.get("relatedAdvice") or advice_for_rule(rid)
    if advice and status in {"violation", "uncertain"}:
        advice_text = f"{advice.get('summary', '')} Источник: {advice.get('source', 'А.А. Шалыто')}, стр. {advice.get('page', '—')}."
        body.append(_label_value("Связанный совет Шалыто", clean_user_text(advice_text), styles))

    correct_example = clean_user_text(str(rule.get("correctExample") or "").strip())
    incorrect_example = clean_user_text(str(rule.get("incorrectExample") or "").strip())
    if correct_example:
        example = f"Неверно: {incorrect_example}\nВерно: {correct_example}" if incorrect_example else f"Верно: {correct_example}"
        body.append(_label_value("Пример по правилу", example, styles))

    lead_count = min(3, len(body))
    return [KeepTogether(body[:lead_count]), *body[lead_count:], Spacer(1, 4 * mm)]


def _where_text(result: dict[str, Any]) -> str:
    pages: list[int] = []
    locations: list[str] = []
    for item in result.get("evidence", []) or []:
        page = item.get("page")
        if isinstance(page, int) and page not in pages:
            pages.append(page)
        location = str(item.get("location") or "").strip()
        if location and not location.startswith("p") and location not in locations:
            locations.append(location)
    if pages:
        pages.sort()
        shown = pages[:8]
        text = "стр. " + ", ".join(str(page) for page in shown)
        if len(pages) > len(shown):
            text += f" и ещё {len(pages) - len(shown)}"
        return text
    matrix_labels = [str(row.get("label") or "").strip() for row in result.get("coverageMatrix", []) or [] if str(row.get("label") or "").strip()]
    if matrix_labels:
        shown = matrix_labels[:3]
        text = "; ".join(shown)
        if len(matrix_labels) > len(shown):
            text += f"; ещё {len(matrix_labels) - len(shown)} фрагм."
        return text
    if locations:
        return "; ".join(locations[:3])
    return ""


def _term_findings_text(result: dict[str, Any]) -> str:
    findings = result.get("termFindings") or []
    if not findings:
        return ""
    target_status = "violation" if result.get("status") == "violation" else "uncertain"
    selected = [str(item.get("term") or "").strip() for item in findings if item.get("status") == target_status and str(item.get("term") or "").strip()]
    if not selected and result.get("status") == "uncertain":
        selected = [str(item.get("term") or "").strip() for item in findings if item.get("status") == "uncertain" and str(item.get("term") or "").strip()]
    selected = list(dict.fromkeys(selected))
    if not selected:
        return ""
    shown = selected[:_MAX_USER_TERMS]
    text = ", ".join(shown)
    if len(selected) > len(shown):
        text += f"; ещё {len(selected) - len(shown)}"
    return text


def _fact_matrix_text(result: dict[str, Any]) -> str:
    details: list[str] = []
    for row in result.get("coverageMatrix", []) or []:
        ambiguous = []
        for item in row.get("items", []) or []:
            status = str(item.get("status") or "")
            if status not in {"ambiguous", "not_found"}:
                continue
            name = _FACT_LABELS.get(str(item.get("name") or ""), str(item.get("name") or ""))
            ambiguous.append(f"{name}: {'неоднозначно' if status == 'ambiguous' else 'не найдено'}")
        if ambiguous:
            label = str(row.get("label") or "").strip()
            details.append((label + " — " if label else "") + ", ".join(ambiguous))
    return "; ".join(details[:3])


def _anchor_match(text: str, anchor: str) -> re.Match[str] | None:
    anchor = re.sub(r"\s+", " ", str(anchor or "")).strip()
    if not anchor:
        return None
    return re.search(re.escape(anchor), text, re.I)


def _sentence_bounds_around(text: str, start: int, end: int) -> tuple[int, int]:
    """Find a readable sentence-like window containing ``start:end``."""
    left = 0
    for match in re.finditer(r"[.!?…](?:[»\"')\]]*)\s+", text[:start]):
        left = match.end()
    # Keep list/bullet markers when the anchor is inside a list item.
    newline = text.rfind("\n", 0, start)
    if newline >= 0:
        left = max(left, newline + 1)

    right = len(text)
    match = re.search(r"[.!?…](?:[»\"')\]]*)(?=\s|$)", text[end:])
    if match:
        right = end + match.end()
    return left, right


def _word_safe_window(text: str, start: int, end: int, limit: int) -> tuple[str, bool]:
    """Return <= limit chars around an anchor while keeping the anchor visible."""
    if len(text) <= limit:
        return text, False
    anchor_mid = (start + end) // 2
    left = max(0, anchor_mid - limit // 2)
    right = min(len(text), left + limit)
    left = max(0, right - limit)

    if left > 0:
        # Move to the next complete word; never display a chopped leading token.
        m = re.search(r"\s+", text[left:min(len(text), left + 90)])
        if m:
            left += m.end()
    if right < len(text):
        # Move back to the previous complete word.
        m = re.search(r"\s+\S*$", text[max(left, right - 90):right])
        if m:
            right = max(left, right - 90) + m.start()

    excerpt = text[left:right].strip(" ,;:-")
    prefix = "… " if left > 0 else ""
    suffix = " …" if right < len(text) else ""
    return prefix + excerpt + suffix, True


def _author_quote(value: str, *, anchor: str = "", context: str = "") -> tuple[str, bool]:
    """Return a readable author-facing excerpt while preserving the proved span.

    Developer evidence remains exact.  The author PDF may shorten it, but when
    an evidence token/marker is known the displayed excerpt is guaranteed to
    contain that marker.  This prevents a card from claiming, for example,
    «предложение начинается с „Но“» while showing a neighbouring sentence that
    does not contain «Но» at all.
    """
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    wider = re.sub(r"\s+", " ", str(context or "")).strip()
    anchor = re.sub(r"\s+", " ", str(anchor or "")).strip()
    if not raw and not wider:
        return "", False

    text = raw or wider
    display_adjusted = False
    match = _anchor_match(text, anchor)

    # Some candidate evidence quotes are sentence slices while the detector's
    # marker sits just outside that slice. Prefer the verified wider context in
    # that case, but only when it actually contains the marker.
    if anchor and match is None and wider:
        wider_match = _anchor_match(wider, anchor)
        if wider_match is not None:
            text = wider
            match = wider_match
            display_adjusted = True

    if match is not None:
        left, right = _sentence_bounds_around(text, match.start(), match.end())
        sentence = text[left:right].strip()
        sentence_match = _anchor_match(sentence, anchor)
        if sentence and len(sentence) <= _MAX_USER_QUOTE and sentence_match is not None:
            return sentence, display_adjusted or sentence != raw
        # Keep the anchor inside the character-limited excerpt even for very
        # long sentences / flattened table rows.
        local_start = match.start() - left if sentence else match.start()
        local_end = match.end() - left if sentence else match.end()
        source = sentence or text
        excerpt, shortened = _word_safe_window(source, local_start, local_end, _MAX_USER_QUOTE)
        return excerpt, True if shortened or display_adjusted or source != raw else False

    # No explicit anchor is available. Keep the quality-4.1 behaviour that
    # avoids visibly starting/ending in the middle of a word.
    protected_start = bool(re.match(r"^(?:[–—•▪◦]|\(?\d+[.)])\s*", text))
    starts_midword = bool(re.match(r"^[а-яёa-z]", text)) and not protected_start
    if starts_midword:
        boundary = re.search(r"[.!?]\s+(?=[А-ЯЁA-Z0-9«„\"])", text[:500])
        if boundary and len(text) - boundary.end() >= 45:
            text = text[boundary.end():].lstrip()
            display_adjusted = True
        else:
            text = "… " + text
            display_adjusted = True

    if len(text) > _MAX_USER_QUOTE:
        text, _ = _word_safe_window(text, 0, min(len(text), 1), _MAX_USER_QUOTE)
        display_adjusted = True

    if text and text[-1].isalnum():
        trimmed = re.sub(r"\s+\S+$", "", text).rstrip(" ,;:-")
        if trimmed and len(trimmed) >= max(20, int(len(text) * 0.65)):
            text = trimmed + " …"
            display_adjusted = True
        else:
            text += " …"
            display_adjusted = True
    return text, display_adjusted

def _compact_evidence(item: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    page = item.get("page")
    token = str(item.get("token") or "").strip()
    quote, _shortened = _author_quote(
        str(item.get("quote") or ""),
        anchor=token,
        context=str(item.get("context") or ""),
    )
    meta_parts = []
    if page:
        meta_parts.append(f"стр. {page}")
    if token:
        meta_parts.append(f"«{token}»")
    meta = " · ".join(meta_parts)
    text = (f"<b>{_safe(meta)}</b><br/>" if meta else "") + f"«{_safe(quote)}»"
    table = Table([[Paragraph(text, styles["quote"])]], colWidths=[170 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F8F8")),
        ("LINEBEFORE", (0, 0), (0, -1), 2, colors.HexColor("#BFC5CA")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _metric(value: str, label: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(f"<b>{_safe(value)}</b><br/>{_safe(label)}", styles["metric_cell"])


def _pages_range(pages: list[Any]) -> str:
    ints = sorted({int(page) for page in pages if isinstance(page, int) or (isinstance(page, str) and page.isdigit())})
    if not ints:
        return ""
    return str(ints[0]) if len(ints) == 1 else f"{ints[0]}–{ints[-1]}"


def _styles(regular_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle("UserEyebrow", parent=base["Normal"], fontName=bold_font, fontSize=9, leading=11, textColor=colors.HexColor("#6B7178"), spaceAfter=3),
        "title": ParagraphStyle("UserTitle", parent=base["Title"], fontName=bold_font, fontSize=22, leading=27, alignment=TA_LEFT, textColor=colors.HexColor("#202124"), spaceAfter=4),
        "document_name": ParagraphStyle("UserDocumentName", parent=base["Normal"], fontName=regular_font, fontSize=12, leading=16, textColor=colors.HexColor("#555C64"), splitLongWords=True),
        "section": ParagraphStyle("UserSection", parent=base["Heading2"], fontName=bold_font, fontSize=15, leading=19, textColor=colors.HexColor("#202124"), spaceBefore=4, spaceAfter=4),
        "category": ParagraphStyle("UserCategory", parent=base["Heading3"], fontName=bold_font, fontSize=12, leading=15, textColor=colors.HexColor("#303337"), spaceBefore=2, spaceAfter=2),
        "subsection": ParagraphStyle("UserSubsection", parent=base["Heading3"], fontName=bold_font, fontSize=10, leading=13, textColor=colors.HexColor("#303337"), spaceBefore=3, spaceAfter=3),
        "body": ParagraphStyle("UserBody", parent=base["BodyText"], fontName=regular_font, fontSize=9.3, leading=13, textColor=colors.HexColor("#202124"), spaceAfter=3, splitLongWords=True),
        "muted": ParagraphStyle("UserMuted", parent=base["BodyText"], fontName=regular_font, fontSize=8.4, leading=11, textColor=colors.HexColor("#6B7178"), spaceAfter=3, splitLongWords=True),
        "notice": ParagraphStyle("UserNotice", parent=base["BodyText"], fontName=regular_font, fontSize=9, leading=12.5, textColor=colors.HexColor("#634B15"), splitLongWords=True),
        "metric_label": ParagraphStyle("UserMetricLabel", parent=base["BodyText"], fontName=regular_font, fontSize=8.5, leading=11, textColor=colors.HexColor("#6B7178")),
        "metric_value": ParagraphStyle("UserMetricValue", parent=base["BodyText"], fontName=bold_font, fontSize=9.5, leading=12, textColor=colors.HexColor("#202124"), splitLongWords=True),
        "metric_cell": ParagraphStyle("UserMetricCell", parent=base["BodyText"], fontName=regular_font, fontSize=8.2, leading=11, alignment=TA_CENTER, textColor=colors.HexColor("#444A50")),
        "badge": ParagraphStyle("UserBadge", parent=base["BodyText"], fontName=bold_font, fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=colors.white),
        "rule_title": ParagraphStyle("UserRuleTitle", parent=base["BodyText"], fontName=bold_font, fontSize=10, leading=13, textColor=colors.HexColor("#202124"), splitLongWords=True),
        "label": ParagraphStyle("UserLabel", parent=base["BodyText"], fontName=bold_font, fontSize=8.5, leading=11, textColor=colors.HexColor("#555C64")),
        "value": ParagraphStyle("UserValue", parent=base["BodyText"], fontName=regular_font, fontSize=9, leading=12.5, textColor=colors.HexColor("#202124"), splitLongWords=True),
        "quote": ParagraphStyle("UserQuote", parent=base["BodyText"], fontName=regular_font, fontSize=8.6, leading=11.5, textColor=colors.HexColor("#303337"), splitLongWords=True),
        "passed_item": ParagraphStyle("UserPassedItem", parent=base["BodyText"], fontName=regular_font, fontSize=8.5, leading=11.5, textColor=colors.HexColor("#2E5D39"), splitLongWords=True),
        "table_head": ParagraphStyle("UserTableHead", parent=base["BodyText"], fontName=bold_font, fontSize=7.8, leading=9.8, textColor=colors.HexColor("#303337"), splitLongWords=True),
        "table_cell": ParagraphStyle("UserTableCell", parent=base["BodyText"], fontName=regular_font, fontSize=7.8, leading=10, textColor=colors.HexColor("#303337"), splitLongWords=True),
        "table_cell_center": ParagraphStyle("UserTableCellCenter", parent=base["BodyText"], fontName=regular_font, fontSize=7.8, leading=10, alignment=TA_CENTER, textColor=colors.HexColor("#303337")),
    }


def _label_value(label: str, value: str, styles: dict[str, ParagraphStyle]) -> Table:
    table = Table([[_p(label, styles["label"]), _p(value, styles["value"]) ]], colWidths=[34 * mm, 140 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return table


def _table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D2D6DA")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E0E3E6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFBFB")]),
    ])


def _p(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_safe(str(value or "")), style)
