from __future__ import annotations

from typing import Any


# 将最终 AgentState 渲染成 Markdown 处置报告。
def render_report(state: dict[str, Any]) -> str:
    extracted = state.get("extracted_fields", {})
    tool_results = state.get("tool_results", {})
    diagnosis = state.get("diagnosis", {})
    actions = state.get("action_results", [])
    status = state.get("status", "unknown")

    evidence_lines: list[str] = []
    for name, result in tool_results.items():
        evidence_lines.append(f"- `{name}`: {summarize(result)}")
    if not evidence_lines:
        evidence_lines.append("- 未产生工具证据。")

    action_lines = [
        f"- `{item.get('action_name')}` -> {item.get('message')}，mock={item.get('mock')}"
        for item in actions
    ]
    if not action_lines:
        action_lines.append("- 未执行动作。")

    approval = state.get("approval_status", "none")
    if approval == "rejected":
        approval_text = f"审批拒绝：{state.get('approval_comment', '')}。高风险动作未执行。"
    elif approval in {"approved", "modified"}:
        approval_text = f"审批状态：{approval}，意见：{state.get('approval_comment', '')}"
    else:
        approval_text = "无需审批或未进入审批。"

    return "\n".join(
        [
            "## 处置摘要",
            f"- Case：`{state.get('case_id')}`",
            f"- 标题：{state.get('title', '')}",
            f"- 最终状态：`{status}`",
            "",
            "## 分类与优先级",
            f"- 类型：`{state.get('case_type', 'unknown')}`",
            f"- 场景：`{state.get('scenario', 'custom')}`",
            f"- 优先级：`{state.get('priority', 'P3')}`",
            f"- 分类置信度：{state.get('confidence', 0)}",
            "",
            "## 关键信息",
            *[f"- {key}: {value}" for key, value in extracted.items()],
            "",
            "## 工具查询证据",
            *evidence_lines,
            "",
            "## 诊断结论",
            f"- 风险等级：`{state.get('risk_level', 'unknown')}`",
            f"- 摘要：{diagnosis.get('user_facing_summary', '无')}",
            "",
            "## 审批记录",
            f"- {approval_text}",
            "",
            "## 执行动作",
            *action_lines,
            "",
            "## 验证结果",
            f"- verified：`{state.get('verified', False)}`",
            f"- 说明：{state.get('verify_notes', '')}",
            "",
            "## 最终状态与后续建议",
            "- 所有查询和动作均为本地 mock，未连接真实生产系统。",
            "- 如需生产落地，应接入正式审批、审计、权限和变更系统。",
        ]
    )


# 为报告中的工具结果生成一行可读摘要。
def summarize(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("error_summary", "reason", "sop_id", "ticket_id", "message"):
            if key in value:
                return str(value[key])
        return ", ".join(f"{k}={v}" for k, v in list(value.items())[:3])
    if isinstance(value, list):
        return f"{len(value)} 条记录"
    return str(value)
