from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from ..config import MAX_FILE_SIZE_MB, REPRODUCIBILITY_RUNS_DIR, REPRODUCIBILITY_UPLOADS_DIR
from ..llm.host_llm import host_provider_status
from ..util import now_iso
from .diagnostics import llm_access, repository_access, runtime_status
from .job_queue import cancel_reproducibility_job, start_reproducibility_queue
from .runner import configured_base_url, configured_model, osa_installed, use_host_llm
from .store import (
    create_reproducibility_job,
    delete_reproducibility_job,
    get_reproducibility_job,
    list_reproducibility_jobs,
    update_reproducibility_job,
)

router = APIRouter(prefix="/api/reproducibility", tags=["reproducibility"])


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def _allowed_repository_hosts() -> set[str]:
    raw = os.getenv(
        "REPRODUCIBILITY_REPOSITORY_HOSTS",
        "github.com,gitlab.com,gitverse.ru,sourcecraft.dev,git.sourcecraft.dev",
    )
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


async def _llm_ready_status(*, force: bool = False) -> tuple[bool, dict[str, object] | None]:
    if use_host_llm():
        status = await asyncio.to_thread(host_provider_status, force=force)
        return bool(status.get("authenticated")), status
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()), None


def _validate_repository(value: str) -> str | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return "Некорректная ссылка на репозиторий."
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "Укажите http(s)-ссылку на репозиторий."

    host = parsed.hostname.lower()
    # SourceCraft's official HTTPS clone URL contains the fixed git user
    # (https://git@git.sourcecraft.dev/owner/repo.git). It is not a secret.
    if parsed.password or (parsed.username and not (host == "git.sourcecraft.dev" and parsed.username == "git")):
        return "Не передавайте токены или пароль в URL репозитория."

    allowed = _allowed_repository_hosts()
    if allowed and host not in allowed:
        return f"Поддерживаются репозитории: {', '.join(sorted(allowed))}."
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        return "Ссылка должна указывать на конкретный репозиторий (owner/repository)."
    return None


def _apply_job_source_metadata(value: object, job: dict[str, object]) -> object:
    """Overlay user-facing source metadata for new and previously finished jobs."""
    if not isinstance(value, dict):
        return value
    meta = value.setdefault("meta", {})
    if not isinstance(meta, dict):
        return value
    source = meta.setdefault("source", {})
    if not isinstance(source, dict):
        return value
    repository = str(job.get("repository") or "").strip()
    if repository:
        source["repository"] = repository
    paper = source.setdefault("paper", {})
    original_name = str(job.get("originalName") or "").strip()
    if isinstance(paper, dict) and original_name:
        paper["path"] = original_name
    paper_claims = value.get("paper_claims")
    if isinstance(paper_claims, dict) and original_name:
        paper_claims["source_path"] = original_name
    return value


