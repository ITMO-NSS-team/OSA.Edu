from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..checking.candidates import collect_candidates, validate_candidate
from ..config import CONFIG_DIR, env_int
from ..llm.client import ask_structured_json, is_fatal_provider_error
from ..llm.rate_limiter import configured_rate_limits
from ..util import merge_usage, unique


def _system_prompt() -> str:
    path = CONFIG_DIR / 'candidate-prompt.txt'
    return path.read_text(encoding='utf-8').strip()


def build_candidate_plan(document: dict, routed_rules: list[dict]) -> dict[str, Any]:
    """Create all candidate batches without silently truncating any candidate."""
    by_family: dict[str, list[dict]] = {}
    routed_by_rule: dict[str, dict] = {}
    for routed in routed_rules:
        family = routed.get('candidateFamily') or routed.get('rule', {}).get('candidateFamily')
        if not family:
            continue
        by_family.setdefault(str(family), []).append(routed['rule'])
        routed_by_rule[routed['rule']['id']] = routed

    family_candidates: dict[str, list[dict]] = {}
    requests: list[dict] = []
    batch_size = max(1, env_int('CANDIDATE_BATCH_SIZE', 24))
    for family, rules in by_family.items():
        candidates = [x for x in collect_candidates(document, family) if validate_candidate(x, document)]
        family_candidates[family] = candidates
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start:start + batch_size]
            requests.append({
                'family': family,
                'rules': rules,
                'candidates': batch,
                'pairs': [(item['id'], rule['id']) for item in batch for rule in rules],
            })
    return {
        'requests': requests,
        'familyCandidates': family_candidates,
        'routedByRule': routed_by_rule,
        'rulesByFamily': by_family,
    }


