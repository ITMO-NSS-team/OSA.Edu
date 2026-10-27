"""Glyph-aware PDF markup; never silently emit .notdef squares."""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from html import escape
from pathlib import Path

import fitz

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ..config import DATA_DIR


class PdfText:
    def __init__(self, regular: str, bold: str):
        self.glyphs = pdfmetrics.getFont(regular).face.charToGlyph
        required = 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя'
        for font in (regular, bold):
            if any(not pdfmetrics.getFont(font).face.charToGlyph.get(ord(c)) for c in required):
                raise RuntimeError('Выбранный PDF-шрифт не поддерживает кириллицу. Настройте OSA_PDF_FONT_REGULAR и OSA_PDF_FONT_BOLD.')
        self.fallbacks = []
        for value in [os.getenv('OSA_PDF_FONT_FALLBACK', ''), str(DATA_DIR / 'fonts' / 'DejaVuMathTeXGyre.ttf'),
                      '/usr/share/fonts/truetype/dejavu/DejaVuMathTeXGyre.ttf']:
            if not value or not Path(value).expanduser().is_file():
                continue
            path = str(Path(value).expanduser())
            name = 'OSALanguageSymbols' + hashlib.sha256(path.encode()).hexdigest()[:10]
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
            self.fallbacks.append((name, pdfmetrics.getFont(name).face.charToGlyph))

    def __call__(self, value) -> str:
        out = []
        for char in str(value or ''):
            if char == '\n':
                out.append('<br/>')
            elif char == '\t':
                out.append(' ')
            elif ord(char) < 32 or unicodedata.category(char) == 'Cf':
                continue
            elif self.glyphs.get(ord(char)):
                out.append(escape(char))
            else:
                fallback = next((name for name, glyphs in self.fallbacks if glyphs.get(ord(char))), None)
                if not fallback:
                    raise RuntimeError(f'Нет PDF-шрифта для символа U+{ord(char):04X}. Установите fonts-dejavu-extra или задайте OSA_PDF_FONT_FALLBACK; исходный текст сохранён в JSON.')
                out.append(f'<font name="{fallback}">{escape(char)}</font>')
        return ''.join(out)


def repair_astral_cmaps(content: bytes) -> bytes:
    """ReportLab 4 emits U+10000+ as 5 hex digits, but PDF needs UTF-16BE.

    Fix only the newly generated report's ToUnicode streams, never the thesis
    and never ReportLab's global functions (other exporters remain unchanged).
    """
    with fitz.open(stream=content, filetype='pdf') as document:
        changed = False
        visited = set()
        for page in document:
            for font in page.get_fonts():
                kind, reference = document.xref_get_key(font[0], 'ToUnicode')
                if kind != 'xref':
                    continue
                xref = int(reference.split()[0])
                if xref in visited:
                    continue
                visited.add(xref)
                stream = document.xref_stream(xref)
                def encode(match):
                    value = int(match[2], 16)
                    return b'<' + match[1] + b'> <' + chr(value).encode('utf-16-be').hex().upper().encode() + b'>'
                fixed = re.sub(rb'<([0-9A-Fa-f]{2})>\s*<([0-9A-Fa-f]{5,6})>', encode, stream)
                if fixed != stream:
                    document.update_stream(xref, fixed)
                    changed = True
        return document.tobytes(garbage=3, deflate=True) if changed else content
