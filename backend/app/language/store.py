"""Persisted language-review jobs. Raw uploads never leave the uploads directory."""
from __future__ import annotations
import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any
from ..config import DATA_DIR
from ..util import now_iso

JOBS_FILE = DATA_DIR / 'language_jobs.json'
_lock = asyncio.Lock()


def _read() -> list[dict[str, Any]]:
    try:
        value = json.loads(JOBS_FILE.read_text('utf-8'))
        if not isinstance(value, list) or not all(isinstance(job, dict) and job.get('id') for job in value):
            raise ValueError('Invalid history schema')
        return value
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RuntimeError('История языковых проверок недоступна или повреждена; запись остановлена для сохранения данных.') from exc


def _write(jobs: list[dict[str, Any]]) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_FILE.with_suffix('.json.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(jobs, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, JOBS_FILE)


def public(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    value = copy.deepcopy({k: v for k, v in job.items() if k not in {'filePath', 'checkpoint'}})
    checkpoint = job.get('checkpoint') or {}
    value['canResume'] = not job.get('resumeError') and (
        not job.get('result') or bool(checkpoint.get('version') == 2 and (job.get('result') or {}).get('resumable', True))
    )
    return value


async def list_jobs() -> list[dict[str, Any]]:
    async with _lock:
        return [public(j) for j in _read()]


async def get_job(job_id: str, *, internal: bool = False) -> dict[str, Any] | None:
    async with _lock:
        job = next((j for j in _read() if j.get('id') == job_id), None)
        return job if internal else public(job)


async def create_job(job: dict[str, Any]) -> dict[str, Any]:
    async with _lock:
        jobs = _read()
        for existing in jobs:
            if (job.get('fileDigest') and existing.get('fileDigest') == job['fileDigest']
                and existing.get('model') == job['model']
                and existing.get('styleEnabled') == job['styleEnabled']
                and existing.get('includeDisputed', False) == job.get('includeDisputed', False)
                and existing.get('status') in {'queued', 'running', 'cancelling'}):
                return public(existing)
        _write([job, *jobs])
    return public(job)


async def update_job(job_id: str, patch: dict[str, Any], *, expected_statuses: set[str] | None = None) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read()
        for idx, job in enumerate(jobs):
            if job.get('id') == job_id:
                if expected_statuses is not None and job.get('status') not in expected_statuses:
                    return None
                jobs[idx] = {**job, **patch, 'updatedAt': now_iso()}
                _write(jobs)
                return public(jobs[idx])
    return None


async def delete_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read()
        removed = next((j for j in jobs if j.get('id') == job_id), None)
        if removed and removed.get('status') in {'running', 'cancelling'}:
            return None
        if removed:
            _write([j for j in jobs if j.get('id') != job_id])
        return removed


async def recover_jobs() -> None:
    async with _lock:
        try:
            jobs = _read()
        except RuntimeError:
            # Keep an unreadable history intact and disable this queue, rather
            # than preventing unrelated OSA workflows from starting.
            return
        changed = False
        for job in jobs:
            if job.get('status') in {'running', 'cancelling'}:
                checkpoint = job.get('checkpoint')
                if checkpoint and checkpoint.get('version') == 2:
                    from .service import result_from_checkpoint
                    try:
                        rebuilt = result_from_checkpoint(checkpoint)
                    except (KeyError, TypeError, ValueError):
                        job.update(status='partial' if job.get('result') else 'failed',
                                   resumeError=True, error='Повреждена контрольная точка; история сохранена, необходима новая проверка.',
                                   finishedAt=now_iso())
                        changed = True
                        continue
                    job['result'] = rebuilt
                    if rebuilt['complete']:
                        job.update(status='done', error=None, progress=100,
                                   progressMessage='Проверка завершена до перезапуска.', finishedAt=now_iso())
                        changed = True
                        continue
                job.update(status='partial' if job.get('result') else 'failed',
                           error='Проверка прервана перезапуском сервера. Можно повторить.',
                           progressMessage='Проверка прервана.', finishedAt=now_iso())
                if job.get('result'):
                    job['result']['complete'] = False
                    job['result']['status'] = 'partial'
                    job['result'].setdefault('warnings', []).append('Проверка прервана перезапуском сервера.')
                changed = True
        if changed:
            _write(jobs)