def request_pairs_for(candidates: list[dict], rules: list[dict], pairs: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    if pairs is not None:
        return [(str(candidate_id), str(rule_id)) for candidate_id, rule_id in pairs]
    return [(item['id'], rule['id']) for item in candidates for rule in rules]



def build_recovery_requests(
    requests: list[dict],
    verdicts: dict[tuple[str, str], dict],
    *,
    pair_batch_size: int,
) -> list[dict]:
    """Split exact missing candidate/rule pairs into small recovery packets.

    Re-sending one large malformed abbreviation packet repeatedly reproduces the
    same long JSON failure.  Recovery therefore operates on exact pairs and caps
    the number of requested verdicts in each packet.
    """
    limit = max(1, int(pair_batch_size))
    recovery: list[dict] = []
    for request in requests:
        expected = request_pairs_for(request['candidates'], request['rules'], request.get('pairs'))
        missing = [pair for pair in expected if pair not in verdicts]
        for start in range(0, len(missing), limit):
            chunk = missing[start:start + limit]
            if not chunk:
                continue
            candidate_ids = {candidate_id for candidate_id, _rule_id in chunk}
            rule_ids = {rule_id for _candidate_id, rule_id in chunk}
            recovery.append({
                'family': request['family'],
                'candidates': [item for item in request['candidates'] if item['id'] in candidate_ids],
                'rules': [rule for rule in request['rules'] if rule['id'] in rule_ids],
                'pairs': chunk,
            })
    return recovery

def _message(family: str, candidates: list[dict], rules: list[dict], pairs: list[tuple[str, str]] | None = None) -> str:
    rule_text = '\n\n'.join(
        f"RULE {rule['id']}\nТребование: {rule.get('requirement','')}\n"
        f"Корректный пример: {rule.get('correctExample') or '—'}\n"
        f"Пример нарушения: {rule.get('incorrectExample') or '—'}"
        for rule in rules
    )
    candidate_text = '\n\n'.join(
        f"CANDIDATE {item['id']}\n"
        f"blockId={item['blockId']} page={item.get('page') or '—'} start={item['start']} end={item['end']}\n"
        f"quote={item['quote']}\ncontext={item['context']}\nmeta={item.get('meta') or {}}"
        for item in candidates
    )
    requested_pairs = request_pairs_for(candidates, rules, pairs)
    pair_text = '\n'.join(f"- {candidate_id} + {rule_id}" for candidate_id, rule_id in requested_pairs)
    return (
        f"FAMILY: {family}\n\nRULES:\n{rule_text}\n\nCANDIDATES:\n{candidate_text}\n\n"
        f"ОБЯЗАТЕЛЬНЫЕ ПАРЫ (верни ровно по одному verdict для каждой пары; другие пары не добавляй):\n{pair_text}\n\n"
        'Верни JSON вида {"verdicts":[{"candidateId":"...","ruleId":"...",'
        '"violation":true|false|null,"reason":"...","fix":"..."}]}.'
    )


def _parse_verdicts(value: Any, request: dict) -> dict[tuple[str, str], dict]:
    records = value.get('verdicts', []) if isinstance(value, dict) and isinstance(value.get('verdicts'), list) else []
    valid_candidates = {x['id'] for x in request['candidates']}
    valid_rules = {x['id'] for x in request['rules']}
    valid_pairs = set(request_pairs_for(request['candidates'], request['rules'], request.get('pairs')))
    grouped: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        candidate_id = str(record.get('candidateId') or '').strip()
        rule_id = str(record.get('ruleId') or '').strip()
        if candidate_id not in valid_candidates or rule_id not in valid_rules or (candidate_id, rule_id) not in valid_pairs:
            continue
        verdict = record.get('violation')
        if verdict not in {True, False, None}:
            continue
        grouped.setdefault((candidate_id, rule_id), []).append({
            'candidateId': candidate_id,
            'ruleId': rule_id,
            'violation': verdict,
            'reason': str(record.get('reason') or '').strip(),
            'fix': str(record.get('fix') or '').strip(),
        })
    # Duplicate answers for the same pair are rejected instead of arbitrarily
    # choosing one of potentially contradictory verdicts.
    return {key: rows[0] for key, rows in grouped.items() if len(rows) == 1}


def _evidence(candidate: dict) -> dict:
    result = {
        'quote': candidate['quote'],
        'context': candidate.get('context') or candidate['quote'],
        'blockId': candidate['blockId'],
        'location': candidate.get('location', ''),
        'start': candidate['start'],
        'end': candidate['end'],
        'verified': True,
    }
    if candidate.get('page') is not None:
        result['page'] = candidate['page']
    return result


def aggregate_candidate_results(plan: dict, verdicts: dict[tuple[str, str], dict]) -> list[dict]:
    results: list[dict] = []
    for family, rules in plan['rulesByFamily'].items():
        candidates = plan['familyCandidates'].get(family, [])
        for rule in rules:
            routed = plan['routedByRule'][rule['id']]
            rows = [(candidate, verdicts.get((candidate['id'], rule['id']))) for candidate in candidates]
            processed = [(candidate, verdict) for candidate, verdict in rows if verdict is not None]
            unclear = [(candidate, verdict) for candidate, verdict in processed if verdict.get('violation') is None]
            violations = [(candidate, verdict) for candidate, verdict in processed if verdict.get('violation') is True]
            total = len(candidates)
            responded = len(processed)
            terminal = responded - len(unclear)
            exhaustive = bool(routed.get('exhaustive', True)) and terminal == total
            coverage = {
                'kind': 'candidate',
                'candidateCount': total,
                # Backwards-compatible field: an answer was returned for this candidate.
                'checkedCandidateCount': responded,
                'respondedCandidateCount': responded,
                'terminalCandidateCount': terminal,
                'unclearCandidateCount': len(unclear),
                'packetCount': total,
                'checkedPacketCount': responded,
                'fraction': 1.0 if total == 0 else responded / total,
                'terminalFraction': 1.0 if total == 0 else terminal / total,
                'exhaustive': exhaustive,
            }
            common = {
                'ruleId': rule['id'],
                'severity': rule.get('severity', 'major'),
                'confidence': 0,
                'checkedBy': 'llm-candidate',
                'coverage': coverage,
                'checkedFragments': [f'candidate:{family}'],
                'candidateFamily': family,
                'dedupKey': rule.get('dedupKey'),
            }
            if violations:
                reasons = unique([verdict.get('reason', '') for _, verdict in violations])
                item = {
                    **common,
                    'status': 'violation',
                    'explanation': ' '.join(reasons) or 'Подтверждено нарушение по найденному кандидату.',
                    'evidence': [_evidence(candidate) for candidate, _ in violations[:20]],
                    'evidenceStatus': 'verified',
                    'findingIds': [f"candidate:{family}:{candidate['id']}" for candidate, _ in violations],
                }
                fix = next((verdict.get('fix') for _, verdict in violations if verdict.get('fix')), None)
                if fix:
                    item['fix'] = fix
                results.append(item)
                continue
            if total == 0 and routed.get('allowPass', True):
                results.append({
                    **common,
                    'status': 'pass',
                    'explanation': 'Python выполнил полный поиск кандидатов в назначенной области; потенциальных нарушений не найдено.',
                    'evidence': [],
                    'evidenceStatus': 'not_required',
                })
                continue
            if exhaustive and routed.get('allowPass', True):
                results.append({
                    **common,
                    'status': 'pass',
                    'explanation': f'Проверены все найденные кандидаты ({terminal}); подтверждённых нарушений нет.',
                    'evidence': [],
                    'evidenceStatus': 'not_required',
                })
                continue
            missing = total - responded
            detail = []
            if missing:
                detail.append(f'не обработано кандидатов: {missing}')
            if unclear:
                detail.append(f'неоднозначных ответов: {len(unclear)}')
            if not routed.get('allowPass', True) and routed.get('reason'):
                detail.append(str(routed['reason']))
            results.append({
                **common,
                'status': 'uncertain',
                'explanation': 'Проверка кандидатов не завершена полностью' + (': ' + '; '.join(detail) if detail else '.'),
                'evidence': [],
                'evidenceStatus': 'not_required',
            })
    return results


async def execute_candidate_plan(
    *,
    plan: dict,
    provider: str,
    model: str,
    usage: dict,
    on_request_done: Callable[[], Awaitable[None] | None] | None = None,
    is_cancelled: Callable[[], Awaitable[bool] | bool] | None = None,
) -> tuple[list[dict], list[str]]:
    verdicts: dict[tuple[str, str], dict] = {}
    warnings: list[str] = []
    transient_errors: list[tuple[str, str, str, set[tuple[str, str]]]] = []
    requests = plan['requests']
    if not requests:
        return aggregate_candidate_results(plan, verdicts), warnings

    fatal: BaseException | None = None

    async def cancelled() -> bool:
        if not is_cancelled:
            return False
        value = is_cancelled()
        return await value if asyncio.iscoroutine(value) else bool(value)

    async def run_requests(batch_requests: list[dict], *, phase: str, report_progress: bool) -> None:
        nonlocal fatal
        if not batch_requests or fatal is not None:
            return
        next_index = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal next_index, fatal
            while fatal is None:
                if await cancelled():
                    return
                async with lock:
                    if next_index >= len(batch_requests):
                        return
                    request = batch_requests[next_index]
                    next_index += 1
                try:
                    response = await ask_structured_json(
                        provider=provider,
                        model=model,
                        system_prompt=_system_prompt(),
                        user_message=_message(
                            request['family'],
                            request['candidates'],
                            request['rules'],
                            request.get('pairs'),
                        ),
                        operation='candidate',
                        packets=1,
                        candidates=len(request_pairs_for(request['candidates'], request['rules'], request.get('pairs'))),
                    )
                    merge_usage(usage, response['usage'])
                    verdicts.update(_parse_verdicts(response['value'], request))
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    merge_usage(usage, getattr(exc, 'llm_usage', None))
                    if is_fatal_provider_error(exc):
                        fatal = exc
                        return
                    transient_errors.append((
                        str(request['family']),
                        phase,
                        str(exc),
                        set(request_pairs_for(request['candidates'], request['rules'], request.get('pairs'))),
                    ))
                finally:
                    if report_progress and on_request_done:
                        value = on_request_done()
                        if asyncio.iscoroutine(value):
                            await value

        workers = min(configured_rate_limits(provider)['maxConcurrent'], max(1, len(batch_requests)))
        await asyncio.gather(*(worker() for _ in range(workers)))

    await run_requests(requests, phase='основной пакет', report_progress=True)
    if fatal is not None:
        raise fatal

    # A valid JSON response may still omit several candidate/rule pairs. Retrying
    # the whole packet wastes tokens and used to leave coverage at values such as
    # 26/48 or 69/91. Retry only the exact missing pairs, preserving all successful
    # verdicts from the first pass.
    retry_rounds = max(1, env_int('CANDIDATE_MISSING_RETRY_ROUNDS', 2))
    recovery_pair_batch = max(4, env_int('CANDIDATE_RECOVERY_PAIR_BATCH_SIZE', 16))
    for retry_round in range(1, retry_rounds + 1):
        # If a large JSON packet was malformed, retrying the same 48+ verdicts
        # tends to reproduce the failure.  Split missing exact pairs; on the next
        # round halve the packet again for an adaptive final recovery.
        round_batch_size = max(4, recovery_pair_batch // (2 ** (retry_round - 1)))
        retry_requests = build_recovery_requests(
            requests,
            verdicts,
            pair_batch_size=round_batch_size,
        )
        if not retry_requests:
            break
        await run_requests(
            retry_requests,
            phase=f'повтор пропущенных пар {retry_round}/{retry_rounds} (до {round_batch_size} пар)',
            report_progress=False,
        )
        if fatal is not None:
            raise fatal

    missing_pairs = {
        pair
        for request in requests
        for pair in request_pairs_for(request['candidates'], request['rules'], request.get('pairs'))
        if pair not in verdicts
    }
    # A malformed/partial first response is an implementation detail when the
    # targeted recovery pass restores every required pair. Do not surface a
    # misleading limitation such as “family not checked” after successful recovery.
    for family, phase, message, affected_pairs in transient_errors:
        unresolved = affected_pairs & missing_pairs
        if unresolved:
            warnings.append(
                f"Кандидаты семейства {family} ({phase}) частично не проверены: {message}; "
                f"после восстановления осталось обязательных пар: {len(unresolved)}."
            )
    if missing_pairs:
        warnings.append(
            f'После точечных повторов LLM не вернула {len(missing_pairs)} обязательных candidate/rule пар; '
            'соответствующие правила оставлены неопределёнными, а не засчитаны как проверенные.'
        )
    return aggregate_candidate_results(plan, verdicts), warnings

