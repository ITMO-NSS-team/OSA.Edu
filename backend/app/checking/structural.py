from __future__ import annotations
import regex as re
from .common import evidence, contextual, dedupe_evidence, result, is_actual_caption, looks_like_contents, is_code_or_prompt, mapped_excluded_ids
from .bibliography import run_bibliography_rule
from .abbreviations import run_abbreviation_check, combined_abbreviation_rules
from ..document.numbered_items import collect_unique_defense_items
from ..scope import main_work_ids

DEFERRED_ABBREVIATIONS={'CORE-4-1','CORE-4-2','CORE-4-3','CORE-12'}


def _pass(rule,msg): return result(rule,'pass',msg,confidence=1)
def _violation(rule,msg,ev,fix): return result(rule,'violation',msg,ev,1,'detector',fix)
def _uncertain(rule,msg): return result(rule,'uncertain',msg,confidence=0)
def _na(rule,msg): return result(rule,'not_applicable',msg,confidence=0)


def _main_work_blocks(document:dict)->list[dict]:
    """Canonical blocks for ordinary structural checks.

    A mapped document is strictly scoped to the main dissertation. Without a map
    we preserve legacy whole-document behaviour for compatibility.
    """
    ids=main_work_ids(document)
    blocks=list(document.get('blocks',[]))
    return blocks if ids is None else [b for b in blocks if str(b.get('id')) in ids]


def _map_blocks(document:dict,types:set[str])->list[dict]:
    mv=document.get('map') or {}; blocks=document.get('blocks',[]); idx={b['id']:i for i,b in enumerate(blocks)}; out=[]; seen=set()
    for el in mv.get('elements',[]):
        if el.get('type') not in types: continue
        s=idx.get(el.get('startBlockId')); e=idx.get(el.get('endBlockId'))
        if s is None or e is None or s>e: continue
        for b in blocks[s:e+1]:
            if b['id'] not in seen: seen.add(b['id']); out.append(b)
    return out


def _positions_chapters(rule,document,routing_fragments=None):
    positions=len(collect_unique_defense_items(document.get('fields',{}).get('defenseStatements',[])))
    chapters=document.get('fields',{}).get('chapterHeadings',[])
    if not positions:
        return _uncertain(rule,'Положения, выносимые на защиту, не подтверждены как отдельная секция; соответствие «положение → отдельная глава» автоматически не оценивается.')

    matrix=[
        item for item in (routing_fragments or [])
        if item.get('selector')=='defense_chapter_matrix'
    ]
    if len(matrix) < positions:
        return _uncertain(rule,f'Распознано положений: {positions}, но надёжное смысловое сопоставление с главами построено только для {len(matrix)}. Проверка по одному количеству глав недостаточна.')

    primary_ids=[]
    ambiguous=[]
    for row in matrix[:positions]:
        ids=[str(value) for value in (row.get('chapterIds') or []) if value]
        # When semantic evidence is absent, router intentionally exposes all
        # chapters. That is conservative context, not proof of a one-to-one map.
        if len(ids) != 1 or not row.get('complete'):
            ambiguous.append(row)
            continue
        primary_ids.append(ids[0])
    if ambiguous:
        return _uncertain(rule,'Не для всех положений определена одна подтверждённая основная глава; автоматический вывод по CORE-1-6 был бы ненадёжным.')

    duplicates=sorted({chapter_id for chapter_id in primary_ids if primary_ids.count(chapter_id)>1})
    if duplicates:
        ev=[evidence(b,b.get('text','')) for b in chapters if str(b.get('id')) in duplicates]
        return _violation(
            rule,
            f'Для {positions} положений построено смысловое сопоставление с главами, но одна и та же основная глава назначена нескольким положениям. Требование отдельной содержательной главы для каждого положения не выполнено.',
            ev,
            'Разнести положения по отдельным содержательным главам либо скорректировать структуру/формулировки так, чтобы для каждого положения была своя основная глава.',
        )
    if len(set(primary_ids)) < positions:
        return _uncertain(rule,'Не удалось подтвердить уникальную основную главу для каждого положения.')
    return _pass(rule,f'Для всех {positions} положений построено отдельное смысловое сопоставление с {positions} различными основными главами. Часть правила о презентации требует отдельного файла.')


