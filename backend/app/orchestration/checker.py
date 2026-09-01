from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable

from ..checking.consistency import apply_consistency_checks
from ..checking.deterministic import run_deterministic
from ..checking.structural import run_structural
from ..document.semantic_model import hydrate_legacy_fields
from ..document.fact_store import build_document_fact_store
from ..domain.models import RuleResultModel
from ..llm.client import ask_structured_json, is_fatal_provider_error, is_retryable_provider_error
from ..llm.rate_limiter import configured_rate_limits
from ..routing.rule_router import build_routing
from ..rules.contracts import is_fact_rule
from ..rules.registry import rules_for_profile
from ..util import empty_usage, map_is_confirmed, merge_usage
from .abbreviation_inventory_checker import execute_abbreviation_inventory_check
from .candidate_checker import build_candidate_plan, execute_candidate_plan
from .evidence_verifier import verify_semantic_evidence
from .result_processing import (
    _aggregate, _coverage_matrix, _derive_shared_fact_item, _fact_item_needs_recovery,
    _manual, _normalize_local, _not_checked, _parse_evidence, _parse_fragment_results,
    _prune_resolved_warnings,
)
from .semantic_packets import ABSENCE_RULES, RULE_GUIDANCE, _fact_recovery_message, _message
from .verdict_contract import enforce_verdict_contracts, technical_rule_result

# Compatibility alias. Canonical direction is Document Map → legacy fields.
hydrate_fields_from_confirmed_map = hydrate_legacy_fields

