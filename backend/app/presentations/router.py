from __future__ import annotations

import asyncio
import json
import shutil
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

import pymupdf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from ..util import now_iso
from .config import Settings
from .models import RunOptions
from .queue import PresentationQueue

router = APIRouter(prefix='/api/presentations', tags=['presentations'])


@lru_cache(maxsize=1)
def get_queue() -> PresentationQueue:
    return PresentationQueue(Settings.from_env())


def job_view(job, queue):
    path = queue.settings.root / job['id'] / 'attempts' / str(job['attempts'])
    return {key: job[key] for key in (
        'id', 'originalName', 'size', 'pageCount', 'status', 'createdAt', 'updatedAt',
        'startedAt', 'finishedAt', 'attempts', 'progress', 'progressMessage', 'error',
        'model', 'options')} | {
        'resultAvailable': bool(job.get('resultPath') and Path(job['resultPath']).is_file()),
        'reportAvailable': bool(job.get('reportPath') and Path(job['reportPath']).is_file()),
        'logAvailable': (path / 'slideguard.log').is_file(),
    }


async def require_job(job_id, queue):
    job = await asyncio.to_thread(queue.store.get, str(job_id))
    if not job:
        raise HTTPException(404, 'Проверка презентации не найдена.')
    return job


@router.get('/status')
async def status(queue: PresentationQueue = Depends(get_queue)):
    runtime = await queue.runtime()
    settings = queue.settings
    return {**runtime, 'ok': bool(runtime.get('installed') and settings.api_key and settings.base_url),
            'llmConfigured': bool(settings.api_key and settings.base_url), 'model': settings.model,
            'concurrentJobs': settings.concurrent_jobs, 'maxConcurrency': settings.concurrency,
            'maxFileSizeMb': settings.max_bytes / (1024 * 1024), 'maxPages': settings.max_pages,
            'maxBatch': settings.max_batch, 'timeoutSeconds': settings.timeout,
            'criteria': runtime.get('criteria', [])}


@router.get('/jobs')
async def jobs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               queue: PresentationQueue = Depends(get_queue)):
    return [job_view(job, queue) for job in await asyncio.to_thread(queue.store.list, limit, offset)]


def validate_pdf(path: Path, maximum_pages: int):
    try:
        with pymupdf.open(path) as document:
            if not document.is_pdf or document.needs_pass:
                raise ValueError('Нужен PDF без пароля.')
            if not 1 <= len(document) <= maximum_pages:
                raise ValueError(f'PDF должен содержать от 1 до {maximum_pages} страниц.')
            return len(document)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Не удалось открыть PDF. Проверьте файл.') from exc


