"""Layout observations for ITMO detectors, using the one canonical PDF model.

These helpers do not assign normative verdicts or read files. Every diagnostic
is a specific subcheck attached to its own source row, not to a whole paragraph.
"""
from collections import Counter
from statistics import median
import regex as re

MM = 72 / 25.4


def issue(row, check, reason, **values):
    return {**row, 'check': check, 'reason': reason, **values}


def font_family(font):
    name = re.sub(r'^[A-Z]{6}\+', '', font or '').casefold()
    # Compare family, ignoring bold/italic and PDF PostScript encoding suffixes.
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r'(?:bolditalic|boldmt|italicmt|bold|italic|regular|oblique|regu|medi|ital|psmt|ps|mt)$', '', name).rstrip('-_ ')
    return re.sub(r'[\s_,-]', '', name)


def pagination(document, inv, centered):
    if 'pagination' in inv:
        return inv['pagination']
    body_fonts = Counter(font_family(b.get('fontName')) for b in inv['body'] if b.get('fontName'))
    body_font = body_fonts.most_common(1)[0][0] if body_fonts else None
    positions = []; bad = []; missing = []; ambiguous = []
    for page in document.get('pages', []):
        if page['number'] < (inv['start'] or 1):
            continue  # ISU front matter is not assumed to follow the main text offset.
        rows = [*(page.get('layoutArtifacts') or []),
                *[l for l in inv['lines'] if l['page'] == page['number']]]
        candidates = [dict(r, page=page['number']) for r in rows if r.get('bbox')
                      and r['bbox'][1] > page['height']*.86
                      and re.fullmatch(r'\d+[.]?', r.get('text', '').strip())]
        candidates = list({tuple(r['bbox']): r for r in candidates}.values())
        artifacts = [r for r in candidates if r.get('layoutRole') == 'page_number']
        if artifacts:
            candidates = artifacts
        if not candidates:
            missing.append(page['number']); continue
        if len(candidates) != 1:
            ambiguous.append(page['number']); continue
        row = candidates[0]; row['printedPage'] = int(row['text'].rstrip('.'))
        positions.append(row)
        if not centered(row['bbox'], page['width']):
            bad.append(issue(row, 'page_center', 'Номер страницы расположен вне центра нижней строки.'))
        if row['text'].endswith('.'):
            bad.append(issue(row, 'page_period', 'После номера страницы стоит точка.'))
        if body_font and row.get('fontName') and font_family(row['fontName']) != body_font:
            bad.append(issue(row, 'page_font', f"Гарнитура номера {row['fontName']}, основного текста — {body_font}."))
    for prev, cur in zip(positions, positions[1:]):
        # Physical gaps are allowed in the observation but still count in the
        # continuous printed sequence. No arbitrary requirement to start at 1.
        if cur['printedPage']-prev['printedPage'] != cur['page']-prev['page']:
            bad.extend([issue(prev, 'page_sequence', 'Начало разрыва сквозной нумерации.'),
                        issue(cur, 'page_sequence', 'Печатный номер не продолжает последовательность с учётом физических страниц.')])
    complete = bool(positions and not missing and not ambiguous and body_font
                    and all(r.get('fontName') for r in positions))
    value = dict(rows=positions, bad=bad, missing=missing, ambiguous=ambiguous,
                 complete=complete, printed={r['page']: r['printedPage'] for r in positions})
    inv['pagination'] = value
    return value