def _defense_section(rule,document):
    element=next((x for x in (document.get('map') or {}).get('elements',[]) if x.get('type')=='defense_statements'),None)
    if not element or not document.get('fields',{}).get('defenseStatements'): return _violation(rule,'Отдельный раздел с положениями, выносимыми на защиту, не распознан.',[],'Добавить отдельный раздел с положениями на защиту.')
    return _uncertain(rule,'Отдельный раздел с положениями распознан, но указание научной новизны или практической значимости внутри каждого положения требует смысловой проверки.')


def _chapters_new_page(rule,document):
    if not document.get('pages'): return _uncertain(rule,'Для DOCX без постраничной разметки начало глав с новой страницы проверить нельзя.')
    ev=[]; blocks=document.get('blocks',[])
    for h in document.get('fields',{}).get('chapterHeadings',[]):
        page=h.get('page')
        if not page: continue
        page_blocks=[b for b in blocks if b.get('page')==page]
        try: pos=next(i for i,b in enumerate(page_blocks) if b['id']==h['id'])
        except StopIteration: continue
        if pos>1: ev.append(evidence(h,h['text']))
    return _violation(rule,'Заголовок главы расположен не в начале страницы.',ev,'Перенести начало главы на новую страницу.') if ev else _pass(rule,'Распознанные заголовки глав находятся в начале страниц.')


def _heading_format(rule,document):
    ev=[]
    for b in _main_work_blocks(document):
        if b.get('type')!='heading' and not is_actual_caption(b): continue
        t=b.get('text','').strip()
        is_chapter=bool(re.match(r'^глава\s+\d+\b',t,re.I))
        is_section=bool(re.match(r'^\d+(?:\.\d+)+(?:\.)?\s+',t))
        is_numbered_object=bool(re.match(r'^(?:рис(?:унок)?|таблица)\s*\d+(?:\.\d+)?\b',t,re.I))
        # CORE-5-4 explicitly concerns numbered chapters/sections/tables/figures.
        # Generic headings such as ``Основное содержание работы.`` belong to
        # CORE-19 and must not be evidence for this rule.
        if not (is_chapter or is_section or is_numbered_object):
            continue
        bad_separator = bool(
            re.search(r'^глава\s+\d+\s+(?![.–—-])',t,re.I)
            or re.search(r'^\d+(?:\.\d+)+\s+(?=[А-ЯЁ])',t)
        )
        if bad_separator or t.endswith('.'):
            ev.append(evidence(b,t))
    return _violation(rule,'Обнаружен нумерованный заголовок или подпись с неверной точкой после номера либо лишней точкой в конце.',ev[:15],'Исправить нумерацию и убрать точку в конце названия.') if ev else _pass(rule,'Явных нарушений формата распознанных нумерованных заголовков и подписей не обнаружено.')


def _chapter_heading_order(rule,document):
    ev=[]
    for b in _main_work_blocks(document):
        for line in b.get('text','').splitlines():
            if re.match(r'^\s*\d+\s+глава\b',line,re.I): ev.append(evidence(b,line.strip()))
    return _violation(rule,'Обнаружен заголовок вида «1 Глава».',ev,'Использовать порядок «Глава 1».') if ev else _pass(rule,'Заголовки вида «1 Глава» не обнаружены.')



def _is_strict_object_caption(block: dict) -> bool:
    """True only for an actual numbered object caption, not a prose reference.

    PDF extraction can occasionally classify «Таблица 1.1 показывает ...» as a
    caption because it starts with the object name.  CORE-7-1 must treat that as
    the *preceding reference*, while an object caption requires a separator after
    the number (dash, colon or dot).
    """
    if block.get('type') != 'caption':
        return False
    text = (block.get('text') or '').strip()
    return bool(re.match(
        r'^(?:рис(?:унок)?|таблица|график|figure|fig\.?|table)\s*\.?\s*\d+(?:\.\d+)*(?:\s*[–—-]{1,2}\s*|\s*:\s*|\.(?!\d)\s+)\S',
        text,
        re.I,
    ))

def _object_key(text:str):
    m=re.match(r'^(рис(?:унок)?|табл(?:ица)?)\.?\s*(\d+(?:\.\d+)?)',text.strip(),re.I)
    return (('рис' if m.group(1).lower().startswith('рис') else 'табл')+':'+m.group(2)) if m else None


