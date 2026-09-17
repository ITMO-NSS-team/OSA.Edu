from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable


DEFAULT_HOST_LLM_MODEL = "gpt-5.6-luna"
_STATUS_TTL_SECONDS = 15.0
_status_cache: tuple[float, dict[str, Any]] | None = None


class HostLlmError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, provider_code: str = "host_llm_error"):
        super().__init__(message)
        self.status = status
        self.provider_code = provider_code
        self.provider_name = "Host LLM (subscription)"
        self.retry_after_ms = 0
        self.request_id = ""
        self.quota_metric = ""
        self.quota_description = ""
        self.network_code = ""


def normalize_host_model(model: str | None) -> str:
    value = (model or DEFAULT_HOST_LLM_MODEL).strip()
    if value.startswith("openai/"):
        value = value.removeprefix("openai/")
    return value or DEFAULT_HOST_LLM_MODEL


def host_command() -> str | None:
    configured = os.getenv("HOST_LLM_COMMAND", "").strip()
    return shutil.which(configured) if configured else None


def host_bridge_dir() -> Path | None:
    configured = os.getenv("HOST_LLM_BRIDGE_DIR", "").strip()
    return Path(configured).resolve() if configured else None


def host_provider_name() -> str:
    return (
        "Host LLM bridge (subscription)"
        if host_bridge_dir()
        else "Host LLM command (subscription)"
    )


