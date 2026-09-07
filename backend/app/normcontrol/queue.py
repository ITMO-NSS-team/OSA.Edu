from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..config import (
    NORMCONTROL_DAG_ID,
    NORMCONTROL_HTTP_TIMEOUT_SECONDS,
    NORMCONTROL_MCP_URL,
    NORMCONTROL_REPORTS_DIR,
)
from ..util import now_iso
from .client import download_pdf, generate_pdf_report, submit_document
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
        async def progress_handler(progress: float, total: float | None, message: str | None) -> None:
            await _record_progress(job['id'], progress, total, message, phase='submit')

        logger.info('Starting normcontrol job id=%s file=%s dag_id=%s', job['id'], job.get('originalName'), job.get('dagId'))
        await update_normcontrol_job(job['id'], {
            'status': 'submitting',
            'progress': 1,
            'progressMessage': 'Отправляем PDF на сервер нормоконтроля.',
            'startedAt': now_iso(),
            'error': None,
        })
        result = await submit_document(
            url=job.get('mcpUrl') or NORMCONTROL_MCP_URL,
            dag_id=job.get('dagId') or NORMCONTROL_DAG_ID,
            pdf_path=source,
            progress_handler=progress_handler,
        )
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
    async def progress_handler(progress: float, total: float | None, message: str | None) -> None:
        await _record_progress(job_id, progress, total, message, phase='report')

    await update_normcontrol_job(job_id, {
        'status': 'reporting',
        'progress': 90,
        'progressMessage': 'Запрашиваем итоговый PDF-отчёт.',
        'error': None,
    })
    result = await generate_pdf_report(
        url=url,
        dag_id=dag_id,
        run_id=run_id,
        progress_handler=progress_handler,
    )
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


async def _record_progress(job_id: str, progress: float, total: float | None, message: str | None, *, phase: str) -> None:
    current = await get_normcontrol_job(job_id)
    if not current or current.get('status') not in {'submitting', 'running', 'queued_report', 'reporting'}:
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
