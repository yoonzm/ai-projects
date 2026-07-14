from __future__ import annotations

import time
from dataclasses import asdict
import re
from typing import Any

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool

from .indexer import vectorstore
from .model_config import MODEL_NOT_CONFIGURED_MESSAGE, build_chat_model, is_model_configured
from .schemas import ModelConfig, QAResult, SourceRef
from .storage import log_chat


UNKNOWN_ANSWER = "知识库未找到依据，当前资料不足以回答该问题。请补充相关制度、流程或 FAQ 文档后再试。"


@tool
def policy_search(query: str, knowledge_base_id: str, top_k: int = 5) -> str:
    """Search company policy knowledge base snippets for a user question."""
    return f"policy_search(query={query}, knowledge_base_id={knowledge_base_id}, top_k={top_k})"


def ask(
    *,
    question: str,
    knowledge_base_id: str,
    session_id: str,
    config: ModelConfig,
    history: list[dict[str, str]] | None = None,
    top_k: int | None = None,
) -> QAResult:
    start = time.perf_counter()
    rewritten = rewrite_question(question, history or [])
    if not is_model_configured(config):
        result = QAResult(
            answer=MODEL_NOT_CONFIGURED_MESSAGE,
            sources=[],
            uncertainty="当前未配置可用的 Chat Model，无法生成知识库问答。",
            rewritten_question=rewritten,
            tool_calls=[],
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        log_chat(
            session_id=session_id,
            knowledge_base_id=knowledge_base_id,
            user_input=question,
            rewritten_query=rewritten,
            answer=result.answer,
            source_refs=[],
            tool_calls=[],
            latency_ms=result.latency_ms,
        )
        return result

    tool_start = time.perf_counter()
    tool_output = policy_search.invoke(
        {"query": rewritten, "knowledge_base_id": knowledge_base_id, "top_k": top_k or config.top_k}
    )
    docs = retrieve(rewritten, knowledge_base_id, config, top_k or config.top_k)
    tool_calls = [
        {
            "name": "policy_search",
            "args": {"query": rewritten, "knowledge_base_id": knowledge_base_id, "top_k": top_k or config.top_k},
            "output": tool_output,
            "returned": len(docs),
            "latency_ms": int((time.perf_counter() - tool_start) * 1000),
        }
    ]
    evidence = [doc for doc, score in docs if score >= config.similarity_threshold]
    if evidence and not _evidence_supports_question(rewritten, evidence):
        evidence = []
    scored_evidence = [
        (doc, score)
        for doc, score in docs
        if score >= config.similarity_threshold and doc in evidence
    ]

    if not evidence:
        result = QAResult(
            answer=UNKNOWN_ANSWER,
            sources=[],
            uncertainty="没有检索到达到置信度阈值的知识库片段。",
            rewritten_question=rewritten,
            tool_calls=tool_calls,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
    else:
        sources = _sources(scored_evidence[:5])
        context = _format_context(evidence[:5])
        answer = generate_answer(question, context, sources, config)
        result = QAResult(
            answer=answer,
            sources=sources,
            uncertainty="回答仅基于展示的知识库来源；资料未说明的信息不做推断。",
            rewritten_question=rewritten,
            tool_calls=tool_calls,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )

    log_chat(
        session_id=session_id,
        knowledge_base_id=knowledge_base_id,
        user_input=question,
        rewritten_query=rewritten,
        answer=result.answer,
        source_refs=[asdict(source) for source in result.sources],
        tool_calls=tool_calls,
        latency_ms=result.latency_ms,
    )
    return result


def retrieve(
    query: str,
    knowledge_base_id: str,
    config: ModelConfig,
    top_k: int,
) -> list[tuple[Document, float]]:
    store = vectorstore(config, knowledge_base_id)
    candidate_k = max(top_k * 3, top_k + 8)
    results = store.similarity_search_with_score(
        query,
        k=candidate_k,
        filter={"knowledge_base_id": knowledge_base_id},
    )
    scored: list[tuple[Document, float]] = []
    for doc, distance in results:
        score = 1.0 / (1.0 + float(distance))
        score += _lexical_boost(query, doc.page_content)
        scored.append((doc, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:top_k]


def generate_answer(question: str, context: str, sources: list[SourceRef], config: ModelConfig) -> str:
    llm = build_chat_model(config)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是企业知识库智能问答助手。只能根据知识库资料回答；资料不足时必须说明知识库未找到依据，"
                "不得编造政策、金额、日期、审批人或流程节点。回答固定包含：直接回答、依据说明、来源、不确定性。",
            ),
            ("human", "用户问题：{question}\n\n知识库资料：\n{context}"),
        ]
    )
    try:
        response = (prompt | llm).invoke({"question": question, "context": context})
        return str(response.content)
    except Exception as exc:  # noqa: BLE001 - Show provider errors without crashing the page.
        return f"模型调用失败：{exc}"


