from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..config import env_bool, env_int
from ..util import empty_usage, now_iso
from .rate_limiter import penalize_model_capacity, reserve_model_capacity

class ModelHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        status: int = 0,
        retry_after_ms: int = 0,
        provider_code: str = '',
        provider_name: str = '',
        request_id: str = '',
        quota_metric: str = '',
        quota_description: str = '',
        network_code: str = '',
    ):
        super().__init__(message)
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.provider_code = provider_code
        self.provider_name = provider_name
        self.request_id = request_id
        self.quota_metric = quota_metric
        self.quota_description = quota_description
        self.network_code = network_code


def estimate_tokens(value: str) -> int:
    chars=max(1,env_int('LLM_CHARS_PER_TOKEN',3))
    return max(1,(len(value)+chars-1)//chars)


def parse_json(raw: str) -> Any:
    stripped=re.sub(r'^```json\s*','',raw.strip(),flags=re.I)
    stripped=re.sub(r'```$','',stripped,flags=re.I).strip()
    try: return json.loads(stripped)
    except json.JSONDecodeError:
        starts=[p for p in (stripped.find('{'),stripped.find('[')) if p>=0]
        start=min(starts) if starts else -1; end=max(stripped.rfind('}'),stripped.rfind(']'))
        if start<0 or end<start: raise ValueError('LLM вернула невалидный JSON.')
        return json.loads(stripped[start:end+1])


def is_fatal_provider_error(error: BaseException) -> bool:
    status=getattr(error,'status',0); code=getattr(error,'provider_code','')
    text=f'{code} {error}'
    return status in {401,402,403} or bool(re.search(r'authentication|invalid[_ ]api[_ ]key|unauthorized|forbidden|insufficient credits|negative credit|billing|PERMISSION_DENIED|API.*disabled',text,re.I))


def _retryable(error: BaseException) -> bool:
    status = getattr(error, 'status', 0)
    text = f'{getattr(error, "provider_code", "")} {error}'
    # Daily/free-tier/billing quota exhaustion is not transient and retrying only burns time.
    permanent_quota = bool(re.search(
        r'per day|daily|\brpd\b|free[- ]model.*day|requests.*day|spend|billing|negative credit|project.*quota.*0',
        text,
        re.I,
    ))
    if status == 429 and permanent_quota:
        return False
    if status in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    return bool(re.search(
        r'overloaded|unavailable|timeout|ECONNRESET|ETIMEDOUT|EAI_AGAIN|fetch failed|aborted|rate.?limit|RESOURCE_EXHAUSTED',
        text,
        re.I,
    ))



def is_retryable_provider_error(error: BaseException) -> bool:
    """True when the provider failure is transient and operation-level code
    should *not* immediately resend the same expensive packet again.
    """
    return _retryable(error) and not is_fatal_provider_error(error)


def adaptive_attempt_limit(provider: str, estimated_input_tokens: int) -> int:
    """Cost-aware transport retry budget.

    Small packets can cheaply tolerate transient provider failures. Large chapter
    packets are capped so a bad API window cannot multiply the same 100k-token
    request four times before a smaller recovery strategy gets control.
    """
    prefix = 'OPENROUTER' if provider == 'openrouter' else 'GEMINI'
    configured = max(1, env_int(f'{prefix}_MAX_ATTEMPTS', 4))
    compact = max(1, env_int('LLM_COMPACT_RETRY_MAX_INPUT_TOKENS', 15_000))
    large = max(compact + 1, env_int('LLM_LARGE_RETRY_INPUT_TOKENS', 50_000))
    if estimated_input_tokens <= compact:
        adaptive = 4
    elif estimated_input_tokens <= large:
        adaptive = 3
    else:
        adaptive = 2
    return max(1, min(configured, adaptive))

def _content_text(content: Any) -> str:
    if isinstance(content,str): return content
    if isinstance(content,list):
        return '\n'.join(x if isinstance(x,str) else str(x.get('text','')) if isinstance(x,dict) else '' for x in content).strip()
    return ''


def _retry_after_ms(response: httpx.Response) -> int:
    raw = response.headers.get('retry-after', '').strip()
    if raw:
        try:
            return max(0, int(float(raw) * 1000))
        except ValueError:
            try:
                moment = parsedate_to_datetime(raw)
                now = __import__('datetime').datetime.now(moment.tzinfo)
                return max(0, int((moment - now).total_seconds() * 1000))
            except Exception:
                pass
    # OpenRouter can return an absolute reset timestamp, usually in seconds.
    reset = response.headers.get('x-ratelimit-reset', '').strip()
    if reset:
        try:
            value = float(reset)
            now_seconds = time.time()
            # Support either seconds or milliseconds since epoch.
            if value > 10_000_000_000:
                value /= 1000
            return max(0, int((value - now_seconds) * 1000))
        except ValueError:
            pass
    return 0


async def ask_structured_json(*,provider:str,model:str,system_prompt:str,user_message:str,operation:str,packets:int=1,candidates:int=0,max_completion_tokens:int|None=None) -> dict:
    usage=empty_usage(); usage['packets']=packets; usage['candidates']=candidates
    estimated=estimate_tokens(system_prompt+'\n'+user_message)
    max_attempts=adaptive_attempt_limit(provider, estimated)
    last_error: BaseException | None=None
    for attempt in range(1,max_attempts+1):
        reservation=await reserve_model_capacity(provider,model,estimated)
        usage['rateLimitWaitMs']+=reservation.wait_ms; usage['requests']+=1; usage['estimatedInputTokens']+=estimated
        started=time.monotonic()
        try:
            if provider=='openrouter': raw,trace=await _openrouter(model,system_prompt,user_message,operation,False,usage,max_completion_tokens)
            else: raw,trace=await _gemini(model,system_prompt,user_message,operation)
            usage['traces'].append(trace)
            try:
                value = parse_json(raw)
            except BaseException as parse_exc:
                # Preserve the raw provider payload for operation-specific salvage.
                # A long structured response can be truncated near the end while
                # still containing many complete result objects that are safe to
                # reuse instead of retransmitting the whole document.
                try:
                    setattr(parse_exc, 'raw_response', raw)
                except Exception:
                    pass
                raise
            return {'value':value,'raw':raw,'usage':usage}
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            last_error=exc
            retry=_retryable(exc) and attempt<max_attempts and not is_fatal_provider_error(exc)
            backoff=max(getattr(exc,'retry_after_ms',0),int(min(30_000,1800*(2**(attempt-1))+random.randint(0,699)))) if retry else 0
            usage['diagnostics'].append({'at':now_iso(),'operation':operation,'attempt':attempt,'httpStatus':getattr(exc,'status',None) or None,'providerCode':getattr(exc,'provider_code','') or None,'message':str(exc),'retryable':retry,'retryAfterMs':getattr(exc,'retry_after_ms',0),'backoffMs':backoff,'provider':provider,'model':model,'providerName':getattr(exc,'provider_name','') or None,'requestId':getattr(exc,'request_id','') or None,'quotaMetric':getattr(exc,'quota_metric','') or None,'quotaDescription':getattr(exc,'quota_description','') or None,'networkCode':getattr(exc,'network_code','') or None,'estimatedInputTokensPerAttempt':estimated,'adaptiveMaxAttempts':max_attempts})
            if not retry: break
            usage['retries']+=1; penalize_model_capacity(provider,model,backoff); await asyncio.sleep(backoff/1000)
        finally:
            usage['requestDurationMs']+=int((time.monotonic()-started)*1000); reservation.release()
    if last_error is None: last_error=RuntimeError('LLM не вернула ответ.')
    setattr(last_error,'llm_usage',usage)
    raise last_error


async def _openrouter(model:str,system:str,user:str,operation:str,compat:bool,usage:dict,max_completion_tokens:int|None=None) -> tuple[str,dict]:
    key=os.getenv('OPENROUTER_API_KEY','').strip()
    if not key: raise ModelHttpError('OPENROUTER_API_KEY не задан.',401,provider_code='missing_api_key',provider_name='OpenRouter')
    base=os.getenv('OPENROUTER_API_BASE_URL','https://openrouter.ai').rstrip('/')
    headers={'Authorization':f'Bearer {key}','Content-Type':'application/json','X-OpenRouter-Metadata':'enabled'}
    if os.getenv('OPENROUTER_HTTP_REFERER','').strip(): headers['HTTP-Referer']=os.getenv('OPENROUTER_HTTP_REFERER','').strip()
    headers['X-OpenRouter-Title']=os.getenv('OPENROUTER_APP_TITLE','OSA.Edu').strip() or 'OSA.Edu'
    def body(compat_mode:bool):
        pref={'allow_fallbacks':True,'require_parameters':False if compat_mode else env_bool('OPENROUTER_REQUIRE_PARAMETERS',False),'data_collection':'deny' if os.getenv('OPENROUTER_DATA_COLLECTION','allow').strip().lower()=='deny' else 'allow'}
        if env_bool('OPENROUTER_ZDR',False): pref['zdr']=True
        result={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user}],'temperature':0.05,'max_completion_tokens':max_completion_tokens or env_int('OPENROUTER_MAX_COMPLETION_TOKENS',16000 if operation=='structure' else 8000),'provider':pref,'metadata':{'operation':operation,'app':'OSA.Edu'}}
        if not compat_mode: result['response_format']={'type':'json_object'}
        return result
    timeout=max(5,env_int('LLM_REQUEST_TIMEOUT_MS',240000)/1000)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response=await client.post(base+'/api/v1/chat/completions',headers=headers,json=body(compat))
        try: payload=response.json()
        except Exception: payload={}
        msg=str((payload.get('error') or {}).get('message') or payload.get('message') or '') if isinstance(payload,dict) else ''
        if response.status_code==404 and re.search(r'no endpoints found.*handle the requested parameters',msg,re.I) and not compat:
            usage['requests']+=1
            usage['estimatedInputTokens'] += estimate_tokens(system + '\n' + user)
            response=await client.post(base+'/api/v1/chat/completions',headers=headers,json=body(True))
            try: payload=response.json()
            except Exception: payload={}
            compat=True
        if response.status_code>=400:
            error=(payload.get('error') or {}) if isinstance(payload,dict) else {}
            message=str(error.get('message') or (payload.get('message') if isinstance(payload,dict) else '') or f'OpenRouter HTTP {response.status_code}')
            if response.status_code == 404 and re.search(r'no endpoints found.*handle the requested parameters', message, re.I):
                message = 'OpenRouter не нашёл совместимый endpoint для выбранной модели и параметров. Выберите другую модель или ослабьте ограничения провайдера.'
            metadata=error.get('metadata') or (payload.get('metadata') if isinstance(payload,dict) else {}) or {}
            raise ModelHttpError(
                message, response.status_code, _retry_after_ms(response),
                str(metadata.get('error_type') or error.get('type') or error.get('code') or ''),
                str(metadata.get('provider_name') or (payload.get('provider') if isinstance(payload,dict) else '') or 'OpenRouter'),
                response.headers.get('x-request-id') or response.headers.get('cf-ray') or '',
                str(metadata.get('quota_metric') or ''), str(metadata.get('quota_description') or ''),
                str(metadata.get('network_code') or ''),
            )
        choice=((payload.get('choices') or [{}])[0]) if isinstance(payload,dict) else {}
        if choice.get('error') or choice.get('finish_reason')=='error':
            raw=choice.get('error') or {}; raise ModelHttpError(str(raw.get('message') or 'OpenRouter provider error'),int(raw.get('code') or 502),0,str(raw.get('type') or raw.get('code') or 'provider_error'),'OpenRouter',response.headers.get('x-request-id',''))
        content=_content_text((choice.get('message') or {}).get('content'))
        if not content: raise ModelHttpError('OpenRouter вернул пустой ответ.',502,provider_name='OpenRouter')
        trace={'at':now_iso(),'operation':operation,'provider':'openrouter','model':model,'providerName':str(payload.get('provider') or (payload.get('metadata') or {}).get('provider_name') or '') or None,'requestId':response.headers.get('x-request-id') or response.headers.get('cf-ray') or None,'compatibilityMode':compat,'httpStatus':response.status_code}
        return content,trace


