from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..util import now_iso
from .runner import run_repository_quality
from .store import get_repository_quality_job, list_repository_quality_jobs, update_repository_quality_job

_queue_task: asyncio.Task[None] | None = None
_queue_lock = asyncio.Lock()
_running_tasks: dict[str, asyncio.Task[Any]] = {}


def start_repository_quality_queue() -> None:
    global _queue_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _queue_task is None or _queue_task.done():
        _queue_task = loop.create_task(_run_queue(), name="osa-edu-repository-quality-queue")


async def cancel_repository_quality_job(job_id: str) -> dict[str, Any] | None:
    current = await get_repository_quality_job(job_id)
    if not current:
        return None
    status = str(current.get("status") or "")
    if status in {"completed", "failed", "cancelled"}:
        return current
    if status == "queued":
        return await update_repository_quality_job(
            job_id,
            {
                "status": "cancelled",
                "progressMessage": "Проверка отменена до запуска.",
                "finishedAt": now_iso(),
            },
        )

    await update_repository_quality_job(
        job_id,
        {"status": "cancelling", "progressMessage": "Останавливаем OSA…"},
    )
    task = _running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    return await get_repository_quality_job(job_id)


async def _run_queue() -> None:
    async with _queue_lock:
        while True:
            jobs = await list_repository_quality_jobs()
            queued = [item for item in jobs if item.get("status") == "queued"]
            job = min(queued, key=lambda item: str(item.get("createdAt") or "")) if queued else None
            if not job:
                return
            await _perform_job(job)


async def _perform_job(job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    output_dir = Path(str(job.get("runDir") or ""))
    await update_repository_quality_job(
        job_id,
        {
            "status": "running",
            "progress": 5,
            "progressMessage": "Запускаем проверку качества репозитория.",
            "startedAt": now_iso(),
            "finishedAt": None,
            "error": None,
            "resultPath": None,
            "resultAvailable": False,
        },
    )

    async def on_progress(progress: int, message: str) -> None:
        current = await get_repository_quality_job(job_id)
        if not current or current.get("status") in {"cancelled", "cancelling"}:
            raise asyncio.CancelledError
        await update_repository_quality_job(
            job_id,
            {
                "status": "running",
                "progress": max(1, min(99, int(progress))),
                "progressMessage": message,
            },
        )

    try:
        task = asyncio.create_task(
            run_repository_quality(
                str(job.get("repository") or ""),
                output_dir,
                on_progress=on_progress,
            ),
            name=f"osa-edu-repository-quality-{job_id}",
        )
        _running_tasks[job_id] = task
        _result, result_path, log_tail = await task
        await update_repository_quality_job(
            job_id,
            {
                "status": "completed",
                "progress": 100,
                "progressMessage": "Проверка качества репозитория завершена.",
                "resultPath": str(result_path.resolve()),
                "resultAvailable": True,
                "logTail": log_tail,
                "error": None,
                "finishedAt": now_iso(),
            },
        )
    except asyncio.CancelledError:
        await update_repository_quality_job(
            job_id,
            {
                "status": "cancelled",
                "progressMessage": "Проверка остановлена.",
                "error": None,
                "finishedAt": now_iso(),
            },
        )
    except Exception as exc:
        current = await get_repository_quality_job(job_id)
        last_progress = int((current or {}).get("progress") or 0)
        raw_error = str(exc).strip()
        first_line = raw_error.splitlines()[0] if raw_error else "Без текста ошибки; смотрите osa.log."
        await update_repository_quality_job(
            job_id,
            {
                "status": "failed",
                "progress": max(1, min(99, last_progress)),
                "progressMessage": "OSA остановилась с ошибкой. Откройте подробный лог.",
                "error": f"{type(exc).__name__}: {first_line}",
                "finishedAt": now_iso(),
            },
        )
    finally:
        _running_tasks.pop(job_id, None)