def colored_spans(document, inv):
    bad = []; ambiguous = []; observed = 0
    body_ids = {b['id'] for b in inv['body']}
    caption_ids = {bid for c in inv['captions'] for bid in c.get('blockIds', [c['id']])}
    for block in document.get('blocks', []):
        if block.get('page', 0) < inv['start']:
            continue
        prose = block['id'] in body_ids or (block.get('type') == 'paragraph' and len(re.findall(r'\p{L}{2,}', block.get('text', ''))) >= 20)
        for line in block.get('lines') or []:
            for span in line.get('spans') or []:
                color = span.get('color'); text = span.get('text', '').strip()
                if color is None or not text:
                    continue
                observed += 1
                if color == 0:
                    continue
                math_font = bool(re.search(r'math|symbol|cmmi|cmsy', span.get('fontName') or '', re.I))
                if text.isdigit() and block.get('fontSize') and span.get('fontSize') and span['fontSize'] < block['fontSize']*.8:
                    math_font = True
                start = span.get('logicalStart', line.get('text', '').find(text))
                citation = any(m.start() <= start < m.end() for m in re.finditer(r'\[[^\]]+\]', line.get('text', '')))
                bib = inv['bibliography'] and block.get('page', 0) >= inv['bibliography']['page']
                kind = ('formula' if math_font or block.get('type') == 'formula' else
                        'link' if re.search(r'https?://|www\.', text) else
                        'bibliography' if bib else 'caption' if block['id'] in caption_ids else
                        'citation' if citation and prose else
                        'body' if prose else
                        'heading' if block.get('type') == 'heading' else
                        'contents' if block.get('type') == 'toc' else
                        'table' if block.get('type') == 'table' else 'unknown')
                rgb = [(int(color) >> shift) & 255 for shift in (16, 8, 0)]
                row = issue({**span, 'text': text, 'page': block['page'], '_parent': block},
                            'text_color', f"Цвет фрагмента #{int(color):06X}, RGB {tuple(rgb)}; элемент: {kind}.",
                            rgb=rgb, hex=f'#{int(color):06X}', elementKind=kind)
                line_text = line.get('text', '')
                source_start = block.get('text', '').find(line_text)
                if source_start >= 0 and start >= 0 and block['text'][source_start+start:source_start+start+len(text)] == text:
                    row['_sourceStart'] = source_start+start
                # A single colored digit in a formula/diagram is not evidence
                # that the surrounding prose uses colored text. No URL exception.
                (ambiguous if kind in {'unknown', 'formula', 'table'} else bad).append(row)
    return bad, ambiguous, observed


def object_references(inv, kind):
    word = r'(?:рисун(?:ок|ке|ка|ки|ков|ку|ках)|figure(?:s)?)' if kind == 'figure' else r'(?:таблиц(?:а|е|ы|у|ах)|tables?)'
    refs = {}
    for line in inv['narrative']:
        # Captions and TOC are excluded before this helper is called. Lists and
        # grouped references are expanded, retaining the actual prose evidence.
        for match in re.finditer(r'\b'+word+r'\s+(\d+(?:\.\d+)*(?:(?:\s*[,;–—-]\s*|\s+(?:and|и)\s+)\d+(?:\.\d+)*)*)', line['text'], re.I):
            value = match[1]
            numbers = []
            for part in re.split(r'\s*[,;]\s*|\s+(?:and|и)\s+', value, flags=re.I):
                bounds = re.split(r'\s*[–—-]\s*', part)
                if len(bounds) == 2:
                    a, b = bounds
                    aa, bb = a.split('.'), b.split('.')
                    if aa[:-1] == bb[:-1] and int(aa[-1]) <= int(bb[-1]) <= int(aa[-1])+100:
                        numbers.extend('.'.join([*aa[:-1], str(n)]) for n in range(int(aa[-1]), int(bb[-1])+1))
                else:
                    numbers.append(part)
            for number in numbers:
                refs.setdefault(number, []).append(line)
    return refs


def caption_continuations(blocks, index, kind, centered):
    """Recover caption lines split into several canonical blocks by PDF maths.

    Requires agreeing page, font size, proximity and alignment. The canonical
    blocks are never changed. Each line keeps its own original parent.
    """
    start = blocks[index]
    segments = [{**l, '_parent': start, 'page': start['page']} for l in start.get('lines') or [start]]
    consumed = [start['id']]
    size = start.get('fontSize')
    if not size or not segments[-1].get('bbox'):
        return segments, consumed
    for following in blocks[index+1:]:
        if following.get('page') != start['page'] or following.get('type') in {'heading', 'caption', 'table', 'toc', 'bibliography', 'code'}:
            break
        candidates = following.get('lines') or [following]
        if any(not l.get('bbox') or not l.get('fontSize') or abs(l['fontSize']-size) > .3 for l in candidates):
            break
        first = candidates[0]
        gap = first['bbox'][1]-segments[-1]['bbox'][3]
        width = start.get('pageWidth') or following.get('pageWidth')
        aligned = (all(centered(l['bbox'], width) for l in candidates) if kind == 'figure' and width else
                   all(abs(l['bbox'][0]-segments[0]['bbox'][0]) < 4 for l in candidates))
        if not (-2 <= gap <= size*.8 and aligned):
            break
        segments.extend({**l, '_parent': following, 'page': start['page']} for l in candidates)
        consumed.append(following['id'])
    return segments, consumed


