from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import EXTRACTED_DIR
from .document.analyze import analyze_document
from .util import normalize_text


def extract_document(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        return _extract_pdf(path)
    if suffix == '.docx':
        return _extract_docx(path)
    raise ValueError('Поддерживаются только PDF и DOCX.')


def _extract_pdf(path: Path) -> dict[str, Any]:
    import pymupdf

    document = pymupdf.open(path)
    try:
        if document.needs_pass:
            raise RuntimeError('PDF защищён паролем и не может быть прочитан без пароля.')
        pages: list[dict[str, Any]] = []
        empty_pages: list[int] = []
        for page_index, page in enumerate(document):
            number = page_index + 1
            text = normalize_text(page.get_text('text', sort=True))
            if not text:
                empty_pages.append(number)
            pages.append({'number': number, 'text': text})
    finally:
        document.close()

    warnings: list[str] = []
    if empty_pages:
        warnings.append('На страницах без текстового слоя потребуется OCR: ' + ', '.join(map(str, empty_pages)) + '.')
    text = '\n\n'.join(f"<<<PAGE {page['number']}>>>\n{page['text']}" for page in pages)
    return analyze_document(text, pages, 'pdf', warnings or ([] if pages else ['Не удалось определить страницы PDF.']))


def _extract_docx(path: Path) -> dict[str, Any]:
    from docx import Document

    doc = Document(path)
    parts: list[str] = []
    # Keep paragraph order. Table cells are appended as text because python-docx does not expose
    # Mammoth's unified raw-text stream, but retaining them is safer than dropping them.
    for paragraph in doc.paragraphs:
        text = normalize_text(paragraph.text)
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            text = normalize_text(' '.join(cell.text for cell in row.cells))
            if text:
                parts.append(text)
    text = normalize_text('\n\n'.join(parts))
    return analyze_document(text, [], 'docx', ['DOCX не содержит надёжной привязки к страницам. Для финальной проверки вёрстки загрузите также PDF.'])


def extracted_path(job_id: str) -> Path:
    return EXTRACTED_DIR / f'{job_id}.json'


def save_extracted(job_id: str, document: dict[str, Any]) -> str:
    path = extracted_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)


def read_extracted(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))
