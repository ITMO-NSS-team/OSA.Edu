from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..llm.host_llm import host_provider_status


def _run(command: list[str], *, timeout: int = 20, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def osa_version() -> str | None:
    for name in ("osa_tool", "osa-tool"):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def git_status() -> dict[str, Any]:
    executable = shutil.which("git")
    if not executable:
        return {"installed": False, "version": None, "path": None}
    try:
        result = _run([executable, "--version"], timeout=5)
        version = (result.stdout or "").strip() or None
    except Exception as exc:  # pragma: no cover - environment dependent
        version = f"Ошибка проверки git: {type(exc).__name__}: {exc}"
    return {"installed": True, "version": version, "path": executable}


def cuda_status() -> dict[str, Any]:
    """Probe torch/CUDA in a child process so the FastAPI worker does not retain GPU state."""
    script = r'''
import json
try:
    import torch
    available = bool(torch.cuda.is_available())
    payload = {
        "torchInstalled": True,
        "torchVersion": getattr(torch, "__version__", None),
        "available": available,
        "runtime": getattr(torch.version, "cuda", None),
        "device": torch.cuda.get_device_name(0) if available else None,
        "deviceCount": torch.cuda.device_count() if available else 0,
    }
except Exception as exc:
    payload = {
        "torchInstalled": False,
        "available": False,
        "error": f"{type(exc).__name__}: {exc}",
    }
print(json.dumps(payload, ensure_ascii=False))
'''
    try:
        result = _run([sys.executable, "-c", script], timeout=25)
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if result.returncode != 0 or not lines:
            return {
                "torchInstalled": False,
                "available": False,
                "error": (result.stdout or f"python exited with {result.returncode}").strip()[-2000:],
            }
        return json.loads(lines[-1])
    except subprocess.TimeoutExpired:
        return {"torchInstalled": None, "available": None, "error": "Проверка CUDA заняла больше 25 секунд."}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"torchInstalled": None, "available": None, "error": f"{type(exc).__name__}: {exc}"}


def runtime_status(*, include_cuda: bool = False) -> dict[str, Any]:
    version = sys.version_info
    python_supported = (3, 11) <= (version.major, version.minor) <= (3, 14)
    result: dict[str, Any] = {
        "pythonVersion": f"{version.major}.{version.minor}.{version.micro}",
        "pythonExecutable": sys.executable,
        "pythonSupportedForPaperClaims": python_supported,
        "platform": sys.platform,
        "osaInstalled": osa_version() is not None,
        "osaVersion": osa_version(),
        "git": git_status(),
    }
    if include_cuda:
        result["cuda"] = cuda_status()
    return result


def _sourcecraft_probe_urls(repository: str) -> tuple[list[str], str | None]:
    """Build safe git probe URLs for a SourceCraft repository.

    The user-facing URL remains https://sourcecraft.dev/<owner>/<repo>. The
    public git endpoint is tried first. If SOURCECRAFT_TOKEN (or OSA's GIT_TOKEN
    fallback) is configured, an authenticated probe is tried second.
    """
    try:
        parsed = urlparse(repository.strip())
    except ValueError:
        return [repository], None

    host = (parsed.hostname or "").lower()
    if host not in {"sourcecraft.dev", "git.sourcecraft.dev"}:
        return [repository], None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return [repository], None

    owner, repo = parts[0], parts[1]
    repo = repo[:-4] if repo.lower().endswith(".git") else repo
    public_url = f"https://git@git.sourcecraft.dev/{owner}/{repo}.git"

    token = (os.getenv("SOURCECRAFT_TOKEN", "").strip() or os.getenv("GIT_TOKEN", "").strip())
    urls = [public_url]
    if token:
        urls.append(f"https://git:{quote(token, safe='')}@git.sourcecraft.dev/{owner}/{repo}.git")
    return urls, token or None


def _redact(value: str, secret: str | None) -> str:
    if secret:
        value = value.replace(secret, "***").replace(quote(secret, safe=""), "***")
    return value


