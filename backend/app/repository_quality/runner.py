from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

ProgressCallback = Callable[[int, str], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]

_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_RE = re.compile(r"\x1b[@-_]")


class RepositoryQualityRunError(RuntimeError):
    pass


def _strip_terminal_sequences(text: str) -> str:
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    return text.replace("\r", "").replace("\b", "")


def osa_installed() -> bool:
    if os.getenv("REPOSITORY_QUALITY_OSA_COMMAND", "").strip():
        return True
    return importlib.util.find_spec("osa_tool") is not None


def configured_model() -> str:
    return (
        os.getenv("REPOSITORY_QUALITY_MODEL", "").strip()
        or os.getenv("REPRODUCIBILITY_MODEL", "").strip()
        or os.getenv("LITERATURE_REVIEW_MODEL", "").strip()
        or "openai/gpt-5.6-luna"
    )


def configured_base_url() -> str:
    explicit = os.getenv("REPOSITORY_QUALITY_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    repro = os.getenv("REPRODUCIBILITY_BASE_URL", "").strip()
    if repro:
        return repro.rstrip("/")
    base = os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai").strip().rstrip("/")
    if base.endswith("/api/v1") or base.endswith("/v1"):
        return base
    if base == "https://openrouter.ai":
        return f"{base}/api/v1"
    return base


def build_osa_command(repository: str, output_dir: Path) -> list[str]:
    model = configured_model()
    template = os.getenv("REPOSITORY_QUALITY_OSA_COMMAND", "").strip()
    if template:
        values = {
            "repository": repository,
            "output": str(output_dir),
            "model": model,
            "base_url": configured_base_url(),
        }
        return [part.format(**values) for part in shlex.split(template)]

    python = os.getenv("REPOSITORY_QUALITY_OSA_PYTHON", "").strip() or sys.executable
    return [
        python,
        "-m",
        "osa_tool.tools.repository_quality",
        "--repository",
        repository,
        "--output-dir",
        str(output_dir),
        "--no-fork",
        "--delete-dir",
        "--base-url",
        configured_base_url(),
        "--model-repository-quality",
        model,
        "--context-window",
        os.getenv("REPOSITORY_QUALITY_CONTEXT_WINDOW", "32768"),
        "--max-tokens",
        os.getenv("REPOSITORY_QUALITY_MAX_TOKENS", "4096"),
        "--max-retries",
        os.getenv("REPOSITORY_QUALITY_LLM_MAX_RETRIES", "3"),
    ]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _quality_payload(value: dict[str, Any]) -> dict[str, Any] | None:
    candidate = value.get("result") if isinstance(value.get("result"), dict) else value
    if not isinstance(candidate, dict):
        return None
    summary = candidate.get("summary")
    checks = candidate.get("checks")
    if isinstance(summary, dict) and isinstance(checks, dict) and "score" in summary:
        return candidate
    return None


def find_report(output_dir: Path) -> tuple[Path, dict[str, Any]]:
    candidates = sorted(output_dir.rglob("report.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    candidates += [
        item
        for item in sorted(output_dir.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        if item.name != "report.json"
    ]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        value = _load_json(path)
        if value and _quality_payload(value):
            return path, value
    raise RepositoryQualityRunError(
        "OSA завершилась, но report.json с результатом repository_quality не найден."
    )


def _parse_progress(line: str) -> tuple[int, str] | None:
    clean = _strip_terminal_sequences(line)
    if "Repository quality stage started: Repository clone" in clean or "Cloning repository" in clean:
        return 15, "OSA клонирует репозиторий."
    if "Repository quality stage completed: Repository clone" in clean or "Repository cloned" in clean:
        return 30, "Репозиторий загружен."
    if "Calculating formal repository quality" in clean or "Running quality checks" in clean:
        return 45, "OSA проверяет структуру и качество репозитория."
    if "Building file tree" in clean:
        return 38, "OSA строит дерево файлов."
    if "Repository quality checks:" in clean:
        return 50, "Запущены формальные и LLM-проверки."
    if "Repository quality report saved" in clean or "Repository quality artifacts written" in clean:
        return 96, "OSA формирует итоговый отчёт."
    return None


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=6)
    except asyncio.TimeoutError:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            pass
        await process.wait()


async def run_repository_quality(
    repository: str,
    output_dir: Path,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
) -> tuple[dict[str, Any], Path, str]:
    if not osa_installed():
        raise RepositoryQualityRunError(
            'Пакет OSA не установлен. Установите `osa_tool[paper-claims]` из ветки feat/thesis-repository-analysis '
            "или задайте REPOSITORY_QUALITY_OSA_COMMAND."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "osa.log"
    command = build_osa_command(repository, output_dir)
    if on_progress:
        await on_progress(8, "Запускаем штатный OSA repository_quality.")

    env = os.environ.copy()
    openrouter_key = env.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key and not env.get("OPENAI_API_KEY", "").strip():
        env["OPENAI_API_KEY"] = openrouter_key
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    env.setdefault("CLICOLOR", "0")
    env.setdefault("TERM", "dumb")

    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(output_dir),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **popen_kwargs,
    )
    timeout = max(60, int(os.getenv("REPOSITORY_QUALITY_TIMEOUT_SECONDS", "1200")))
    heartbeat_seconds = max(10, int(os.getenv("REPOSITORY_QUALITY_HEARTBEAT_SECONDS", "20")))
    log_lines: list[str] = []
    state = {"percent": 8, "message": "OSA запущена.", "last_line": ""}
    started = time.monotonic()

    with log_path.open("w", encoding="utf-8", buffering=1) as log_file:
        header = [
            f"[OSA.Edu] PID: {process.pid}",
            f"[OSA.Edu] Python: {command[0]}",
            f"[OSA.Edu] Model: {configured_model()}",
            f"[OSA.Edu] Base URL: {configured_base_url()}",
            f"[OSA.Edu] Repository: {repository}",
            "[OSA.Edu] Pipeline: osa_tool.tools.repository_quality",
        ]
        for line in header:
            log_file.write(line + "\n")
            log_lines.append(line)
            if on_log:
                await on_log(line)

        async def consume_output() -> None:
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    return
                line = _strip_terminal_sequences(raw.decode("utf-8", errors="replace")).rstrip("\r\n")
                if not line.strip():
                    continue
                log_file.write(line + "\n")
                log_lines.append(line)
                state["last_line"] = line
                if on_log:
                    await on_log(line)
                parsed = _parse_progress(line)
                if parsed:
                    state["percent"], state["message"] = parsed
                    if on_progress:
                        await on_progress(*parsed)

        async def heartbeat() -> None:
            while process.returncode is None:
                await asyncio.sleep(heartbeat_seconds)
                if process.returncode is not None or not on_progress:
                    return
                elapsed = max(0, int(time.monotonic() - started))
                minutes, seconds = divmod(elapsed, 60)
                suffix = f"{minutes} мин {seconds} с" if minutes else f"{seconds} с"
                message = str(state["message"])
                last_line = str(state["last_line"]).strip()
                if last_line:
                    message = f"{message} Работает {suffix}. Последний лог: {last_line[-180:]}"
                else:
                    message = f"{message} Работает {suffix}."
                await on_progress(int(state["percent"]), message)

        consumer = asyncio.create_task(consume_output())
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            await consumer
        except asyncio.TimeoutError as exc:
            await _terminate_process_tree(process)
            raise RepositoryQualityRunError(
                f"OSA не завершила repository_quality за {timeout} секунд."
            ) from exc
        except asyncio.CancelledError:
            await _terminate_process_tree(process)
            raise
        finally:
            if not consumer.done():
                consumer.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(consumer, heartbeat_task, return_exceptions=True)

    tail = "\n".join(log_lines[-160:])
    if process.returncode != 0:
        raise RepositoryQualityRunError(
            f"OSA завершилась с кодом {process.returncode}.\n{tail[-8000:]}"
        )

    result_path, raw = find_report(output_dir)
    if on_progress:
        await on_progress(99, "Результат OSA получен.")
    return raw, result_path, tail[-12000:]
