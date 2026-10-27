"""Offline container smoke check: python -m backend.app.language.check_environment."""
from __future__ import annotations

import fitz
from reportlab.pdfbase import pdfmetrics

from ..pdf_reporting import _register_fonts
from .report import generate_pdf
from .spelling import dictionary


def main():
    regular, bold = _register_fonts()
    alphabet = 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя№'
    for name in (regular, bold):
        glyphs = pdfmetrics.getFont(name).face.charToGlyph
        assert all(glyphs.get(ord(char)) for char in alphabet), f'{name} lacks Cyrillic glyphs'
    content = generate_pdf({'originalName': 'Русский отчёт.pdf', 'result': {'complete': False, 'issues': [], 'warnings': ['Формула: 𝑡 + 𝜔; Δ ± ∑.']}})
    with fitz.open(stream=content, filetype='pdf') as document:
        text = ''.join(page.get_text() for page in document)
    assert 'Языковая проверка ВКР' in text
    assert 'Русский отчёт.pdf' in text
    assert '𝑡 + 𝜔' in text
    assert dictionary() is not None, 'Russian Hunspell dictionary is not installed'
    print('Cyrillic regular/bold glyphs, PDF round trip, Russian dictionary: OK')


if __name__ == '__main__':
    main()
