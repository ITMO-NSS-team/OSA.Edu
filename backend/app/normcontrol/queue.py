from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..config import (
    NORMCONTROL_DAG_ID,
    NORMCONTROL_HTTP_TIMEOUT_SECONDS,
    NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS,
    NORMCONTROL_MCP_ATTEMPTS,
    NORMCONTROL_MCP_URL,
    NORMCONTROL_REPORTS_DIR,
)
from ..util import now_iso
from .client import collect_endpoint_diagnostics, download_pdf, generate_pdf_report, submit_document, with_deadline
from .store import get_normcontrol_job, list_normcontrol_jobs, update_normcontrol_job

_queue_task: asyncio.Task[None] | None = None
_queue_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


def start_normcontrol_queue() -> None:
    global _queue_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _queue_task is None or _queue_task.done():
        _queue_task = loop.create_task(_run_queue(), name='osa-edu-normcontrol-queue')


async def _run_queue() -> None:
    async with _queue_lock:
        while True:
            jobs = await list_normcontrol_jobs()
            job = next((item for item in jobs if item.get('status') in {'queued', 'queued_report'}), None)
            if not job:
                return
            if job.get('status') == 'queued_report':
                await _request_existing_report(job)
            else:
                await _submit_and_download_report(job)


async def _submit_and_download_report(job: dict[str, Any]) -> None:
    try:
        source = Path(job['filePath'])
        if not source.exists():
            raise RuntimeError('Исходный PDF не найден. Загрузите документ заново.')
        logger.info('Starting normcontrol job id=%s file=%s dag_id=%s', job['id'], job.get('originalName'), job.get('dagId'))
        await update_normcontrol_job(job['id'], {
            'status': 'submitting',
            'progress': 1,
            'progressMessage': 'Отправляем PDF на сервер нормоконтроля.',
            'startedAt': now_iso(),
            'attempts': 0,
            'maxAttempts': _max_attempts(job),
            'timeoutSeconds': _timeout_seconds(job),
            'error': None,
        })
        result = await _submit_document_with_retries(job, source)
        logger.info(
            'MCP submit_document result job_id=%s status=%s task_id=%s has_report=%s errors=%s warnings=%s',
            job['id'],
            result.get('status'),
            result.get('task_id') or result.get('run_id'),
            bool(result.get('report_pdf')),
            len(_string_list(result.get('errors'))),
            len(_string_list(result.get('warnings'))),
        )
        await _record_submit_result(job['id'], result)
        if _mcp_status(result) == 'failed':
            raise RuntimeError(_message(result, 'MCP pipeline returned failed status.'))
        run_id = str(result.get('task_id') or result.get('run_id') or '').strip()
        if not run_id:
            raise RuntimeError('MCP не вернул run_id/task_id для проверки.')
        report_url = _optional_string(result.get('report_pdf'))
        if not report_url:
            report_result = await _generate_report(job['id'], job.get('mcpUrl') or NORMCONTROL_MCP_URL, job.get('dagId') or NORMCONTROL_DAG_ID, run_id)
            report_url = _optional_string(report_result.get('report_pdf'))
        if not report_url:
            raise RuntimeError('MCP завершил обработку, но не вернул ссылку на PDF-отчёт.')
        await _download_report(job['id'], report_url)
    except Exception as exc:
        await _fail_job(job['id'], exc)


async def _request_existing_report(job: dict[str, Any]) -> None:
    try:
        run_id = str(job.get('runId') or job.get('taskId') or '').strip()
        if not run_id:
            raise RuntimeError('Нельзя запросить отчёт без run_id/task_id.')
        report_result = await _generate_report(job['id'], job.get('mcpUrl') or NORMCONTROL_MCP_URL, job.get('dagId') or NORMCONTROL_DAG_ID, run_id)
        if _mcp_status(report_result) == 'failed':
            raise RuntimeError(_message(report_result, 'MCP pipeline returned failed status.'))
        report_url = _optional_string(report_result.get('report_pdf'))
        if not report_url:
            raise RuntimeError('MCP не вернул ссылку на PDF-отчёт.')
        await _download_report(job['id'], report_url)
    except Exception as exc:
        await _fail_job(job['id'], exc)