@router.post('/jobs', status_code=201)
async def create_jobs(files: list[UploadFile] = File(...),
                      presentationType: str = Form('scientific'), language: str = Form('ru'),
                      criteria: str = Form('[]'), queue: PresentationQueue = Depends(get_queue)):
    directories = []
    persisted = False
    try:
        if not 1 <= len(files) <= queue.settings.max_batch:
            raise HTTPException(422, f'За один запрос разрешено от 1 до {queue.settings.max_batch} PDF.')
        try:
            options = RunOptions(presentationType=presentationType, language=language, criteria=json.loads(criteria))
        except (ValueError, TypeError, ValidationError) as exc:
            raise HTTPException(422, 'Некорректные параметры: тип презентации, язык или JSON-массив criteria.') from exc
        ready = await status(queue)
        if not ready['ok']:
            raise HTTPException(503, ready.get('error') or 'Не настроен API-ключ или endpoint SlideGuard.')
        allowed = {c['id'] for c in ready['criteria'] if options.presentationType in c['presentationTypes']}
        if not set(options.criteria) <= allowed:
            raise HTTPException(422, 'Критерий не существует или не подходит для выбранного типа презентации.')
        created = []
        for upload in files:
            name = Path((upload.filename or '').replace('\\', '/')).name
            if Path(name).suffix.lower() != '.pdf':
                raise HTTPException(422, 'Поддерживаются только PDF.')
            identifier = uuid4().hex
            folder = queue.settings.root / identifier
            folder.mkdir(parents=True)
            directories.append(folder)
            target = folder / 'input.pdf'
            size = 0
            with target.open('wb') as stream:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > queue.settings.max_bytes:
                        raise HTTPException(413, 'Презентация превышает лимит размера.')
                    stream.write(chunk)
            if not size:
                raise HTTPException(422, 'Загружен пустой PDF.')
            try:
                pages = await asyncio.to_thread(validate_pdf, target, queue.settings.max_pages)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            now = now_iso()
            created.append({'id': identifier, 'originalName': name, 'size': size, 'pageCount': pages,
                'filePath': str(target.resolve()), 'status': 'queued', 'options': options.model_dump(),
                'model': queue.settings.model, 'baseUrl': queue.settings.base_url,
                'createdAt': now, 'updatedAt': now, 'startedAt': None, 'finishedAt': None,
                'attempts': 1, 'progress': 0, 'progressMessage': 'Презентация добавлена в очередь.',
                'resultPath': None, 'reportPath': None, 'error': None})
        await asyncio.to_thread(queue.store.create_many, created)
        persisted = True
        queue.start()
        return [job_view(job, queue) for job in created]
    finally:
        for upload in files:
            await upload.close()
        if not persisted:
            for folder in directories:
                await asyncio.to_thread(shutil.rmtree, folder, True)


@router.get('/jobs/{job_id}')
async def get_job(job_id: UUID, queue: PresentationQueue = Depends(get_queue)):
    return job_view(await require_job(job_id.hex, queue), queue)


@router.post('/jobs/{job_id}/retry')
async def retry(job_id: UUID, queue: PresentationQueue = Depends(get_queue)):
    try:
        updated = await asyncio.to_thread(queue.store.retry, job_id.hex)
    except KeyError as exc:
        raise HTTPException(404, 'Проверка не найдена.') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    queue.start()
    return job_view(updated, queue)


def artifact_path(job, queue, key):
    value = job.get(key)
    path = Path(value).resolve() if value else None
    if path is None or not path.is_relative_to(queue.settings.root.resolve()) or not path.is_file():
        raise HTTPException(409, 'Результат ещё не сформирован или файл отсутствует.')
    return path


@router.get('/jobs/{job_id}/result')
async def result(job_id: UUID, queue: PresentationQueue = Depends(get_queue)):
    job = await require_job(job_id.hex, queue)
    path = artifact_path(job, queue, 'resultPath')
    return await asyncio.to_thread(lambda: json.loads(path.read_text(encoding='utf-8')))


@router.get('/jobs/{job_id}/report.json')
async def download_json(job_id: UUID, queue: PresentationQueue = Depends(get_queue)):
    job = await require_job(job_id.hex, queue)
    return FileResponse(artifact_path(job, queue, 'resultPath'), media_type='application/json',
                        filename=f'{Path(job["originalName"]).stem}-slideguard.json')


@router.get('/jobs/{job_id}/report.pdf')
async def download_pdf(job_id: UUID, queue: PresentationQueue = Depends(get_queue)):
    job = await require_job(job_id.hex, queue)
    return FileResponse(artifact_path(job, queue, 'reportPath'), media_type='application/pdf',
                        filename=f'{Path(job["originalName"]).stem}-slideguard.pdf')


@router.get('/jobs/{job_id}/log.txt')
async def download_log(job_id: UUID, queue: PresentationQueue = Depends(get_queue)):
    job = await require_job(job_id.hex, queue)
    log = queue.settings.root / job['id'] / 'attempts' / str(job['attempts']) / 'slideguard.log'
    if not log.is_file():
        raise HTTPException(409, 'Лог ещё не создан.')
    return FileResponse(log, media_type='text/plain; charset=utf-8', filename=f'slideguard-{job_id.hex}.log')
