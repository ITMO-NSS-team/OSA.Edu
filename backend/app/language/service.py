"""Checkpointed proofreading. A failed model call never certifies clean text."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import env_int
from ..util import empty_usage
from .extraction import extract_fragments, make_chunks, split_ranges
from .llm import DetectionOutput, ReviewOutput, ReviewError, request_json
from .spelling import candidates as spelling_candidates
from .validation import (
    CATEGORIES, CATEGORY_LABELS, SYSTEM_PROMPT, REVIEW_PROMPT,
    adjudicate, consolidate, validate_issues,
)

PIPELINE_VERSION = 2
Progress = Callable[[int, int, list[dict], dict], Awaitable[None]]


def detection_data(fragments: list[dict], hints: list[dict], style: bool) -> dict:
    owned = {f['id'] for f in fragments}
    inputs = [{'fragment_id': f['id'], 'page': f['page'], 'type': f['type'], 'text': f['text'],
               'contextBefore': '' if f.get('contextBeforeId') in owned else f.get('contextBefore', ''),
               'contextAfter': '' if f.get('contextAfterId') in owned else f.get('contextAfter', ''),
               'contextBeforeId': f.get('contextBeforeId'), 'contextAfterId': f.get('contextAfterId'),
               'contextAfterType': f.get('contextAfterType')} for f in fragments]
    return {'style': style, 'fragments': inputs, 'dictionary_candidates': hints,
            'reading_order': 'Fragments are in document order; read adjacent fragments as context. External context is read-only.',
            'dictionary_note': 'Кандидаты могут быть корректными терминами; проверь их по контексту.'}


def review_data(findings: list[dict], style: bool) -> dict:
    sources = {f['fragmentId']: {'text': f['context'], 'before': f['contextBefore'], 'after': f['contextAfter']}
               for f in findings}
    keys = ('id', 'fragmentId', 'category', 'quote', 'replacement', 'explanation', 'originalSentence', 'correctedSentence')
    return {'style': style, 'source_fragments': sources,
            'findings': [{key: f[key] for key in keys} for f in findings]}


def _unit(fragments: list[dict], depth: int = 0) -> dict:
    identity = json.dumps([(f['id'], f['text']) for f in fragments], ensure_ascii=False)
    return {'id': hashlib.sha256(identity.encode()).hexdigest()[:18], 'fragments': fragments,
            'depth': depth, 'status': 'pending', 'candidates': None, 'issues': [], 'disputed': [],
            'rejected': 0, 'attempts': {'detect': 0, 'review': 0}, 'history': [], 'error': None}


def _leaves(checkpoint: dict) -> list[dict]:
    return [u for u in checkpoint['units'] if u['status'] != 'split']


def result_from_checkpoint(checkpoint: dict, *, running: bool = False, stopped: str | None = None) -> dict:
    units = _leaves(checkpoint)
    checked = [u for u in units if u['status'] == 'checked']
    issues, conflicts = consolidate([f for u in units for f in u['issues']], True)
    disputed = [f for u in units for f in u['disputed']] + (conflicts if checkpoint['includeDisputed'] else [])
    disputed = list({f['id']: f for f in disputed}.values())
    metadata = checkpoint['extraction']
    total_chars = sum(len(f['text']) for u in units for f in u['fragments'])
    covered_chars = sum(len(f['text']) for u in checked for f in u['fragments'])
    unchecked = [{
        'id': u['id'], 'chunk': index + 1,
        'fragmentIds': [f['id'] for f in u['fragments']],
        'pages': sorted({f['page'] for f in u['fragments']}),
        'characters': sum(len(f['text']) for f in u['fragments']),
        'kind': (u.get('error') or {}).get('kind', 'pending'),
        'message': (u.get('error') or {}).get('message', 'Фрагменты ещё не проверены.'),
    } for index, u in enumerate(units) if u['status'] != 'checked']
    complete = not (unchecked or metadata.get('emptyPages') or running or stopped)
    warnings = []
    if running:
        warnings.append('Промежуточный результат. Проверка ещё выполняется.')
    if unchecked:
        warnings.append(f'Не проверено пакетов: {len(unchecked)}. Отсутствие замечаний не означает отсутствия ошибок.')
    if metadata.get('emptyPages'):
        warnings.append(f'Страницы без извлечённого текста: {metadata["emptyPages"]}. Возможно, нужен OCR.')
    if metadata.get('omittedBlocks'):
        warnings.append('Оглавление, библиография, формулы, код и нерусский текст исключены; перечень исключений сохранён в JSON.')
    if not metadata.get('dictionaryAvailable'):
        warnings.append('Русский Hunspell-словарь не найден: словарные подсказки отключены, работает контекстная LLM-проверка.')
    if metadata.get('ambiguousHyphenations'):
        warnings.append('Часть переносов с видимым дефисом неоднозначна; такие изменения требуют ручной проверки.')
    if stopped:
        warnings.append(stopped)
    if conflicts:
        warnings.append(f'Несовместимых исправлений одного участка: {len(conflicts)}. Они исключены из основных замечаний.')
    rejected = sum(u['rejected'] for u in units)
    if rejected:
        warnings.append(f'Отклонено или скрыто спорных предложений после валидации: {rejected}.')
    counts = {category: sum(f['category'] == category for f in issues) for category in CATEGORIES}
    public_usage = {k: v for k, v in checkpoint['usage'].items() if k not in {'traces', 'diagnostics'}}
    return {
        'schemaVersion': PIPELINE_VERSION, 'status': 'complete' if complete else 'partial',
        'issues': issues, 'disputed': sorted(disputed, key=lambda f: (f['page'], f['id'])),
        'counts': counts, 'totalIssues': len(issues), 'styleEnabled': checkpoint['style'],
        'includeDisputed': checkpoint['includeDisputed'], 'model': checkpoint['model'],
        'totalChunks': len(units), 'checkedChunks': len(checked), 'complete': complete,
        'warnings': warnings, 'errors': unchecked, 'uncheckedFragments': unchecked,
        'rejectedModelSuggestions': rejected, 'extraction': metadata, 'usage': public_usage,
        'coverage': {'checkedCharacters': covered_chars, 'totalCharacters': total_chars,
                     'ratio': covered_chars / max(1, total_chars),
                     'uncheckedPages': sorted({p for item in unchecked for p in item['pages']} | set(metadata.get('emptyPages', [])))},
        'packets': [{'id': u['id'], 'status': u['status'],
                     'fragmentIds': [f['id'] for f in u['fragments']],
                     'pages': sorted({f['page'] for f in u['fragments']}),
                     'attempts': u['attempts']} for u in units],
        'resumable': bool(unchecked),
    }


def _divide(unit: dict) -> list[dict]:
    fragments = unit['fragments']
    if len(fragments) > 1:
        middle = len(fragments) // 2
        return [_unit(fragments[:middle], unit['depth'] + 1), _unit(fragments[middle:], unit['depth'] + 1)]
    fragment = fragments[0]
    text = fragment['text']
    if len(text) < 600:
        return []
    ranges = split_ranges(text, max(250, len(text) // 2))
    children = []
    for index, (start, end) in enumerate(ranges):
        child = {**fragment, 'id': f'{fragment["id"]}-r{index}', 'text': text[start:end],
                 'source': {**fragment['source'], 'start': fragment['source']['start'] + start,
                            'end': fragment['source']['start'] + end},
                 'contextBefore': (fragment.get('contextBefore', '') + text[:start])[-900:],
                 'contextAfter': (text[end:] + fragment.get('contextAfter', ''))[:900]}
        children.append(_unit([child], unit['depth'] + 1))
    return children


async def analyze_pdf(
    path: Path, *, provider: str, model: str, style: bool,
    progress: Progress | None = None, checkpoint: dict | None = None,
    include_disputed: bool = False,
) -> dict[str, Any]:
    fragments, metadata = await asyncio.to_thread(extract_fragments, path)
    source_hash = await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest())
    identity = json.dumps([PIPELINE_VERSION, source_hash, model, style, include_disputed,
                           [(f['id'], f['text']) for f in fragments],
                           os.getenv('LANGUAGE_ALLOWED_TERMS_FILE', ''),
                           hashlib.sha256((SYSTEM_PROMPT + REVIEW_PROMPT).encode()).hexdigest()], ensure_ascii=False)
    fingerprint = hashlib.sha256(identity.encode()).hexdigest()
    if checkpoint:
        if checkpoint.get('version') != PIPELINE_VERSION or checkpoint.get('fingerprint') != fingerprint:
            raise ValueError('Исходный PDF, настройки или схема извлечения изменились. Сохранённый запуск нельзя безопасно продолжить; создайте новую проверку.')
        state = copy.deepcopy(checkpoint)
        # Resume does not reset cumulative usage or completed work. Only failed
        # stages get a fresh, bounded automatic attempt window.
        for unit in _leaves(state):
            if unit['status'] != 'checked':
                unit.update(status='detected' if unit['candidates'] is not None else 'pending', error=None)
    else:
        state = {'version': PIPELINE_VERSION, 'fingerprint': fingerprint,
                 'model': model, 'style': style, 'includeDisputed': include_disputed,
                 'extraction': metadata, 'usage': empty_usage(),
                 'units': [_unit(chunk) for chunk in make_chunks(fragments)]}
    usage = state['usage']
    max_requests = min(1000, env_int('LANGUAGE_MAX_REQUESTS', 160))
    max_tokens = env_int('LANGUAGE_MAX_ESTIMATED_TOKENS', 1200000)
    max_chunks = min(1000, env_int('LANGUAGE_MAX_CHUNKS', 160))
    attempts = min(5, env_int('LANGUAGE_PACKET_ATTEMPTS', 3))
    max_depth = min(3, env_int('LANGUAGE_SPLIT_DEPTH', 2))
    stopped = None

    async def persist() -> None:
        if progress:
            result = result_from_checkpoint(state, running=True)
            await progress(result['checkedChunks'], result['totalChunks'], result['issues'], {
                'checkpoint': copy.deepcopy(state), 'result': result,
                'extraction': metadata, 'errors': result['errors'], 'usage': dict(usage),
            })

    async def before_request(estimated: int, output_limit: int) -> None:
        if usage['requests'] >= max_requests or usage.get('reservedTokens', 0) + estimated + output_limit > max_tokens:
            raise ReviewError('budget', 'Достигнут лимит запросов или оценочного бюджета токенов. Успешные пакеты сохранены.')
        usage['requests'] += 1
        usage['estimatedInputTokens'] += estimated
        usage['reservedTokens'] = usage.get('reservedTokens', 0) + estimated + output_limit
        # Account before dispatch: cancellation/crash cannot make an in-flight
        # paid request disappear from the checkpoint's budget.
        await persist()

    async def retry_wait(attempt: int, error: Exception) -> None:
        usage['retries'] += 1
        delay = max(min(30.0, float(os.getenv('LANGUAGE_RETRY_DELAY_SECONDS', '1')) * 2**attempt),
                    getattr(error, 'retry_after_ms', 0) / 1000)
        await asyncio.sleep(max(0.0, delay))

    def enlarge_output(unit: dict, error: Exception) -> None:
        if getattr(error, 'kind', None) == 'truncated':
            cap = min(12000, env_int('LANGUAGE_MAX_OUTPUT_TOKENS', 8000))
            current = unit.get('outputLimit', min(cap, env_int('LANGUAGE_INITIAL_OUTPUT_TOKENS', 4000)))
            unit['outputLimit'] = min(cap, current * 2)

    async def review_candidates(unit: dict) -> None:
        eligible = [f for f in unit['candidates'] if not f.get('disputeReason')]
        guarded = [f for f in unit['candidates'] if f.get('disputeReason')]
        guarded_disputed = [{**f, 'status': 'disputed', 'validation': {
            'technical': True, 'semantic': 'uncertain', 'reason': f['disputeReason'],
            'method': 'programmatic_guard'}} for f in guarded] if include_disputed else []

        def batch(findings, depth=0):
            return {'findings': findings, 'depth': depth, 'status': 'pending',
                    'issues': [], 'disputed': [], 'rejected': 0}

        if 'reviewBatches' not in unit:
            batches, current, used = [], [], 0
            # Returning complete corrected sentences must fit the output limit.
            # Normal packets remain one call; unusually long ones use bounded
            # batches instead of repeatedly truncating a successful detection.
            limit = min(6000, unit.get('outputLimit', env_int('LANGUAGE_INITIAL_OUTPUT_TOKENS', 4000)), env_int('LANGUAGE_MAX_OUTPUT_TOKENS', 8000))
            for finding in eligible:
                length = len(finding['correctedSentence']) + 220
                if current and used + length > limit:
                    batches.append(batch(current))
                    current, used = [], 0
                current.append(finding)
                used += length
            if current:
                batches.append(batch(current))
            unit['reviewBatches'] = batches

        def collect():
            completed = [b for b in unit['reviewBatches'] if b['status'] == 'checked']
            unit['issues'] = [f for b in completed for f in b['issues']]
            unit['disputed'] = guarded_disputed + [f for b in completed for f in b['disputed']]
            unit['rejected'] = (0 if include_disputed else len(guarded)) + sum(b['rejected'] for b in completed)

        collect()
        index = 0
        while index < len(unit['reviewBatches']):
            item = unit['reviewBatches'][index]
            index += 1
            if item['status'] in {'checked', 'split'}:
                continue
            last_error = None
            for attempt in range(attempts):
                unit['attempts']['review'] += 1
                try:
                    payload = await request_json(provider=provider, model=model, system=REVIEW_PROMPT,
                                                 message=json.dumps(review_data(item['findings'], style), ensure_ascii=False),
                                                 schema=ReviewOutput, usage=usage, before_request=before_request,
                                                 output_limit=unit.get('outputLimit'))
                    accepted, disputed, rejected = adjudicate(item['findings'], payload, include_disputed)
                    item.update(status='checked', issues=accepted, disputed=disputed, rejected=rejected)
                    collect()
                    await persist()
                    last_error = None
                    break
                except ReviewError as exc:
                    last_error = exc
                    enlarge_output(unit, exc)
                    fatal = exc.kind in {'budget', 'refusal', 'quota'} or exc.status in {401, 402, 403}
                    transient = exc.kind in {'invalid_json', 'schema', 'semantic', 'empty', 'truncated', 'timeout', 'network'} or exc.status in {408, 409, 425, 429, 500, 502, 503, 504}
                    if fatal or not transient:
                        raise
                    if attempt + 1 < attempts:
                        await retry_wait(attempt, exc)
            if last_error:
                if (len(item['findings']) > 1 and item['depth'] < max_depth
                    and last_error.kind in {'invalid_json', 'schema', 'semantic', 'empty', 'truncated'}):
                    middle = len(item['findings']) // 2
                    item['status'] = 'split'
                    unit['reviewBatches'].extend([batch(item['findings'][:middle], item['depth'] + 1),
                                                 batch(item['findings'][middle:], item['depth'] + 1)])
                    await persist()
                else:
                    raise last_error

    await persist()
    index = 0
    while index < len(state['units']):
        unit = state['units'][index]
        index += 1
        if unit['status'] in {'checked', 'split'}:
            continue
        if sum(u['status'] == 'checked' for u in _leaves(state)) >= max_chunks:
            unit['error'] = {'kind': 'limit', 'message': 'Достигнут лимит обрабатываемых пакетов.'}
            stopped = 'Проверка остановлена лимитом пакетов; незавершённые участки доступны для дозапуска.'
            break
        fatal = False
        for stage in ('detect', 'review'):
            if stage == 'detect' and unit['candidates'] is not None:
                continue
            succeeded = False
            for attempt in range(attempts if stage == 'detect' else 1):
                if stage == 'detect':
                    unit['attempts'][stage] += 1
                unit['status'] = 'detecting' if stage == 'detect' else 'reviewing'
                await persist()
                try:
                    if stage == 'detect':
                        hints = await asyncio.to_thread(spelling_candidates, unit['fragments'])
                        data = detection_data(unit['fragments'], hints, style)
                        payload = await request_json(provider=provider, model=model, system=SYSTEM_PROMPT,
                                                     message=json.dumps(data, ensure_ascii=False), schema=DetectionOutput,
                                                     usage=usage, before_request=before_request, output_limit=unit.get('outputLimit'))
                        unit['candidates'], _ = validate_issues(payload, unit['fragments'], style)
                        unit.update(status='detected', error=None)
                    else:
                        await review_candidates(unit)
                        unit.update(status='checked', error=None)
                        usage['packets'] += 1
                    succeeded = True
                    await persist()
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    enlarge_output(unit, exc)
                    kind = getattr(exc, 'kind', 'internal')
                    error = {'kind': kind, 'message': str(exc)[:240] if isinstance(exc, ReviewError) else 'Внутренняя ошибка обработки пакета.'}
                    unit.update(status='failed', error=error)
                    unit['history'] = [*unit['history'], {'stage': stage, **error}][-12:]
                    await persist()
                    status = getattr(exc, 'status', 0)
                    fatal = kind in {'budget', 'internal', 'refusal', 'quota'} or status in {401, 402, 403}
                    retryable = kind in {'invalid_json', 'schema', 'semantic', 'empty', 'truncated', 'timeout', 'network'} or status in {408, 409, 425, 429, 500, 502, 503, 504}
                    if fatal or not retryable:
                        break
                    if stage == 'detect' and attempt + 1 < attempts:
                        await retry_wait(attempt, exc)
            if not succeeded:
                if fatal:
                    stopped = unit['error']['message']
                elif stage == 'detect' and unit['depth'] < max_depth and unit['error']['kind'] in {'invalid_json', 'schema', 'semantic', 'empty', 'truncated'}:
                    children = _divide(unit)
                    if children:
                        unit['status'] = 'split'
                        state['units'].extend(children)
                break
        await persist()
        if fatal:
            break
    result = result_from_checkpoint(state, stopped=stopped)
    # Final checkpoint is persisted before returning to the queue.
    if progress:
        await progress(result['checkedChunks'], result['totalChunks'], result['issues'], {
            'checkpoint': copy.deepcopy(state), 'result': result,
            'extraction': metadata, 'errors': result['errors'], 'usage': dict(usage),
        })
    return result
