# AI 工单/告警智能处置编排 Agent 概要设计

## 1. 文档说明

本文档根据《PRD_题目二_LangGraph_AI工单告警智能处置编排Agent.docx》整理，用于指导两天 AI 大赛 MVP 版本的设计、开发和演示。

本项目目标不是建设完整生产级 ITSM、AIOps 或安全运营平台，而是实现一个可演示、可解释、可暂停恢复的 LangGraph Agent Demo。重点覆盖 Stateful Agent、条件路由、循环重试、工具调用、人工审批、状态持久化、过程可观测和最终报告生成能力。

## 2. 项目目标与范围

### 2.1 业务目标

- 将工单、运维告警、安全告警从单轮问答升级为有状态处置流程。
- 演示 Agent 如何完成分类、补充信息、查系统、诊断、风险判断、审批、执行、验证和报告闭环。
- 让评委能通过时间线或节点状态看清 Agent 的每一步判断、工具调用和路由原因。
- 形成一套可复用的 LangGraph 工作流样板，后续可扩展到真实 IT 支持、运维和安全响应场景。

### 2.2 MVP 范围

MVP 必须完成：

- 工单/告警自然语言输入和样例选择。
- 使用 LangGraph 显式定义状态图，至少包含分类、信息检查、工具查询、诊断、审批、执行、验证、关闭或升级节点。
- 至少 3 类样例流转：IT 工单、运维告警、安全事件。
- 信息不足时进入追问，不直接执行诊断或动作。
- 至少调用 3 个 mock 工具，并展示工具入参和返回结果。
- 高风险动作前必须暂停并等待人工审批；拒绝审批时不得执行动作。
- 展示执行时间线或节点状态。
- 生成最终处置报告。

MVP 建议完成：

- 刷新页面后恢复 case 当前状态。
- 验证失败后重试诊断或升级人工。
- 审批人可修改动作参数。
- 展示 LangGraph 节点图或 Mermaid 流程图。
- 导出 Markdown 处置报告。

不纳入 MVP：

- 接入真实生产 CMDB、日志、工单、通知、账号或变更系统。
- 执行真实重置密码、重启服务、封禁 IP 等变更动作。
- 生产级权限、审计、审批流、队列和高可用部署。
- 覆盖所有企业 IT、运维和安全流程。

## 3. 待确认事项

以下事项会影响实现方案，建议开发前确认；如未确认，本文档给出默认选择。

| 事项 | 默认选择 | 需要确认的问题 |
| --- | --- | --- |
| 场景策略 | 统一入口，内置 IT 工单、运维告警、安全事件 3 类样例 | 是否只聚焦一个场景做深，还是三个场景都做基础闭环？ |
| Web 技术栈 | FastAPI + React | React 组件库是否有指定要求？ |
| LangGraph 版本 | 使用当前环境可安装的稳定版 LangGraph | 比赛环境是否固定依赖版本？ |
| LLM 来源 | 前端配置多套 OpenAI-compatible 模型连接，可切换当前启用模型 | 比赛现场可用模型服务、模型名和 API Key 是什么？ |
| 结构化输出 | 优先使用 Pydantic schema + JSON 输出修复 | 所选模型是否稳定支持 structured output 或 tool calling？ |
| 持久化 | SQLite 保存业务数据，并接入 LangGraph checkpoint | checkpoint 使用 SQLite 实现还是 Postgres/Redis 等外部存储？ |
| 审批身份 | Demo 中使用页面按钮模拟审批人 | 是否需要区分普通用户和审批人登录？ |
| 图可视化 | Mermaid 静态图 + 当前节点高亮 | 是否要求运行时动态图渲染？ |
| mock 数据 | 本地 JSON + Python 函数 | 是否提供统一样例数据包？ |
| 观测工具 | 本地 timeline 展示 | 是否需要后续再接入 LangSmith 或其他观测平台？ |

## 4. 总体架构

### 4.1 架构视图

```text
用户浏览器
   |
   v
前端应用
   |
   v
FastAPI Backend
   |
   +-- Case API
   |      |
   |      v
   |   创建 case / 补充信息 / 审批 / 查询详情 / 导出报告
   |
   +-- Model Config API
   |      |
   |      v
   |   新增配置 / 测试连接 / 启用配置 / 禁用配置
   |
   +-- LangGraph 编排层
   |      |
   |      +-- receive_input
   |      +-- classify_case
   |      +-- check_info
   |      +-- ask_clarification
   |      +-- query_context
   |      +-- diagnose
   |      +-- human_approval
   |      +-- execute_action
   |      +-- verify_result
   |      +-- close_or_escalate
   |
   +-- LLM 能力层
   |      |
   |      +-- 当前启用模型配置
   |      +-- 分类结构化输出
   |      +-- 诊断结构化输出
   |      +-- 报告 Markdown 生成
   |
   +-- Mock 工具层
   |      |
   |      +-- CMDB 查询
   |      +-- 日志查询
   |      +-- SOP 检索
   |      +-- 风险评分
   |      +-- 工单创建
   |      +-- Mock 动作执行
   |      +-- 通知发送
   |
   +-- 存储与观测层
          |
          v
       SQLite / JSON 文件 / LangGraph Checkpoint
       case_state / checkpoint / timeline / tool_calls / reports / model_configs
```

