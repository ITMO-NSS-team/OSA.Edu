"""Independent language-review API and downloadable report."""
from __future__ import annotations

import os
import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from ..config import MAX_FILE_SIZE_MB, UPLOADS_DIR
from ..defaults import MODELS, model_definition
from ..llm.host_llm import host_provider_status
from ..util import now_iso
from .queue import cancel, start_queue
from .report import generate_pdf
from .store import create_job, delete_job, get_job, list_jobs, update_job

router = APIRouter(prefix='/api/language', tags=['language'])
UPLOAD_DIR = UPLOADS_DIR / 'language'


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={'error': message})


@router.get('/jobs')
async def jobs():
    try:
        return await list_jobs()
    except RuntimeError as exc:
        return _error(503, str(exc))


@router.get('/jobs/{job_id}')
async def job(job_id: str):
    return await get_job(job_id) or _error(404, 'Проверка не найдена.')


@router.post('/jobs')
async def submit(file: UploadFile = File(...), model: str = Form(''), style: bool = Form(False), includeDisputed: bool = Form(False)):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', file.filename or 'thesis.pdf')[:180]
    if not name.lower().endswith('.pdf'):
        await file.close()
        return _error(400, 'Поддерживаются только PDF.')
    requested = model or next((x['id'] for x in MODELS if x.get('tier') == 'production'), MODELS[0]['id'])
    selected = model_definition(requested)
    if not selected:
        await file.close()
        return _error(400, 'Неизвестная модель.')
    if selected['provider'] == 'openrouter' and not os.getenv('OPENROUTER_API_KEY', '').strip():
        await file.close()
        return _error(400, 'OPENROUTER_API_KEY не задан.')
    if selected['provider'] == 'host' and not host_provider_status(force=True).get('authenticated'):
        await file.close()
        return _error(400, 'Host LLM не настроен.')
    job_id = str(uuid.uuid4())
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f'{job_id}.pdf'
    size = 0
    digest = hashlib.sha256()
    registered = False
    try:
        with path.open('wb') as stream:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE_MB * 1024 * 1024:
                    return _error(413, f'PDF превышает лимит {MAX_FILE_SIZE_MB} МБ.')
                stream.write(chunk)
                digest.update(chunk)
        with path.open('rb') as saved:
            signature = saved.read(5)
        if size < 5 or signature != b'%PDF-':
            return _error(400, 'Файл не является PDF.')
        stamp = now_iso()
        created = await create_job({'id': job_id, 'originalName': name, 'filePath': str(path),
                                    'size': size, 'createdAt': stamp, 'updatedAt': stamp,
                                    'status': 'queued', 'model': selected['id'], 'styleEnabled': style,
                                    'includeDisputed': includeDisputed, 'fileDigest': digest.hexdigest(),
                                    'progress': 0, 'progressMessage': 'Добавлено в очередь.',
                                    'result': None, 'error': None, 'startedAt': None, 'finishedAt': None})
        registered = created['id'] == job_id
        start_queue()
        return JSONResponse(status_code=201 if registered else 200, content=created)
    except RuntimeError as exc:
        return _error(503, str(exc))
    except Exception:
        return _error(500, 'Не удалось сохранить файл.')
    finally:
        await file.close()
        # Failed uploads leave no bytes on disk. Registered files stay for retry.
        if not registered:
            path.unlink(missing_ok=True)


@router.post('/jobs/{job_id}/cancel')
async def cancel_endpoint(job_id: str):
    return await cancel(job_id) or _error(404, 'Проверка не найдена.')


@router.post('/jobs/{job_id}/retry')
async def retry(job_id: str):
    current = await get_job(job_id, internal=True)
    if not current:
        return _error(404, 'Проверка не найдена.')
    if current['status'] in {'queued', 'running', 'cancelling'}:
        return _error(409, 'Проверка уже выполняется.')
    if (current.get('result') or {}).get('complete'):
        return _error(409, 'Все доступные фрагменты уже проверены. Для новой проверки загрузите файл заново.')
    if current.get('resumeError') or (current.get('checkpoint') and current['checkpoint'].get('version') != 2):
        return _error(409, 'Контрольная точка повреждена или имеет несовместимую версию. История сохранена; загрузите PDF для новой проверки.')
    if current.get('result') and not current.get('checkpoint'):
        return _error(409, 'Этот старый запуск не содержит карты выполненных пакетов. История сохранена; для новой проверки загрузите PDF заново.')
    if not Path(current['filePath']).exists():
        return _error(409, 'Исходный PDF удалён.')
    updated = await update_job(job_id, {'status': 'queued', 'error': None,
                                        'progressMessage': 'Дозапуск незавершённых частей в очереди.',
                                        'finishedAt': None}, expected_statuses={'partial', 'failed', 'cancelled'})
    if not updated:
        return _error(409, 'Состояние проверки уже изменилось.')
    start_queue()
    return updated


@router.delete('/jobs/{job_id}')
async def remove(job_id: str):
    current = await get_job(job_id)
    if not current:
        return _error(404, 'Проверка не найдена.')
    if current['status'] in {'running', 'cancelling'}:
        return _error(409, 'Сначала остановите проверку.')
    removed = await delete_job(job_id)
    if not removed:
        return _error(409, 'Состояние проверки изменилось; удаление отменено.')
    Path(removed['filePath']).unlink(missing_ok=True)
    return Response(status_code=204)


@router.get('/jobs/{job_id}/report.pdf')
async def report_pdf(job_id: str):
    current = await get_job(job_id)
    if not current:
        return _error(404, 'Проверка не найдена.')
    if current.get('status') not in {'done', 'partial', 'cancelled', 'failed'} or not current.get('result'):
        return _error(409, 'Отчёт ещё не готов.')
    try:
        body = generate_pdf(current)
    except Exception as exc:
        return _error(500, f'Не удалось сформировать PDF: {exc}')
    return Response(content=body, media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="language-{job_id}.pdf"'})


@router.get('/jobs/{job_id}/report.json')
async def report_json(job_id: str):
    current = await get_job(job_id)
    if not current:
        return _error(404, 'Проверка не найдена.')
    if not current.get('result'):
        return _error(409, 'Отчёт ещё не готов.')
    return JSONResponse(current['result'], headers={
        'Content-Disposition': f'attachment; filename="language-{job_id}.json"'
    })