def object_box(document, cap):
    """Only a unique, nearby, horizontally overlapping PDF object is assigned."""
    page = next((p for p in document.get('pages', []) if p['number'] == cap['page']), {})
    objects = (page.get('layoutObjects') or {}).get('tables' if cap['kind'] == 'table' else 'images', [])
    box = cap.get('bbox')
    if not box:
        return None
    candidates = []
    for obj in objects:
        overlap = max(0, min(obj[2], box[2])-max(obj[0], box[0]))
        if overlap < min(obj[2]-obj[0], box[2]-box[0])*.4:
            continue
        gap = min(abs(obj[3]-box[1]), abs(obj[1]-box[3]))
        if gap <= 60:
            candidates.append((gap, obj))
    candidates.sort()
    if not candidates or (len(candidates) > 1 and candidates[1][0]-candidates[0][0] < 10):
        return None
    return candidates[0][1]


def caption_number_issues(caps, document):
    bad = []; numbers = [c['number'] for c in caps]
    for number, count in Counter(numbers).items():
        if count > 1:
            bad.extend(issue(c, 'duplicate_number', f'Номер {number} повторяется у разных объектов.') for c in caps if c['number'] == number)
    schemas = {len(n.split('.')) for n in numbers}
    if len(schemas) > 1:
        bad.extend(issue(c, 'numbering_scheme', 'В одном виде объектов смешаны сквозная и внутриглавная нумерация.') for c in caps)
    if any(len(n.split('.')) > 2 or any(int(p) == 0 for p in n.split('.')) for n in numbers):
        bad.extend(issue(c, 'number_format', 'Номер должен быть положительным арабским или иметь вид глава.номер.')
                   for c in caps if len(c['number'].split('.')) > 2 or any(int(p) == 0 for p in c['number'].split('.')))
    # A gap alone can be an undetected object. Do not claim a violation of the
    # whole sequence on that basis. Confirmed chapter metadata proves a mismatch.
    index = {b['id']: i for i, b in enumerate(document.get('blocks', []))}
    chapters = [(index.get(e.get('startBlockId')), e) for e in (document.get('map') or {}).get('elements', [])
                if e.get('type') == 'chapter' and e.get('state') == 'confirmed']
    by_id = {b['id']: b for b in document.get('blocks', [])}
    for cap in caps:
        if '.' not in cap['number']:
            continue
        preceding = [(i, e) for i, e in chapters if i is not None and i <= index.get(cap['id'], -1)]
        if not preceding:
            continue
        el = max(preceding, key=lambda x: x[0])[1]
        match = re.match(r'^(?:(?:глава|chapter)\s+)?(\d+)(?:\s|\.)', by_id.get(el['startBlockId'], {}).get('text', ''), re.I)
        if match and match[1] != cap['number'].split('.')[0]:
            bad.append(issue(cap, 'chapter_number', f"Номер {cap['number']} не соответствует подтверждённой главе {match[1]}."))
    return bad


def caption_format_issues(caps, document, centered):
    bad = []; observations = []
    for cap in caps:
        before = len(bad)
        rest = cap['rest'].strip(); title = re.sub(r'^[–—]\s*', '', rest)
        if not re.match(r'^[–—]\s+\S', rest):
            bad.append(issue(cap, 'caption_separator', 'Между номером и названием требуется тире с пробелами.'))
        if re.match(r'^\p{Ll}', title):
            bad.append(issue(cap, 'caption_title_case', 'Название начинается со строчной буквы.'))
        if cap['text'].rstrip().endswith('.'):
            bad.append(issue(cap, 'caption_period', 'В конце полного названия стоит точка.'))
        page = next((p for p in document.get('pages', []) if p['number'] == cap['page']), {})
        lines = cap.get('lines') or [cap]
        if cap['kind'] == 'figure' and page:
            for line in lines:
                if line.get('bbox') and not centered(line['bbox'], page['width']):
                    bad.append(issue({**line, 'page': cap['page'], '_parent': line.get('_parent') or cap['_parent']}, 'caption_center', 'Строка подписи рисунка не центрирована.'))
        if cap['kind'] == 'table':
            # Use an assigned table's left edge if known, otherwise text margin.
            obj = object_box(document, cap)
            left = obj[0] if obj else 30*MM
            for line in lines:
                if line.get('bbox') and abs(line['bbox'][0]-left) > 8:
                    bad.append(issue({**line, 'page': cap['page'], '_parent': line.get('_parent') or cap['_parent']}, 'table_title_left',
                                     'Название таблицы имеет отступ от левого края таблицы/текста.',
                                     measurement={'observedLeftPt': line['bbox'][0], 'expectedLeftPt': left}))
        obj = object_box(document, cap)
        if obj and cap.get('bbox'):
            if cap['kind'] == 'figure' and cap['bbox'][3] < obj[1]:
                bad.append(issue(cap, 'caption_below', 'Подпись находится над однозначно сопоставленной иллюстрацией.', measurement={'objectBbox': obj}))
            elif cap['kind'] == 'table' and cap['bbox'][1] > obj[3]:
                bad.append(issue(cap, 'table_title_above', 'Название находится под однозначно сопоставленной таблицей.', measurement={'objectBbox': obj}))
        failed = {r['check'] for r in bad[before:]}
        alignment = 'caption_center' if cap['kind'] == 'figure' else 'table_title_left'
        position = 'caption_below' if cap['kind'] == 'figure' else 'table_title_above'
        observations.append({'number': cap['number'], 'page': cap['page'], 'objectBbox': obj,
                             'positionChecked': bool(obj), 'lineCount': len(lines),
                             'checks': {name: 'violation' if name in failed else 'pass'
                                        for name in ['caption_separator', 'caption_title_case', 'caption_period']},
                             'alignment': 'violation' if alignment in failed else 'pass' if all(l.get('bbox') for l in lines) else 'uncertain',
                             'position': 'violation' if position in failed else 'pass' if obj else 'uncertain',
                             'singleSpacing': 'uncertain' if len(lines) > 1 else 'not_applicable'})
        if 'caption_title_case' not in failed and not re.match(r'^[\p{Lu}\p{N}]', title):
            observations[-1]['checks']['caption_title_case'] = 'uncertain'
    return bad, observations


