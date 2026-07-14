from __future__ import annotations

import json
import sys
import uuid
import pandas as pd
from html import escape
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import admin_password
from src.indexer import index_paths, sample_paths, save_upload
from src.model_config import (
    MODEL_NOT_CONFIGURED_MESSAGE,
    ModelNotConfiguredError,
    OPENAI_COMPATIBLE_PROVIDER,
    is_model_configured,
    test_model_connection,
)
from src.rag import ask, generate_material
from src.storage import (
    get_enabled_model_config,
    init_db,
    list_documents,
    list_logs,
    list_model_configs,
    save_model_config,
    set_feedback,
)


st.set_page_config(
    page_title="企业知识库智能问答助手",
    layout="wide",
    initial_sidebar_state="expanded",
)


STYLE = """
<style>
  :root {
    --color-background: #f8fafc;
    --color-foreground: #1e293b;
    --color-muted: #eaeff3;
    --color-border: #e2e8f0;
    --color-accent: #2563eb;
  }
  .block-container { padding-top: 1.25rem; padding-bottom: 2rem; }
  h1, h2, h3 { letter-spacing: 0; }
  .kb-header {
    padding: 1rem 0 0.5rem 0;
    border-bottom: 1px solid var(--color-border);
    margin-bottom: 1rem;
  }
  .status-row {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.75rem;
  }
  .status-box {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    background: white;
    padding: 0.85rem;
  }
  .status-label { color: #64748b; font-size: 0.8rem; }
  .status-value { color: var(--color-foreground); font-size: 1.15rem; font-weight: 650; margin-top: 0.2rem; }
  .source-card {
    border: 1px solid var(--color-border);
    border-radius: 8px;
    background: #ffffff;
    padding: 0.75rem;
    margin-bottom: 0.5rem;
  }
  .source-title { font-weight: 650; color: #0f172a; }
  .source-meta { color: #64748b; font-size: 0.82rem; margin-bottom: 0.4rem; }
  .tool-call {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.82rem;
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 0.5rem;
  }
  @media (max-width: 800px) {
    .status-row { grid-template-columns: 1fr; }
  }
</style>
"""


def main() -> None:
    init_db()
    st.markdown(STYLE, unsafe_allow_html=True)
    _init_session()

    config = get_enabled_model_config()
    _sidebar(config)

    st.markdown(
        "<div class='kb-header'><h1>企业知识库智能问答与材料生成助手</h1>"
        "<p>基于文档索引、来源引用、工具调用和结构化材料生成的 AI 工作台。</p></div>",
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["系统配置", "知识库", "智能问答", "材料生成", "日志评估"])
    with tabs[0]:
        admin_config_tab(config)
    with tabs[1]:
        knowledge_base_tab(config)
    with tabs[2]:
        chat_tab(config)
    with tabs[3]:
        material_tab(config)
    with tabs[4]:
        logs_tab()


def _init_session() -> None:
    st.session_state.setdefault("session_id", f"s_{uuid.uuid4().hex[:10]}")
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("admin_authed", False)
    st.session_state.setdefault("knowledge_base_id", "employee-demo")


def _sidebar(config) -> None:
    with st.sidebar:
        st.subheader("当前配置")
        configured = is_model_configured(config)
        st.write(f"状态：`{'已配置' if configured else '未配置'}`")
        st.write(f"模型：`{config.chat_model_name or '未配置'}`")
        st.write(f"Embedding：`{config.embedding_model_name or '未配置'}`")
        st.write(f"知识库：`{st.session_state.knowledge_base_id}`")
        st.divider()
        st.session_state.knowledge_base_id = st.text_input(
            "知识库 ID",
            value=st.session_state.knowledge_base_id,
            help="用于隔离不同知识库空间。",
        )
        st.caption("普通用户无需配置 API Key；管理员在系统配置中统一启用模型。")


