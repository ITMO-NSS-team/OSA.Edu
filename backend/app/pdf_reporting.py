from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

STATUS_ORDER = ["violation", "pass", "uncertain", "not_checked", "not_applicable"]
STATUS_LABELS = {
    "violation": "Нарушено",
    "pass": "Выполнено",
    "uncertain": "Неопределённо",
    "not_checked": "Не обработано",
    "not_applicable": "Неприменимо",
}
EVIDENCE_LABELS = {
    "verified": "цитаты подтверждены по исходным блокам",
    "coverage_verified": "отсутствие подтверждено полным просмотром назначенной области",
    "rejected": "заявленное нарушение не подтверждено допустимой цитатой или полной матрицей",
    "not_required": "доказательство не требовалось",
}
MATRIX_LABELS = {
    "found": "найдено",
    "not_found": "не найдено",
    "ambiguous": "неоднозначно",
}
ELEMENT_LABELS = {
    "title": "Название",
    "abstract": "Аннотация",
    "introduction": "Введение",
    "goal": "Цель",
    "tasks": "Задачи",
    "defense_statements": "Положения на защиту",
    "chapter": "Глава",
    "chapter_conclusions": "Выводы по главе",
    "conclusion": "Заключение",
    "bibliography": "Библиография",
    "appendices": "Приложения",
    "other": "Другой фрагмент",
}

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = 18 * mm
RIGHT_MARGIN = 18 * mm
TOP_MARGIN = 20 * mm
BOTTOM_MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN


@lru_cache(maxsize=1)
def _register_fonts() -> tuple[str, str]:
    """Register a Unicode font pair without bundling third-party font files."""
    repo_root = Path(__file__).resolve().parents[2]
    configured_regular = os.getenv("OSA_PDF_FONT_REGULAR", "").strip()
    configured_bold = os.getenv("OSA_PDF_FONT_BOLD", "").strip()

    candidates: list[tuple[Path, Path]] = []
    if configured_regular:
        regular = Path(configured_regular).expanduser()
        candidates.append((regular, Path(configured_bold).expanduser() if configured_bold else regular))

    candidates.extend(
        [
            (repo_root / "config/fonts/DejaVuSans.ttf", repo_root / "config/fonts/DejaVuSans-Bold.ttf"),
            (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
            (Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"), Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")),
            (Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"), Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")),
            (Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"), Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf")),
            (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
            (Path("C:/Windows/Fonts/calibri.ttf"), Path("C:/Windows/Fonts/calibrib.ttf")),
            (Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
            (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf")),
        ]
    )

    for regular, bold in candidates:
        if not regular.is_file():
            continue
        selected_bold = bold if bold.is_file() else regular
        pdfmetrics.registerFont(TTFont("OSAReport", str(regular)))
        pdfmetrics.registerFont(TTFont("OSAReport-Bold", str(selected_bold)))
        pdfmetrics.registerFontFamily(
            "OSAReport",
            normal="OSAReport",
            bold="OSAReport-Bold",
            italic="OSAReport",
            boldItalic="OSAReport-Bold",
        )
        return "OSAReport", "OSAReport-Bold"

    raise RuntimeError(
        "Не найден Unicode-шрифт для PDF-отчёта. Установите DejaVu Sans, Liberation Sans или Noto Sans "
        "либо задайте OSA_PDF_FONT_REGULAR и OSA_PDF_FONT_BOLD в .env."
    )


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold = _register_fonts()
    base = getSampleStyleSheet()

    def style(name: str, parent: str = "BodyText", **kwargs: Any) -> ParagraphStyle:
        defaults = {
            "fontName": regular,
            "fontSize": 9.2,
            "leading": 12.3,
            "textColor": colors.HexColor("#172033"),
            "spaceAfter": 4,
            "wordWrap": "CJK",
            "splitLongWords": 1,
        }
        defaults.update(kwargs)
        return ParagraphStyle(name, parent=base[parent], **defaults)

    return {
        "title": style(
            "OSA-Title",
            fontName=bold,
            fontSize=20,
            leading=24,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#13213C"),
            spaceAfter=7 * mm,
        ),
        "subtitle": style(
            "OSA-Subtitle",
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#5C667A"),
            spaceAfter=5 * mm,
        ),
        "h1": style(
            "OSA-H1",
            fontName=bold,
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#173B6C"),
            spaceBefore=7 * mm,
            spaceAfter=3 * mm,
            keepWithNext=True,
        ),
        "h2": style(
            "OSA-H2",
            fontName=bold,
            fontSize=11.5,
            leading=15,
            textColor=colors.HexColor("#1F2E49"),
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
            keepWithNext=True,
        ),
        "body": style("OSA-Body"),
        "small": style("OSA-Small", fontSize=8, leading=10.5, textColor=colors.HexColor("#5C667A")),
        "label": style("OSA-Label", fontName=bold, fontSize=8.5, leading=11),
        "evidence_location": style(
            "OSA-EvidenceLocation",
            fontName=bold,
            fontSize=8.1,
            leading=10.5,
            textColor=colors.HexColor("#41516B"),
            spaceAfter=0,
        ),
        "quote": style(
            "OSA-Quote",
            fontSize=8.7,
            leading=12.2,
            textColor=colors.HexColor("#172033"),
            spaceBefore=0,
            spaceAfter=0,
        ),
        "warning": style(
            "OSA-Warning",
            fontSize=8.8,
            leading=12,
            borderColor=colors.HexColor("#D69E2E"),
            borderWidth=0.8,
            borderPadding=3 * mm,
            backColor=colors.HexColor("#FFF9E8"),
            spaceBefore=2 * mm,
            spaceAfter=4 * mm,
        ),
        "footer": style("OSA-Footer", fontSize=7.5, leading=9, textColor=colors.HexColor("#6B7280")),
    }


def _text(value: Any) -> str:
    if value is None:
        return "—"
    raw = str(value).strip()
    return raw if raw else "—"


def _html(value: Any) -> str:
    return escape(_text(value)).replace("\n", "<br/>")


def _paragraph(value: Any, styles: dict[str, ParagraphStyle], kind: str = "body") -> Paragraph:
    return Paragraph(_html(value), styles[kind])


def _label_value(label: str, value: Any, styles: dict[str, ParagraphStyle], kind: str = "body") -> Paragraph:
    return Paragraph(f"<b>{escape(label)}:</b> {_html(value)}", styles[kind])


def _bullet(value: Any, styles: dict[str, ParagraphStyle], kind: str = "body") -> Paragraph:
    return Paragraph(_html(value), styles[kind], bulletText="-")


def _table(
    rows: list[list[Any]],
    widths: list[float],
    styles: dict[str, ParagraphStyle],
    *,
    header: bool = False,
    compact: bool = False,
) -> Table:
    prepared: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        prepared_row: list[Any] = []
        for cell in row:
            if isinstance(cell, (Paragraph, Table, Spacer)):
                prepared_row.append(cell)
            else:
                kind = "label" if header and row_index == 0 else ("small" if compact else "body")
                prepared_row.append(_paragraph(cell, styles, kind))
        prepared.append(prepared_row)

    table_class = LongTable if len(prepared) > 8 else Table
    table = table_class(prepared, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4 if compact else 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4 if compact else 5),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF6")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#173B6C")),
            ]
        )
    for index in range(1 if header else 0, len(prepared)):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor("#FAFBFC")))
    table.setStyle(TableStyle(commands))
    return table


