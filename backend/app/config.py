"""Central configuration. Everything is overridable through environment variables (.env is loaded)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = BACKEND_DIR / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
DB_PATH = Path(os.getenv("EKT_DB_PATH", DATA_DIR / "catalog.sqlite"))
UPLOAD_DIR = Path(os.getenv("EKT_UPLOAD_DIR", DATA_DIR / "uploads"))
ATTACHMENT_DB_PATH = Path(os.getenv("EKT_ATTACHMENT_DB_PATH", DATA_DIR / "attachments.sqlite3"))

EKT_SITE = "https://ekt.kz"
EKT_API_BASE = os.getenv("EKT_API_BASE", "https://ekt.kz/api")
EKT_API_USER = os.getenv("EKT_API_USER", "apiuser")
EKT_API_PASS = os.getenv("EKT_API_PASS", "ApiEkt!2026")
EKT_CART_URL = "https://ekt.kz/personal/cart/"

LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5")
LLM_EFFORT = os.getenv("LLM_EFFORT", "low")  # low | medium | high — chat must answer in seconds
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

DETAIL_CACHE_TTL = int(os.getenv("DETAIL_CACHE_TTL", "300"))  # seconds; stock changes, keep it short
MAX_UPLOAD_MB = 15
