from __future__ import annotations
import regex as re
from .common import grounded_check, evidence, result, dedupe_evidence, contextual


_SPACED_DASH = re.compile(r'\s[–—]\s')

_ENTRY_START = re.compile(r'^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)])\s+(?![—–-])(?=\S)')


def bibliography_entries(blocks: list[dict]) -> tuple[list[dict], bool]:
    """Join wrapped records, retaining every source block and original number.

    Numbering locates records; it is never evidence of bibliographic style.
    Unassigned text means the parser cannot claim exhaustive coverage.
    """
    from ..literature.extractor import REF_HEADING_RE
    entries = []
    complete = True
    for block in _without_appendix_tail(blocks):
        for line in str(block.get('text') or '').splitlines():
            if not line.strip() or REF_HEADING_RE.match(line):
                continue
            match = _ENTRY_START.match(line)
            if match:
                entries.append({'number': int(match.group(1) or match.group(2)),
                                'text': line[match.end():].strip(), 'blocks': [block]})
            elif entries:
                entries[-1]['text'] += ' ' + line.strip()
                if block not in entries[-1]['blocks']:
                    entries[-1]['blocks'].append(block)
            else:
                complete = False
    numbers = [e['number'] for e in entries]
    return entries, bool(entries and complete and len(numbers) == len(set(numbers)))


def _reference_profile(text: str) -> tuple[str, dict]:
    """Observe explicit field syntax; do not infer missing metadata or a style guide."""
    from ..literature.metadata import classify_source_type
    kind = classify_source_type(text)
    if kind == 'PAPER':
        # Journal and conference records need not share optional fields.
        if re.search(r'\b(?:proceedings|conference|workshop|symposium)\b', text, re.I):
            kind = 'CONFERENCE_PAPER'
        elif re.search(r'\b(?:journal|transactions)\b|(?:\bvol\.|\bт\.)\s*\d', text, re.I):
            kind = 'JOURNAL_ARTICLE'
        else:
            kind = 'UNKNOWN'
    # A URL alone does not prove a web source (papers also have URLs).
    if kind in {'UNKNOWN', 'OTHER'}:
        return kind, {}
    fields = {}
    initials = r'(?:[A-ZА-ЯЁ]\.\s*){1,3}'
    surname = r'[A-ZА-ЯЁ][\p{L}’\'-]+'
    if re.match(rf'^{initials}{surname}', text):
        fields['author_order'] = 'initials_surname'
    elif re.match(rf'^{surname},?\s+{initials}', text):
        fields['author_order'] = 'surname_initials'
        fields['author_separator'] = 'comma' if re.match(rf'^{surname},', text) else 'space'
    patterns = {
        'year': r'\b(?:19|20)\d{2}\b',
        'volume': r'(?i)(?:\bvol\.|\bт\.)\s*\d+',
        'issue': r'(?i)(?:\bno\.|№)\s*\d+',
        'pages': r'(?i)(?:\bpp?\.|\bс\.)\s*\d+(?:\s*[-–—]\s*\d+)?',
        'url': r'https?://\S+',
        'doi': r'(?i)\bdoi\s*:\s*\S+',
        'electronic': r'(?i)\[(?:электронный\s+ресурс|electronic\s+resource)\]',
    }
    positions = {}
    for name, pattern in patterns.items():
        matches = list(re.finditer(pattern, text))
        # Multiple years may include an access date. Do not pick one silently.
        if len(matches) != 1:
            continue
        match = matches[0]
        positions[name] = match.start()
        prefix = text[:match.start()].rstrip()
        sep = re.search(r'[.,;:()\[\]–—-]+(?:\s*[.,;:()\[\]–—-]+)*$', prefix)
        if sep:
            fields[name + '_separator'] = re.sub(r'\s+', '', sep.group())
        if name == 'url':
            fields['url_label'] = bool(re.search(r'(?i)URL\s*:\s*$', prefix))
        if name == 'pages':
            marker = re.match(r'\D+', match.group()).group().strip().casefold()
            # Russian с. is valid for both single pages and ranges.
            if marker in {'p.', 'pp.'}:
                fields['pages_marker'] = marker
    # Compare relative order only for fields actually observed in both entries.
    fields['field_positions'] = positions
    if '//' in text:
        fields['venue_separator'] = '//'
    return kind, fields


def _bibliography_consistency(rule: dict, blocks: list[dict]) -> dict:
    from itertools import combinations
    entries, complete = bibliography_entries(blocks)
    groups = {}
    for entry in entries:
        kind, profile = _reference_profile(entry['text'])
        if profile:
            groups.setdefault(kind, []).append((entry, profile))
    differences = []
    ev = []
    for kind, rows in groups.items():
        for (left, a), (right, b) in combinations(rows, 2):
            changed = [key for key in a.keys() & b.keys() - {'field_positions'} if a[key] != b[key]]
            pa, pb = a['field_positions'], b['field_positions']
            for x, y in combinations(sorted(pa.keys() & pb.keys()), 2):
                if (pa[x] < pa[y]) != (pb[x] < pb[y]):
                    changed.append(f'order({x},{y})')
            if changed:
                differences.append({'type': kind, 'entries': [left['number'], right['number']], 'fields': changed})
                for entry in (left, right):
                    ev.extend(evidence(block, block.get('text', '')[:500]) for block in entry['blocks'])
            if len(differences) >= 15:
                break
    if differences:
        out = result(rule, 'violation', 'В записях одного типа обнаружены различия структуры библиографического оформления: '
                     + '; '.join(f"{d['type']} №{d['entries']}: {', '.join(d['fields'])}" for d in differences),
                     dedupe_evidence(ev)[:20], .95, 'detector', 'Согласовать структуру оформления записей одного типа.')
    else:
        out = result(rule, 'uncertain', 'Структурные различия в распознанных полях не подтверждены. '
                     'Полный разбор типов источников и всех полей оформления не гарантирован; нумерация не доказывает единообразие.')
    out['bibliographyComparison'] = {'entryCount': len(entries), 'segmentationComplete': complete,
                                     'comparedTypes': list(groups), 'differences': differences}
    return out