def generate_material(
    *,
    material_type: str,
    topic: str,
    knowledge_base_id: str,
    config: ModelConfig,
    top_k: int | None = None,
) -> tuple[str, list[SourceRef], list[dict[str, Any]]]:
    if not is_model_configured(config):
        return MODEL_NOT_CONFIGURED_MESSAGE, [], []

    tool_output = policy_search.invoke(
        {"query": topic, "knowledge_base_id": knowledge_base_id, "top_k": top_k or config.top_k}
    )
    docs = retrieve(topic, knowledge_base_id, config, top_k or config.top_k)
    evidence = [(doc, score) for doc, score in docs if score >= config.similarity_threshold]
    tool_calls = [
        {
            "name": "policy_search",
            "args": {"query": topic, "knowledge_base_id": knowledge_base_id, "top_k": top_k or config.top_k},
            "output": tool_output,
            "returned": len(docs),
        }
    ]
    if not evidence:
        return UNKNOWN_ANSWER, [], tool_calls
    sources = _sources(evidence[:6])
    context = _format_context([doc for doc, _ in evidence[:6]])
    llm = build_chat_model(config)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是企业办公材料生成助手。必须基于知识库资料生成材料，不得使用资料外的信息补全政策内容。"
                "资料没有说明的字段写“资料未说明”。所有步骤、规则和注意事项都要保留来源。",
            ),
            ("human", "材料类型：{material_type}\n主题：{topic}\n\n知识库资料：\n{context}"),
        ]
    )
    try:
        response = (prompt | llm).invoke(
            {"material_type": material_type, "topic": topic, "context": context}
        )
        return str(response.content), sources, tool_calls
    except Exception as exc:  # noqa: BLE001 - Show provider errors without crashing the page.
        return f"模型调用失败：{exc}", sources, tool_calls


def rewrite_question(question: str, history: list[dict[str, str]]) -> str:
    if not history:
        return question
    pronouns = ("这个", "那个", "它", "该", "上述", "也", "还", "谁", "怎么")
    if any(word in question for word in pronouns):
        recent = next((item["content"] for item in reversed(history) if item.get("role") == "user"), "")
        if recent and recent != question:
            return f"{recent}。追问：{question}"
    return question


def _format_context(docs: list[Document]) -> str:
    parts = []
    for idx, doc in enumerate(docs, start=1):
        meta = doc.metadata
        parts.append(
            f"[{idx}] {meta.get('file_name')} - {meta.get('title') or ''} - {meta.get('chunk_id')}\n"
            f"{doc.page_content}"
        )
    return "\n\n".join(parts)


def _sources(scored_docs: list[tuple[Document, float]]) -> list[SourceRef]:
    refs: list[SourceRef] = []
    for doc, score in scored_docs:
        meta = doc.metadata
        refs.append(
            SourceRef(
                source_id=str(meta.get("chunk_id")),
                file_name=str(meta.get("file_name")),
                chunk_id=str(meta.get("chunk_id")),
                quote=doc.page_content[:320],
                title=meta.get("title"),
                page=meta.get("page"),
                score=round(score, 4),
            )
        )
    return refs


def _evidence_supports_question(question: str, evidence: list[Document]) -> bool:
    context = "\n".join(doc.page_content for doc in evidence)
    strict_terms = ["明年", "增加", "新增", "调整", "取消", "是否增加", "会不会"]
    asked_terms = [term for term in strict_terms if term in question]
    if asked_terms and not all(term in context for term in asked_terms if term not in {"是否增加", "会不会"}):
        return False
    return True


def _lexical_boost(query: str, text: str) -> float:
    terms = _query_terms(query)
    if not terms:
        return 0.0
    boost = 0.0
    for term in terms:
        if term and term in text:
            boost += 0.08 if len(term) <= 1 else 0.18
    if query in text:
        boost += 0.25
    return min(boost, 0.8)


def _query_terms(query: str) -> list[str]:
    terms: set[str] = set(re.findall(r"[A-Za-z0-9_]+", query))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]+", query))
    for size in (2, 3, 4):
        for idx in range(0, max(0, len(chinese) - size + 1)):
            term = chinese[idx : idx + size]
            if term not in {"是谁", "什么", "哪个", "多少"}:
                terms.add(term)
    return sorted(terms, key=len, reverse=True)