def admin_config_tab(config) -> None:
    st.subheader("管理员系统配置")
    if not st.session_state.admin_authed:
        password = st.text_input("管理员密码", type="password", help="默认来自 ADMIN_PASSWORD 环境变量。")
        if st.button("进入配置", type="primary"):
            if password == admin_password():
                st.session_state.admin_authed = True
                st.rerun()
            st.error("管理员密码不正确。")
        st.info("普通用户无需进入本页。当前页面仅用于管理员统一配置模型和密钥。")
        return

    st.success("已进入管理员配置。API Key 不会展示给普通用户。")
    providers = [OPENAI_COMPATIBLE_PROVIDER]
    with st.form("model_config_form"):
        col1, col2 = st.columns(2)
        with col1:
            chat_provider = st.selectbox("Chat Provider", providers, index=providers.index(config.chat_provider) if config.chat_provider in providers else 0)
            chat_model_name = st.text_input("Chat Model", value="" if config.chat_model_name == "local-demo" else config.chat_model_name)
            chat_base_url = st.text_input("Chat Base URL", value=config.chat_base_url)
            chat_api_key = st.text_input("Chat API Key", type="password", value="")
        with col2:
            embedding_provider = st.selectbox("Embedding Provider", providers, index=providers.index(config.embedding_provider) if config.embedding_provider in providers else 0)
            embedding_model_name = st.text_input("Embedding Model", value="" if config.embedding_model_name == "hash-embedding" else config.embedding_model_name)
            embedding_base_url = st.text_input("Embedding Base URL", value=config.embedding_base_url)
            embedding_api_key = st.text_input("Embedding API Key", type="password", value="")

        p1, p2, p3, p4 = st.columns(4)
        temperature = p1.slider("Temperature", 0.0, 1.0, float(config.temperature), 0.05)
        top_k = p2.slider("Top K", 1, 10, int(config.top_k), 1)
        threshold = p3.slider("相似度阈值", 0.0, 1.0, float(config.similarity_threshold), 0.01)
        timeout = p4.number_input("超时秒数", 5, 120, int(config.timeout_seconds), 5)
        max_tokens = st.number_input("最大输出长度", 256, 8000, int(config.max_tokens), 128)

        submitted = st.form_submit_button("保存并启用配置", type="primary")

    if submitted:
        updated = save_model_config(
            name="admin-config",
            chat_provider=chat_provider,
            chat_model_name=chat_model_name,
            chat_base_url=chat_base_url,
            chat_api_key=chat_api_key,
            embedding_provider=embedding_provider,
            embedding_model_name=embedding_model_name,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
            temperature=temperature,
            top_k=top_k,
            similarity_threshold=threshold,
            timeout_seconds=int(timeout),
            max_tokens=int(max_tokens),
        )
        ok, message = test_model_connection(updated)
        if ok:
            st.success(message)
        else:
            st.warning(message)
        st.warning("如果 embedding 配置发生变化，请重新构建知识库索引。")

    st.markdown("#### 已保存配置")
    for item in list_model_configs():
        marker = "启用" if item.enabled else "备用"
        st.write(f"- `{marker}` {item.name} / {item.chat_provider}:{item.chat_model_name} / {item.embedding_provider}:{item.embedding_model_name} / {item.updated_at}")


