from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')

DATA_DIR = ROOT / 'data'
UPLOADS_DIR = DATA_DIR / 'uploads'
EXTRACTED_DIR = DATA_DIR / 'extracted'
JOBS_FILE = DATA_DIR / 'jobs.json'
CONFIG_DIR = ROOT / 'config'
RULES_DIR = ROOT / 'rules-data'

for directory in (DATA_DIR, UPLOADS_DIR, EXTRACTED_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}

PORT = env_int('PORT', 8787)
WEB_ORIGIN = os.getenv('WEB_ORIGIN', 'http://127.0.0.1:5173').strip()
MAX_FILE_SIZE_MB = env_int('MAX_FILE_SIZE_MB', 35)
AUTO_DELETE_SOURCE = env_bool('AUTO_DELETE_SOURCE', True)
APP_VERSION = '3.5.0-py'