async def _generate_report(job_id: str, url: str, dag_id: str, run_id: str) -> dict[str, Any]:
    await update_normcontrol_job(job_id, {
        'status': 'reporting',
        'progress': 90,
        'progressMessage': 'Запрашиваем итоговый PDF-отчёт.',
        'attempts': 0,
        'error': None,
    })
    result = await _generate_report_with_retries(job_id, url, dag_id, run_id)
    logger.info('MCP generate_pdf_report result job_id=%s status=%s has_report=%s', job_id, result.get('status'), bool(result.get('report_pdf')))
    mcp_status = _mcp_status(result)
    await update_normcontrol_job(job_id, {
        **_result_patch(result),
        'status': 'reporting' if mcp_status in {'queued', 'running'} else 'downloading',
        'progress': 90 if mcp_status in {'queued', 'running'} else 95,
    })
    return result


async def _download_report(job_id: str, report_url: str) -> None:
    target = NORMCONTROL_REPORTS_DIR / f'{job_id}.pdf'
    await update_normcontrol_job(job_id, {
        'status': 'downloading',
        'progress': 96,
        'progressMessage': 'Скачиваем PDF-отчёт нормоконтроля.',
        'error': None,
    })
    size = await download_pdf(report_url, target, timeout_seconds=NORMCONTROL_HTTP_TIMEOUT_SECONDS)
    logger.info('Normcontrol report downloaded job_id=%s bytes=%s path=%s', job_id, size, target)
    await update_normcontrol_job(job_id, {
        'status': 'completed',
        'progress': 100,
        'progressMessage': 'Нормоконтроль завершён. PDF-отчёт готов.',
        'reportPath': str(target.resolve()),
        'reportSize': size,
        'finishedAt': now_iso(),
        'error': None,
    })