def _evidence_card(
    item: dict[str, Any],
    index: int,
    styles: dict[str, ParagraphStyle],
) -> KeepTogether:
    """Render one evidence item as an indivisible card.

    A bordered Paragraph can be split at a page boundary, and in some PDF
    viewers its border/padding may then overlap the next quote. A small table
    calculates the complete card height before placement, while KeepTogether
    moves the card to the next page when there is not enough room.
    """
    location = _text(item.get("location"))
    if item.get("page"):
        location += f", стр. {item['page']}"

    location_paragraph = Paragraph(
        f"Доказательство {index}. {escape(location)}",
        styles["evidence_location"],
    )
    quote_paragraph = Paragraph(
        f"«{_html(item.get('quote'))}»",
        styles["quote"],
    )

    card = Table(
        [[location_paragraph], [quote_paragraph]],
        colWidths=[CONTENT_WIDTH],
        hAlign="LEFT",
        splitByRow=1,
    )
    card.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.55, colors.HexColor("#AAB8CC")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.35, colors.HexColor("#C7D1DF")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F7")),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F7F9FC")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("TOPPADDING", (0, 1), (-1, 1), 8),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
            ]
        )
    )
    return KeepTogether([card, Spacer(1, 2.8 * mm)])


def _score_text(report: dict[str, Any]) -> str:
    score = report.get("score")
    if score is None:
        return "Не рассчитана"
    suffix = " (предварительно)" if report.get("scoreIsProvisional") else ""
    return f"{score}/100{suffix}"


