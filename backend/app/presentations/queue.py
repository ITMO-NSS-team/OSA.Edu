from __future__ import annotations

import asyncio
import logging
import time

from ..util import now_iso
from .config import Settings
from .runner import inspect_runtime, run_slideguard
from .store import JobStore

logger = logging.getLogger(__name__)


class PresentationQueue:
    def __init__(self, settings: Settings, *, runner=run_slideguard):
        self.settings = settings
        self.store = JobStore(settings.root)
        self.runner = runner
        self.task = None
        self.active: set[asyncio.Task] = set()
        self._runtime = None
        self._checked_at = 0.0
        self._inspect_lock = asyncio.Lock()

    async def runtime(self):
        async with self._inspect_lock:
            if self._runtime is None or time.monotonic() - self._checked_at > 60:
                self._runtime = await inspect_runtime(self.settings)
                self._checked_at = time.monotonic()
            return self._runtime

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(), name='osa-presentations-queue')

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self):
        try:
            while True:
                self.active = {task for task in self.active if not task.done()}
                while len(self.active) < self.settings.concurrent_jobs:
                    job = await asyncio.to_thread(self.store.claim, self.settings.concurrent_jobs)
                    if job is None:
                        break
                    task = asyncio.create_task(self._perform(job), name=f'slideguard-{job["id"]}')
                    self.active.add(task)
                await asyncio.sleep(0.5)
        finally:
            for task in self.active:
                task.cancel()
            await asyncio.gather(*self.active, return_exceptions=True)
            self.active.clear()

    async def _perform(self, job):
        logger.info('SlideGuard job %s attempt %s started', job['id'], job['attempts'])
        async def progress(value, message):
            await asyncio.to_thread(self.store.update, job['id'], {'progress': value, 'progressMessage': message})
        try:
            result, path, report = await self.runner(job, self.settings, progress)
            outcome = result['outcome']
            await asyncio.to_thread(self.store.update, job['id'], {
                'status': outcome, 'progress': 100, 'resultPath': str(path),
                'reportPath': str(report) if report else None, 'finishedAt': now_iso(),
                'error': None if outcome == 'completed' else 'Часть проверок SlideGuard не выполнена. Результаты и диагностика сохранены.',
                'progressMessage': 'Проверка завершена.' if outcome == 'completed' else 'Получен неполный результат.',
            })
        except asyncio.CancelledError:
            await asyncio.to_thread(self.store.update, job['id'], {
                'status': 'failed', 'finishedAt': now_iso(), 'progressMessage': 'Выполнение прервано.',
                'error': 'Backend остановлен во время проверки. Доступен ручной повтор.'})
            raise
        except Exception as exc:
            logger.warning('SlideGuard job %s failed (%s)', job['id'], type(exc).__name__)
            # Do not publish provider exception bodies or credentials in API errors.
            message = (str(exc) if isinstance(exc, TimeoutError) else
                       f'Ошибка SlideGuard ({type(exc).__name__}). Откройте технический лог или повторите проверку.')
            await asyncio.to_thread(self.store.update, job['id'], {
                'status': 'failed', 'error': message, 'finishedAt': now_iso(),
                'progressMessage': 'Проверка завершилась с ошибкой.'})
