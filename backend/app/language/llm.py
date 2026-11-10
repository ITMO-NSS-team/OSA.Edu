"""Language-only transport: strict schemas, classified failures and accounted fallbacks.

Other OSA workflows keep their existing client and retry policy unchanged.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import env_int
from ..llm.client import _content_text, _retry_after_ms, estimate_tokens
from ..llm.host_llm import HostLlmError, run_host_llm
from ..llm.rate_limiter import reserve_model_capacity


class IssueOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    fragment_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=8000)
    replacement: str = Field(min_length=1, max_length=10000)
    category: Literal['spelling', 'grammar', 'syntax', 'punctuation', 'style']
    explanation: str = Field(min_length=12, max_length=1600)


class DetectionOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reviewed_fragment_ids: list[str]
    issues: list[IssueOutput] = Field(max_length=80)


class VerdictOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str
    verdict: Literal['accept', 'reject', 'uncertain']
    reason: str = Field(min_length=12, max_length=1600)
    corrected_sentence: str


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    verdicts: list[VerdictOutput]


class ReviewError(RuntimeError):
    def __init__(self, kind: str, message: str, *, status: int = 0, retry_after_ms: int = 0):
        super().__init__(message)
        self.kind, self.status, self.retry_after_ms = kind, status, retry_after_ms


def decode(raw: str, schema: type[BaseModel]) -> dict:
    # A complete fenced JSON object is supported. Do not scavenge arbitrary
    # braces, repair commas, or treat a recovered prefix as full coverage.
    value = raw.strip()
    if value.startswith('```json\n') and value.endswith('```'):
        value = value[8:-3].strip()
    if not value:
        raise ReviewError('empty', 'Модель вернула пустой ответ.')
    try:
        data = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
    except (ValueError, TypeError) as exc:
        raise ReviewError('invalid_json', 'Ответ модели не является полным корректным JSON.') from exc
    try:
        return schema.model_validate(data).model_dump()
    except ValidationError as exc:
        raise ReviewError('schema', 'Ответ модели не соответствует обязательной схеме данных.') from exc


def _unsupported(response: httpx.Response, message: str) -> bool:
    return response.status_code in {400, 404, 422} and bool(re.search(
        r'(?:unsupported|not support|not available|unrecognized|unknown).*(?:response_format|json_schema|structured|parameter)|'
        r'(?:response_format|json_schema|structured).*(?:unsupported|not support|invalid)|'
        r'no endpoints found.*(?:parameter|structured)', message, re.I,
    ))


async def request_json(
    *, provider: str, model: str, system: str, message: str,
    schema: type[BaseModel], usage: dict,
    before_request: Callable[[int, int], Awaitable[None]],
    output_limit: int | None = None,
) -> dict:
    schema_value = schema.model_json_schema()
    system += '\nОбязательная JSON Schema ответа:\n' + json.dumps(schema_value, ensure_ascii=False)
    estimated = estimate_tokens(system + '\n' + message)
    maximum_output = min(12000, env_int('LANGUAGE_MAX_OUTPUT_TOKENS', 8000))
    output_limit = min(maximum_output, output_limit or env_int('LANGUAGE_INITIAL_OUTPUT_TOKENS', 4000))
    timeout = min(600, env_int('LANGUAGE_REQUEST_TIMEOUT_SECONDS', 120))
    modes = ['schema', 'json', 'text'] if provider == 'openrouter' else ['text']
    previous_mode = usage.get('responseFormat')
    if previous_mode in modes:
        modes = modes[modes.index(previous_mode):]
    for mode in modes:
        await before_request(estimated, output_limit)
        started = time.monotonic()
        reservation = None
        try:
            reservation = await reserve_model_capacity(provider, model, estimated)
            usage['rateLimitWaitMs'] += reservation.wait_ms
            async with asyncio.timeout(timeout):
                if provider == 'host':
                    raw = await run_host_llm(model=model, system_prompt=system, user_message=message, timeout_seconds=timeout)
                elif provider == 'openrouter':
                    key = os.getenv('OPENROUTER_API_KEY', '').strip()
                    if not key:
                        raise ReviewError('api', 'OPENROUTER_API_KEY не задан.', status=401)
                    body = {
                        'model': model,
                        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': message}],
                        'max_completion_tokens': output_limit,
                        'provider': {'allow_fallbacks': True, 'require_parameters': True},
                    }
                    if mode == 'schema':
                        body['response_format'] = {'type': 'json_schema', 'json_schema': {
                            'name': schema.__name__, 'strict': True, 'schema': schema_value,
                        }}
                    elif mode == 'json':
                        body['response_format'] = {'type': 'json_object'}
                    if os.getenv('OPENROUTER_DATA_COLLECTION', '').strip().lower() == 'deny':
                        body['provider']['data_collection'] = 'deny'
                    if os.getenv('OPENROUTER_ZDR', '').strip().lower() in {'true', '1', 'yes', 'on'}:
                        body['provider']['zdr'] = True
                    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                               'X-OpenRouter-Title': 'OSA.Edu language review'}
                    base = os.getenv('OPENROUTER_API_BASE_URL', 'https://openrouter.ai').rstrip('/')
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(base + '/api/v1/chat/completions', headers=headers, json=body)
                    try:
                        payload = response.json()
                    except ValueError:
                        raise ReviewError('api', 'Провайдер вернул не-JSON HTTP-ответ.', status=response.status_code if response.is_error else 502)
                    if not isinstance(payload, dict):
                        raise ReviewError('api', 'Провайдер вернул некорректный HTTP-ответ.', status=502)
                    error = payload.get('error') or {}
                    detail = str(error.get('message', '')) if isinstance(error, dict) else ''
                    if response.is_error or error:
                        if mode != 'text' and _unsupported(response, detail):
                            usage.setdefault('formatFallbacks', []).append({'from': mode, 'status': response.status_code})
                            usage['responseFormat'] = modes[modes.index(mode) + 1]
                            continue
                        # Never persist an upstream response containing the source text.
                        kind = 'quota' if response.status_code == 429 and re.search(r'daily|per day|billing|spend|credits', detail, re.I) else 'api'
                        raise ReviewError(kind, f'Ошибка API: HTTP {response.status_code}.',
                                          status=response.status_code, retry_after_ms=_retry_after_ms(response))
                    billed = payload.get('usage') or {}
                    if not isinstance(billed, dict):
                        raise ReviewError('api', 'Некорректные данные расхода в ответе API.', status=502)
                    for target, source in (('actualInputTokens', 'prompt_tokens'), ('actualOutputTokens', 'completion_tokens')):
                        if isinstance(billed.get(source), (int, float)):
                            usage[target] = usage.get(target, 0) + int(billed[source])
                    if isinstance(billed.get('cost'), (int, float)):
                        usage['actualCostUsd'] = usage.get('actualCostUsd', 0.0) + billed['cost']
                    choices = payload.get('choices') or []
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        raise ReviewError('empty', 'Провайдер не вернул вариант ответа.')
                    choice = choices[0]
                    if choice.get('finish_reason') in {'length', 'max_tokens'}:
                        raise ReviewError('truncated', 'Ответ усечён лимитом выходных токенов.')
                    if choice.get('error') or choice.get('finish_reason') in {'error', 'content_filter'}:
                        raise ReviewError('api', 'Провайдер не завершил генерацию ответа.')
                    message_value = choice.get('message') or {}
                    if not isinstance(message_value, dict):
                        raise ReviewError('api', 'Некорректное сообщение в ответе API.', status=502)
                    if message_value.get('refusal'):
                        raise ReviewError('refusal', 'Модель отказалась проверять пакет.')
                    raw = _content_text(message_value.get('content'))
                else:
                    raise ReviewError('api', 'Языковая проверка поддерживает OpenRouter и Host LLM.')
            value = decode(raw, schema)
            usage['responseFormat'] = mode
            return value
        except HostLlmError as exc:
            code = exc.provider_code
            kind = 'empty' if 'empty' in code else 'timeout' if 'timeout' in code else 'api'
            status = 401 if code in {'host_unavailable', 'host_not_authenticated'} else (exc.status or 502)
            raise ReviewError(kind, f'Ошибка Host LLM ({code}); пакет не проверен.', status=status) from exc
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ReviewError('timeout', 'Превышено время ожидания ответа модели.') from exc
        except httpx.RequestError as exc:
            raise ReviewError('network', 'Не удалось связаться с провайдером.') from exc
        finally:
            usage['requestDurationMs'] += round((time.monotonic() - started) * 1000)
            if reservation:
                reservation.release()
    raise ReviewError('format_unsupported', 'Провайдер не поддерживает доступные форматы ответа.')
