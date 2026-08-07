from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_STATUS_ORDER = ["violation", "pass", "uncertain", "not_checked", "not_applicable"]
_STATUS_LABELS = {
    "violation": "Нарушено",
    "pass": "Выполнено",
    "uncertain": "Неопределённо",
    "not_checked": "Не обработано",
    "not_applicable": "Неприменимо",
}
_STATUS_COLORS = {
    "violation": colors.HexColor("#9B2C2C"),
    "pass": colors.HexColor("#2F6B3C"),
    "uncertain": colors.HexColor("#82651A"),
    "not_checked": colors.HexColor("#626970"),
    "not_applicable": colors.HexColor("#626970"),
}
_EVIDENCE_LABELS = {
    "verified": "цитаты подтверждены по исходным блокам",
    "coverage_verified": "отсутствие подтверждено полным просмотром назначенной области",
    "rejected": "заявленное нарушение не подтверждено допустимой цитатой или полной матрицей",
    "not_required": "доказательство не требовалось",
}
_MATRIX_LABELS = {
    "found": "найдено",
    "not_found": "не найдено",
    "ambiguous": "неоднозначно",
}
_INVALID_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_MAX_QUOTE_CHARS = 2400
_MAX_DIAGNOSTICS = 60


def report_to_pdf(
    name: str,
    report: dict[str, Any],
    *,
    profile: str | None = None,
    generated_at: str | None = None,
) -> bytes:
    """Build a printable Unicode PDF report directly from the report JSON."""

    regular_font, bold_font = _register_fonts()
    styles = _styles(regular_font, bold_font)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=17 * mm,
        bottomMargin=18 * mm,
        title=f"Протокол нормоконтроля - {name}",
        author="OSA.Edu",
        subject="Отчёт автоматизированной проверки ВКР",
    )

    story: list[Any] = []
    story.extend(_cover(name, report, profile, generated_at, styles))
    story.extend(_structure_section(report.get("documentMap"), styles))

    catalog = {str(item.get("id")): item for item in report.get("ruleCatalog", []) or []}
    results = report.get("ruleResults", []) or []
    for status in _STATUS_ORDER:
        items = [item for item in results if item.get("status") == status]
        if not items:
            continue
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"{_STATUS_LABELS[status]} ({len(items)})", styles["section"]))
        story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#D9DDE1"), spaceAfter=3 * mm))
        for result in items:
            story.extend(_rule_result(result, catalog.get(str(result.get("ruleId"))), styles))

    story.append(PageBreak())
    story.extend(_technical_section(report, profile, styles))

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#6B7178"))
        canvas.setFont(regular_font, 8)
        canvas.drawString(doc.leftMargin, 9 * mm, "OSA.Edu - протокол нормоконтроля")
        canvas.drawRightString(A4[0] - doc.rightMargin, 9 * mm, f"Страница {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


def _cover(
    name: str,
    report: dict[str, Any],
    profile: str | None,
    generated_at: str | None,
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    score = report.get("score")
    score_text = "не рассчитана" if score is None else f"{score}/100" + (" (предварительно)" if report.get("scoreIsProvisional") else "")
    coverage = round(float(report.get("coverage", 0) or 0) * 100)
    candidate_coverage = round(float(report.get("candidateCoverage", 0) or 0) * 100)
    counts = report.get("counts", {}) or {}

    rows = [
        [_p("Оценка", styles["metric_label"]), _p(score_text, styles["metric_value"])],
        [_p("Покрытие правил", styles["metric_label"]), _p(f"{coverage}% ({report.get('checkedRules', 0)} из {report.get('totalRules', 0)})", styles["metric_value"])],
        [_p("Отправлено назначенных фрагментов", styles["metric_label"]), _p(f"{candidate_coverage}%", styles["metric_value"])],
        [_p("Профиль", styles["metric_label"]), _p("Ядро" if profile == "core" else "Полный набор" if profile == "full" else "-", styles["metric_value"])],
    ]
    metric_table = Table(rows, colWidths=[62 * mm, 112 * mm], hAlign="LEFT")
    metric_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F6F7")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D9DDE1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E5E8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    status_rows = [[
        Paragraph(f"<b>{counts.get('violation', 0)}</b><br/>Нарушено", styles["status_cell"]),
        Paragraph(f"<b>{counts.get('pass', 0)}</b><br/>Выполнено", styles["status_cell"]),
        Paragraph(f"<b>{counts.get('uncertain', 0)}</b><br/>Неопределённо", styles["status_cell"]),
        Paragraph(f"<b>{counts.get('notChecked', 0)}</b><br/>Не обработано", styles["status_cell"]),
        Paragraph(f"<b>{counts.get('notApplicable', 0)}</b><br/>Неприменимо", styles["status_cell"]),
    ]]
    status_table = Table(status_rows, colWidths=[34.8 * mm] * 5)
    status_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D9DDE1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E5E8")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))

    result: list[Any] = [
        Paragraph("OSA.Edu", styles["eyebrow"]),
        Paragraph("Протокол нормоконтроля", styles["title"]),
        Paragraph(name, styles["document_name"]),
    ]
    if generated_at:
        result.append(Paragraph(f"Сформирован: {_safe(generated_at)}", styles["muted"]))
    result.extend([
        Spacer(1, 5 * mm),
        metric_table,
        Spacer(1, 4 * mm),
        status_table,
        Spacer(1, 4 * mm),
        Paragraph(_safe(str(report.get("summary", ""))), styles["summary"]),
        Spacer(1, 2 * mm),
        Table([[_p("Оценка не является решением о допуске к защите. Смысловые замечания и исправления необходимо проверить вручную.", styles["notice"]) ]], colWidths=[174 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E6")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E4C982")),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])),
    ])
    return result


