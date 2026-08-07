from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..checking.consistency import apply_consistency_checks
from ..checking.deterministic import run_deterministic
from ..checking.structural import run_structural
from ..document.semantic_ranges import trim_blocks_for_element
from ..document.title import extract_best_title
from ..llm.client import ask_structured_json, is_fatal_provider_error
from ..llm.rate_limiter import configured_rate_limits
from ..routing.rule_router import build_routing
from .candidate_checker import build_candidate_plan, execute_candidate_plan
from ..rules.registry import rules_for_profile
from ..util import empty_usage, merge_usage, normalized_quote, unique

ABSENCE_RULES={
    'CORE-2-3':['analogs','prototype','prototype_disadvantages'],
    'CORE-15':['analogs_inside_chapter','prototype_inside_chapter','prototype_disadvantages_inside_chapter'],
    'CORE-8-2':['comparison_with_prototype_in_chapter_conclusions'],
}


def _not_checked(rule,msg): return {'ruleId':rule['id'],'status':'not_checked','severity':rule.get('severity','major'),'explanation':msg,'confidence':0,'evidence':[],'evidenceStatus':'not_required','checkedBy':'system'}
def _manual(rule,reason=None): return {'ruleId':rule['id'],'status':'not_applicable','severity':rule.get('severity','major'),'explanation':reason or 'Требуется другой артефакт или ручное наблюдение.','confidence':0,'evidence':[],'evidenceStatus':'not_required','checkedBy':'system'}


def hydrate_fields_from_confirmed_map(document:dict)->None:
    mv=document.get('map'); blocks=document.get('blocks',[])
    if not mv:return
    idx={b['id']:i for i,b in enumerate(blocks)}
    def elements(t): return [e for e in mv.get('elements',[]) if e.get('type')==t]
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
        if t=='title': return extract_best_title(rng,blocks) or document.get('fields',{}).get('title')
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
    blocks='\n\n'.join(f"BLOCK {b['id']} | {b.get('location','')}"+(f" | page={b['page']}" if b.get('page') else '')+f"\n{b.get('text','')}" for b in fragment.get('blocks',[]))
    chunks=[]
    for rule in rules:
        required=ABSENCE_RULES.get(rule['id']); absence=''
        if required:
            absence=f'''\nТИП ПРОВЕРКИ: отсутствие элемента. Просмотри ВСЕ {len(fragment.get('blocks',[]))} блоков фрагмента. Помимо обычных полей верни absenceCheck: {{"complete": boolean, "checkedBlockCount": number, "items": [{{"name": string, "status": "found"|"not_found"|"ambiguous", "evidence": [{{"blockId": string, "quote": string}}]}}]}}. Обязательные элементы: {', '.join(required)}. Статус violation допускается без цитаты только при complete=true и полном покрытии.'''
        chunks.append(f"RULE {rule['id']}\nКатегория: {rule.get('category','')}\nТребование: {rule.get('requirement','')}\nКорректный пример: {rule.get('correctExample') or '—'}\nПример нарушения: {rule.get('incorrectExample') or '—'}{absence}")
    return f'''DOCUMENT_MAP:\n{summary}\n\nCHECK_FRAGMENT:\nid={fragment['id']}\nlabel={fragment['label']}\ncomplete={str(fragment.get('complete',False)).lower()}\ntotalBlocks={len(fragment.get('blocks',[]))}\n\n{blocks}\n\nRULES:\n{'\n\n'.join(chunks)}\n\nВерни JSON: {{"results":[{{"ruleId":"...","status":"pass|violation|uncertain|not_applicable","explanation":"...","fix":"...","evidence":[{{"blockId":"...","quote":"точная непрерывная цитата"}}],"absenceCheck":...}}]}}.'''


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
        by[name]={'name':name,'status':status,'evidence':_parse_evidence(raw.get('evidence'),block_map)}
    items=[by.get(name,{'name':name,'status':'ambiguous','evidence':[]}) for name in required]
    total=len(fragment.get('blocks',[])); complete=value.get('complete') is True and fragment.get('complete') is True and checked>=total and all(n in by for n in required)
    return {'fragmentId':fragment['id'],'label':fragment['label'],'complete':complete,'checkedBlocks':max(0,min(checked,total)),'totalBlocks':total,'items':items}