### 4.2 推荐技术栈

| 分层 | 技术方案 | 说明 |
| --- | --- | --- |
| UI | React | 前端负责 Case 创建、详情、审批、时间线、图展示、报告和模型配置。 |
| API | FastAPI | 提供 case、审批、timeline、report、model config 和 mock 工具调试接口。 |
| 编排 | LangGraph StateGraph | 显式表达节点、条件边、循环、暂停恢复和状态更新。 |
| LLM | LangChain Chat Model 或 OpenAI-compatible SDK | 用于分类、诊断、报告生成；运行时读取当前启用模型配置。 |
| 结构化输出 | Pydantic / TypedDict / JSON parser | 关键节点必须返回可路由的结构化字段。 |
| Mock 工具 | Python 函数 + 本地 JSON 数据 | 模拟 CMDB、日志、SOP、工单、风险评分、通知和动作执行。 |
| 状态存储 | SQLite | 保存 case、state、timeline、tool_call、approval、report，支持刷新恢复。 |
| Checkpoint | LangGraph SQLite checkpointer | 按 `thread_id=case_id` 保存图执行检查点，支撑等待用户和等待审批后的恢复。 |
| 可观测 | 本地 timeline | 页面展示节点、耗时、输入摘要、输出摘要、路由原因、工具调用和异常。 |

### 4.3 部署形态

MVP 采用本地前后端分离部署：

```text
后端：uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
前端：npm run dev
```

目录建议：

```text
ticket-alert-agent/
  backend/
    main.py
    requirements.txt
    src/
      config.py
      schemas.py
      graph.py
      nodes.py
      routing.py
      llm.py
      prompts.py
      tools.py
      storage.py
      checkpoint.py
      timeline.py
      report.py
      model_config.py
  frontend/
    package.json
    src/
      api/
      pages/
      components/
      state/
  README.md
  .env.example
  data/
    mock/
      cmdb.json
      logs.json
      sop.json
      users.json
      history_tickets.json
    app.db
```

## 5. 核心状态设计

### 5.1 Agent State Schema

建议使用 TypedDict 或 Pydantic 定义图状态，避免所有信息都塞进 messages。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| case_id | str | 唯一 case 编号。 |
| created_at | str | 创建时间。 |
| updated_at | str | 最近更新时间。 |
| messages | list[dict] | 用户、Agent、审批人的对话和操作消息。 |
| user_message | str | 当前轮用户输入。 |
| case_type | str | it_ticket / ops_alert / security_incident / unknown。 |
| scenario | str | vpn_login / cpu_alert / abnormal_login / custom。 |
| priority | str | P0 / P1 / P2 / P3。 |
| confidence | float | 分类置信度。 |
| required_fields | list[str] | 当前类型需要的字段。 |
| extracted_fields | dict | 已抽取的账号、服务名、时间范围、错误码等字段。 |
| missing_fields | list[str] | 仍需补充的字段。 |
| pending_question | str | 等待用户回答的问题。 |
| tool_results | dict | 各 mock 工具返回结果。 |
| diagnosis | dict | 原因假设、证据、置信度、建议动作。 |
| risk_level | str | low / medium / high。 |
| proposed_actions | list[dict] | 待执行动作列表。 |
| approval_status | str | none / pending / approved / rejected / modified。 |
| approved_actions | list[dict] | 审批后允许执行的动作。 |
| action_results | list[dict] | 动作执行结果。 |
| verified | bool | 是否验证通过。 |
| verify_notes | str | 验证说明。 |
| retry_count | int | 验证失败后的重试次数。 |
| status | str | open / waiting_user / waiting_approval / executing / closed / escalated / failed。 |
| timeline | list[dict] | 节点执行记录，用于 UI 展示。 |
| final_report | str | 最终 Markdown 报告。 |
| error | dict | 异常信息。 |

### 5.2 Case 状态机

