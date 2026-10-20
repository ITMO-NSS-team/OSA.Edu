"""Conservative ITMO layout detectors over the canonical extracted PDF.

No I/O, PDF reparsing, LLM calls or student-specific vocabulary. The single
layout inventory is cached in the existing Fact Store for the duration of a run.
A positive sample can prove a violation; partial detection never proves PASS.
"""
from __future__ import annotations

from collections import Counter
from statistics import median
import regex as re

from .common import evidence, result
from .bibliography import bibliography_entries, numeric_citations
from .itmo_layout import (issue, pagination, colored_spans, object_references,
                          caption_number_issues, caption_format_issues,
                          object_box, toc_issues, toc_rows, caption_continuations)

MM = 72 / 25.4
NAMED = {
    'contents': r'(?:содержание|оглавление|table of contents|contents)',
    'introduction': r'(?:введение|introduction)',
    'conclusion': r'(?:заключение|conclusions?)',
    'bibliography': r'(?:список (?:использованных )?(?:источников|литературы)|references|bibliography)',
    'abbreviations': r'(?:(?:список|перечень) (?:сокращений(?: и условных обозначений)?|условных обозначений)|(?:list of )?abbreviations(?: and (?:symbols|acronyms))?)',
    'terms': r'(?:термины и определения|словарь терминов|terms and definitions|glossary)',
    'appendices': r'(?:приложени[ея]|appendi[xc](?:es)?)(?:\s+[A-ZА-ЯЁ0-9]+)?',
    'abstract': r'(?:аннотация|реферат|abstract|synopsis)',
    'assignment': r'(?:задание(?: на (?:вкр|выпускную квалификационную работу))?|(?:thesis )?assignment)',
}
SECTION = re.compile(r'^(?:(?:глава|chapter)\s+)?(?P<num>\d+(?:\.\d+)*)(?P<dot>\.)?\s+(?P<title>\p{L}.+)$', re.I)
CAPTION = re.compile(r'^(?P<word>рисунок|рис\.?|figure|fig\.?|таблица|table)\s+(?P<num>\d+(?:\.\d+)*)(?P<rest>.*)$', re.I)
CITATION = re.compile(r'\[\s*(\d+(?:\s*(?:[,;–—-]\s*\d+))*)\s*\]')


def _norm(text):
    return ' '.join(str(text or '').replace('\u00ad', '').split())


def _centered(box, width):
    # Word/TeX center within the text area. With asymmetric ITMO margins this
    # differs from the physical sheet center. Both conventional placements are
    # accepted; 4.2 explicitly permits two right-margin values.
    center = (box[0] + box[2]) / 2
    return min(abs(center-x) for x in (width/2, (30*MM+width-15*MM)/2,
                                      (30*MM+width-10*MM)/2)) <= 8


def _named(text):
    value = _norm(text)
    for name, pattern in NAMED.items():
        if re.fullmatch(r'(?:\d+(?:\.\d+)*\.?\s+)?'+pattern+r'\.?', value, re.I):
            return name
    return None


def _ev(row, detail=None):
    if row.get('_segments'):
        segment = row['_segments'][-1 if row.get('check') == 'caption_period' else 0]
        row = {**row, **segment, 'reason': row.get('reason'), 'check': row.get('check')}
    parent = row.get('_parent') or row
    if parent.get('id'):
        out = evidence(parent, _norm(row.get('text'))[:500])
    else:
        out = {'page': row.get('page'), 'quote': _norm(row.get('text'))[:500],
               'location': f"Страница {row.get('page')}", 'verified': True, 'sourceKind': 'pdf_layout'}
    if row.get('bbox'):
        out['bbox'] = row['bbox']
    if row.get('elementKind'):
        # A repeated colored digit must not inherit the offset of the first
        # equal digit in its paragraph. Its PDF box is always the primary anchor.
        out.pop('start', None); out.pop('end', None)
        if row.get('_sourceStart') is not None:
            out.update(start=row['_sourceStart'], end=row['_sourceStart']+len(row['text']))
    for key in ('reason', 'check', 'elementKind', 'rgb', 'hex', 'printedPage', 'measurement', 'sourceKind', 'bboxes'):
        if key in row:
            out[key] = row[key]
    if detail:
        out['measurement'] = detail
    return out


