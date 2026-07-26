from __future__ import annotations
import regex as re
from .common import evidence, contextual, dedupe_evidence, result, is_actual_caption, looks_like_contents, is_code_or_prompt
from .bibliography import run_bibliography_rule
from .abbreviations import run_abbreviation_check, combined_abbreviation_rules
from ..document.numbered_items import collect_unique_numbered_items

DEFERRED_ABBREVIATIONS={'CORE-4-1','CORE-4-2','CORE-4-3','CORE-12'}


def _pass(rule,msg): return result(rule,'pass',msg,confidence=1)
def _violation(rule,msg,ev,fix): return result(rule,'violation',msg,ev,1,'detector',fix)
def _uncertain(rule,msg): return result(rule,'uncertain',msg,confidence=0)
def _na(rule,msg): return result(rule,'not_applicable',msg,confidence=0)


def _map_blocks(document:dict,types:set[str])->list[dict]:
    mv=document.get('map') or {}; blocks=document.get('blocks',[]); idx={b['id']:i for i,b in enumerate(blocks)}; out=[]; seen=set()
    for el in mv.get('elements',[]):
        if el.get('type') not in types: continue
        s=idx.get(el.get('startBlockId')); e=idx.get(el.get('endBlockId'))
        if s is None or e is None or s>e: continue
        for b in blocks[s:e+1]:
            if b['id'] not in seen: seen.add(b['id']); out.append(b)
    return out


def _positions_chapters(rule,document):
    positions=len(collect_unique_numbered_items(document.get('fields',{}).get('defenseStatements',[])))
    chapters=len(document.get('fields',{}).get('chapterHeadings',[]))
    if not positions: return _uncertain(rule,'Не удалось определить число положений, выносимых на защиту.')
    if chapters<positions:
        ev=[evidence(b,b['text']) for b in document['fields'].get('chapterHeadings',[])]
        return _violation(rule,f'Распознано положений: {positions}; глав: {chapters}. Отдельная содержательная глава для каждого положения по количеству невозможна.',ev,'Сопоставить каждое положение с отдельной содержательной главой.')
    return _pass(rule,f'В тексте распознано {positions} положений и {chapters} глав: соответствие по количеству возможно. Часть правила о презентации требует отдельного файла.')


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
    for b in document.get('blocks',[]):
        if b.get('type')!='heading' and not is_actual_caption(b): continue
        t=b.get('text','').strip()
        if re.search(r'^глава\s+\d+\s+(?![.–—-])',t,re.I) or re.search(r'^\d+(?:\.\d+)+\s+(?=[А-ЯЁ])',t) or t.endswith('.'):
            ev.append(evidence(b,t))
    return _violation(rule,'Обнаружен заголовок или подпись с неверной точкой после номера либо лишней точкой в конце.',ev[:15],'Исправить нумерацию и убрать точку в конце названия.') if ev else _pass(rule,'Явных нарушений формата распознанных заголовков и подписей не обнаружено.')


def _chapter_heading_order(rule,document):
    ev=[]
    for b in document.get('blocks',[]):
        for line in b.get('text','').splitlines():
            if re.match(r'^\s*\d+\s+глава\b',line,re.I): ev.append(evidence(b,line.strip()))
    return _violation(rule,'Обнаружен заголовок вида «1 Глава».',ev,'Использовать порядок «Глава 1».') if ev else _pass(rule,'Заголовки вида «1 Глава» не обнаружены.')


def _object_key(text:str):
    m=re.match(r'^(рис(?:унок)?|табл(?:ица)?)\.?\s*(\d+(?:\.\d+)?)',text.strip(),re.I)
    return (('рис' if m.group(1).lower().startswith('рис') else 'табл')+':'+m.group(2)) if m else None


def _reference_keys(text:str):
    normalized=re.sub(r'([А-ЯЁа-яё])(?:-|\u00ad)\s*\n\s*([А-ЯЁа-яё])',r'\1\2',text)
    out=[]
    for m in re.finditer(r'(?<![\p{L}\p{N}_])(?:рис\.?|рисунок|рисунк(?:е|а|у|ом)|табл\.?|таблиц(?:а|е|ы|у|ей)|figure|fig\.?|table)\s*(\d+(?:\.\d+)?)',normalized,re.I):
        out.append(('рис' if re.search(r'рис|fig|figure',m.group(0),re.I) else 'табл')+':'+m.group(1))
    return list(dict.fromkeys(out))


def _nearest_section(blocks,before):
    for i in range(before-1,-1,-1):
        t=re.sub(r'^\s*\d{1,3}\s*\n','',blocks[i].get('text','')).strip()
        if re.match(r'^(?:Реферат|Synopsis|Введение|ГЛАВА\s+\d+|Заключение|Приложение\b)',t,re.I): return i
    return 0