def _add_document_map(story: list[Any], report: dict[str, Any], styles: dict[str, ParagraphStyle]) -> None:
    document_map = report.get("documentMap")
    if not isinstance(document_map, dict):
        return

    elements = document_map.get("elements", []) or []
    extraction = document_map.get("extraction", {}) or {}
    review = document_map.get("review", {}) or {}
    story.append(Paragraph("Структурная карта", styles["h1"]))
    story.append(
        _table(
            [
                ["Статус", "готова" if document_map.get("status") == "ready" else "частичная"],
                ["Подтверждена пользователем", "да" if review.get("confirmedByUser") else "нет"],
                ["Обработано блоков", f"{extraction.get('processedBlocks', 0)} из {extraction.get('totalBlocks', 0)}"],
                ["Смысловых диапазонов", len(elements)],
                ["Неоднозначных элементов", sum(1 for item in elements if item.get("state") == "ambiguous")],
            ],
            [55 * mm, CONTENT_WIDTH - 55 * mm],
            styles,
            compact=True,
        )
    )
    story.append(Spacer(1, 3 * mm))

    important = {
        "title",
        "introduction",
        "goal",
        "tasks",
        "defense_statements",
        "chapter",
        "chapter_conclusions",
        "conclusion",
        "bibliography",
    }
    displayed = [item for item in elements if item.get("type") in important]
    if displayed:
        rows: list[list[Any]] = [["Тип", "Фрагмент", "Границы", "Страницы", "Статус"]]
        for element in displayed:
            pages = element.get("pages", []) or []
            page_text = f"{pages[0]}-{pages[-1]}" if pages else "—"
            rows.append(
                [
                    ELEMENT_LABELS.get(str(element.get("type")), str(element.get("type") or "—")),
                    element.get("label", ""),
                    f"{element.get('startBlockId', '—')} → {element.get('endBlockId', '—')}",
                    page_text,
                    "неоднозначен" if element.get("state") == "ambiguous" else "подтверждён",
                ]
            )
        story.append(
            _table(
                rows,
                [24 * mm, 55 * mm, 40 * mm, 20 * mm, CONTENT_WIDTH - 139 * mm],
                styles,
                header=True,
                compact=True,
            )
        )

    issues = document_map.get("issues", []) or []
    if issues:
        story.append(Paragraph("Замечания к карте", styles["h2"]))
        for issue in issues:
            story.append(_bullet(issue.get("message", ""), styles))


def _add_rule_result(
    story: list[Any],
    result: dict[str, Any],
    rule: dict[str, Any] | None,
    styles: dict[str, ParagraphStyle],
) -> None:
    title = str(result.get("ruleId", "—"))
    if rule and rule.get("title"):
        title += f" - {rule['title']}"
    story.append(Paragraph(_html(title), styles["h2"]))

    meta_rows: list[list[Any]] = []
    if rule:
        meta_rows.extend(
            [
                ["Требование", rule.get("requirement", "")],
                ["Источник", f"{rule.get('sourceLabel', '—')}, строка {rule.get('sourceLine', '—')}"],
                ["Категория", rule.get("category", "—")],
            ]
        )
    meta_rows.extend(
        [
            ["Проверено", result.get("checkedBy", "—")],
            ["Проверка доказательств", EVIDENCE_LABELS.get(str(result.get("evidenceStatus") or "not_required"), "доказательство не требовалось")],
        ]
    )
    story.append(_table(meta_rows, [43 * mm, CONTENT_WIDTH - 43 * mm], styles, compact=True))
    story.append(Spacer(1, 2 * mm))
    story.append(_label_value("Результат", result.get("explanation", ""), styles))

    coverage = result.get("coverage")
    if isinstance(coverage, dict):
        story.append(
            _label_value(
                "Фрагменты",
                f"{coverage.get('checkedCandidateCount', 0)} из {coverage.get('candidateCount', 0)}; "
                f"полная область: {'да' if coverage.get('exhaustive') else 'нет'}",
                styles,
                "small",
            )
        )
    if result.get("checkedFragments"):
        story.append(_label_value("Проверенные фрагменты", ", ".join(map(str, result["checkedFragments"])), styles, "small"))
    if result.get("relatedRuleIds"):
        story.append(_label_value("Связанные правила", ", ".join(map(str, result["relatedRuleIds"])), styles, "small"))
    if result.get("consistencyNotes"):
        story.append(_label_value("Проверка согласованности", " ".join(map(str, result["consistencyNotes"])), styles, "small"))

    term_findings = result.get("termFindings", []) or []
    if term_findings:
        story.append(Paragraph("Разбор обозначений", styles["h2"]))
        rows = [["Термин", "Тип", "Статус"]]
        rows.extend([[item.get("term", ""), item.get("kind", ""), item.get("status", "")] for item in term_findings])
        story.append(_table(rows, [55 * mm, 45 * mm, CONTENT_WIDTH - 100 * mm], styles, header=True, compact=True))

    matrix = result.get("coverageMatrix", []) or []
    if matrix:
        story.append(Paragraph("Матрица полного покрытия", styles["h2"]))
        rows = [["Фрагмент", "Блоки", "Полнота", "Элементы"]]
        for row in matrix:
            items = "; ".join(
                f"{item.get('name', '—')}: {MATRIX_LABELS.get(str(item.get('status')), str(item.get('status') or '—'))}"
                for item in row.get("items", []) or []
            )
            rows.append(
                [
                    row.get("label", ""),
                    f"{row.get('checkedBlocks', 0)}/{row.get('totalBlocks', 0)}",
                    "полная" if row.get("complete") else "неполная",
                    items,
                ]
            )
        story.append(_table(rows, [45 * mm, 22 * mm, 25 * mm, CONTENT_WIDTH - 92 * mm], styles, header=True, compact=True))

    evidence = result.get("evidence", []) or []
    if evidence:
        story.append(Paragraph("Подтверждённые доказательства", styles["h2"]))
        for index, item in enumerate(evidence, start=1):
            story.append(_evidence_card(item, index, styles))

    if result.get("fix"):
        story.append(_label_value("Исправление", result.get("fix"), styles))

    story.append(Spacer(1, 2 * mm))
    story.append(HRFlowable(width="100%", thickness=0.35, color=colors.HexColor("#D7DEE8"), spaceBefore=2 * mm, spaceAfter=2 * mm))


