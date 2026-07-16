from __future__ import annotations

import hashlib
from typing import Any


HIGH_RISK_ACTIONS = {
    "reset_password_mock",
    "restart_service_mock",
    "rollback_release_mock",
    "block_ip_mock",
    "unlock_account_mock",
}


# 基于输入内容生成稳定的 mock ID，便于演示结果可重复。
def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:8].upper()
    return f"{prefix}-{digest}"


# 模拟 CMDB 查询，返回服务或账号的负责人、等级和最近变更。
def mock_cmdb_lookup(**kwargs: Any) -> dict[str, Any]:
    service_name = kwargs.get("service_name") or kwargs.get("target") or ""
    user_id = kwargs.get("user_id") or ""
    if service_name:
        is_payment = "支付" in service_name or "payment" in service_name.lower()
        return {
            "kind": "service",
            "target": service_name,
            "owner": "支付平台值班组" if is_payment else "平台运维组",
            "asset_level": "production_core" if is_payment else "standard",
            "dependencies": ["user-center", "order-api"] if is_payment else ["vpn-gateway"],
            "recent_changes": [
                {"change_id": "CHG-20260716-021", "summary": "20 分钟前灰度发布 v2.8.4"}
            ]
            if is_payment
            else [],
        }
    return {
        "kind": "user",
        "target": user_id or "unknown_employee",
        "owner": "IT 服务台",
        "asset_level": "account",
        "account_status": "locked" if user_id else "needs_lookup",
        "recent_changes": [],
    }


# 模拟日志检索，返回异常摘要、峰值标记和 trace 信息。
def mock_log_search(**kwargs: Any) -> dict[str, Any]:
    service_name = kwargs.get("service_name", "unknown-service")
    keyword = kwargs.get("keyword", "error")
    return {
        "service_name": service_name,
        "time_range": kwargs.get("time_range", "当前告警窗口"),
        "keyword": keyword,
        "error_summary": "线程池耗尽与数据库连接等待升高" if "CPU" in keyword.upper() else "账号校验失败多次",
        "spike": True,
        "trace_ids": ["trace-8f3a21", "trace-c7d901"],
        "sample_logs": [
            "mock log: worker queue saturation",
            "mock log: retry storm detected",
        ],
    }


# 模拟 SOP 检索，按场景返回处置步骤和审批要求。
def mock_sop_search(**kwargs: Any) -> dict[str, Any]:
    case_type = kwargs.get("case_type", "unknown")
    symptom = kwargs.get("symptom", "")
    if case_type == "ops_alert":
        return {
            "sop_id": "SOP-OPS-CPU-001",
            "steps": ["确认影响范围", "通知服务负责人", "审批后重启单实例或回滚发布"],
            "risk_tips": ["生产服务重启和回滚必须审批"],
            "approval_required_actions": ["restart_service_mock", "rollback_release_mock"],
        }
    if case_type == "security_incident":
        return {
            "sop_id": "SOP-SEC-LOGIN-002",
            "steps": ["核对登录地点", "评估暴力破解风险", "审批后冻结或重置账号"],
            "risk_tips": ["账号冻结、封禁 IP、重置密码均需审批"],
            "approval_required_actions": ["reset_password_mock", "block_ip_mock", "unlock_account_mock"],
        }
    return {
        "sop_id": "SOP-IT-VPN-001",
        "steps": ["确认账号", "检查错误提示", "引导自助密码更新", "必要时创建服务台工单"],
        "risk_tips": ["自助指引和创建工单为低风险动作"],
        "approval_required_actions": [],
        "symptom": symptom,
    }


# 模拟历史工单检索，返回相似 case 和历史处置方式。
def mock_history_search(**kwargs: Any) -> dict[str, Any]:
    return {
        "matches": [
            {
                "case_id": stable_id("HIST", kwargs.get("case_type", ""), kwargs.get("target", "")),
                "summary": "相似问题通过 SOP 指引和通知负责人闭环",
                "resolution": "create_ticket + send_notification",
                "success": True,
            }
        ],
        "similarity": 0.82,
    }


# 模拟风险评分，判断动作是否属于高风险并需要审批。
def mock_risk_score(**kwargs: Any) -> dict[str, Any]:
    action = kwargs.get("action", "")
    asset_level = kwargs.get("asset_level", "")
    high = action in HIGH_RISK_ACTIONS or asset_level == "production_core"
    return {
        "risk_level": "high" if high else "low",
        "reason": "动作会影响生产核心系统或账号安全" if high else "仅创建记录或发送内部通知",
        "approval_required": high,
    }


# 模拟创建 ITSM 工单，返回 mock 工单号和链接。
def mock_ticket_create(**kwargs: Any) -> dict[str, Any]:
    title = kwargs.get("title", "AI Agent mock 工单")
    return {
        "ticket_id": stable_id("TCK", title, kwargs.get("priority", "P3")),
        "url": "mock://itsm/tickets/" + stable_id("TCK", title, kwargs.get("priority", "P3")),
        "status": "created",
        "mock": True,
    }


# 模拟发送内部通知，返回 mock 消息 ID。
def mock_notify(**kwargs: Any) -> dict[str, Any]:
    return {
        "sent": True,
        "channel": "mock-im",
        "receiver": kwargs.get("receiver", "oncall"),
        "message_id": stable_id("MSG", kwargs.get("receiver", ""), kwargs.get("message", "")),
        "mock": True,
    }


# 模拟执行变更动作，并对高风险动作做审批状态硬校验。
def mock_execute_action(
    action_name: str,
    target: str,
    params: dict[str, Any],
    approval_status: str,
) -> dict[str, Any]:
    if action_name in HIGH_RISK_ACTIONS and approval_status not in {"approved", "modified"}:
        return {
            "action_name": action_name,
            "target": target,
            "success": False,
            "blocked": True,
            "message": "高风险 mock 动作未获得人工审批，已被执行层硬校验拦截。",
            "verify_hint": "blocked_by_policy",
            "mock": True,
        }
    force_fail = bool(params.get("force_verify_fail"))
    return {
        "action_name": action_name,
        "target": target,
        "success": True,
        "blocked": False,
        "message": f"已模拟执行 {action_name}，未连接真实生产系统。",
        "verify_hint": "force_fail" if force_fail else "mock_success",
        "mock": True,
    }