async def check_document(*,document:dict,provider:str,model:str,prompt:str,profile:str,additional_criteria:str,only_rule_ids:list[str]|None=None,on_progress:Callable[[int,int,str],Awaitable[None]|None]|None=None,is_cancelled:Callable[[],Awaitable[bool]|bool]|None=None)->dict:
    if not map_is_confirmed(document.get('map')):
        raise ValueError('Структура документа не подтверждена.')
    hydrate_legacy_fields(document)
    fact_store=build_document_fact_store(document)
    document['factStore']=fact_store
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
        if rule.get('engineKind') == 'abbreviation_fact_map':
            # Abbreviation rules are intercepted by the shared fact-map stage
            # before ordinary semantic routing. Python owns candidate discovery/scope;
            # one compact LLM inventory audit owns the CORE-4 verdicts.
            abbreviation_routed.append(routed)
        elif st=='deterministic':
            detector_rule = {**rule, **({'detectorId': routed.get('detectorId')} if routed.get('detectorId') else {})}
            try:
                local[rule['id']]=_normalize_local(routed,run_deterministic(detector_rule,document))
            except Exception as exc:
                warnings.append(f"{rule['id']}: deterministic checker завершился ошибкой: {exc}")
                local[rule['id']]=technical_rule_result(rule,'deterministic',exc)
        elif st=='structural':
            try:
                local[rule['id']]=_normalize_local(routed,run_structural(rule,document,routing.get('fragments',[])))
            except Exception as exc:
                warnings.append(f"{rule['id']}: structural checker завершился ошибкой: {exc}")
                local[rule['id']]=technical_rule_result(rule,'structural',exc)
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
    shared_fact_targets={
        str(item['rule'].get('id')): str(item['rule'].get('sharedFactsFrom'))
        for item in llm_routed
        if item['rule'].get('sharedFactsFrom') and str(item['rule'].get('sharedFactsFrom')) in llm_rule_ids
    }
    fact_cache_hits=0
    fact_recovery_requests=0
    for routed in llm_routed:
        if str(routed['rule'].get('id')) in shared_fact_targets:
            continue
        for fid in routed.get('fragmentIds',[]):
            assignments.setdefault(fid,[]).append(routed['rule'])

    max_rules=max(1,int(os.getenv('RULES_PER_FRAGMENT_REQUEST','4') or 4))
    requests=[]
    for fid,frules in assignments.items():
        for i in range(0,len(frules),max_rules):
            requests.append((fid,frules[i:i+max_rules]))

    try:
        candidate_plan=build_candidate_plan(document,candidate_routed)
    except Exception as exc:
        warnings.append(f"Candidate plan не построен: {exc}")
        candidate_plan={'requests': [], 'rulesByFamily': {}}
        for item in candidate_routed:
            rule=item['rule']
            local[rule['id']]=technical_rule_result(rule,'candidate_plan',exc)
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
        on_request_done=lambda: progress_step(f'Проверяем потенциальные нарушения: {completed_total + 1}/{total_requests}'),
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
            fact_store=fact_store,
        )
        await progress_step(f'Проверяем сокращения и обозначения: {completed_total + 1}/{total_requests}')
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
                await progress_step(f'Проверяем правила по разделам: {completed_total + 1}/{total_requests}')
                continue
            error=None
            for attempt in range(1,packet_attempts+1):
                try:
                    response=await ask_structured_json(
                        provider=provider,model=model,system_prompt=prompt,
                        user_message=_message(document['map'],fragment,frules,fact_store),
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
                                user_message=_message(document['map'],fragment,missing_rules,fact_store),
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
            await progress_step(f'Проверяем правила по разделам: {completed_total + 1}/{total_requests}')

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
            if rid in shared_fact_targets:
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
                            user_message=_fact_recovery_message(fragment,rule,fact_store),
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

        # Shared semantic facts are declarative in the rule manifest. Targets reuse
        # the source rule's verified matrix and therefore do not create a second
        # LLM request for the same semantic entity.
        for target_rule_id, source_rule_id in shared_fact_targets.items():
            target_routed=next((x for x in llm_routed if str(x['rule'].get('id'))==target_rule_id),None)
            if not target_routed:
                continue
            for fid in target_routed.get('fragmentIds',[]):
                source=next((x for x in reversed(raw) if x.get('ruleId')==source_rule_id and x.get('fragmentId')==fid),None)
                if source:
                    raw.append(_derive_shared_fact_item(source,target_rule_id))
                    fact_cache_hits+=1

    if fatal is not None:
        # Preserve deterministic/candidate work even when one semantic request hits
        # a fatal provider error. Only still-unprocessed semantic fragments become
        # technical not_checked results.
        warnings.append(f"Семантическая проверка частично остановлена провайдером: {fatal}")
        for routed in llm_routed:
            rule=routed['rule']
            for fid in routed.get('fragmentIds',[]):
                if any(x.get('ruleId')==rule['id'] and x.get('fragmentId')==fid for x in raw):
                    continue
                raw.append({
                    **_not_checked(rule,str(fatal)),
                    'fragmentId':fid,
                    'checkedBy':'llm',
                    'checkedFragments':[fid],
                    'technicalIncomplete':True,
                })

    try:
        candidate_results,candidate_warnings=await candidate_task
        warnings.extend(candidate_warnings)
        for item in candidate_results:
            local[item['ruleId']]=item
    except Exception as exc:
        warnings.append(f"Candidate stage завершился ошибкой: {exc}")
        for routed in candidate_routed:
            rule=routed['rule']
            local.setdefault(rule['id'],technical_rule_result(rule,'candidate',exc))

    abbreviation_usage=empty_usage()
    try:
        abbreviation_results, abbreviation_usage, abbreviation_warnings = await abbreviation_task
        merge_usage(usage, abbreviation_usage)
        warnings.extend(abbreviation_warnings)
        for item in abbreviation_results:
            local[item['ruleId']] = item
    except Exception as exc:
        warnings.append(f"Abbreviation fact-map завершился ошибкой: {exc}")
        merge_usage(usage,getattr(exc,'llm_usage',None))
        for routed in abbreviation_routed:
            rule=routed['rule']
            local.setdefault(rule['id'],technical_rule_result(rule,'abbreviation_fact_map',exc))

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
    verifier_usage=empty_usage()
    try:
        verified, verifier_usage, verifier_warnings = await verify_semantic_evidence(
            document=document, rules=rules, results=initial, provider=provider, model=model, system_prompt=prompt,
        )
        merge_usage(usage, verifier_usage)
        warnings.extend(verifier_warnings)
    except Exception as exc:
        warnings.append(f"Evidence verifier завершился ошибкой; сохранены предварительные результаты: {exc}")
        merge_usage(usage,getattr(exc,'llm_usage',None))
        verified=initial

    try:
        results=apply_consistency_checks(verified, document=document)
    except Exception as exc:
        warnings.append(f"Consistency post-processing завершился ошибкой; сохранены проверенные результаты: {exc}")
        results=verified

    results=enforce_verdict_contracts(results)
    rule_by_id={str(rule.get('id')):rule for rule in rules}
    validated=[]
    for item in results:
        try:
            validated.append(RuleResultModel.model_validate(item).model_dump(exclude_none=True))
        except Exception as exc:
            rid=str((item or {}).get('ruleId') or '') if isinstance(item,dict) else ''
            rule=rule_by_id.get(rid) or {'id':rid or 'UNKNOWN','severity':'major'}
            warnings.append(f"{rid or 'UNKNOWN'}: финальная валидация результата завершилась ошибкой: {exc}")
            validated.append(RuleResultModel.model_validate(
                technical_rule_result(rule,'result_validation',exc)
            ).model_dump(exclude_none=True))
    results=validated
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
            'abbreviationMode':str(abbreviation_usage.get('abbreviationMode') or 'llm-fact-map-high-recall'),
            'abbreviationPhysicalRequests':int(abbreviation_usage.get('requests',0)),
            'abbreviationCandidateCount':int(abbreviation_usage.get('abbreviationCandidateCount',0)),
            'abbreviationResolvedCandidates':int(abbreviation_usage.get('abbreviationResolvedCandidates',0)),
            'abbreviationUnresolvedCandidates':int(abbreviation_usage.get('abbreviationUnresolvedCandidates',0)),
            'abbreviationRecoveryRequests':int(abbreviation_usage.get('abbreviationRecoveryRequests',0)),
            'evidenceVerifierRequests':int(verifier_usage.get('requests',0)),
            'factRecoveryRequests':fact_recovery_requests,
            'factCacheHits':fact_cache_hits,
            'globalFactStoreVersion':int(fact_store.get('schemaVersion',1)),
            'globalAbbreviationGlossaryEntries':len(((fact_store.get('abbreviationGlossary') or {}).get('definitions') or {})),
            'globalTermDefinitions':len(fact_store.get('termDefinitions') or []),
            'candidateFamilies':len(candidate_plan['rulesByFamily']),
            'explicitRules':routing['explicitRules'],
            'fallbackRules':routing['fallbackRules'],
        },
    }
