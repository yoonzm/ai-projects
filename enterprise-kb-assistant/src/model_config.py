from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .local_models import HashEmbeddings
from .schemas import ModelConfig
from .storage import get_model_secrets


LOCAL_PROVIDER = "local-demo"
OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"
MODEL_NOT_CONFIGURED_MESSAGE = "Chat Model 未配置，请管理员先在“系统配置”中配置 Chat Model 和 API Key。"


class ModelNotConfiguredError(RuntimeError):
    pass


def is_model_configured(config: ModelConfig) -> bool:
    return is_chat_configured(config)


def is_chat_configured(config: ModelConfig) -> bool:
    chat_key, _ = get_model_secrets(config)
    if config.chat_provider in {"", LOCAL_PROVIDER}:
        return False
    if not config.chat_model_name or config.chat_model_name == LOCAL_PROVIDER:
        return False
    if not chat_key:
        return False
    return True


def is_embedding_configured(config: ModelConfig) -> bool:
    chat_key, embedding_key = get_model_secrets(config)
    if config.embedding_provider in {"", LOCAL_PROVIDER}:
        return False
    if not config.embedding_model_name or config.embedding_model_name == "hash-embedding":
        return False
    if not (embedding_key or chat_key):
        return False
    return True


def ensure_model_configured(config: ModelConfig) -> None:
    if not is_model_configured(config):
        raise ModelNotConfiguredError(MODEL_NOT_CONFIGURED_MESSAGE)


def build_embeddings(config: ModelConfig):
    if not is_embedding_configured(config):
        return HashEmbeddings()
    chat_key, embedding_key = get_model_secrets(config)
    primary = OpenAIEmbeddings(
        model=config.embedding_model_name,
        base_url=config.embedding_base_url or None,
        api_key=embedding_key or chat_key or None,
        timeout=config.timeout_seconds,
    )
    return FallbackEmbeddings(primary=primary, fallback=HashEmbeddings())


def build_chat_model(config: ModelConfig):
    ensure_model_configured(config)
    chat_key, _ = get_model_secrets(config)
    return ChatOpenAI(
        model=config.chat_model_name,
        base_url=config.chat_base_url or None,
        api_key=chat_key or None,
        temperature=config.temperature,
        timeout=config.timeout_seconds,
        max_tokens=config.max_tokens,
    )


def test_model_connection(config: ModelConfig) -> tuple[bool, str]:
    try:
        ensure_model_configured(config)
        llm = build_chat_model(config)
        response = llm.invoke("请只回复：连接成功")
        if not getattr(response, "content", ""):
            return False, "Chat Model 返回为空。"
        if is_embedding_configured(config):
            embeddings = build_embeddings(config)
            vector = embeddings.embed_query("连接测试")
            if not vector:
                return True, "Chat Model 连接通过；Embedding 返回为空，运行时将使用本地 embedding。"
            return True, "Chat Model 和 Embedding 连接测试通过。"
        return True, "Chat Model 连接测试通过；Embedding 未配置，运行时将使用本地 embedding。"
    except ModelNotConfiguredError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - Surface provider errors in UI.
        return False, f"连接测试失败：{exc}"


class FallbackEmbeddings(Embeddings):
    def __init__(self, primary: Embeddings, fallback: Embeddings) -> None:
        self.primary = primary
        self.fallback = fallback

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return self.primary.embed_documents(texts)
        except Exception:
            return self.fallback.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        try:
            return self.primary.embed_query(text)
        except Exception:
            return self.fallback.embed_query(text)