async def _submit_document_with_retries(job: dict[str, Any], source: Path) -> dict[str, Any]:
    job_id = job['id']
    url = job.get('mcpUrl') or NORMCONTROL_MCP_URL
    dag_id = job.get('dagId') or NORMCONTROL_DAG_ID
    attempts = _max_attempts(job)
    timeout = _timeout_seconds(job)
    last_exc: BaseException | None = None

    for attempt in range(1, attempts + 1):
        progress_seen = False

        async def progress_handler(progress: float, total: float | None, message: str | None) -> None:
            nonlocal progress_seen
            progress_seen = True
            await _record_progress(job_id, progress, total, message, phase='submit', attempt=attempt)

        await update_normcontrol_job(job_id, {
            'status': 'submitting',
            'attempts': attempt,
            'maxAttempts': attempts,
            'timeoutSeconds': timeout,
            'lastAttemptAt': now_iso(),
            'progressMessage': f'Отправляем PDF на сервер нормоконтроля. Попытка {attempt}/{attempts}.',
            'error': None,
        })
        started = time.perf_counter()
        logger.info(
            'MCP attempt tool=submit_document attempt=%s/%s timeout_seconds=%s elapsed_ms=0 endpoint=%s dag_id=%s progress_seen=false',
            attempt,
            attempts,
            timeout,
            url,
            dag_id,
        )
        try:
            result = await with_deadline(
                submit_document(url=url, dag_id=dag_id, pdf_path=source, progress_handler=progress_handler),
                timeout,
            )
            mcp_status = _mcp_status(result)
            mcp_message = _message(result, '')
            logger.info(
                'MCP attempt responded tool=submit_document attempt=%s/%s timeout_seconds=%s elapsed_ms=%s endpoint=%s dag_id=%s progress_seen=%s mcp_status=%s mcp_message=%s run_id_received=%s has_report=%s mcp_errors=%s mcp_warnings=%s',
                attempt,
                attempts,
                timeout,
                _elapsed_ms(started),
                url,
                dag_id,
                progress_seen,
                mcp_status,
                mcp_message,
                bool(_optional_string(result.get('task_id') or result.get('run_id'))),
                bool(_optional_string(result.get('report_pdf'))),
                len(_string_list(result.get('errors'))),
                len(_string_list(result.get('warnings'))),
            )
            await _append_attempt_log(job_id, _attempt_log_entry(
                tool='submit_document',
                attempt=attempt,
                attempts=attempts,
                timeout=timeout,
                elapsed_ms=_elapsed_ms(started),
                url=url,
                dag_id=dag_id,
                progress_seen=progress_seen,
                status='remote_failed' if mcp_status == 'failed' else 'succeeded',
                mcp_result=result,
            ))
            return result
        except TimeoutError as exc:
            last_exc = exc
            diagnostics = await collect_endpoint_diagnostics(url)
            error = f'MCP submit_document timed out after {timeout} seconds.'
            logger.warning(
                'MCP attempt timed out tool=submit_document attempt=%s/%s timeout_seconds=%s elapsed_ms=%s endpoint=%s dag_id=%s progress_seen=%s error=%s diagnostics=%s',
                attempt,
                attempts,
                timeout,
                _elapsed_ms(started),
                url,
                dag_id,
                progress_seen,
                error,
                diagnostics,
            )
            await _append_attempt_log(job_id, _attempt_log_entry(
                tool='submit_document',
                attempt=attempt,
                attempts=attempts,
                timeout=timeout,
                elapsed_ms=_elapsed_ms(started),
                url=url,
                dag_id=dag_id,
                progress_seen=progress_seen,
                status='timed_out',
                error=error,
                diagnostics=diagnostics,
            ))
            if attempt < attempts:
                await _schedule_next_attempt(job_id, timeout, attempt + 1, attempts, timed_out=True)
                await asyncio.sleep(_retry_delay_before_attempt(attempt + 1))
        except Exception as exc:
            last_exc = exc
            diagnostics = await collect_endpoint_diagnostics(url)
            error = _error_text(exc)
            logger.exception(
                'MCP attempt failed tool=submit_document attempt=%s/%s timeout_seconds=%s elapsed_ms=%s endpoint=%s dag_id=%s progress_seen=%s error=%s diagnostics=%s',
                attempt,
                attempts,
                timeout,
                _elapsed_ms(started),
                url,
                dag_id,
                progress_seen,
                error,
                diagnostics,
            )
            await _append_attempt_log(job_id, _attempt_log_entry(
                tool='submit_document',
                attempt=attempt,
                attempts=attempts,
                timeout=timeout,
                elapsed_ms=_elapsed_ms(started),
                url=url,
                dag_id=dag_id,
                progress_seen=progress_seen,
                status='failed',
                error=error,
                diagnostics=diagnostics,
            ))
            if attempt < attempts:
                await _schedule_next_attempt(job_id, timeout, attempt + 1, attempts, timed_out=False)
                await asyncio.sleep(_retry_delay_before_attempt(attempt + 1))

    if isinstance(last_exc, TimeoutError):
        raise RuntimeError(
            f'MCP submit_document timed out after {attempts} attempts of {timeout} seconds. '
            'Remote run_id was not received; повторная автоматическая отправка остановлена, чтобы не плодить внешние DAG-запуски.'
        ) from last_exc
    message = f'MCP submit_document failed after {attempts} attempts.'
    if last_exc:
        message = f'{message} Last error: {last_exc}'
    raise RuntimeError(
        f'{message} Remote run_id was not received; повторная автоматическая отправка остановлена, чтобы не плодить внешние DAG-запуски.'
    ) from last_exc