| 状态 | 含义 | 用户可操作 |
| --- | --- | --- |
| open | 已创建，图正在推进。 | 查看进度。 |
| waiting_user | 缺少关键信息，等待用户补充。 | 提交补充信息并继续同一个 case。 |
| waiting_approval | 高风险动作等待审批。 | 批准、拒绝、修改动作并填写审批意见。 |
| executing | 正在执行 mock 动作。 | 查看进度。 |
| closed | 已完成处理并生成报告。 | 查看和导出报告。 |
| escalated | 无法自动闭环，已升级人工。 | 查看升级原因和工单号。 |
| failed | 流程异常。 | 查看错误，允许重试或重新创建。 |

## 6. LangGraph 工作流设计

### 6.1 状态图

```text
START
  |
  v
receive_input
  |
  v
classify_case
  |
  v
check_info
  |-- missing_fields 非空 --> ask_clarification --> END(waiting_user)
  |
  |-- 信息完整
  v
query_context
  |
  v
diagnose
  |-- 高风险 / 需审批 --> human_approval --> END(waiting_approval)
  |
  |-- 低风险 / 可自动执行
  v
execute_action
  |
  v
verify_result
  |-- verified=true --> close_or_escalate --> END(closed)
  |
  |-- verified=false 且 retry_count<2 --> query_context
  |
  |-- verified=false 且 retry_count>=2 --> close_or_escalate --> END(escalated)
```

当用户补充信息时，从保存的 state 恢复，追加用户消息，重新进入 `check_info`。当审批人批准或修改动作时，从保存的 state 恢复，写入审批结果，进入 `execute_action`。当审批人拒绝时，进入 `close_or_escalate`，状态为 `escalated` 或 `closed`。

### 6.2 节点职责

| 节点 | 类型 | 职责 | 关键输出 |
| --- | --- | --- | --- |
| receive_input | 规则节点 | 创建或恢复 case，写入用户输入，初始化状态和 timeline。 | case_id, user_message, status |
| classify_case | LLM 节点 | 判断类型、优先级、置信度和必要字段。 | case_type, priority, confidence, required_fields |
| check_info | 规则 + LLM 节点 | 抽取字段，判断是否缺少账号、服务名、时间范围、错误码、影响范围等信息。 | extracted_fields, missing_fields, next_route |
| ask_clarification | 交互节点 | 生成追问问题并暂停流程。 | pending_question, status=waiting_user |
| query_context | 工具节点 | 按 case 类型调用 CMDB、日志、SOP、历史工单、登录记录等工具。 | tool_results |
| diagnose | LLM 节点 | 基于上下文生成原因假设、证据、风险等级和建议动作。 | diagnosis, risk_level, proposed_actions |
| human_approval | 人工节点 | 对高风险动作暂停，等待批准、拒绝或修改。 | approval_status=pending, status=waiting_approval |
| execute_action | 工具节点 | 执行 mock 动作，如创建工单、发送通知、重置密码、重启服务、封禁 IP。 | action_results, status=executing |
| verify_result | 工具 + LLM 节点 | 根据执行结果和 verify_hint 检查是否解决问题。 | verified, verify_notes, retry_count |
| close_or_escalate | 结束节点 | 生成处置报告，关闭或升级人工。 | final_report, status |

### 6.3 条件路由

| 来源节点 | 条件 | 目标节点 |
| --- | --- | --- |
| check_info | missing_fields 非空 | ask_clarification |
| check_info | 信息完整 | query_context |
| diagnose | risk_level=high 或 proposed_actions 含高风险动作 | human_approval |
| diagnose | risk_level=low/medium 且动作安全 | execute_action |
| human_approval | approved 或 modified | execute_action |
| human_approval | rejected | close_or_escalate |
| verify_result | verified=true | close_or_escalate |
| verify_result | verified=false 且 retry_count < 2 | query_context |
| verify_result | verified=false 且 retry_count >= 2 | close_or_escalate |
| 任意节点 | 工具或模型异常且不可恢复 | close_or_escalate |

### 6.4 暂停与恢复策略

- `ask_clarification` 不继续执行后续节点，保存 `status=waiting_user`、`pending_question` 和完整 state。
- 用户补充信息后调用 `continue_case(case_id, message)`，追加消息并从 `check_info` 继续。
- `human_approval` 不执行动作，保存 `status=waiting_approval`、`approval_status=pending` 和建议动作。
- 审批通过后调用 `approve_case(case_id, decision, comment, modified_actions)`，从 `execute_action` 继续。
- 审批拒绝时直接进入 `close_or_escalate`，报告中说明拒绝原因和未执行动作。
- 所有暂停点必须写入 timeline，便于评委确认流程确实暂停。
- 每次图执行必须传入 `configurable.thread_id=case_id`，通过 LangGraph checkpointer 保存中断位置和状态。
- 业务表中的 `state_json` 用于列表和详情页快速渲染；恢复执行以 checkpointer 为准。