def _add_technical(story: list[Any], report: dict[str, Any], styles: dict[str, ParagraphStyle]) -> None:
    usage = report.get("llmUsage", {}) or {}
    technical = report.get("technical", {}) or {}
    routing = report.get("routing", {}) or {}

    story.append(PageBreak())
    story.append(Paragraph("Технические сведения", styles["h1"]))
    story.append(
        _table(
            [
                ["Версия приложения", technical.get("appVersion", "—")],
                ["Провайдер API", technical.get("provider", "—")],
                ["Model ID", technical.get("model", "—")],
                ["Хеш промпта проверки", technical.get("promptHash", "—")],
                ["Хеш промпта структуры", technical.get("mapPromptHash") or "—"],
            ],
            [55 * mm, CONTENT_WIDTH - 55 * mm],
            styles,
            compact=True,
        )
    )

    story.append(Paragraph("Нагрузка LLM", styles["h1"]))
    story.append(
        _table(
            [
                ["Физических запросов", usage.get("requests", 0)],
                ["Повторных попыток", usage.get("retries", 0)],
                ["Проверено пакетов", usage.get("packets", 0)],
                ["Передано правил/объектов", usage.get("candidates", 0)],
                ["Оценочно входных токенов", usage.get("estimatedInputTokens", 0)],
                ["Ожидание rate limiter", f"{round(float(usage.get('rateLimitWaitMs', 0) or 0) / 1000)} с"],
                ["Время запросов к модели", f"{round(float(usage.get('requestDurationMs', 0) or 0) / 1000)} с"],
                [
                    "Маршрутизация",
                    f"{routing.get('strategy', '—')}; явно задано: {routing.get('explicitRules', 0)}; "
                    f"fallback: {routing.get('fallbackRules', 0)}; фрагментов: {routing.get('fragments', 0)}; "
                    f"запросов проверки: {routing.get('checkRequests', 0)}",
                ],
            ],
            [55 * mm, CONTENT_WIDTH - 55 * mm],
            styles,
            compact=True,
        )
    )

    traces = usage.get("traces", []) or []
    if traces:
        story.append(Paragraph("Успешные обращения к модели", styles["h2"]))
        for trace in traces:
            story.append(
                _bullet(
                    f"{trace.get('operation', '—')}: {trace.get('provider', '—')}/{trace.get('model', '—')}; "
                    f"upstream={trace.get('providerName') or '—'}; HTTP {trace.get('httpStatus', '—')}; "
                    f"compatibility={'да' if trace.get('compatibilityMode') else 'нет'}; request={trace.get('requestId') or '—'}",
                    styles,
                    "small",
                )
            )

    diagnostics = usage.get("diagnostics", []) or []
    if diagnostics:
        story.append(Paragraph("Диагностика API", styles["h2"]))
        for item in diagnostics:
            quota = f"; quota={item.get('quotaMetric')}" if item.get("quotaMetric") else ""
            story.append(
                _bullet(
                    f"{item.get('operation', '—')}, попытка {item.get('attempt', '—')}, HTTP {item.get('httpStatus') or '—'}: "
                    f"{item.get('message', '')}; retry={str(bool(item.get('retryable'))).lower()}; "
                    f"ожидание={item.get('backoffMs', 0)} мс{quota}",
                    styles,
                    "small",
                )
            )

    warnings = report.get("warnings", []) or []
    if warnings:
        story.append(Paragraph("Ограничения проверки", styles["h1"]))
        for warning in warnings:
            story.append(_bullet(warning, styles))