def _structure_section(document_map: dict[str, Any] | None, styles: dict[str, ParagraphStyle]) -> list[Any]:
    if not document_map:
        return []
    elements = document_map.get("elements", []) or []
    important = {"title", "introduction", "goal", "tasks", "defense_statements", "chapter", "chapter_conclusions", "conclusion", "bibliography"}
    displayed = [item for item in elements if item.get("type") in important]
    extraction = document_map.get("extraction", {}) or {}
    review = document_map.get("review", {}) or {}

    result: list[Any] = [
        Spacer(1, 6 * mm),
        Paragraph("Структурная карта", styles["section"]),
        Paragraph(
            f"Статус: {'готова' if document_map.get('status') == 'ready' else 'частичная'}; "
            f"подтверждена пользователем: {'да' if review.get('confirmedByUser') else 'нет'}; "
            f"обработано блоков: {extraction.get('processedBlocks', 0)} из {extraction.get('totalBlocks', 0)}; "
            f"неоднозначных элементов: {sum(1 for item in elements if item.get('state') == 'ambiguous')}.",
            styles["body"],
        ),
        Spacer(1, 2 * mm),
    ]
    if displayed:
        rows: list[list[Any]] = [[_p("Тип", styles["table_head"]), _p("Фрагмент", styles["table_head"]), _p("Диапазон", styles["table_head"])]]
        for item in displayed:
            pages = item.get("pages", []) or []
            pages_text = f"; стр. {pages[0]}-{pages[-1]}" if pages else ""
            state = "; требует проверки" if item.get("state") == "ambiguous" else ""
            rows.append([
                _p(str(item.get("type", "")), styles["table_cell"]),
                _p(str(item.get("label", "")), styles["table_cell"]),
                _p(f"{item.get('startBlockId', '')} - {item.get('endBlockId', '')}{pages_text}{state}", styles["table_cell"]),
            ])
        table = LongTable(rows, colWidths=[34 * mm, 88 * mm, 52 * mm], repeatRows=1)
        table.setStyle(_table_style())
        result.append(table)
    issues = document_map.get("issues", []) or []
    if issues:
        result.extend([Spacer(1, 2 * mm), Paragraph("Замечания к карте", styles["subsection"])])
        result.extend(Paragraph(f"- {_safe(str(item.get('message', '')))}", styles["body"]) for item in issues)
    return result


