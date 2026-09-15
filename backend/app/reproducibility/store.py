from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ..config import REPRODUCIBILITY_JOBS_FILE
from ..util import now_iso

_lock = asyncio.Lock()
ACTIVE_STATUSES = {"queued", "running", "cancelling"}


def _read_sync() -> list[dict[str, Any]]:
    if not REPRODUCIBILITY_JOBS_FILE.exists():
        return []
    try:
        value = json.loads(REPRODUCIBILITY_JOBS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_sync(jobs: list[dict[str, Any]]) -> None:
    REPRODUCIBILITY_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{REPRODUCIBILITY_JOBS_FILE}.tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REPRODUCIBILITY_JOBS_FILE)


async def list_reproducibility_jobs() -> list[dict[str, Any]]:
    async with _lock:
        return _read_sync()


async def get_reproducibility_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        return next((job for job in _read_sync() if job.get("id") == job_id), None)


async def create_reproducibility_job(job: dict[str, Any]) -> dict[str, Any]:
    async with _lock:
        jobs = _read_sync()
        _write_sync([job, *jobs])
    return job


async def update_reproducibility_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
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


async def delete_reproducibility_job(job_id: str) -> dict[str, Any] | None:
    async with _lock:
        jobs = _read_sync()
        removed = next((job for job in jobs if job.get("id") == job_id), None)
        if removed is None:
            return None
        _write_sync([job for job in jobs if job.get("id") != job_id])
        return removed


async def recover_reproducibility_jobs() -> None:
    async with _lock:
        jobs = _read_sync()
        changed = False
        for job in jobs:
            status = str(job.get("status") or "")
            if status == "queued":
                continue
            if status in {"running", "cancelling"}:
                job.update(
                    status="failed",
                    progress=100,
                    progressMessage="Проверка была остановлена перезапуском сервера.",
                    error="Backend был перезапущен во время выполнения OSA. Запустите проверку повторно.",
                    finishedAt=now_iso(),
                    updatedAt=now_iso(),
                )
                changed = True
        if changed:
            _write_sync(jobs)