## 7. Mock 工具设计

### 7.1 工具清单

| 工具名 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- |
| mock_cmdb_lookup | service_name / user_id / asset_id | 负责人、系统等级、依赖、最近变更、资产归属 | 判断影响范围和责任人。 |
| mock_log_search | service_name, time_range, keyword | 错误摘要、异常峰值、trace_id、最近异常日志 | 支撑运维诊断。 |
| mock_sop_search | case_type, symptom, action | SOP 步骤、风险提示、审批要求 | 生成处理建议和审批依据。 |
| mock_history_search | case_type, target, symptom | 历史相似 case、处理方式、是否成功 | 复用历史经验。 |
| mock_risk_score | case_type, action, asset_level | risk_level, reason, approval_required | 决定是否进入审批。 |
| mock_ticket_create | title, priority, assignee, description | ticket_id, url, status | 升级人工或沉淀工单。 |
| mock_execute_action | action_name, target, params | success, message, verify_hint | 模拟动作执行。 |
| mock_notify | receiver, message | sent, channel, message_id | 模拟通知负责人或用户。 |

### 7.2 高风险动作控制

| 动作 | 是否必须审批 | 原因 |
| --- | --- | --- |
| reset_password_mock | 是 | 涉及账号安全和用户影响。 |
| restart_service_mock | 是 | 可能影响线上可用性。 |
| rollback_release_mock | 是 | 涉及生产变更回滚。 |
| block_ip_mock | 是 | 可能误伤正常访问。 |
| unlock_account_mock | 是 | 涉及账号安全策略变更。 |
| create_ticket | 否 | 仅创建记录，不改变系统状态。 |
| send_notification | 通常否 | 普通内部通知低风险，涉及对外通知可设为高风险。 |
| generate_report | 否 | 只生成文本报告。 |

执行层必须做硬校验：只要动作属于高风险清单且 `approval_status` 不是 `approved` 或 `modified`，`mock_execute_action` 不得执行，并返回阻断结果。不能只依赖 LLM 自觉遵守。

## 8. 核心流程设计

### 8.1 Case 创建流程

```text
用户输入问题或选择样例
   |
   v
创建 case_id 和初始 state
   |
   v
写入 case 表和 timeline
   |
   v
触发 LangGraph invoke
   |
   v
返回当前状态、Agent 回复、下一步操作
```

样例输入：

- VPN 无法登录：“我今天突然连不上 VPN，提示账号异常。”
- 服务 CPU 告警：“支付服务 CPU 连续 10 分钟超过 90%。”
- 疑似异常登录：“某员工账号凌晨从异地登录并失败多次。”

### 8.2 信息补全流程

```text
classify_case 输出 required_fields
   |
   v
check_info 从用户输入和历史消息抽取字段
   |
   v
缺少关键字段？
   |-- 是 -> ask_clarification 生成追问并暂停
   |
   v
信息完整 -> query_context
```

不同场景的关键字段：

| 场景 | 关键字段 |
| --- | --- |
| IT 工单 | user_id、问题类型、错误提示、发生时间、设备或网络环境 |
| 运维告警 | service_name、metric、threshold、duration、time_range、environment |
| 安全事件 | user_id 或 source_ip、事件类型、发生时间、失败次数、登录地点 |

### 8.3 工具查询流程

```text
根据 case_type 和 extracted_fields 选择工具
   |
   +-- IT 工单：mock_sop_search + mock_history_search + mock_risk_score
   +-- 运维告警：mock_cmdb_lookup + mock_log_search + mock_sop_search + mock_risk_score
   +-- 安全事件：mock_cmdb_lookup/user_lookup + mock_history_search + mock_risk_score
   |
   v
合并 tool_results
   |
   v
写入 timeline 和 UI 工具调用区
```

工具失败策略：

- 单个非关键工具失败时记录错误，继续使用其他工具结果。
- 关键工具失败导致无法判断时，进入 `close_or_escalate` 并创建人工工单。
- 工具返回必须是结构化 JSON，页面支持展开查看完整内容。

### 8.4 诊断与建议流程

诊断节点输入：

- 用户原始问题和补充信息。
- 分类结果和优先级。
- 工具查询结果。
- SOP 风险提示和历史 case。

诊断节点输出 JSON：

```json
{
  "hypotheses": [
    {
      "cause": "最近发布导致支付服务 CPU 升高",
      "evidence": ["CMDB 显示 20 分钟前有发布", "日志出现线程池耗尽错误"],
      "confidence": 0.78
    }
  ],
  "risk_level": "high",
  "proposed_actions": [
    {
      "action_name": "restart_service_mock",
      "target": "payment-api",
      "params": {"instances": ["payment-api-01"]},
      "reason": "CPU 持续超过阈值且错误率升高",
      "approval_required": true
    }
  ],
  "user_facing_summary": "疑似最近发布引发资源异常，建议先通知负责人并审批后执行重启或回滚。"
}
```