def knowledge_base_tab(config) -> None:
    st.subheader("知识库导入与索引")
    configured = is_model_configured(config)
    if not configured:
        st.warning(MODEL_NOT_CONFIGURED_MESSAGE)
    kb_id = st.session_state.knowledge_base_id
    col1, col2 = st.columns([1, 1])
    with col1:
        uploads = st.file_uploader(
            "上传文档",
            accept_multiple_files=True,
            type=["md", "markdown", "txt", "pdf", "docx"],
        )
        if st.button("上传并构建索引", type="primary", disabled=not uploads or not configured):
            paths = [save_upload(file, kb_id) for file in uploads]
            with st.spinner("正在解析、切分并写入向量库..."):
                try:
                    result = index_paths(paths, kb_id, config)
                    _show_import_result(result)
                except ModelNotConfiguredError as exc:
                    st.error(str(exc))

    with col2:
        st.write("样例资料包")
        st.caption("包含员工手册、差旅报销、研发流程、IT FAQ、AI 使用规范。")
        if st.button("导入样例文档", disabled=not configured):
            with st.spinner("正在导入样例资料..."):
                try:
                    result = index_paths(sample_paths(), kb_id, config)
                    _show_import_result(result)
                except ModelNotConfiguredError as exc:
                    st.error(str(exc))

    docs = list_documents(kb_id)
    st.markdown("#### 索引状态")
    st.markdown(
        f"<div class='status-row'>"
        f"<div class='status-box'><div class='status-label'>文件数</div><div class='status-value'>{len(docs)}</div></div>"
        f"<div class='status-box'><div class='status-label'>Chunk 数</div><div class='status-value'>{sum(d.get('chunk_count', 0) for d in docs)}</div></div>"
        f"<div class='status-box'><div class='status-label'>当前模型</div><div class='status-value'>{config.chat_model_name or '未配置'}</div></div>"
        f"<div class='status-box'><div class='status-label'>Embedding</div><div class='status-value'>{config.embedding_model_name or '未配置'}</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if docs:
        st.dataframe(docs, width="stretch", hide_index=True)
    else:
        st.info("当前知识库还没有索引文档。")


def chat_tab(config) -> None:
    st.subheader("智能问答")
    configured = is_model_configured(config)
    if not configured:
        st.warning(MODEL_NOT_CONFIGURED_MESSAGE)
    question = st.text_area("问题", placeholder="例如：出差住宿费标准是多少？如果超标怎么审批？", height=100)
    col1, col2 = st.columns([1, 4])
    top_k = col1.slider("Top K", 1, 10, int(config.top_k), 1, key="chat_top_k")
    ask_clicked = col2.button("提问", type="primary", width="stretch")

    if ask_clicked and question.strip():
        with st.spinner("正在检索资料并生成回答..."):
            try:
                result = ask(
                    question=question.strip(),
                    knowledge_base_id=st.session_state.knowledge_base_id,
                    session_id=st.session_state.session_id,
                    config=config,
                    history=st.session_state.chat_history,
                    top_k=top_k,
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"问答失败：{exc}")
                return
        st.session_state.chat_history.append({"role": "user", "content": question.strip()})
        st.session_state.chat_history.append({"role": "assistant", "content": result.answer})
        render_qa_result(result)

    if st.session_state.chat_history:
        with st.expander("会话上下文", expanded=False):
            for item in st.session_state.chat_history[-8:]:
                st.write(f"**{item['role']}**：{item['content']}")


def material_tab(config) -> None:
    st.subheader("材料生成")
    configured = is_model_configured(config)
    if not configured:
        st.warning(MODEL_NOT_CONFIGURED_MESSAGE)
    col1, col2 = st.columns([1, 2])
    material_type = col1.selectbox("材料类型", ["流程清单", "FAQ", "政策摘要", "邮件草稿"])
    topic = col2.text_input("主题", placeholder="例如：申请差旅报销")
    if st.button("生成材料", type="primary", disabled=not topic.strip()):
        with st.spinner("正在检索来源并生成材料..."):
            try:
                text, sources, tool_calls = generate_material(
                    material_type=material_type,
                    topic=topic.strip(),
                    knowledge_base_id=st.session_state.knowledge_base_id,
                    config=config,
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"材料生成失败：{exc}")
                return
        st.markdown(text)
        st.download_button("导出 Markdown", data=text, file_name=f"{topic}.md", mime="text/markdown")
        render_sources(sources)
        render_tool_calls(tool_calls)


def logs_tab() -> None:
    st.subheader("日志与反馈")
    logs = list_logs(50)
    if not logs:
        st.info("暂无问答日志。")
        return
    for item in logs:
        with st.expander(f"{item['created_at']} · {item['user_input'][:60]}"):
            st.write("**改写问题**")
            st.code(item["rewritten_query"] or "")
            st.write("**回答**")
            st.markdown(item["answer"] or "")
            st.write("**来源**")
            st.json(json.loads(item["source_refs"] or "[]"))
            st.write("**工具调用**")
            st.json(json.loads(item["tool_calls"] or "[]"))
            c1, c2, c3 = st.columns([1, 1, 4])
            if c1.button("点赞", key=f"up_{item['id']}"):
                set_feedback(item["id"], "up")
                st.success("已记录反馈。")
            if c2.button("点踩", key=f"down_{item['id']}"):
                set_feedback(item["id"], "down")
                st.warning("已记录反馈。")
            c3.caption(f"耗时：{item['latency_ms']} ms · 当前反馈：{item['feedback'] or '无'}")
    export_rows = [
        {
            "created_at": item["created_at"],
            "knowledge_base_id": item["knowledge_base_id"],
            "question": item["user_input"],
            "rewritten_query": item["rewritten_query"],
            "latency_ms": item["latency_ms"],
            "feedback": item["feedback"],
        }
        for item in logs
    ]
    st.download_button(
        "导出日志 CSV",
        data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8-sig"),
        file_name="qa_logs.csv",
        mime="text/csv",
    )


def render_qa_result(result) -> None:
    st.markdown("#### 回答")
    st.markdown(result.answer)
    st.caption(f"改写问题：{result.rewritten_question} · 耗时：{result.latency_ms} ms")
    if result.uncertainty:
        st.info(result.uncertainty)
    render_sources(result.sources)
    render_tool_calls(result.tool_calls)


def render_sources(sources) -> None:
    st.markdown("#### 来源引用")
    if not sources:
        st.warning("没有可展示的来源引用。")
        return
    for idx, source in enumerate(sources, start=1):
        page = f" · 页码 {source.page}" if source.page else ""
        score = f" · 置信度 {source.score}" if source.score is not None else ""
        st.markdown(
            f"<div class='source-card'><div class='source-title'>[{idx}] {source.file_name}</div>"
            f"<div class='source-meta'>{source.chunk_id}{page}{score}</div>"
            f"<div>{escape(source.quote)}</div></div>",
            unsafe_allow_html=True,
        )


def render_tool_calls(tool_calls) -> None:
    st.markdown("#### 工具调用")
    if not tool_calls:
        st.caption("本次没有工具调用。")
        return
    for call in tool_calls:
        st.markdown(f"<div class='tool-call'>{json.dumps(call, ensure_ascii=False)}</div>", unsafe_allow_html=True)


def _show_import_result(result) -> None:
    if result.failed_files:
        st.warning(f"导入完成：成功 {result.success_files} 个文件，失败 {len(result.failed_files)} 个文件，生成 {result.chunk_count} 个 chunk。")
        st.json(result.failed_files)
    else:
        st.success(f"导入成功：{result.success_files} 个文件，生成 {result.chunk_count} 个 chunk。")


if __name__ == "__main__":
    main()
