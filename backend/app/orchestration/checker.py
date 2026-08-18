from __future__ import annotations

import asyncio
import copy
import os
import re
from typing import Any, Awaitable, Callable

from ..checking.consistency import apply_consistency_checks
from ..checking.deterministic import run_deterministic
from ..checking.structural import run_structural
from ..document.semantic_ranges import trim_blocks_for_element
from ..document.title import extract_best_title
from ..document.units import canonical_elements
from ..llm.client import ask_structured_json, is_fatal_provider_error, is_retryable_provider_error
from ..llm.rate_limiter import configured_rate_limits
from ..routing.rule_router import build_routing
from .candidate_checker import build_candidate_plan, execute_candidate_plan
from .abbreviation_inventory_checker import execute_abbreviation_inventory_check

ABBREVIATION_RULE_IDS = ("CORE-4-1", "CORE-4-2", "CORE-4-3", "CORE-12")
from .evidence_verifier import verify_semantic_evidence
from .fact_rules import aggregate_fact_rule, enrich_matrix, parse_candidates
from ..rules.contracts import is_fact_rule
from ..rules.registry import rules_for_profile
from ..util import empty_usage, map_is_confirmed, merge_usage, normalized_quote, unique

ABSENCE_RULES={
    'CORE-2-3':['analogs','prototype','prototype_disadvantages'],
    'CORE-15':['analogs_inside_chapter','prototype_inside_chapter','prototype_disadvantages_inside_chapter'],
    'CORE-8-2':['comparison_with_prototype_in_chapter_conclusions'],
}

# CORE-2-3 and CORE-15 inspect the same primary chapter for the same semantic
# facts. Repo-stable keeps the proven shared-fact design: extract once and reuse the verified matrix.
_SHARED_FACT_NAMES = {
    'CORE-15': {
        'analogs': 'analogs_inside_chapter',
        'prototype': 'prototype_inside_chapter',
        'prototype_disadvantages': 'prototype_disadvantages_inside_chapter',
    }
}

RULE_GUIDANCE = {
    'CORE-2-3': (
        'Смысловой критерий CORE-2-3: проверяй содержание, а не буквальные маркеры. '
        'Не требуй слов «аналог» и «прототип» как обязательных формулировок. analogs=found, если в главе названы или '
        'однозначно определены существующие решения/подходы и есть содержательное сравнение с ними. '
        'prototype=found, если одно конкретное существующее решение или baseline функционально выделено как ближайшая '
        'точка сравнения, основа, от которой отталкивается новый метод, или наиболее близкий подход — даже без слова «прототип». '
        'prototype_disadvantages=found, если для этого же решения описаны ограничения/недостатки и понятно, какое из них '
        'устраняет или обходит предлагаемое решение. Если сравниваются несколько близких решений, но нельзя уверенно выбрать '
        'одно основное, ставь prototype=ambiguous, а не not_found. Нарушение ставь только при реальном отсутствии требуемого '
        'смысла после просмотра всей назначенной главы, а не из-за отсутствия терминов «прототип»/«аналог».'
    ),
    'CORE-8-2': (
        'Смысловой критерий CORE-8-2: НЕ требуй буквальную фразу «отличающийся тем, что». '
        'Считай comparison_with_prototype_in_chapter_conclusions=found, если вывод содержательно '
        'сопоставляет разработанное решение с известным методом, классом подходов или обычной практикой: называет или однозначно '
        'идентифицирует сравниваемое решение, указывает его ограничение/отличие и описывает, чем '
        'разработанный метод это ограничение устраняет, заменяет или принципиально делает иначе. Конкретное название прототипа не обязательно, если класс известного подхода определён однозначно. Например, конструкция '
        '«алгоритм устраняет ограничение ..., присущее E-SINDy: произведение ... заменяется ...» '
        'является полноценным сравнением. Простое перечисление результатов или преимуществ без '
        'сравниваемого известного решения — not_found.'
    ),
}


def _not_checked(rule,msg): return {'ruleId':rule['id'],'status':'not_checked','severity':rule.get('severity','major'),'explanation':msg,'confidence':0,'evidence':[],'evidenceStatus':'not_required','checkedBy':'system'}
def _manual(rule,reason=None): return {'ruleId':rule['id'],'status':'not_applicable','severity':rule.get('severity','major'),'explanation':reason or 'Требуется другой артефакт или ручное наблюдение.','confidence':0,'evidence':[],'evidenceStatus':'not_required','checkedBy':'system'}


