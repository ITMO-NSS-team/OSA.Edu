from __future__ import annotations
import regex as re
from ..util import compact, normalized_quote
from ..scope import main_work_ids, is_code_or_prompt as _scope_is_code_or_prompt


def evidence(block:dict, quote:str) -> dict:
    clean=compact(quote)
    out={'quote':clean,'blockId':block['id'],'location':block.get('location',''),'verified':True}
    source=block.get('text','')
    start=source.find(clean)
    if start>=0:
        out['start']=start
        out['end']=start+len(clean)
    if block.get('page') is not None: out['page']=block['page']
    return out


def contextual(text:str,index:int,length:int,before:int=80,after:int=140) -> str:
    return compact(text[max(0,index-before):min(len(text),index+length+after)])


def dedupe_evidence(items:list[dict]) -> list[dict]:
    seen=set(); out=[]
    for item in items:
        key=(item.get('blockId'),normalized_quote(item.get('quote','')))
        if key not in seen: seen.add(key); out.append(item)
    return out


def result(rule:dict,status:str,explanation:str,evidence_items:list[dict]|None=None,confidence:float=0,checked_by:str='detector',fix:str|None=None,evidence_status:str|None=None) -> dict:
    ev=evidence_items or []
    out={'ruleId':rule['id'],'status':status,'severity':rule.get('severity','major'),'explanation':explanation,'confidence':confidence,'evidence':ev,'checkedBy':checked_by,'evidenceStatus':evidence_status or ('verified' if ev else 'not_required')}
    if fix: out['fix']=fix
    return out


def is_actual_caption(block:dict) -> bool:
    return block.get('type')=='caption' and bool(re.match(r'^(?:рис(?:унок)?|таблица|график)\s*\d+(?:\.\d+)?\s*(?:[.–—-]{1,2}|:)',(block.get('text') or '').strip(),re.I))


def mapped_excluded_ids(document:dict) -> set[str]:
    map_value=document.get('map') or {}; blocks=document.get('blocks',[]); idx={b['id']:i for i,b in enumerate(blocks)}; excluded=set()
    for el in map_value.get('elements',[]):
        if el.get('type') not in {'bibliography','appendices'}: continue
        s=idx.get(el.get('startBlockId')); e=idx.get(el.get('endBlockId'))
        if s is not None and e is not None and s<=e: excluded.update(b['id'] for b in blocks[s:e+1])
    return excluded




def mapped_scientific_body_ids(document: dict) -> set[str] | None:
    """Return ids of authored prose in the canonical main work only.

    3.8 intentionally excludes abstract/synopsis/front matter. Ordinary language
    checks must not report the same issue from an early synopsis copy or from
    attached publication reprints.
    """
    return main_work_ids(document)


def looks_like_contents(value:str) -> bool:
    c=compact(value)
    return bool(re.search(r'оглавление|содержание',c,re.I) or (re.search(r'(?:\.\s*){5,}',c) and re.search(r'\b(?:глава|введение|заключение|раздел)\b',c,re.I)))


def is_code_or_prompt(value:str) -> bool:
    # Backwards-compatible export used by candidate detectors. The authoritative
    # implementation lives in backend.app.scope.
    return _scope_is_code_or_prompt(value)


def contents_page_range(document: dict) -> set[int]:
    blocks = document.get('blocks', [])
    start = next((b.get('page') for b in blocks if b.get('page') is not None and re.search(r'(?:^|\n)\s*(?:\d+\s*)?(?:оглавление|содержание)', b.get('text', ''), re.I)), None)
    if start is None:
        return set()
    end = next((b.get('page') for b in blocks if b.get('page') is not None and b.get('page') > start and re.search(r'(?:^|\n)\s*(?:\d+\s*)?(?:реферат|synopsis|введение)', b.get('text', ''), re.I)), None)
    return set(range(int(start), int(end if end is not None else start + 1)))


def formula_like_block(value: str) -> bool:
    symbols = len(re.findall(r'[←→∈∪{}=τ퐴-힣]', value))
    return symbols >= 4 or bool(re.search(r'(?:^|\n)\s*Алгоритм\s*:', value, re.I))


def is_likely_table_context(value: str) -> bool:
    compact_value = compact(value)
    numbers = len(re.findall(r'(?<!\p{L})\d+(?:[,.]\d+)?', compact_value))
    return bool(
        re.search(r'(?:Модель\s+Обслуживание|Precision\s+Recall)', compact_value, re.I)
        or (re.search(r'(?:Датасет|Конфигурация|Кодировщик|Шум\s+Поиск|Судья\s+Необходим)', compact_value, re.I) and numbers >= 2)
    )


def narrative_blocks(document:dict) -> list[dict]:
    excluded=mapped_excluded_ids(document)
    scientific_ids=mapped_scientific_body_ids(document)
    bibliography={b['id'] for b in document.get('fields',{}).get('bibliographyBlocks',[])}
    contents_pages = contents_page_range(document)
    out=[]
    allowed_types={'paragraph','list'}
    for b in document.get('blocks',[]):
        text=b.get('text','')
        if scientific_ids is not None and b.get('id') not in scientific_ids:
            continue
        if b['id'] in excluded or b['id'] in bibliography or b.get('type') not in allowed_types or looks_like_contents(text) or b.get('page') in contents_pages or is_code_or_prompt(text):
            continue
        letters=re.findall(r'\p{L}',text); cyr=re.findall(r'[А-ЯЁа-яё]',text)
        if letters and len(cyr)/len(letters)>=0.28:
            out.append(b)
    return out
