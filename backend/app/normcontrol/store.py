from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ..config import NORMCONTROL_JOBS_FILE
from ..util import now_iso

_lock = asyncio.Lock()

ACTIVE_STATUSES = {'queued', 'queued_report', 'submitting', 'running', 'reporting', 'downloading'}


def _read_sync() -> list[dict[str, Any]]:
    if not NORMCONTROL_JOBS_FILE.exists():
        return []
    try:
        value = json.loads(NORMCONTROL_JOBS_FILE.read_text(encoding='utf-8'))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_sync(jobs: list[dict[str, Any]]) -> None:
    NORMCONTROL_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f'{NORMCONTROL_JOBS_FILE}.tmp')
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, NORMCONTROL_JOBS_FILE)


async def list_normcontrol_jobs() -> list[dict[str, Any]]:
    async with _lock:
        return _read_sync()


async def get_normcontrol_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        return next((job for job in _read_sync() if job.get('id') == job_id), None)


async def create_normcontrol_job(job: dict[str, Any]) -> dict[str, Any]:
    async with _lock:
        jobs = _read_sync()
        _write_sync([job, *jobs])
    return job


async def update_normcontrol_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read_sync()
        for index, job in enumerate(jobs):
            if job.get('id') != job_id:
                continue
            updated = {**job, **patch, 'updatedAt': now_iso()}
            jobs[index] = updated
            _write_sync(jobs)
            return updated
    return None


async def delete_normcontrol_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read_sync()
        removed = next((job for job in jobs if job.get('id') == job_id), None)
        if removed is None:
            return None
        _write_sync([job for job in jobs if job.get('id') != job_id])
        return removed


async def recover_interrupted_normcontrol_jobs() -> None:
    async with _lock:
        jobs = _read_sync()
        changed = False
        for job in jobs:
            status = job.get('status')
            if status not in ACTIVE_STATUSES:
                continue
            if job.get('taskId') or job.get('runId'):
                job.update(
                    status='queued_report',
                    progress=max(65, int(job.get('progress') or 0)),
                    progressMessage='Сервер перезапущен. Повторно запрашиваем готовый отчёт нормоконтроля.',
                    error='Сервер был перезапущен; загрузка итогового отчёта будет повторена без повторной отправки PDF.',
                    updatedAt=now_iso(),
                )
            else:
                job.update(
                    status='failed',
                    progress=100,
                    progressMessage='Нормоконтроль остановлен после перезапуска сервера.',
                    error='Сервер был перезапущен до получения run_id от MCP. Состояние внешнего запуска неизвестно; загрузите PDF заново.',
                    finishedAt=now_iso(),
                    updatedAt=now_iso(),
                )
            changed = True
        if changed:
            _write_sync(jobs)
