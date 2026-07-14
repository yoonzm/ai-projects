from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ModelConfig:
    id: str
    name: str
    enabled: bool
    chat_provider: str
    chat_model_name: str
    chat_base_url: str
    chat_api_key_ref: str | None
    embedding_provider: str
    embedding_model_name: str
    embedding_base_url: str
    embedding_api_key_ref: str | None
    temperature: float
    top_k: int
    similarity_threshold: float
    timeout_seconds: int
    max_tokens: int
    created_by: str | None = None
    updated_at: str | None = None


@dataclass
class SourceRef:
    source_id: str
    file_name: str
    chunk_id: str
    quote: str
    title: str | None = None
    page: int | None = None
    score: float | None = None


@dataclass
class QAResult:
    answer: str
    sources: list[SourceRef]
    uncertainty: str
    rewritten_question: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0


@dataclass
class ImportResult:
    success_files: int
    failed_files: list[dict[str, str]]
    chunk_count: int
    indexed_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