### 8.5 审批流程

```text
diagnose 判断存在高风险动作
   |
   v
human_approval 写入 waiting_approval
   |
   v
页面显示风险原因、影响范围、建议动作、工具证据
   |
   +-- 批准 -> execute_action
   +-- 修改动作 -> execute_action
   +-- 拒绝 -> close_or_escalate
```

审批区必须展示：

- 风险等级和风险原因。
- 动作名称、目标对象和参数。
- 影响范围，如生产系统、账号、IP、用户。
- 工具证据，如 CMDB 等级、SOP 审批要求、日志摘要。
- 审批意见输入框。

### 8.6 执行与验证流程

```text
execute_action 执行低风险动作或已审批动作
   |
   v
记录 action_results
   |
   v
verify_result 调用验证逻辑
   |
   +-- 验证成功 -> close_or_escalate(status=closed)
   +-- 验证失败且 retry_count<2 -> query_context
   +-- 验证失败且 retry_count>=2 -> close_or_escalate(status=escalated)
```

验证示例：

- VPN 工单：账号状态恢复、SOP 自助步骤已返回、工单已创建。
- CPU 告警：mock 指标下降、错误日志减少或服务状态恢复。
- 安全事件：账号冻结或密码重置动作完成，后续异常登录停止。

### 8.7 最终报告流程

报告内容：

- 问题概述。
- 分类与优先级。
- 补充信息记录。
- 工具查询证据。
- 诊断结论。
- 风险判断与审批记录。
- 执行动作和验证结果。
- 最终状态：已关闭或已升级。
- 后续建议。

报告格式使用 Markdown，便于页面展示和导出。

## 9. 页面与交互设计

### 9.1 页面结构

| 页面/区域 | 必需元素 | 推荐增强 |
| --- | --- | --- |
| Case 创建页 | 自然语言输入框、场景选择、提交按钮 | 一键加载 VPN、CPU 告警、异常登录样例。 |
| Case 详情页 | 当前状态、Agent 摘要、下一步操作 | 自动刷新或手动刷新按钮。 |
| 状态图区域 | 节点列表、当前节点高亮、路由结果 | Mermaid 图、节点耗时、成功/失败颜色。 |
| 工具调用区 | 工具名称、入参、返回摘要 | 展开完整 JSON、错误标记。 |
| 审批区 | 批准、拒绝、修改动作、审批意见 | 显示风险原因和影响范围。 |
| 时间线区 | 节点执行记录、用户输入、工具结果、审批结果 | 支持按节点类型过滤。 |
| 报告区 | 最终处置报告 | 导出 Markdown。 |
| 模型配置页 | 配置名称、供应商、Base URL、模型名、API Key、温度、启用开关 | 支持多套配置、测试连通性、切换当前使用模型。 |

### 9.2 关键交互规则

- 状态为 `waiting_user` 时，只允许用户补充信息并继续同一个 case。
- 状态为 `waiting_approval` 时，执行动作按钮必须锁定，只允许审批操作。
- 审批拒绝后必须明确显示“高风险动作未执行”。
- Agent 输出优先结构化展示，避免只显示一大段自然语言。
- 每次工具调用必须在工具调用区和 timeline 中可见。
- 每次条件路由必须显示路由原因，例如“缺少 service_name，进入追问”。
- 所有 mock 动作必须明确标记为模拟执行，不能暗示已修改真实系统。
- 模型配置页必须隐藏 API Key 明文；保存后只展示脱敏值。
- 切换当前模型后，新创建 case 使用新配置；已进入等待状态的 case 恢复时默认也读取当前启用配置，但 timeline 记录本次使用的模型配置 ID。

## 10. 数据模型与 API

### 10.1 SQLite 表设计

#### cases

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| case_id | TEXT PRIMARY KEY | Case 编号。 |
| title | TEXT | 自动生成标题。 |
| case_type | TEXT | 类型。 |
| scenario | TEXT | 样例或自定义场景。 |
| priority | TEXT | 优先级。 |
| status | TEXT | 当前状态。 |
| state_json | TEXT | 完整 Agent state JSON。 |
| created_at | TEXT | 创建时间。 |
| updated_at | TEXT | 更新时间。 |

