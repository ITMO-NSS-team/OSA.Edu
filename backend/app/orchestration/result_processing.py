from __future__ import annotations

import copy
import re
from typing import Any

from ..domain.models import RuleResultModel
from ..rules.contracts import fact_items, is_fact_rule
from ..rules.manifest import manifest_entry
from ..util import normalized_quote, unique
from .fact_rules import aggregate_fact_rule, enrich_matrix, parse_candidates


def _validated_result(value: dict) -> dict:
    return RuleResultModel.model_validate(value).model_dump(exclude_none=True)

def _not_checked(rule,msg): return _validated_result({'ruleId':rule['id'],'status':'not_checked','severity':rule.get('severity','major'),'explanation':msg,'confidence':0,'evidence':[],'evidenceStatus':'not_required','checkedBy':'system'})


def _manual(rule,reason=None): return _validated_result({'ruleId':rule['id'],'status':'not_applicable','severity':rule.get('severity','major'),'explanation':reason or 'Требуется другой артефакт или ручное наблюдение.','confidence':0,'evidence':[],'evidenceStatus':'not_required','checkedBy':'system'})


def _normalize_local(routed:dict,res:dict)->dict:
    checked=0 if res.get('status')=='not_checked' else 1
    cov={'candidateCount':1,'checkedCandidateCount':checked,'packetCount':1,'checkedPacketCount':checked,'fraction':checked,'exhaustive':checked==1}
    if res.get('status')=='pass' and not routed.get('allowPass',True):
        return {**res,'status':'uncertain','explanation':(res.get('explanation','')+' '+(routed.get('reason') or 'Полная область не подтверждена.')).strip(),'coverage':{**cov,'exhaustive':False},'checkedFragments':[routed.get('strategy')]}
    return {**res,'coverage':cov,'checkedFragments':[routed.get('strategy')]}


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


def _derive_shared_fact_item(source_item: dict, target_rule_id: str) -> dict:
    item = copy.deepcopy(source_item)
    item['ruleId'] = target_rule_id
    item['checkedBy'] = f"fact-cache:{source_item.get('ruleId','source')}"
    name_map = (manifest_entry(target_rule_id).engine.factNameMap if manifest_entry(target_rule_id) else {}) or {}
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
        ev=_parse_evidence(rec.get('evidence'),bmap); matrix=_coverage_matrix(rec.get('absenceCheck'),fragment,bmap,fact_items(rule['id']))
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