_APPENDIX_BOUNDARY = re.compile(
    r'^\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:ПРИЛОЖЕНИЕ|APPENDIX)\b',
    re.I,
)


def _without_appendix_tail(blocks: list[dict]) -> list[dict]:
    """Clip accidental appendix spillover from a bibliography range.

    Structure extraction can occasionally end the bibliography on the first
    appendix heading.  A top-level appendix heading is never a bibliography
    entry, even when it starts with a number such as ``5. ПРИЛОЖЕНИЕ А``.
    Treat the first heading-like appendix marker as a hard boundary so all
    bibliography rules share the same safe scope.
    """
    out: list[dict] = []
    for block in blocks:
        text = re.sub(r'\s+', ' ', str(block.get('text') or '')).strip()
        is_boundary = bool(_APPENDIX_BOUNDARY.match(text)) and (
            str(block.get('type') or '').lower() == 'heading'
            or (text == text.upper() and bool(re.search(r'\p{L}', text)))
        )
        if is_boundary:
            break
        out.append(block)
    return out


def _bibliography_dash_separators(text: str):
    """Yield spaced dashes that behave like bibliography field separators.

    A plain ``\\s—\\s`` search is too broad: a dash can legitimately occur inside
    an article title (for example between a short model name and its subtitle).
    Bibliographic separators are much more constrained: they usually follow a
    completed field (full stop/bracket) or introduce a structured publication
    field such as year, volume, pages, URL or DOI.
    """
    for match in _SPACED_DASH.finditer(text):
        left=text[:match.start()].rstrip()
        right=text[match.end():].lstrip()
        follows_completed_field=bool(left and left[-1] in '.;])')
        starts_structured_field=bool(re.match(
            r'(?:19|20)\d{2}\b|(?:vol\.|том\b|т\.\s*\d|no\.|№|p{1,2}\.|с\.\s*\d|'
            r'URL\s*:|DOI\s*:|дата\s+обращения\b|издательство\b|[Мм]\.\s*:|СПб\.?\s*:)',
            right,
            re.I,
        ))
        if follows_completed_field or starts_structured_field:
            yield match


@grounded_check
def run_bibliography_rule(rule:dict,document:dict)->dict:
    blocks=_without_appendix_tail(document.get('fields',{}).get('bibliographyBlocks',[]))
    if not blocks: return result(rule,'uncertain','Список литературы не удалось надёжно распознать.')
    rid=rule['id']
    if rid=='CORE-9-1':
        return _bibliography_consistency(rule, blocks)
    if rid=='CORE-18':
        ev=[]
        # CORE-18 is about author-name order only.  Do not mix it with access-date
        # requirements.  Flag high-confidence forms such as ``M. Chen`` or
        # ``M. H. Lees``; full names without initials are not evidence here.
        initials_before_surname=re.compile(
            r'(?<![\p{L}.])(?:[А-ЯЁA-Z]\.\s*){1,3}[А-ЯЁA-Z][\p{L}`’\'’-]{1,}(?=\s*(?:[,;/]|\[|$))'
        )
        for b in blocks:
            text=b.get('text','')
            for m in initials_before_surname.finditer(text):
                ev.append(evidence(b,contextual(text,m.start(),len(m.group(0)))))
        if ev:
            return result(rule,'violation','Обнаружены авторы, у которых инициалы стоят перед фамилией.',dedupe_evidence(ev)[:15],1,'detector','Оформить имена авторов единообразно: «Фамилия И. О.».')
        return result(rule,'pass','Высокоуверенных случаев расположения инициалов перед фамилией в распознанных библиографических записях не обнаружено.',confidence=1)
    # deterministic bibliographic formatting patterns
    ev=[]
    patterns=[]
    if rid=='CORE-9-2': patterns=[r'\bISBN\b',r'\bed\.\s+by\b',r'\s&\s',r'\b[A-ZА-ЯЁ][\p{L}-]+,\s+[A-ZА-ЯЁ]\.']
    elif rid=='CORE-9-3': patterns=[r'\bp\.\s*\d+(?:\s*[-–—]\s*\d+)?\b']
    for b in blocks:
        if rid=='CORE-9-2':
            text=b.get('text','')
            for m in _bibliography_dash_separators(text):
                ev.append(evidence(b,contextual(text,m.start(),len(m.group(0)))))
        for p in patterns:
            for m in re.finditer(p,b.get('text',''),re.I): ev.append(evidence(b,contextual(b['text'],m.start(),len(m.group(0)))))
    if ev: return result(rule,'violation',rule.get('requirement','Обнаружено нарушение оформления библиографии.'),dedupe_evidence(ev)[:15],1,'detector','Унифицировать оформление библиографической записи.')
    return result(rule,'pass','Явных нарушений данного библиографического правила не обнаружено.',confidence=1)
