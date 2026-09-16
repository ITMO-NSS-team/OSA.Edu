from __future__ import annotations

import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from .config import (
    APP_VERSION,
    MAX_FILE_SIZE_MB,
    NORMCONTROL_DAG_ID,
    NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS,
    NORMCONTROL_MCP_ATTEMPTS,
    NORMCONTROL_MCP_URL,
    NORMCONTROL_UPLOADS_DIR,
    UPLOADS_DIR,
    WEB_ORIGINS,
)
from .defaults import (
    DEFAULT_ADDITIONAL_CRITERIA,
    DEFAULT_MAP_PROMPT,
    DEFAULT_PROFILE,
    DEFAULT_PROMPT,
    MODELS,
    model_definition,
)
from .document.map_builder import ALLOWED_TYPES, refresh_map
from .extraction import read_extracted, save_extracted
from .literature.queue import start_literature_queue
from .literature.router import router as literature_router
from .literature.store import recover_literature_jobs
from .llm.rate_limiter import configured_rate_limits
from .normcontrol.client import inspect_server as inspect_normcontrol_server
from .normcontrol.queue import start_normcontrol_queue
from .normcontrol.store import (
    ACTIVE_STATUSES as ACTIVE_NORMCONTROL_STATUSES,
    create_normcontrol_job as persist_normcontrol_job,
    delete_normcontrol_job,
    get_normcontrol_job,
    list_normcontrol_jobs,
    recover_interrupted_normcontrol_jobs,
)
from .reproducibility.job_queue import start_reproducibility_queue
from .reproducibility.router import router as reproducibility_router
from .reproducibility.store import recover_reproducibility_jobs
from .pdf_reporting import report_to_pdf as developer_report_to_pdf
from .queue import start_queue
from .reporting import report_to_markdown
from .rules.registry import load_rule_registry
from .store import (
    create_jobs,
    delete_job,
    get_job,
    list_jobs,
    recover_interrupted_jobs,
    update_job,
)
from .user_pdf_reporting import report_to_user_pdf
from .util import map_is_confirmed, normalized_quote, now_iso


@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    NORMCONTROL_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    await recover_interrupted_jobs()
    await recover_interrupted_normcontrol_jobs()
    start_queue()
    start_normcontrol_queue()
    await recover_literature_jobs()
    await recover_reproducibility_jobs()
    start_literature_queue()
    start_reproducibility_queue()
    yield


app = FastAPI(title="OSA.Edu API", version=APP_VERSION, lifespan=lifespan)
app.include_router(literature_router)
app.include_router(reproducibility_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=WEB_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Некорректный запрос.", "details": exc.errors()},
    )


@app.get("/api/health")
async def health():
    registry = load_rule_registry()
    return {
        "ok": True,
        "models": MODELS,
        "configured": {
            "gemini": bool(
                (
                    os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
                ).strip()
            ),
            "openrouter": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
        },
        "defaults": {
            "prompt": DEFAULT_PROMPT,
            "mapPrompt": DEFAULT_MAP_PROMPT,
            "additionalCriteria": DEFAULT_ADDITIONAL_CRITERIA,
            "profile": DEFAULT_PROFILE,
        },
        "rateLimits": {
            "gemini": configured_rate_limits("gemini"),
            "openrouter": configured_rate_limits("openrouter"),
        },
        "knowledge": {
            "coreCount": len(registry["core"]),
            "softCount": len(registry["soft"]),
            "fullCount": len(registry["all"]),
            "retrieval": "После подтверждения карты точные правила проверяются кодом, языковые — через полный поиск коротких кандидатов и LLM-судью, содержательные — по назначенным разделам документа.",
        },
        "normcontrol": {
            "mcpUrl": NORMCONTROL_MCP_URL,
            "dagId": NORMCONTROL_DAG_ID,
            "mcpAttempts": NORMCONTROL_MCP_ATTEMPTS,
            "mcpAttemptTimeoutSeconds": NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS,
            "enabled": bool(NORMCONTROL_MCP_URL and NORMCONTROL_DAG_ID),
        },
    }


@app.get("/api/rules")
async def rules(profile: str = "core"):
    registry = load_rule_registry()
    return registry["all"] if profile == "full" else registry["core"]


@app.get("/api/jobs")
async def jobs():
    return await list_jobs()


@app.get("/api/normcontrol/jobs")
async def normcontrol_jobs():
    return await list_normcontrol_jobs()


