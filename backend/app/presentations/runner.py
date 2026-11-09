from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

from ..config import ROOT
from .config import Settings, worker_environment
from .worker import EVENT_PREFIX

logger = logging.getLogger(__name__)


async def terminate(process):
    if process.returncode is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        await process.wait()


async def inspect_runtime(settings: Settings):
    process = None
    try:
        env = os.environ.copy()
        env.update(AUTH_DB_URL='sqlite:///:memory:', LANGFUSE_TRACING_ENABLED='false',
                   LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false', GRADIO_ANALYTICS_ENABLED='false')
        process = await asyncio.create_subprocess_exec(
            settings.python, '-m', 'backend.app.presentations.worker', '--inspect',
            cwd=ROOT, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == 'posix')
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        if process.returncode:
            logger.warning('SlideGuard inspection failed: %s', stderr.decode(errors='replace')[-2000:])
            return {'installed': False, 'error': 'Не удалось импортировать SlideGuard. Проверьте отдельное Python-окружение и requirements-slideguard.txt.'}
        data = json.loads(stdout.decode().strip().splitlines()[-1])
        return {'installed': True, **data}
    except (OSError, ValueError, asyncio.TimeoutError):
        return {'installed': False, 'error': 'SlideGuard не готов. Установите requirements-slideguard.txt в Python 3.11/3.12 и настройте SLIDEGUARD_PYTHON.'}
    finally:
        if process is not None:
            await terminate(process)


async def run_slideguard(job: dict, settings: Settings, on_progress):
    attempt = settings.root / job['id'] / 'attempts' / str(job['attempts'])
    attempt.mkdir(parents=True, exist_ok=True)
    config = {'options': job['options'], 'originalName': job['originalName'],
              'concurrency': settings.concurrency, 'llmRetries': settings.llm_retries}
    options_path = attempt / 'options.json'
    options_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    log_path = attempt / 'slideguard.log'
    env = worker_environment(settings, job, attempt)
    process = await asyncio.create_subprocess_exec(
        settings.python, '-m', 'backend.app.presentations.worker', '--input', job['filePath'],
        '--output', str(attempt), '--options', str(options_path), cwd=ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == 'posix', limit=4 * 1024 * 1024)
    written = 0
    with log_path.open('w', encoding='utf-8') as stream:
        async def drain(pipe):
            nonlocal written
            while line := await pipe.readline():
                text = line.decode('utf-8', errors='replace')
                if settings.api_key:
                    text = text.replace(settings.api_key, '[REDACTED]')
                if written < 5 * 1024 * 1024:
                    remaining = min(65536, 5 * 1024 * 1024 - written)
                    clipped = text.encode('utf-8')[:remaining].decode('utf-8', errors='ignore')
                    stream.write(clipped)
                    stream.flush()
                    written += len(clipped.encode('utf-8'))
                if text.startswith(EVENT_PREFIX):
                    try:
                        event = json.loads(text[len(EVENT_PREFIX):])
                        progress = int(event['progress'])
                        message = str(event['message'])[:500]
                    except (ValueError, KeyError, TypeError):
                        continue
                    await on_progress(min(99, max(5, progress)), message)

        readers = [asyncio.create_task(drain(process.stdout)), asyncio.create_task(drain(process.stderr))]
        try:
            await asyncio.wait_for(asyncio.gather(process.wait(), *readers), timeout=settings.timeout)
            if process.returncode:
                raise RuntimeError(f'SlideGuard завершился с кодом {process.returncode}. См. технический лог.')
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f'SlideGuard превысил лимит {settings.timeout} с.') from exc
        finally:
            await terminate(process)
            for task in readers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
    result_path = attempt / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if (result.get('schemaVersion') != 1 or result.get('outcome') not in {'completed', 'partial', 'failed'}
            or not isinstance(result.get('evaluation'), dict)
            or not isinstance(result['evaluation'].get('slide_evaluations'), list)):
        raise ValueError('SlideGuard вернул результат с неподдерживаемой структурой.')
    report_path = attempt / 'report.pdf'
    return result, result_path, report_path if report_path.is_file() else None