def _inventory(document):
    store = document.setdefault('factStore', {})
    if 'itmoMaster' in store:
        return store['itmoMaster']
    blocks = document.get('blocks') or []
    lines = []
    for block in blocks:
        for line in block.get('lines') or [block]:
            # DOCX has no physical pages. Zero is an internal ordering sentinel;
            # evidence still comes from the original block, without a page.
            lines.append({**line, 'text': _norm(line.get('text')), 'page': block.get('page') or 0, '_parent': block})
    headings = []
    # Named headings may wrap across several physical lines.
    heading_rows = [{**b, 'page': b.get('page') or 0, '_parent': b} for b in blocks if _named(b.get('text'))]
    for line in [*heading_rows, *lines]:
        name = _named(line['text'])
        if name and (line['_parent'].get('type') == 'heading' or line['text'] == line['text'].upper()) and not re.search(r'[.․…]{2,}\s*\d+\s*$', line['text']):
            headings.append({**line, 'section': name})
    headings = list({(h['_parent'].get('id'), h['section']): h for h in reversed(headings)}.values())
    headings.sort(key=lambda h: (h['page'], h.get('bbox', [0, 0])[1]))
    # Keep only actual heading occurrences; TOC entries have a final page number
    # and cannot match the exact anchored named-heading grammar above.
    toc = next((x for x in headings if x['section'] == 'contents'), None)
    start = toc['page'] if toc else None
    intro = next((x for x in headings if x['section'] == 'introduction' and (not start or x['page'] >= start)), None)
    bib = next((x for x in headings if x['section'] == 'bibliography' and (not intro or x['page'] > intro['page'])), None)
    body = []
    for block in blocks:
        if start and block.get('page', 0) < start:
            continue
        if bib and block.get('page', 0) >= bib['page']:
            continue
        if intro and block.get('page', 0) < intro['page']:
            continue
        if block.get('type') != 'paragraph' or len(block.get('text', '')) < 180:
            continue
        blines = block.get('lines') or []
        # Exclude formulae, tabular/vector payloads, code and short captions.
        prose = [l for l in blines if len(re.findall(r'\p{L}{2,}', l.get('text', ''))) >= 5
                 and not CAPTION.match(_norm(l.get('text')))]
        if len(prose) >= 3 and len(prose) >= len(blines) * .8:
            body.append({**block, '_prose': prose})
    captions = []
    first_body_page = intro['page'] if intro else start
    last_body_page = bib['page'] if bib else None
    consumed = set()
    for block_index, block in enumerate(blocks):
        line = {**block, 'text': _norm(block.get('text')), '_parent': block}
        if block['id'] in consumed:
            continue
        if first_body_page and line.get('page', 0) < first_body_page:
            continue
        if last_body_page and line.get('page', 0) >= last_body_page:
            continue
        match = CAPTION.match(line['text'])
        if not match:
            continue
        # Narrative "Figure 1 shows ..." is not a caption. Accept caption
        # classification, or a distinct dash/colon after a labelled number.
        if line['_parent'].get('type') != 'caption' and not re.match(r'^\s*(?:[–—:-]|\.(?!\d))', match['rest']):
            continue
        kind = 'table' if match['word'].lower() in {'table','таблица'} else 'figure'
        segments, ids = caption_continuations(blocks, block_index, kind, _centered)
        consumed.update(ids)
        caption = {**line, 'kind': kind, 'number': match['num'], 'rest': match['rest'], 'word': match['word'],
                   '_segments': segments, 'blockIds': ids, 'lines': segments}
        if len(ids) > 1:
            caption['text'] = _norm(' '.join(s['text'] for s in segments))
            caption['rest'] = CAPTION.match(caption['text'])['rest']
            boxes = [s['bbox'] for s in segments if s.get('bbox')]
            caption['bbox'] = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        captions.append(caption)
    caption_ids = {bid for c in captions for bid in c['blockIds']}
    narrative = [l for l in lines if (not first_body_page or l.get('page', 0) >= first_body_page)
                 and (not bib or l.get('page', 0) < bib['page'])
                 and l['_parent'].get('type') not in {'toc', 'caption', 'code', 'bibliography', 'formula', 'table'}
                 and l['_parent'].get('id') not in caption_ids]
    # Join wrapped prose before recognizing references such as "Figure\n2".
    narrative = list({l['_parent']['id']: {**l['_parent'], 'text': _norm(l['_parent']['text']), '_parent': l['_parent']}
                      for l in narrative}.values())
    records = []; entries_complete = False
    if bib:
        appendix = next((h for h in headings if h['section'] == 'appendices' and h['page'] >= bib['page']), None)
        bib_lines = []
        for line in lines:
            if line.get('page', 0) < bib['page'] or (appendix and line['page'] >= appendix['page']):
                continue
            if _named(line['text']) == 'bibliography':
                continue
            bib_lines.append({**line, 'id': line['_parent']['id']})
        entries, entries_complete = bibliography_entries(bib_lines)
        for entry in entries:
            line = entry['blocks'][0]
            match = re.match(r'^\s*(?:\[(\d+)\]|(\d+)([.)]))\s+', line['text'])
            records.append({**line, 'number': entry['number'], 'entryText': entry['text'],
                            'separator': match[3] if match and match[2] else '[]'})
    citations = []
    for line in narrative:
        for match in numeric_citations(line['text']):
            citations.extend({'number': n, 'row': {**line, 'text': match['text']}, 'context': line['text']}
                             for n in match['numbers'])
    value = dict(lines=lines, headings=headings, contents=toc, introduction=intro, bibliography=bib,
                 start=start, body=body, captions=captions, narrative=narrative,
                 records=records, citations=citations, entriesComplete=entries_complete,
                 textComplete=bool(document.get('pages')) and all(p.get('text', '').strip() for p in document.get('pages', [])))
    store['itmoMaster'] = value
    return value