def _page_decorator(name: str, regular_font: str):
    header = "OSA.Edu - протокол нормоконтроля"
    generated = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")

    def draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D7DEE8"))
        canvas.setLineWidth(0.35)
        canvas.line(LEFT_MARGIN, PAGE_HEIGHT - 12 * mm, PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 12 * mm)
        canvas.setFont(regular_font, 7.5)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 9.2 * mm, header)
        canvas.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 9.2 * mm, f"Страница {doc.page}")
        canvas.line(LEFT_MARGIN, 11 * mm, PAGE_WIDTH - RIGHT_MARGIN, 11 * mm)
        canvas.drawString(LEFT_MARGIN, 7.3 * mm, f"Сформировано: {generated}")
        footer_name = name if len(name) <= 80 else name[:77] + "..."
        canvas.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, 7.3 * mm, footer_name)
        canvas.restoreState()

    return draw


def report_to_pdf(name: str, report: dict[str, Any]) -> bytes:
    """Render the structured OSA.Edu report to a self-contained PDF document."""
    regular, _bold = _register_fonts()
    styles = _styles()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=RIGHT_MARGIN,
        leftMargin=LEFT_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=f"Протокол нормоконтроля - {name}",
        author="OSA.Edu",
        subject="Отчёт автоматизированной проверки ВКР",
    )

    counts = report.get("counts", {}) or {}
    score = _score_text(report)
    story: list[Any] = [
        Paragraph("Протокол нормоконтроля", styles["title"]),
        Paragraph(_html(name), styles["subtitle"]),
        _table(
            [
                ["Оценка", "Покрытие правил", "Покрытие фрагментов"],
                [
                    score,
                    f"{round(float(report.get('coverage', 0) or 0) * 100)} %\n"
                    f"{report.get('checkedRules', 0)} из {report.get('totalRules', 0)} правил",
                    f"{round(float(report.get('candidateCoverage', 0) or 0) * 100)} %",
                ],
            ],
            [CONTENT_WIDTH / 3] * 3,
            styles,
            header=True,
        ),
        Spacer(1, 4 * mm),
        _table(
            [
                ["Нарушено", "Выполнено", "Неопределённо", "Не обработано", "Неприменимо"],
                [
                    counts.get("violation", 0),
                    counts.get("pass", 0),
                    counts.get("uncertain", 0),
                    counts.get("notChecked", 0),
                    counts.get("notApplicable", 0),
                ],
            ],
            [CONTENT_WIDTH / 5] * 5,
            styles,
            header=True,
            compact=True,
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            "Оценка не является решением о допуске к защите. Смысловые замечания и предлагаемые исправления необходимо проверить вручную.",
            styles["warning"],
        ),
        Paragraph("Краткий итог", styles["h1"]),
        _paragraph(report.get("summary", ""), styles),
    ]

    _add_document_map(story, report, styles)

    catalog = {item.get("id"): item for item in report.get("ruleCatalog", []) or []}
    for status in STATUS_ORDER:
        items = [item for item in report.get("ruleResults", []) or [] if item.get("status") == status]
        story.append(Paragraph(f"{STATUS_LABELS.get(status, status)} ({len(items)})", styles["h1"]))
        if not items:
            story.append(_paragraph("В этой категории правил нет.", styles, "small"))
            continue
        for result in items:
            _add_rule_result(story, result, catalog.get(result.get("ruleId")), styles)

    _add_technical(story, report, styles)

    decorator = _page_decorator(name, regular)
    document.build(story, onFirstPage=decorator, onLaterPages=decorator)
    return buffer.getvalue()
