from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./.runtime/codeagent.db")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash[1m]")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
TEST_EXECUTION_MODE = os.environ.get("TEST_EXECUTION_MODE", "local").strip().lower()
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "codeagent-app:local")
SANDBOX_WORKSPACE_VOLUME = os.environ.get("SANDBOX_WORKSPACE_VOLUME", "codeagent_jobs")
SANDBOX_SERVICE_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://sandbox:8001")
LOCAL_REPOS_ROOT = Path(os.environ.get("LOCAL_REPOS_ROOT", ROOT / "local_repos"))
REVIEWS_ROOT = Path(os.environ.get("REVIEWS_ROOT", ROOT / "reviews"))
