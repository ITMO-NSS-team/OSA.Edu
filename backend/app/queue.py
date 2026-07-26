from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from .config import AUTO_DELETE_SOURCE
from .defaults import DEFAULT_MAP_PROMPT
from .document.map_builder import build_document_map, map_can_be_reused
from .extraction import extract_document, read_extracted, save_extracted
from .orchestration.checker import check_document
from .reporting import make_report
from .store import get_job, list_jobs, update_job
from .util import merge_usage, now_iso, unique

_queue_task: asyncio.Task[None] | None = None
_queue_lock = asyncio.Lock()


def start_queue() -> None:
    global _queue_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _queue_task is None or _queue_task.done():
        _queue_task = loop.create_task(_run_queue(), name="osa-edu-job-queue")


async def _run_queue() -> None:
    async with _queue_lock:
        while True:
            jobs = await list_jobs()
            job = next((x for x in jobs if x.get("status") in {"queued", "queued_check"}), None)
            if not job:
                return
            if job.get("status") == "queued":
                await _prepare_structure(job)
            else:
                await _perform_check(job)


async def _is_cancelled(job_id: str) -> bool:
    current = await get_job(job_id)
    return bool(current and current.get("status") == "cancelled")


async def _prepare_structure(job: dict[str, Any]) -> None:
    try:
        await update_job(job["id"], {
            "status": "extracting", "progress": 3, "startedAt": now_iso(), "error": None,
            "diagnostics": [], "attempts": 0, "report": None,
        })
        document, extracted_path = await _obtain_document(job)
        if len(document.get("text", "")) < 100:
            raise RuntimeError("Из файла удалось извлечь слишком мало текста. Попробуйте DOCX или PDF с текстовым слоем.")
        await update_job(job["id"], {"extractedPath": extracted_path, "progress": 10})

        map_prompt = job.get("mapPrompt") or DEFAULT_MAP_PROMPT
        if not map_can_be_reused(document.get("map"), job.get("provider", "openrouter"), job["model"], map_prompt):
            await update_job(job["id"], {"status": "mapping", "progress": 12})
            if await _is_cancelled(job["id"]):
                return
            document["map"] = await build_document_map(
                document,
                provider=job.get("provider", "openrouter"), model=job["model"], prompt=map_prompt,
            )
            await update_job(job["id"], {"status": "mapping", "progress": 30})
            save_extracted(job["id"], document)

        if AUTO_DELETE_SOURCE:
            try:
                Path(job["filePath"]).unlink(missing_ok=True)
            except OSError:
                pass
        if await _is_cancelled(job["id"]):
            return
        document_map = document.get("map") or {}
        await update_job(job["id"], {
            "status": "awaiting_review", "progress": 30, "documentMap": document_map,
            "diagnostics": (document_map.get("usage") or {}).get("diagnostics", []),
            "error": None, "finishedAt": None,
        })
    except Exception as exc:
        if await _is_cancelled(job["id"]):
            return
        diagnostics = _diagnostics_from_error(exc)
        suffix = " Подробности сохранены в диагностике LLM ниже." if diagnostics else ""
        await update_job(job["id"], {
            "status": "failed", "progress": 100, "finishedAt": now_iso(),
            "diagnostics": diagnostics, "error": f"{exc}{suffix}",
        })


async def _perform_check(job: dict[str, Any]) -> None:
    try:
        extracted_path = job.get("extractedPath")
        if not extracted_path:
            raise RuntimeError("Кэш документа не найден.")
        document = read_extracted(extracted_path)
        if not (document.get("map") or {}).get("review", {}).get("confirmedByUser"):
            raise RuntimeError("Сначала подтвердите выделенную структуру документа.")

        await update_job(job["id"], {
            "status": "checking", "progress": 35, "error": None,
            "diagnostics": ((document.get("map") or {}).get("usage") or {}).get("diagnostics", []),
            "attempts": 0,
        })
        retry_ids = job.get("retryRuleIds") or []
        previous_report = job.get("report") if retry_ids else None

        async def progress(done: int, total: int, _message: str = "") -> None:
            if await _is_cancelled(job["id"]):
                return
            await update_job(job["id"], {
                "status": "checking",
                "progress": 35 + round(done / max(1, total) * 63),
                "attempts": done,
            })

        checked = await check_document(
            document=document,
            provider=job.get("provider", "openrouter"),
            model=job["model"],
            prompt=job.get("prompt", ""),
            profile=job.get("profile", "core"),
            additional_criteria=job.get("additionalCriteria", ""),
            only_rule_ids=retry_ids or None,
            on_progress=progress,
            is_cancelled=lambda: _is_cancelled(job["id"]),
        )
        if await _is_cancelled(job["id"]):
            return

        results = _merge_rule_results(previous_report.get("ruleResults", []), checked["results"]) if previous_report else checked["results"]
        warnings = unique([
            *(document.get("warnings") or []),
            *((document.get("map") or {}).get("warnings") or []),
            *(checked.get("warnings") or []),
        ])
        usage = _merge_usage_stats(previous_report.get("llmUsage"), checked["llmUsage"]) if previous_report else checked["llmUsage"]
        routing = (
            {**previous_report.get("routing", {}), "checkRequests": int((previous_report.get("routing") or {}).get("checkRequests", 0)) + int(checked["routing"].get("checkRequests", 0))}
            if previous_report else checked["routing"]
        )
        report = make_report(
            checked["rules"], results, warnings, usage, document.get("map"), routing,
            {
                "appVersion": "3.5.0-py",
                "provider": job.get("provider", "openrouter"),
                "model": job["model"],
                "promptHash": _hash_text(job.get("prompt", "")),
                "mapPromptHash": _hash_text(job.get("mapPrompt", "")),
            },
        )
        await update_job(job["id"], {
            "status": "completed", "progress": 100, "finishedAt": now_iso(),
            "documentMap": document.get("map"), "report": report, "retryRuleIds": [],
            "diagnostics": [
                *(((document.get("map") or {}).get("usage") or {}).get("diagnostics", [])),
                *(usage.get("diagnostics", [])),
            ],
            "error": None,
        })
    except Exception as exc:
        if await _is_cancelled(job["id"]):
            return
        await update_job(job["id"], {
            "status": "failed", "progress": 100, "finishedAt": now_iso(),
            "diagnostics": _diagnostics_from_error(exc), "error": str(exc),
        })


async def _obtain_document(job: dict[str, Any]) -> tuple[dict[str, Any], str]:
    cached = job.get("extractedPath")
    if cached:
        try:
            return read_extracted(cached), cached
        except Exception:
            pass
    source = Path(job["filePath"])
    if not source.exists():
        raise RuntimeError("Исходный файл удалён, а кэш извлечённого документа отсутствует. Загрузите файл заново.")
    document = await asyncio.to_thread(extract_document, source)
    path = save_extracted(job["id"], document)
    return document, path


def _diagnostics_from_error(exc: BaseException) -> list[dict[str, Any]]:
    usage = getattr(exc, "llm_usage", None) or getattr(exc, "llmUsage", None)
    return list((usage or {}).get("diagnostics", []))


def _merge_rule_results(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    replacements = {x.get("ruleId"): x for x in current}
    return [replacements.get(x.get("ruleId"), x) for x in previous]


def _merge_usage_stats(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    if not left:
        return right
    result = {
        "requests": 0, "retries": 0, "packets": 0, "candidates": 0,
        "estimatedInputTokens": 0, "rateLimitWaitMs": 0, "requestDurationMs": 0,
        "diagnostics": [], "traces": [],
    }
    merge_usage(result, left)
    merge_usage(result, right)
    return result


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]