def toc_rows(document, inv):
    if 'tocRows' in inv:
        return inv['tocRows']
    start = inv['start']; intro = inv['introduction']; rows = []; pending = []
    ends = [h['page'] for h in inv['headings'] if start and h['page'] > start and h['section'] != 'contents']
    end = min(ends) if ends else (intro or {}).get('page')
    inv['tocEnd'] = end
    for line in inv['lines']:
        if not start or not end or not start <= line['page'] < end:
            continue
        if line['_parent']['id'] == inv['contents']['_parent']['id']:
            continue
        text = line['text']
        # Rightmost integer is a page, only in the established TOC region.
        match = re.match(r'^(.*?)\s*[.․…·\s]{2,}(\d+)\s*$', text)
        if not match:
            # No leaders can also be a validly extracted entry with a distant
            # final page span; its missing leaders are evaluated separately.
            match = re.match(r'^(.+\p{L}.*?)\s+(\d+)\s*$', text)
        if match:
            title = ' '.join([*[p['text'] for p in pending], match[1].strip(' .․…·')])
            rows.append({**line, 'tocTitle': title, 'tocPage': int(match[2]), 'hasLeaders': bool(re.search(r'[.․…·]{2,}', text)),
                         'wrapped': pending[:]})
            pending = []
        elif text and not re.fullmatch(r'\d+', text):
            pending.append(line)
    inv['tocRows'] = rows
    return rows


def toc_issues(document, inv, centered):
    entries = toc_rows(document, inv); bad = []; unknown = []
    pg = pagination(document, inv, centered)['printed']
    headings = [b for b in document.get('blocks', []) if b.get('type') == 'heading'
                and inv.get('tocEnd') and b.get('page', 0) >= inv['tocEnd']]
    clean = lambda x: re.sub(r'\s+', ' ', x.replace('\u00ad', '')).strip()
    for entry in entries:
        title = clean(entry['tocTitle'])
        matches = [h for h in headings if clean(h['text']) == title]
        if len(matches) != 1:
            # Positive evidence: same numbered heading, genuinely different title.
            number = re.match(r'^(\d+(?:\.\d+)*)\.?\s+', title)
            candidates = [h for h in headings if number and re.match(r'^'+re.escape(number[1])+r'\.?\s+', clean(h['text']))]
            if len(candidates) == 1:
                bad.extend([issue(entry, 'toc_title', 'Формулировка в содержании отличается от заголовка в тексте.'),
                            issue(candidates[0], 'toc_title', 'Фактический заголовок с тем же номером.')])
            else:
                unknown.append(entry)
            continue
        actual = matches[0]
        printed = pg.get(actual['page'])
        if printed is not None and printed != entry['tocPage']:
            bad.extend([issue(entry, 'toc_page', f"В содержании указано {entry['tocPage']}, напечатано {printed} (PDF {actual['page']})."),
                        issue(actual, 'toc_page', 'Фактическое начало элемента.', printedPage=printed)])
        elif printed is None:
            unknown.append(entry)
        if not entry['hasLeaders']:
            # Dots may be vector leaders rather than text. Lack of extraction
            # does not prove a visual defect.
            unknown.append(entry)
    return bad, unknown, entries
