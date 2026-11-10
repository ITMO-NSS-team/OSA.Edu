"""Single-process worker with durable checkpoints and atomic state transitions."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..defaults import model_definition
from ..config import env_int
from ..util import now_iso
from .service import analyze_pdf
from .llm import ReviewError
from .store import get_job, list_jobs, update_job

_task: asyncio.Task[None] | None = None
_lock = asyncio.Lock()
_active: dict[str, asyncio.Task[Any]] = {}


def start_queue() -> None:
    global _task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _task is None or _task.done():
        _task = loop.create_task(_drain(), name='osa-language-review-queue')


async def cancel(job_id: str) -> dict[str, Any] | None:
    current = await get_job(job_id)
    if not current:
        return None
    if current['status'] == 'queued':
        changed = await update_job(job_id, {'status': 'cancelled', 'finishedAt': now_iso(),
                                           'progressMessage': 'Отменено до запуска.'}, expected_statuses={'queued'})
        if changed:
            return changed
    result = await update_job(job_id, {'status': 'cancelling', 'progressMessage': 'Остановка…'},
                              expected_statuses={'running', 'cancelling'})
    if result:
        active = _active.get(job_id)
        if active:
            active.cancel()
    return result or await get_job(job_id)


async def _drain() -> None:
    async with _lock:
        while True:
            try:
                jobs = await list_jobs()
            except RuntimeError:
                return
            queued = [j for j in jobs if j['status'] == 'queued']
            if not queued:
                return
            await _perform(min(queued, key=lambda value: value.get('createdAt', '')))


async def _perform(job: dict[str, Any]) -> None:
    job_id = job['id']
    current = await get_job(job_id, internal=True)
    if not current or current['status'] != 'queued':
        return
    source = Path(current['filePath'])
    selected = model_definition(current['model'])
    if not source.exists() or not selected:
        await update_job(job_id, {'status': 'failed', 'error': 'Исходный PDF или модель не найдены.',
                                  'finishedAt': now_iso()}, expected_statuses={'queued'})
        return
    claimed = await update_job(job_id, {'status': 'running', 'startedAt': current.get('startedAt') or now_iso(),
                                        'error': None, 'progressMessage': 'Извлечение и проверка незавершённого текста…'},
                               expected_statuses={'queued'})
    if not claimed:
        return

    async def progress(checked: int, total: int, findings: list[dict], extra: dict) -> None:
        latest = await get_job(job_id)
        if not latest or latest['status'] == 'cancelling':
            raise asyncio.CancelledError
        result = extra['result']
        await update_job(job_id, {
            'progress': round(result['coverage']['ratio'] * 100),
            'progressMessage': f'Проверено пакетов: {checked} / {total}',
            'result': result, 'checkpoint': extra['checkpoint'],
        }, expected_statuses={'running'})

    run = asyncio.create_task(analyze_pdf(
        source, provider=selected['provider'], model=current['model'], style=current['styleEnabled'],
        include_disputed=current.get('includeDisputed', False), progress=progress, checkpoint=current.get('checkpoint'),
    ))
    _active[job_id] = run
    freshest = await get_job(job_id)
    if freshest and freshest['status'] == 'cancelling':
        run.cancel()
    try:
        result = await asyncio.wait_for(run, timeout=env_int('LANGUAGE_JOB_TIMEOUT_SECONDS', 3600))
        latest = await get_job(job_id)
        if latest and latest['status'] == 'cancelling':
            raise asyncio.CancelledError
        status = 'done' if result['complete'] else ('partial' if result['checkedChunks'] or result['totalIssues'] or result.get('disputed') else 'failed')
        await update_job(job_id, {'status': status, 'result': result, 'finishedAt': now_iso(),
                                  'progress': round(result['coverage']['ratio'] * 100),
                                  'progressMessage': 'Проверка завершена.' if result['complete'] else 'Есть непроверенные фрагменты.'},
                         expected_statuses={'running'})
    except asyncio.CancelledError:
        await _finish_interrupted(job_id, 'cancelled', 'Проверка остановлена; результаты частичные.')
        # Cancellation of the worker itself must propagate, cancellation of a
        # child (the user pressed Stop) must allow the queue to continue.
        if asyncio.current_task().cancelling():
            raise
    except TimeoutError:
        await _finish_interrupted(job_id, 'partial', 'Достигнут общий лимит времени; успешные пакеты сохранены для дозапуска.')
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, ReviewError)) else 'Проверка прервана внутренней ошибкой; успешные пакеты сохранены.'
        await _finish_interrupted(job_id, 'partial', message)
    finally:
        _active.pop(job_id, None)


async def _finish_interrupted(job_id: str, status: str, message: str) -> None:
    latest = await get_job(job_id)
    result = latest.get('result') if latest else None
    if result:
        result = {**result, 'complete': False, 'status': 'cancelled' if status == 'cancelled' else 'partial',
                  'warnings': list(dict.fromkeys([*result.get('warnings', []), message]))}
    await update_job(job_id, {'status': status if result or status == 'cancelled' else 'failed',
                              'result': result, 'finishedAt': now_iso(), 'progressMessage': message},
                     expected_statuses={'running', 'cancelling'})
