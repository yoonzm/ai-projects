from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .mock_tools import (
    HIGH_RISK_ACTIONS,
    mock_cmdb_lookup,
    mock_execute_action,
    mock_history_search,
    mock_log_search,
    mock_notify,
    mock_risk_score,
    mock_sop_search,
    mock_ticket_create,
)
from .llm import CHECK_INFO_PROMPT, CLASSIFY_PROMPT, DIAGNOSE_PROMPT, LLMService
from .report import render_report
from .schemas import AgentState
from .storage import Storage, utc_now


NodeFn = Callable[[AgentState], AgentState]


class TicketAlertGraph:
    # 初始化 LangGraph 图和 SQLite checkpointer，支撑按 case_id 持久化执行状态。
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.llm = LLMService(storage)
        checkpoint_path = self.storage.db_path.with_name("checkpoints.db")
        self._checkpoint_conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self._checkpoint_conn)
        self.checkpointer.setup()
        self.graph = self._build_graph().compile(checkpointer=self.checkpointer)

    # 显式定义 LangGraph 节点、普通边、条件边和验证失败循环。
    def _build_graph(self) -> StateGraph:
        builder = StateGraph(AgentState)
        builder.add_node("receive_input", self._node("receive_input", self.receive_input))
        builder.add_node("classify_case", self._node("classify_case", self.classify_case))
        builder.add_node("check_info", self._node("check_info", self.check_info))
        builder.add_node("ask_clarification", self._node("ask_clarification", self.ask_clarification))
        builder.add_node("query_context", self._node("query_context", self.query_context))
        builder.add_node("diagnose", self._node("diagnose", self.diagnose))
        builder.add_node("human_approval", self._node("human_approval", self.human_approval))
        builder.add_node("execute_action", self._node("execute_action", self.execute_action))
        builder.add_node("verify_result", self._node("verify_result", self.verify_result))
        builder.add_node("close_or_escalate", self._node("close_or_escalate", self.close_or_escalate))

        builder.add_edge(START, "receive_input")
        builder.add_conditional_edges(
            "receive_input",
            self.route_after_receive,
            {
                "classify_case": "classify_case",
                "execute_action": "execute_action",
                "close_or_escalate": "close_or_escalate",
            },
        )
        builder.add_edge("classify_case", "check_info")
        builder.add_conditional_edges(
            "check_info",
            self.route_after_check_info,
            {"ask_clarification": "ask_clarification", "query_context": "query_context"},
        )
        builder.add_edge("ask_clarification", END)
        builder.add_edge("query_context", "diagnose")
        builder.add_conditional_edges(
            "diagnose",
            self.route_after_diagnose,
            {"human_approval": "human_approval", "execute_action": "execute_action"},
        )
        builder.add_edge("human_approval", END)
        builder.add_edge("execute_action", "verify_result")
        builder.add_conditional_edges(
            "verify_result",
            self.route_after_verify,
            {"query_context": "query_context", "close_or_escalate": "close_or_escalate"},
        )
        builder.add_edge("close_or_escalate", END)
        return builder

    # 执行一次图流程，并将最终 state 保存到业务表。
    def run(self, state: AgentState) -> AgentState:
        config = {"configurable": {"thread_id": state["case_id"]}}
        result = self.graph.invoke(state, config=config)
        self.storage.save_case(dict(result))
        return result

    # 包装节点函数，统一记录节点开始、结束、耗时和异常到 timeline。
    def _node(self, name: str, fn: NodeFn) -> NodeFn:
        # 内部闭包实际承载节点执行，保证每个节点都有统一观测记录。
        def wrapped(state: AgentState) -> AgentState:
            started = time.perf_counter()
            self.storage.add_timeline(
                state["case_id"],
                name,
                "node_start",
                input_data=self._input_summary(state),
            )
            try:
                result = fn(state)
                duration = int((time.perf_counter() - started) * 1000)
                self.storage.add_timeline(
                    result["case_id"],
                    name,
                    "node_end",
                    output_data=self._output_summary(result),
                    duration_ms=duration,
                )
                return result
            except Exception as exc:
                duration = int((time.perf_counter() - started) * 1000)
                state["status"] = "failed"
                state["error"] = {"node": name, "message": str(exc)}
                self.storage.add_timeline(
                    state["case_id"],
                    name,
                    "error",
                    output_data=state["error"],
                    duration_ms=duration,
                )
                return state

        return wrapped

    # 接收新输入或恢复输入，初始化通用字段并记录当前启用模型配置。
    def receive_input(self, state: AgentState) -> AgentState:
        now = utc_now()
        state.setdefault("created_at", now)
        state["updated_at"] = now
        state.setdefault("messages", [])
        state.setdefault("retry_count", 0)
        state.setdefault("tool_results", {})
        state.setdefault("action_results", [])
        state.setdefault("approval_status", "none")
        state["status"] = "open" if state.get("status") not in {"closed", "escalated"} else state["status"]

        active = self.storage.get_active_model_config()
        if active:
            state["model_config_id"] = active["id"]
            state["model_name"] = active["model_name"]
            self.storage.add_timeline(
                state["case_id"],
                "model_config",
                "llm",
                output_data={
                    "model_config_id": active["id"],
                    "model_name": active["model_name"],
                    "provider": active["provider"],
                },
            )
        else:
            state["status"] = "failed"
            state["error"] = {"message": "未启用模型配置，请先在模型配置页启用一套配置。"}
        return state

    # 调用真实大模型判断 case 类型、场景、优先级和后续必填字段。
    def classify_case(self, state: AgentState) -> AgentState:
        try:
            result = self.llm.invoke_json(
                CLASSIFY_PROMPT,
                {
                    "messages": state.get("messages", []),
                    "allowed_case_types": ["it_ticket", "ops_alert", "security_incident", "unknown"],
                    "allowed_scenarios": ["vpn_login", "cpu_alert", "abnormal_login", "custom"],
                },
            )
            state.update(
                {
                    "case_type": normalize_choice(
                        result.get("case_type"),
                        {"it_ticket", "ops_alert", "security_incident", "unknown"},
                        "unknown",
                    ),
                    "scenario": normalize_choice(
                        result.get("scenario"),
                        {"vpn_login", "cpu_alert", "abnormal_login", "custom"},
                        "custom",
                    ),
                    "priority": normalize_choice(result.get("priority"), {"P0", "P1", "P2", "P3"}, "P3"),
                    "confidence": safe_float(result.get("confidence"), 0.5),
                    "required_fields": ensure_str_list(result.get("required_fields")) or ["problem_description"],
                    "route_reason": str(result.get("reason") or "模型完成分类。"),
                }
            )
            self.storage.add_timeline(
                state["case_id"],
                "classify_case",
                "llm",
                input_data={"messages": state.get("messages", [])},
                output_data=result,
            )
        except Exception as exc:
            self.storage.add_timeline(
                state["case_id"],
                "classify_case",
                "error",
                output_data={"message": f"LLM 分类失败，使用规则兜底：{exc}"},
            )
            apply_rule_classification(state)
        state["title"] = build_title(state)
        return state

    # 调用真实大模型抽取关键信息，并计算仍缺失的字段。
    def check_info(self, state: AgentState) -> AgentState:
        try:
            result = self.llm.invoke_json(
                CHECK_INFO_PROMPT,
                {
                    "messages": state.get("messages", []),
                    "case_type": state.get("case_type"),
                    "scenario": state.get("scenario"),
                    "required_fields": state.get("required_fields", []),
                    "existing_extracted_fields": state.get("extracted_fields", {}),
                },
            )
            extracted = result.get("extracted_fields") if isinstance(result.get("extracted_fields"), dict) else {}
            required = ensure_str_list(state.get("required_fields"))
            missing = [
                item
                for item in ensure_str_list(result.get("missing_fields"))
                if item in required and not extracted.get(item)
            ]
            state["extracted_fields"] = extracted
            state["missing_fields"] = missing
            state["pending_question"] = str(result.get("pending_question") or "")
            state["route_reason"] = (
                f"模型判断缺少 {', '.join(missing)}，进入追问。"
                if missing
                else "模型判断关键信息完整，进入工具查询。"
            )
            self.storage.add_timeline(
                state["case_id"],
                "check_info",
                "llm",
                input_data={"case_type": state.get("case_type"), "required_fields": required},
                output_data=result,
            )
        except Exception as exc:
            self.storage.add_timeline(
                state["case_id"],
                "check_info",
                "error",
                output_data={"message": f"LLM 信息抽取失败，使用规则兜底：{exc}"},
            )
            apply_rule_info_check(state)
        return state

    # 基于类型、字段和工具结果调用真实大模型生成诊断结论、风险等级和建议动作。
    def diagnose(self, state: AgentState) -> AgentState:
        try:
            result = self.llm.invoke_json(
                DIAGNOSE_PROMPT,
                {
                    "messages": state.get("messages", []),
                    "case_type": state.get("case_type"),
                    "scenario": state.get("scenario"),
                    "priority": state.get("priority"),
                    "extracted_fields": state.get("extracted_fields", {}),
                    "tool_results": state.get("tool_results", {}),
                    "retry_count": state.get("retry_count", 0),
                },
                max_tokens=1200,
            )
            proposed_actions = normalize_actions(result.get("proposed_actions"))
            if not proposed_actions:
                proposed_actions = [
                    {
                        "action_name": "create_ticket",
                        "target": "manual_triage",
                        "params": {"title": state.get("title", "人工分诊"), "priority": state.get("priority", "P3")},
                        "reason": "模型未给出可执行动作，创建人工分诊工单。",
                        "approval_required": False,
                    }
                ]
            state["risk_level"] = normalize_choice(result.get("risk_level"), {"low", "medium", "high"}, "medium")
            state["proposed_actions"] = proposed_actions
            state["diagnosis"] = {
                "hypotheses": result.get("hypotheses") if isinstance(result.get("hypotheses"), list) else [],
                "risk_level": state["risk_level"],
                "proposed_actions": proposed_actions,
                "user_facing_summary": str(result.get("user_facing_summary") or "模型已生成处置建议。"),
            }
            self.storage.add_timeline(
                state["case_id"],
                "diagnose",
                "llm",
                input_data={"case_type": state.get("case_type"), "tool_results": state.get("tool_results", {})},
                output_data=result,
            )
        except Exception as exc:
            self.storage.add_timeline(
                state["case_id"],
                "diagnose",
                "error",
                output_data={"message": f"LLM 诊断失败，使用规则兜底：{exc}"},
            )
            apply_rule_diagnosis(state)
        return state

    # 生成追问问题并把流程暂停在 waiting_user。
    def ask_clarification(self, state: AgentState) -> AgentState:
        missing = state.get("missing_fields", [])
        if state.get("pending_question"):
            question = state["pending_question"]
        else:
            labels = {
                "user_id": "账号",
                "error_message": "错误提示",
                "occurred_at": "发生时间",
                "environment": "终端或网络环境",
                "service_name": "服务名",
                "time_range": "告警时间范围",
                "failure_count": "失败次数",
                "location": "登录地点",
            }
            readable = "、".join(labels.get(item, item) for item in missing)
            question = f"为了继续处置，请补充：{readable}。"
        state["pending_question"] = question
        state["status"] = "waiting_user"
        state["route_reason"] = "信息不足，流程暂停等待用户补充。"
        self.storage.add_timeline(
            state["case_id"],
            "check_info",
            "route",
            output_data={"reason": state["route_reason"], "missing_fields": missing},
            route_to="ask_clarification",
        )
        return state

    # 根据 case 类型选择并调用 mock 工具，收集上下文证据。
    def query_context(self, state: AgentState) -> AgentState:
        case_type = state.get("case_type")
        fields = state.get("extracted_fields", {})
        results = dict(state.get("tool_results", {}))
        if case_type == "it_ticket":
            results["mock_sop_search"] = self.call_tool(
                state, "mock_sop_search", mock_sop_search, case_type=case_type, symptom=fields.get("error_message")
            )
            results["mock_history_search"] = self.call_tool(
                state,
                "mock_history_search",
                mock_history_search,
                case_type=case_type,
                target=fields.get("user_id", ""),
                symptom=fields.get("error_message", ""),
            )
            results["mock_risk_score"] = self.call_tool(
                state,
                "mock_risk_score",
                mock_risk_score,
                case_type=case_type,
                action="create_ticket",
                asset_level="account",
            )
        elif case_type == "ops_alert":
            cmdb = self.call_tool(
                state, "mock_cmdb_lookup", mock_cmdb_lookup, service_name=fields.get("service_name")
            )
            results["mock_cmdb_lookup"] = cmdb
            results["mock_log_search"] = self.call_tool(
                state,
                "mock_log_search",
                mock_log_search,
                service_name=fields.get("service_name"),
                time_range=fields.get("time_range"),
                keyword=fields.get("metric", "error"),
            )
            results["mock_sop_search"] = self.call_tool(
                state, "mock_sop_search", mock_sop_search, case_type=case_type, symptom="CPU high"
            )
            results["mock_risk_score"] = self.call_tool(
                state,
                "mock_risk_score",
                mock_risk_score,
                case_type=case_type,
                action="restart_service_mock",
                asset_level=cmdb.get("asset_level"),
            )
        elif case_type == "security_incident":
            cmdb = self.call_tool(
                state, "mock_cmdb_lookup", mock_cmdb_lookup, user_id=fields.get("user_id")
            )
            results["mock_cmdb_lookup"] = cmdb
            results["mock_history_search"] = self.call_tool(
                state,
                "mock_history_search",
                mock_history_search,
                case_type=case_type,
                target=fields.get("user_id"),
                symptom="abnormal_login",
            )
            results["mock_sop_search"] = self.call_tool(
                state, "mock_sop_search", mock_sop_search, case_type=case_type, symptom="abnormal_login"
            )
            results["mock_risk_score"] = self.call_tool(
                state,
                "mock_risk_score",
                mock_risk_score,
                case_type=case_type,
                action="reset_password_mock",
                asset_level="account",
            )
        state["tool_results"] = results
        return state

    # 高风险动作的人工审批暂停点，等待前端提交审批决策。
    def human_approval(self, state: AgentState) -> AgentState:
        state["approval_status"] = "pending"
        state["status"] = "waiting_approval"
        state["route_reason"] = "存在高风险动作，流程暂停等待人工审批。"
        self.storage.add_timeline(
            state["case_id"],
            "diagnose",
            "route",
            output_data={
                "reason": state["route_reason"],
                "risk_level": state.get("risk_level"),
                "proposed_actions": state.get("proposed_actions", []),
            },
            route_to="human_approval",
        )
        return state

    # 执行低风险动作或已审批动作，所有动作仍然只调用 mock 工具。
    def execute_action(self, state: AgentState) -> AgentState:
        state["status"] = "executing"
        actions = state.get("approved_actions") or state.get("proposed_actions", [])
        results: list[dict[str, Any]] = []
        for action in actions:
            name = action["action_name"]
            if name == "create_ticket":
                result = self.call_tool(state, "mock_ticket_create", mock_ticket_create, **action.get("params", {}))
                result.update({"action_name": name, "target": action.get("target"), "message": "已创建 mock 工单"})
            elif name == "send_notification":
                result = self.call_tool(state, "mock_notify", mock_notify, **action.get("params", {}))
                result.update({"action_name": name, "target": action.get("target"), "message": "已发送 mock 通知"})
            else:
                result = self.call_tool(
                    state,
                    "mock_execute_action",
                    mock_execute_action,
                    action_name=name,
                    target=action.get("target", ""),
                    params=action.get("params", {}),
                    approval_status=state.get("approval_status", "none"),
                )
            results.append(result)
        state["action_results"] = results
        return state

    # 根据动作结果判断是否验证通过，失败时增加重试计数。
    def verify_result(self, state: AgentState) -> AgentState:
        results = state.get("action_results", [])
        blocked = any(item.get("blocked") for item in results)
        force_fail = any(item.get("verify_hint") == "force_fail" for item in results)
        success = bool(results) and all(
            item.get("success") or item.get("sent") or item.get("status") == "created"
            for item in results
        )
        if blocked:
            state["verified"] = False
            state["verify_notes"] = "高风险动作被硬校验拦截，未执行。"
            state["retry_count"] = 2
        elif force_fail:
            state["verified"] = False
            state["retry_count"] = int(state.get("retry_count", 0)) + 1
            state["verify_notes"] = f"模拟验证失败，第 {state['retry_count']} 次重试。"
        elif success:
            state["verified"] = True
            state["verify_notes"] = "mock 验证通过：动作结果成功，后续异常停止或工单已创建。"
        else:
            state["verified"] = False
            state["retry_count"] = int(state.get("retry_count", 0)) + 1
            state["verify_notes"] = "未获得成功动作结果，准备重试或升级。"
        return state

    # 关闭成功 case 或升级失败/拒绝 case，并生成最终 Markdown 报告。
    def close_or_escalate(self, state: AgentState) -> AgentState:
        if state.get("approval_status") == "rejected":
            state["status"] = "escalated"
            state["verified"] = False
            state["verify_notes"] = "审批拒绝，高风险动作未执行，已升级人工。"
            ticket = self.call_tool(
                state,
                "mock_ticket_create",
                mock_ticket_create,
                title=f"人工升级：{state.get('title', '')}",
                priority=state.get("priority", "P2"),
                assignee="人工值班组",
                description=state.get("approval_comment", "审批拒绝"),
            )
            state.setdefault("tool_results", {})["escalation_ticket"] = ticket
        elif state.get("verified"):
            state["status"] = "closed"
        else:
            state["status"] = "escalated"
            ticket = self.call_tool(
                state,
                "mock_ticket_create",
                mock_ticket_create,
                title=f"验证失败升级：{state.get('title', '')}",
                priority=state.get("priority", "P2"),
                assignee="人工值班组",
                description=state.get("verify_notes", ""),
            )
            state.setdefault("tool_results", {})["escalation_ticket"] = ticket

        state["final_report"] = render_report(dict(state))
        self.storage.save_report(state["case_id"], state["final_report"])
        self.storage.add_timeline(
            state["case_id"],
            "close_or_escalate",
            "route",
            output_data={"status": state["status"], "verified": state.get("verified")},
            route_to=END,
        )
        return state

    # 根据恢复模式决定从分类、执行或关闭节点继续。
    def route_after_receive(self, state: AgentState) -> str:
        if state.get("status") == "failed":
            return "close_or_escalate"
        mode = state.get("resume_mode")
        if mode == "approval_rejected":
            route = "close_or_escalate"
            reason = "审批拒绝，直接生成升级报告。"
        elif mode in {"approval_approved", "approval_modified"}:
            route = "execute_action"
            reason = "审批通过或修改，恢复执行动作。"
        else:
            route = "classify_case"
            reason = "新输入或用户补充，进入分类和信息检查。"
        self.record_route(state, "receive_input", route, reason)
        return route

    # 信息完整则进入工具查询，否则进入追问暂停节点。
    def route_after_check_info(self, state: AgentState) -> str:
        route = "ask_clarification" if state.get("missing_fields") else "query_context"
        self.record_route(state, "check_info", route, state.get("route_reason", ""))
        return route

    # 高风险或要求审批的动作进入人工审批，低风险直接执行。
    def route_after_diagnose(self, state: AgentState) -> str:
        actions = state.get("proposed_actions", [])
        needs_approval = state.get("risk_level") == "high" or any(
            item.get("approval_required") or item.get("action_name") in HIGH_RISK_ACTIONS for item in actions
        )
        route = "human_approval" if needs_approval else "execute_action"
        reason = "高风险或动作要求审批。" if needs_approval else "低风险动作可自动执行。"
        self.record_route(state, "diagnose", route, reason)
        return route

    # 验证成功则关闭，失败未超阈值则回到查询形成循环，否则升级。
    def route_after_verify(self, state: AgentState) -> str:
        if state.get("verified"):
            route = "close_or_escalate"
            reason = "验证通过，关闭 case。"
        elif int(state.get("retry_count", 0)) < 2:
            route = "query_context"
            reason = "验证失败且未超过重试阈值，回到工具查询形成循环。"
        else:
            route = "close_or_escalate"
            reason = "验证失败超过阈值，升级人工。"
        self.record_route(state, "verify_result", route, reason)
        return route

    # 调用单个 mock 工具，并把入参、结果或异常写入 timeline。
    def call_tool(self, state: AgentState, name: str, fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            result = fn(**kwargs)
            self.storage.add_timeline(
                state["case_id"],
                name,
                "tool_call",
                input_data=kwargs,
                output_data=result,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        except Exception as exc:
            result = {"error": str(exc)}
            self.storage.add_timeline(
                state["case_id"],
                name,
                "error",
                input_data=kwargs,
                output_data=result,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result

    # 记录条件路由的目标节点和原因，便于前端解释 Agent 流转。
    def record_route(self, state: AgentState, node_name: str, route_to: str, reason: str) -> None:
        self.storage.add_timeline(
            state["case_id"],
            node_name,
            "route",
            output_data={"reason": reason},
            route_to=route_to,
        )

    # 生成节点开始时的简短输入摘要，避免 timeline 过大。
    def _input_summary(self, state: AgentState) -> dict[str, Any]:
        return {
            "status": state.get("status"),
            "case_type": state.get("case_type"),
            "resume_mode": state.get("resume_mode"),
            "message": state.get("user_message", "")[:120],
        }

    # 生成节点结束时的简短输出摘要，突出状态、风险和验证结果。
    def _output_summary(self, state: AgentState) -> dict[str, Any]:
        return {
            "status": state.get("status"),
            "case_type": state.get("case_type"),
            "priority": state.get("priority"),
            "missing_fields": state.get("missing_fields", []),
            "risk_level": state.get("risk_level"),
            "approval_status": state.get("approval_status"),
            "verified": state.get("verified"),
        }


# 从候选集合中规范化模型返回值，非法值回退到默认值。
def normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


# 将模型返回值转换为 float，失败时使用默认值。
def safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# 将模型返回的列表字段清理为字符串列表。
def ensure_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


# 规范化模型建议动作，并强制高风险动作标记 approval_required。
def normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action_name = str(item.get("action_name") or "").strip()
        if not action_name:
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        approval_required = bool(item.get("approval_required")) or action_name in HIGH_RISK_ACTIONS
        actions.append(
            {
                "action_name": action_name,
                "target": str(item.get("target") or ""),
                "params": params,
                "reason": str(item.get("reason") or ""),
                "approval_required": approval_required,
            }
        )
    return actions


# 模型分类失败时使用关键词规则兜底，保证 demo 可继续流转。
def apply_rule_classification(state: AgentState) -> None:
    text = full_text(state)
    lower = text.lower()
    if any(word in text for word in ["VPN", "vpn", "连不上", "密码过期", "账号异常"]):
        state.update(
            {
                "case_type": "it_ticket",
                "scenario": "vpn_login",
                "priority": "P3",
                "confidence": 0.91,
                "required_fields": ["user_id", "issue_type", "error_message", "occurred_at", "environment"],
                "route_reason": "LLM 失败后命中 VPN/账号问题关键词，规则兜底为 IT 工单。",
            }
        )
    elif any(word in lower for word in ["cpu", "payment", "service"]) or any(word in text for word in ["支付服务", "告警", "超过"]):
        state.update(
            {
                "case_type": "ops_alert",
                "scenario": "cpu_alert",
                "priority": "P1",
                "confidence": 0.93,
                "required_fields": ["service_name", "metric", "threshold", "duration", "time_range", "environment"],
                "route_reason": "LLM 失败后命中服务指标告警关键词，规则兜底为运维告警。",
            }
        )
    elif any(word in text for word in ["异地登录", "失败多次", "安全", "封禁", "冻结"]):
        state.update(
            {
                "case_type": "security_incident",
                "scenario": "abnormal_login",
                "priority": "P1",
                "confidence": 0.9,
                "required_fields": ["user_id", "event_type", "occurred_at", "failure_count", "location"],
                "route_reason": "LLM 失败后命中异常登录关键词，规则兜底为安全事件。",
            }
        )
    else:
        state.update(
            {
                "case_type": "unknown",
                "scenario": "custom",
                "priority": "P3",
                "confidence": 0.4,
                "required_fields": ["problem_description"],
                "route_reason": "LLM 失败且未命中明确场景，规则兜底为未知类型。",
            }
        )


# 模型抽取失败时使用规则抽取字段，并计算缺失项。
def apply_rule_info_check(state: AgentState) -> None:
    text = full_text(state)
    extracted = dict(state.get("extracted_fields", {}))
    case_type = state.get("case_type", "unknown")

    if case_type == "it_ticket":
        extracted["issue_type"] = "VPN 登录失败"
        if match := re.search(r"(?:账号|用户|user|员工)[：:\s]*([A-Za-z0-9_.-]+)", text, re.I):
            extracted["user_id"] = match.group(1)
        elif "zhangsan" in text.lower():
            extracted["user_id"] = "zhangsan"
        if "密码过期" in text:
            extracted["error_message"] = "密码过期"
        elif "账号异常" in text:
            extracted["error_message"] = "账号异常"
        elif "连不上" in text:
            extracted.setdefault("error_message", "")
        if "今天" in text:
            extracted["occurred_at"] = "今天"
        if any(word in text for word in ["公司网络", "家庭网络", "Wi-Fi", "wifi", "Mac", "Windows"]):
            extracted["environment"] = "用户补充的终端/网络环境"
        elif extracted.get("user_id") and extracted.get("error_message") == "密码过期":
            extracted["environment"] = "默认远程办公网络"
    elif case_type == "ops_alert":
        if "支付" in text:
            extracted["service_name"] = "支付服务"
        elif match := re.search(r"([A-Za-z0-9_-]+)\s*(?:服务|service)", text, re.I):
            extracted["service_name"] = match.group(1)
        if "CPU" in text.upper():
            extracted["metric"] = "CPU"
        if match := re.search(r"超过\s*([0-9]+%?)", text):
            extracted["threshold"] = match.group(1)
        if match := re.search(r"连续\s*([0-9]+\s*分钟)", text):
            extracted["duration"] = match.group(1)
        extracted.setdefault("time_range", "当前告警窗口")
        if any(word in text for word in ["生产", "prod", "线上"]):
            extracted["environment"] = "production"
        elif extracted.get("service_name") and extracted.get("metric"):
            extracted["environment"] = "production"
    elif case_type == "security_incident":
        if match := re.search(r"(?:账号|用户|员工)[：:\s]*([A-Za-z0-9_.-]+)", text, re.I):
            extracted["user_id"] = match.group(1)
        else:
            extracted["user_id"] = "unknown_employee"
        extracted["event_type"] = "异常登录"
        if "凌晨" in text:
            extracted["occurred_at"] = "凌晨"
        if "多次" in text:
            extracted["failure_count"] = "多次"
        if "异地" in text:
            extracted["location"] = "异地"
    elif text.strip():
        extracted["problem_description"] = text.strip()

    missing = [field for field in state.get("required_fields", []) if not extracted.get(field)]
    state["extracted_fields"] = extracted
    state["missing_fields"] = missing
    state["route_reason"] = f"规则兜底判断缺少 {', '.join(missing)}，进入追问。" if missing else "规则兜底判断信息完整，进入工具查询。"


# 模型诊断失败时使用固定策略兜底，仍保留审批和硬校验。
def apply_rule_diagnosis(state: AgentState) -> None:
    case_type = state.get("case_type")
    fields = state.get("extracted_fields", {})
    tool_results = state.get("tool_results", {})
    if case_type == "ops_alert":
        action = {
            "action_name": "restart_service_mock",
            "target": fields.get("service_name", "payment-api"),
            "params": {"instances": ["payment-api-01"]},
            "reason": "CPU 持续超过阈值，日志显示线程池耗尽；建议审批后重启单实例。",
            "approval_required": True,
        }
        state["risk_level"] = "high"
        summary = "疑似最近发布引发资源异常，建议通知负责人并审批后执行模拟重启。"
    elif case_type == "security_incident":
        action = {
            "action_name": "reset_password_mock",
            "target": fields.get("user_id", "unknown_employee"),
            "params": {"notify_user": True},
            "reason": "异地登录失败多次，涉及账号安全，需审批后模拟重置密码。",
            "approval_required": True,
        }
        state["risk_level"] = "high"
        summary = "疑似账号遭遇撞库或暴力破解，建议审批后执行账号保护动作。"
    elif case_type == "it_ticket":
        action = {
            "action_name": "create_ticket",
            "target": fields.get("user_id", "unknown_user"),
            "params": {
                "title": "VPN 登录问题",
                "priority": state.get("priority", "P3"),
                "assignee": "IT 服务台",
                "description": fields.get("error_message", "VPN 登录失败"),
            },
            "reason": "低风险，创建 mock 工单并发送自助处理指引。",
            "approval_required": False,
        }
        state["risk_level"] = "low"
        summary = "账号登录问题可先按 SOP 自助处理，并创建服务台记录跟进。"
    else:
        action = {
            "action_name": "create_ticket",
            "target": "manual_triage",
            "params": {"title": "未知问题人工分诊", "priority": "P3", "assignee": "服务台"},
            "reason": "类型不明确，升级人工分诊。",
            "approval_required": False,
        }
        state["risk_level"] = "medium"
        summary = "问题类型不明确，建议创建人工分诊工单。"

    if "验证失败" in full_text(state):
        action["params"]["force_verify_fail"] = True

    state["proposed_actions"] = [action]
    state["diagnosis"] = {
        "hypotheses": [{"cause": summary, "evidence": list(tool_results.keys()), "confidence": 0.78}],
        "risk_level": state.get("risk_level"),
        "proposed_actions": state["proposed_actions"],
        "user_facing_summary": summary,
    }


# 创建新 case 的初始 AgentState，作为 LangGraph 的输入状态。
def new_state(message: str, scenario: str = "custom") -> AgentState:
    case_id = "CASE-" + uuid.uuid4().hex[:8].upper()
    now = utc_now()
    return {
        "case_id": case_id,
        "title": message[:36],
        "created_at": now,
        "updated_at": now,
        "messages": [{"role": "user", "content": message, "created_at": now}],
        "user_message": message,
        "resume_mode": "new",
        "case_type": "unknown",
        "scenario": scenario,
        "priority": "P3",
        "confidence": 0.0,
        "required_fields": [],
        "extracted_fields": {},
        "missing_fields": [],
        "pending_question": "",
        "tool_results": {},
        "diagnosis": {},
        "risk_level": "unknown",
        "proposed_actions": [],
        "approval_status": "none",
        "approved_actions": [],
        "action_results": [],
        "verified": False,
        "verify_notes": "",
        "retry_count": 0,
        "status": "open",
    }


# 在等待用户补充时追加一条用户消息，并清理追问状态。
def append_user_message(state: AgentState, message: str) -> AgentState:
    state["messages"] = list(state.get("messages", [])) + [
        {"role": "user", "content": message, "created_at": utc_now()}
    ]
    state["user_message"] = message
    state["resume_mode"] = "user_message"
    state["status"] = "open"
    state["pending_question"] = ""
    return state


# 汇总当前 case 的所有消息文本，供规则分类和字段抽取使用。
def full_text(state: AgentState) -> str:
    return "\n".join(str(item.get("content", "")) for item in state.get("messages", []))


# 根据分类结果生成页面列表和报告中使用的标题。
def build_title(state: AgentState) -> str:
    if state.get("case_type") == "ops_alert":
        return f"{state.get('extracted_fields', {}).get('service_name', '服务')} CPU 告警"
    if state.get("case_type") == "security_incident":
        return "异常登录安全事件"
    if state.get("case_type") == "it_ticket":
        return "VPN 登录工单"
    return state.get("user_message", "未知问题")[:36]