@app.get("/api/normcontrol/status")
async def normcontrol_status():
    if not NORMCONTROL_MCP_URL:
        return _error(400, "NORMCONTROL_MCP_URL не настроен.")
    try:
        return await inspect_normcontrol_server(
            url=NORMCONTROL_MCP_URL,
            attempts=NORMCONTROL_MCP_ATTEMPTS,
            timeout_seconds=NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return _error(502, str(exc))


@app.get("/api/normcontrol/jobs/{job_id}")
async def normcontrol_job(job_id: str):
    job = await get_normcontrol_job(job_id)
    return job if job else _error(404, "Задача нормоконтроля не найдена.")


@app.post("/api/normcontrol/jobs")
async def create_normcontrol_job_endpoint(file: UploadFile = File(...)):
    original_name = _sanitize(file.filename or "document.pdf")
    suffix = Path(original_name).suffix.lower()
    if suffix != ".pdf":
        await file.close()
        return _error(400, "Для нормоконтроля поддерживаются только PDF.")
    if not NORMCONTROL_MCP_URL or not NORMCONTROL_DAG_ID:
        await file.close()
        return _error(400, "Интеграция нормоконтроля не настроена.")

    technical = (
        NORMCONTROL_UPLOADS_DIR
        / f"{int(__import__('time').time() * 1000)}-{uuid.uuid4()}.pdf"
    )
    try:
        size = await _save_upload(file, technical)
    except Exception as exc:
        technical.unlink(missing_ok=True)
        return _error(500, str(exc))
    if size > MAX_FILE_SIZE_MB * 1024 * 1024:
        technical.unlink(missing_ok=True)
        return _error(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ.")

    now = now_iso()
    job = {
        "id": str(uuid.uuid4()),
        "originalName": original_name,
        "filePath": str(technical.resolve()),
        "size": size,
        "createdAt": now,
        "updatedAt": now,
        "status": "queued",
        "progress": 0,
        "progressMessage": "PDF принят. Ожидаем отправку на сервер нормоконтроля.",
        "attempts": 0,
        "maxAttempts": NORMCONTROL_MCP_ATTEMPTS,
        "timeoutSeconds": NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS,
        "dagId": NORMCONTROL_DAG_ID,
        "mcpUrl": NORMCONTROL_MCP_URL,
        "message": "",
        "errors": [],
        "warnings": [],
        "attemptLogs": [],
    }
    await persist_normcontrol_job(job)
    start_normcontrol_queue()
    return JSONResponse(status_code=201, content=job)


@app.post("/api/normcontrol/jobs/{job_id}/retry")
async def retry_normcontrol_job(job_id: str):
    current = await get_normcontrol_job(job_id)
    if not current:
        return _error(404, "Задача нормоконтроля не найдена.")
    if current.get("status") in ACTIVE_NORMCONTROL_STATUSES:
        return _error(409, "Нормоконтроль уже выполняется.")
    if current.get("status") != "failed":
        return _error(409, "Повтор доступен только для задач с ошибкой.")

    run_id = str(current.get("runId") or current.get("taskId") or "").strip()
    source_exists = bool(current.get("filePath") and Path(current["filePath"]).exists())
    if not run_id and not source_exists:
        return _error(409, "Исходный PDF не найден. Загрузите документ заново.")

    patch = {
        "status": "queued_report" if run_id else "queued",
        "progress": 65 if run_id else 0,
        "progressMessage": "Повторно запрашиваем готовый отчёт без отправки PDF."
        if run_id
        else "Повторно отправляем PDF на сервер нормоконтроля.",
        "attempts": 0,
        "maxAttempts": NORMCONTROL_MCP_ATTEMPTS,
        "timeoutSeconds": NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS,
        "lastAttemptAt": None,
        "mcpStatus": "",
        "message": "",
        "errors": [],
        "warnings": [],
        "attemptLogs": [],
        "error": None,
        "finishedAt": None,
    }
    job = await update_normcontrol_job(job_id, patch)
    start_normcontrol_queue()
    return job if job else _error(404, "Задача нормоконтроля не найдена.")


@app.delete("/api/normcontrol/jobs/{job_id}")
async def remove_normcontrol_job(job_id: str):
    current = await get_normcontrol_job(job_id)
    if not current:
        return _error(404, "Задача нормоконтроля не найдена.")
    if current.get("status") in ACTIVE_NORMCONTROL_STATUSES:
        return _error(409, "Дождитесь завершения нормоконтроля перед удалением задачи.")
    job = await delete_normcontrol_job(job_id)
    if not job:
        return _error(404, "Задача нормоконтроля не найдена.")
    for raw in [job.get("filePath"), job.get("reportPath")]:
        if raw:
            try:
                Path(raw).unlink(missing_ok=True)
            except OSError:
                pass
    return Response(status_code=204)


@app.get("/api/normcontrol/jobs/{job_id}/report.pdf")
async def normcontrol_report_pdf(job_id: str):
    job = await get_normcontrol_job(job_id)
    if not job or not job.get("reportPath"):
        return _error(404, "Отчёт нормоконтроля ещё не готов.")
    path = Path(job["reportPath"])
    if not path.exists():
        return _error(404, "Файл отчёта нормоконтроля не найден.")
    filename = quote(f"{job['originalName']}-normcontrol-report.pdf")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/api/jobs")
async def create_job_endpoint(
    files: list[UploadFile] = File(...),
    model: str = Form(""),
    profile: str = Form("core"),
    prompt: str = Form(""),
    mapPrompt: str = Form(""),
    additionalCriteria: str = Form(""),
    developerMode: bool = Form(False),
):
    if not files:
        return _error(400, "Добавьте хотя бы один PDF или DOCX файл.")
    if len(files) > 30:
        return _error(400, "За один запуск можно загрузить не более 30 файлов.")
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return _error(400, "Для OpenRouter не найден OPENROUTER_API_KEY в .env.")

    requested_model = model or MODELS[0]["id"]
    selected = model_definition(requested_model)
    if not selected:
        return _error(400, "Выбрана неизвестная модель OpenRouter.")
    selected_profile = "full" if profile == "full" else "core"
    semantic_prompt = (prompt or DEFAULT_PROMPT).strip()
    map_prompt = (mapPrompt or DEFAULT_MAP_PROMPT).strip()
    additional = additionalCriteria.strip()
    if len(semantic_prompt) < 300 or len(map_prompt) < 300:
        return _error(400, "Промпты слишком короткие для строгого JSON-режима.")

    # Validate names before creating any files so a bad item cannot leave earlier uploads behind.
    validated: list[tuple[UploadFile, str, str]] = []
    for upload in files:
        original_name = _sanitize(upload.filename or "document")
        suffix = Path(original_name).suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            for item in files:
                await item.close()
            return _error(400, "Поддерживаются только PDF и DOCX.")
        validated.append((upload, original_name, suffix))

    prepared: list[dict[str, Any]] = []
    written: list[Path] = []
    now = now_iso()
    try:
        for upload, original_name, suffix in validated:
            technical = (
                UPLOADS_DIR
                / f"{int(__import__('time').time() * 1000)}-{uuid.uuid4()}{suffix}"
            )
            size = await _save_upload(upload, technical)
            written.append(technical)
            if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                for path in written:
                    path.unlink(missing_ok=True)
                for remaining, *_ in validated:
                    await remaining.close()
                return _error(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ.")
            prepared.append(
                {
                    "id": str(uuid.uuid4()),
                    "originalName": original_name,
                    "filePath": str(technical.resolve()),
                    "mimeType": upload.content_type or "application/octet-stream",
                    "size": size,
                    "createdAt": now,
                    "updatedAt": now,
                    "status": "queued",
                    "provider": selected["provider"],
                    "model": selected["id"],
                    "profile": selected_profile,
                    "prompt": semantic_prompt,
                    "mapPrompt": map_prompt,
                    "additionalCriteria": additional,
                    "developerMode": bool(developerMode),
                    "attempts": 0,
                    "progress": 0,
                    "progressMessage": "Файл принят. Ожидаем запуск обработки.",
                }
            )
    except Exception as exc:
        for path in written:
            path.unlink(missing_ok=True)
        return _error(500, str(exc))

    await create_jobs(prepared)
    start_queue()
    return JSONResponse(status_code=201, content=prepared)


@app.get("/api/jobs/{job_id}/structure")
async def structure(job_id: str):
    context = await _map_context(job_id)
    if not context:
        return _error(404, "Структура документа ещё не готова.")
    job, document = context
    return {"map": document["map"], "blocks": document.get("blocks", [])}


@app.patch("/api/jobs/{job_id}/map/elements/{element_id}")
async def patch_map_element(job_id: str, element_id: str, request: Request):
    context = await _map_context(job_id)
    if not context:
        return _error(404, "Структура документа не найдена.")
    job, document = context
    body = await request.json()
    element = next(
        (x for x in document["map"].get("elements", []) if x.get("id") == element_id),
        None,
    )
    if not element:
        return _error(404, "Фрагмент структуры не найден.")
    proposed_start = (
        body.get("startBlockId")
        if isinstance(body.get("startBlockId"), str)
        else element.get("startBlockId")
    )
    proposed_end = (
        body.get("endBlockId")
        if isinstance(body.get("endBlockId"), str)
        else element.get("endBlockId")
    )
    boundary_error = _validate_boundaries(
        document.get("blocks", []), proposed_start, proposed_end
    )
    if boundary_error:
        return _error(400, boundary_error)
    if isinstance(body.get("quote"), str) and body["quote"].strip():
        quote_error = _validate_quote(
            document.get("blocks", []), proposed_start, proposed_end, body["quote"]
        )
        if quote_error:
            return _error(400, quote_error)
    if isinstance(body.get("type"), str) and body["type"] in ALLOWED_TYPES:
        element["type"] = body["type"]
    if isinstance(body.get("label"), str) and body["label"].strip():
        element["label"] = body["label"].strip()[:180]
    if isinstance(body.get("quote"), str):
        element["quote"] = body["quote"].strip()[:1200]
    element["startBlockId"] = proposed_start
    element["endBlockId"] = proposed_end
    if body.get("state") in {"confirmed", "ambiguous"}:
        element["state"] = body["state"]
    element["source"] = "user"
    _unconfirm(document["map"])
    document["map"] = refresh_map(document, document["map"])
    await _persist_map(job, document)
    return await get_job(job_id)


@app.post("/api/jobs/{job_id}/map/elements")
async def add_map_element(job_id: str, request: Request):
    context = await _map_context(job_id)
    if not context:
        return _error(404, "Структура документа не найдена.")
    job, document = context
    blocks = document.get("blocks", [])
    if not blocks:
        return _error(400, "В документе нет текстовых блоков.")
    body = await request.json()
    element_type = body.get("type") if body.get("type") in ALLOWED_TYPES else "other"
    start = str(body.get("startBlockId") or blocks[0]["id"])
    end = str(body.get("endBlockId") or blocks[0]["id"])
    boundary_error = _validate_boundaries(blocks, start, end)
    if boundary_error:
        return _error(400, boundary_error)
    document["map"].setdefault("elements", []).append(
        {
            "id": f"section-{str(uuid.uuid4())[:8]}",
            "type": element_type,
            "label": str(body.get("label") or "Новый фрагмент")[:180],
            "startBlockId": start,
            "endBlockId": end,
            "blockIds": [],
            "pages": [],
            "text": "",
            "quote": "",
            "confidence": 1,
            "state": "confirmed",
            "source": "user",
        }
    )
    _unconfirm(document["map"])
    document["map"] = refresh_map(document, document["map"])
    await _persist_map(job, document)
    return JSONResponse(status_code=201, content=await get_job(job_id))


@app.delete("/api/jobs/{job_id}/map/elements/{element_id}")
async def delete_map_element(job_id: str, element_id: str):
    context = await _map_context(job_id)
    if not context:
        return _error(404, "Структура документа не найдена.")
    job, document = context
    before = len(document["map"].get("elements", []))
    document["map"]["elements"] = [
        x for x in document["map"].get("elements", []) if x.get("id") != element_id
    ]
    if before == len(document["map"]["elements"]):
        return _error(404, "Фрагмент структуры не найден.")
    _unconfirm(document["map"])
    document["map"] = refresh_map(document, document["map"])
    await _persist_map(job, document)
    return Response(status_code=204)


@app.post("/api/jobs/{job_id}/confirm-structure")
async def confirm_structure(job_id: str):
    context = await _map_context(job_id)
    if not context:
        return _error(404, "Структура документа не найдена.")
    job, document = context
    document["map"] = refresh_map(document, document["map"])
    if not document["map"].get("elements"):
        return _error(400, "Добавьте хотя бы один смысловой фрагмент.")
    invalid_codes = {"invalid_boundaries", "invalid_boundary", "empty_structure"}
    if any(x.get("code") in invalid_codes for x in document["map"].get("issues", [])):
        return _error(
            400,
            "Исправьте недействительные границы фрагментов перед запуском проверки.",
        )
    document["map"].setdefault("review", {})["required"] = True
    document["map"]["review"]["confirmedByUser"] = True
    document["map"]["review"]["autoConfirmed"] = False
    document["map"]["review"]["confirmationMode"] = "user"
    document["map"]["review"]["confirmedAt"] = now_iso()
    save_extracted(job_id, document)
    updated = await update_job(
        job_id,
        {
            "status": "queued_check",
            "progress": 32,
            "progressMessage": "Структура подтверждена. Проверка правил начнётся следующим шагом.",
            "documentMap": document["map"],
            "error": None,
        },
    )
    start_queue()
    return updated


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    job = await update_job(
        job_id,
        {
            "status": "cancelled",
            "progressMessage": "Проверка отменена пользователем.",
            "finishedAt": now_iso(),
        },
    )
    return job if job else _error(404, "Задача не найдена.")


@app.post("/api/jobs/{job_id}/retry")
async def retry(job_id: str):
    current = await get_job(job_id)
    if not current:
        return _error(404, "Задача не найдена.")
    cache_exists = bool(
        current.get("extractedPath") and Path(current["extractedPath"]).exists()
    )
    source_exists = bool(current.get("filePath") and Path(current["filePath"]).exists())
    if not source_exists and not cache_exists:
        return _error(409, "Нет исходного файла или кэша. Загрузите ВКР заново.")
    next_status = (
        "queued_check"
        if map_is_confirmed(current.get("documentMap")) and cache_exists
        else "queued"
    )
    job = await update_job(
        job_id,
        {
            "status": next_status,
            "progress": 32 if next_status == "queued_check" else 0,
            "progressMessage": "Повторяем проверку по подтверждённой структуре."
            if next_status == "queued_check"
            else "Проверка поставлена в очередь заново.",
            "error": None,
            "diagnostics": [],
            "finishedAt": None,
            "report": None,
            "retryRuleIds": [],
            "attempts": 0,
        },
    )
    start_queue()
    return job


@app.post("/api/jobs/{job_id}/restart")
async def restart(job_id: str):
    """Start the job from the structure-building stage, keeping the same file/settings."""
    current = await get_job(job_id)
    if not current:
        return _error(404, "Задача не найдена.")
    cache_exists = bool(
        current.get("extractedPath") and Path(current["extractedPath"]).exists()
    )
    source_exists = bool(current.get("filePath") and Path(current["filePath"]).exists())
    if not source_exists and not cache_exists:
        return _error(409, "Нет исходного файла или кэша. Загрузите ВКР заново.")
    if cache_exists:
        try:
            document = read_extracted(current["extractedPath"])
            document.pop("map", None)
            document.pop("factStore", None)
            # Runtime-derived semantic state is rebuilt from the next confirmed map.
            document.pop("semanticModel", None)
            save_extracted(job_id, document)
        except Exception:
            if not source_exists:
                return _error(
                    409,
                    "Не удалось подготовить кэш для нового запуска. Загрузите ВКР заново.",
                )
    job = await update_job(
        job_id,
        {
            "status": "queued",
            "progress": 0,
            "progressMessage": "Новый запуск поставлен в очередь. Структура будет построена заново.",
            "startedAt": None,
            "finishedAt": None,
            "documentMap": None,
            "report": None,
            "error": None,
            "diagnostics": [],
            "retryRuleIds": [],
            "attempts": 0,
        },
    )
    start_queue()
    return job


@app.post("/api/jobs/{job_id}/retry-failed")
async def retry_failed(job_id: str):
    current = await get_job(job_id)
    if not current or not current.get("report") or not current.get("extractedPath"):
        return _error(409, "Сначала должна завершиться хотя бы одна проверка.")
    retry_ids: list[str] = []
    for item in current["report"].get("ruleResults", []):
        coverage = item.get("coverage") or {}
        checked_by = str(item.get("checkedBy") or "")
        is_llm = checked_by.startswith("llm")
        failed = item.get("status") == "not_checked" and is_llm
        incomplete = (
            item.get("status") == "uncertain"
            and is_llm
            and (
                item.get("evidenceStatus") == "rejected"
                or int(coverage.get("checkedCandidateCount", 0))
                < int(coverage.get("candidateCount", 0))
            )
        )
        if failed or incomplete:
            retry_ids.append(item.get("ruleId"))
    retry_ids = [x for x in retry_ids if x]
    if not retry_ids:
        return _error(409, "Правил с ошибкой запроса или неполным покрытием нет.")
    job = await update_job(
        job_id,
        {
            "status": "queued_check",
            "progress": 32,
            "progressMessage": f"Повторяем только незавершённые проверки: {len(retry_ids)}.",
            "error": None,
            "diagnostics": [],
            "finishedAt": None,
            "retryRuleIds": retry_ids,
            "attempts": 0,
        },
    )
    start_queue()
    return job


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str):
    job = await delete_job(job_id)
    if not job:
        return _error(404, "Задача не найдена.")
    for raw in [job.get("filePath"), job.get("extractedPath")]:
        if raw:
            try:
                Path(raw).unlink(missing_ok=True)
            except OSError:
                pass
    return Response(status_code=204)


@app.get("/api/jobs/{job_id}/report.md")
async def report_markdown(job_id: str):
    job = await get_job(job_id)
    if not job or not job.get("report"):
        return PlainTextResponse("Отчёт ещё не готов.", status_code=404)
    filename = quote(f"{job['originalName']}-protocol.md")
    return PlainTextResponse(
        report_to_markdown(job["originalName"], job["report"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/jobs/{job_id}/report.pdf")
async def report_pdf(job_id: str):
    """Concise report intended for the thesis author."""
    job = await get_job(job_id)
    if not job or not job.get("report"):
        return _error(404, "Отчёт ещё не готов.")
    try:
        content = report_to_user_pdf(
            job["originalName"],
            job["report"],
            profile=job.get("profile"),
            generated_at=job.get("finishedAt"),
        )
    except Exception as exc:
        return _error(500, f"Не удалось сформировать PDF: {exc}")
    filename = quote(f"{job['originalName']}-report.pdf")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/jobs/{job_id}/developer-report.pdf")
async def developer_report_pdf(job_id: str):
    """Full technical report with routing, evidence and LLM diagnostics."""
    job = await get_job(job_id)
    if not job or not job.get("report"):
        return _error(404, "Отчёт ещё не готов.")
    try:
        content = developer_report_to_pdf(
            job["originalName"],
            job["report"],
            profile=job.get("profile"),
            generated_at=job.get("finishedAt"),
        )
    except Exception as exc:
        return _error(500, f"Не удалось сформировать технический PDF: {exc}")
    filename = quote(f"{job['originalName']}-developer-report.pdf")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/jobs/{job_id}/report.json")
async def report_json(job_id: str):
    job = await get_job(job_id)
    if not job or not job.get("report"):
        return _error(404, "Отчёт ещё не готов.")
    filename = quote(f"{job['originalName']}-protocol.json")
    return JSONResponse(
        job["report"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


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


async def _map_context(job_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    job = await get_job(job_id)
    if not job or not job.get("extractedPath"):
        return None
    try:
        document = read_extracted(job["extractedPath"])
    except Exception:
        return None
    return (job, document) if document.get("map") else None


async def _persist_map(job: dict[str, Any], document: dict[str, Any]) -> None:
    path = save_extracted(job["id"], document)
    if path != job.get("extractedPath"):
        await update_job(job["id"], {"extractedPath": path})
    await update_job(
        job["id"],
        {
            "documentMap": document.get("map"),
            "status": "awaiting_review",
            "progress": 30,
            "report": None,
        },
    )


def _unconfirm(document_map: dict[str, Any]) -> None:
    review = document_map.setdefault("review", {"required": True})
    review["required"] = True
    review["confirmedByUser"] = False
    review["autoConfirmed"] = False
    review.pop("confirmationMode", None)
    review.pop("confirmedAt", None)


def _validate_boundaries(
    blocks: list[dict[str, Any]], start_id: str, end_id: str
) -> str:
    index = {x.get("id"): i for i, x in enumerate(blocks)}
    if start_id not in index or end_id not in index:
        return "Выбранный блок не найден в документе."
    if index[start_id] > index[end_id]:
        return "Первый блок диапазона должен находиться раньше последнего."
    return ""


def _validate_quote(
    blocks: list[dict[str, Any]], start_id: str, end_id: str, quote_value: str
) -> str:
    index = {x.get("id"): i for i, x in enumerate(blocks)}
    if start_id not in index or end_id not in index or index[start_id] > index[end_id]:
        return "Нельзя проверить цитату при недействительных границах."
    target = normalized_quote(quote_value)
    if len(target) < 4:
        return "Опорная цитата слишком короткая."
    for block in blocks[index[start_id] : index[end_id] + 1]:
        if target in normalized_quote(block.get("text", "")):
            return ""
    return "Опорная цитата должна дословно находиться внутри выбранного диапазона."


def _sanitize(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def _error(status: int, message: str) -> JSONResponse:
    # Explicit charset keeps Cyrillic readable when an error endpoint is opened
    # directly in a browser (for example when PDF generation fails).
    return JSONResponse(
        status_code=status,
        content={"error": message},
        media_type="application/json; charset=utf-8",
    )
