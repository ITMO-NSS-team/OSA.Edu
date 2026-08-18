from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable
import regex as re

from .common import mapped_excluded_ids, mapped_scientific_body_ids, contents_page_range, is_code_or_prompt, looks_like_contents, is_likely_table_context, formula_like_block
from ..util import compact

# Candidate-first checking keeps recall in code and lets the LLM make only a
# small semantic decision. A candidate is never treated as a violation by itself.

LEXICONS: dict[str, str] = {
    'filler': r'\b(?:нужно|значит|заключа(?:ется|ются|лось))\b',
    'condescending': r'\b(?:очевидно|несомненно|легко\s+видеть|хорошо\s+известно)\b',
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
_NUMERAL_RE = re.compile(r'(?<![\d.,:/–—-])(?<![\p{L}\p{N}_])([0-9])\s+([А-Яа-яЁё]{3,})')
_NUMERAL_SKIP_BEFORE = re.compile(r'(?:глав\p{L}*|раздел\p{L}*|рисун\p{L}*|рис\.|табл\p{L}*|стр\.|пункт\p{L}*|подпункт\p{L}*|направлен\p{L}*|специальност\p{L}*|формул\p{L}*|этап\p{L}*|верси\p{L}*|положен\p{L}*|свойств\p{L}*|гипотез\p{L}*|задач\p{L}*|теорем\p{L}*|определен\p{L}*|определён\p{L}*|алгоритм\p{L}*|шаг\p{L}*|пример\p{L}*|№|прилож\p{L}*)\s*$', re.I)
_NUMERAL_SKIP_AFTER = re.compile(r'^(?:мм|см|км|кг|мс|мкс|гб|мб|кб|бит|байт|раз|процент\p{L}*|%|шт)\b', re.I)
_ABBREV_RE = re.compile(r'(?<![\p{L}\p{N}_@-])([A-ZА-ЯЁ]{2,12}(?:[-–][A-ZА-ЯЁ0-9]{1,12})?(?:@[A-Za-z0-9]{1,4})?\d{0,3})(?![\p{L}\p{N}_@])')
_ABBREV_STOP = {
    'ВКР', 'ГОСТ', 'РФ', 'СССР', 'США', 'ЕС', 'ООН', 'МГУ', 'СПБГУ', 'ИТМО',
    'ГЛАВА', 'ВВЕДЕНИЕ', 'ЗАКЛЮЧЕНИЕ', 'СПИСОК', 'ЛИТЕРАТУРЫ', 'ПРИЛОЖЕНИЕ',
    'РЕФЕРАТ', 'ОГЛАВЛЕНИЕ', 'СОДЕРЖАНИЕ', 'ТАБЛИЦА', 'РИСУНОК', 'ВЫВОДЫ',
    'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII',
    # Uppercase English service headings are words, not abbreviations.
    'CONTENT', 'CONTENTS', 'ABSTRACT', 'SYNOPSIS', 'INTRODUCTION', 'CONCLUSION',
    'REFERENCES', 'PUBLICATIONS', 'AUTHOR', 'CONTRIBUTION',
    # Common all-caps Russian heading words / prepositions are typography, not acronyms.
    'НА', 'ПО', 'ОТ', 'ДО', 'ДЛЯ', 'ОБЗОР', 'АНАЛИЗ', 'МЕТОДЫ', 'РЕЗУЛЬТАТЫ', 'ЭКСПЕРИМЕНТ',
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
        # Cross-page neighbours are intentionally retained. A sentence may continue
        # on the next PDF page, and hiding the previous page made sentence-start
        # checks treat continuations such as «а источник ...» as new sentences.
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
    if index <= 0:
        return False
    return str(blocks[index - 1].get('text', '')).rstrip().endswith(('\u00ad', '-'))



def _continues_previous_sentence(document: dict, block: dict, offset: int) -> bool:
    """Return True when a candidate at block start is a continuation of the
    preceding prose block, including a page boundary.

    PDF layout extraction intentionally keeps page-local blocks. This helper adds
    only the linguistic continuity needed by sentence-start checks; it does not
    merge block ids or alter evidence offsets.
    """
    if offset > 2:
        return False
    blocks = document.get('blocks', [])
    try:
        index = next(i for i, item in enumerate(blocks) if item.get('id') == block.get('id'))
    except StopIteration:
        return False
    if index <= 0:
        return False
    previous = blocks[index - 1]
    if previous.get('type') not in {'paragraph', 'list'}:
        return False
    prev_text = compact(previous.get('text', '')).rstrip()
    if not prev_text:
        return False
    # A full stop/question/exclamation mark closes the sentence. Comma, colon,
    # semicolon, dash or no terminal punctuation means the next block can continue it.
    if re.search(r"[.!?…][»\"')\]]?$", prev_text):
        return False
    current = block.get('text', '').lstrip()
    return bool(current and re.match(r'^(?:[а-яё]|и\b|а\b|но\b|либо\b|или\b|котор\p{L}*\b|где\b|что\b)', current, re.I))


def _math_heavy_local_context(text: str, start: int, end: int) -> bool:
    """Filter uppercase tokens that are clearly embedded in a mathematical
    expression produced by the PDF text layer (e.g. DD/BND inside a broken formula).
    """
    window = text[max(0, start - 40):min(len(text), end + 40)]
    math_marks = len(re.findall(r'[=≈≤≥∑∫√±∞∈∉⊂∪∩×·←→{}\[\]𝑃𝑅ℒξσπθ]', window))
    words = re.findall(r'[А-ЯЁа-яёA-Za-z]{3,}', window)
    return math_marks >= 5 and len(words) <= 8

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
    scientific_ids = mapped_scientific_body_ids(document)
    contents_pages = contents_page_range(document)
    allowed = {'paragraph', 'list'}
    result: list[dict] = []
    for block in document.get('blocks', []):
        text = block.get('text', '')
        if scientific_ids is not None and block.get('id') not in scientific_ids:
            continue
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
            if family == 'sentence-start' and _continues_previous_sentence(document, block, match.start()):
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


def _numeral_in_math_context(text: str, start: int, end: int) -> bool:
    """Reject small-number candidates that are operands in an expression.

    CORE-3-2 is a prose rule.  Expressions such as ``∆S-IDR ≤ 0`` or
    ``x = 5`` must not become suggestions to spell the operand as a word.
    """
    left = text[max(0, start - 40):start]
    right = text[end:min(len(text), end + 28)]
    if re.search(r'\b(?:GPT|Claude(?:\s+Sonnet|\s+Opus|\s+Haiku)?|Gemini|Llama|Qwen|Mistral|Gemma)\s*$', left, re.I):
        return True
    if re.search(r'(?:<=|>=|!=|==|[=<>≤≥≈±])\s*$', left):
        return True
    if re.match(r'\s+где\b', right, re.I) and re.search(r'[A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё0-9_]{0,16}\s*$', left):
        return True
    if re.search(r'(?:[∆Δ]\s*)?[A-Za-zΑ-ωА-ЯЁа-яёρστμ][A-Za-zА-ЯЁа-яё0-9_@./-]{0,24}\s*(?:<=|>=|=|<|>|≤|≥|≈)\s*$', left):
        return True
    # A compact formula-like neighbourhood with several mathematical marks is
    # safer to exclude; ordinary prose such as «5 итераций» has none.
    window = text[max(0, start - 70):min(len(text), end + 70)]
    marks = len(re.findall(r'[=<>≤≥≈±∆Δρστμ^_{}\[\]|]', window))
    words = len(re.findall(r'[А-ЯЁа-яё]{3,}', window))
    if marks >= 3 and words <= 8:
        return True
    if re.match(r'\s*(?:или|и|при|если|то)\b', right, re.I) and re.search(r'(?:<=|>=|[=<>≤≥≈])\s*$', left):
        return True
    return False




def _numeral_is_structural_sequence(text: str, start: int, end: int) -> bool:
    """Treat enumerated identifiers as identifiers, not prose quantities.

    The one-token left-context guard catches ``Шаг 5`` but the second/third
    numbers in ``Шаги 1, 2, 3`` previously leaked through. Evaluate the
    complete local sequence up to the current number.
    """
    local = text[max(0, start - 80):end]
    return bool(re.search(
        r'\b(?:шаг\p{L}*|этап\p{L}*|пункт\p{L}*|подпункт\p{L}*|положен\p{L}*|свойств\p{L}*|гипотез\p{L}*|задач\p{L}*|теорем\p{L}*|определен\p{L}*|определён\p{L}*|алгоритм\p{L}*|пример\p{L}*)\s+'
        r'\d(?:\s*(?:[,;/]|и|или|–|-)\s*\d)*$',
        local, re.I
    ))


def _inside_embedded_table_tail(text: str, start: int) -> bool:
    """Detect PDF blocks where a table was flattened into a paragraph.

    PyMuPDF can occasionally merge a caption, column headers and table cells into
    one paragraph block. Small integers inside that flattened tail are table data,
    not prose numerals. Keep this local and conservative: require a recent table
    caption plus a data-dense tail before the candidate.
    """
    prefix = text[max(0, start - 1600):start]
    matches = list(re.finditer(r"\bТаблица\s+(?:(?:[А-ЯЁA-Z]\.)?\d+(?:\.\d+)*)\s*[—–-]", prefix, re.I))
    if not matches:
        return False
    tail = prefix[matches[-1].start():]
    numbers = len(re.findall(r"(?<!\p{L})\d+(?:[,.]\d+)?", tail))
    table_words = bool(re.search(r"\b(?:Параметр\s+Значение|Смысловая\s+группа\s+Тип|Модель\s+|Метрика\s+|Идентификатор\s+Назначение)\b", tail, re.I))
    return numbers >= 5 or table_words


def _numerals(document: dict) -> list[dict]:
    result: list[dict] = []
    for block in _narrative_blocks(document):
        text = block.get('text', '')
        if formula_like_block(text) or _looks_like_pseudocode(text):
            continue
        for match in _NUMERAL_RE.finditer(text):
            before = text[max(0, match.start() - 45):match.start()]
            after = text[match.end(1) + 1:match.end(1) + 24]
            local = _context(text, match.start(), match.end(), before=120, after=160)
            if _inside_embedded_table_tail(text, match.start()):
                continue
            scope_before = re.sub(r'([А-Яа-яЁё])(?:-|\u00ad)\s*([А-Яа-яЁё])', r'\1\2', before)
            if _NUMERAL_SKIP_BEFORE.search(scope_before) or _NUMERAL_SKIP_AFTER.match(after.strip()):
                continue
            if _numeral_is_structural_sequence(text, match.start(1), match.end(1)):
                continue
            if match.group(2).lower() == 'где' and re.search(r'[A-Za-zА-ЯЁа-яё][A-Za-zА-ЯЁа-яё0-9_]{0,16}\s*$', before):
                continue
            if _numeral_in_math_context(text, match.start(), match.end()):
                continue
            if is_likely_table_context(local) or re.search(r'[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]{0,20}\s*=\s*$', before):
                continue
            if re.search(r'^(?:Категория|Модель|Датасет|Шум|Метод|Подход)\s+.{0,80}(?:Значение|Precision|Recall|F1|Инструмент|Проверяем)', text, re.I):
                continue
            start, end = _sentence_span(text, match.start())
            result.append(_make('numerals', block, start, end, context=_block_context(document, block, match.start(), match.end()), meta={'digit': match.group(1), 'word': match.group(2)}))
    return _dedupe(result)




def _looks_like_pseudocode(value: str) -> bool:
    text = compact(value)
    if is_code_or_prompt(text):
        return True
    # Flattened algorithm listings often become one paragraph such as
    # «Algorithm 2: ... Вход: ... Выход: ... 1 Этап 1. ... 2 for ...».  In that
    # representation the leading line numbers are not prose numerals at all.
    if re.search(r'\b(?:Algorithm|Алгоритм)\s+\d+\s*:', text, re.I) and (
        (re.search(r'\b(?:Вход|Input)\s*:', text, re.I) and re.search(r'\b(?:Выход|Output)\s*:', text, re.I))
        or len(re.findall(r'(?:^|[;:.])\s*\d{1,3}\s+(?:Этап\s+\d+|for\b|while\b|if\b|return\b)', text, re.I)) >= 2
    ):
        return True
    # Common algorithm-listing artefacts from PDF extraction.  Requiring at
    # least two code markers keeps normal mathematical prose in scope.
    markers = 0
    markers += 1 if re.search(r'(?:←|:=|->)', text) else 0
    markers += 1 if '//' in text else 0
    markers += 1 if re.search(r'\b[A-Za-z][A-Za-z0-9_]*\s*\([^)]{0,120}\)', text) else 0
    markers += 1 if re.search(r'(?:^|[;])\s*\d{1,3}\s+(?:Этап\s+\d+|[A-Za-zА-ЯЁ_][\w.-]*\s*(?:←|:=))', text, re.I) else 0
    return markers >= 2

def _abbreviation_occurrences(document: dict, token: str) -> list[dict]:
    pattern = re.compile(rf'(?<![\p{{L}}\p{{N}}_@-]){re.escape(token)}(?![\p{{L}}\p{{N}}_@])')
    rows: list[dict] = []
    for block in document.get('blocks', []):
        if block.get('type') in {'table', 'code', 'formula', 'figure'} or _looks_like_pseudocode(block.get('text', '')):
            continue
        match = pattern.search(block.get('text', ''))
        if match:
            rows.append({'block': block, 'start': match.start(), 'end': match.end()})
    return rows


def _listed_abbreviation_tokens(document: dict) -> set[str]:
    listed: set[str] = set()
    for block in document.get('blocks', []):
        if block.get('type') in {'bibliography', 'table', 'code', 'formula', 'figure'}:
            continue
        for line in str(block.get('text') or '').splitlines():
            match = re.match(r'\s*([A-ZА-ЯЁ]{2,12}(?:[-–][A-ZА-ЯЁ0-9]{1,12})?\d{0,3})\s*[—–-]\s+.{3,}', line)
            if match:
                listed.add(match.group(1).upper().replace('–', '-'))
    return listed


def _looks_like_uppercase_word(token: str) -> bool:
    # Long all-caps Cyrillic headings such as «ФЕДЕРАЛЬНОЕ» or «АННОТАЦИЯ»
    # are ordinary words created by typography, not abbreviations.
    return bool(
        re.fullmatch(r'[А-ЯЁ]{6,}', token)
        and len(re.findall(r'[АЕЁИОУЫЭЮЯ]', token)) >= 2
    )


def _abbrev_candidate_class(token: str, local_context: str, listed: set[str]) -> str:
    key = token.upper().replace('–', '-')
    if key in listed:
        return 'abbreviation'
    if re.fullmatch(r'[A-Z]{2,10}-(?:19|20)?\d{2,4}', key):
        return 'conference_or_standard_designation'
    if re.fullmatch(r'(?:GPT|LLAMA|GEMINI|CLAUDE|MISTRAL|QWEN|GEMMA)[-–]?\d+(?:[.-]\d+)*', key):
        return 'model_version'
    if re.fullmatch(r'[A-Z]{1,3}\d{1,4}', key) and re.search(r'\b(?:газ|веществ|молекул|диоксид|оксид|формул)', local_context, re.I):
        return 'chemical_or_symbolic_designation'
    if len(key) <= 3 and re.search(rf'\b{re.escape(token)}\s*[—–-]\s*(?:множеств\p{{L}}*|переменн\p{{L}}*|величин\p{{L}}*|значени\p{{L}}*)', local_context, re.I):
        return 'chemical_or_symbolic_designation'
    if re.search(r'\b(?:конференци\p{L}*|симулятор\p{L}*|платформ\p{L}*|сервис\p{L}*|продукт\p{L}*)\s+(?:\S+\s+){0,3}' + re.escape(token) + r'\b', local_context, re.I):
        return 'proper_name_candidate'
    if len(token) >= 4 and re.search(r'\bсимулятор\p{L}*\b', local_context, re.I):
        return 'proper_name_candidate'
    return 'abbreviation_candidate'


def _abbrev_first_use(document: dict) -> list[dict]:
    skip = {'title', 'heading', 'toc', 'bibliography', 'table', 'code', 'formula', 'figure', 'caption'}
    excluded = mapped_excluded_ids(document)
    toc_pages = contents_page_range(document)
    seen: set[str] = set()
    result: list[dict] = []
    listed_tokens = _listed_abbreviation_tokens(document)
    for block in sorted(document.get('blocks', []), key=lambda x: int(x.get('order', 0))):
        if block.get('type') in skip or block.get('id') in excluded or block.get('page') in toc_pages or _looks_like_pseudocode(block.get('text', '')):
            continue
        text = block.get('text', '')
        if _cyrillic_ratio(text) < .20:
            continue
        for match in _ABBREV_RE.finditer(text):
            token = match.group(1)
            if _inside_embedded_table_tail(text, match.start()):
                continue
            # PDF kerning can split one token into e.g. ``V LP (q)``.
            if re.search(r'\b[A-ZА-ЯЁ]\s+$', text[max(0, match.start() - 8):match.start()]):
                continue
            # Broken PDF line wrapping may split one uppercase word into tokens
            # such as ``ПЕРЕ- СМОТР`` or ``ВОЗ- ВРАТ``. Neither half is an
            # abbreviation candidate.
            if (
                re.match(r"[-–—]\s*[A-ZА-ЯЁ]{2,}", text[match.end():match.end() + 24])
                or re.search(r"[A-ZА-ЯЁ]{2,}[-–—]\s*$", text[max(0, match.start() - 24):match.start()])
            ):
                continue
            key = token.upper().replace('–', '-')
            if _looks_like_uppercase_word(token):
                continue
            if key in seen or key in _ABBREV_STOP or _math_heavy_local_context(text, match.start(), match.end()):
                continue
            seen.add(key)
            sentence = _sentence_span(text, match.start())
            local_context = _context(text, sentence[0], sentence[1], before=260, after=220)
            candidate_class = _abbrev_candidate_class(token, local_context, listed_tokens)
            # Edition/version/formula designations are names, not abbreviations
            # that should be expanded at their first occurrence. Filter these
            # high-confidence cases before spending an LLM verdict.
            if candidate_class in {'conference_or_standard_designation', 'model_version', 'chemical_or_symbolic_designation', 'proper_name_candidate'}:
                continue
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
            result.append(_make('abbrev-first-use', block, match.start(), match.end(), context=context, meta={'token': token, 'occurrences': len(occurrences), 'candidateClass': candidate_class, 'listedInAbbreviations': key in listed_tokens}))
    return result



def _looks_like_table_header_heading(document: dict, block: dict) -> bool:
    if block.get('type') != 'heading':
        return False
    text = compact(block.get('text', ''))
    if re.match(r'^(?:таблица|рис(?:унок)?|figure|table)\b', text, re.I):
        return True
    # A section/chapter number is strong evidence of a real heading even if a
    # table happens to end immediately before it.
    if re.match(r'^(?:глава\s+)?\d+(?:\.\d+){0,4}\.?\s+', text, re.I):
        return False
    blocks = document.get('blocks', [])
    try:
        index = next(i for i, item in enumerate(blocks) if item.get('id') == block.get('id'))
    except StopIteration:
        return False
    page = block.get('page')
    for previous in reversed(blocks[max(0, index - 3):index]):
        if page is not None and previous.get('page') != page:
            break
        prev_text = compact(previous.get('text', ''))
        if previous.get('type') == 'caption' and re.match(r'^таблица\b', prev_text, re.I):
            # PDF font heuristics often mark the bold column-name row as a heading.
            return len(re.findall(r'\S+', text)) <= 12
        if previous.get('type') == 'heading' and not _looks_like_table_header_heading(document, previous):
            break
    return False

_ABBREVIATION_SECTION_START_RE = re.compile(
    r'^\s*(?:список|перечень)\s+(?:используемых\s+)?(?:сокращений|условных\s+обозначений)(?:\s+и\s+условных\s+обозначений)?\b',
    re.I,
)
_ABBREVIATION_SECTION_END_RE = re.compile(
    r'^\s*(?:термины\s+и\s+определения|введение|реферат|аннотация|abstract|introduction|глава\s+\d+|chapter\s+\d+)\b',
    re.I,
)
_ABBREVIATION_ENTRY_RE = re.compile(
    r'^\s*[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9@+./-]{0,30}\s*[—–-]\s+\S',
)


def _abbreviation_list_block_ids(document: dict) -> set[str]:
    """Return blocks belonging to an explicit abbreviation/glossary list.

    PDF typography can classify entries such as ``AI — Artificial Intelligence``
    as ``toc`` or ``heading``. CORE-4-2 is about abbreviations in titles, the
    table of contents and section headings; the dedicated abbreviation list is
    intentionally outside that scope.
    """
    result: set[str] = set()
    active = False
    for block in document.get('blocks', []):
        text = compact(block.get('text', ''))
        if not active and _ABBREVIATION_SECTION_START_RE.search(text):
            active = True
        elif active and (
            _ABBREVIATION_SECTION_END_RE.search(text)
            or (block.get('type') == 'heading' and not _ABBREVIATION_ENTRY_RE.match(text))
        ):
            # Do not let a missing/novel end heading suppress CORE-4-2 for the
            # rest of the document. A new non-entry heading closes the glossary.
            active = False
        if active and block.get('id'):
            result.add(str(block['id']))
    return result


def _abbrev_in_heading(document: dict) -> list[dict]:
    allowed = {'title', 'heading', 'toc'}
    excluded = mapped_excluded_ids(document)
    abbreviation_list_ids = _abbreviation_list_block_ids(document)
    result: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    occurrence_counts: dict[tuple[str, str], int] = {}
    for block in document.get('blocks', []):
        if block.get('type') not in allowed or block.get('id') in excluded or block.get('id') in abbreviation_list_ids:
            continue
        if _looks_like_table_header_heading(document, block):
            continue
        for match in _ABBREV_RE.finditer(block.get('text', '')):
            token = match.group(1)
            if _looks_like_uppercase_word(token) or token.upper() in _ABBREV_STOP:
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