def hydrate_fields_from_confirmed_map(document:dict)->None:
    mv=document.get('map'); blocks=document.get('blocks',[])
    if not mv:return
    idx={b['id']:i for i,b in enumerate(blocks)}
    def elements(t): return canonical_elements(mv.get('elements',[]), t) if t in {'goal','tasks','defense_statements'} else [e for e in mv.get('elements',[]) if e.get('type')==t]
    def elem_blocks(t):
        out=[]
        for e in elements(t):
            s=idx.get(e.get('startBlockId')); en=idx.get(e.get('endBlockId'))
            if s is not None and en is not None and s<=en: out += trim_blocks_for_element(t,blocks[s:en+1])
        return out
    def precise(t):
        es=elements(t)
        if not es:return None
        e=es[0]; s=idx.get(e.get('startBlockId')); en=idx.get(e.get('endBlockId'))
        if s is None or en is None or s>en:return None
        source=blocks[s]; rng=blocks[s:en+1]
        if t=='title':
            found=extract_best_title(rng,blocks) or document.get('fields',{}).get('title')
            if found:return found
            label=str(e.get('label') or '').strip()
            generic_label=any(token in label.lower() for token in ('титульн', 'title page', 'название работы'))
            return {**source,'text':label} if label and not generic_label else None
        quote=(e.get('quote') or '').strip()
        return {**source,'text':quote} if quote else source
    fields=document.setdefault('fields',{})
    title=precise('title') or fields.get('title'); goal=precise('goal') or fields.get('goal')
    tasks=elem_blocks('tasks'); defense=elem_blocks('defense_statements'); bib=elem_blocks('bibliography')
    chapters=[]
    for e in elements('chapter'):
        p=idx.get(e.get('startBlockId'))
        if p is not None: chapters.append(blocks[p])
    conclusions=[]
    for e in [x for x in mv.get('elements',[]) if x.get('type') in {'chapter_conclusions','conclusion'}]:
        p=idx.get(e.get('startBlockId'))
        if p is not None: conclusions.append(blocks[p])
    fields.update({'title':title,'goal':goal,'tasks':tasks or fields.get('tasks',[]),'defenseStatements':defense or fields.get('defenseStatements',[]),'chapterHeadings':chapters,'conclusionHeadings':[ *conclusions, *fields.get('conclusionHeadings',[]) ],'bibliographyBlocks':bib or fields.get('bibliographyBlocks',[])})


def _normalize_local(routed:dict,res:dict)->dict:
    checked=0 if res.get('status')=='not_checked' else 1
    cov={'candidateCount':1,'checkedCandidateCount':checked,'packetCount':1,'checkedPacketCount':checked,'fraction':checked,'exhaustive':checked==1}
    if res.get('status')=='pass' and not routed.get('allowPass',True):
        return {**res,'status':'uncertain','explanation':(res.get('explanation','')+' '+(routed.get('reason') or 'Полная область не подтверждена.')).strip(),'coverage':{**cov,'exhaustive':False},'checkedFragments':[routed.get('strategy')]}
    return {**res,'coverage':cov,'checkedFragments':[routed.get('strategy')]}


def _message(map_value:dict,fragment:dict,rules:list[dict])->str:
    summary='\n'.join(f"{e.get('type')} | {e.get('label')} | {e.get('startBlockId')}…{e.get('endBlockId')}" for e in map_value.get('elements',[]))
    blocks='\n\n'.join(f"BLOCK {b['id']} | {b.get('location','')}"+(f" | page={b['page']}" if b.get('page') else '')+f" | type={b.get('type','paragraph')}\n{b.get('text','')}" for b in fragment.get('blocks',[]))
    chunks=[]
    for rule in rules:
        required=ABSENCE_RULES.get(rule['id']); absence=''
        if required:
            absence=f'''\nТИП ПРОВЕРКИ: FACT-FIRST / отсутствие элемента. Просмотри ВСЕ {len(fragment.get('blocks',[]))} блоков фрагмента. Главная задача — извлечь факты, а не выбрать финальный verdict: Python рассчитает его отдельно и проигнорирует твой status для этого правила. Помимо обычных полей верни absenceCheck: {{"complete": boolean, "checkedBlockCount": number, "items": [{{"name": string, "status": "found"|"not_found"|"ambiguous", "reason": "коротко", "evidence": [{{"blockId": string, "quote": string}}], "candidates": [{{"label": "кандидат только из BLOCK", "relation": "почему это возможный аналог/baseline/прототип", "evidence": [{{"blockId": string, "quote": string}}]}}]}}]}}. Обязательные элементы: {', '.join(required)}. Если есть один или несколько правдоподобных candidates, но нельзя выбрать единственный — status=ambiguous, НИКОГДА not_found. not_found означает, что после полного просмотра не найден даже правдоподобный кандидат.'''
        guidance = RULE_GUIDANCE.get(rule['id'])
        guidance_text = f"\nУТОЧНЕНИЕ ПРОВЕРКИ: {guidance}" if guidance else ''
        chunks.append(f"RULE {rule['id']}\nКатегория: {rule.get('category','')}\nТребование: {rule.get('requirement','')}\nКорректный пример: {rule.get('correctExample') or '—'}\nПример нарушения: {rule.get('incorrectExample') or '—'}{guidance_text}{absence}")
    semantic_context = str(fragment.get('semanticContext') or '').strip()
    semantic_section = f"\nSEMANTIC_CONTEXT (контекст для понимания; evidence всё равно только из BLOCK):\n{semantic_context}\n" if semantic_context else ''
    return f'''DOCUMENT_MAP:\n{summary}\n\nCHECK_FRAGMENT:\nid={fragment['id']}\nlabel={fragment['label']}\ncomplete={str(fragment.get('complete',False)).lower()}\ntotalBlocks={len(fragment.get('blocks',[]))}{semantic_section}\n{blocks}\n\nRULES:\n{'\n\n'.join(chunks)}\n\nОБЯЗАТЕЛЬНОЕ ОГРАНИЧЕНИЕ: используй только факты и названия, которые присутствуют в BLOCK или явно даны в SEMANTIC_CONTEXT. Внешние знания запрещены. Не предлагай в explanation/fix новые методы, статьи, продукты, авторов или бенчмарки, которых нет во входном тексте. Если документ не даёт основания для конкретного совета, формулируй исправление обобщённо.\n\nВерни JSON: {{"results":[{{"ruleId":"...","status":"pass|violation|uncertain|not_applicable","explanation":"...","fix":"...","evidence":[{{"blockId":"...","quote":"точная непрерывная цитата"}}],"absenceCheck":...}}]}}.'''


