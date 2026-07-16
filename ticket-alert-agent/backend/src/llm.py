from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .storage import Storage


class LLMService:
    # 保存存储服务引用，运行时从当前启用模型配置创建 ChatOpenAI。
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    # 使用当前启用的 OpenAI-compatible 配置创建 LangChain Chat Model。
    def chat_model(self, temperature: float | None = None, max_tokens: int = 900) -> ChatOpenAI:
        config = self.storage.get_active_real_model_config_with_secret()
        if not config:
            raise RuntimeError("未启用真实模型配置")
        return ChatOpenAI(
            model=config["model_name"],
            api_key=config["api_key"],
            base_url=config.get("base_url") or None,
            temperature=config.get("temperature", 0.2) if temperature is None else temperature,
            max_tokens=max_tokens,
            timeout=config.get("timeout_seconds") or 30,
            max_retries=1,
        )

    # 调用模型并要求返回 JSON，对常见 Markdown 代码块做清理后解析。
    def invoke_json(self, system_prompt: str, user_payload: dict[str, Any], max_tokens: int = 900) -> dict[str, Any]:
        llm = self.chat_model(temperature=0, max_tokens=max_tokens)
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
            ]
        )
        return parse_json_response(str(response.content))


# 从模型输出中解析 JSON；兼容 ```json ... ``` 和前后多余说明文字。
def parse_json_response(content: str) -> dict[str, Any]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return parsed


# 分类节点 prompt：要求模型输出可路由的结构化字段。
CLASSIFY_PROMPT = """
你是企业 ITSM/AIOps/SOC 工单告警分类器。只能输出 JSON 对象，不要输出 Markdown。
根据用户消息判断 case_type、scenario、priority、confidence、required_fields 和 reason。
case_type 只能是 it_ticket、ops_alert、security_incident、unknown。
scenario 优先使用 vpn_login、cpu_alert、abnormal_login、custom。
priority 只能是 P0、P1、P2、P3。
required_fields 必须是后续处置所需的字段名数组。
"""


# 信息检查节点 prompt：要求模型抽取字段并列出缺失项。
CHECK_INFO_PROMPT = """
你是工单信息抽取器。只能输出 JSON 对象，不要输出 Markdown。
根据所有用户消息、case_type 和 required_fields，输出 extracted_fields、missing_fields、pending_question。
missing_fields 只能包含 required_fields 中仍然缺失的字段。
pending_question 用中文提出一次性追问；如果 missing_fields 为空则返回空字符串。
不要编造明确账号、IP、错误码等用户未提供的信息；环境和时间范围可基于告警常识保守推断。
"""


# 诊断节点 prompt：要求模型基于 mock 工具证据生成诊断和动作。
DIAGNOSE_PROMPT = """
你是工单/告警处置 Agent 的诊断节点。只能输出 JSON 对象，不要输出 Markdown。
必须基于用户输入、字段抽取结果和 mock 工具结果给出 hypotheses、risk_level、proposed_actions、user_facing_summary。
risk_level 只能是 low、medium、high。
proposed_actions 是数组，每项包含 action_name、target、params、reason、approval_required。
允许动作名：create_ticket、send_notification、reset_password_mock、restart_service_mock、rollback_release_mock、block_ip_mock、unlock_account_mock。
高风险动作 reset_password_mock、restart_service_mock、rollback_release_mock、block_ip_mock、unlock_account_mock 必须 approval_required=true。
不能声称执行了真实生产动作；所有动作都是 mock 建议。
"""
