from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA_DIR, MAX_FILE_SIZE_MB, ROOT, env_int

SLIDEGUARD_REVISION = '7f62d6a2a02918ca4e1f7562041a5d03bad5743a'


@dataclass(frozen=True)
class Settings:
    root: Path
    python: str
    api_key: str = field(repr=False)
    base_url: str = 'https://openrouter.ai/api/v1'
    model: str = 'google/gemini-2.5-flash'
    concurrent_jobs: int = 1
    concurrency: int = 2
    timeout: int = 3600
    max_bytes: int = 35 * 1024 * 1024
    max_pages: int = 100
    max_batch: int = 25
    llm_retries: int = 3

    @classmethod
    def from_env(cls) -> Settings:
        explicit_key = os.getenv('SLIDEGUARD_LLM_API_KEY', '').strip()
        router_key = os.getenv('OPENROUTER_API_KEY', '').strip()
        native_key = os.getenv('OPENAI_API_KEY', '').strip()
        base = os.getenv('SLIDEGUARD_LLM_API_BASE', '').strip()
        if not base:
            base = (os.getenv('OPENROUTER_API_BASE_URL', 'https://openrouter.ai').strip()
                    if router_key or not native_key else
                    os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').strip())
            if base.rstrip('/') == 'https://openrouter.ai':
                base = base.rstrip('/') + '/api/v1'
        default_python = ROOT / '.venv-slideguard' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        return cls(
            root=DATA_DIR / 'presentations',
            python=os.getenv('SLIDEGUARD_PYTHON', '').strip() or str(default_python),
            api_key=explicit_key or router_key or native_key,
            base_url=base.rstrip('/'),
            model=os.getenv('SLIDEGUARD_LLM_MODEL', '').strip() or 'google/gemini-2.5-flash',
            concurrent_jobs=min(8, env_int('SLIDEGUARD_CONCURRENT_JOBS', 1)),
            concurrency=min(16, env_int('SLIDEGUARD_MAX_CONCURRENCY', 2)),
            timeout=env_int('SLIDEGUARD_TIMEOUT_SECONDS', 3600),
            max_bytes=env_int('SLIDEGUARD_MAX_FILE_SIZE_MB', MAX_FILE_SIZE_MB) * 1024 * 1024,
            max_pages=env_int('SLIDEGUARD_MAX_PAGES', 100),
            max_batch=min(200, env_int('SLIDEGUARD_MAX_BATCH', 25)),
            llm_retries=min(5, env_int('SLIDEGUARD_LLM_MAX_RETRIES', 3)),
        )


def worker_environment(settings: Settings, job: dict, attempt_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Upstream gives ANTHROPIC_API_KEY and OPENAI_API_KEY priority over its custom
    # endpoint. Only the explicitly selected configuration may reach this worker.
    for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'OPENAI_MODEL', 'ANTHROPIC_MODEL'):
        env.pop(key, None)
    env.update(
        SLIDEGUARD_LLM_API_KEY=settings.api_key,
        SLIDEGUARD_LLM_API_BASE=job['baseUrl'],
        SLIDEGUARD_LLM_MODEL=job['model'],
        SLIDEGUARD_CACHE_DIR=str(attempt_dir / 'cache'),
        SLIDEGUARD_FILE_CACHE_DIR=str(attempt_dir / 'cache' / 'files'),
        SLIDEGUARD_EVALUATIONS_DIR=str(attempt_dir / 'cache' / 'evaluations'),
        LANGFUSE_TRACING_ENABLED='false',
        LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false', GRADIO_ANALYTICS_ENABLED='false',
        PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
        OSA_SLIDEGUARD_PARENT_PID=str(os.getpid()),
    )
    # Do not let the SlideGuard UI package's import-time auth engine touch a user's DB.
    env['AUTH_DB_URL'] = 'sqlite:///:memory:'
    return env