def _parse_evidence(value:Any,block_map:dict[str,dict])->list[dict]:
    if not isinstance(value,list):return []
    out=[]; seen=set()
    for raw in value[:20]:
        if not isinstance(raw,dict):continue
        b=block_map.get(str(raw.get('blockId','')).strip()); q=' '.join(str(raw.get('quote','')).split())
        if not b or len(q)<4 or len(q)>600 or normalized_quote(q) not in normalized_quote(b.get('text','')): continue
        key=(b['id'],normalized_quote(q))
        if key in seen:continue
        seen.add(key); item={'quote':q,'blockId':b['id'],'location':b.get('location',''),'verified':True}
        exact_start=b.get('text','').find(q)
        if exact_start>=0:
            item['start']=exact_start; item['end']=exact_start+len(q)
        if b.get('page') is not None:item['page']=b['page']
        out.append(item)
    return out


def _coverage_matrix(value:Any,fragment:dict,block_map:dict,required:list[str]|None):
    if not required or not isinstance(value,dict):return None
    try: checked=int(value.get('checkedBlockCount',0))
    except Exception:checked=0
    by={}
    for raw in value.get('items',[]) if isinstance(value.get('items'),list) else []:
        if not isinstance(raw,dict):continue
        name=str(raw.get('name','')).strip()
        if name not in required:continue
        status=raw.get('status') if raw.get('status') in {'found','not_found','ambiguous'} else 'ambiguous'
        verified_evidence=_parse_evidence(raw.get('evidence'),block_map)
        candidates=parse_candidates(raw.get('candidates'),block_map)
        reason=' '.join(str(raw.get('reason') or '').split())[:500]
        if status == 'found' and not verified_evidence and not any(candidate.get('evidence') for candidate in candidates):
            status='ambiguous'
            reason=(reason+' found→ambiguous: факт не имеет проверяемого evidence из BLOCK.').strip()
        by[name]={'name':name,'status':status,'reason':reason,'evidence':verified_evidence,'candidates':candidates}
    items=[by.get(name,{'name':name,'status':'ambiguous','reason':'','evidence':[],'candidates':[]}) for name in required]
    total=len(fragment.get('blocks',[])); complete=fragment.get('complete') is True and checked>=total and all(n in by for n in required)
    return {'fragmentId':fragment['id'],'label':fragment['label'],'complete':complete,'checkedBlocks':max(0,min(checked,total)),'totalBlocks':total,'items':items}


def _fact_item_needs_recovery(item: dict | None) -> bool:
    if not item or item.get('status') == 'not_checked' or item.get('technicalIncomplete'):
        return True
    matrices = item.get('coverageMatrix') or []
    return not matrices or not all(bool(matrix.get('complete')) for matrix in matrices)


