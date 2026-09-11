from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from ..config import MAX_FILE_SIZE_MB, UPLOADS_DIR
from ..defaults import MODELS, model_definition
from ..util import now_iso
from .queue import cancel_literature_job, start_literature_queue
from .service import check_literature
from .store import (
    create_literature_jobs,
    delete_literature_job,
    get_literature_job,
    list_literature_jobs,
    update_literature_job,
)

router = APIRouter(prefix="/api/literature", tags=["literature"])
LITERATURE_UPLOADS_DIR = UPLOADS_DIR / "literature"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value)


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


@router.get("/jobs")
async def literature_jobs():
    return await list_literature_jobs()


@router.get("/jobs/{job_id}")
async def literature_job(job_id: str):
    job = await get_literature_job(job_id)
    return job if job else _error(404, "Проверка литературы не найдена.")


@router.post("/jobs")
async def create_literature_job_endpoint(
    files: list[UploadFile] = File(...),
    model: str = Form(""),
):
    if not files:
        return _error(400, "Добавьте хотя бы один PDF.")
    if len(files) > 30:
        return _error(400, "За один раз можно добавить не более 30 PDF.")
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        return _error(400, "Для проверки литературы не найден OPENROUTER_API_KEY в .env.")

    requested_model = model or next((item["id"] for item in MODELS if item.get("tier") == "production"), MODELS[0]["id"])
    selected = model_definition(requested_model)
    if not selected:
        return _error(400, "Выбрана неизвестная модель OpenRouter.")

    validated: list[tuple[UploadFile, str]] = []
    for upload in files:
        name = _safe_name(upload.filename or "document.pdf")
        if not name.lower().endswith(".pdf"):
            for item in files:
                await item.close()
            return _error(400, "Для проверки литературы поддерживаются только PDF.")
        validated.append((upload, name))

    prepared: list[dict] = []
    written: list[Path] = []
    try:
        for upload, name in validated:
            job_id = str(uuid.uuid4())
            target = LITERATURE_UPLOADS_DIR / f"{job_id}.pdf"
            size = await _save_upload(upload, target)
            written.append(target)
            if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                for path in written:
                    path.unlink(missing_ok=True)
                return _error(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ.")
            created_at = now_iso()
            prepared.append(
                {
                    "id": job_id,
                    "originalName": name,
                    "filePath": str(target.resolve()),
                    "size": size,
                    "createdAt": created_at,
                    "updatedAt": created_at,
                    "status": "queued",
                    "model": selected["id"],
                    "progress": 0,
                    "progressMessage": "Работа добавлена в очередь.",
                    "result": None,
                    "error": None,
                    "startedAt": None,
                    "finishedAt": None,
                }
            )
    except Exception as exc:
        for path in written:
            path.unlink(missing_ok=True)
        return _error(500, f"Не удалось добавить работу в очередь: {exc}")

    await create_literature_jobs(prepared)
    start_literature_queue()
    return JSONResponse(status_code=201, content=prepared)


@router.post("/jobs/{job_id}/cancel")
async def cancel_literature_job_endpoint(job_id: str):
    job = await cancel_literature_job(job_id)
    if not job:
        return _error(404, "Проверка литературы не найдена.")
    start_literature_queue()
    return job


@router.post("/jobs/{job_id}/retry")
async def retry_literature_job_endpoint(job_id: str):
    current = await get_literature_job(job_id)
    if not current:
        return _error(404, "Проверка литературы не найдена.")
    if current.get("status") in {"running", "cancelling", "queued"}:
        return _error(409, "Эта работа уже находится в очереди или выполняется.")
    path = Path(str(current.get("filePath") or ""))
    if not path.exists():
        return _error(409, "Исходный PDF удалён. Загрузите работу заново.")
    updated = await update_literature_job(
        job_id,
        {
            "status": "queued",
            "progress": 0,
            "progressMessage": "Повторная проверка добавлена в очередь.",
            "result": None,
            "error": None,
            "startedAt": None,
            "finishedAt": None,
        },
    )
    start_literature_queue()
    return updated


@router.delete("/jobs/{job_id}")
async def delete_literature_job_endpoint(job_id: str):
    current = await get_literature_job(job_id)
    if not current:
        return _error(404, "Проверка литературы не найдена.")
    if current.get("status") in {"running", "cancelling"}:
        return _error(409, "Сначала остановите текущую проверку.")
    removed = await delete_literature_job(job_id)
    if removed and removed.get("filePath"):
        try:
            Path(str(removed["filePath"])).unlink(missing_ok=True)
        except OSError:
            pass
    return Response(status_code=204)


# Legacy one-shot endpoint kept for scripts that used the earlier MVP.
@router.post("/check")
async def check_literature_endpoint(
    file: UploadFile = File(...),
    model: str = Form(""),
):
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        await file.close()
        return _error(400, "Для проверки литературы загрузите PDF.")
    try:
        content = await file.read()
    finally:
        await file.close()
    if not content:
        return _error(400, "Загружен пустой PDF.")
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        return _error(413, f"Файл больше {MAX_FILE_SIZE_MB} МБ.")
    selected_model = model or None
    if selected_model and not model_definition(selected_model):
        return _error(400, "Выбрана неизвестная модель OpenRouter.")
    try:
        return await check_literature(content, filename, model=selected_model)
    except Exception as exc:
        return _error(500, f"Не удалось проверить литературу: {exc}")
