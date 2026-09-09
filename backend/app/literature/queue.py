from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..util import now_iso
from .service import check_literature
from .store import get_literature_job, list_literature_jobs, update_literature_job

_queue_task: asyncio.Task[None] | None = None
_queue_lock = asyncio.Lock()
_running_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}


def start_literature_queue() -> None:
    global _queue_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _queue_task is None or _queue_task.done():
        _queue_task = loop.create_task(_run_queue(), name="osa-edu-literature-queue")


async def cancel_literature_job(job_id: str) -> dict[str, Any] | None:
    current = await get_literature_job(job_id)
    if not current:
        return None
    if current.get("status") in {"done", "failed", "cancelled"}:
        return current
    if current.get("status") == "queued":
        return await update_literature_job(
            job_id,
            {
                "status": "cancelled",
                "progressMessage": "Проверка отменена до запуска.",
                "finishedAt": now_iso(),
            },
        )

    await update_literature_job(job_id, {"status": "cancelling", "progressMessage": "Останавливаем проверку…"})
    task = _running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    return await get_literature_job(job_id)


async def _run_queue() -> None:
    async with _queue_lock:
        while True:
            jobs = await list_literature_jobs()
            queued = [item for item in jobs if item.get("status") == "queued"]
            job = min(queued, key=lambda item: str(item.get("createdAt") or "")) if queued else None
            if not job:
                return
            await _perform_job(job)


async def _perform_job(job: dict[str, Any]) -> None:
    job_id = str(job["id"])
    path = Path(str(job.get("filePath") or ""))
    if not path.exists():
        await update_literature_job(
            job_id,
            {
                "status": "failed",
                "progress": 100,
                "progressMessage": "Исходный PDF не найден.",
                "error": "Исходный PDF не найден. Загрузите работу заново.",
                "finishedAt": now_iso(),
            },
        )
        return

    await update_literature_job(
        job_id,
        {
            "status": "running",
            "progress": 1,
            "progressMessage": "Подготавливаем документ.",
            "startedAt": now_iso(),
            "finishedAt": None,
            "error": None,
            "result": None,
        },
    )

    async def on_progress(progress: int, message: str) -> None:
        current = await get_literature_job(job_id)
        if not current or current.get("status") in {"cancelled", "cancelling"}:
            raise asyncio.CancelledError
        await update_literature_job(
            job_id,
            {
                "status": "running",
                "progress": max(1, min(99, int(progress))),
                "progressMessage": message,
            },
        )

    try:
        pdf_bytes = await asyncio.to_thread(path.read_bytes)
        task = asyncio.create_task(
            check_literature(
                pdf_bytes,
                str(job.get("originalName") or path.name),
                model=str(job.get("model") or "") or None,
                on_progress=on_progress,
            ),
            name=f"osa-edu-literature-{job_id}",
        )
        _running_tasks[job_id] = task
        result = await task
        current = await get_literature_job(job_id)
        if current and current.get("status") in {"cancelled", "cancelling"}:
            await update_literature_job(
                job_id,
                {"status": "cancelled", "progressMessage": "Проверка остановлена.", "finishedAt": now_iso()},
            )
            return
        await update_literature_job(
            job_id,
            {
                "status": "done",
                "progress": 100,
                "progressMessage": "Проверка литературы завершена.",
                "result": result,
                "error": None,
                "finishedAt": now_iso(),
            },
        )
    except asyncio.CancelledError:
        await update_literature_job(
            job_id,
            {
                "status": "cancelled",
                "progressMessage": "Проверка остановлена.",
                "finishedAt": now_iso(),
                "error": None,
            },
        )
    except Exception as exc:
        await update_literature_job(
            job_id,
            {
                "status": "failed",
                "progress": 100,
                "progressMessage": "Проверка остановлена из-за ошибки.",
                "error": str(exc),
                "finishedAt": now_iso(),
            },
        )
    finally:
        _running_tasks.pop(job_id, None)
