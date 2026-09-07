from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

ProgressHandler = Callable[[float, float | None, str | None], Awaitable[None]]
logger = logging.getLogger(__name__)


async def inspect_server(*, url: str) -> dict[str, Any]:
    Client = _fastmcp_client()
    client = Client(url)
    diagnostics = await collect_endpoint_diagnostics(url)
    try:
        logger.info('Inspecting MCP server at %s diagnostics=%s', url, diagnostics)
        async with client:
            prompts = await client.list_prompts()
            tools = await client.list_tools()
    except Exception as exc:
        logger.exception('MCP list_tools failed at %s diagnostics=%s', url, diagnostics)
        raise RuntimeError(_mcp_error_message(exc, url, 'list_tools', diagnostics)) from exc
    return {
        'ok': True,
        'diagnostics': diagnostics,
        'prompts': [_describe_mcp_item(item) for item in prompts],
        'tools': [_describe_mcp_item(item) for item in tools],
    }


async def submit_document(
    *,
    url: str,
    dag_id: str,
    pdf_path: Path,
    progress_handler: ProgressHandler | None = None,
) -> dict[str, Any]:
    Client = _fastmcp_client()
    pdf_bytes = await asyncio.to_thread(pdf_path.read_bytes)
    pdf_b64 = base64.b64encode(pdf_bytes).decode('ascii')
    client = Client(url, progress_handler=progress_handler)
    try:
        logger.info('Calling MCP submit_document at %s dag_id=%s file=%s bytes=%s', url, dag_id, pdf_path.name, len(pdf_bytes))
        async with client:
            result = await client.call_tool('submit_document', {'pdf_file': pdf_b64, 'dag_id': dag_id or None})
    except Exception as exc:
        diagnostics = await collect_endpoint_diagnostics(url)
        logger.exception('MCP submit_document failed at %s dag_id=%s diagnostics=%s', url, dag_id, diagnostics)
        raise RuntimeError(_mcp_error_message(exc, url, 'submit_document', diagnostics)) from exc
    return _structured_content(result)


async def generate_pdf_report(
    *,
    url: str,
    dag_id: str,
    run_id: str,
    progress_handler: ProgressHandler | None = None,
) -> dict[str, Any]:
    Client = _fastmcp_client()
    client = Client(url, progress_handler=progress_handler)
    try:
        logger.info('Calling MCP generate_pdf_report at %s dag_id=%s run_id=%s', url, dag_id, run_id)
        async with client:
            result = await client.call_tool('generate_pdf_report', {'run_id': run_id, 'dag_id': dag_id or None})
    except Exception as exc:
        diagnostics = await collect_endpoint_diagnostics(url)
        logger.exception('MCP generate_pdf_report failed at %s dag_id=%s run_id=%s diagnostics=%s', url, dag_id, run_id, diagnostics)
        raise RuntimeError(_mcp_error_message(exc, url, 'generate_pdf_report', diagnostics)) from exc
    return _structured_content(result)


async def download_pdf(url: str, target: Path, *, timeout_seconds: int) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
        logger.info('Downloading normcontrol PDF from %s to %s', url, target)
        response = await client.get(url)
        response.raise_for_status()
    tmp = Path(f'{target}.tmp')
    tmp.write_bytes(response.content)
    tmp.replace(target)
    return len(response.content)


def _fastmcp_client() -> Any:
    try:
        from fastmcp import Client
    except ImportError as exc:
        raise RuntimeError('Не установлен пакет fastmcp. Выполните pip install -r requirements.txt и перезапустите backend.') from exc
    return Client


async def collect_endpoint_diagnostics(url: str) -> dict[str, str]:
    diagnostics: dict[str, str] = {'url': url}
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    diagnostics['scheme'] = parsed.scheme or ''
    diagnostics['host'] = host or ''
    diagnostics['port'] = str(port)
    if not host:
        diagnostics['urlError'] = 'URL does not include a hostname.'
        return diagnostics

    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        addresses = sorted({item[4][0] for item in infos})
        diagnostics['dns'] = ','.join(addresses[:5]) or 'ok'
    except Exception as exc:
        diagnostics['dnsError'] = _exception_details(exc)

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
        writer.close()
        await writer.wait_closed()
        diagnostics['tcp'] = 'ok'
        del reader
    except Exception as exc:
        diagnostics['tcpError'] = _exception_details(exc)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
            async with client.stream('GET', url, headers={'Accept': 'application/json, text/event-stream'}) as response:
                diagnostics['httpGetStatus'] = str(response.status_code)
                diagnostics['httpGetContentType'] = response.headers.get('content-type', '')
                diagnostics['httpGetLocation'] = response.headers.get('location', '')
    except Exception as exc:
        diagnostics['httpGetError'] = _exception_details(exc)
    return diagnostics


def _mcp_error_message(exc: BaseException, url: str, tool: str, diagnostics: dict[str, str] | None = None) -> str:
    detail = _exception_details(exc)
    diagnostics_text = _format_diagnostics(diagnostics)
    if 'session terminated' in detail.lower():
        return (
            f"MCP session terminated while calling {tool} at {url}. "
            "For dev Docker this usually means the backend container cannot use the host VPN route, "
            "the report service is exposed only through host-local networking, or the URL is not the exact MCP endpoint. "
            f"Original error: {detail}{diagnostics_text}"
        )
    return f"MCP call {tool} failed at {url}: {detail}{diagnostics_text}"


def _format_diagnostics(diagnostics: dict[str, str] | None) -> str:
    if not diagnostics:
        return ''
    ordered = [
        'dns',
        'dnsError',
        'tcp',
        'tcpError',
        'httpGetStatus',
        'httpGetContentType',
        'httpGetLocation',
        'httpGetError',
    ]
    values = [f'{key}={diagnostics[key]}' for key in ordered if diagnostics.get(key)]
    return f" Endpoint diagnostics: {'; '.join(values)}" if values else ''


def _exception_details(exc: BaseException) -> str:
    parts: list[str] = []
    for item in _walk_exceptions(exc, set()):
        text = str(item).strip()
        parts.append(f'{item.__class__.__name__}: {text}' if text else item.__class__.__name__)
    return ' -> '.join(parts)


def _walk_exceptions(exc: BaseException, seen: set[int]) -> list[BaseException]:
    if id(exc) in seen:
        return []
    seen.add(id(exc))
    result = [exc]
    nested = getattr(exc, 'exceptions', None)
    if isinstance(nested, tuple):
        for item in nested:
            if isinstance(item, BaseException):
                result.extend(_walk_exceptions(item, seen))
    if exc.__cause__:
        result.extend(_walk_exceptions(exc.__cause__, seen))
    if exc.__context__:
        result.extend(_walk_exceptions(exc.__context__, seen))
    return result


def _structured_content(result: Any) -> dict[str, Any]:
    value = getattr(result, 'structured_content', None)
    if value is None:
        value = getattr(result, 'structuredContent', None)
    if value is None:
        value = _json_content(result)
    if isinstance(value, dict):
        return value
    raise RuntimeError('MCP вернул ответ без structured_content.')


def _json_content(result: Any) -> dict[str, Any] | None:
    content = getattr(result, 'content', None)
    if not isinstance(content, list):
        return None
    for item in content:
        text = getattr(item, 'text', None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _describe_mcp_item(item: Any) -> dict[str, str | None]:
    return {
        'name': _optional_text(getattr(item, 'name', None)),
        'title': _optional_text(getattr(item, 'title', None)),
        'description': _optional_text(getattr(item, 'description', None)),
    }


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