def _figure_order(rule,document):
    captions=[]; blocks=document.get('blocks',[])
    for i,b in enumerate(blocks):
        key=_object_key(b.get('text',''))
        if key and is_actual_caption(b): captions.append((b,i,key))
    if not captions: return _uncertain(rule,'Подписи рисунков или таблиц не удалось распознать.')
    ev=[]
    for b,i,key in captions:
        start=_nearest_section(blocks,i); prior='\n'.join(x.get('text','') for x in blocks[start:i])
        if key not in _reference_keys(prior): ev.append(evidence(b,b['text']))
    return _violation(rule,'Для объекта не найдена предшествующая ссылка в пределах текущего раздела документа.',ev[:15],'Добавить ссылку на рисунок или таблицу до объекта.') if ev else _pass(rule,'Для распознанных подписей найдены предшествующие ссылки в соответствующих разделах.')


def _figure_reference_no_see(rule,document):
    ev=[]
    for b in document.get('blocks',[]):
        for m in re.finditer(r'\bсм\.\s*(?:рис|рисунок)\.?\s*\d+',b.get('text',''),re.I): ev.append(evidence(b,contextual(b['text'],m.start(),len(m.group()))))
    order=_figure_order({**rule,'id':'CORE-7-1'},document)
    if order.get('status')=='violation': ev+=order.get('evidence',[])
    ev=dedupe_evidence(ev)
    return _violation(rule,'Обнаружена ссылка «см. рис.» либо рисунок без предшествующей ссылки.',ev[:15],'Сослаться на рисунок до его размещения и убрать «см.».') if ev else _uncertain(rule,'Явные конструкции «см. рис.» и нарушения порядка не обнаружены; визуальное положение рисунка требует просмотра PDF.')


def _caption_format(rule,document):
    captions=[b for b in document.get('blocks',[]) if is_actual_caption(b)]
    if not captions: return _uncertain(rule,'Подписи объектов не распознаны.')
    ev=[evidence(b,b['text']) for b in captions if b.get('text','').strip().endswith('.')]
    return _violation(rule,'Подпись рисунка или название таблицы заканчивается точкой.',ev[:15],'Убрать точку в конце подписи или названия.') if ev else _uncertain(rule,'Точки в конце подписей не обнаружены, но положение подписи относительно объекта по текстовому слою не проверяется.')


def _formula_numbering(rule,document):
    formulas=[b for b in document.get('blocks',[]) if b.get('type')=='formula']
    if not formulas: return _na(rule,'Формулы в извлечённом тексте не распознаны.')
    bad=[]; numbered=0
    for b in formulas:
        t=b.get('text','')
        if re.search(r'\(\d+(?:\.\d+)*\)',t): numbered+=1
        if re.search(r'\(\d+(?:\.\d+)*\)\s*[.,;:]',t): bad.append(evidence(b,t[:450]))
    return _violation(rule,'В том же извлечённом блоке знак препинания расположен после номера формулы.',bad,'Перенести знак перед номером формулы.') if bad else _uncertain(rule,f'Распознано формул: {len(formulas)}; в тех же блоках найдено номеров: {numbered}. Отсутствие или расположение остальных номеров требует просмотра PDF.')


def _formula_explanation(rule,document):
    formulas=[b for b in document.get('blocks',[]) if b.get('type')=='formula']
    return _na(rule,'Формулы в извлечённом тексте не распознаны.') if not formulas else _uncertain(rule,'Наличие слова «где», перенос строки и регистр после формул необходимо подтвердить визуально.')


def _chapter_conclusions(rule,document):
    chapters=document.get('fields',{}).get('chapterHeadings',[]); blocks=document.get('blocks',[])
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
    for b in document.get('blocks',[]):
        if b['id'] in bibids: continue
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
    blocks=document.get('blocks',[]); code=[b for b in blocks if _is_code(b.get('text',''))]
    if not code:return _na(rule,'Фрагменты программного кода не обнаружены.')
    ev=[]
    for b in code:
        i=next((j for j,x in enumerate(blocks) if x['id']==b['id']),0); prior=' '.join(x.get('text','') for x in blocks[max(0,i-3):i])
        if not re.search(r'(?:фрагмент|код|листинг|реализ|выполня|алгоритм)',prior,re.I): ev.append(evidence(b,b.get('text','')[:400]))
    return _violation(rule,'Для фрагмента кода не найдено предшествующее описание.',ev,'Перед кодом объяснить его назначение.') if ev else _pass(rule,'Для распознанных фрагментов кода найдено описание.')


def _heading_periods(rule,document):
    ev=[evidence(b,b['text']) for b in document.get('blocks',[]) if (b.get('type')=='heading' or is_actual_caption(b)) and b.get('text','').strip().endswith('.')]
    return _violation(rule,'В конце заголовка или подписи стоит точка.',ev[:15],'Убрать точку.') if ev else _pass(rule,'В распознанных заголовках и подписях лишние точки не обнаружены.')


def run_structural(rule:dict,document:dict)->dict:
    rid=rule['id']
    if rid in DEFERRED_ABBREVIATIONS:
        return _uncertain(rule,'Автоматическая проверка аббревиатур временно отключена: текущий детектор недостаточно надёжен для категорического вывода.')
    if rid == 'SOFT-055': return combined_abbreviation_rules(rule,document)
    if rid in {'SOFT-056','SOFT-077','SOFT-151'}: return run_abbreviation_check(rule,document)
    if rid=='CORE-1-6': return _positions_chapters(rule,document)
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
