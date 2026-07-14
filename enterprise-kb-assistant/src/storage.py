from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet

from .config import DB_PATH, DEFAULTS, MASTER_KEY_PATH
from .schemas import ModelConfig


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _fernet() -> Fernet:
    if MASTER_KEY_PATH.exists():
        key = MASTER_KEY_PATH.read_bytes()
    else:
        key = Fernet.generate_key()
        MASTER_KEY_PATH.write_bytes(key)
        MASTER_KEY_PATH.chmod(0o600)
    return Fernet(key)


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            create table if not exists model_config (
              id text primary key,
              name text not null,
              enabled integer not null default 0,
              chat_provider text not null,
              chat_model_name text not null,
              chat_base_url text,
              chat_api_key_ref text,
              embedding_provider text not null,
              embedding_model_name text not null,
              embedding_base_url text,
              embedding_api_key_ref text,
              temperature real not null,
              top_k integer not null,
              similarity_threshold real not null,
              timeout_seconds integer not null,
              max_tokens integer not null,
              created_by text,
              updated_at text not null
            );

            create table if not exists documents (
              id text primary key,
              knowledge_base_id text not null,
              file_name text not null,
              file_type text not null,
              file_hash text not null,
              status text not null,
              error_message text,
              chunk_count integer not null default 0,
              created_at text not null,
              indexed_at text
            );

            create table if not exists chat_logs (
              id text primary key,
              session_id text not null,
              knowledge_base_id text not null,
              user_input text,
              rewritten_query text,
              answer text,
              source_refs text,
              tool_calls text,
              latency_ms integer,
              feedback text,
              created_at text not null
            );

            create table if not exists admin_audit_logs (
              id text primary key,
              actor text,
              action text not null,
              detail text,
              created_at text not null
            );
            """
        )
        count = conn.execute("select count(*) from model_config").fetchone()[0]
        if count == 0:
            conn.execute(
                """
                insert into model_config (
                  id, name, enabled, chat_provider, chat_model_name, chat_base_url,
                  chat_api_key_ref, embedding_provider, embedding_model_name,
                  embedding_base_url, embedding_api_key_ref, temperature, top_k,
                  similarity_threshold, timeout_seconds, max_tokens, created_by, updated_at
                ) values (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cfg_default",
                    "default",
                    DEFAULTS.chat_provider,
                    DEFAULTS.model_name,
                    DEFAULTS.model_base_url,
                    encrypt_secret(DEFAULTS.model_api_key),
                    DEFAULTS.embedding_provider,
                    DEFAULTS.embedding_model_name,
                    DEFAULTS.embedding_base_url,
                    encrypt_secret(DEFAULTS.embedding_api_key),
                    DEFAULTS.temperature,
                    DEFAULTS.top_k,
                    DEFAULTS.similarity_threshold,
                    DEFAULTS.timeout_seconds,
                    DEFAULTS.max_tokens,
                    "system",
                    _now(),
                ),
            )


def get_enabled_model_config() -> ModelConfig:
    init_db()
    with connect() as conn:
        row = conn.execute("select * from model_config where enabled = 1 limit 1").fetchone()
        if row is None:
            raise RuntimeError("未找到已启用的模型配置。")
        return _row_to_model_config(row)