def repository_access(repository: str) -> dict[str, Any]:
    executable = shutil.which("git")
    if not executable:
        return {"ok": False, "detail": "git не найден в PATH."}

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    probe_urls, sourcecraft_token = _sourcecraft_probe_urls(repository)
    last_detail = ""

    for index, probe_url in enumerate(probe_urls):
        try:
            result = _run([executable, "ls-remote", probe_url, "HEAD"], timeout=30, env=env)
        except subprocess.TimeoutExpired:
            last_detail = "git ls-remote не ответил за 30 секунд. Проверьте сеть/VPN/DNS."
            continue
        except Exception as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            continue

        output = _redact((result.stdout or "").strip(), sourcecraft_token)
        if result.returncode == 0:
            if sourcecraft_token and index > 0:
                return {
                    "ok": True,
                    "detail": "SourceCraft-репозиторий доступен с SOURCECRAFT_TOKEN/GIT_TOKEN.",
                    "authenticated": True,
                }
            return {
                "ok": True,
                "detail": "Репозиторий доступен через git ls-remote.",
                "authenticated": False,
            }
        last_detail = output[-3000:] or f"git ls-remote завершился с кодом {result.returncode}."

    if sourcecraft_token is None and "sourcecraft.dev" in repository.lower():
        suffix = " Если репозиторий приватный, добавьте SOURCECRAFT_TOKEN в .env."
        last_detail = (last_detail + suffix).strip()

    return {"ok": False, "detail": _redact(last_detail, sourcecraft_token), "authenticated": False}


def _openrouter_key() -> tuple[str | None, str | None]:
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return os.getenv("OPENROUTER_API_KEY", "").strip(), "OPENROUTER_API_KEY"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return os.getenv("OPENAI_API_KEY", "").strip(), "OPENAI_API_KEY"
    return None, None


def _host_key_source(status: dict[str, Any]) -> str:
    return "HOST_LLM_BRIDGE_DIR" if status.get("transport") == "host_bridge" else "HOST_LLM_COMMAND"


def _host_llm_access_from_status(status: dict[str, Any]) -> dict[str, Any]:
    ok = bool(status.get("authenticated"))
    source = _host_key_source(status)
    return {
        "ok": ok,
        "checked": True,
        "keySource": source,
        "detail": str(status.get("detail") or ("Host LLM готов." if ok else "Host LLM не готов.")),
        "transport": status.get("transport") or ("host_command" if status.get("installed") else None),
        "path": status.get("path"),
    }


async def llm_access(base_url: str, model: str | None = None, *, use_host_llm: bool = False) -> dict[str, Any]:
    if use_host_llm:
        status = host_provider_status(force=True)
        return _host_llm_access_from_status(status)

    key, source = _openrouter_key()
    if not key:
        return {"ok": False, "checked": True, "keySource": None, "detail": "API key не найден."}

    normalized = base_url.rstrip("/")
    if "openrouter.ai" not in normalized:
        return {
            "ok": True,
            "checked": False,
            "keySource": source,
            "detail": "Ключ найден, но автоматическая проверка выполняется только для OpenRouter.",
        }

    url = normalized + "/key" if normalized.endswith("/api/v1") else normalized + "/api/v1/key"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except Exception as exc:
        return {
            "ok": False,
            "checked": True,
            "keySource": source,
            "detail": f"OpenRouter недоступен: {type(exc).__name__}: {exc}",
        }

    if response.status_code == 200:
        try:
            data = response.json().get("data", {})
        except Exception:
            data = {}
        remaining = data.get("limit_remaining")
        detail = "OpenRouter API key действителен."
        if remaining is not None:
            detail += f" Остаток лимита ключа: {remaining}."

        if model:
            models_url = normalized + "/models" if normalized.endswith("/api/v1") else normalized + "/api/v1/models"
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    models_response = await client.get(models_url, headers={"Authorization": f"Bearer {key}"})
                if models_response.status_code == 200:
                    payload = models_response.json()
                    ids = {str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")}
                    if model not in ids:
                        return {
                            "ok": False,
                            "checked": True,
                            "keySource": source,
                            "detail": detail + f" Но модель {model} не найдена в текущем каталоге OpenRouter.",
                        }
                    detail += f" Модель {model} доступна в каталоге OpenRouter."
                else:
                    detail += f" Каталог моделей не удалось проверить (HTTP {models_response.status_code})."
            except Exception as exc:
                detail += f" Каталог моделей не удалось проверить: {type(exc).__name__}: {exc}."
        return {"ok": True, "checked": True, "keySource": source, "detail": detail}

    try:
        payload = response.json()
        message = payload.get("error", {}).get("message") or payload.get("message")
    except Exception:
        message = None
    return {
        "ok": False,
        "checked": True,
        "keySource": source,
        "detail": f"OpenRouter вернул HTTP {response.status_code}: {message or response.text[:500]}",
    }
