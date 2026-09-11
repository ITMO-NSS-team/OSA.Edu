from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..util import now_iso

LITERATURE_JOBS_FILE = DATA_DIR / "literature_jobs.json"
_lock = asyncio.Lock()


def _read_sync() -> list[dict[str, Any]]:
    if not LITERATURE_JOBS_FILE.exists():
        return []
    try:
        value = json.loads(LITERATURE_JOBS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_sync(jobs: list[dict[str, Any]]) -> None:
    LITERATURE_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{LITERATURE_JOBS_FILE}.tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, LITERATURE_JOBS_FILE)


async def list_literature_jobs() -> list[dict[str, Any]]:
    async with _lock:
        return _read_sync()


async def get_literature_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        return next((job for job in _read_sync() if job.get("id") == job_id), None)


async def create_literature_jobs(new_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    async with _lock:
        jobs = _read_sync()
        jobs = [*new_jobs, *jobs]
        _write_sync(jobs)
    return new_jobs


async def update_literature_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read_sync()
        for index, job in enumerate(jobs):
            if job.get("id") != job_id:
                continue
            updated = {**job, **patch, "updatedAt": now_iso()}
            jobs[index] = updated
            _write_sync(jobs)
            return updated
    return None


async def delete_literature_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read_sync()
        removed = next((job for job in jobs if job.get("id") == job_id), None)
        if removed is None:
            return None
        _write_sync([job for job in jobs if job.get("id") != job_id])
        return removed


async def recover_literature_jobs() -> None:
    async with _lock:
        jobs = _read_sync()
        changed = False
        for job in jobs:
            status = job.get("status")
            if status == "running":
                job.update(
                    status="queued",
                    progress=0,
                    progressMessage="Сервер перезапущен. Проверка литературы возвращена в очередь.",
                    error=None,
                    startedAt=None,
                    finishedAt=None,
                    updatedAt=now_iso(),
                )
                changed = True
            elif status == "cancelling":
                job.update(
                    status="cancelled",
                    progressMessage="Проверка остановлена.",
                    finishedAt=now_iso(),
                    updatedAt=now_iso(),
                )
                changed = True
        if changed:
            _write_sync(jobs)