def _reference_keys(text:str):
    normalized=re.sub(r'([А-ЯЁа-яё])(?:-|\u00ad)\s*\n?\s*([А-ЯЁа-яё])',r'\1\2',text)
    out=[]
    pattern = re.compile(r'(?<![\p{L}\p{N}_])(?P<kind>рис\.?|рисунок|рисунк(?:е|а|у|ом|ах|ами)|табл\.?|таблиц(?:а|е|ы|у|ей|ах|ами)|figure|fig\.?|table)\s*(?P<num>\d+(?:\.\d+)?)(?P<tail>(?:\s*(?:,|и|–|—|-)\s*\d+(?:\.\d+)?)*)', re.I)
    for m in pattern.finditer(normalized):
        prefix='рис' if re.search(r'рис|fig|figure',m.group('kind'),re.I) else 'табл'
        out.append(prefix+':'+m.group('num'))
        for number in re.findall(r'\d+(?:\.\d+)?', m.group('tail') or ''):
            out.append(prefix+':'+number)
    return list(dict.fromkeys(out))


def _nearest_section(blocks,before):
    for i in range(before-1,-1,-1):
        t=re.sub(r'^\s*\d{1,3}\s*\n','',blocks[i].get('text','')).strip()
        if re.match(r'^(?:Реферат|Synopsis|Введение|ГЛАВА\s+\d+|Заключение|Приложение\b)',t,re.I): return i
    return 0


def _figure_order(rule,document):
    captions=[]; blocks=_main_work_blocks(document)
    for i,b in enumerate(blocks):
        key=_object_key(b.get('text',''))
        if key and _is_strict_object_caption(b): captions.append((b,i,key))
    if not captions: return _uncertain(rule,'Подписи рисунков или таблиц не удалось распознать.')
    ev=[]
    for b,i,key in captions:
        start=_nearest_section(blocks,i); prior='\n'.join(x.get('text','') for x in blocks[start:i])
        if key not in _reference_keys(prior): ev.append(evidence(b,b['text']))
    return _violation(rule,'Для объекта не найдена предшествующая ссылка в пределах текущего раздела документа.',ev[:15],'Добавить ссылку на рисунок или таблицу до объекта.') if ev else _pass(rule,'Для распознанных подписей найдены предшествующие ссылки в соответствующих разделах.')


def _figure_reference_no_see(rule,document):
    ev=[]
    for b in _main_work_blocks(document):
        for m in re.finditer(r'\bсм\.\s*(?:рис|рисунок)\.?\s*\d+',b.get('text',''),re.I): ev.append(evidence(b,contextual(b['text'],m.start(),len(m.group()))))
    order=_figure_order({**rule,'id':'CORE-7-1'},document)
    if order.get('status')=='violation': ev+=order.get('evidence',[])
    ev=dedupe_evidence(ev)
    return _violation(rule,'Обнаружена ссылка «см. рис.» либо рисунок без предшествующей ссылки.',ev[:15],'Сослаться на рисунок до его размещения и убрать «см.».') if ev else _uncertain(rule,'Явные конструкции «см. рис.» и нарушения порядка не обнаружены; визуальное положение рисунка требует просмотра PDF.')


def _caption_format(rule,document):
    captions=[b for b in _main_work_blocks(document) if _is_strict_object_caption(b)]
    if not captions: return _uncertain(rule,'Подписи объектов не распознаны.')
    ev=[evidence(b,b['text']) for b in captions if b.get('text','').strip().endswith('.')]
    return _violation(rule,'Подпись рисунка или название таблицы заканчивается точкой.',ev[:15],'Убрать точку в конце подписи или названия.') if ev else _uncertain(rule,'Точки в конце подписей не обнаружены, но положение подписи относительно объекта по текстовому слою не проверяется.')


def _formula_numbering(rule,document):
    formulas=[b for b in _main_work_blocks(document) if b.get('type')=='formula']
    if not formulas: return _na(rule,'Формулы в извлечённом тексте не распознаны.')
    bad=[]; numbered=0
    for b in formulas:
        t=b.get('text','').strip()
        # A formula number is a parenthesized integer/section number aligned at
        # the end of the formula block. Parentheses inside expressions such as
        # s(1), x(2) or {s(1), ..., s(N)} are indices/arguments, not numbering.
        if re.search(r'\(\d+(?:\.\d+)*\)\s*$', t):
            numbered += 1
        if re.search(r'\(\d+(?:\.\d+)*\)\s*[.,;:]\s*$', t):
            bad.append(evidence(b,t[:450]))
    return _violation(rule,'В том же извлечённом блоке знак препинания расположен после номера формулы.',bad,'Перенести знак перед номером формулы.') if bad else _uncertain(rule,f'Распознано формул: {len(formulas)}; в тех же блоках найдено номеров: {numbered}. Отсутствие или расположение остальных номеров требует просмотра PDF.')


