from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
EXTRACTED_DIR = DATA_DIR / "extracted"
JOBS_FILE = DATA_DIR / "jobs.json"
NORMCONTROL_DIR = DATA_DIR / "normcontrol"
NORMCONTROL_UPLOADS_DIR = NORMCONTROL_DIR / "uploads"
NORMCONTROL_REPORTS_DIR = NORMCONTROL_DIR / "reports"
NORMCONTROL_JOBS_FILE = NORMCONTROL_DIR / "jobs.json"
REPRODUCIBILITY_DIR = DATA_DIR / "reproducibility"
REPRODUCIBILITY_UPLOADS_DIR = REPRODUCIBILITY_DIR / "uploads"
REPRODUCIBILITY_RUNS_DIR = REPRODUCIBILITY_DIR / "runs"
REPRODUCIBILITY_JOBS_FILE = REPRODUCIBILITY_DIR / "jobs.json"
CONFIG_DIR = ROOT / "config"
RULES_DIR = ROOT / "rules-data"

for directory in (
    DATA_DIR,
    UPLOADS_DIR,
    EXTRACTED_DIR,
    NORMCONTROL_UPLOADS_DIR,
    NORMCONTROL_REPORTS_DIR,
    REPRODUCIBILITY_UPLOADS_DIR,
    REPRODUCIBILITY_RUNS_DIR,
):
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
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


PORT = env_int("PORT", 8787)
WEB_ORIGIN = os.getenv("WEB_ORIGIN", "http://127.0.0.1:5173").strip()
WEB_ORIGINS = env_list("WEB_ORIGINS") or ([WEB_ORIGIN] if WEB_ORIGIN else [])
MAX_FILE_SIZE_MB = env_int("MAX_FILE_SIZE_MB", 35)
AUTO_DELETE_SOURCE = env_bool("AUTO_DELETE_SOURCE", True)
NORMCONTROL_MCP_URL = os.getenv(
    "NORMCONTROL_MCP_URL", "http://mcp.10.32.11.60.nip.io/mcp"
).strip()
NORMCONTROL_DAG_ID = os.getenv("NORMCONTROL_DAG_ID", "flow-dqc-control-10").strip()
NORMCONTROL_MCP_ATTEMPTS = env_int("NORMCONTROL_MCP_ATTEMPTS", 3)
NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS = env_int(
    "NORMCONTROL_MCP_ATTEMPT_TIMEOUT_SECONDS", 300
)
NORMCONTROL_HTTP_TIMEOUT_SECONDS = env_int("NORMCONTROL_HTTP_TIMEOUT_SECONDS", 120)
APP_VERSION = "5.0.0"
