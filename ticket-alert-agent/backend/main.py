from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_openai import ChatOpenAI

from src.graph import TicketAlertGraph, append_user_message, new_state
from src.schemas import (
    ApproveCaseRequest,
    ContinueCaseRequest,
    CreateCaseRequest,
    ModelConfigCreate,
    ModelConfigUpdate,
)
from src.storage import Storage, utc_now


storage = Storage()
agent_graph = TicketAlertGraph(storage)

app = FastAPI(title="AI 工单/告警智能处置编排 Agent", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 统一封装 case 详情响应，附带当前状态、时间线和最终报告。
def case_response(state: dict) -> dict:
    return {
        "case_id": state["case_id"],
        "status": state.get("status"),
        "state": state,
        "timeline": storage.get_timeline(state["case_id"]),
        "report": storage.get_report(state["case_id"]),
    }


# 校验是否已启用真实模型配置，避免未配置模型时创建或继续 case。
def require_real_model_config() -> dict:
    config = storage.get_active_real_model_config()
    if not config:
        raise HTTPException(
            status_code=400,
            detail="未配置并启用真实模型，请先在“模型配置”页填写 Base URL、模型名和 API Key，并启用该配置。",
        )
    return config


# 提供后端健康检查，便于前端或脚本确认服务可用。
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "time": utc_now()}


# 创建新 case，并触发 LangGraph 从头执行处置流程。
@app.post("/api/cases")
def create_case(payload: CreateCaseRequest) -> dict:
    require_real_model_config()
    state = new_state(payload.message, payload.scenario)
    storage.save_case(dict(state))
    result = agent_graph.run(state)
    return case_response(dict(result))


# 返回最近的 case 列表，供前端侧边栏展示和恢复。
@app.get("/api/cases")
def list_cases() -> list[dict]:
    return storage.list_cases()


# 查询单个 case 的完整状态、时间线和报告。
@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    state = storage.get_case_state(case_id)
    if not state:
        raise HTTPException(status_code=404, detail="case 不存在")
    return case_response(state)


# 在等待用户补充信息时追加消息，并从同一个 case 恢复执行。
@app.post("/api/cases/{case_id}/message")
def continue_case(case_id: str, payload: ContinueCaseRequest) -> dict:
    state = storage.get_case_state(case_id)
    if not state:
        raise HTTPException(status_code=404, detail="case 不存在")
    if state.get("status") != "waiting_user":
        raise HTTPException(status_code=409, detail="当前 case 不处于等待用户补充状态")
    require_real_model_config()
    state = append_user_message(state, payload.message)
    result = agent_graph.run(state)
    return case_response(dict(result))


# 处理人工审批决策，支持批准、拒绝和修改动作后继续流程。
@app.post("/api/cases/{case_id}/approve")
def approve_case(case_id: str, payload: ApproveCaseRequest) -> dict:
    state = storage.get_case_state(case_id)
    if not state:
        raise HTTPException(status_code=404, detail="case 不存在")
    if state.get("status") != "waiting_approval":
        raise HTTPException(status_code=409, detail="当前 case 不处于等待审批状态")

    original_actions = state.get("proposed_actions", [])
    modified_actions = [item.model_dump() for item in payload.modified_actions] if payload.modified_actions else None
    state["approval_status"] = payload.decision
    state["approval_comment"] = payload.comment
    state["approved_actions"] = modified_actions if payload.decision == "modified" else original_actions
    state["resume_mode"] = "approval_rejected" if payload.decision == "rejected" else f"approval_{payload.decision}"
    state["messages"] = list(state.get("messages", [])) + [
        {
            "role": "approver",
            "content": f"{payload.decision}: {payload.comment}",
            "created_at": utc_now(),
        }
    ]
    storage.add_approval(case_id, payload.decision, original_actions, modified_actions, payload.comment)
    storage.add_timeline(
        case_id,
        "human_approval",
        "approval",
        input_data={"decision": payload.decision, "comment": payload.comment},
        output_data={"approved_actions": state["approved_actions"]},
    )
    result = agent_graph.run(state)
    return case_response(dict(result))