def _parse_fragment_results(value:Any,fragment:dict,rules:list[dict])->list[dict]:
    records=value.get('results',[]) if isinstance(value,dict) and isinstance(value.get('results'),list) else []
    by={str(x.get('ruleId','')).strip():x for x in records if isinstance(x,dict)}; bmap={b['id']:b for b in fragment.get('blocks',[])}; out=[]
    for rule in rules:
        rec=by.get(rule['id'])
        if not rec:
            out.append({**_not_checked(rule,'LLM не вернула результат для правила.'),'fragmentId':fragment['id'],'checkedBy':'llm','checkedFragments':[fragment['id']]});continue
        status=rec.get('status') if rec.get('status') in {'pass','violation','uncertain','not_applicable'} else 'uncertain'
        ev=_parse_evidence(rec.get('evidence'),bmap); matrix=_coverage_matrix(rec.get('absenceCheck'),fragment,bmap,ABSENCE_RULES.get(rule['id']))
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
    if not checked:return {**_not_checked(rule,'Не удалось проверить обязательные фрагменты: '+', '.join(routed.get('fragmentIds',[]))+'.'),'coverage':cov,'checkedFragments':routed.get('fragmentIds',[])}
    all_pass=all(x.get('status')=='pass' for x in checked); matrices_complete=not matrices or all(r.get('complete') and all(c.get('status')=='found' for c in r.get('items',[])) for r in matrices)
    if all_pass and cov['exhaustive'] and routed.get('allowPass',True) and matrices_complete:
        out={'ruleId':rule['id'],'status':'pass','severity':rule.get('severity','major'),'explanation':f'Проверена вся назначенная область ({len(checked)} фрагм.); подтверждённых нарушений не найдено.','confidence':0,'evidence':[],'evidenceStatus':'coverage_verified' if matrices else 'not_required','checkedBy':'llm','coverage':cov,'checkedFragments':unique([x.get('fragmentId','') for x in items])}
        if matrices:out['coverageMatrix']=matrices
        return out
    details=' '.join(unique([x.get('explanation','') for x in checked])); reason=routed.get('reason') if not routed.get('allowPass',True) else ('Часть ответов или ячеек матрицы осталась неопределённой.' if cov['exhaustive'] else 'Проверена не вся обязательная область правила.')
    out={'ruleId':rule['id'],'status':'uncertain','severity':rule.get('severity','major'),'explanation':(details+' '+(reason or '')).strip(),'confidence':0,'evidence':_dedupe_ev([e for x in checked for e in x.get('evidence',[])]),'evidenceStatus':'rejected' if any(x.get('evidenceStatus')=='rejected' for x in checked) else 'verified' if any(x.get('evidence') for x in checked) else 'coverage_verified' if matrices else 'not_required','checkedBy':'llm','coverage':cov,'checkedFragments':unique([x.get('fragmentId','') for x in items]),'findingIds':unique([f for x in checked for f in x.get('findingIds',[])])}
    if matrices:out['coverageMatrix']=matrices
    return out


async def check_document(*,document:dict,provider:str,model:str,prompt:str,profile:str,additional_criteria:str,only_rule_ids:list[str]|None=None,on_progress:Callable[[int,int,str],Awaitable[None]|None]|None=None,is_cancelled:Callable[[],Awaitable[bool]|bool]|None=None)->dict:
    if not (document.get('map') or {}).get('review',{}).get('confirmedByUser'):
        raise ValueError('Структура документа не подтверждена пользователем.')
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
    for routed in routing['routed']:
        st=routed['strategy']; rule=routed['rule']
        if st=='deterministic':
            detector_rule = {**rule, **({'detectorId': routed.get('detectorId')} if routed.get('detectorId') else {})}
            local[rule['id']]=_normalize_local(routed,run_deterministic(detector_rule,document))
        elif st=='structural':
            local[rule['id']]=_normalize_local(routed,run_structural(rule,document))
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
    for routed in llm_routed:
        for fid in routed.get('fragmentIds',[]):
            assignments.setdefault(fid,[]).append(routed['rule'])

    import os
    max_rules=max(1,int(os.getenv('RULES_PER_FRAGMENT_REQUEST','12') or 12))
    requests=[]
    for fid,frules in assignments.items():
        for i in range(0,len(frules),max_rules):
            requests.append((fid,frules[i:i+max_rules]))

    candidate_plan=build_candidate_plan(document,candidate_routed)
    total_requests=max(1,len(requests)+len(candidate_plan['requests']))
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

    candidate_results,candidate_warnings=await execute_candidate_plan(
        plan=candidate_plan,
        provider=provider,
        model=model,
        usage=usage,
        on_request_done=lambda: progress_step(f'Кандидаты {completed_total + 1}/{total_requests}'),
        is_cancelled=is_cancelled,
    )
    warnings.extend(candidate_warnings)
    for item in candidate_results:
        local[item['ruleId']]=item

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
                    raw.extend(_parse_fragment_results(response['value'],fragment,frules))
                    error=None
                    break
                except Exception as exc:
                    error=exc
                    merge_usage(usage,getattr(exc,'llm_usage',None))
                    if is_fatal_provider_error(exc):
                        fatal=exc
                        break
                    if attempt<packet_attempts:
                        await asyncio.sleep(.6*attempt)
            if error and fatal is None:
                warnings.append(f"Фрагмент «{fragment['label']}» не проверен: {error}")
                for rule in frules:
                    raw.append({**_not_checked(rule,str(error)),'fragmentId':fid,'checkedBy':'llm','checkedFragments':[fid]})
            await progress_step(f'Фрагменты {completed_total + 1}/{total_requests}')

    workers=min(configured_rate_limits(provider)['maxConcurrent'],max(1,len(requests)))
    if requests:
        await asyncio.gather(*(worker() for _ in range(workers)))
    if fatal is not None:
        raise fatal

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
    results=apply_consistency_checks(initial)
    return {
        'rules':all_rules,
        'results':results,
        'warnings':warnings,
        'llmUsage':usage,
        'routing':{
            'strategy':routing['strategy'],
            'fragments':len(routing['fragments']),
            'checkRequests':len(requests)+len(candidate_plan['requests']),
            'semanticRequests':len(requests),
            'candidateRequests':len(candidate_plan['requests']),
            'candidateFamilies':len(candidate_plan['rulesByFamily']),
            'explicitRules':routing['explicitRules'],
            'fallbackRules':routing['fallbackRules'],
        },
    }
