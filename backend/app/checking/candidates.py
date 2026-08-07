from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable
import regex as re

from .common import mapped_excluded_ids, contents_page_range, is_code_or_prompt, looks_like_contents
from ..util import compact

# Candidate-first checking keeps recall in code and lets the LLM make only a
# small semantic decision. A candidate is never treated as a violation by itself.

LEXICONS: dict[str, str] = {
    'filler': r'\b(?:нужно|надо|значит|заключа(?:ется|ются|лось)|в\s+принципе|как\s+бы|некоторым\s+образом)\b',
    'condescending': r'\b(?:очевидно|несомненно|безусловно|легко\s+(?:видеть|заметить|показать)|хорошо\s+известно|общеизвестно|разумеется|ясно,?\s+что|как\s+известно)\b',
    'overclaim': r'\b(?:уникальн\p{L}*|высокоэффективн\p{L}*|исключительн\p{L}*|беспрецедентн\p{L}*|значительн\p{L}+\s+вклад|наглядно\s+демонстрир\p{L}*|существенно\s+лучше|весьма\s+эффективн\p{L}*)\b',
    'diminutive': r'\b(?:лампочк\p{L}*|программк\p{L}*|строчк\p{L}*|стрелочк\p{L}*|кнопочк\p{L}*|табличк\p{L}*|файлик\p{L}*|скриптик\p{L}*)\b',
    'to-est': r'\bто\s+есть\b',
    'sentence-start': r'(?:^|[.!?…]\s+)(?:А|Но|Так\s+как|То\s+есть|Т\.?\s*к\.|Т\.?\s*е\.)\s+',
}

_PRONOUNS = re.compile(
    r'(?<![\p{L}\p{N}_-])(?:я|мы|нас|нам|нами|меня|мне|мной|мною|наш(?:а|е|и|его|ему|им|ими|ей|ем)?)(?![\p{L}\p{N}_-])',
    re.I,
)
_FIRST_PERSON_VERB = re.compile(
    r'(?<![\p{L}\p{N}_-])(?:рассмотрим|предложим|получим|проведём|проведем|разработаем|исследуем|покажем|опишем|отметим|перейдём|перейдем|сравним|используем|применим|выберем|определим|сформулируем|представим|приведём|приведем|обозначим|построим|реализуем|оценим|будем|можем|видим|считаем|полагаем)(?![\p{L}\p{N}_-])',
    re.I,
)
_NUMERAL_RE = re.compile(r'(?<![\d.,:/-])(?<![\p{L}\p{N}_])([0-9])\s+([А-Яа-яЁё]{3,})')
_NUMERAL_SKIP_BEFORE = re.compile(r'(?:глав\p{L}*|раздел\p{L}*|рисун\p{L}*|рис\.|табл\p{L}*|стр\.|пункт\p{L}*|формул\p{L}*|этап\p{L}*|верси\p{L}*|№|прилож\p{L}*)\s*$', re.I)
_NUMERAL_SKIP_AFTER = re.compile(r'^(?:мм|см|км|кг|мс|мкс|гб|мб|кб|бит|байт|раз|процент\p{L}*|%|шт)\b', re.I)
_ABBREV_RE = re.compile(r'(?<![\p{L}\p{N}_@-])([A-ZА-ЯЁ]{2,12}(?:[-–][A-ZА-ЯЁ0-9]{1,12})?(?:@[A-Za-z0-9]{1,4})?\d{0,3})(?![\p{L}\p{N}_@])')
_ABBREV_STOP = {
    'ВКР', 'ГОСТ', 'РФ', 'СССР', 'США', 'ЕС', 'ООН', 'МГУ', 'СПБГУ', 'ИТМО',
    'ГЛАВА', 'ВВЕДЕНИЕ', 'ЗАКЛЮЧЕНИЕ', 'СПИСОК', 'ЛИТЕРАТУРЫ', 'ПРИЛОЖЕНИЕ',
    'РЕФЕРАТ', 'ОГЛАВЛЕНИЕ', 'СОДЕРЖАНИЕ', 'ТАБЛИЦА', 'РИСУНОК', 'ВЫВОДЫ',
    'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII',
}