def save_model_config(
    *,
    name: str,
    chat_provider: str,
    chat_model_name: str,
    chat_base_url: str,
    chat_api_key: str,
    embedding_provider: str,
    embedding_model_name: str,
    embedding_base_url: str,
    embedding_api_key: str,
    temperature: float,
    top_k: int,
    similarity_threshold: float,
    timeout_seconds: int,
    max_tokens: int,
    actor: str = "admin",
) -> ModelConfig:
    init_db()
    config_id = _new_id("cfg")
    with connect() as conn:
        conn.execute("update model_config set enabled = 0")
        conn.execute(
            """
            insert into model_config (
              id, name, enabled, chat_provider, chat_model_name, chat_base_url,
              chat_api_key_ref, embedding_provider, embedding_model_name,
              embedding_base_url, embedding_api_key_ref, temperature, top_k,
              similarity_threshold, timeout_seconds, max_tokens, created_by, updated_at
            ) values (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config_id,
                name,
                chat_provider,
                chat_model_name,
                chat_base_url,
                encrypt_secret(chat_api_key),
                embedding_provider,
                embedding_model_name,
                embedding_base_url,
                encrypt_secret(embedding_api_key),
                temperature,
                top_k,
                similarity_threshold,
                timeout_seconds,
                max_tokens,
                actor,
                _now(),
            ),
        )
        audit(conn, actor, "model_config.enable", {"config_id": config_id, "name": name})
    return get_enabled_model_config()


def list_model_configs() -> list[ModelConfig]:
    init_db()
    with connect() as conn:
        rows = conn.execute("select * from model_config order by updated_at desc").fetchall()
        return [_row_to_model_config(row) for row in rows]


def get_model_secrets(config: ModelConfig) -> tuple[str, str]:
    return decrypt_secret(config.chat_api_key_ref), decrypt_secret(config.embedding_api_key_ref)


def upsert_document(
    knowledge_base_id: str,
    file_name: str,
    file_type: str,
    file_hash: str,
    status: str,
    chunk_count: int,
    error_message: str | None = None,
) -> str:
    init_db()
    doc_id = f"doc_{knowledge_base_id}_{file_hash[:16]}"
    with connect() as conn:
        conn.execute(
            """
            insert into documents (
              id, knowledge_base_id, file_name, file_type, file_hash, status,
              error_message, chunk_count, created_at, indexed_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
              file_name = excluded.file_name,
              file_type = excluded.file_type,
              status = excluded.status,
              error_message = excluded.error_message,
              chunk_count = excluded.chunk_count,
              indexed_at = excluded.indexed_at
            """,
            (
                doc_id,
                knowledge_base_id,
                file_name,
                file_type,
                file_hash,
                status,
                error_message,
                chunk_count,
                _now(),
                _now(),
            ),
        )
    return doc_id


def delete_documents_by_file(knowledge_base_id: str, file_name: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "delete from documents where knowledge_base_id = ? and file_name = ?",
            (knowledge_base_id, file_name),
        )


def list_documents(knowledge_base_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if knowledge_base_id:
            rows = conn.execute(
                "select * from documents where knowledge_base_id = ? order by indexed_at desc",
                (knowledge_base_id,),
            ).fetchall()
        else:
            rows = conn.execute("select * from documents order by indexed_at desc").fetchall()
        return [dict(row) for row in rows]


def log_chat(
    session_id: str,
    knowledge_base_id: str,
    user_input: str,
    rewritten_query: str,
    answer: str,
    source_refs: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    latency_ms: int,
) -> str:
    init_db()
    log_id = _new_id("log")
    with connect() as conn:
        conn.execute(
            """
            insert into chat_logs (
              id, session_id, knowledge_base_id, user_input, rewritten_query, answer,
              source_refs, tool_calls, latency_ms, feedback, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
            """,
            (
                log_id,
                session_id,
                knowledge_base_id,
                user_input,
                rewritten_query,
                answer,
                json.dumps(source_refs, ensure_ascii=False),
                json.dumps(tool_calls, ensure_ascii=False),
                latency_ms,
                _now(),
            ),
        )
    return log_id


def list_logs(limit: int = 20) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "select * from chat_logs order by created_at desc limit ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def set_feedback(log_id: str, feedback: str) -> None:
    with connect() as conn:
        conn.execute("update chat_logs set feedback = ? where id = ?", (feedback, log_id))


def audit(conn: sqlite3.Connection, actor: str, action: str, detail: dict[str, Any]) -> None:
    conn.execute(
        "insert into admin_audit_logs (id, actor, action, detail, created_at) values (?, ?, ?, ?, ?)",
        (_new_id("audit"), actor, action, json.dumps(detail, ensure_ascii=False), _now()),
    )


def _row_to_model_config(row: sqlite3.Row) -> ModelConfig:
    return ModelConfig(
        id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        chat_provider=row["chat_provider"],
        chat_model_name=row["chat_model_name"],
        chat_base_url=row["chat_base_url"] or "",
        chat_api_key_ref=row["chat_api_key_ref"],
        embedding_provider=row["embedding_provider"],
        embedding_model_name=row["embedding_model_name"],
        embedding_base_url=row["embedding_base_url"] or "",
        embedding_api_key_ref=row["embedding_api_key_ref"],
        temperature=float(row["temperature"]),
        top_k=int(row["top_k"]),
        similarity_threshold=float(row["similarity_threshold"]),
        timeout_seconds=int(row["timeout_seconds"]),
        max_tokens=int(row["max_tokens"]),
        created_by=row["created_by"],
        updated_at=row["updated_at"],
    )