#### timeline_events

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INTEGER PRIMARY KEY | 自增 ID。 |
| case_id | TEXT | Case 编号。 |
| node_name | TEXT | 节点名称。 |
| event_type | TEXT | node_start / node_end / tool_call / route / approval / error。 |
| input_json | TEXT | 输入摘要。 |
| output_json | TEXT | 输出摘要。 |
| route_to | TEXT | 路由目标。 |
| duration_ms | INTEGER | 耗时。 |
| created_at | TEXT | 事件时间。 |

#### approvals

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INTEGER PRIMARY KEY | 自增 ID。 |
| case_id | TEXT | Case 编号。 |
| decision | TEXT | approved / rejected / modified。 |
| original_actions_json | TEXT | 原始动作。 |
| modified_actions_json | TEXT | 修改后动作。 |
| comment | TEXT | 审批意见。 |
| created_at | TEXT | 审批时间。 |

#### reports

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| case_id | TEXT PRIMARY KEY | Case 编号。 |
| report_markdown | TEXT | 最终报告。 |
| created_at | TEXT | 生成时间。 |

#### model_configs

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PRIMARY KEY | 模型配置 ID。 |
| name | TEXT | 用户可识别的配置名称，如“公司网关 GPT-4.1”。 |
| provider | TEXT | openai_compatible / azure_openai / custom_gateway。 |
| base_url | TEXT | 模型服务地址。 |
| model_name | TEXT | Chat Model 名称。 |
| api_key_encrypted | TEXT | 加密或本地混淆后的 API Key。 |
| temperature | REAL | 默认生成温度。 |
| timeout_seconds | INTEGER | 请求超时时间。 |
| is_active | INTEGER | 是否为当前启用配置；同一时间只能有一条为 1。 |
| last_test_status | TEXT | success / failed / unknown。 |
| last_test_message | TEXT | 最近一次连通性测试结果。 |
| created_at | TEXT | 创建时间。 |
| updated_at | TEXT | 更新时间。 |

#### langgraph checkpoint

LangGraph checkpointer 可使用独立 SQLite 数据库或与业务库同库。checkpoint 表结构优先交给 LangGraph 官方 checkpointer 创建和维护，业务代码只负责传入稳定的 `thread_id=case_id` 和必要的 `checkpoint_ns`。

业务表 `cases.state_json` 保留为页面快速展示和兜底摘要，不替代 LangGraph checkpoint。真正的图恢复必须通过 checkpointer 读取上一次中断位置和 state。

### 10.2 API 建议

采用 FastAPI 对前端提供 HTTP API。

| 接口 | 方法 | 说明 | 关键字段 |
| --- | --- | --- | --- |
| /api/cases | POST | 创建 case 并触发图执行。 | message, scenario |
| /api/cases | GET | 查询 case 列表。 | status, case_type |
| /api/cases/{case_id} | GET | 获取 case 当前状态。 | status, state, timeline |
| /api/cases/{case_id}/message | POST | 用户补充信息后继续流程。 | message |
| /api/cases/{case_id}/approve | POST | 审批动作。 | decision, comment, modified_actions |
| /api/cases/{case_id}/timeline | GET | 获取执行时间线。 | case_id |
| /api/cases/{case_id}/report | GET | 获取最终报告。 | case_id |
| /api/model-configs | GET | 获取模型配置列表。 | active_only |
| /api/model-configs | POST | 新增模型配置。 | name, provider, base_url, model_name, api_key, temperature |
| /api/model-configs/{config_id} | PUT | 更新模型配置。 | name, base_url, model_name, api_key, temperature |
| /api/model-configs/{config_id} | DELETE | 删除未启用模型配置。 | config_id |
| /api/model-configs/{config_id}/activate | POST | 切换当前使用模型。 | config_id |
| /api/model-configs/{config_id}/test | POST | 测试模型连通性。 | config_id |

### 10.3 模型配置运行规则

- 后端启动时不强制要求 `.env` 中存在 API Key；前端配置页负责创建模型配置。
- 如果没有启用模型配置，创建 case 时返回明确错误，提示先完成模型配置。
- LLM 节点执行前通过 `model_config_service.get_active_config()` 获取当前启用配置并创建模型客户端。
- 每次 LLM 调用在 timeline 中记录 `model_config_id`、`model_name`、节点名、耗时和错误摘要，不记录 API Key。
- 同一时间只允许一个启用模型配置，切换时使用事务保证旧配置取消启用、新配置启用。
- 删除模型配置时，如果该配置正在启用，必须先切换到其他配置。
- API Key 存储至少做本地加密或混淆；Demo 可使用环境变量中的 `CONFIG_SECRET` 派生密钥，生产环境应接入密钥管理服务。

### 10.4 本地观测设计

MVP 先不接入 LangSmith。观测能力通过本地 timeline 和结构化日志实现，目标是让评委能在页面上看懂 LangGraph 的节点流转、条件路由、工具调用、人工审批、执行结果和异常信息。