# 单独返回 case 时间线，方便前端局部刷新或调试。
@app.get("/api/cases/{case_id}/timeline")
def get_timeline(case_id: str) -> list[dict]:
    if not storage.get_case(case_id):
        raise HTTPException(status_code=404, detail="case 不存在")
    return storage.get_timeline(case_id)


# 单独返回最终 Markdown 报告。
@app.get("/api/cases/{case_id}/report")
def get_report(case_id: str) -> dict:
    report = storage.get_report(case_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return {"case_id": case_id, "report_markdown": report}


# 返回所有模型配置，API Key 只展示脱敏值。
@app.get("/api/model-configs")
def list_model_configs() -> list[dict]:
    return storage.list_model_configs()


# 新增真实模型配置，禁止再创建 mock 模型。
@app.post("/api/model-configs")
def create_model_config(payload: ModelConfigCreate) -> dict:
    if payload.provider == "mock":
        raise HTTPException(status_code=400, detail="不能创建 mock 模型配置，请配置真实模型")
    return storage.add_model_config(payload.model_dump())


# 更新模型配置，保留未传字段并禁止切换为 mock。
@app.put("/api/model-configs/{config_id}")
def update_model_config(config_id: str, payload: ModelConfigUpdate) -> dict:
    if payload.provider == "mock":
        raise HTTPException(status_code=400, detail="不能切换为 mock 模型配置，请配置真实模型")
    if not storage.update_model_config(config_id, payload.model_dump(exclude_unset=True)):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return {"ok": True}


# 删除未启用的模型配置，防止误删当前工作配置。
@app.delete("/api/model-configs/{config_id}")
def delete_model_config(config_id: str) -> dict:
    ok, message = storage.delete_model_config(config_id)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True}


# 启用指定真实模型配置，同一时间只保留一个 active 配置。
@app.post("/api/model-configs/{config_id}/activate")
def activate_model_config(config_id: str) -> dict:
    configs = [item for item in storage.list_model_configs() if item["id"] == config_id]
    if not configs:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    config = configs[0]
    if config["provider"] == "mock":
        raise HTTPException(status_code=400, detail="内置 mock 模型不能作为真实模型启用")
    if not config["model_name"] or not config["api_key_masked"]:
        raise HTTPException(status_code=400, detail="请先填写模型名和 API Key 后再启用")
    if not storage.activate_model_config(config_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return {"ok": True}


# 使用 LangChain 发起最小请求，真实测试模型连通性。
@app.post("/api/model-configs/{config_id}/test")
def test_model_config(config_id: str) -> dict:
    config = storage.get_model_config(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    if config["provider"] == "mock":
        message = "mock 模型不能作为真实模型使用，请配置 OpenAI-compatible、Azure OpenAI 或公司网关模型。"
        storage.record_model_test(config_id, "failed", message)
        return {"status": "failed", "message": message}

    base_url = (config.get("base_url") or "").strip()
    model_name = (config.get("model_name") or "").strip()
    api_key = storage.decrypt_key(config.get("api_key_encrypted"))
    if not base_url or not model_name or not api_key:
        message = "请填写 Base URL、模型名和 API Key 后再测试。"
        storage.record_model_test(config_id, "failed", message)
        return {"status": "failed", "message": message}

    try:
        llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            max_tokens=8,
            timeout=config.get("timeout_seconds") or 30,
            max_retries=0,
        )
        response = llm.invoke("请只回复 OK，用于连通性测试。")
        content = str(response.content).strip()
        if not content:
            raise RuntimeError("模型返回为空")
        message = f"模型连通性测试成功，返回：{content[:80]}"
        storage.record_model_test(config_id, "success", message)
        return {"status": "success", "message": message}
    except Exception as exc:
        message = f"模型连通性测试失败：{exc}"
        storage.record_model_test(config_id, "failed", message)
        return {"status": "failed", "message": message}