def run_itmo_rule(rule, document):
    inv = _inventory(document)
    key = rule['detectorId'].removeprefix('itmo:')
    def finish(status, message, rows=(), exhaustive=False, measurements=None, checks=None):
        unique = {}
        for row in rows:
            ev = _ev(row)
            unique[(ev.get('page'), ev.get('blockId'), tuple(ev.get('bbox') or []), ev.get('quote'), ev.get('check'))] = ev
        out = result(rule, status, message, list(unique.values()),
                     .99 if status in {'pass','violation'} else 0, 'detector:itmo')
        out['coverage'] = {'exhaustive': exhaustive, 'kind': 'layout'}
        out['normativeReference'] = rule['normativeReference']
        if measurements:
            out['measurements'] = measurements
        if checks is not None:
            out['subchecks'] = checks
        out['assessmentScope'] = 'full' if exhaustive else 'counterexample' if status == 'violation' else 'partial'
        if status == 'uncertain':
            out['unresolvedReason'] = 'insufficient_evidence'
        elif status == 'not_checked':
            out['unresolvedReason'] = 'missing_prerequisite'
        return out
    def unknown(message):
        return finish('not_checked', message)
    def violations(rows, message, fallback='Проверенные признаки не доказывают полного соответствия; нужна ручная проверка.'):
        return finish('violation', message, rows) if rows else unknown(fallback)

    if key.startswith('presence:'):
        kind = key.split(':')[1]
        mapped = [x for x in (document.get('map') or {}).get('elements', [])
                  if x.get('type') == kind and x.get('state') == 'confirmed' and x.get('canonicalRole') != 'secondary_copy']
        if kind in {'titlepage','assignment','abstract'}:
            if kind == 'titlepage':
                front = [b for b in document.get('blocks', []) if b.get('page') == 1]
                text = ' '.join(b['text'] for b in front)
                ru = re.search(r'ВЫПУСКНАЯ\s+КВАЛИФИКАЦИОННАЯ\s+РАБОТА', text, re.I)
                en = re.search(r"MASTER[’']?S?\s+THESIS", text, re.I)
                if (ru and re.search(r'Обучающийся\s*:', text, re.I) and re.search(r'Руководитель', text, re.I)) or (en and re.search(r'Student\s*:', text, re.I) and re.search(r'Supervisor\s*:', text, re.I)):
                    title_row = next(b for b in front if re.search(r"ВЫПУСКНАЯ\s+КВАЛИФИКАЦИОННАЯ\s+РАБОТА|MASTER[’']?S?\s+THESIS",b['text'],re.I))
                    return finish('pass', 'Титульный лист найден по совокупности реквизитов. Согласование в ИСУ этим не подтверждается.', [title_row], True)
            rows = [x for x in inv['headings'] if x['section'] == kind]
            if rows:
                return finish('pass', 'Обязательный элемент найден по явному заголовку.', rows, True)
            # A title fact alone does not prove an ISU title page.
            return unknown('Наличие элемента не подтверждено надёжно; отсутствие явного заголовка не доказывает его отсутствия.')
        if kind == 'contents':
            return finish('pass', 'Содержание найдено.', [inv['contents']], True) if inv['contents'] else unknown('Содержание не распознано; требуется проверка полного файла.')
        if mapped:
            ids = {x.get('startBlockId') for x in mapped}
            rows = [b for b in document.get('blocks', []) if b['id'] in ids]
            return finish('pass', 'Элемент подтверждён структурной картой.', rows, True)
        rows = [x for x in inv['headings'] if x['section'] == kind]
        if rows:
            return finish('pass', 'Обязательный элемент найден по явному заголовку.', rows, True)
        # Absence can be asserted only by an explicit, manually reviewed absence
        # fact. Neither failed regex nor a missing map element is such a fact.
        state = ((document.get('map') or {}).get('reviewedAbsences') or {}).get(kind)
        if state is True and inv['textComplete']:
            return finish('violation', 'Отсутствие обязательного элемента явно подтверждено при проверке структуры.', exhaustive=True)
        return unknown('Обязательный элемент не подтверждён. Проверьте структуру: отсутствие распознавания не является доказательством отсутствия.')

    if document.get('sourceFormat') != 'pdf' or not inv['start']:
        return unknown('Нужен PDF с текстовым слоем и надёжно найденным содержанием для определения области проверки.')
    pages = [p for p in document.get('pages', []) if p['number'] >= inv['start']]
    body = inv['body']

    if key == '3-4':
        bad, unclear, entries = toc_issues(document, inv, _centered)
        if bad:
            return finish('violation', 'Подтверждены различия между содержанием и фактическими заголовками/печатными страницами.', bad)
        return finish('uncertain', 'Сопоставлены распознанные записи содержания; полнота всех заголовков и отточий не доказана.',
                      unclear, measurements={'entries': len(entries), 'unresolved': len(unclear)}) if entries else unknown('Записи содержания не выделены однозначно.')

    if key == '4-2-color':
        bad, ambiguous, observed = colored_spans(document, inv)
        checks = [{'aspect': 'colored_text', 'status': 'violation' if bad else 'uncertain', 'coloredSpans': len(bad),
                   'ambiguousSpans': len(ambiguous), 'observedSpans': observed}]
        if bad:
            return finish('violation', 'Обнаружены конкретные цветные текстовые фрагменты. Цвет, вид элемента и координаты приведены для каждого фрагмента.', bad, checks=checks)
        if ambiguous:
            return finish('uncertain', 'Цветные фрагменты найдены, но их принадлежность тексту, формуле или изображению неоднозначна.', ambiguous, checks=checks)
        return unknown('У извлечённых spans цвет чёрный; полнота текстового слоя и всех элементов PDF не доказана.')

    if key == '4-2-spacing':
        samples = []; bad = []
        for b in body:
            ls = b['_prose']; baselines = []
            for line in ls:
                spans = [s for s in line.get('spans', []) if s.get('origin') and s.get('fontSize')
                         and s.get('fontSize') >= (b.get('fontSize') or 0)*.95]
                if spans:
                    baselines.append(median(s['origin'][1] for s in spans))
            steps = [y-x for x, y in zip(baselines, baselines[1:]) if y > x]
            if len(steps) < 2 or not b.get('fontSize'):
                continue
            step = median(steps); ratio = step/b['fontSize']
            sample = {'page': b['page'], 'baselineStepPt': round(step, 3), 'fontSizePt': b['fontSize'], 'stepToEm': round(ratio, 3)}
            samples.append(sample)
            # Single and 1.5-line spacing are font-metric dependent. Only a
            # severe, repeated compression in TNR is a counterexample: the
            # observed step is below even 1.5 times its extracted ink height.
            ink = median(l['bbox'][3]-l['bbox'][1] for l in ls if l.get('bbox'))
            if ratio < 1.2 and step < ink*1.3 and re.search('TimesNewRoman', b.get('fontName') or '', re.I):
                bad.append(issue(b, 'line_spacing_compressed', 'Шаг строк меньше минимально совместимого с полуторным интервалом.', measurement=sample))
        if len(bad) >= 2:
            return finish('violation', 'В нескольких независимых абзацах подтверждён чрезмерно малый шаг строк.', bad, measurements=samples)
        return finish('uncertain', 'Измерен шаг базовых линий. Точное значение интервала редактора по PDF не восстанавливается; полное соответствие не утверждается.', measurements=samples) if samples else unknown('Для оценки интервала нет надёжных базовых линий.')

    if key == '4-2-a4':
        valid = [p for p in pages if p.get('width') and p.get('height')]
        bad = [p for p in valid if abs(p['width']-210*MM)>2 or abs(p['height']-297*MM)>2]
        rows = [dict(page=p['number'], text=f"Размер страницы {p['width']} × {p['height']} пт") for p in bad]
        return finish('violation','Найдены страницы формата, отличного от А4.',rows) if bad else finish('pass','Все страницы начиная с содержания имеют формат А4 (допуск измерения 2 пт).',exhaustive=True) if len(valid)==len(pages) and valid else unknown('Размеры страниц недоступны.')

    if key == '4-2-font':
        if not body:
            return unknown('Недостаточно уверенно выделенных абзацев основного текста.')
        bad = []
        for b in body:
            font = re.sub(r'^[A-Z]{6}\+', '', b.get('fontName') or '')
            size = b.get('fontSize')
            if not size:
                continue
            tnr = bool(re.search(r'times[\s_-]*new[\s_-]*roman|timesnewroman', font, re.I))
            known_other = bool(re.search(r'arial|calibri|cambria(?!math)|liberation|dejavu|cmr|lmr|computer.?modern|nimbus(?:rom|roman)', font, re.I))
            if size < 11.8 or (not tnr and known_other):
                bad.append(issue(b, 'body_font', f'Гарнитура {font}, кегль {size} пт. Требуется Times New Roman, не менее 12 пт.',
                                 measurement={'fontName': font, 'fontSizePt': size}))
        return violations(bad, 'В связных абзацах основного текста обнаружен размер менее 12 пт или иная гарнитура.',
                          'Проверена выборка связных абзацев; полное соответствие гарнитуры и кегля всего текста не доказано.')

    if key in {'4-2-alignment','4-2-indent','4-2-margins'}:
        bad=[]; samples=[]
        for b in body:
            ls=b['_prose']; boxes=[l.get('bbox') for l in ls]
            if any(not x for x in boxes):continue
            width=float(b.get('pageWidth') or next((p['width'] for p in pages if p['number']==b['page']),0))
            height=float(b.get('pageHeight') or next((p['height'] for p in pages if p['number']==b['page']),0))
            if not width or not height:continue
            # The last line is allowed to be short in a justified paragraph.
            left=median(x[0] for x in boxes[1:]); rights=[x[2] for x in boxes[:-1]]
            indent=boxes[0][0]-left
            if key=='4-2-alignment':
                if len(rights)>=3 and max(x[0] for x in boxes[1:])-min(x[0] for x in boxes[1:])<3 and max(rights)-min(rights)>18 and median(rights)>width*.65:
                    bad.append(b);samples.append(dict(page=b['page'],rightSpreadPt=round(max(rights)-min(rights),2)))
            elif key=='4-2-indent':
                # A split paragraph at a page boundary is not a new paragraph.
                text=b.get('text','')
                if not re.match(r'^[\p{Lu}«“"]',text) or not re.search(r'[.!?][»”"]?$',text):continue
                if abs(indent-12.5*MM)>6 and max(x[0] for x in boxes[1:])-min(x[0] for x in boxes[1:])<3:
                    bad.append(b);samples.append(dict(page=b['page'],indentMm=round(indent/MM,2)))
            else:
                # Ink can establish encroachment, not the editor's nominal
                # margin. A 3 mm measurement allowance avoids glyph-box noise.
                if left < 27*MM or max(rights)>width-7*MM or min(x[1] for x in boxes)<17*MM or max(x[3] for x in boxes)>height-17*MM:
                    bad.append(b);samples.append(dict(page=b['page'],leftInkMm=round(left/MM,2),rightInkMm=round((width-max(rights))/MM,2)))
        # Require corroboration by independent paragraphs; one noisy block is
        # insufficient to diagnose paragraph settings.
        return finish('violation','Геометрия нескольких связных абзацев противоречит требованию.',bad,measurements=samples) if len(bad)>=2 else unknown('По геометрии PDF полное соответствие настройки не установлено; недостаточно независимых подтверждений нарушения.')

    if key=='4-3-1':
        pg = pagination(document, inv, _centered)
        checks = [{'aspect': name, 'status': 'violation' if any(r.get('check') == name for r in pg['bad']) else 'observed',
                   'observedPages': len(pg['rows'])} for name in ['page_sequence', 'page_center', 'page_period', 'page_font']]
        if pg['bad']:
            return finish('violation', 'Подтверждены конкретные отклонения напечатанных номеров страниц; каждый признак приведён отдельно.', pg['bad'], checks=checks)
        if pg['complete']:
            return finish('pass', 'На каждой странице от содержания до конца распознан один номер: сквозная последовательность, центр внизу, без точки, гарнитура основного текста. Служебные листы до содержания не включены в проверяемую область.', pg['rows'], exhaustive=True, checks=checks)
        return finish('uncertain', 'Проверены доступные печатные номера; отсутствуют надёжные номера или метаданные шрифта для части страниц.', pg['rows'], checks=checks,
                      measurements={'missingPdfPages': pg['missing'], 'ambiguousPdfPages': pg['ambiguous']})

    headings=[h for h in inv['headings'] if h['page']>=inv['start'] and h['section'] not in {'abstract','assignment'}]
    if key=='4-4-1':
        bad=[]
        for h in headings:
            text=h['text']; box=h.get('bbox'); page=next((p for p in pages if p['number']==h['page']),{})
            # Appendix designation is allowed by 4.11.2 and is not numbering.
            if text.endswith('.') or text!=text.upper() or re.match(r'^\d',text):bad.append(h)
            elif box and page and not _centered(box,page['width']):bad.append(h)
        return violations(bad,'Структурный заголовок имеет нумерацию, строчные буквы, конечную точку или расположен не по центру.')

    if key=='4-4-2':
        starts=[*headings]
        by_id={b['id']:b for b in document.get('blocks',[])}
        for el in (document.get('map') or {}).get('elements',[]):
            if el.get('type')=='chapter' and el.get('state')=='confirmed' and el.get('startBlockId') in by_id:
                starts.append(by_id[el['startBlockId']])
        bad=[]
        for row in starts:
            box=row.get('bbox')
            if not box:continue
            preceding=[l for l in inv['lines'] if l['page']==row['page'] and l.get('bbox') and l['bbox'][3]<box[1]-3 and len(l['text'])>5]
            if preceding:bad.append(row)
        return violations(bad,'Структурный элемент или подтверждённая глава начинается после другого текста на той же странице.')

    if key=='4-4-3':
        bad=[]
        heading_blocks=[b for b in document.get('blocks',[]) if b.get('type')=='heading' and b.get('page',0)>=inv['start'] and not _named(b['text'])]
        for b in heading_blocks:
            match=SECTION.match(_norm(b['text']))
            if match and match['dot']:bad.append(b)
        by_id={b['id']:b for b in document.get('blocks',[])}
        for el in (document.get('map') or {}).get('elements',[]):
            b=by_id.get(el.get('startBlockId'))
            if el.get('type')=='chapter' and el.get('state')=='confirmed' and b:
                match=SECTION.match(_norm(b['text']))
                if not match or match['dot']:bad.append(b)
        return violations(list({b['id']:b for b in bad}.values()),'У подтверждённой главы отсутствует арабский номер или после номера заголовка стоит точка.')

    if key == '4-4-4':
        bad = []; checked = []
        for b in document.get('blocks', []):
            if b.get('type') != 'heading' or b.get('page', 0) < inv['start'] or _named(b['text']):
                continue
            if inv['bibliography'] and b['page'] >= inv['bibliography']['page']:
                continue
            match = SECTION.match(_norm(b['text']))
            if not match:
                continue
            checked.append(b)
            if b['text'].rstrip().endswith('.'):
                bad.append(issue(b, 'heading_period', 'В конце заголовка стоит точка.'))
            ls = b.get('lines') or [b]; box = ls[0].get('bbox')
            if box and abs(box[0]-(30+12.5)*MM) > 8:
                bad.append(issue({**ls[0], 'page': b['page'], '_parent': b}, 'heading_indent',
                                 'Начало заголовка не соответствует абзацному отступу 1,25 см от левого поля 30 мм.',
                                 measurement={'observedLeftMm': round(box[0]/MM, 2), 'expectedLeftMm': 42.5}))
        return finish('violation', 'Подтверждены отклонения оформления заголовков.', bad) if bad else finish('uncertain', 'Проверены отступы и конечные точки заголовков. Аббревиатуры, переносы и подчёркивания полностью не проверены.', checked) if checked else unknown('Не выделены надёжные заголовки разделов.')

    if key in {'4-7-1', '4-7-4'}:
        formulas = [b for b in document.get('blocks', []) if b.get('type') == 'formula'
                    and b.get('page', 0) >= inv['start']]
        bad = []; observations = []
        for b in formulas:
            text = _norm(b['text'])
            match = re.search(r'\((\d+(?:\.\d+)?)\)\s*$', text)
            if not match:
                continue  # unnumbered equations are expressly allowed
            observations.append({'number': match[1], 'page': b['page'], 'bbox': b.get('bbox')})
            # Only a recognized, separate rightmost number span proves position.
            spans = [s for l in b.get('lines', []) for s in l.get('spans', [])
                     if re.fullmatch(r'\('+re.escape(match[1])+r'\)', s.get('text', '').strip())]
            if key == '4-7-4' and len(spans) == 1:
                page = next((p for p in pages if p['number'] == b['page']), {})
                if page and spans[0].get('bbox') and spans[0]['bbox'][2] < page['width']-25*MM:
                    bad.append(issue({**spans[0], 'page': b['page'], '_parent': b}, 'equation_number_right', 'Номер формулы существенно удалён от правого поля.'))
            # A full blank line requires font-dependent formula extents. Record
            # measured boxes, but never infer a violation from an unknown extent.
        if bad:
            return finish('violation', 'Подтверждено отклонение положения номера формулы.', bad, checks=observations)
        return finish('uncertain', 'Выделены кандидаты формул; надёжная полнота формул, свободных строк, пояснений и ссылок не установлена. Отсутствие номера и левое выравнивание сами по себе допустимы.', formulas[:5], checks=observations) if formulas else unknown('Формулы не выделены надёжно; отсутствие распознавания не означает неприменимость.')

    if key in {'4-8-4', '4-8-5', '4-9-2', '4-9-3'}:
        kind = 'abbreviations' if key.startswith('4-8') else 'terms'
        heading = next((h for h in headings if h['section'] == kind), None)
        if not heading:
            return unknown('Список не найден надёжно. Условие необходимости списка этим детектором не устанавливается.')
        if key in {'4-8-5', '4-9-3'}:
            entries = toc_rows(document, inv)
            rows = [e for e in entries if _named(e['tocTitle']) == kind]
            if rows:
                return finish('pass', 'Распознанный список указан в содержании.', rows, True)
            return finish('uncertain', 'Список найден, но запись в содержании не подтверждена; нужна проверка полноты содержания.', [heading])
        next_heading = next((h for h in headings if h['page'] > heading['page']), None)
        rows = [l for l in inv['lines'] if l['page'] >= heading['page']
                and (not next_heading or l['page'] < next_heading['page'])
                and l['_parent']['id'] != heading['_parent']['id']]
        pairs = [(r, re.match(r'^(.+?)\s+[–—]\s+(.+)$', r['text'])) for r in rows]
        pairs = [(r, m) for r, m in pairs if m]
        bad = []
        for row, match in pairs:
            if row['text'].endswith(('.', ',', ';', ':')):
                bad.append(issue(row, 'list_terminal_punctuation', 'В конце строки перечня стоит знак препинания.'))
        # Table format for terms is explicitly permitted; no dash is demanded
        # when a table or wrapped definition prevents reliable row segmentation.
        if bad:
            return finish('violation', 'Подтверждены отклонения оформления распознанного перечня.', bad)
        return finish('uncertain', 'Проверены распознанные строки перечня. Алфавитный порядок, полнота расшифровок и допустимые двухколоночные таблицы требуют надёжной сегментации.', [r for r, _ in pairs[:5]])

    if key.startswith('4-11-'):
        appendices = [h for h in inv['headings'] if h['section'] == 'appendices']
        if not appendices:
            return unknown('Приложения не распознаны; их отсутствие и неприменимость не утверждаются.')
        bad = []
        if key == '4-11-2':
            for h in appendices:
                box = h.get('bbox'); page = next((p for p in pages if p['number'] == h['page']), {})
                if box and page and not _centered(box, page['width']):
                    bad.append(issue(h, 'appendix_heading_center', 'Обозначение приложения не центрировано.'))
                match = re.search(r'\s+([А-ЯЁA-Z])$', h['text'], re.I)
                if match and match[1].upper() in set('ЁЗЙОЧЬЫЪIO'):
                    bad.append(issue(h, 'appendix_letter', 'Использована недопустимая буква обозначения приложения.'))
            return finish('violation' if bad else 'uncertain', 'Проверены отдельные признаки обозначений приложений. Полное оформление заголовков и порядок букв требуют проверки.', bad or appendices)
        if key == '4-11-3':
            pg = pagination(document, inv, _centered)
            return finish('violation', 'Приложения текущего PDF не продолжают сквозную пагинацию.', pg['bad']) if any(r['page'] >= appendices[0]['page'] and r['check'] == 'page_sequence' for r in pg['bad']) else finish('uncertain', 'Проверена доступная пагинация приложений текущего PDF. Наличие отдельного тома не устанавливается.', appendices)
        if key == '4-11-4':
            markers = [(h, re.search(r'\s+([А-ЯЁA-Z]|\d+)$', h['text'], re.I)) for h in appendices]
            markers = [(h, m[1].upper()) for h, m in markers if m]
            first = {}
            for row in inv['narrative']:
                for match in re.finditer(r'\b(?:приложени[еяию]|appendix|appendices)\s+([А-ЯЁA-Z]|\d+)\b', row['text'], re.I):
                    first.setdefault(match[1].upper(), row)
            found = [(h, mark, first[mark]) for h, mark in markers if mark in first]
            if len(found) >= 2 and len(found) == len(markers):
                index = {b['id']: i for i, b in enumerate(document.get('blocks', []))}
                positions = [index.get(r['_parent']['id'], -1) for _, _, r in found]
                if positions != sorted(positions):
                    return finish('violation', 'Порядок приложений расходится с порядком их первых распознанных ссылок.',
                                  [issue(r, 'appendix_reference_order', f'Первая ссылка на приложение {mark}.') for _, mark, r in found])
            missing = [h for h, mark in markers if mark not in first]
            return finish('uncertain', 'Ссылки на приложения и их порядок проверены по распознанным обозначениям. Отсутствие ссылки требует проверки полноты текста.', missing or appendices,
                          measurements={'referenced': [mark for _, mark, _ in found], 'identified': len(markers)})
        if key == '4-11-5':
            entries = toc_rows(document, inv)
            pg = pagination(document, inv, _centered)['printed']
            matches = []
            for h in appendices:
                row = next((r for r in entries if _norm(h['text']).casefold() in r['tocTitle'].casefold()), None)
                if row:
                    matches.append({'appendixPdfPage': h['page'], 'tocPage': row['tocPage']})
                    if h['page'] in pg and row['tocPage'] != pg[h['page']]:
                        bad.append(issue(row, 'appendix_toc_page', f"Неверная страница приложения: указано {row['tocPage']}, напечатано {pg[h['page']]} (PDF {h['page']})."))
            return finish('violation' if bad else 'uncertain', 'Проверены распознанные обозначения и страницы приложений в содержании. Полнота отдельных заголовков требует проверки.', bad or appendices, checks=matches)

    if key == 'A1-links':
        bad = []; groups = {}
        for p in pages:
            if not inv['bibliography'] or p['number'] < inv['bibliography']['page']:
                continue
            for link in (p.get('layoutObjects') or {}).get('links', []):
                if re.match(r'https?://', link['uri']):
                    groups.setdefault((p['number'], link['uri']), []).append(link['bbox'])
        for (page, uri), boxes in groups.items():
            boxes = list({tuple(b): b for b in boxes}.values())
            union = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
            bad.append(issue(dict(page=page, text=uri, bbox=union, bboxes=boxes, sourceKind='pdf_annotation'),
                             'active_resource_link', 'Адрес подтверждён URL-аннотацией PDF; это активная гиперссылка электронного ресурса в списке источников.'))
        return finish('violation', 'В библиографии найдены активные гиперссылки электронных ресурсов (сноска 5 приложения 1).', bad) if bad else finish('uncertain', 'Активные URL-аннотации в библиографии не найдены. Текст URL не является доказательством активной гиперссылки.')

    if key in {'4-5-3','4-6-3','4-6-2-number','4-5-4','4-6-2-links','4-5-2','4-6-1'}:
        kind = 'figure' if key.startswith('4-5') else 'table'
        caps = [c for c in inv['captions'] if c['kind']==kind]
        if not caps:
            return unknown('Подписи соответствующих объектов не распознаны; неприменимость и отсутствие объектов не подтверждены.')
        refs = object_references(inv, kind)
        if key in {'4-5-4','4-6-2-links'}:
            missing = [c for c in caps if c['number'] not in refs]
            per_object = [{'number': c['number'], 'page': c['page'], 'status': 'uncertain' if c in missing else 'found',
                           'referencePages': sorted({r['page'] for r in refs.get(c['number'], [])})} for c in caps]
            if missing:
                return finish('uncertain', 'Для отдельных объектов не найдены словесные ссылки вне подписей. Неполная экстракция не позволяет доказать отсутствие; кандидаты приведены пообъектно.',
                              [issue(c, 'object_reference', 'Ссылка в основном тексте не подтверждена.') for c in missing], checks=per_object)
            # Existence of references for every detected object is recorded, but
            # detection of all vector/raster objects is not guaranteed.
            return finish('not_checked', 'На все распознанные объекты найдены ссылки; полнота обнаружения объектов не подтверждена.',
                          [r for number in refs for r in refs[number][:1]], checks=per_object)
        if key in {'4-5-2','4-6-1'}:
            bad = []; observations = []
            for c in caps:
                obj = object_box(document, c)
                first = min(refs.get(c['number'], []), key=lambda r: (r['page'], r.get('bbox', [0, 0])[1]), default=None)
                observations.append({'number': c['number'], 'objectPage': c['page'], 'firstReferencePage': first['page'] if first else None, 'objectBbox': obj})
                if not first or not obj:
                    continue
                # Applies to the main body only; appendix placement is allowed.
                if c['page'] not in {first['page'], first['page']+1}:
                    bad.append(issue(c, 'object_after_reference', 'Объект не на странице первой ссылки и не на следующей.', measurement=observations[-1]))
                elif c['page'] == first['page'] and first.get('bbox') and obj[3] < first['bbox'][1]:
                    bad.append(issue(c, 'object_after_reference', 'Объект целиком расположен над текстом первой ссылки.', measurement=observations[-1]))
            return finish('violation' if bad else 'uncertain', 'Проверено положение однозначно сопоставленных объектов относительно первой распознанной ссылки; полнота всех объектов не гарантирована.', bad, checks=observations)
        if key == '4-6-2-number':
            bad = caption_number_issues(caps, document)
            return violations(bad, 'Подтверждены отклонения нумерации таблиц (отдельно от оформления названий).')
        bad, observations = caption_format_issues(caps, document, _centered)
        if key == '4-5-3':
            bad.extend(caption_number_issues(caps, document))
        return finish('violation', 'Подтверждены конкретные отклонения подписи: см. причину у каждого фрагмента.', bad, checks=observations) if bad else finish('not_checked', 'Формат распознанных названий проверен; положение и одинарный интервал всех подписей не доказаны.', checks=observations)

    if key.startswith('4-10'):
        records=inv['records'];citations=inv['citations'];bib=inv['bibliography']
        if not bib:return unknown('Библиография не распознана надёжно.')
        if key=='4-10-2':
            els=(document.get('map') or {}).get('elements',[])
            idx={b['id']:i for i,b in enumerate(document.get('blocks',[]))}
            bib_idx=idx.get(bib['_parent']['id'],0)
            bad=[e for e in els if e.get('type') in {'chapter','conclusion'} and idx.get(e.get('endBlockId'),0)>bib_idx]
            if bad:
                return finish('violation','Основной текст продолжается после начала списка источников.',[bib])
            confirmed = [e for e in els if e.get('type') in {'chapter','conclusion'} and e.get('state') == 'confirmed']
            if confirmed and any(e['type'] == 'conclusion' for e in confirmed) and all(e.get('endBlockId') in idx and idx[e['endBlockId']] < bib_idx for e in confirmed):
                return finish('pass', 'Список источников расположен после всех подтверждённых глав и заключения основной работы.', [bib], True)
            return unknown('Положение списка найдено; полнота основной части требует подтверждения структуры.')
        if not records:return unknown('Границы библиографических записей не распознаны надёжно.')
        if key=='4-10-3-order':
            if not citations:return unknown('Не найдены надёжные числовые ссылки для проверки порядка.')
            ids=[r['number'] for r in records]
            if len(ids)!=len(set(ids)):return unknown('Номера библиографии неоднозначны.')
            order={n:i for i,n in enumerate(ids)}
            first_rows={}
            for citation in citations:
                if citation['number'] in order:
                    first_rows.setdefault(citation['number'], citation['row'])
            first=list(first_rows)
            rows=[]
            for a,b in zip(first,first[1:]):
                if order[b] < order[a]:
                    detail=f'Первое упоминание источника {a} предшествует первому упоминанию {b}; порядок записей в списке обратный.'
                    rows.extend([issue(first_rows[a], 'first_citation_order', detail), issue(first_rows[b], 'first_citation_order', detail)])
            return violations(rows,'Порядок первых упоминаний источников не совпадает с порядком записей библиографии.', 'Порядок распознанных ссылок не противоречит списку; полнота всех ссылок не доказана.')
        if key=='4-10-3-format':
            bad=[issue(r, 'reference_number_period', 'После номера записи отсутствует обязательная точка.') for r in records if r['separator']!='.']
            nums=[r['number'] for r in records]
            if nums != list(range(1,len(nums)+1)):
                bad.extend(issue(r, 'reference_number_sequence', 'Записи не образуют последовательную арабскую нумерацию от 1.') for r in records)
            # First line of a reference must start at body margin + indent.
            bad.extend(issue(r, 'reference_indent', 'Запись начинается без требуемого абзацного отступа.',
                             measurement={'observedLeftMm': round(r['bbox'][0]/MM, 2), 'expectedLeftMm': 42.5})
                       for r in records if r.get('bbox') and abs(r['bbox'][0]-(30+12.5)*MM)>8)
            if bad:
                return finish('violation', 'Подтверждены отклонения нумерации или отступа библиографических записей.', bad)
            return finish('pass', 'Все полностью выделенные записи пронумерованы арабскими цифрами с точкой и имеют абзацный отступ.', records, True) if inv['entriesComplete'] and all(r.get('bbox') for r in records) else unknown('Распознанные записи оформлены допустимо; полнота списка не подтверждена.')
        if key in {'4-10-1','4-10-4'}:
            used={c['number'] for c in citations};ids={r['number'] for r in records}
            unmatched = {}
            for c in citations:
                if c['number'] not in ids:
                    row = c['row']; signature = (row['page'], row['_parent']['id'], row['text'])
                    group = unmatched.setdefault(signature, {'row': row, 'numbers': set()})
                    group['numbers'].add(c['number'])
            dangling = [issue(g['row'], 'citation_target', 'В полностью выделенном списке нет записей для номеров: '+', '.join(map(str, sorted(g['numbers'])))+'.') for g in unmatched.values()]
            if dangling and inv['entriesComplete']:
                # 4.10.4 specifies bracket syntax/placement, not completeness
                # of the bibliography. Do not charge the same defect against
                # a clause whose literal criterion has not been disproved.
                out = finish('violation' if key == '4-10-1' else 'uncertain',
                             'Числовые ссылки указывают на отсутствующие библиографические записи.' if key == '4-10-1' else
                             'Сопоставление со списком выявило отсутствующие записи (отдельное нарушение п.4.10.1). Полнота квадратных скобок и положения после всех цитат по п.4.10.4 не доказана.', dangling,
                             measurements={'bibliographyNumbers': sorted(ids), 'danglingNumbers': sorted(used-ids)})
                return out
            missing=[r for r in records if r['number'] not in used]
            if missing:return finish('uncertain','Для части записей не найдены числовые ссылки в квадратных скобках; это не доказывает, что источник не использован. Требуется проверка заимствований и полноты извлечения.',missing)
            return finish('uncertain', 'Ссылки в квадратных скобках сопоставлены со списком. Реальное использование источников, ссылки на все заимствования и положение после цитат требуют содержательной проверки.',
                          measurements={'linkedRecords': len(ids & used), 'records': len(ids)})
    return unknown('Детерминированная проверка этого требования пока не реализована.')
