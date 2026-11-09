"""OSA.Edu appearance and layout fixes for the upstream SlideGuard PDF renderer.

Import only inside the separate SlideGuard environment. Analysis and report
contents remain upstream; styles follow OSA's rule reports and panels can split.
"""
from datetime import datetime
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from slideguard.ui.report_generator import SlideGuardReportGenerator
from slideguard.ui.translations import Translator

from ..pdf_reporting import _register_fonts, _styles


class SplittablePanel(SlideGuardReportGenerator.RoundedPanel):
    def wrap(self, availWidth, availHeight):
        self.width, self.height = super().wrap(availWidth, availHeight)
        return self.width, self.height

    def split(self, availWidth, availHeight):
        width = availWidth - 2 * self.padding
        remaining = availHeight - 2 * self.padding
        if width <= 0 or remaining <= 0:
            return []
        first, rest = [], []
        for index, child in enumerate(self.children):
            _, height = child.wrapOn(self.canv, width, remaining)
            if height <= remaining:
                first.append(child)
                remaining -= height
                continue
            parts = child.splitOn(self.canv, width, remaining)
            if parts:
                first.append(parts[0])
                rest = parts[1:] + self.children[index + 1:]
            else:
                rest = self.children[index:]
            break
        if not first or not rest:
            return []
        return [type(self)(children, self.bg_color.hexval(), self.accent_color.hexval(),
                           padding=self.padding, radius=self.radius)
                for children in (first, rest)]


class ReportPanel(SplittablePanel):
    def __init__(self, inner_flowables, bg_color, accent_color, padding=8, radius=0):
        # Muted fills and thin borders match rule reports. Priority is still
        # conveyed by the upstream severity badge inside each finding.
        background = '#F5F6F7' if bg_color.lower() in {
            '#fff7e0', '#fffde7', '0xf5f6f7', '#f5f6f7'} else '#FFFFFF'
        super().__init__(inner_flowables, background, '#BFC5CA', padding, radius=0)

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(self.bg_color)
        canvas.setStrokeColor(HexColor('#D9DDE1'))
        canvas.setLineWidth(.5)
        canvas.rect(0, 0, self._w, self._h, stroke=1, fill=1)
        canvas.restoreState()
        y = self._h - self.padding
        for child, (_, height) in zip(self.children, self.child_sizes):
            y -= height
            child.drawOn(canvas, self.padding, y)


class PresentationReportGenerator(SlideGuardReportGenerator):
    RoundedPanel = ReportPanel

    def _register_fonts(self):
        self._font_regular, self._font_bold = _register_fonts()

    def _setup_custom_styles(self):
        styles = _styles(self._font_regular, self._font_bold)
        self.title_style = styles['title']
        self.section_style = styles['section']
        self.subsection_style = styles['subsection']
        self.normal_style = styles['body']
        self.score_style = styles['metric_value']

    def _get_severity_color(self, severity):
        return {1: '#2F6B3C', 2: '#82651A', 3: '#9B2C2C'}.get(severity, '#626970')

    def _get_score_color(self, score, max_score=5.0, higher_better=True):
        return '#303337'

    def _create_header(self, canvas):
        canvas.saveState()
        canvas.setFont(self._font_regular, 8)
        canvas.setFillColor(HexColor('#6B7178'))
        canvas.drawString(50, A4[1] - 40, self._t('report_title'))
        canvas.setStrokeColor(HexColor('#D9DDE1'))
        canvas.setLineWidth(.5)
        canvas.line(50, A4[1] - 49, A4[0] - 50, A4[1] - 49)
        canvas.restoreState()

    def _create_footer(self, canvas):
        canvas.saveState()
        canvas.setFont(self._font_regular, 8)
        canvas.setFillColor(HexColor('#6B7178'))
        canvas.drawString(50, 40, f"OSA.Edu · {self._t('report_generated')} "
                          f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        canvas.drawRightString(A4[0] - 50, 40,
                               f"{self._t('report_page')} {canvas.getPageNumber()}")
        canvas.restoreState()


def save_report(evaluation, path: Path, *, name: str, language: str, outcome: str):
    if outcome != 'completed':
        name = ('НЕПОЛНЫЙ РЕЗУЛЬТАТ — ' if language == 'ru' else 'INCOMPLETE RESULT — ') + name
    temporary = path.with_suffix('.tmp.pdf')
    try:
        PresentationReportGenerator(Translator(language)).save_report(
            evaluation, str(temporary), presentation_name=name)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def restore_evaluation(value):
    """Restore upstream's dynamic criterion models from saved JSON, without LLMs."""
    from slideguard.criteria import get_default_registry
    from slideguard.schemes import FullEvaluation

    evaluation = FullEvaluation.model_validate(value)
    registry = get_default_registry()
    collections = [slide.evaluations for slide in evaluation.slide_evaluations]
    if evaluation.deck_evaluations:
        collections.append(evaluation.deck_evaluations.evaluations)
    for results in collections:
        for criterion, result in (results or {}).items():
            if isinstance(result, dict) and not result.get('__slideguard_not_applicable__'):
                results[criterion] = registry.by_id[criterion].pydantic.model_validate(result)
    return evaluation