def host_provider_status(*, force: bool = False) -> dict[str, Any]:
    global _status_cache
    now = time.monotonic()
    bridge_dir = host_bridge_dir()
    if bridge_dir:
        bridge_dir.mkdir(parents=True, exist_ok=True)
        return {
            "installed": True,
            "authenticated": True,
            "path": str(bridge_dir),
            "detail": "Host bridge готов; запросы выполняются моделью Luna в текущей подписке.",
            "transport": "host_bridge",
        }
    if not force and _status_cache and now - _status_cache[0] < _STATUS_TTL_SECONDS:
        return dict(_status_cache[1])

    executable = host_command()
    if not executable:
        result = {
            "installed": False,
            "authenticated": False,
            "path": None,
            "detail": "HOST_LLM_COMMAND не задан или указанная команда недоступна.",
        }
        _status_cache = (now, result)
        return dict(result)

    try:
        completed = subprocess.run(
            [executable, "login", "status"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        detail = (completed.stdout or "").strip()
        authenticated = completed.returncode == 0 and "not logged in" not in detail.lower()
        result = {
            "installed": True,
            "authenticated": authenticated,
            "path": executable,
            "detail": detail or ("Вход выполнен." if authenticated else "Вход не выполнен."),
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        result = {
            "installed": True,
            "authenticated": False,
            "path": executable,
            "detail": f"Не удалось проверить состояние Host LLM: {type(exc).__name__}: {exc}",
        }
    _status_cache = (now, result)
    return dict(result)


def _prompt(system_prompt: str, user_message: str, *, allow_web_search: bool = False) -> str:
    tool_instruction = (
        "Use live web search when the supplied instruction requires it, but do not inspect local files or run shell commands. "
        if allow_web_search
        else "Do not inspect files, run shell commands, browse, or ask questions. "
    )
    return (
        "You are a JSON-only inference worker inside OSA.Edu. "
        f"{tool_instruction}"
        "Do not ask questions. "
        "Follow the supplied system instruction and return only the requested JSON value, "
        "without Markdown fences or commentary.\n\n"
        "<system_instruction>\n"
        f"{system_prompt.strip()}\n"
        "</system_instruction>\n\n"
        "<user_message>\n"
        f"{user_message.strip()}\n"
        "</user_message>"
    )


def _command(
    executable: str,
    model: str,
    output_path: Path,
    workspace: Path,
    *,
    allow_web_search: bool = False,
) -> list[str]:
    command = [executable]
    if allow_web_search:
        command.append("--search")
    command.extend([
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--model",
        normalize_host_model(model),
        "--cd",
        str(workspace),
        "--output-last-message",
        str(output_path),
        "-",
    ])
    return command


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    # ChatGPT sign-in must be used for this provider. Avoid silently charging an
    # API account if a key happens to be present in the parent process.
    env.pop("OPENAI_API_KEY", None)
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    return env


def _run_host_bridge_sync(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    timeout_seconds: int,
    allow_web_search: bool,
) -> str:
    bridge_dir = host_bridge_dir()
    if bridge_dir is None:
        raise HostLlmError(
            "HOST_LLM_BRIDGE_DIR не настроен.",
            status=503,
            provider_code="host_bridge_not_configured",
        )

    requests_dir = bridge_dir / "requests"
    responses_dir = bridge_dir / "responses"
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    request_id = f"osa-edu-{uuid.uuid4().hex}"
    request_path = requests_dir / f"{request_id}.json"
    response_path = responses_dir / f"{request_id}.json"
    request = {
        "request_id": request_id,
        "agent_name": "osa_edu_luna_worker",
        "model": normalize_host_model(model),
        "prompt": _prompt(system_prompt, user_message, allow_web_search=allow_web_search),
        "allow_web_search": allow_web_search,
        "response_path": str(response_path.resolve()),
        "created_at": time.time(),
    }
    temporary_path = requests_dir / f".{request_id}.tmp"
    temporary_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, request_path)
    print(f"HOST_LLM_BRIDGE_REQUEST {request_path.resolve()}", flush=True)

    started = time.monotonic()
    while not response_path.exists():
        if time.monotonic() - started > timeout_seconds:
            raise HostLlmError(
                f"Host bridge не вернул ответ за {timeout_seconds} секунд.",
                status=504,
                provider_code="host_bridge_timeout",
            )
        time.sleep(0.25)

    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HostLlmError(
            f"Host bridge вернул некорректный JSON: {exc}",
            status=502,
            provider_code="host_bridge_invalid_response",
        ) from exc
    if response.get("request_id") != request_id:
        raise HostLlmError(
            "Host bridge вернул ответ с другим request_id.",
            status=502,
            provider_code="host_bridge_id_mismatch",
        )
    if response.get("error"):
        raise HostLlmError(
            str(response["error"]),
            status=502,
            provider_code="host_bridge_worker_error",
        )
    requested_model = normalize_host_model(model)
    response_model = str(response.get("model") or requested_model)
    if response_model != requested_model:
        raise HostLlmError(
            f"Host bridge использовал {response_model} вместо {requested_model}.",
            status=502,
            provider_code="host_bridge_model_mismatch",
        )
    text = str(response.get("text") or "").strip()
    if not text:
        raise HostLlmError(
            "Host bridge вернул пустой ответ.",
            status=502,
            provider_code="host_bridge_empty_response",
        )
    return text


def run_host_llm_sync(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    timeout_seconds: int | None = None,
    allow_web_search: bool = False,
) -> str:
    timeout = timeout_seconds or max(30, int(os.getenv("HOST_LLM_REQUEST_TIMEOUT_SECONDS", "600")))
    if host_bridge_dir():
        return _run_host_bridge_sync(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            timeout_seconds=timeout,
            allow_web_search=allow_web_search,
        )

    status = host_provider_status()
    if not status["installed"]:
        raise HostLlmError(str(status["detail"]), status=503, provider_code="host_unavailable")
    if not status["authenticated"]:
        raise HostLlmError(
            "Host LLM не авторизован. Авторизуйте команду, указанную в HOST_LLM_COMMAND.",
            status=401,
            provider_code="host_not_authenticated",
        )

    executable = str(status["path"])
    with tempfile.TemporaryDirectory(prefix="osa-edu-host-") as raw_workspace:
        workspace = Path(raw_workspace)
        output_path = workspace / "last-message.txt"
        try:
            completed = subprocess.run(
                _command(executable, model, output_path, workspace, allow_web_search=allow_web_search),
                input=_prompt(system_prompt, user_message, allow_web_search=allow_web_search),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=_clean_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise HostLlmError(
                f"Host LLM provider не завершил запрос за {timeout} секунд.",
                status=504,
                provider_code="host_timeout",
            ) from exc

        raw = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        if completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout or "").strip()[-4000:]
            lowered = diagnostic.lower()
            status_code = 401 if "login" in lowered or "auth" in lowered else 502
            raise HostLlmError(
                f"Host LLM provider завершился с кодом {completed.returncode}: {diagnostic or 'нет диагностического вывода'}",
                status=status_code,
                provider_code="host_exec_failed",
            )
        if not raw:
            raw = (completed.stdout or "").strip()
        if not raw:
            raise HostLlmError(
                "Host LLM provider вернул пустой ответ.",
                status=502,
                provider_code="host_empty_response",
            )
        return raw


async def run_host_llm(
    *,
    model: str,
    system_prompt: str,
    user_message: str,
    timeout_seconds: int | None = None,
    allow_web_search: bool = False,
) -> str:
    return await asyncio.to_thread(
        run_host_llm_sync,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        timeout_seconds=timeout_seconds,
        allow_web_search=allow_web_search,
    )


class OsaHostLlmHandler:
    """Duck-typed OSA ModelHandler backed by the user's Host LLM subscription."""

    def __init__(self, model_settings: Any):
        self.model_settings = model_settings
        self.max_retries = max(1, int(getattr(model_settings, "max_retries", 3)))
        self.last_successful_model: str | None = None
        self.successful_models: list[str] = []

    def _system(self, system_message: str | None) -> str:
        return system_message or str(getattr(self.model_settings, "system_prompt", ""))

    def _record_success(self) -> None:
        model = normalize_host_model(getattr(self.model_settings, "model", None))
        self.last_successful_model = model
        if model not in self.successful_models:
            self.successful_models.append(model)

    def reset_model_provenance(self) -> None:
        self.last_successful_model = None
        self.successful_models.clear()

    def reset_to_primary_model(self) -> None:
        return None

    def send_request(self, prompt: str, system_message: str = None, retry_delay: float = 1) -> str:
        raw = run_host_llm_sync(
            model=getattr(self.model_settings, "model", DEFAULT_HOST_LLM_MODEL),
            system_prompt=self._system(system_message),
            user_message=prompt,
        )
        self._record_success()
        return raw

    async def async_request(self, prompt: str, system_message: str = None, retry_delay: float = 1) -> str:
        raw = await run_host_llm(
            model=getattr(self.model_settings, "model", DEFAULT_HOST_LLM_MODEL),
            system_prompt=self._system(system_message),
            user_message=prompt,
        )
        self._record_success()
        return raw

    @staticmethod
    def _parse(raw: str, parser: Any) -> Any:
        from osa_tool.core.llm.llm import _parse_llm_response

        return _parse_llm_response(raw, parser)

    def send_and_parse(self, prompt: str, parser: Any, system_message: str = None, retry_delay: float = 0.5):
        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._parse(self.send_request(prompt, system_message), parser)
            except BaseException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(retry_delay)
        assert last_error is not None
        raise last_error

    async def async_send_and_parse(
        self,
        prompt: str,
        parser: Any,
        system_message: str = None,
        retry_delay: float = 0.5,
    ):
        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._parse(await self.async_request(prompt, system_message), parser)
            except BaseException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(retry_delay)
        assert last_error is not None
        raise last_error

    async def generate_concurrently(self, prompts: list[str], system_message: str = None) -> list[str]:
        # Subscription-backed CLI runs are intentionally serialized to avoid a
        # burst of independent Host LLM sessions.
        return [await self.async_request(prompt, system_message) for prompt in prompts]

    def run_chain(self, prompt: str, parser: Any, system_message: str = None, retry_delay: float = 0.5) -> Any:
        raw = self.send_request(prompt, system_message)
        if hasattr(parser, "parse"):
            return parser.parse(raw)
        return self._parse(raw, parser)

    async def async_run_chain(
        self,
        prompt: str,
        parser: Any,
        system_message: str = None,
        retry_delay: float = 0.5,
    ) -> Any:
        raw = await self.async_request(prompt, system_message)
        if hasattr(parser, "parse"):
            return parser.parse(raw)
        return self._parse(raw, parser)


def patch_osa_model_handler() -> bool:
    if os.getenv("REPRODUCIBILITY_USE_HOST_LLM", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False

    from osa_tool.core.llm.llm import ModelHandlerFactory

    if getattr(ModelHandlerFactory, "_osa_edu_host_patched", False):
        return False

    @classmethod
    def build(cls, model_settings: Any) -> OsaHostLlmHandler:
        return OsaHostLlmHandler(model_settings)

    ModelHandlerFactory.build = build
    ModelHandlerFactory._osa_edu_host_patched = True
    return True
