from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["APP_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "app.db")

from main import app
from src.llm import CHECK_INFO_PROMPT, CLASSIFY_PROMPT, DIAGNOSE_PROMPT, LLMService


client = TestClient(app)


# 用确定性假 LLM 响应替代真实模型调用，让自测不消耗外部 API。
def fake_invoke_json(self: LLMService, system_prompt: str, user_payload: dict, max_tokens: int = 900) -> dict:
    messages = "\n".join(item.get("content", "") for item in user_payload.get("messages", []))
    if system_prompt == CLASSIFY_PROMPT:
        if "VPN" in messages or "vpn" in messages or "连不上" in messages:
            return {
                "case_type": "it_ticket",
                "scenario": "vpn_login",
                "priority": "P3",
                "confidence": 0.91,
                "required_fields": ["user_id", "issue_type", "error_message", "occurred_at", "environment"],
                "reason": "模型识别为 VPN 登录工单。",
            }
        if "CPU" in messages or "支付服务" in messages:
            return {
                "case_type": "ops_alert",
                "scenario": "cpu_alert",
                "priority": "P1",
                "confidence": 0.93,
                "required_fields": ["service_name", "metric", "threshold", "duration", "time_range", "environment"],
                "reason": "模型识别为 CPU 运维告警。",
            }
        return {
            "case_type": "security_incident",
            "scenario": "abnormal_login",
            "priority": "P1",
            "confidence": 0.9,
            "required_fields": ["user_id", "event_type", "occurred_at", "failure_count", "location"],
            "reason": "模型识别为异常登录安全事件。",
        }

    if system_prompt == CHECK_INFO_PROMPT:
        case_type = user_payload.get("case_type")
        if case_type == "it_ticket" and "账号 zhangsan" not in messages:
            return {
                "extracted_fields": {"issue_type": "VPN 登录失败"},
                "missing_fields": ["user_id", "error_message", "occurred_at", "environment"],
                "pending_question": "请补充账号、错误提示、发生时间和网络环境。",
            }
        if case_type == "it_ticket":
            return {
                "extracted_fields": {
                    "issue_type": "VPN 登录失败",
                    "user_id": "zhangsan",
                    "error_message": "密码过期",
                    "occurred_at": "今天",
                    "environment": "家庭 Wi-Fi",
                },
                "missing_fields": [],
                "pending_question": "",
            }
        if case_type == "ops_alert":
            return {
                "extracted_fields": {
                    "service_name": "支付服务",
                    "metric": "CPU",
                    "threshold": "90%",
                    "duration": "10 分钟",
                    "time_range": "当前告警窗口",
                    "environment": "production",
                },
                "missing_fields": [],
                "pending_question": "",
            }
        return {
            "extracted_fields": {
                "user_id": "unknown_employee",
                "event_type": "异常登录",
                "occurred_at": "凌晨",
                "failure_count": "多次",
                "location": "异地",
            },
            "missing_fields": [],
            "pending_question": "",
        }

    if system_prompt == DIAGNOSE_PROMPT:
        case_type = user_payload.get("case_type")
        fields = user_payload.get("extracted_fields", {})
        if case_type == "ops_alert":
            return {
                "hypotheses": [{"cause": "CPU 持续过高，疑似资源异常。", "evidence": ["mock_log_search"], "confidence": 0.8}],
                "risk_level": "high",
                "proposed_actions": [
                    {
                        "action_name": "restart_service_mock",
                        "target": fields.get("service_name", "支付服务"),
                        "params": {"instances": ["payment-api-01"]},
                        "reason": "高 CPU 需要模拟重启单实例。",
                        "approval_required": True,
                    }
                ],
                "user_facing_summary": "建议审批后执行模拟重启。",
            }
        if case_type == "security_incident":
            return {
                "hypotheses": [{"cause": "异地失败登录多次，疑似账号风险。", "evidence": ["mock_history_search"], "confidence": 0.82}],
                "risk_level": "high",
                "proposed_actions": [
                    {
                        "action_name": "reset_password_mock",
                        "target": fields.get("user_id", "unknown_employee"),
                        "params": {"notify_user": True},
                        "reason": "账号安全动作需审批。",
                        "approval_required": True,
                    }
                ],
                "user_facing_summary": "建议审批后执行账号保护动作。",
            }
        return {
            "hypotheses": [{"cause": "VPN 密码过期。", "evidence": ["mock_sop_search"], "confidence": 0.86}],
            "risk_level": "low",
            "proposed_actions": [
                {
                    "action_name": "create_ticket",
                    "target": fields.get("user_id", "zhangsan"),
                    "params": {"title": "VPN 登录问题", "priority": "P3", "assignee": "IT 服务台"},
                    "reason": "低风险创建服务台记录。",
                    "approval_required": False,
                }
            ],
            "user_facing_summary": "创建 mock 工单并给出自助指引。",
        }

    raise AssertionError("unexpected prompt")


LLMService.invoke_json = fake_invoke_json


# 封装 POST 请求并断言成功，减少测试用例重复代码。
def post(path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# 按验收清单跑通模型门禁、追问、审批、执行、报告和时间线。
def main() -> None:
    health = client.get("/api/health")
    assert health.status_code == 200

    configs = client.get("/api/model-configs").json()
    assert configs == []

    missing_model = client.post(
        "/api/cases",
        json={"message": "支付服务 CPU 连续 10 分钟超过 90%。", "scenario": "cpu_alert"},
    )
    assert missing_model.status_code == 400
    assert "未配置并启用真实模型" in missing_model.text

    created_config = post(
        "/api/model-configs",
        {
            "name": "测试真实模型",
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "model_name": "demo-model",
            "api_key": "test-api-key",
            "temperature": 0.2,
            "timeout_seconds": 30,
        },
    )
    activated = post(f"/api/model-configs/{created_config['id']}/activate", {})
    assert activated["ok"] is True

    vpn = post("/api/cases", {"message": "我连不上 VPN。", "scenario": "vpn_login"})
    assert vpn["state"]["case_type"] == "it_ticket"
    assert vpn["state"]["status"] == "waiting_user"
    assert vpn["state"]["missing_fields"]

    vpn2 = post(
        f"/api/cases/{vpn['case_id']}/message",
        {"message": "账号 zhangsan，提示密码过期，今天在家庭 Wi-Fi 上。"},
    )
    assert vpn2["state"]["status"] == "closed"
    assert vpn2["state"]["verified"] is True
    assert "mock_sop_search" in vpn2["state"]["tool_results"]
    assert vpn2["report"]

    cpu = post("/api/cases", {"message": "支付服务 CPU 连续 10 分钟超过 90%。", "scenario": "cpu_alert"})
    assert cpu["state"]["case_type"] == "ops_alert"
    assert cpu["state"]["status"] == "waiting_approval"
    assert cpu["state"]["risk_level"] == "high"

    rejected = post(
        f"/api/cases/{cpu['case_id']}/approve",
        {"decision": "rejected", "comment": "先人工排查，不允许自动重启。"},
    )
    assert rejected["state"]["status"] == "escalated"
    assert not rejected["state"]["action_results"]
    assert "高风险动作未执行" in rejected["report"]

    cpu2 = post("/api/cases", {"message": "支付服务 CPU 连续 10 分钟超过 90%。", "scenario": "cpu_alert"})
    approved = post(
        f"/api/cases/{cpu2['case_id']}/approve",
        {"decision": "approved", "comment": "批准单实例 mock 重启。"},
    )
    assert approved["state"]["status"] == "closed"
    assert approved["state"]["verified"] is True
    assert approved["state"]["action_results"][0]["action_name"] == "restart_service_mock"

    sec = post("/api/cases", {"message": "某员工账号凌晨从异地登录并失败多次。", "scenario": "abnormal_login"})
    assert sec["state"]["case_type"] == "security_incident"
    assert sec["state"]["status"] == "waiting_approval"

    timeline = client.get(f"/api/cases/{cpu2['case_id']}/timeline").json()
    assert any(item["event_type"] == "tool_call" for item in timeline)
    assert any(item["event_type"] == "route" for item in timeline)
    assert len([item for item in timeline if item["node_name"] == "verify_result"]) >= 1

    print("Smoke test passed: model gating, classification, clarification, tools, approval, execution, report, timeline.")


if __name__ == "__main__":
    main()