def _fact_recovery_message(fragment: dict, rule: dict) -> str:
    required = ABSENCE_RULES.get(str(rule.get('id'))) or []
    blocks = '\n\n'.join(
        f"BLOCK {b['id']} | {b.get('location','')}"
        + (f" | page={b.get('page')}" if b.get('page') is not None else '')
        + f" | type={b.get('type','paragraph')}\n{b.get('text','')}"
        for b in fragment.get('blocks', [])
    )
    fact_rows = ','.join(
        '{"name":"'+name+'","status":"found|not_found|ambiguous","reason":"коротко","evidence":[{"blockId":"...","quote":"точная цитата"}],"candidates":[]}'
        for name in required
    )
    return f'''RECOVERY FACT-FIRST. Проверяется только одна сущность и одно правило.
Не формируй pass/violation: Python проигнорирует status. Для status=found ОБЯЗАТЕЛЬНО дай хотя бы одну точную evidence-цитату из BLOCK; found без проверяемого evidence будет автоматически понижен до ambiguous. Просмотри ВСЕ {len(fragment.get('blocks', []))} BLOCK.
Внешние знания запрещены; кандидаты и evidence только из BLOCK.

FRAGMENT: {fragment.get('label')}
SEMANTIC_CONTEXT: {fragment.get('semanticContext','')}

RULE {rule.get('id')}: {rule.get('requirement','')}
FACTS: {', '.join(required)}

{blocks}

Верни только JSON:
{{"results":[{{"ruleId":"{rule.get('id')}","status":"uncertain","explanation":"fact extraction","evidence":[],"absenceCheck":{{"complete":true,"checkedBlockCount":{len(fragment.get('blocks', []))},"items":[{fact_rows}]}}}}]}}
Если есть правдоподобный кандидат, но выбор не однозначен, используй ambiguous, не not_found.'''


def _derive_shared_fact_item(source_item: dict, target_rule_id: str) -> dict:
    item = copy.deepcopy(source_item)
    item['ruleId'] = target_rule_id
    item['checkedBy'] = f"fact-cache:{source_item.get('ruleId','CORE-2-3')}"
    name_map = _SHARED_FACT_NAMES.get(target_rule_id) or {}
    for matrix in item.get('coverageMatrix') or []:
        for cell in matrix.get('items') or []:
            cell['name'] = name_map.get(str(cell.get('name')), str(cell.get('name')))
    item['findingIds'] = [
        str(value).replace('absence:CORE-2-3:', f'absence:{target_rule_id}:')
        for value in item.get('findingIds') or []
    ]
    return item


def _parse_fragment_results(value:Any,fragment:dict,rules:list[dict])->list[dict]:
    records=value.get('results',[]) if isinstance(value,dict) and isinstance(value.get('results'),list) else []
    by={str(x.get('ruleId','')).strip():x for x in records if isinstance(x,dict)}; bmap={b['id']:b for b in fragment.get('blocks',[])}; out=[]
    for rule in rules:
        rec=by.get(rule['id'])
        if not rec:
            out.append({**_not_checked(rule,'LLM не вернула результат для правила.'),'fragmentId':fragment['id'],'checkedBy':'llm','checkedFragments':[fragment['id']],'technicalIncomplete':True});continue
        status=rec.get('status') if rec.get('status') in {'pass','violation','uncertain','not_applicable'} else 'uncertain'
        ev=_parse_evidence(rec.get('evidence'),bmap); matrix=_coverage_matrix(rec.get('absenceCheck'),fragment,bmap,ABSENCE_RULES.get(rule['id']))
        matrix=enrich_matrix(rule['id'],matrix,fragment)
        valid_abs=bool(matrix and matrix['complete'] and any(x['status']=='not_found' for x in matrix['items']))
        evidence_status='verified' if ev else 'coverage_verified' if valid_abs else 'not_required'
        if status=='violation' and not ev and not valid_abs: status='uncertain'; evidence_status='rejected'
        if status=='pass' and (not fragment.get('complete') or (matrix and not matrix['complete'])):status='uncertain'
        item={'ruleId':rule['id'],'fragmentId':fragment['id'],'status':status,'severity':rule.get('severity','major'),'explanation':str(rec.get('explanation') or 'Недостаточно данных для объяснения.'),'confidence':0,'evidence':ev,'evidenceStatus':evidence_status,'checkedBy':'llm','checkedFragments':[fragment['id']]}
        if rec.get('fix'): item['fix']=str(rec.get('fix'))
        if matrix:
            item['coverageMatrix']=[matrix]; item['findingIds']=[f"absence:{rule['id']}:{fragment['id']}:{x['name']}" for x in matrix['items'] if x['status']=='not_found']
        out.append(item)
    return out


def _dedupe_ev(items):
    seen=set();out=[]
    for x in items:
        key=(x.get('blockId'),normalized_quote(x.get('quote','')))
        if key not in seen:seen.add(key);out.append(x)
    return out