接入规则：

- 每个节点开始和结束都写入 `timeline_events`，记录节点名、事件类型、输入摘要、输出摘要、耗时和路由目标。
- 每次 mock 工具调用都写入 timeline，记录工具名称、脱敏后的入参、返回摘要和错误信息。
- 每次条件路由都写入 timeline，记录判断字段和路由原因，例如 `missing_fields 非空，进入 ask_clarification`。
- 每次 LLM 调用在 timeline 中记录 `model_config_id`、`model_name`、节点名、耗时和错误摘要，不记录 API Key。
- 页面 timeline 作为主要观测入口，支持按节点类型、工具调用、审批事件、异常事件过滤。
- 后端同时输出结构化日志到控制台或本地 JSONL，便于排查 Demo 问题。

## 11. Prompt 与结构化输出设计

### 11.1 分类 Prompt 输出

```json
{
  "case_type": "ops_alert",
  "scenario": "cpu_alert",
  "priority": "P1",
  "confidence": 0.92,
  "required_fields": ["service_name", "metric", "threshold", "duration", "time_range"],
  "reason": "用户描述了支付服务 CPU 连续 10 分钟超过 90%，符合运维告警。"
}
```

### 11.2 信息检查输出

```json
{
  "extracted_fields": {
    "service_name": "支付服务",
    "metric": "CPU",
    "threshold": "90%",
    "duration": "10 分钟"
  },
  "missing_fields": ["time_range", "environment"],
  "pending_question": "请补充告警发生时间范围，以及这是生产环境还是测试环境？"
}
```

### 11.3 诊断 Prompt 约束

系统提示词必须强调：

- 只能基于用户输入和 mock 工具结果诊断。
- 不能声称已经执行真实生产动作。
- 高风险动作必须标记 `approval_required=true`。
- 不确定时必须升级人工，不得编造工具证据。
- 输出必须是 JSON，供条件路由使用。

### 11.4 报告 Prompt 输出

报告使用 Markdown：

```markdown
## 处置摘要

## 分类与优先级

## 关键信息

## 工具查询证据

## 诊断结论

## 审批记录

## 执行动作

## 验证结果

## 最终状态与后续建议
```

## 12. 异常处理与安全控制

### 12.1 安全控制

- 禁止连接真实生产系统。
- 所有外部系统调用均使用本地 mock 函数或 mock 数据。
- 高风险动作执行前必须通过硬编码策略校验审批状态。
- 报告和 timeline 中明确标记“模拟执行”。
- timeline 和结构化日志必须脱敏，不记录 API Key、真实账号、真实日志和真实生产地址。
- `.env`、API Key、真实账号、真实日志不得提交到仓库。

### 12.2 异常处理

| 异常 | 处理策略 |
| --- | --- |
| LLM 分类失败 | 使用规则兜底分类；仍失败则标记 unknown 并升级人工。 |
| LLM 输出非 JSON | 尝试 JSON 修复一次；失败则升级人工并记录错误。 |
| mock 工具失败 | 记录工具错误；非关键工具失败继续，关键工具失败升级。 |
| 审批状态丢失 | 禁止执行高风险动作，要求重新审批。 |
| 验证失败 | retry_count 加 1，回到 query_context 或 diagnose；超过阈值升级。 |
| 存储失败 | 页面提示失败，不继续执行动作。 |

## 13. 测试与验收设计

### 13.1 核心测试用例

| 用例 | 输入 | 预期结果 |
| --- | --- | --- |
| 信息不足追问 | “我连不上 VPN。” | 分类为 IT 工单，缺少账号/错误提示/时间等信息，进入 waiting_user。 |
| 低风险自动处理 | “我今天突然连不上 VPN，账号 zhangsan，提示密码过期。” | 查询 SOP，建议自助重置或创建低风险工单，不需要执行高风险动作。 |
| 运维高风险审批 | “支付服务 CPU 连续 10 分钟超过 90%。” | 查询 CMDB/日志/SOP，建议重启或回滚，进入 waiting_approval。 |
| 审批拒绝 | 对高风险动作点击拒绝 | 动作不执行，case 关闭或升级，报告写明拒绝原因。 |
| 审批批准 | 对高风险动作点击批准 | 执行 mock 动作，验证结果，生成报告。 |
| 安全事件 | “某员工账号凌晨从异地登录并失败多次。” | 分类安全事件，风险评分，高风险动作需审批。 |
| 验证失败重试 | mock_execute_action 返回 success 但验证失败 | retry_count 增加，回到 query_context/diagnose；超过 2 次升级。 |
| 状态恢复 | waiting_approval 时刷新页面 | 仍显示待审批动作和审批按钮，未执行动作。 |