async def _generate_report_with_retries(job_id: str, url: str, dag_id: str, run_id: str) -> dict[str, Any]:
    current = await get_normcontrol_job(job_id)
    attempts = _max_attempts(current or {})
    timeout = _timeout_seconds(current or {})
    last_exc: BaseException | None = None

    for attempt in range(1, attempts + 1):
        progress_seen = False

        async def progress_handler(progress: float, total: float | None, message: str | None) -> None:
            nonlocal progress_seen
            progress_seen = True
            await _record_progress(job_id, progress, total, message, phase='report', attempt=attempt)

        await update_normcontrol_job(job_id, {
            'status': 'reporting',
            'attempts': attempt,
            'maxAttempts': attempts,
            'timeoutSeconds': timeout,
            'lastAttemptAt': now_iso(),
            'progressMessage': f'Запрашиваем итоговый PDF-отчёт. Попытка {attempt}/{attempts}.',
            'error': None,
        })
        started = time.perf_counter()
        logger.info(
            'MCP attempt tool=generate_pdf_report attempt=%s/%s timeout_seconds=%s elapsed_ms=0 endpoint=%s dag_id=%s progress_seen=false',
            attempt,
            attempts,
            timeout,
            url,
            dag_id,
        )
        try:
            result = await with_deadline(
                generate_pdf_report(url=url, dag_id=dag_id, run_id=run_id, progress_handler=progress_handler),
                timeout,
            )
            mcp_status = _mcp_status(result)
            mcp_message = _message(result, '')
            logger.info(
                'MCP attempt responded tool=generate_pdf_report attempt=%s/%s timeout_seconds=%s elapsed_ms=%s endpoint=%s dag_id=%s progress_seen=%s mcp_status=%s mcp_message=%s run_id_received=%s has_report=%s mcp_errors=%s mcp_warnings=%s',
                attempt,
                attempts,
                timeout,
                _elapsed_ms(started),
                url,
                dag_id,
                progress_seen,
                mcp_status,
                mcp_message,
                bool(_optional_string(result.get('task_id') or result.get('run_id'))),
                bool(_optional_string(result.get('report_pdf'))),
                len(_string_list(result.get('errors'))),
                len(_string_list(result.get('warnings'))),
            )
            await _append_attempt_log(job_id, _attempt_log_entry(
                tool='generate_pdf_report',
                attempt=attempt,
                attempts=attempts,
                timeout=timeout,
                elapsed_ms=_elapsed_ms(started),
                url=url,
                dag_id=dag_id,
                progress_seen=progress_seen,
                status='remote_failed' if mcp_status == 'failed' else 'succeeded',
                mcp_result=result,
            ))
            return result
        except TimeoutError as exc:
            last_exc = exc
            diagnostics = await collect_endpoint_diagnostics(url)
            error = f'MCP generate_pdf_report timed out after {timeout} seconds.'
            logger.warning(
                'MCP attempt timed out tool=generate_pdf_report attempt=%s/%s timeout_seconds=%s elapsed_ms=%s endpoint=%s dag_id=%s progress_seen=%s error=%s diagnostics=%s',
                attempt,
                attempts,
                timeout,
                _elapsed_ms(started),
                url,
                dag_id,
                progress_seen,
                error,
                diagnostics,
            )
            await _append_attempt_log(job_id, _attempt_log_entry(
                tool='generate_pdf_report',
                attempt=attempt,
                attempts=attempts,
                timeout=timeout,
                elapsed_ms=_elapsed_ms(started),
                url=url,
                dag_id=dag_id,
                progress_seen=progress_seen,
                status='timed_out',
                error=error,
                diagnostics=diagnostics,
            ))
            if attempt < attempts:
                await _schedule_next_attempt(job_id, timeout, attempt + 1, attempts, timed_out=True)
                await asyncio.sleep(_retry_delay_before_attempt(attempt + 1))
        except Exception as exc:
            last_exc = exc
            diagnostics = await collect_endpoint_diagnostics(url)
            error = _error_text(exc)
            logger.exception(
                'MCP attempt failed tool=generate_pdf_report attempt=%s/%s timeout_seconds=%s elapsed_ms=%s endpoint=%s dag_id=%s progress_seen=%s error=%s diagnostics=%s',
                attempt,
                attempts,
                timeout,
                _elapsed_ms(started),
                url,
                dag_id,
                progress_seen,
                error,
                diagnostics,
            )
            await _append_attempt_log(job_id, _attempt_log_entry(
                tool='generate_pdf_report',
                attempt=attempt,
                attempts=attempts,
                timeout=timeout,
                elapsed_ms=_elapsed_ms(started),
                url=url,
                dag_id=dag_id,
                progress_seen=progress_seen,
                status='failed',
                error=error,
                diagnostics=diagnostics,
            ))
            if attempt < attempts:
                await _schedule_next_attempt(job_id, timeout, attempt + 1, attempts, timed_out=False)
                await asyncio.sleep(_retry_delay_before_attempt(attempt + 1))

    if isinstance(last_exc, TimeoutError):
        raise RuntimeError(
            f'MCP generate_pdf_report timed out after {attempts} attempts of {timeout} seconds. '
            'Remote run_id is known; можно повторить запрос отчёта позже без повторной отправки PDF.'
        ) from last_exc
    message = f'MCP generate_pdf_report failed after {attempts} attempts.'
    if last_exc:
        message = f'{message} Last error: {last_exc}'
    raise RuntimeError(f'{message} Remote run_id is known; можно повторить запрос отчёта позже без повторной отправки PDF.') from last_exc