def _formula_explanation(rule,document):
    formulas=[b for b in _main_work_blocks(document) if b.get('type')=='formula']
    return _na(rule,'Формулы в извлечённом тексте не распознаны.') if not formulas else _uncertain(rule,'Наличие слова «где», перенос строки и регистр после формул необходимо подтвердить визуально.')


def _chapter_conclusions(rule,document):
    chapters=document.get('fields',{}).get('chapterHeadings',[]); blocks=_main_work_blocks(document)
    if not chapters: return _uncertain(rule,'Заголовки глав не распознаны.')
    ev=[]
    for i,ch in enumerate(chapters):
        try: start=next(j for j,b in enumerate(blocks) if b['id']==ch['id'])
        except StopIteration: continue
        end=len(blocks)
        if i+1<len(chapters):
            try:end=next(j for j,b in enumerate(blocks) if b['id']==chapters[i+1]['id'])
            except StopIteration: pass
        segment=blocks[start+1:end]; ci=next((j for j,b in enumerate(segment) if re.search(r'выводы(?:\s+по\s+главе)?',b.get('text',''),re.I)),None)
        if ci is None: ev.append(evidence(ch,ch['text'])); continue
        conclusion=' '.join(b.get('text','') for b in segment[ci+1:ci+8])
        if not re.search(r'(?:^|\s)1\.\s+(?=[А-ЯЁA-Z])',conclusion): ev.append(evidence(segment[ci],(segment[ci]['text']+' '+conclusion[:500]).strip()))
    return _violation(rule,'Для главы не найдены выводы, оформленные нумерованным списком.',ev[:15],'Представить выводы по каждой главе нумерованным списком.') if ev else _pass(rule,'Для распознанных глав найдены выводы с нумерованными пунктами.')


def _citation_numbers(value:str)->set[int]:
    out=set(); normalized=value.replace('−','–').replace('—','–')
    for m in re.finditer(r'(\d{1,3})\s*[–-]\s*(\d{1,3})',normalized):
        a,b=int(m.group(1)),int(m.group(2))
        if b>=a and b-a<=200: out.update(range(a,b+1))
    no_ranges=re.sub(r'\d{1,3}\s*[–-]\s*\d{1,3}',' ',normalized)
    out.update(int(x) for x in re.findall(r'\d{1,3}',no_ranges))
    return out


def _bibliography_refs(rule,document):
    entries=document.get('fields',{}).get('bibliographyBlocks',[])
    if not entries: return _uncertain(rule,'Список литературы не удалось распознать.')
    nums=set()
    for b in entries:
        for m in re.finditer(r'(?:^|\n|\s)(\d{1,3})[.)]\s+(?=[А-ЯЁA-Z])',b.get('text',''),re.M): nums.add(int(m.group(1)))
    if not nums: return _uncertain(rule,'Нумерацию библиографии не удалось распознать.')
    bibids={b['id'] for b in entries}; cited=set()
    excluded=mapped_excluded_ids(document)
    for b in document.get('blocks',[]):
        if b['id'] in bibids or b['id'] in excluded: continue
        for br in re.finditer(r'\[([^\]]+)\]',b.get('text','')): cited|=_citation_numbers(br.group(1))
    missing=sorted(nums-cited)
    if not missing: return _pass(rule,'Для всех распознанных источников найдены ссылки в основном тексте, включая номера внутри диапазонов.')
    ev=[]
    for b in entries:
        if any(re.search(rf'(?:^|\s){n}[.)]\s+',b.get('text',''),re.M) for n in missing): ev.append(evidence(b,b.get('text','')[:450]))
    return _violation(rule,'Не найдены ссылки на источники: '+', '.join(map(str,missing[:25]))+'.',ev[:15],'Добавить ссылки либо удалить неиспользованные записи.')


