from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..util import now_iso

ACTIVE = {'queued', 'running'}
RETRYABLE = {'failed', 'partial', 'cancelled'}


class JobStore:
    """Small persistent queue; SQLite transactions also protect batch/retry admission."""

    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / 'jobs.sqlite3', timeout=10)
        try:
            connection.execute('''CREATE TABLE IF NOT EXISTS presentation_jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                payload TEXT NOT NULL)''')
            connection.execute('CREATE INDEX IF NOT EXISTS presentations_status ON presentation_jobs(status, created_at)')
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _get(connection, job_id):
        row = connection.execute('SELECT payload FROM presentation_jobs WHERE id=?', (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _save(connection, job):
        connection.execute('UPDATE presentation_jobs SET status=?, payload=? WHERE id=?',
                           (job['status'], json.dumps(job, ensure_ascii=False), job['id']))

    def create_many(self, jobs):
        with self.connect() as connection:
            connection.executemany('INSERT INTO presentation_jobs VALUES (?,?,?,?)',
                [(j['id'], j['status'], j['createdAt'], json.dumps(j, ensure_ascii=False)) for j in jobs])

    def get(self, job_id):
        with self.connect() as connection:
            return self._get(connection, job_id)

    def list(self, limit=50, offset=0):
        with self.connect() as connection:
            rows = connection.execute('SELECT payload FROM presentation_jobs ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?',
                                      (limit, offset)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, job_id, patch):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            job = self._get(connection, job_id)
            if job is None:
                raise KeyError(job_id)
            job = {**job, **patch, 'updatedAt': now_iso()}
            self._save(connection, job)
            return job

    def claim(self, maximum):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute("SELECT count(*) FROM presentation_jobs WHERE status='running'").fetchone()[0] >= maximum:
                return None
            row = connection.execute("SELECT id FROM presentation_jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if not row:
                return None
            job = self._get(connection, row[0])
            job.update(status='running', startedAt=now_iso(), updatedAt=now_iso(),
                       progress=5, progressMessage='Запускаем SlideGuard.', error=None)
            self._save(connection, job)
            return job

    def retry(self, job_id):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            job = self._get(connection, job_id)
            if job is None:
                raise KeyError(job_id)
            if job['status'] not in RETRYABLE:
                raise ValueError('Повтор разрешён только для неуспешной, частичной или отменённой проверки.')
            if not Path(job['filePath']).is_file():
                raise ValueError('Исходный PDF отсутствует. Загрузите презентацию заново.')
            job.update(status='queued', progress=0, progressMessage='Повтор добавлен в очередь.',
                       updatedAt=now_iso(), startedAt=None, finishedAt=None, error=None,
                       attempts=job['attempts'] + 1, resultPath=None, reportPath=None)
            self._save(connection, job)
            return job

    def recover(self):
        # One OSA.Edu ASGI process owns execution, as for existing module queues.
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows = connection.execute("SELECT payload FROM presentation_jobs WHERE status='running'").fetchall()
            for row in rows:
                job = json.loads(row[0])
                job.update(status='failed', error='Backend перезапущен во время проверки. Доступен ручной повтор.',
                           finishedAt=now_iso(), updatedAt=now_iso(), progressMessage='Выполнение прервано.')
                self._save(connection, job)
