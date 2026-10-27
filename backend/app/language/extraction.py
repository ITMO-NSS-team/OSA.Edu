"""Proofreading views of PDF blocks with stable source ranges and explicit exclusions."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
from razdel import sentenize

from ..pdf_blocks import build_pdf_pages
from .spelling import dictionary

MAX_FRAGMENT_CHARS = 2100
MAX_CHUNK_CHARS = 4000


def split_ranges(text: str, limit: int = MAX_FRAGMENT_CHARS) -> list[tuple[int, int]]:
    """Prefer full sentences; retain every character, including boundary spaces."""
    sentences = list(sentenize(text))
    boundaries = [s.start for s in sentences[1:]]
    result = []
    start = 0
    while len(text) - start > limit:
        choices = [p for p in boundaries if start < p <= start + limit]
        end = max(choices) if choices else text.rfind(' ', start + max(1, limit // 2), start + limit)
        if end <= start:
            end = start + limit
        result.append((start, end))
        start = end
    if start < len(text):
        result.append((start, len(text)))
    return result


def _text(block: dict, lexicon) -> tuple[str, list[str]]:
    raw = str(block.get('rawText') or block.get('text') or '')
    raw = raw.replace('\u00ad\n', '').replace('\u00ad', '')
    unresolved = []

    def join(match):
        left, right = match.groups()
        combined, compound = left + right, left + '-' + right
        if lexicon is not None and lexicon.lookup(combined) and not lexicon.lookup(compound):
            return combined
        unresolved.append(compound)
        return compound

    raw = re.sub(r'([А-Яа-яЁё]+)-\n\s*([а-яё]+)', join, raw)
    # Block boundaries remain paragraph boundaries; line breaks inside a block
    # are presentation only. The original lines and bbox remain in source.
    return re.sub(r'\s+', ' ', raw.replace('\u00a0', ' ')).strip(), unresolved


def extract_fragments(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with fitz.open(path) as document:
        if document.needs_pass:
            raise ValueError('PDF защищён паролем; извлечение текста невозможно.')
        if not len(document):
            raise ValueError('PDF не содержит страниц.')
        page_count = len(document)
        pages, _, empty_pages = build_pdf_pages(document)
    lexicon = dictionary()
    fragments = []
    exclusions = []
    omitted = Counter()
    ambiguous_hyphens = 0
    for page in pages:
        for artifact in page.get('layoutArtifacts', []):
            exclusions.append({'page': page['number'], 'reason': artifact.get('layoutRole', 'margin'),
                               'bbox': artifact.get('bbox'), 'characters': len(artifact.get('text', ''))})
        for index, block in enumerate(page.get('blocks', [])):
            kind = str(block.get('type') or 'paragraph').lower()
            text, hyphens = _text(block, lexicon)
            reason = None
            if not text:
                reason = 'empty'
            elif kind in {'toc', 'bibliography', 'reference'}:
                reason = 'bibliography_or_toc'
            elif not re.search(r'[А-Яа-яЁё]', text):
                reason = 'non_russian_or_formula'
            elif kind in {'formula', 'equation'} and len(re.findall(r'[А-Яа-яЁё]{2,}', text)) < 3:
                reason = kind
            elif kind == 'code':
                reason = kind
            elif len(re.findall(r'\b(?:def|import|return|const|function)\b|[{}]', text)) >= 4:
                reason = 'probable_code'
            if reason:
                omitted[reason] += 1
                exclusions.append({'page': page['number'], 'blockId': f'p{page["number"]}-b{index}',
                                   'reason': reason, 'bbox': block.get('bbox'), 'characters': len(text)})
                continue
            ambiguous_hyphens += len(hyphens)
            for part, (start, end) in enumerate(split_ranges(text)):
                fragments.append({
                    'id': f'p{page["number"]}-b{index}-s{part}', 'page': page['number'],
                    'type': kind, 'text': text[start:end],
                    'source': {'page': page['number'], 'blockId': f'p{page["number"]}-b{index}',
                               'start': start, 'end': end, 'order': index, 'bbox': block.get('bbox'),
                               'sourceBlockIds': block.get('sourceBlockIds', [])},
                    'hyphenatedWords': hyphens,
                })
    if not fragments:
        raise ValueError('Не удалось извлечь русский текст. Для сканированного PDF нужен OCR.')
    for index, fragment in enumerate(fragments):
        # Context is read-only. It also crosses blocks/pages, preserving list
        # introductions and continuations without counting text twice.
        before = fragments[index - 1] if index else None
        after = fragments[index + 1] if index + 1 < len(fragments) else None
        fragment['contextBefore'] = before['text'][-900:] if before and fragment['page'] - before['page'] <= 1 else ''
        fragment['contextAfter'] = after['text'][:900] if after and after['page'] - fragment['page'] <= 1 else ''
        fragment['contextBeforeType'] = before['type'] if before else None
        fragment['contextAfterType'] = after['type'] if after else None
        fragment['contextBeforeId'] = before['id'] if before else None
        fragment['contextAfterId'] = after['id'] if after else None
    metadata = {
        'pageCount': page_count, 'emptyPages': empty_pages,
        'analyzableFragments': len(fragments),
        'analyzableCharacters': sum(len(f['text']) for f in fragments),
        'omittedBlocks': dict(omitted), 'excludedBlocks': exclusions,
        'ambiguousHyphenations': ambiguous_hyphens,
        'dictionaryAvailable': lexicon is not None,
        'normalization': 'Line wrapping and soft hyphens normalized; source ranges refer to normalized PDF blocks.',
    }
    return fragments, metadata


def make_chunks(fragments: list[dict[str, Any]], limit: int = MAX_CHUNK_CHARS) -> list[list[dict[str, Any]]]:
    chunks, current, used = [], [], 0
    for fragment in fragments:
        length = len(fragment['text']) + 80
        if current and used + length > limit:
            chunks.append(current)
            current, used = [], 0
        current.append(fragment)
        used += length
    if current:
        chunks.append(current)
    return chunks
