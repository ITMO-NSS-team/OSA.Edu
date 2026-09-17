#!/usr/bin/env python3
"""Submit academic-work checks to OSA.Edu and preserve its output artifacts."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


DEFAULT_BASE_URL = os.getenv("OSA_EDU_BASE_URL", "http://127.0.0.1:8787")
CHECKS = ("full", "literature", "normcontrol", "reproducibility")


class OsaEduError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120,
) -> tuple[bytes, str]:
    request = Request(
        f"{_base_url(base_url)}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", **(headers or {})},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get_content_type()
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload)
            detail = parsed.get("error") or parsed.get("detail") or payload
        except json.JSONDecodeError:
            detail = payload
        raise OsaEduError(f"OSA.Edu HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise OsaEduError(f"OSA.Edu недоступен по адресу {_base_url(base_url)}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise OsaEduError(f"OSA.Edu не ответил за {timeout:g} секунд: {path}") from exc


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120,
) -> Any:
    payload, _ = _request(base_url, path, method=method, body=body, headers=headers, timeout=timeout)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OsaEduError(f"OSA.Edu вернул некорректный JSON для {path}.") from exc


def _multipart(file_path: Path, fields: dict[str, str], *, file_field: str = "file") -> tuple[bytes, str]:
    boundary = f"----osa-edu-agent-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    fallback_name = "".join(character if character.isascii() and character not in '"\\' else "_" for character in file_path.name)
    encoded_name = quote(file_path.name, safe="")
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{fallback_name}"; '
                f"filename*=UTF-8''{encoded_name}\r\n"
            ).encode("ascii"),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _submit(
    base_url: str,
    path: str,
    file_path: Path,
    fields: dict[str, str],
    *,
    file_field: str = "file",
) -> Any:
    body, content_type = _multipart(file_path, fields, file_field=file_field)
    return _json_request(
        base_url,
        path,
        method="POST",
        body=body,
        headers={"Content-Type": content_type},
        timeout=300,
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _download(base_url: str, path: str, target: Path) -> str:
    payload, _ = _request(base_url, path, timeout=300)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return str(target.resolve())


def _try_download(
    base_url: str,
    path: str,
    target: Path,
    artifacts: dict[str, str],
    artifact_errors: list[str],
    key: str,
) -> None:
    try:
        artifacts[key] = _download(base_url, path, target)
    except OsaEduError as exc:
        artifact_errors.append(f"{key}: {exc}")


def _poll(
    label: str,
    fetch: Callable[[], dict[str, Any]],
    terminal: set[str],
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    previous: tuple[str, int, str] | None = None
    while True:
        job = fetch()
        status = str(job.get("status") or "")
        progress = int(job.get("progress") or 0)
        message = str(job.get("progressMessage") or job.get("error") or "")
        state = (status, progress, message)
        if state != previous:
            print(f"[{label}] {status or 'unknown'} · {progress}% · {message}".rstrip(" ·"), file=sys.stderr, flush=True)
            previous = state
        if status in terminal:
            return job
        if time.monotonic() >= deadline:
            raise OsaEduError(f"Истёк timeout ожидания этапа {label} ({timeout_seconds} секунд).")
        time.sleep(poll_seconds)


def _job_from_list(base_url: str, job_id: str) -> dict[str, Any]:
    jobs = _json_request(base_url, "/api/jobs")
    if not isinstance(jobs, list):
        raise OsaEduError("OSA.Edu вернул неожиданный список задач.")
    for job in jobs:
        if isinstance(job, dict) and job.get("id") == job_id:
            return job
    raise OsaEduError(f"Задача {job_id} не найдена в очереди OSA.Edu.")


def _run_full(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    fields = {"profile": args.profile, "developerMode": "true"}
    if args.model:
        fields["model"] = args.model
    created = _submit(args.base_url, "/api/jobs", args.file, fields, file_field="files")
    if not isinstance(created, list) or not created or not isinstance(created[0], dict):
        raise OsaEduError("OSA.Edu не вернул созданную задачу полной проверки.")
    job_id = str(created[0].get("id") or "")
    job = _poll(
        "full",
        lambda: _job_from_list(args.base_url, job_id),
        {"awaiting_review", "completed", "failed", "cancelled"},
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    stage = root / "full"
    _write_json(stage / "job.json", job)
    artifacts: dict[str, str] = {"job": str((stage / "job.json").resolve())}
    artifact_errors: list[str] = []
    if job.get("status") == "completed":
        _try_download(args.base_url, f"/api/jobs/{job_id}/report.json", stage / "report.json", artifacts, artifact_errors, "report_json")
        _try_download(args.base_url, f"/api/jobs/{job_id}/report.md", stage / "report.md", artifacts, artifact_errors, "report_markdown")
        _try_download(args.base_url, f"/api/jobs/{job_id}/report.pdf", stage / "report.pdf", artifacts, artifact_errors, "report_pdf")
    return {"job_id": job_id, "status": job.get("status"), "error": job.get("error"), "artifacts": artifacts, "artifact_errors": artifact_errors}


def _run_literature(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.file.suffix.lower() != ".pdf":
        raise OsaEduError("Проверка литературы поддерживает только PDF.")
    fields = {"model": args.model} if args.model else {}
    created = _submit(args.base_url, "/api/literature/jobs", args.file, fields, file_field="files")
    if not isinstance(created, list) or not created or not isinstance(created[0], dict):
        raise OsaEduError("OSA.Edu не вернул созданную задачу проверки литературы.")
    job_id = str(created[0].get("id") or "")
    job = _poll(
        "literature",
        lambda: _json_request(args.base_url, f"/api/literature/jobs/{job_id}"),
        {"done", "failed", "cancelled"},
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    stage = root / "literature"
    _write_json(stage / "job.json", job)
    artifacts: dict[str, str] = {"job": str((stage / "job.json").resolve())}
    artifact_errors: list[str] = []
    if job.get("result") is not None:
        _write_json(stage / "result.json", job["result"])
        artifacts["result_json"] = str((stage / "result.json").resolve())
    elif job.get("status") == "done":
        artifact_errors.append("result_json: завершённая задача не содержит result")
    return {"job_id": job_id, "status": job.get("status"), "error": job.get("error"), "artifacts": artifacts, "artifact_errors": artifact_errors}


def _run_normcontrol(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.file.suffix.lower() != ".pdf":
        raise OsaEduError("Нормоконтроль поддерживает только PDF.")
    created = _submit(args.base_url, "/api/normcontrol/jobs", args.file, {})
    if not isinstance(created, dict):
        raise OsaEduError("OSA.Edu не вернул созданную задачу нормоконтроля.")
    job_id = str(created.get("id") or "")
    job = _poll(
        "normcontrol",
        lambda: _json_request(args.base_url, f"/api/normcontrol/jobs/{job_id}"),
        {"completed", "failed", "cancelled"},
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    stage = root / "normcontrol"
    _write_json(stage / "job.json", job)
    artifacts: dict[str, str] = {"job": str((stage / "job.json").resolve())}
    artifact_errors: list[str] = []
    if job.get("status") == "completed":
        _try_download(
            args.base_url,
            f"/api/normcontrol/jobs/{job_id}/report.pdf",
            stage / "report.pdf",
            artifacts,
            artifact_errors,
            "report_pdf",
        )
    return {"job_id": job_id, "status": job.get("status"), "error": job.get("error"), "artifacts": artifacts, "artifact_errors": artifact_errors}


def _run_reproducibility(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.file.suffix.lower() != ".pdf":
        raise OsaEduError("Проверка воспроизводимости поддерживает только PDF.")
    if not args.repository:
        raise OsaEduError("Для воспроизводимости нужен явный --repository URL.")
    query = urlencode({"repository": args.repository})
    preflight = _json_request(args.base_url, f"/api/reproducibility/preflight?{query}", timeout=300)
    stage = root / "reproducibility"
    _write_json(stage / "preflight.json", preflight)
    if not isinstance(preflight, dict) or not preflight.get("ok"):
        raise OsaEduError("Preflight воспроизводимости не пройден; подробности сохранены в preflight.json.")
    created = _submit(args.base_url, "/api/reproducibility/jobs", args.file, {"repository": args.repository})
    if not isinstance(created, dict):
        raise OsaEduError("OSA.Edu не вернул созданную задачу воспроизводимости.")
    job_id = str(created.get("id") or "")
    job = _poll(
        "reproducibility",
        lambda: _json_request(args.base_url, f"/api/reproducibility/jobs/{job_id}"),
        {"completed", "failed", "cancelled"},
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    _write_json(stage / "job.json", job)
    artifacts: dict[str, str] = {
        "preflight": str((stage / "preflight.json").resolve()),
        "job": str((stage / "job.json").resolve()),
    }
    artifact_errors: list[str] = []
    try:
        artifacts["log"] = _download(args.base_url, f"/api/reproducibility/jobs/{job_id}/log.txt", stage / "osa.log")
    except OsaEduError:
        pass
    if job.get("status") == "completed":
        try:
            result = _json_request(args.base_url, f"/api/reproducibility/jobs/{job_id}/result", timeout=300)
            _write_json(stage / "paper_analysis.json", result)
            artifacts["result_json"] = str((stage / "paper_analysis.json").resolve())
        except OsaEduError as exc:
            artifact_errors.append(f"result_json: {exc}")
    return {"job_id": job_id, "status": job.get("status"), "error": job.get("error"), "artifacts": artifacts, "artifact_errors": artifact_errors}


RUNNERS: dict[str, Callable[[argparse.Namespace, Path], dict[str, Any]]] = {
    "full": _run_full,
    "literature": _run_literature,
    "normcontrol": _run_normcontrol,
    "reproducibility": _run_reproducibility,
}


def _parse_checks(value: str) -> list[str]:
    result: list[str] = []
    for item in value.split(","):
        check = item.strip().lower()
        if not check:
            continue
        if check not in CHECKS:
            raise argparse.ArgumentTypeError(f"Неизвестный этап {check!r}; доступны: {', '.join(CHECKS)}")
        if check not in result:
            result.append(check)
    if not result:
        raise argparse.ArgumentTypeError("Укажите хотя бы один этап проверки.")
    return result


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Значение должно быть больше нуля.")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Значение должно быть больше нуля.")
    return parsed


def _default_output(file_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / "osa-edu-results" / f"{file_path.stem}-{stamp}"


def _health(args: argparse.Namespace) -> int:
    health = _json_request(args.base_url, "/api/health")
    summary = health
    if isinstance(health, dict):
        summary = {
            "ok": health.get("ok"),
            "configured": health.get("configured"),
            "models": [
                {
                    key: model.get(key)
                    for key in ("id", "provider", "tier", "recommended")
                    if key in model
                }
                for model in health.get("models", [])
                if isinstance(model, dict)
            ],
            "host": health.get("host"),
            "normcontrol": health.get("normcontrol"),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if isinstance(health, dict) and health.get("ok") else 1


def _run(args: argparse.Namespace) -> int:
    args.file = args.file.expanduser().resolve()
    if not args.file.is_file():
        raise OsaEduError(f"Файл не найден: {args.file}")
    if args.file.suffix.lower() not in {".pdf", ".docx"}:
        raise OsaEduError("OSA.Edu принимает PDF или DOCX; отдельные этапы могут поддерживать только PDF.")
    args.base_url = _base_url(args.base_url)
    output = (args.output_dir or _default_output(args.file)).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    health = _json_request(args.base_url, "/api/health")
    _write_json(output / "health.json", health)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "base_url": args.base_url,
        "source_file": str(args.file),
        "requested_checks": args.checks,
        "output_dir": str(output),
        "health": str((output / "health.json").resolve()),
        "checks": {},
    }
    failed = False
    for check in args.checks:
        print(f"[{check}] отправляем работу в OSA.Edu", file=sys.stderr, flush=True)
        try:
            result = RUNNERS[check](args, output)
            manifest["checks"][check] = result
            if result.get("status") not in {"completed", "done"} or result.get("artifact_errors"):
                failed = True
        except Exception as exc:
            failed = True
            manifest["checks"][check] = {"status": "client_error", "error": str(exc), "artifacts": {}}
            print(f"[{check}] ошибка: {exc}", file=sys.stderr, flush=True)
        _write_json(output / "osa-edu-manifest.json", manifest)

    manifest["finished_at"] = _utc_now()
    manifest["ok"] = not failed
    _write_json(output / "osa-edu-manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OSA.Edu checks and save their original artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Read OSA.Edu health information.")
    health.add_argument("--base-url", default=DEFAULT_BASE_URL)
    health.set_defaults(handler=_health)

    run = subparsers.add_parser("run", help="Submit a work and wait for selected checks.")
    run.add_argument("file", type=Path)
    run.add_argument("--checks", type=_parse_checks, default=["full"], help=f"Comma-separated: {', '.join(CHECKS)}")
    run.add_argument("--base-url", default=DEFAULT_BASE_URL)
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--model", default="")
    run.add_argument("--profile", choices=("core", "full"), default="core")
    run.add_argument("--repository", default="")
    run.add_argument("--timeout-seconds", type=_positive_int, default=7200)
    run.add_argument("--poll-seconds", type=_positive_float, default=3.0)
    run.set_defaults(handler=_run)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        return 130
    except OsaEduError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
