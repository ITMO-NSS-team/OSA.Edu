from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

ProgressCallback = Callable[[int, str], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
PROGRESS_PREFIX = "OSA_EDU_PROGRESS "

# OSA uses Rich for CLI output. In a real terminal these sequences are interpreted
# as colors, cursor movement and clickable file links. OSA.Edu captures stdout and
# renders it in the browser, so we strip the terminal-only control sequences first.
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_RE = re.compile(r"\x1b[@-_]")


def _strip_terminal_sequences(text: str) -> str:
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC_RE.sub("", text)
    # Rich progress bars also use carriage returns/backspaces for in-place updates.
    return text.replace("\r", "").replace("\b", "")


class OsaRunError(RuntimeError):
    pass


def osa_installed() -> bool:
    if os.getenv("REPRODUCIBILITY_OSA_COMMAND", "").strip():
        return True
    return importlib.util.find_spec("osa_tool") is not None


def configured_model() -> str:
    return (
        os.getenv("REPRODUCIBILITY_MODEL", "").strip()
        or os.getenv("LITERATURE_REVIEW_MODEL", "").strip()
        or "z-ai/glm-5.3-flash"
    )


def configured_base_url() -> str:
    explicit = os.getenv("REPRODUCIBILITY_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    base = os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai").strip().rstrip("/")
    if base.endswith("/api/v1") or base.endswith("/v1"):
        return base
    if base == "https://openrouter.ai":
        return f"{base}/api/v1"
    return base


def build_osa_command(repository: str, paper_path: Path, output_dir: Path, *, mode: str = "full", claims_path: Path | None = None) -> list[str]:
    """Run OSA's canonical paper-analysis CLI from feat/thesis-repository-analysis.

    OSA.Edu owns only job lifecycle and presentation. PDF parsing, claim extraction,
    filtering, batching and repository verification stay inside OSA.
    """
    model = configured_model()
    template = os.getenv("REPRODUCIBILITY_OSA_COMMAND", "").strip()
    if template:
        values = {
            "repository": repository,
            "paper": str(paper_path),
            "output": str(output_dir),
            "model": model,
            "base_url": configured_base_url(),
            "mode": mode,
            "claims": str(claims_path) if claims_path else "",
        }
        return [part.format(**values) for part in shlex.split(template)]

    python = os.getenv("REPRODUCIBILITY_OSA_PYTHON", "").strip() or sys.executable
    command = [
        python,
        "-m",
        "osa_tool.run",
        "--paper-analysis",
        "--repository",
        repository,
        "--paper-output-dir",
        str(output_dir),
        "--skip-repository-quality",
        "--only-high-medium-verifiability",
        "--hide-low-confidence",
        "--delete-dir",
        "--base-url",
        configured_base_url(),
        "--model-paper-claims",
        model,
        "--model-paper-verification",
        model,
        "--context-window",
        os.getenv("REPRODUCIBILITY_CONTEXT_WINDOW", "65536"),
        "--max-tokens",
        os.getenv("REPRODUCIBILITY_MAX_TOKENS", "8000"),
        "--max-retries",
        os.getenv("REPRODUCIBILITY_LLM_MAX_RETRIES", "3"),
    ]
    if mode == "verification-only":
        if not claims_path:
            raise OsaRunError("Для возобновления проверки не найден claims.json.")
        command.extend(["--claims-json", str(claims_path)])
    else:
        command.extend(["--paper", str(paper_path)])
    return command


def _tail(text: str, limit: int = 12000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[-limit:]


def _is_web_analysis_envelope(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    verification = value.get("claim_verification")
    paper_claims = value.get("paper_claims")
    return isinstance(verification, dict) and isinstance(verification.get("claims"), list) and isinstance(paper_claims, dict)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def find_analysis(output_dir: Path) -> tuple[Path, dict[str, Any]]:
    exact = sorted(output_dir.rglob("paper_analysis.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in exact:
        value = _load_json(path)
        if value and _is_web_analysis_envelope(value):
            return path, value

    for path in sorted(output_dir.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        value = _load_json(path)
        if value and _is_web_analysis_envelope(value):
            return path, value

    raise OsaRunError(
        "OSA завершилась, но web presentation paper_analysis.json не найден. "
        "Ожидается schema с paper_claims и claim_verification.claims."
    )


def _enrich_source_metadata(value: dict[str, Any], repository: str, paper_path: Path) -> None:
    meta = value.setdefault("meta", {})
    if not isinstance(meta, dict):
        return
    source = meta.setdefault("source", {})
    if not isinstance(source, dict):
        return
    source.setdefault("repository", repository)
    paper = source.setdefault("paper", {})
    if isinstance(paper, dict):
        paper.setdefault("path", paper_path.name)


def _parse_progress(line: str) -> tuple[int, str] | None:
    """Translate bridge markers and OSA's native logs into web-only progress.

    This parser changes presentation only. It never affects OSA execution.
    """
    if line.startswith(PROGRESS_PREFIX):
        try:
            payload = json.loads(line[len(PROGRESS_PREFIX) :])
            percent = int(payload.get("percent", 0))
            message = str(payload.get("message") or "OSA выполняет проверку.")
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        return max(1, min(99, percent)), message

    clean = _strip_terminal_sequences(line)
    if "Paper analysis stage started: Repository clone" in clean or "Cloning repository" in clean:
        return 11, "OSA: клонирует репозиторий."
    if "Paper analysis stage completed: Repository clone" in clean or "Repository cloned" in clean:
        return 18, "Репозиторий готов."
    if "Paper analysis stage started: Repository file-tree collection" in clean:
        return 20, "OSA: строит дерево файлов репозитория."
    if "Paper analysis stage started: Paper-claim extraction" in clean:
        return 24, "OSA: извлекает утверждения из работы."
    if "Paper analysis stage completed: Paper-claim extraction" in clean:
        return 52, "Утверждения извлечены; готовим проверку по коду."
    if "Paper analysis stage started: Claim verification" in clean:
        return 55, "OSA ClaimVerifier: проверяем утверждения по репозиторию."
    batch = re.search(r"Verifying batch (\d+)/(\d+)", clean, re.IGNORECASE)
    if batch:
        current, total = int(batch.group(1)), max(1, int(batch.group(2)))
        return 55 + round((current - 1) / total * 38), f"OSA ClaimVerifier: batch {current}/{total}."
    if "Claim verification complete" in clean or "Paper analysis stage completed: Claim verification" in clean:
        return 95, "OSA ClaimVerifier завершил проверку."
    if "Paper analysis stage started: Writing canonical artifacts" in clean:
        return 97, "OSA формирует итоговый paper_analysis.json."

    if "Stage 1/4: starting PDF splitting" in clean:
        return 26, "OSA: разбивает PDF на части."
    if "Stage 2/4: starting Marker conversion" in clean:
        return 28, "OSA Marker: преобразуем PDF в Markdown."

    chunk = re.search(r"Converting chunk (\d+)/(\d+) with Marker", clean)
    if chunk:
        current, total = int(chunk.group(1)), max(1, int(chunk.group(2)))
        percent = 28 + round((current - 1) / total * 13)
        return percent, f"OSA Marker: часть {current}/{total}."

    if "Marker conversion completed" in clean or "Stage 2/4 completed" in clean:
        return 41, "OSA Marker завершён. Markdown готов."
    if "Stage 3/4: parsing converted Markdown into sections" in clean:
        return 42, "OSA: разбирает Markdown на разделы."
    if "Stage 3/4 completed" in clean:
        return 44, "OSA: разделы Markdown готовы."
    if "Stage 4/4: starting claim extraction" in clean:
        return 45, "OSA: запускает ClaimExtractor."

    selected = re.search(r"selected (\d+)/(\d+) candidate sections", clean, re.IGNORECASE)
    if selected:
        return 46, f"OSA: выбрано {selected.group(1)} из {selected.group(2)} разделов для claims."

    section_progress = re.search(r"Extracting section claims.*?([0-9]{1,3})%", clean)
    if section_progress:
        inner = max(0, min(100, int(section_progress.group(1))))
        return 46 + round(inner * 0.05), f"OSA ClaimExtractor: {inner}% выбранных разделов."

    if "three-step claim extraction" in clean.lower() and "completed" in clean.lower():
        return 51, "OSA ClaimExtractor завершил выделение claims."
    if "PaperClaimPipeline.run() completed" in clean:
        return 52, "OSA PaperClaimPipeline завершён."
    if "Preparing repository checkout" in clean:
        return 53, "Готовим репозиторий для ClaimVerifier."
    if "Repository checkout ready" in clean:
        return 54, "Репозиторий готов; запускаем ClaimVerifier."
    return None


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        def taskkill() -> None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        await asyncio.to_thread(taskkill)
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
        return
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    await process.wait()


async def run_osa_analysis(
    repository: str,
    paper_path: Path,
    output_dir: Path,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
    *,
    mode: str = "full",
    claims_path: Path | None = None,
    display_repository: str | None = None,
    display_paper_name: str | None = None,
    paper_display_name: str | None = None,
    **compat_kwargs: Any,
) -> tuple[dict[str, Any], Path, str]:
    if not osa_installed():
        raise OsaRunError(
            'Пакет OSA не установлен. Установите `osa_tool[paper-claims]` из ветки feat/thesis-repository-analysis '
            "или задайте REPRODUCIBILITY_OSA_COMMAND."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "osa.log"
    command = build_osa_command(repository, paper_path, output_dir, mode=mode, claims_path=claims_path)
    if on_progress:
        if mode == "verification-only":
            await on_progress(68, "Возобновляем проверку: claims уже выделены, запускаем только проверку по репозиторию.")
        else:
            await on_progress(10, "Запускаем штатный OSA paper-analysis: PDF → claims → проверка по репозиторию.")

    env = os.environ.copy()
    openrouter_key = env.get("OPENROUTER_API_KEY", "").strip()
    if openrouter_key and not env.get("OPENAI_API_KEY", "").strip():
        env["OPENAI_API_KEY"] = openrouter_key
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")

    # Tell Rich/CLI libraries that stdout is being captured rather than displayed
    # in an interactive terminal. We still sanitize output below as a safety net.
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
    timeout = max(60, int(os.getenv("REPRODUCIBILITY_TIMEOUT_SECONDS", "3600")))
    heartbeat_seconds = max(10, int(os.getenv("REPRODUCIBILITY_HEARTBEAT_SECONDS", "20")))
    log_lines: list[str] = []
    state = {
        "percent": 10,
        "message": "OSA запущена.",
        "last_line": "",
        "last_progress_at": time.monotonic(),
    }
    started = time.monotonic()

    with log_path.open("w", encoding="utf-8", buffering=1) as log_file:
        repository_for_display = display_repository or str(compat_kwargs.get("repository_display_url") or repository)
        paper_for_display = (
            display_paper_name
            or paper_display_name
            or str(compat_kwargs.get("display_paper") or compat_kwargs.get("paper_name") or paper_path.name)
        )
        header = [
            f"[OSA.Edu] PID: {process.pid}",
            f"[OSA.Edu] Python: {command[0]}",
            f"[OSA.Edu] Model: {configured_model()}",
            f"[OSA.Edu] Base URL: {configured_base_url()}",
            f"[OSA.Edu] Repository: {repository}",
            f"[OSA.Edu] Repository display URL: {repository_for_display}",
            f"[OSA.Edu] Paper: {paper_path}",
            f"[OSA.Edu] Paper display name: {paper_for_display}",
            f"[OSA.Edu] Mode: {mode}",
            f"[OSA.Edu] Claims path: {claims_path}" if claims_path else "[OSA.Edu] Claims path: -",
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
                line = _strip_terminal_sequences(
                    raw.decode("utf-8", errors="replace")
                ).rstrip("\r\n")
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
                    state["last_progress_at"] = time.monotonic()
                    if on_progress:
                        await on_progress(*parsed)

        async def heartbeat() -> None:
            while process.returncode is None:
                await asyncio.sleep(heartbeat_seconds)
                if process.returncode is not None:
                    return
                if not on_progress:
                    continue
                elapsed = _format_elapsed(time.monotonic() - started)
                message = str(state["message"])
                last_line = str(state["last_line"]).strip()
                if last_line and not last_line.startswith(PROGRESS_PREFIX):
                    compact = last_line[-220:]
                    message = f"{message} Работает {elapsed}. Последний лог: {compact}"
                else:
                    message = f"{message} Работает {elapsed}."
                await on_progress(int(state["percent"]), message)

        reader = asyncio.create_task(consume_output(), name="osa-reproducibility-log-reader")
        heart = asyncio.create_task(heartbeat(), name="osa-reproducibility-heartbeat")
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            await reader
        except asyncio.CancelledError:
            reader.cancel()
            heart.cancel()
            await _terminate_process_tree(process)
            raise
        except asyncio.TimeoutError as exc:
            reader.cancel()
            heart.cancel()
            await _terminate_process_tree(process)
            raise OsaRunError(f"OSA не завершилась за {timeout} секунд. Полный лог: {log_path}") from exc
        finally:
            heart.cancel()
            try:
                await heart
            except asyncio.CancelledError:
                pass

    log = "\n".join(log_lines)
    if process.returncode != 0:
        raise OsaRunError(
            f"OSA завершилась с кодом {process.returncode}. Полный лог: {log_path}\n{_tail(log)}"
        )

    if on_progress:
        await on_progress(99, "OSA завершила расчёт. Загружаем paper_analysis.json.")

    result_path, result = find_analysis(output_dir)
    _enrich_source_metadata(result, repository, paper_path)
    presentation_path = output_dir / "paper_analysis.json"
    presentation_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, presentation_path, _tail(log)


def cleanup_run_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