### 13.2 验收对照

| PRD 验收项 | 设计覆盖 |
| --- | --- |
| LangGraph 显式工作流 | `graph.py` 定义 StateGraph、节点、条件边和循环。 |
| 至少 3 类样例输入 | IT 工单、运维告警、安全事件。 |
| 至少 3 个 mock 工具 | CMDB、日志、SOP、风险评分、工单、执行、通知等。 |
| 高风险动作审批 | `human_approval` 暂停 + 执行层硬校验。 |
| 状态恢复和时间线 | LangGraph checkpoint 保存图状态，SQLite 保存 state_json 摘要和 timeline_events。 |
| 前端多模型配置 | 模型配置页支持新增、测试、启用、切换多套模型配置。 |
| 本地观测 | 页面 timeline 和结构化日志能展示 LLM、工具、路由、审批和异常事件。 |
| 最终处置报告 | `close_or_escalate` 生成 Markdown 报告。 |

## 14. 两天开发计划

### 第 1 天上午：图结构和数据准备

- 确定三类样例场景和关键字段。
- 定义 AgentState、CaseStatus、Action schema。
- 准备 mock JSON 数据。
- 搭建 FastAPI 后端、前端工程、SQLite 初始化和模型配置表。
- 设计 timeline 事件结构和本地结构化日志格式。

产出：状态图草图、schema、mock 数据、基础页面。

### 第 1 天下午：跑通核心 LangGraph

- 实现 receive_input、classify_case、check_info、query_context、diagnose、execute_action、verify_result、close_or_escalate。
- 实现条件路由和验证失败循环。
- 实现至少 3 个 mock 工具。
- 接入 LangGraph SQLite checkpointer，确保 `thread_id=case_id` 可恢复。
- 接入 timeline 记录，确保 LangGraph 节点、LLM 调用和工具调用产生可展示事件。
- 命令行或页面跑通低风险自动处理路径。

产出：可运行核心图，能看到节点流转和工具结果。

### 第 2 天上午：审批与恢复

- 实现 human_approval 暂停点。
- 实现审批通过、拒绝、修改动作后的恢复执行。
- 持久化 case state、checkpoint、timeline、approval 和 report。
- 完成 Case 详情页、审批区、工具调用区、时间线区。
- 完成模型配置页，支持多模型配置、连通性测试和当前模型切换。
- 在 Case 详情页展示 timeline，并支持工具调用、审批事件和异常事件展开。

产出：可交互 Demo，支持高风险审批路径。

### 第 2 天下午：演示打磨

- 完善报告生成、错误处理、样例按钮和 README。
- 增加 Mermaid 状态图或节点高亮。
- 检查 timeline 和结构化日志脱敏效果。
- 编写演示脚本和验收自测清单。
- 清理真实敏感配置，确认只使用 mock 数据。

产出：最终提交物和 5 分钟演示材料。

## 15. 演示脚本

### 15.1 5 分钟演示流程

1. 展示 LangGraph 状态图，说明节点、条件边、循环和审批暂停点。
2. 输入“我连不上 VPN”，展示 Agent 分类为 IT 工单并追问缺失信息。
3. 补充账号和错误提示，展示查询 SOP、历史工单并给出低风险处置建议。
4. 输入“支付服务 CPU 连续 10 分钟超过 90%”，展示 CMDB、日志、SOP 和风险评分工具调用。
5. 展示高风险动作进入审批，先拒绝一次，证明动作未执行。
6. 重新批准或修改动作，展示 mock 执行、验证和最终报告。
7. 打开时间线和工具 JSON，说明每一步可解释、可追踪、可恢复。

### 15.2 提交物

- 代码仓库。
- README：启动方式、依赖安装、模型配置、状态图说明、样例输入、演示步骤、已知限制。
- mock 数据和 mock 工具说明。
- 最终处置报告样例。
- 可选：演示视频、评估用例。

## 16. 已知限制

- MVP 不接入真实生产系统，所有系统查询和执行动作均为 mock。
- 审批身份使用页面操作模拟，不实现完整 RBAC。
- LLM 分类和诊断依赖模型质量，需通过结构化输出、规则兜底和错误升级降低不稳定性。
- SQLite 适合 Demo 和单机演示，不适合生产高并发。
- LangGraph checkpoint 接入 SQLite，适合 Demo 的暂停恢复；生产环境应评估更强的 checkpoint 存储、并发控制和迁移策略。
- MVP 暂不接入 LangSmith 等外部观测平台；生产环境如需集中化观测，可再评估 LangSmith、OpenTelemetry 或内部可观测平台。
