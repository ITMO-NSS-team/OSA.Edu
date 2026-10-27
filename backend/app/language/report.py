"""Standalone Unicode report rendered from saved JSON, without model calls."""
from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether

from ..pdf_reporting import _register_fonts
from .validation import CATEGORIES, CATEGORY_LABELS
from .pdf_text import PdfText, repair_astral_cmaps


def generate_pdf(job: dict[str, Any]) -> bytes:
    regular_font, bold_font = _register_fonts()
    p = PdfText(regular_font, bold_font)
    pdfmetrics.registerFontFamily(regular_font, normal=regular_font, bold=bold_font,
                                italic=regular_font, boldItalic=bold_font)
    result = job.get('result') or {}
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(210*mm, 297*mm), leftMargin=17*mm, rightMargin=17*mm,
                            topMargin=19*mm, bottomMargin=18*mm)
    styles = {
        'title': ParagraphStyle('osa-title', fontName=bold_font, fontSize=16, leading=21, spaceAfter=14),
        'head': ParagraphStyle('osa-head', fontName=bold_font, fontSize=10.5, leading=15, spaceBefore=12, spaceAfter=5),
        'body': ParagraphStyle('osa-body', fontName=regular_font, fontSize=9, leading=14, spaceAfter=6, splitLongWords=True),
        'muted': ParagraphStyle('osa-muted', fontName=regular_font, fontSize=8.3, leading=12, textColor=colors.HexColor('#475569'), spaceAfter=6),
        'quote': ParagraphStyle('osa-quote', fontName=regular_font, fontSize=8.7, leading=13, leftIndent=9, spaceAfter=5, splitLongWords=True),
    }
    story: list[Any] = [Paragraph('Языковая проверка ВКР', styles['title'])]
    def paragraph(value, kind='body'):
        story.append(Paragraph(value, styles[kind]))
    paragraph(f'<b>Файл:</b> {p(job.get("originalName"))}')
    paragraph(f'<b>Модель:</b> {p(result.get("model"))}', 'muted')
    paragraph(f'<b>Проверено пакетов:</b> {result.get("checkedChunks", 0)} из {result.get("totalChunks", 0)}. '
              f'<b>Предложений:</b> {result.get("totalIssues", 0)}. '
              f'<b>Стилистика:</b> {"включена" if result.get("styleEnabled") else "отключена"}.')
    if result.get('complete'):
        paragraph('<b>Статус: COMPLETE.</b> Обработан весь запланированный извлечённый текст; это не гарантия отсутствия всех ошибок.')
    else:
        paragraph('<b>Статус: НЕПОЛНАЯ ПРОВЕРКА.</b> PARTIAL. Часть текста не обработана; отсутствие замечаний не означает отсутствия ошибок.')
    if result.get('status') == 'cancelled' or job.get('status') == 'cancelled':
        paragraph('Проверка остановлена пользователем.')
    coverage = result.get('coverage') or {}
    if coverage:
        paragraph(f'Проверено символов: {coverage.get("checkedCharacters", 0)} / {coverage.get("totalCharacters", 0)}. '
                  f'Непроверенные страницы: {p(", ".join(map(str, coverage.get("uncheckedPages", []))) or "нет")}.', 'muted')
    counts = result.get('counts') or {category: sum(i.get('category') == category for i in result.get('issues', [])) for category in CATEGORIES}
    paragraph(' · '.join(f'{CATEGORY_LABELS[c]}: {counts.get(c, 0)}' for c in CATEGORIES), 'muted')
    paragraph('Замечания являются предложениями для ручной проверки. OSA.Edu не изменяла исходный PDF.', 'muted')
    for warning in result.get('warnings', []):
        paragraph(f'Внимание: {p(warning)}', 'muted')

    def append_issue(issue):
        title = f'Страница {issue["page"]} · {p(issue.get("categoryLabel"))}'
        # Keep only the short heading and label together. Quotes, replacements
        # and explanations must independently flow across arbitrarily many pages.
        story.append(KeepTogether([Paragraph(title, styles['head']), Paragraph('<b>Было:</b>', styles['muted'])]))
        paragraph(p(issue['quote']), 'quote')
        paragraph('<b>Предлагается:</b>', 'muted')
        paragraph(p(issue['replacement']), 'quote')
        paragraph(p(issue['explanation']))
        if issue.get('disputeReason'):
            paragraph(f'Спорное предложение: {p(issue["disputeReason"])}', 'muted')
        source = issue.get('source') or {}
        if source:
            paragraph(f'Источник: {p(issue.get("fragmentId"))}; блок {p(source.get("blockId"))}.', 'muted')
        story.append(Spacer(1, 3*mm))

    main = [i for i in result.get('issues', []) if i.get('category') != 'style']
    stylistic = [i for i in result.get('issues', []) if i.get('category') == 'style']
    paragraph('Основные замечания', 'head')
    for issue in sorted(main, key=lambda i: i['page']):
        append_issue(issue)
    if not main:
        paragraph('Уверенные локальные замечания не найдены в обработанных фрагментах. Это не гарантирует отсутствия всех ошибок.')
    if stylistic:
        paragraph('Стилистические рекомендации', 'head')
        for issue in sorted(stylistic, key=lambda i: i['page']):
            append_issue(issue)
    if result.get('disputed'):
        paragraph('Спорные рекомендации — обязательность исправлений не установлена', 'head')
        for issue in sorted(result['disputed'], key=lambda i: i['page']):
            append_issue(issue)
    unchecked = result.get('uncheckedFragments') or result.get('errors') or []
    if unchecked:
        paragraph('Техническое состояние: непроверенные фрагменты', 'head')
        for item in unchecked:
            pages = ', '.join(map(str, item.get('pages', [])))
            paragraph(f'Пакет {p(item.get("chunk", item.get("id")))}; страницы {p(pages or "неизвестны")}. '
                      f'{p(item.get("kind"))}: {p(item.get("message"))}', 'muted')
    usage = result.get('usage') or {}
    paragraph(f'Запросов: {usage.get("requests", 0)}; повторов: {usage.get("retries", 0)}; '
              f'оценочных входных токенов: {usage.get("estimatedInputTokens", 0)}.', 'muted')

    def footer(canvas, _doc):
        canvas.setFont(regular_font, 8)
        canvas.drawRightString(193*mm, 10*mm, f'Страница {canvas.getPageNumber()}')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return repair_astral_cmaps(buffer.getvalue())