def _aggregate(rule:dict,routed:dict,items:list[dict])->dict:
    if is_fact_rule(rule.get('id','')):
        return aggregate_fact_rule(rule,routed,items)
    checked=[x for x in items if x.get('status')!='not_checked']; count=len(routed.get('fragmentIds',[]));
    cov={'candidateCount':count,'checkedCandidateCount':len(checked),'packetCount':count,'checkedPacketCount':len(checked),'fraction':len(checked)/count if count else 0,'exhaustive':bool(routed.get('exhaustive') and len(checked)==count)}
    matrices=[row for x in checked for row in (x.get('coverageMatrix') or [])]
    violations=[x for x in checked if x.get('status')=='violation' and (x.get('evidence') or any(row.get('complete') and any(c.get('status')=='not_found' for c in row.get('items',[])) for row in x.get('coverageMatrix') or []))]
    if violations:
        out={'ruleId':rule['id'],'status':'violation','severity':rule.get('severity','major'),'explanation':' '.join(unique([x.get('explanation','') for x in violations])),'confidence':0,'evidence':_dedupe_ev([e for x in violations for e in x.get('evidence',[])]),'evidenceStatus':'verified' if any(x.get('evidence') for x in violations) else 'coverage_verified','checkedBy':'llm','coverage':cov,'checkedFragments':unique([x.get('fragmentId','') for x in items]),'findingIds':unique([f for x in violations for f in x.get('findingIds',[])])}
        fix=next((x.get('fix') for x in violations if x.get('fix')),None)
        if fix:out['fix']=fix
        if matrices:out['coverageMatrix']=matrices
        return out
    if not checked:
        return {
            **_not_checked(rule,'Не удалось проверить обязательные фрагменты: '+', '.join(routed.get('fragmentIds',[]))+'.'),
            'coverage':cov,
            'checkedFragments':routed.get('fragmentIds',[]),
            **({'technicalIncomplete': True, 'checkedBy': 'llm'} if any(x.get('technicalIncomplete') for x in items) else {}),
        }
    all_pass=all(x.get('status')=='pass' for x in checked); matrices_complete=not matrices or all(r.get('complete') and all(c.get('status')=='found' for c in r.get('items',[])) for r in matrices)
    if all_pass and cov['exhaustive'] and routed.get('allowPass',True) and matrices_complete:
        out={'ruleId':rule['id'],'status':'pass','severity':rule.get('severity','major'),'explanation':f'Проверена вся назначенная область ({len(checked)} фрагм.); подтверждённых нарушений не найдено.','confidence':0,'evidence':[],'evidenceStatus':'coverage_verified' if matrices else 'not_required','checkedBy':'llm','coverage':cov,'checkedFragments':unique([x.get('fragmentId','') for x in items])}
        if matrices:out['coverageMatrix']=matrices
        return out
    details=' '.join(unique([x.get('explanation','') for x in checked])); reason=routed.get('reason') if not routed.get('allowPass',True) else ('Часть ответов или ячеек матрицы осталась неопределённой.' if cov['exhaustive'] else 'Проверена не вся обязательная область правила.')
    out={'ruleId':rule['id'],'status':'uncertain','severity':rule.get('severity','major'),'explanation':(details+' '+(reason or '')).strip(),'confidence':0,'evidence':_dedupe_ev([e for x in checked for e in x.get('evidence',[])]),'evidenceStatus':'rejected' if any(x.get('evidenceStatus')=='rejected' for x in checked) else 'verified' if any(x.get('evidence') for x in checked) else 'coverage_verified' if matrices else 'not_required','checkedBy':'llm','coverage':cov,'checkedFragments':unique([x.get('fragmentId','') for x in items]),'findingIds':unique([f for x in checked for f in x.get('findingIds',[])])}
    if any(x.get('technicalIncomplete') for x in items): out['technicalIncomplete']=True
    if matrices:out['coverageMatrix']=matrices
    return out


_RULE_ID_IN_WARNING = re.compile(r'\b(?:CORE|SOFT)-[A-Za-z0-9-]+\b')
_TRANSIENT_WARNING = re.compile(
    r'(?:не проверен|повтор для пропущенных правил|Fact recovery|без итогового ответа|recovery)',
    re.I,
)


def _prune_resolved_warnings(warnings: list[str], results: list[dict]) -> list[str]:
    """Drop provisional retry/recovery warnings after the final rule result is healthy."""
    unresolved = {
        str(item.get('ruleId')) for item in results
        if item.get('technicalIncomplete')
        or (item.get('status') == 'not_checked' and str(item.get('checkedBy') or '') in {'llm', 'fact-engine+llm-extractor'})
    }
    cleaned: list[str] = []
    for warning in warnings:
        text = str(warning)
        ids = set(_RULE_ID_IN_WARNING.findall(text))
        if _TRANSIENT_WARNING.search(text):
            if ids and not (ids & unresolved):
                continue
            if not ids and not unresolved and re.search(r'не проверен|recovery|без итогового ответа', text, re.I):
                continue
        cleaned.append(text)
    return unique(cleaned)


