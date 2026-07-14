from __future__ import annotations

import shutil
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import CHROMA_DIR, UPLOADS_DIR
from .document_loader import file_hash, load_file
from .model_config import build_embeddings
from .schemas import ImportResult, ModelConfig
from .storage import delete_documents_by_file, upsert_document


def collection_name(knowledge_base_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in knowledge_base_id.lower())
    return f"kb_{safe[:48]}"


def vectorstore(config: ModelConfig, knowledge_base_id: str) -> Chroma:
    return Chroma(
        collection_name=collection_name(knowledge_base_id),
        persist_directory=str(CHROMA_DIR),
        embedding_function=build_embeddings(config),
    )


def index_paths(paths: list[Path], knowledge_base_id: str, config: ModelConfig) -> ImportResult:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max(200, int(config.max_tokens * 0.75)) if config.max_tokens < 1200 else 900,
        chunk_overlap=150,
        separators=["\n## ", "\n### ", "\n", "。", "；", "，", " ", ""],
    )
    store = vectorstore(config, knowledge_base_id)
    failed: list[dict[str, str]] = []
    success = 0
    total_chunks = 0

    for path in paths:
        try:
            digest = file_hash(path)
            doc_id = f"doc_{knowledge_base_id}_{digest[:16]}"
            raw_docs = load_file(path, knowledge_base_id)
            if not raw_docs or not any(doc.page_content.strip() for doc in raw_docs):
                raise ValueError("未解析到有效文本。")

            chunks = splitter.split_documents(raw_docs)
            prepared = _prepare_chunks(chunks, knowledge_base_id, doc_id)
            try:
                store.delete(where={"file_name": path.name})
            except Exception:
                pass
            delete_documents_by_file(knowledge_base_id, path.name)
            store.add_documents(prepared, ids=[doc.metadata["chunk_id"] for doc in prepared])
            upsert_document(
                knowledge_base_id=knowledge_base_id,
                file_name=path.name,
                file_type=path.suffix.lower(),
                file_hash=digest,
                status="indexed",
                chunk_count=len(prepared),
            )
            total_chunks += len(prepared)
            success += 1
        except Exception as exc:  # noqa: BLE001 - Continue batch import.
            failed.append({"file_name": path.name, "error": str(exc)})
            upsert_document(
                knowledge_base_id=knowledge_base_id,
                file_name=path.name,
                file_type=path.suffix.lower(),
                file_hash=file_hash(path) if path.exists() else path.name,
                status="failed",
                chunk_count=0,
                error_message=str(exc),
            )

    return ImportResult(success_files=success, failed_files=failed, chunk_count=total_chunks)


def save_upload(file_obj, knowledge_base_id: str) -> Path:
    kb_dir = UPLOADS_DIR / knowledge_base_id
    kb_dir.mkdir(parents=True, exist_ok=True)
    target = kb_dir / file_obj.name
    with target.open("wb") as fh:
        fh.write(file_obj.getbuffer())
    return target


def sample_paths() -> list[Path]:
    from .config import SAMPLES_DIR

    return sorted(SAMPLES_DIR.glob("*.md"))


def reset_vectorstore() -> None:
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)


def _prepare_chunks(chunks: list[Document], knowledge_base_id: str, document_id: str) -> list[Document]:
    prepared: list[Document] = []
    for idx, doc in enumerate(chunks, start=1):
        chunk_id = f"{document_id}_chunk_{idx:04d}"
        metadata = dict(doc.metadata)
        metadata = {key: ("" if value is None else value) for key, value in metadata.items()}
        metadata.update(
            {
                "knowledge_base_id": knowledge_base_id,
                "document_id": document_id,
                "chunk_id": chunk_id,
                "chunk_index": idx,
                "text_preview": doc.page_content[:160],
            }
        )
        prepared.append(Document(page_content=doc.page_content, metadata=metadata))
    return prepared
