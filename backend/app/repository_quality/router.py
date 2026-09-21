from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form
from fastapi.responses import FileResponse, JSONResponse, Response

from ..config import REPOSITORY_QUALITY_RUNS_DIR
from ..reproducibility.diagnostics import llm_access, repository_access, runtime_status
from ..util import now_iso
from .queue import cancel_repository_quality_job, start_repository_quality_queue
from .runner import configured_base_url, configured_model, osa_installed
from .store import (
    create_repository_quality_job,
    delete_repository_quality_job,
    get_repository_quality_job,
    list_repository_quality_jobs,
    update_repository_quality_job,
)

router = APIRouter(prefix="/api/repository-quality", tags=["repository-quality"])


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _allowed_repository_hosts() -> set[str]:
    raw = os.getenv(
        "REPOSITORY_QUALITY_REPOSITORY_HOSTS",
        os.getenv(
            "REPRODUCIBILITY_REPOSITORY_HOSTS",
            "github.com,gitlab.com,gitverse.ru,sourcecraft.dev,git.sourcecraft.dev",
        ),
    )
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _validate_repository(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return "Некорректная ссылка на репозиторий."
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "Укажите http(s)-ссылку на репозиторий."
    host = parsed.hostname.lower()
    if parsed.password or (parsed.username and not (host == "git.sourcecraft.dev" and parsed.username == "git")):
        return "Не передавайте токены или пароль в URL репозитория."
    allowed = _allowed_repository_hosts()
    if allowed and host not in allowed:
        return f"Поддерживаются репозитории: {', '.join(sorted(allowed))}."
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        return "Ссылка должна указывать на конкретный репозиторий (owner/repository)."
    return None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _job_view(job: dict[str, object]) -> dict[str, object]:
    result_path = Path(str(job.get("resultPath") or "")) if job.get("resultPath") else None
    log_path = Path(str(job.get("runDir") or "")) / "osa.log"
    return {
        **job,
        "resultAvailable": bool(result_path and result_path.exists()),
        "logAvailable": log_path.exists(),
    }


@router.get("/status")
async def repository_quality_status():
    has_key = bool(os.getenv("OPENROUTER_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip())
    runtime = await asyncio.to_thread(runtime_status, include_cuda=False)
    return {
        "ok": osa_installed() and has_key and bool(runtime.get("git", {}).get("installed")),
        "osaInstalled": osa_installed(),
        "osaVersion": runtime.get("osaVersion"),
        "llmConfigured": has_key,
        "model": configured_model(),
        "baseUrl": configured_base_url(),
        "pythonVersion": runtime.get("pythonVersion"),
        "gitInstalled": bool(runtime.get("git", {}).get("installed")),
        "gitVersion": runtime.get("git", {}).get("version"),
    }


@router.get("/preflight")
async def repository_quality_preflight(repository: str):
    repository = repository.strip()
    repository_error = _validate_repository(repository)
    if repository_error:
        return _error(400, repository_error)

    runtime_task = asyncio.to_thread(runtime_status, include_cuda=False)
    repository_task = asyncio.to_thread(repository_access, repository)
    llm_task = llm_access(configured_base_url(), configured_model())
    runtime, repo, llm = await asyncio.gather(runtime_task, repository_task, llm_task)
    git = runtime.get("git", {})
    checks = [
        {
            "id": "osa",
            "label": "OSA",
            "ok": bool(runtime.get("osaInstalled")),
            "blocking": True,
            "detail": f"osa_tool {runtime.get('osaVersion')}" if runtime.get("osaInstalled") else "Пакет osa_tool не установлен.",
        },
        {
            "id": "git",
            "label": "Git",
            "ok": bool(git.get("installed")),
            "blocking": True,
            "detail": str(git.get("version") or "git не найден в PATH."),
        },
        {
            "id": "repository",
            "label": "Репозиторий",
            "ok": bool(repo.get("ok")),
            "blocking": True,
            "detail": str(repo.get("detail") or repo.get("error") or "Проверка доступа завершена."),
        },
        {
            "id": "llm",
            "label": "LLM",
            "ok": bool(llm.get("ok")),
            "blocking": True,
            "detail": str(llm.get("detail") or llm.get("error") or configured_model()),
        },
    ]
    return {
        "ok": all(bool(item["ok"]) for item in checks if item["blocking"]),
        "checks": checks,
        "warnings": [],
        "model": configured_model(),
        "baseUrl": configured_base_url(),
    }


@router.get("/jobs")
async def repository_quality_jobs():
    return [_job_view(job) for job in await list_repository_quality_jobs()]


@router.get("/jobs/{job_id}")
async def repository_quality_job(job_id: str):
    job = await get_repository_quality_job(job_id)
    if not job:
        return _error(404, "Проверка не найдена.")
    return _job_view(job)


@router.post("/jobs")
async def create_repository_quality_job_endpoint(repository: str = Form(...)):
    repository = repository.strip()
    repository_error = _validate_repository(repository)
    if repository_error:
        return _error(400, repository_error)

    job_id = uuid.uuid4().hex
    run_dir = REPOSITORY_QUALITY_RUNS_DIR / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    now = now_iso()
    job = {
        "id": job_id,
        "repository": repository,
        "model": configured_model(),
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": None,
        "status": "queued",
        "progress": 0,
        "progressMessage": "Проверка добавлена в очередь.",
        "attempts": 1,
        "runDir": str(run_dir.resolve()),
        "resultPath": None,
        "resultAvailable": False,
        "error": None,
    }
    await create_repository_quality_job(job)
    start_repository_quality_queue()
    return _job_view(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_repository_quality_job_endpoint(job_id: str):
    job = await cancel_repository_quality_job(job_id)
    if not job:
        return _error(404, "Проверка не найдена.")
    return _job_view(job)


@router.post("/jobs/{job_id}/retry")
async def retry_repository_quality_job(job_id: str):
    current = await get_repository_quality_job(job_id)
    if not current:
        return _error(404, "Проверка не найдена.")
    if current.get("status") in {"queued", "running", "cancelling"}:
        return _error(409, "Эта проверка ещё выполняется.")
    run_dir = Path(str(current.get("runDir") or ""))
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    updated = await update_repository_quality_job(
        job_id,
        {
            "status": "queued",
            "progress": 0,
            "progressMessage": "Повторная проверка добавлена в очередь.",
            "startedAt": None,
            "finishedAt": None,
            "error": None,
            "resultPath": None,
            "resultAvailable": False,
            "attempts": int(current.get("attempts") or 1) + 1,
        },
    )
    start_repository_quality_queue()
    return _job_view(updated or current)


@router.delete("/jobs/{job_id}")
async def remove_repository_quality_job(job_id: str):
    current = await get_repository_quality_job(job_id)
    if not current:
        return Response(status_code=204)
    if current.get("status") in {"queued", "running", "cancelling"}:
        return _error(409, "Сначала остановите выполняющуюся проверку.")
    removed = await delete_repository_quality_job(job_id)
    if removed:
        run_dir = Path(str(removed.get("runDir") or ""))
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
    return Response(status_code=204)


@router.get("/jobs/{job_id}/result")
async def repository_quality_result(job_id: str):
    job = await get_repository_quality_job(job_id)
    if not job:
        return _error(404, "Проверка не найдена.")
    result_path = Path(str(job.get("resultPath") or ""))
    if not result_path.exists():
        return _error(404, "Результат ещё не сформирован.")
    value = _read_json(result_path)
    if value is None:
        return _error(500, "Не удалось прочитать JSON-отчёт OSA.")
    return value


@router.get("/jobs/{job_id}/result.json")
async def repository_quality_result_json(job_id: str):
    job = await get_repository_quality_job(job_id)
    if not job:
        return _error(404, "Проверка не найдена.")
    result_path = Path(str(job.get("resultPath") or ""))
    if not result_path.exists():
        return _error(404, "Результат ещё не сформирован.")
    return FileResponse(
        result_path,
        media_type="application/json",
        filename="repository-quality-report.json",
    )


@router.get("/jobs/{job_id}/log")
async def repository_quality_log(job_id: str):
    job = await get_repository_quality_job(job_id)
    if not job:
        return _error(404, "Проверка не найдена.")
    path = Path(str(job.get("runDir") or "")) / "osa.log"
    if not path.exists():
        return {"text": "", "available": False, "size": 0, "truncated": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    limit = 120_000
    truncated = len(text) > limit
    return {
        "text": text[-limit:] if truncated else text,
        "available": True,
        "size": path.stat().st_size,
        "truncated": truncated,
    }


@router.get("/jobs/{job_id}/log.txt")
async def repository_quality_log_download(job_id: str):
    job = await get_repository_quality_job(job_id)
    if not job:
        return _error(404, "Проверка не найдена.")
    path = Path(str(job.get("runDir") or "")) / "osa.log"
    if not path.exists():
        return _error(404, "Лог ещё не сформирован.")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename="repository-quality-osa.log")
