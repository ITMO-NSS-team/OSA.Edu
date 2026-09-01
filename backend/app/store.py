from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .config import JOBS_FILE
from .util import now_iso

_lock = asyncio.Lock()


def _read_sync() -> list[dict[str, Any]]:
    if not JOBS_FILE.exists():
        return []
    try:
        value = json.loads(JOBS_FILE.read_text(encoding='utf-8'))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_sync(jobs: list[dict[str, Any]]) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f'{JOBS_FILE}.tmp')
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, JOBS_FILE)


async def list_jobs() -> list[dict[str, Any]]:
    async with _lock:
        return _read_sync()


async def get_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        return next((j for j in _read_sync() if j.get('id') == job_id), None)


async def create_jobs(new_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    async with _lock:
        jobs = _read_sync()
        jobs = [*new_jobs, *jobs]
        _write_sync(jobs)
    return new_jobs


async def update_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
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


async def delete_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read_sync()
        removed = next((j for j in jobs if j.get('id') == job_id), None)
        if removed is None:
            return None
        _write_sync([j for j in jobs if j.get('id') != job_id])
        return removed


async def recover_interrupted_jobs() -> None:
    async with _lock:
        jobs = _read_sync()
        changed = False
        for job in jobs:
            status = job.get('status')
            if status in {'extracting', 'mapping'}:
                job.update(status='queued', progress=0, progressMessage='Сервер перезапущен. Построение структуры будет запущено заново.', error='Сервер был перезапущен; построение структуры возвращено в очередь.', updatedAt=now_iso())
                changed = True
            elif status in {'checking', 'queued_check'}:
                job.update(status='queued_check', progress=32, progressMessage='Сервер перезапущен. Проверка правил продолжится по подтверждённой структуре.', error='Сервер был перезапущен; проверка возвращена в очередь.', updatedAt=now_iso())
                changed = True
        if changed:
            _write_sync(jobs)