def _is_code(value:str)->bool:
    if '```' in value:return True
    lines=[x.strip() for x in value.splitlines() if x.strip()]
    statements=sum(bool(re.match(r'^(?:def\s+\w+\s*\(|class\s+\w+|import\s+\w+|from\s+\w+\s+import|function\s+\w+\s*\(|(?:SELECT|INSERT|UPDATE|DELETE)\b)',x)) for x in lines)
    xml=sum(bool(re.match(r'^</?[A-Za-z][^>]*>',x)) for x in lines); syntax=len(re.findall(r'[{};]|=>|:=|==|\b(?:const|let|var)\s+\w+',value))
    return statements>=2 or xml>=3 or (statements>=1 and syntax>=3)


def _code_explanation(rule,document):
    blocks=_main_work_blocks(document); code=[b for b in blocks if _is_code(b.get('text',''))]
    if not code:return _na(rule,'Фрагменты программного кода не обнаружены.')
    ev=[]
    for b in code:
        i=next((j for j,x in enumerate(blocks) if x['id']==b['id']),0); prior=' '.join(x.get('text','') for x in blocks[max(0,i-3):i])
        if not re.search(r'(?:фрагмент|код|листинг|реализ|выполня|алгоритм)',prior,re.I): ev.append(evidence(b,b.get('text','')[:400]))
    return _violation(rule,'Для фрагмента кода не найдено предшествующее описание.',ev,'Перед кодом объяснить его назначение.') if ev else _pass(rule,'Для распознанных фрагментов кода найдено описание.')


def _heading_periods(rule,document):
    ev=[evidence(b,b['text']) for b in _main_work_blocks(document) if (b.get('type')=='heading' or is_actual_caption(b)) and b.get('text','').strip().endswith('.')]
    return _violation(rule,'В конце заголовка или подписи стоит точка.',ev[:15],'Убрать точку.') if ev else _pass(rule,'В распознанных заголовках и подписях лишние точки не обнаружены.')


def run_structural(rule:dict,document:dict,routing_fragments:list[dict]|None=None)->dict:
    rid=rule['id']
    if rid in DEFERRED_ABBREVIATIONS:
        return _uncertain(rule,'Автоматическая проверка аббревиатур временно отключена: текущий детектор недостаточно надёжен для категорического вывода.')
    if rid == 'SOFT-055': return combined_abbreviation_rules(rule,document)
    if rid in {'SOFT-056','SOFT-077','SOFT-151'}: return run_abbreviation_check(rule,document)
    if rid=='CORE-1-6': return _positions_chapters(rule,document,routing_fragments)
    if rid=='SOFT-026': return _defense_section(rule,document)
    if rid in {'CORE-5-3','SOFT-062'}: return _chapters_new_page(rule,document)
    if rid=='CORE-5-4': return _heading_format(rule,document)
    if rid=='SOFT-120': return _chapter_heading_order(rule,document)
    if rid in {'CORE-7-1','SOFT-065'}: return _figure_order(rule,document)
    if rid=='SOFT-064': return _figure_reference_no_see(rule,document)
    if rid in {'CORE-7-2','SOFT-066'}: return _caption_format(rule,document)
    if rid in {'CORE-7-4','SOFT-070'}: return _formula_numbering(rule,document)
    if rid in {'CORE-7-5','SOFT-072'}: return _formula_explanation(rule,document)
    if rid in {'CORE-8-1','SOFT-160'}: return _chapter_conclusions(rule,document)
    if rid in {'CORE-9-1','CORE-18'}: return run_bibliography_rule(rule,document)
    if rid=='CORE-9-4': return _bibliography_refs(rule,document)
    if rid=='CORE-13': return _code_explanation(rule,document)
    if rid=='CORE-19': return _heading_periods(rule,document)
    if rid=='SOFT-139': return _uncertain(rule,'Смешение русских и английских аббревиатур требует смыслового сопоставления терминов.')
    text=(rule.get('category','')+' '+rule.get('requirement','')).lower()
    if re.search(r'аббревиатур|сокращени',text): return run_abbreviation_check(rule,document)
    if re.search(r'вывод.*глав',text): return _chapter_conclusions(rule,document)
    if re.search(r'ссылк.*источник|все источники',text): return _bibliography_refs(rule,document)
    return _uncertain(rule,'Для этого структурного правила нет достаточно надёжного алгоритма; автоматический вердикт не формируется.')