async def check_document(*,document:dict,provider:str,model:str,prompt:str,profile:str,additional_criteria:str,only_rule_ids:list[str]|None=None,on_progress:Callable[[int,int,str],Awaitable[None]|None]|None=None,is_cancelled:Callable[[],Awaitable[bool]|bool]|None=None)->dict:
    if not map_is_confirmed(document.get('map')):
        raise ValueError('Структура документа не подтверждена.')
    hydrate_fields_from_confirmed_map(document)
    all_rules=rules_for_profile(profile,additional_criteria)
    selected=set(only_rule_ids or [])
    rules=[r for r in all_rules if not selected or r['id'] in selected]
    warnings=[]
    usage=empty_usage()
    routing=await build_routing(document=document,map_value=document['map'],rules=rules)
    merge_usage(usage,routing.get('usage'))

    local={}
    llm_routed=[]
    candidate_routed=[]
    abbreviation_routed=[]
    for routed in routing['routed']:
        st=routed['strategy']; rule=routed['rule']
        if rule['id'] in ABBREVIATION_RULE_IDS:
            # Experimental 3.9.3 path: abbreviation rules are intercepted
            # before ordinary semantic routing. Python owns candidate discovery/scope;
            # one compact LLM inventory audit owns the CORE-4 verdicts.
            abbreviation_routed.append(routed)
        elif st=='deterministic':
            detector_rule = {**rule, **({'detectorId': routed.get('detectorId')} if routed.get('detectorId') else {})}
            local[rule['id']]=_normalize_local(routed,run_deterministic(detector_rule,document))
        elif st=='structural':
            local[rule['id']]=_normalize_local(routed,run_structural(rule,document,routing.get('fragments',[])))
        elif st=='manual':
            local[rule['id']]=_manual(rule,routed.get('reason'))
        elif st=='unavailable':
            local[rule['id']]=_not_checked(rule,routed.get('reason') or 'Надёжная автоматическая проверка недоступна.')
        elif st=='candidate':
            candidate_routed.append(routed)
        else:
            llm_routed.append(routed)

    fragment_by={x['id']:x for x in routing['fragments']}
    assignments={}
    llm_rule_ids={str(item['rule'].get('id')) for item in llm_routed}
    shared_fact_cache_enabled='CORE-2-3' in llm_rule_ids and 'CORE-15' in llm_rule_ids
    fact_cache_hits=0
    fact_recovery_requests=0
    for routed in llm_routed:
        if shared_fact_cache_enabled and routed['rule'].get('id') == 'CORE-15':
            continue
        for fid in routed.get('fragmentIds',[]):
            assignments.setdefault(fid,[]).append(routed['rule'])

    max_rules=max(1,int(os.getenv('RULES_PER_FRAGMENT_REQUEST','12') or 12))
    requests=[]
    for fid,frules in assignments.items():
        for i in range(0,len(frules),max_rules):
            requests.append((fid,frules[i:i+max_rules]))

    candidate_plan=build_candidate_plan(document,candidate_routed)
    abbreviation_rules=[item['rule'] for item in abbreviation_routed]
    total_requests=max(1,len(requests)+len(candidate_plan['requests'])+(1 if abbreviation_rules else 0))
    completed_total=0
    progress_lock=asyncio.Lock()

    async def progress_step(label:str)->None:
        nonlocal completed_total
        async with progress_lock:
            completed_total+=1
            current=completed_total
        if on_progress:
            value=on_progress(current,total_requests,label)
            if asyncio.iscoroutine(value):
                await value

    # Candidate and semantic packets are independent. Run them concurrently so
    # candidate-first does not create a second sequential LLM phase. Both paths
    # still share the provider rate limiter and therefore respect RPM/concurrency.
    candidate_task=asyncio.create_task(execute_candidate_plan(
        plan=candidate_plan,
        provider=provider,
        model=model,
        usage=usage,
        on_request_done=lambda: progress_step(f'Кандидаты {completed_total + 1}/{total_requests}'),
        is_cancelled=is_cancelled,
    ))

    async def abbreviation_runner():
        if not abbreviation_rules:
            return [], empty_usage(), []
        local_results, local_usage, local_warnings = await execute_abbreviation_inventory_check(
            document=document,
            rules=abbreviation_rules,
            provider=provider,
            model=model,
            system_prompt=prompt,
        )
        await progress_step(f'Аббревиатуры {completed_total + 1}/{total_requests}')
        return local_results, local_usage, local_warnings

    abbreviation_task=asyncio.create_task(abbreviation_runner())

    raw=[]
    packet_attempts=max(1,int(os.getenv('CHECK_PACKET_MAX_ATTEMPTS','2') or 2))
    fatal=None
    next_index=0
    lock=asyncio.Lock()

    async def cancelled():
        if not is_cancelled:return False
        value=is_cancelled()
        return await value if asyncio.iscoroutine(value) else bool(value)

    async def worker():
        nonlocal next_index,fatal
        while fatal is None:
            if await cancelled():return
            async with lock:
                if next_index>=len(requests):return
                current=requests[next_index];next_index+=1
            fid,frules=current
            fragment=fragment_by.get(fid)
            if not fragment:
                await progress_step(f'Фрагменты {completed_total + 1}/{total_requests}')
                continue
            error=None
            for attempt in range(1,packet_attempts+1):
                try:
                    response=await ask_structured_json(
                        provider=provider,model=model,system_prompt=prompt,
                        user_message=_message(document['map'],fragment,frules),
                        operation='check',packets=1,candidates=len(frules),
                    )
                    merge_usage(usage,response['usage'])
                    parsed = _parse_fragment_results(response['value'],fragment,frules)
                    returned_ids = {
                        str(item.get('ruleId','')).strip()
                        for item in (response.get('value') or {}).get('results',[])
                        if isinstance(item,dict)
                    } if isinstance(response.get('value'),dict) else set()
                    missing_rules = [rule for rule in frules if rule['id'] not in returned_ids]
                    if missing_rules:
                        # Models occasionally omit one item from an otherwise valid
                        # batched JSON response. Retry only the omitted rule(s) once
                        # instead of discarding the successful work for this fragment.
                        try:
                            followup = await ask_structured_json(
                                provider=provider,model=model,system_prompt=prompt,
                                user_message=_message(document['map'],fragment,missing_rules),
                                operation='check',packets=1,candidates=len(missing_rules),
                            )
                            merge_usage(usage,followup['usage'])
                            retry_parsed = _parse_fragment_results(followup['value'],fragment,missing_rules)
                            retry_by = {item['ruleId']: item for item in retry_parsed}
                            parsed = [
                                retry_by.get(item['ruleId'], item)
                                if item['ruleId'] in {rule['id'] for rule in missing_rules}
                                else item
                                for item in parsed
                            ]
                        except Exception as followup_error:
                            merge_usage(usage,getattr(followup_error,'llm_usage',None))
                            if is_fatal_provider_error(followup_error):
                                raise
                            warnings.append(
                                f"Фрагмент «{fragment['label']}»: повтор для пропущенных правил не удался: {followup_error}"
                            )
                    raw.extend(parsed)
                    error=None
                    break
                except Exception as exc:
                    error=exc
                    merge_usage(usage,getattr(exc,'llm_usage',None))
                    if is_fatal_provider_error(exc):
                        fatal=exc
                        break
                    # ask_structured_json already exhausted its transport retry
                    # budget. Do not immediately resend the same expensive
                    # fragment in this outer packet loop; fact rules have their
                    # own targeted entity recovery below.
                    if is_retryable_provider_error(exc):
                        break
                    if attempt<packet_attempts:
                        await asyncio.sleep(.6*attempt)
            if error and fatal is None:
                warnings.append(f"Фрагмент «{fragment['label']}» не проверен: {error}")
                for rule in frules:
                    raw.append({**_not_checked(rule,str(error)),'fragmentId':fid,'checkedBy':'llm','checkedFragments':[fid],'technicalIncomplete':True})
            await progress_step(f'Фрагменты {completed_total + 1}/{total_requests}')

    workers=min(configured_rate_limits(provider)['maxConcurrent'],max(1,len(requests)))
    if requests:
        await asyncio.gather(*(worker() for _ in range(workers)))

    # Entity-level fact recovery: keep successful fragments, and retry only the
    # single incomplete statement↔chapter fact packet. This is deliberately
    # separate from the normal packet retry so one malformed response cannot make
    # an otherwise complete CORE-2-3/CORE-15 rule technically incomplete.
    if fatal is None:
        recovery_targets=[]
        for routed in llm_routed:
            rule=routed['rule']; rid=str(rule.get('id'))
            if shared_fact_cache_enabled and rid == 'CORE-15':
                continue
            if not is_fact_rule(rid):
                continue
            for fid in routed.get('fragmentIds',[]):
                current=next((x for x in reversed(raw) if x.get('ruleId')==rid and x.get('fragmentId')==fid),None)
                if _fact_item_needs_recovery(current):
                    fragment=fragment_by.get(fid)
                    if fragment:
                        recovery_targets.append((rule,fragment))

        recovery_attempts=max(1,int(os.getenv('FACT_ENTITY_RECOVERY_ATTEMPTS','2') or 2))
        recovery_lock=asyncio.Lock()
        recovery_index=0

        async def fact_recovery_worker():
            nonlocal recovery_index,fact_recovery_requests,fatal
            while fatal is None:
                async with recovery_lock:
                    if recovery_index>=len(recovery_targets): return
                    rule,fragment=recovery_targets[recovery_index]; recovery_index+=1
                last_error=None
                for attempt in range(1,recovery_attempts+1):
                    try:
                        response=await ask_structured_json(
                            provider=provider,model=model,system_prompt=prompt,
                            user_message=_fact_recovery_message(fragment,rule),
                            operation='check',packets=1,candidates=1,max_completion_tokens=3000,
                        )
                        fact_recovery_requests+=1
                        merge_usage(usage,response.get('usage'))
                        parsed=_parse_fragment_results(response.get('value'),fragment,[rule])
                        candidate=parsed[0] if parsed else None
                        if not _fact_item_needs_recovery(candidate):
                            raw[:]=[x for x in raw if not (x.get('ruleId')==rule['id'] and x.get('fragmentId')==fragment['id'])]
                            raw.append(candidate)
                            last_error=None
                            break
                        last_error=RuntimeError('recovery вернул неполную fact matrix')
                    except Exception as exc:
                        fact_recovery_requests+=1
                        last_error=exc
                        merge_usage(usage,getattr(exc,'llm_usage',None))
                        if is_fatal_provider_error(exc):
                            fatal=exc
                            return
                    if attempt<recovery_attempts:
                        await asyncio.sleep(.4*attempt)
                if last_error is not None:
                    warnings.append(f"Fact recovery «{fragment['label']}» / {rule['id']} не завершён: {last_error}")

        recovery_workers=min(configured_rate_limits(provider)['maxConcurrent'],max(1,len(recovery_targets)))
        if recovery_targets:
            await asyncio.gather(*(fact_recovery_worker() for _ in range(recovery_workers)))

        # CORE-15 consumes the exact CORE-2-3 facts from the same primary chapter.
        # No second LLM call is allowed when both rules are present.
        if shared_fact_cache_enabled:
            target_routed=next((x for x in llm_routed if x['rule'].get('id')=='CORE-15'),None)
            if target_routed:
                for fid in target_routed.get('fragmentIds',[]):
                    source=next((x for x in reversed(raw) if x.get('ruleId')=='CORE-2-3' and x.get('fragmentId')==fid),None)
                    if source:
                        raw.append(_derive_shared_fact_item(source,'CORE-15'))
                        fact_cache_hits+=1

    if fatal is not None:
        if not candidate_task.done():
            candidate_task.cancel()
        if not abbreviation_task.done():
            abbreviation_task.cancel()
        raise fatal
    candidate_results,candidate_warnings=await candidate_task
    warnings.extend(candidate_warnings)
    for item in candidate_results:
        local[item['ruleId']]=item

    abbreviation_results, abbreviation_usage, abbreviation_warnings = await abbreviation_task
    merge_usage(usage, abbreviation_usage)
    warnings.extend(abbreviation_warnings)
    for item in abbreviation_results:
        local[item['ruleId']] = item

    routed_by={x['rule']['id']:x for x in routing['routed']}
    initial=[]
    for rule in rules:
        if rule['id'] in local:
            initial.append(local[rule['id']])
            continue
        routed=routed_by.get(rule['id'])
        if not routed or not routed.get('fragmentIds'):
            initial.append(_not_checked(rule,(routed or {}).get('reason') or 'Для правила не найден обязательный смысловой фрагмент.'))
            continue
        initial.append(_aggregate(rule,routed,[x for x in raw if x.get('ruleId')==rule['id']]))
    verified, verifier_usage, verifier_warnings = await verify_semantic_evidence(
        document=document, rules=rules, results=initial, provider=provider, model=model, system_prompt=prompt,
    )
    merge_usage(usage, verifier_usage)
    warnings.extend(verifier_warnings)
    results=apply_consistency_checks(verified)
    warnings=_prune_resolved_warnings(warnings,results)
    return {
        'rules':all_rules,
        'results':results,
        'warnings':warnings,
        'llmUsage':usage,
        'routing':{
            'strategy':routing['strategy'],
            'fragments':len(routing['fragments']),
            # plannedCheckRequests is the logical first-pass plan. physicalRequests
            # includes targeted recovery and evidence critics and is therefore the
            # truthful network-request metric for production diagnostics.
            'plannedCheckRequests':len(requests)+len(candidate_plan['requests'])+(1 if abbreviation_rules else 0),
            'checkRequests':int(usage.get('requests',0)),
            'physicalRequests':int(usage.get('requests',0)),
            'semanticRequests':len(requests),
            'candidateRequests':len(candidate_plan['requests']),
            'abbreviationAuditRequests':int(abbreviation_usage.get('requests',0)),
            'abbreviationMode':'llm-inventory',
            'abbreviationPhysicalRequests':int(abbreviation_usage.get('requests',0)),
            'abbreviationCandidateCount':int(abbreviation_usage.get('abbreviationCandidateCount',0)),
            'abbreviationResolvedCandidates':int(abbreviation_usage.get('abbreviationResolvedCandidates',0)),
            'abbreviationUnresolvedCandidates':int(abbreviation_usage.get('abbreviationUnresolvedCandidates',0)),
            'abbreviationRecoveryRequests':int(abbreviation_usage.get('abbreviationRecoveryRequests',0)),
            'evidenceVerifierRequests':int(verifier_usage.get('requests',0)),
            'factRecoveryRequests':fact_recovery_requests,
            'factCacheHits':fact_cache_hits,
            'candidateFamilies':len(candidate_plan['rulesByFamily']),
            'explicitRules':routing['explicitRules'],
            'fallbackRules':routing['fallbackRules'],
        },
    }