async def _save_upload(upload: UploadFile, target: Path) -> int:
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as stream:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                break
            stream.write(chunk)
    await upload.close()
    return size


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _claims_file(run_dir: Path) -> Path | None:
    candidates = [
        run_dir / "osa_native" / "paper_claims" / "claims.json",
        run_dir / "paper_claims" / "claims.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _sections_file(run_dir: Path) -> Path | None:
    candidates = [
        run_dir / "osa_native" / "paper_claims" / "sections.json",
        run_dir / "paper_claims" / "sections.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _artifact_snapshot(job: dict[str, object]) -> dict[str, object]:
    run_dir = Path(str(job.get("runDir") or ""))
    claims_path = _claims_file(run_dir)
    sections_path = _sections_file(run_dir)
    verification_candidates = [
        run_dir / "claim_verification" / "report.json",
        run_dir / "osa_native" / "claim_verification.json",
    ]
    verification_path = next((path for path in verification_candidates if path.exists()), verification_candidates[0])
    result_path = Path(str(job.get("resultPath") or run_dir / "paper_analysis.json"))
    log_path = run_dir / "osa.log"

    claims_count = None
    if claims_path and claims_path.exists():
        payload = _read_json(claims_path)
        if isinstance(payload, list):
            claims_count = len(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("claims"), list):
            claims_count = len(payload.get("claims") or [])

    section_count = None
    if sections_path and sections_path.exists():
        payload = _read_json(sections_path)
        if isinstance(payload, list):
            section_count = len(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("sections"), list):
            section_count = len(payload.get("sections") or [])

    verified_count = None
    if verification_path.exists():
        payload = _read_json(verification_path)
        if isinstance(payload, dict):
            verification_payload = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            if isinstance(verification_payload, dict) and isinstance(verification_payload.get("claims"), list):
                verified_count = len(verification_payload.get("claims") or [])

    return {
        "logAvailable": log_path.exists(),
        "sectionsAvailable": sections_path is not None and sections_path.exists(),
        "sectionCount": section_count,
        "claimsAvailable": claims_path is not None and claims_path.exists(),
        "claimsCount": claims_count,
        "claimsPath": str(claims_path.resolve()) if claims_path and claims_path.exists() else None,
        "verificationAvailable": verification_path.exists(),
        "verifiedClaimsCount": verified_count,
        "resultAvailable": result_path.exists(),
    }


def _job_actions(job: dict[str, object], artifacts: dict[str, object]) -> dict[str, bool | str | None]:
    status = str(job.get("status") or "")
    terminal = status in {"failed", "cancelled", "completed"}
    claims_available = bool(artifacts.get("claimsAvailable"))
    verification_available = bool(artifacts.get("verificationAvailable"))
    result_available = bool(artifacts.get("resultAvailable"))
    resume_stage = None
    if claims_available and not verification_available:
        resume_stage = "verification"
    elif bool(artifacts.get("sectionsAvailable")) and not claims_available:
        resume_stage = "claims"
    return {
        "retryFull": terminal and status != "completed",
        "resumeVerification": terminal and claims_available and not result_available,
        "downloadLog": bool(artifacts.get("logAvailable")),
        "openResult": result_available or bool(job.get("resultAvailable")),
        "resumeStage": resume_stage,
    }


def _job_view(job: dict[str, object]) -> dict[str, object]:
    artifacts = _artifact_snapshot(job)
    actions = _job_actions(job, artifacts)
    return {**job, "artifacts": artifacts, "availableActions": actions}


@router.get("/status")
async def reproducibility_status():
    host_mode = use_host_llm()
    llm_configured, host_status = await _llm_ready_status()
    runtime = await asyncio.to_thread(runtime_status, include_cuda=False)
    return {
        "ok": osa_installed() and llm_configured and bool(runtime.get("pythonSupportedForPaperClaims")) and bool(runtime.get("git", {}).get("installed")),
        "osaInstalled": osa_installed(),
        "osaVersion": runtime.get("osaVersion"),
        "llmConfigured": llm_configured,
        "llmProvider": "host" if host_mode else "openrouter",
        "host": host_status,
        "model": configured_model(),
        "baseUrl": configured_base_url(),
        "pythonVersion": runtime.get("pythonVersion"),
        "pythonSupportedForPaperClaims": runtime.get("pythonSupportedForPaperClaims"),
        "gitInstalled": bool(runtime.get("git", {}).get("installed")),
        "gitVersion": runtime.get("git", {}).get("version"),
        "windowsReloadWarning": sys.platform == "win32",
    }


@router.get("/preflight")
async def reproducibility_preflight(repository: str):
    repository = repository.strip()
    repository_error = _validate_repository(repository)
    if repository_error:
        return _error(400, repository_error)

    runtime_task = asyncio.to_thread(runtime_status, include_cuda=True)
    repository_task = asyncio.to_thread(repository_access, repository)
    host_mode = use_host_llm()
    llm_task = llm_access(configured_base_url(), configured_model(), use_host_llm=host_mode)
    runtime, repo, llm = await asyncio.gather(runtime_task, repository_task, llm_task)

    git = runtime.get("git", {})
    cuda = runtime.get("cuda", {})
    checks = [
        {
            "id": "osa",
            "label": "OSA",
            "ok": bool(runtime.get("osaInstalled")),
            "blocking": True,
            "detail": f"osa_tool {runtime.get('osaVersion')}" if runtime.get("osaInstalled") else "Пакет osa_tool не установлен.",
        },
        {
            "id": "python",
            "label": "Python",
            "ok": bool(runtime.get("pythonSupportedForPaperClaims")),
            "blocking": True,
            "detail": f"Python {runtime.get('pythonVersion')}; paper-claims требует 3.11–3.14.",
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
            "label": "Доступ к репозиторию",
            "ok": bool(repo.get("ok")),
            "blocking": True,
            "detail": str(repo.get("detail") or "Не удалось проверить репозиторий."),
        },
        {
            "id": "llm",
            "label": "Host LLM" if host_mode else "OpenRouter / LLM",
            "ok": bool(llm.get("ok")),
            "blocking": True,
            "detail": str(llm.get("detail") or ("Не удалось проверить Host LLM." if host_mode else "Не удалось проверить API key.")),
        },
        {
            "id": "cuda",
            "label": "CUDA",
            "ok": bool(cuda.get("available")),
            "blocking": False,
            "detail": (
                f"{cuda.get('device')} · PyTorch {cuda.get('torchVersion')} · CUDA runtime {cuda.get('runtime')}"
                if cuda.get("available")
                else str(cuda.get("error") or "CUDA недоступна; Marker сможет работать на CPU, но заметно медленнее.")
            ),
        },
    ]
    if "sourcecraft.dev" in repository.lower():
        sourcecraft_token = (
            os.getenv("SOURCECRAFT_TOKEN", "").strip()
            or os.getenv("GIT_TOKEN", "").strip()
        )
        checks.append(
            {
                "id": "sourcecraft-token",
                "label": "SourceCraft token",
                "ok": bool(sourcecraft_token),
                "blocking": False,
                "detail": (
                    "SOURCECRAFT_TOKEN/GIT_TOKEN настроен; приватные репозитории могут использовать авторизацию."
                    if sourcecraft_token
                    else "Токен не задан. Для публичного SourceCraft он не нужен; для приватного добавьте SOURCECRAFT_TOKEN в .env."
                ),
            }
        )

    blocking_ok = all(item["ok"] for item in checks if item["blocking"])
    warnings: list[str] = []
    if sys.platform == "win32":
        warnings.append("На Windows запускайте backend без uvicorn --reload: asyncio subprocess с reload может падать с NotImplementedError.")
    if not cuda.get("available"):
        warnings.append("CUDA не обязательна, но Marker на CPU может обрабатывать большую ВКР значительно дольше.")
    return {
        "ok": blocking_ok,
        "checks": checks,
        "warnings": warnings,
        "llmProvider": "host" if host_mode else "openrouter",
        "model": configured_model(),
        "baseUrl": configured_base_url(),
    }


@router.get("/jobs")
async def reproducibility_jobs():
    return [_job_view(job) for job in await list_reproducibility_jobs()]


@router.get("/jobs/{job_id}")
async def reproducibility_job(job_id: str):
    job = await get_reproducibility_job(job_id)
    return _job_view(job) if job else _error(404, "Проверка воспроизводимости не найдена.")


@router.get("/jobs/{job_id}/log")
async def reproducibility_log(job_id: str):
    job = await get_reproducibility_job(job_id)
    if not job:
        return _error(404, "Проверка воспроизводимости не найдена.")
    run_dir = Path(str(job.get("runDir") or ""))
    log_path = run_dir / "osa.log"
    if not log_path.exists():
        return {"text": "", "available": False, "size": 0}
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _error(500, f"Не удалось прочитать osa.log: {exc}")
    max_chars = 50000
    return {
        "text": text[-max_chars:],
        "available": True,
        "size": log_path.stat().st_size,
        "truncated": len(text) > max_chars,
    }


@router.get("/jobs/{job_id}/log.txt")
async def reproducibility_log_download(job_id: str):
    job = await get_reproducibility_job(job_id)
    if not job:
        return _error(404, "Проверка воспроизводимости не найдена.")
    log_path = Path(str(job.get("runDir") or "")) / "osa.log"
    if not log_path.exists():
        return _error(404, "osa.log ещё не создан.")
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _error(500, f"Не удалось прочитать osa.log: {exc}")
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="osa-{job_id}.log"'},
    )


@router.post("/jobs")
async def create_reproducibility_job_endpoint(
    repository: str = Form(...),
    file: UploadFile = File(...),
):
    repository = repository.strip()
    repository_error = _validate_repository(repository)
    if repository_error:
        await file.close()
        return _error(400, repository_error)

    if not osa_installed():
        await file.close()
        return _error(
            503,
            'OSA не установлена в backend. Установите `osa_tool[paper-claims]` или настройте REPRODUCIBILITY_OSA_COMMAND.',
        )

    llm_configured, host_status = await _llm_ready_status(force=True)
    if not llm_configured and use_host_llm():
        await file.close()
        detail = host_status.get("detail") if isinstance(host_status, dict) else None
        return _error(400, f"Host LLM не готов: {detail or 'провайдер не авторизован.'}")
    if not llm_configured:
        await file.close()
        return _error(400, "Для OSA не найден OPENROUTER_API_KEY/OPENAI_API_KEY в .env.")

    repo_access = await asyncio.to_thread(repository_access, repository)
    if not repo_access.get("ok"):
        await file.close()
        return _error(400, f"Нет git-доступа к репозиторию: {repo_access.get('detail')}")

    name = _safe_name(file.filename or "thesis.pdf")
    if not name.lower().endswith(".pdf"):
        await file.close()
        return _error(400, "Для проверки воспроизводимости загрузите PDF.")

    job_id = str(uuid.uuid4())
    target = REPRODUCIBILITY_UPLOADS_DIR / f"{job_id}.pdf"
    run_dir = REPRODUCIBILITY_RUNS_DIR / job_id
    try:
        size = await _save_upload(file, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        return _error(500, f"Не удалось сохранить PDF: {exc}")
    if size > MAX_FILE_SIZE_MB * 1024 * 1024:
        target.unlink(missing_ok=True)
        return _error(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ.")
    if size == 0:
        target.unlink(missing_ok=True)
        return _error(400, "Загружен пустой PDF.")

    created_at = now_iso()
    job = {
        "id": job_id,
        "originalName": name,
        "filePath": str(target.resolve()),
        "runDir": str(run_dir.resolve()),
        "logPath": str((run_dir / "osa.log").resolve()),
        "repository": repository,
        "size": size,
        "model": configured_model(),
        "createdAt": created_at,
        "updatedAt": created_at,
        "status": "queued",
        "progress": 0,
        "progressMessage": "Проверка добавлена в очередь OSA.",
        "resultPath": None,
        "resultAvailable": False,
        "error": None,
        "startedAt": None,
        "finishedAt": None,
        "runMode": "full",
        "claimsPath": None,
    }
    await create_reproducibility_job(job)
    start_reproducibility_queue()
    return JSONResponse(status_code=201, content=_job_view(job))


@router.get("/jobs/{job_id}/result")
async def reproducibility_result(job_id: str):
    job = await get_reproducibility_job(job_id)
    if not job:
        return _error(404, "Проверка воспроизводимости не найдена.")
    if job.get("status") != "completed" or not job.get("resultPath"):
        return _error(409, "paper_analysis.json ещё не готов.")
    path = Path(str(job["resultPath"]))
    if not path.exists():
        return _error(404, "paper_analysis.json не найден на сервере.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _error(500, f"Не удалось прочитать paper_analysis.json: {exc}")
    return _apply_job_source_metadata(value, job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_reproducibility_job_endpoint(job_id: str):
    job = await cancel_reproducibility_job(job_id)
    if not job:
        return _error(404, "Проверка воспроизводимости не найдена.")
    start_reproducibility_queue()
    return _job_view(job)


@router.post("/jobs/{job_id}/retry")
async def retry_reproducibility_job_endpoint(job_id: str):
    current = await get_reproducibility_job(job_id)
    if not current:
        return _error(404, "Проверка воспроизводимости не найдена.")
    if current.get("status") in {"queued", "running", "cancelling"}:
        return _error(409, "Эта проверка уже выполняется.")
    path = Path(str(current.get("filePath") or ""))
    if not path.exists():
        return _error(409, "Исходный PDF удалён. Загрузите работу заново.")
    run_dir = Path(str(current.get("runDir") or ""))
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    updated = await update_reproducibility_job(
        job_id,
        {
            "status": "queued",
            "progress": 0,
            "progressMessage": "Повторная проверка добавлена в очередь OSA.",
            "resultPath": None,
            "resultAvailable": False,
            "error": None,
            "startedAt": None,
            "finishedAt": None,
            "runMode": "full",
            "claimsPath": None,
        },
    )
    start_reproducibility_queue()
    return _job_view(updated) if updated else _error(500, "Не удалось подготовить повторный запуск.")


@router.post("/jobs/{job_id}/resume")
async def resume_reproducibility_job_endpoint(job_id: str):
    current = await get_reproducibility_job(job_id)
    if not current:
        return _error(404, "Проверка воспроизводимости не найдена.")
    if current.get("status") in {"queued", "running", "cancelling"}:
        return _error(409, "Эта проверка уже выполняется.")
    path = Path(str(current.get("filePath") or ""))
    if not path.exists():
        return _error(409, "Исходный PDF удалён. Загрузите работу заново.")
    run_dir = Path(str(current.get("runDir") or ""))
    claims_path = _claims_file(run_dir)
    if claims_path and claims_path.exists():
        updated = await update_reproducibility_job(
            job_id,
            {
                "status": "queued",
                "progress": 68,
                "progressMessage": "Возобновляем проверку с этапа проверки репозитория.",
                "resultPath": None,
                "resultAvailable": False,
                "error": None,
                "startedAt": None,
                "finishedAt": None,
                "runMode": "verification-only",
                "claimsPath": str(claims_path.resolve()),
            },
        )
        start_reproducibility_queue()
        return _job_view(updated) if updated else _error(500, "Не удалось возобновить проверку.")
    return _error(409, "Сохранённые claims не найдены, поэтому можно только запустить полную проверку заново.")


@router.delete("/jobs/{job_id}")
async def delete_reproducibility_job_endpoint(job_id: str):
    current = await get_reproducibility_job(job_id)
    if not current:
        return _error(404, "Проверка воспроизводимости не найдена.")
    if current.get("status") in {"running", "cancelling"}:
        return _error(409, "Сначала остановите текущую проверку.")
    removed = await delete_reproducibility_job(job_id)
    if removed:
        if removed.get("filePath"):
            Path(str(removed["filePath"])).unlink(missing_ok=True)
        if removed.get("runDir"):
            shutil.rmtree(Path(str(removed["runDir"])), ignore_errors=True)
    return Response(status_code=204)