async def _gemini(model:str,system:str,user:str,operation:str) -> tuple[str,dict]:
    key=(os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY') or '').strip()
    if not key: raise ModelHttpError('GOOGLE_API_KEY не задан.',401,provider_code='missing_api_key',provider_name='Google')
    base=os.getenv('GEMINI_API_BASE_URL','https://generativelanguage.googleapis.com').rstrip('/')
    url=f'{base}/v1beta/models/{model}:generateContent?key={key}'
    body={'systemInstruction':{'parts':[{'text':system}]},'contents':[{'role':'user','parts':[{'text':user}]}],'generationConfig':{'temperature':0.05,'responseMimeType':'application/json'}}
    timeout=max(5,env_int('LLM_REQUEST_TIMEOUT_MS',240000)/1000)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response=await client.post(url,json=body)
        try: payload=response.json()
        except Exception: payload={}
        if response.status_code>=400:
            error=payload.get('error') or {}; raise ModelHttpError(str(error.get('message') or f'Gemini HTTP {response.status_code}'),response.status_code,_retry_after_ms(response),str(error.get('status') or ''),'Google',response.headers.get('x-request-id',''))
        parts=((((payload.get('candidates') or [{}])[0]).get('content') or {}).get('parts') or [])
        text='\n'.join(str(x.get('text','')) for x in parts if isinstance(x,dict)).strip()
        if not text: raise ModelHttpError('Gemini вернул пустой ответ.',502,provider_name='Google')
        return text,{'at':now_iso(),'operation':operation,'provider':'gemini','model':model,'providerName':'Google','requestId':response.headers.get('x-request-id') or None,'compatibilityMode':False,'httpStatus':response.status_code}
