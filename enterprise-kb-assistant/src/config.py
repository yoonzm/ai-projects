from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SAMPLES_DIR = DATA_DIR / "samples"
UPLOADS_DIR = DATA_DIR / "uploads"
CHROMA_DIR = DATA_DIR / "chroma"
DB_PATH = DATA_DIR / "app.db"
MASTER_KEY_PATH = DATA_DIR / "master.key"


def init_paths() -> None:
    for path in (DATA_DIR, SAMPLES_DIR, UPLOADS_DIR, CHROMA_DIR):
        path.mkdir(parents=True, exist_ok=True)


load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env.local")
init_paths()


@dataclass(frozen=True)
class RuntimeDefaults:
    chat_provider: str = os.getenv("CHAT_PROVIDER", "")
    model_name: str = os.getenv("MODEL_NAME", "")
    model_base_url: str = os.getenv("MODEL_BASE_URL", "")
    model_api_key: str = os.getenv("MODEL_API_KEY", "")
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "")
    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "900"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    top_k: int = int(os.getenv("TOP_K", "5"))
    similarity_threshold: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.12"))
    temperature: float = float(os.getenv("TEMPERATURE", "0.2"))
    timeout_seconds: int = int(os.getenv("TIMEOUT_SECONDS", "30"))
    max_tokens: int = int(os.getenv("MAX_TOKENS", "1200"))


DEFAULTS = RuntimeDefaults()


def admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "admin")