async def _schedule_next_attempt(job_id: str, timeout: int, next_attempt: int, attempts: int, *, timed_out: bool) -> None:
    progress_message = (
        f'MCP не ответил за {timeout} с. Повторяем попытку {next_attempt}/{attempts}.'
        if timed_out else
        f'MCP вернул ошибку. Повторяем попытку {next_attempt}/{attempts}.'
    )
    await update_normcontrol_job(job_id, {
        'attempts': next_attempt,
        'maxAttempts': attempts,
        'timeoutSeconds': timeout,
        'progressMessage': progress_message,
    })


async def _append_attempt_log(job_id: str, entry: dict[str, Any]) -> None:
    current = await get_normcontrol_job(job_id)
    if not current:
        return
    raw_logs = current.get('attemptLogs')
    attempt_logs = [item for item in raw_logs if isinstance(item, dict)] if isinstance(raw_logs, list) else []
    attempt_logs.append(entry)
    await update_normcontrol_job(job_id, {'attemptLogs': attempt_logs[-50:]})


def _attempt_log_entry(
    *,
    tool: str,
    attempt: int,
    attempts: int,
    timeout: int,
    elapsed_ms: int,
    url: str,
    dag_id: str,
    progress_seen: bool,
    status: str,
    error: str | None = None,
    diagnostics: dict[str, str] | None = None,
    mcp_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        'at': now_iso(),
        'tool': tool,
        'attempt': attempt,
        'maxAttempts': attempts,
        'timeoutSeconds': timeout,
        'elapsedMs': elapsed_ms,
        'endpoint': url,
        'dagId': dag_id,
        'progressSeen': progress_seen,
        'status': status,
    }
    if error:
        entry['error'] = _bounded_text(error)
    if diagnostics:
        entry['diagnostics'] = {key: _bounded_text(value, limit=1200) for key, value in diagnostics.items() if isinstance(value, str)}
    if mcp_result:
        entry.update(_mcp_result_log_patch(mcp_result))
    return entry


def _mcp_result_log_patch(result: dict[str, Any]) -> dict[str, Any]:
    errors = _string_list(result.get('errors'))
    warnings = _string_list(result.get('warnings'))
    message = _message(result, '')
    return {
        'mcpStatus': _mcp_status(result),
        'mcpMessage': _bounded_text(message, limit=2000) if message else '',
        'mcpErrorsCount': len(errors),
        'mcpWarningsCount': len(warnings),
        'mcpErrors': [_bounded_text(item, limit=2000) for item in errors[:50]],
        'mcpWarnings': [_bounded_text(item, limit=2000) for item in warnings[:50]],
        'runIdReceived': bool(_optional_string(result.get('task_id') or result.get('run_id'))),
        'reportPdfReceived': bool(_optional_string(result.get('report_pdf'))),
        'outputPdfReceived': bool(_optional_string(result.get('output_pdf'))),
    }