def _hash(*parts: Any) -> str:
    raw = '\x01'.join(str(x) for x in parts)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _sentence_span(text: str, offset: int) -> tuple[int, int]:
    # Conservative sentence boundaries. Abbreviations may make this span a little
    # larger, which is preferable to clipping the evidence.
    left = max(text.rfind('.', 0, offset), text.rfind('!', 0, offset), text.rfind('?', 0, offset), text.rfind('\n', 0, offset))
    start = left + 1 if left >= 0 else 0
    right_candidates = [x for mark in '.!?' if (x := text.find(mark, offset)) >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    while start < right and text[start].isspace():
        start += 1
    while right > start and text[right - 1].isspace():
        right -= 1
    return start, right


def _context(text: str, start: int, end: int, before: int = 160, after: int = 220) -> str:
    left = max(0, start - before)
    right = min(len(text), end + after)
    return ('…' if left else '') + compact(text[left:right]) + ('…' if right < len(text) else '')


def _block_context(document: dict, block: dict, start: int, end: int) -> str:
    blocks = document.get('blocks', [])
    try:
        index = next(i for i, item in enumerate(blocks) if item.get('id') == block.get('id'))
    except StopIteration:
        return _context(block.get('text', ''), start, end)
    selected: list[str] = []
    for item in blocks[max(0, index - 2):min(len(blocks), index + 3)]:
        if item.get('page') != block.get('page'):
            continue
        selected.append(compact(item.get('text', '')))
    return compact(' '.join(selected))[:1200]


def _split_word_continuation(document: dict, block: dict, offset: int) -> bool:
    if offset > 1 or not block.get('text', '') or not block.get('text', '')[0].islower():
        return False
    blocks = document.get('blocks', [])
    try:
        index = next(i for i, item in enumerate(blocks) if item.get('id') == block.get('id'))
    except StopIteration:
        return False
    if index <= 0 or blocks[index - 1].get('page') != block.get('page'):
        return False
    return str(blocks[index - 1].get('text', '')).rstrip().endswith(('\u00ad', '-'))


def _cyrillic_ratio(value: str) -> float:
    letters = re.findall(r'\p{L}', value)
    return len(re.findall(r'[А-ЯЁа-яё]', value)) / len(letters) if letters else 0.0


def _make(family: str, block: dict, start: int, end: int, *, context: str | None = None, meta: dict | None = None) -> dict:
    text = block.get('text', '')
    quote = text[start:end]
    return {
        'id': _hash(family, block.get('id'), start, end, quote),
        'family': family,
        'blockId': block.get('id'),
        'page': block.get('page'),
        'location': block.get('location', ''),
        'start': start,
        'end': end,
        'quote': quote,
        'context': context if context is not None else _context(text, start, end),
        'meta': meta or {},
    }


def _narrative_blocks(document: dict) -> list[dict]:
    excluded = mapped_excluded_ids(document)
    contents_pages = contents_page_range(document)
    allowed = {'paragraph', 'list'}
    result: list[dict] = []
    for block in document.get('blocks', []):
        text = block.get('text', '')
        if block.get('id') in excluded or block.get('page') in contents_pages:
            continue
        if block.get('type') not in allowed or looks_like_contents(text) or is_code_or_prompt(text):
            continue
        letters = re.findall(r'\p{L}', text)
        cyr = re.findall(r'[А-ЯЁа-яё]', text)
        if letters and len(cyr) / len(letters) >= .28:
            result.append(block)
    return result


def _lexical(document: dict, family: str) -> list[dict]:
    pattern = re.compile(LEXICONS[family], re.I)
    result: list[dict] = []
    for block in _narrative_blocks(document):
        text = block.get('text', '')
        for match in pattern.finditer(text):
            if _split_word_continuation(document, block, match.start()):
                continue
            start, end = _sentence_span(text, match.start())
            result.append(_make(family, block, start, end, context=_block_context(document, block, match.start(), match.end()), meta={'marker': match.group(0)}))
    return _dedupe(result)


def _impersonal(document: dict) -> list[dict]:
    result: list[dict] = []
    for block in _narrative_blocks(document):
        text = block.get('text', '')
        matches = sorted([*_PRONOUNS.finditer(text), *_FIRST_PERSON_VERB.finditer(text)], key=lambda x: x.start())
        seen_sentences: set[tuple[int, int]] = set()
        for match in matches:
            if _split_word_continuation(document, block, match.start()):
                continue
            span = _sentence_span(text, match.start())
            if span in seen_sentences:
                continue
            seen_sentences.add(span)
            result.append(_make('impersonal', block, span[0], span[1], context=_block_context(document, block, match.start(), match.end()), meta={'marker': match.group(0)}))
    return result


def _numerals(document: dict) -> list[dict]:
    result: list[dict] = []
    for block in _narrative_blocks(document):
        text = block.get('text', '')
        for match in _NUMERAL_RE.finditer(text):
            before = text[max(0, match.start() - 45):match.start()]
            after = text[match.end(1) + 1:match.end(1) + 24]
            if _NUMERAL_SKIP_BEFORE.search(before) or _NUMERAL_SKIP_AFTER.match(after.strip()):
                continue
            start, end = _sentence_span(text, match.start())
            result.append(_make('numerals', block, start, end, context=_block_context(document, block, match.start(), match.end()), meta={'digit': match.group(1), 'word': match.group(2)}))
    return _dedupe(result)


def _abbreviation_occurrences(document: dict, token: str) -> list[dict]:
    pattern = re.compile(rf'(?<![\p{{L}}\p{{N}}_@-]){re.escape(token)}(?![\p{{L}}\p{{N}}_@])')
    rows: list[dict] = []
    for block in document.get('blocks', []):
        match = pattern.search(block.get('text', ''))
        if match:
            rows.append({'block': block, 'start': match.start(), 'end': match.end()})
    return rows


def _abbrev_first_use(document: dict) -> list[dict]:
    skip = {'toc', 'bibliography', 'table', 'code', 'formula', 'caption'}
    excluded = mapped_excluded_ids(document)
    toc_pages = contents_page_range(document)
    seen: set[str] = set()
    result: list[dict] = []
    for block in sorted(document.get('blocks', []), key=lambda x: int(x.get('order', 0))):
        if block.get('type') in skip or block.get('id') in excluded or block.get('page') in toc_pages:
            continue
        text = block.get('text', '')
        if _cyrillic_ratio(text) < .20:
            continue
        for match in _ABBREV_RE.finditer(text):
            token = match.group(1)
            key = token.upper().replace('–', '-')
            if key in seen or key in _ABBREV_STOP:
                continue
            seen.add(key)
            sentence = _sentence_span(text, match.start())
            occurrences = _abbreviation_occurrences(document, token)
            # The abbreviation list is often near the end of a thesis. Keep both
            # early and late supporting occurrences so the judge can see an
            # available Russian expansion without receiving the whole document.
            support_rows: list[dict] = []
            for row in [*occurrences[:3], *occurrences[-3:]]:
                if row not in support_rows:
                    support_rows.append(row)
            supporting: list[str] = []
            for row in support_rows:
                other = row['block']
                if other.get('id') == block.get('id'):
                    continue
                supporting.append(f"[{other.get('id')}] {compact(other.get('text', ''))[:240]}")
                if len(supporting) >= 4:
                    break
            context = _context(text, sentence[0], sentence[1], before=260, after=220)
            if supporting:
                context += '\nДругие употребления/список сокращений:\n' + '\n'.join(supporting)
            result.append(_make('abbrev-first-use', block, match.start(), match.end(), context=context, meta={'token': token, 'occurrences': len(occurrences)}))
    return result


def _abbrev_in_heading(document: dict) -> list[dict]:
    allowed = {'title', 'heading', 'toc'}
    excluded = mapped_excluded_ids(document)
    result: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    occurrence_counts: dict[tuple[str, str], int] = {}
    for block in document.get('blocks', []):
        if block.get('type') not in allowed or block.get('id') in excluded:
            continue
        for match in _ABBREV_RE.finditer(block.get('text', '')):
            token = match.group(1)
            if token.upper() in _ABBREV_STOP:
                continue
            key = (token.upper().replace('–', '-'), str(block.get('type')))
            occurrence_counts[key] = occurrence_counts.get(key, 0) + 1
            if key not in seen:
                seen[key] = _make('abbrev-in-heading', block, match.start(), match.end(), context=_block_context(document, block, match.start(), match.end()), meta={'token': token, 'blockType': block.get('type')})
    for key, item in seen.items():
        item['meta']['occurrencesInHeadings'] = occurrence_counts[key]
        result.append(item)
    return _dedupe(result)


def _abbrev_foreign(document: dict) -> list[dict]:
    result: list[dict] = []
    for item in _abbrev_first_use(document):
        token = str(item.get('meta', {}).get('token', ''))
        if re.search(r'[A-Z]', token):
            item = dict(item)
            item['family'] = 'abbrev-foreign'
            item['id'] = _hash('abbrev-foreign', item['blockId'], item['start'], item['end'], token)
            result.append(item)
    return result


def _dedupe(items: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        if item['id'] in seen:
            continue
        seen.add(item['id'])
        result.append(item)
    return result


_BUILDERS: dict[str, Callable[[dict], list[dict]]] = {
    'impersonal': _impersonal,
    'numerals': _numerals,
    'filler': lambda doc: _lexical(doc, 'filler'),
    'condescending': lambda doc: _lexical(doc, 'condescending'),
    'overclaim': lambda doc: _lexical(doc, 'overclaim'),
    'diminutive': lambda doc: _lexical(doc, 'diminutive'),
    'to-est': lambda doc: _lexical(doc, 'to-est'),
    'sentence-start': lambda doc: _lexical(doc, 'sentence-start'),
    'abbrev-first-use': _abbrev_first_use,
    'abbrev-in-heading': _abbrev_in_heading,
    'abbrev-foreign': _abbrev_foreign,
}


def collect_candidates(document: dict, family: str) -> list[dict]:
    builder = _BUILDERS.get(family)
    return builder(document) if builder else []


def validate_candidate(candidate: dict, document: dict) -> bool:
    block = next((x for x in document.get('blocks', []) if x.get('id') == candidate.get('blockId')), None)
    if not block:
        return False
    try:
        start = int(candidate.get('start'))
        end = int(candidate.get('end'))
    except (TypeError, ValueError):
        return False
    return 0 <= start < end <= len(block.get('text', '')) and block.get('text', '')[start:end] == candidate.get('quote')