def _rule_result(result: dict[str, Any], rule: dict[str, Any] | None, styles: dict[str, ParagraphStyle]) -> list[Any]:
    rule_id = str(result.get("ruleId", ""))
    title = str((rule or {}).get("title") or result.get("explanation") or "")
    status = str(result.get("status", "not_checked"))
    color = _STATUS_COLORS.get(status, colors.HexColor("#626970"))

    heading_table = Table([[Paragraph(_safe(_STATUS_LABELS.get(status, status)), styles["badge"]), Paragraph(f"{_safe(rule_id)} - {_safe(title)}", styles["rule_title"]) ]], colWidths=[34 * mm, 140 * mm])
    heading_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D9DDE1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    items: list[Any] = [Spacer(1, 2.5 * mm), heading_table, Spacer(1, 2 * mm)]
    if rule:
        items.append(_label_value("Требование", str(rule.get("requirement", "")), styles))
        source = str(rule.get("sourceLabel", ""))
        if rule.get("sourceLine") is not None:
            source += f", строка {rule.get('sourceLine')}"
        items.append(_label_value("Источник", source, styles))
    items.append(_label_value("Результат", str(result.get("explanation", "")), styles))
    items.append(_label_value("Проверено", str(result.get("checkedBy", "")), styles))
    items.append(_label_value("Проверка доказательств", _EVIDENCE_LABELS.get(str(result.get("evidenceStatus") or "not_required"), "доказательство не требовалось"), styles))

    coverage = result.get("coverage") or {}
    if coverage:
        items.append(_label_value(
            "Покрытие",
            f"фрагменты: {coverage.get('checkedCandidateCount', 0)} из {coverage.get('candidateCount', 0)}; полная область: {'да' if coverage.get('exhaustive') else 'нет'}",
            styles,
        ))
    if result.get("relatedRuleIds"):
        items.append(_label_value("Связанные правила", ", ".join(str(value) for value in result["relatedRuleIds"]), styles))
    if result.get("consistencyNotes"):
        items.append(_label_value("Проверка согласованности", " ".join(str(value) for value in result["consistencyNotes"]), styles))

    term_findings = result.get("termFindings") or []
    if term_findings:
        rows = [[_p("Термин", styles["table_head"]), _p("Тип", styles["table_head"]), _p("Статус", styles["table_head"])]]
        for item in term_findings:
            rows.append([_p(str(item.get("term", "")), styles["table_cell"]), _p(str(item.get("kind", "")), styles["table_cell"]), _p(str(item.get("status", "")), styles["table_cell"])])
        table = LongTable(rows, colWidths=[52 * mm, 56 * mm, 66 * mm], repeatRows=1)
        table.setStyle(_table_style())
        items.extend([Spacer(1, 1.5 * mm), Paragraph("Разбор обозначений", styles["subsection"]), table])

    matrix = result.get("coverageMatrix") or []
    if matrix:
        rows = [[_p("Фрагмент", styles["table_head"]), _p("Блоки", styles["table_head"]), _p("Полнота", styles["table_head"]), _p("Элементы", styles["table_head"])]]
        for row in matrix:
            elements = "; ".join(f"{item.get('name')}: {_MATRIX_LABELS.get(str(item.get('status')), str(item.get('status')))}" for item in row.get("items", []) or [])
            rows.append([
                _p(str(row.get("label", "")), styles["table_cell"]),
                _p(f"{row.get('checkedBlocks', 0)}/{row.get('totalBlocks', 0)}", styles["table_cell"]),
                _p("полная" if row.get("complete") else "неполная", styles["table_cell"]),
                _p(elements, styles["table_cell"]),
            ])
        table = LongTable(rows, colWidths=[49 * mm, 23 * mm, 25 * mm, 77 * mm], repeatRows=1)
        table.setStyle(_table_style())
        items.extend([Spacer(1, 1.5 * mm), Paragraph("Матрица полного покрытия", styles["subsection"]), table])

    evidence = result.get("evidence") or []
    if evidence:
        items.extend([Spacer(1, 1.5 * mm), Paragraph("Доказательства", styles["subsection"])])
        for item in evidence:
            meta = str(item.get("location", ""))
            if item.get("page"):
                meta += f", стр. {item.get('page')}"
            if item.get("start") is not None and item.get("end") is not None:
                meta += f", символы {item.get('start')}–{item.get('end')}"
            quote, shortened = _shorten(str(item.get("quote", "")), _MAX_QUOTE_CHARS)
            quote_text = f"<b>{_safe(meta)}</b><br/>{_safe(quote)}"
            context = str(item.get("context") or "").strip()
            if context and context != str(item.get("quote", "")).strip():
                context_value, context_shortened = _shorten(context, min(_MAX_QUOTE_CHARS, 900))
                quote_text += f"<br/><i>Контекст:</i> {_safe(context_value)}"
                shortened = shortened or context_shortened
            if shortened:
                quote_text += "<br/><i>Текст сокращён в PDF; полный вариант доступен в JSON/Markdown.</i>"
            quote_table = Table([[Paragraph(quote_text, styles["quote"]) ]], colWidths=[170 * mm])
            quote_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F8F8")),
                ("LINEBEFORE", (0, 0), (0, -1), 2, colors.HexColor("#BFC5CA")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            items.extend([quote_table, Spacer(1, 1.5 * mm)])

    if result.get("fix"):
        items.append(_label_value("Исправление", str(result.get("fix")), styles))
    return items


def _technical_section(report: dict[str, Any], profile: str | None, styles: dict[str, ParagraphStyle]) -> list[Any]:
    technical = report.get("technical", {}) or {}
    usage = report.get("llmUsage", {}) or {}
    routing = report.get("routing", {}) or {}
    rows = [
        ["Версия приложения", technical.get("appVersion", "")],
        ["Провайдер API", technical.get("provider", "")],
        ["Model ID", technical.get("model", "")],
        ["Профиль", "Ядро" if profile == "core" else "Полный набор" if profile == "full" else "-"],
        ["Хеш промпта проверки", technical.get("promptHash", "")],
        ["Хеш промпта структуры", technical.get("mapPromptHash") or "-"],
        ["Физических запросов", usage.get("requests", 0)],
        ["Повторных попыток", usage.get("retries", 0)],
        ["Оценочно входных токенов", usage.get("estimatedInputTokens", 0)],
        ["Ожидание rate limiter", f"{round(float(usage.get('rateLimitWaitMs', 0) or 0) / 1000)} с"],
        ["Время запросов к модели", f"{round(float(usage.get('requestDurationMs', 0) or 0) / 1000)} с"],
        ["Маршрутизация", f"{routing.get('strategy', '')}; явно задано: {routing.get('explicitRules', 0)}; fallback: {routing.get('fallbackRules', 0)}; фрагментов: {routing.get('fragments', 0)}; запросов проверки: {routing.get('checkRequests', 0)} (candidate: {routing.get('candidateRequests', 0)}, semantic: {routing.get('semanticRequests', 0)})"],
    ]
    table_rows = [[_p("Параметр", styles["table_head"]), _p("Значение", styles["table_head"])]] + [[_p(str(key), styles["table_cell"]), _p(str(value), styles["table_cell"])] for key, value in rows]
    table = LongTable(table_rows, colWidths=[55 * mm, 119 * mm], repeatRows=1)
    table.setStyle(_table_style())

    result: list[Any] = [Paragraph("Технические сведения", styles["section"]), table]
    traces = usage.get("traces", []) or []
    if traces:
        result.extend([Spacer(1, 3 * mm), Paragraph("Успешные обращения к модели", styles["subsection"])])
        for trace in traces:
            value = (
                f"{trace.get('operation')}: {trace.get('provider')}/{trace.get('model')}; "
                f"upstream={trace.get('providerName') or '-'}; HTTP {trace.get('httpStatus')}; "
                f"compatibility={'да' if trace.get('compatibilityMode') else 'нет'}; request={trace.get('requestId') or '-'}"
            )
            result.append(Paragraph(f"- {_safe(value)}", styles["small"]))

    diagnostics = usage.get("diagnostics", []) or []
    if diagnostics:
        result.extend([Spacer(1, 3 * mm), Paragraph("Диагностика API", styles["subsection"])])
        for item in diagnostics[:_MAX_DIAGNOSTICS]:
            value = (
                f"{item.get('operation')}, попытка {item.get('attempt')}, HTTP {item.get('httpStatus') or '-'}: "
                f"{item.get('message')}; retry={'да' if item.get('retryable') else 'нет'}; "
                f"ожидание={item.get('backoffMs', 0)} мс"
            )
            result.append(Paragraph(f"- {_safe(value)}", styles["small"]))
        if len(diagnostics) > _MAX_DIAGNOSTICS:
            result.append(Paragraph(f"Показаны первые {_MAX_DIAGNOSTICS} записей из {len(diagnostics)}. Полная диагностика доступна в JSON/Markdown.", styles["muted"]))

    warnings = report.get("warnings", []) or []
    if warnings:
        result.extend([Spacer(1, 3 * mm), Paragraph("Ограничения проверки", styles["subsection"])])
        result.extend(Paragraph(f"- {_safe(str(value))}", styles["body"]) for value in warnings)
    return result


def _styles(regular_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle("Eyebrow", parent=base["Normal"], fontName=bold_font, fontSize=9, leading=11, textColor=colors.HexColor("#6B7178"), spaceAfter=3),
        "title": ParagraphStyle("TitleRu", parent=base["Title"], fontName=bold_font, fontSize=22, leading=27, alignment=TA_LEFT, textColor=colors.HexColor("#202124"), spaceAfter=4),
        "document_name": ParagraphStyle("DocumentName", parent=base["Normal"], fontName=regular_font, fontSize=12, leading=16, textColor=colors.HexColor("#555C64"), splitLongWords=True),
        "section": ParagraphStyle("Section", parent=base["Heading2"], fontName=bold_font, fontSize=15, leading=19, textColor=colors.HexColor("#202124"), spaceBefore=4, spaceAfter=4),
        "subsection": ParagraphStyle("Subsection", parent=base["Heading3"], fontName=bold_font, fontSize=11, leading=14, textColor=colors.HexColor("#303337"), spaceBefore=3, spaceAfter=3),
        "body": ParagraphStyle("BodyRu", parent=base["BodyText"], fontName=regular_font, fontSize=9.5, leading=13, textColor=colors.HexColor("#202124"), spaceAfter=3, splitLongWords=True),
        "small": ParagraphStyle("SmallRu", parent=base["BodyText"], fontName=regular_font, fontSize=8.2, leading=10.5, textColor=colors.HexColor("#202124"), spaceAfter=2, splitLongWords=True),
        "muted": ParagraphStyle("Muted", parent=base["BodyText"], fontName=regular_font, fontSize=8.5, leading=11, textColor=colors.HexColor("#6B7178"), spaceAfter=3, splitLongWords=True),
        "summary": ParagraphStyle("Summary", parent=base["BodyText"], fontName=regular_font, fontSize=10, leading=14, textColor=colors.HexColor("#303337"), splitLongWords=True),
        "notice": ParagraphStyle("Notice", parent=base["BodyText"], fontName=regular_font, fontSize=9, leading=12.5, textColor=colors.HexColor("#634B15"), splitLongWords=True),
        "metric_label": ParagraphStyle("MetricLabel", parent=base["BodyText"], fontName=regular_font, fontSize=8.5, leading=11, textColor=colors.HexColor("#6B7178")),
        "metric_value": ParagraphStyle("MetricValue", parent=base["BodyText"], fontName=bold_font, fontSize=9.5, leading=12, textColor=colors.HexColor("#202124"), splitLongWords=True),
        "status_cell": ParagraphStyle("StatusCell", parent=base["BodyText"], fontName=regular_font, fontSize=8, leading=11, alignment=TA_CENTER, textColor=colors.HexColor("#444A50")),
        "badge": ParagraphStyle("Badge", parent=base["BodyText"], fontName=bold_font, fontSize=8, leading=10, alignment=TA_CENTER, textColor=colors.white),
        "rule_title": ParagraphStyle("RuleTitle", parent=base["BodyText"], fontName=bold_font, fontSize=10, leading=13, textColor=colors.HexColor("#202124"), splitLongWords=True),
        "label": ParagraphStyle("Label", parent=base["BodyText"], fontName=bold_font, fontSize=8.5, leading=11, textColor=colors.HexColor("#555C64")),
        "value": ParagraphStyle("Value", parent=base["BodyText"], fontName=regular_font, fontSize=9, leading=12.5, textColor=colors.HexColor("#202124"), splitLongWords=True),
        "quote": ParagraphStyle("Quote", parent=base["BodyText"], fontName=regular_font, fontSize=8.5, leading=11.5, textColor=colors.HexColor("#303337"), splitLongWords=True),
        "table_head": ParagraphStyle("TableHead", parent=base["BodyText"], fontName=bold_font, fontSize=7.6, leading=9.5, textColor=colors.HexColor("#303337"), splitLongWords=True),
        "table_cell": ParagraphStyle("TableCell", parent=base["BodyText"], fontName=regular_font, fontSize=7.4, leading=9.5, textColor=colors.HexColor("#303337"), splitLongWords=True),
    }


def _label_value(label: str, value: str, styles: dict[str, ParagraphStyle]) -> Table:
    table = Table([[_p(label, styles["label"]), _p(value, styles["value"]) ]], colWidths=[40 * mm, 134 * mm])
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


def _safe(value: str) -> str:
    cleaned = _INVALID_XML.sub(" ", value.replace("\r\n", "\n").replace("\r", "\n"))
    return escape(cleaned).replace("\n", "<br/>")


def _shorten(value: str, limit: int) -> tuple[str, bool]:
    normalized = value.strip()
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit].rstrip() + "...", True


def _register_fonts() -> tuple[str, str]:
    regular_path = _find_font(
        os.getenv("OSA_PDF_FONT_REGULAR") or os.getenv("PDF_FONT_REGULAR"),
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ],
    )
    bold_path = _find_font(
        os.getenv("OSA_PDF_FONT_BOLD") or os.getenv("PDF_FONT_BOLD"),
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ],
        required=False,
    ) or regular_path
    if not regular_path:
        raise RuntimeError(
            "Не найден Unicode-шрифт для PDF. Установите DejaVu Sans или задайте OSA_PDF_FONT_REGULAR и OSA_PDF_FONT_BOLD (или совместимые PDF_FONT_REGULAR/PDF_FONT_BOLD) в .env."
        )
    if "OSAReportRegular" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("OSAReportRegular", regular_path))
    if "OSAReportBold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("OSAReportBold", bold_path))
    return "OSAReportRegular", "OSAReportBold"


def _find_font(value: str | None, candidates: Iterable[str], *, required: bool = True) -> str | None:
    paths = [value, *candidates] if value else list(candidates)
    for raw in paths:
        if raw and Path(raw).expanduser().is_file():
            return str(Path(raw).expanduser())
    return None if not required else None