async def _record_progress(job_id: str, progress: float, total: float | None, message: str | None, *, phase: str, attempt: int | None = None) -> None:
    current = await get_normcontrol_job(job_id)
    if not current or current.get('status') not in {'submitting', 'running', 'queued_report', 'reporting'}:
        return
    if attempt is not None:
        try:
            current_attempt = int(current.get('attempts') or 0)
        except (TypeError, ValueError):
            return
        if current_attempt != attempt:
            return
    pct = _progress_percent(progress, total)
    if phase == 'report':
        status = 'reporting'
        pct = max(65, pct)
    else:
        status = 'submitting' if pct < 10 else 'running'
    logger.info('Normcontrol progress job_id=%s phase=%s progress=%s total=%s pct=%s message=%s', job_id, phase, progress, total, pct, message)
    await update_normcontrol_job(job_id, {
        'status': status,
        'progress': min(95, pct),
        'progressMessage': (message or '').strip() or current.get('progressMessage') or 'MCP обрабатывает документ.',
    })


async def _record_submit_result(job_id: str, result: dict[str, Any]) -> None:
    run_id = _optional_string(result.get('task_id') or result.get('run_id'))
    patch = _result_patch(result)
    if run_id:
        patch.update({'taskId': run_id, 'runId': run_id})
    await update_normcontrol_job(job_id, patch)


async def _fail_job(job_id: str, exc: BaseException) -> None:
    logger.exception('Normcontrol job failed job_id=%s', job_id)
    await update_normcontrol_job(job_id, {
        'status': 'failed',
        'progress': 100,
        'progressMessage': 'Нормоконтроль остановлен из-за технической ошибки.',
        'finishedAt': now_iso(),
        'error': str(exc),
    })


def _result_patch(result: dict[str, Any]) -> dict[str, Any]:
    return {
        'mcpStatus': _mcp_status(result),
        'message': _message(result, ''),
        'errors': _string_list(result.get('errors')),
        'warnings': _string_list(result.get('warnings')),
    }


def _progress_percent(progress: float, total: float | None) -> int:
    try:
        value = (float(progress) / float(total) * 100) if total else float(progress)
    except (TypeError, ValueError, ZeroDivisionError):
        value = 0
    if total is None and 0 < value <= 1:
        value *= 100
    return max(0, min(100, round(value)))


def _mcp_status(result: dict[str, Any]) -> str:
    status = str(result.get('status') or '').strip().lower()
    return status if status in {'queued', 'running', 'done', 'failed'} else ''


def _message(result: dict[str, Any], default: str) -> str:
    value = result.get('message')
    return value.strip() if isinstance(value, str) and value.strip() else default


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _error_text(exc: BaseException) -> str:
    parts: list[str] = []
    for item in _walk_exceptions(exc, set()):
        text = str(item).strip()
        parts.append(f'{item.__class__.__name__}: {text}' if text else item.__class__.__name__)
    return _bounded_text(' -> '.join(parts))


def _walk_exceptions(exc: BaseException, seen: set[int]) -> list[BaseException]:
    if id(exc) in seen:
        return []
    seen.add(id(exc))
    result = [exc]
    nested = getattr(exc, 'exceptions', None)
    if isinstance(nested, tuple):
        for item in nested:
            if isinstance(item, BaseException):
                result.extend(_walk_exceptions(item, seen))
    if exc.__cause__:
        result.extend(_walk_exceptions(exc.__cause__, seen))
    if exc.__context__:
        result.extend(_walk_exceptions(exc.__context__, seen))
    return result


def _bounded_text(value: str, *, limit: int = 8000) -> str:
    if len(value) <= limit:
        return value
    return f'{value[:limit]}…'


def _max_attempts(job: dict[str, Any]) -> int:
    try:
        return max(1, int(job.get('maxAttempts') or NORMCONTROL_MCP_ATTEMPTS))
    except (TypeError, ValueError):
        return NORMCONTROL_MCP_ATTEMPTS


def _timeout_seconds(job: dict[str, Any]) -> int:
    try:
        return max(1, int(job.get('timeoutSeconds') or NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _retry_delay_before_attempt(attempt: int) -> int:
    if attempt <= 1:
        return 0
    if attempt == 2:
        return 2
    if attempt == 3:
        return 5
    return 5
