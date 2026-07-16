from __future__ import annotations

import base64
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = Path(os.getenv("APP_DB_PATH", DATA_DIR / "app.db"))


# 返回 UTC ISO 时间字符串，统一用于数据库时间字段。
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 以中文友好的方式序列化 JSON，便于 state 和 timeline 入库。
def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# 从数据库 JSON 字符串恢复对象，空值时返回调用方给定默认值。
def load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class Storage:
    # 初始化 SQLite 存储，并确保业务表已经创建。
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    # 创建 SQLite 连接并启用 Row 映射，便于按字段名读取。
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # 创建 case、timeline、审批、报告和模型配置所需的表。
    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                  case_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  case_type TEXT NOT NULL,
                  scenario TEXT NOT NULL,
                  priority TEXT NOT NULL,
                  status TEXT NOT NULL,
                  state_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS timeline_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  case_id TEXT NOT NULL,
                  node_name TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  input_json TEXT,
                  output_json TEXT,
                  route_to TEXT,
                  duration_ms INTEGER DEFAULT 0,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approvals (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  case_id TEXT NOT NULL,
                  decision TEXT NOT NULL,
                  original_actions_json TEXT NOT NULL,
                  modified_actions_json TEXT,
                  comment TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reports (
                  case_id TEXT PRIMARY KEY,
                  report_markdown TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_configs (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  base_url TEXT,
                  model_name TEXT NOT NULL,
                  api_key_encrypted TEXT,
                  temperature REAL NOT NULL,
                  timeout_seconds INTEGER NOT NULL,
                  is_active INTEGER NOT NULL DEFAULT 0,
                  last_test_status TEXT NOT NULL DEFAULT 'unknown',
                  last_test_message TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
        self.disable_mock_model_configs()

    # 启动时禁用历史遗留 mock 模型，避免误当作真实模型使用。
    def disable_mock_model_configs(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE model_configs
                SET is_active=0,
                    last_test_status='failed',
                    last_test_message='mock 模型不能作为真实模型使用，请配置真实模型',
                    updated_at=?
                WHERE provider='mock'
                """,
                (utc_now(),),
            )

    # 新建或更新 case 主记录，并持久化完整 AgentState。
    def save_case(self, state: dict[str, Any]) -> None:
        now = utc_now()
        state["updated_at"] = now
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM cases WHERE case_id = ?", (state["case_id"],)
            ).fetchone()
            values = (
                state["case_id"],
                state.get("title") or state.get("user_message", "")[:36] or "未命名 Case",
                state.get("case_type", "unknown"),
                state.get("scenario", "custom"),
                state.get("priority", "P3"),
                state.get("status", "open"),
                dump_json(state),
                state.get("created_at", now),
                now,
            )
            if exists:
                conn.execute(
                    """
                    UPDATE cases
                    SET title=?, case_type=?, scenario=?, priority=?, status=?,
                        state_json=?, created_at=?, updated_at=?
                    WHERE case_id=?
                    """,
                    values[1:] + (state["case_id"],),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO cases (
                      case_id, title, case_type, scenario, priority, status,
                      state_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )

    # 查询 case 主记录，不解析 state_json。
    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return dict(row) if row else None

    # 查询并解析 case 的完整 AgentState。
    def get_case_state(self, case_id: str) -> dict[str, Any] | None:
        row = self.get_case(case_id)
        return load_json(row["state_json"], {}) if row else None

    # 查询最近 case 列表，供前端侧边栏展示。
    def list_cases(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT case_id, title, case_type, scenario, priority, status,
                       created_at, updated_at
                FROM cases
                ORDER BY updated_at DESC
                LIMIT 100
                """
            ).fetchall()
        return [dict(row) for row in rows]

    # 写入一条 timeline 事件，记录节点、工具、路由、审批或异常。
    def add_timeline(
        self,
        case_id: str,
        node_name: str,
        event_type: str,
        input_data: Any | None = None,
        output_data: Any | None = None,
        route_to: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO timeline_events (
                    case_id, node_name, event_type, input_json, output_json,
                    route_to, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    node_name,
                    event_type,
                    dump_json(input_data) if input_data is not None else None,
                    dump_json(output_data) if output_data is not None else None,
                    route_to,
                    duration_ms,
                    utc_now(),
                ),
            )

    # 读取 case 的完整 timeline，并把 JSON 字段还原成对象。
    def get_timeline(self, case_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, case_id, node_name, event_type, input_json, output_json,
                       route_to, duration_ms, created_at
                FROM timeline_events
                WHERE case_id=?
                ORDER BY id ASC
                """,
                (case_id,),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["input"] = load_json(event.pop("input_json"), None)
            event["output"] = load_json(event.pop("output_json"), None)
            events.append(event)
        return events

    # 保存审批人的决策、原始动作、修改动作和审批意见。
    def add_approval(
        self,
        case_id: str,
        decision: str,
        original_actions: list[dict[str, Any]],
        modified_actions: list[dict[str, Any]] | None,
        comment: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    case_id, decision, original_actions_json, modified_actions_json,
                    comment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    decision,
                    dump_json(original_actions),
                    dump_json(modified_actions) if modified_actions else None,
                    comment,
                    utc_now(),
                ),
            )

    # 保存或覆盖最终 Markdown 报告。
    def save_report(self, case_id: str, report: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reports (case_id, report_markdown, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE
                SET report_markdown=excluded.report_markdown, created_at=excluded.created_at
                """,
                (case_id, report, utc_now()),
            )

    # 获取指定 case 的最终报告文本。
    def get_report(self, case_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT report_markdown FROM reports WHERE case_id = ?", (case_id,)
            ).fetchone()
        return row["report_markdown"] if row else None

    # 对 API Key 做本地轻量混淆，避免明文入库。
    def encrypt_key(self, api_key: str) -> str:
        if not api_key:
            return ""
        secret = os.getenv("CONFIG_SECRET", "demo-local-secret")
        raw = "".join(chr(ord(ch) ^ ord(secret[i % len(secret)])) for i, ch in enumerate(api_key))
        return base64.urlsafe_b64encode(raw.encode()).decode()

    # 还原本地混淆后的 API Key，仅后端测试模型时使用。
    def decrypt_key(self, encrypted: str | None) -> str:
        if not encrypted:
            return ""
        secret = os.getenv("CONFIG_SECRET", "demo-local-secret")
        raw = base64.urlsafe_b64decode(encrypted.encode()).decode()
        return "".join(chr(ord(ch) ^ ord(secret[i % len(secret)])) for i, ch in enumerate(raw))

    # 生成前端可展示的 API Key 脱敏值。
    def mask_key(self, encrypted: str | None) -> str:
        if not encrypted:
            return ""
        return "sk-****" + encrypted[-4:]

    # 返回模型配置列表，并移除 API Key 密文。
    def list_model_configs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, provider, base_url, model_name, api_key_encrypted,
                       temperature, timeout_seconds, is_active, last_test_status,
                       last_test_message, created_at, updated_at
                FROM model_configs
                ORDER BY is_active DESC, updated_at DESC
                """
            ).fetchall()
        configs = []
        for row in rows:
            item = dict(row)
            item["api_key_masked"] = self.mask_key(item.pop("api_key_encrypted"))
            item["is_active"] = bool(item["is_active"])
            configs.append(item)
        return configs

    # 获取当前启用的模型配置，包含后端内部字段。
    def get_active_model_config(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM model_configs WHERE is_active=1 LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # 按 ID 获取模型配置，供测试接口读取密文 API Key。
    def get_model_config(self, config_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM model_configs WHERE id=?", (config_id,)).fetchone()
        return dict(row) if row else None

    # 获取当前启用且满足“真实模型”条件的配置。
    def get_active_real_model_config(self) -> dict[str, Any] | None:
        config = self.get_active_model_config()
        if not config:
            return None
        if config.get("provider") == "mock":
            return None
        if not config.get("model_name") or not config.get("api_key_encrypted"):
            return None
        return config

    # 获取当前启用真实模型并解密 API Key，供 LangGraph 节点调用 LLM。
    def get_active_real_model_config_with_secret(self) -> dict[str, Any] | None:
        config = self.get_active_real_model_config()
        if not config:
            return None
        config["api_key"] = self.decrypt_key(config.get("api_key_encrypted"))
        return config

    # 新增模型配置，默认不启用，等待用户显式启用。
    def add_model_config(self, data: dict[str, Any]) -> dict[str, Any]:
        config_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO model_configs (
                  id, name, provider, base_url, model_name, api_key_encrypted,
                  temperature, timeout_seconds, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    config_id,
                    data["name"],
                    data.get("provider", "openai_compatible"),
                    data.get("base_url", ""),
                    data["model_name"],
                    self.encrypt_key(data.get("api_key", "")),
                    data.get("temperature", 0.2),
                    data.get("timeout_seconds", 30),
                    now,
                    now,
                ),
            )
        return {"id": config_id}

    # 更新模型配置的可编辑字段，API Key 传入时重新混淆保存。
    def update_model_config(self, config_id: str, data: dict[str, Any]) -> bool:
        allowed = {
            "name",
            "provider",
            "base_url",
            "model_name",
            "temperature",
            "timeout_seconds",
        }
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if "api_key" in data and data["api_key"] is not None:
            updates["api_key_encrypted"] = self.encrypt_key(data["api_key"])
        if not updates:
            return True
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE model_configs SET {assignments} WHERE id=?",
                tuple(updates.values()) + (config_id,),
            )
        return cur.rowcount > 0

    # 激活指定模型配置，并取消其他配置的启用状态。
    def activate_model_config(self, config_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM model_configs WHERE id=?", (config_id,)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE model_configs SET is_active=0")
            conn.execute(
                "UPDATE model_configs SET is_active=1, updated_at=? WHERE id=?",
                (utc_now(), config_id),
            )
        return True

    # 删除未启用的模型配置，启用中的配置需要先切换。
    def delete_model_config(self, config_id: str) -> tuple[bool, str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT is_active FROM model_configs WHERE id=?", (config_id,)
            ).fetchone()
            if not row:
                return False, "模型配置不存在"
            if row["is_active"]:
                return False, "当前启用的模型配置不能删除"
            conn.execute("DELETE FROM model_configs WHERE id=?", (config_id,))
        return True, "已删除"

    # 记录最近一次模型连通性测试的结果和提示信息。
    def record_model_test(self, config_id: str, status: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE model_configs
                SET last_test_status=?, last_test_message=?, updated_at=?
                WHERE id=?
                """,
                (status, message, utc_now(), config_id),
            )
