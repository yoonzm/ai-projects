import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  ClipboardList,
  FileText,
  GitBranch,
  Play,
  RefreshCw,
  Send,
  Settings,
  ShieldAlert,
  Wrench,
  XCircle,
} from "lucide-react";
import { api } from "./api/client";
import "./styles.css";

const samples = [
  { label: "VPN 追问", scenario: "vpn_login", message: "我连不上 VPN。" },
  {
    label: "VPN 低风险",
    scenario: "vpn_login",
    message: "我今天突然连不上 VPN，账号 zhangsan，提示密码过期。",
  },
  { label: "CPU 审批", scenario: "cpu_alert", message: "支付服务 CPU 连续 10 分钟超过 90%。" },
  {
    label: "异常登录",
    scenario: "abnormal_login",
    message: "某员工账号凌晨从异地登录并失败多次。",
  },
];

const graphNodes = [
  "receive_input",
  "classify_case",
  "check_info",
  "ask_clarification",
  "query_context",
  "diagnose",
  "human_approval",
  "execute_action",
  "verify_result",
  "close_or_escalate",
];

function App() {
  const [cases, setCases] = useState<any[]>([]);
  const [detail, setDetail] = useState<any | null>(null);
  const [message, setMessage] = useState(samples[0].message);
  const [scenario, setScenario] = useState(samples[0].scenario);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"cases" | "models">("cases");

  async function refresh(caseId = detail?.case_id) {
    const nextCases = await api.listCases();
    setCases(nextCases);
    if (caseId) {
      setDetail(await api.getCase(caseId));
    }
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, []);

  async function run(action: () => Promise<any>) {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      if (result?.case_id) setDetail(result);
      await refresh(result?.case_id);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>AI 工单/告警智能处置编排 Agent</h1>
          <p>LangGraph 状态编排、条件路由、人工审批、mock 执行和处置报告。</p>
        </div>
        <nav className="tabs">
          <button className={tab === "cases" ? "active" : ""} onClick={() => setTab("cases")}>
            <ClipboardList size={18} /> Case
          </button>
          <button className={tab === "models" ? "active" : ""} onClick={() => setTab("models")}>
            <Settings size={18} /> 模型配置
          </button>
          <button onClick={() => refresh()} title="刷新">
            <RefreshCw size={18} />
          </button>
        </nav>
      </header>

      {error && <div className="error">{error}</div>}

      {tab === "cases" ? (
        <section className="workspace">
          <aside className="left-pane">
            <section className="panel">
              <h2>创建 Case</h2>
              <div className="sample-grid">
                {samples.map((item) => (
                  <button
                    key={item.label}
                    onClick={() => {
                      setMessage(item.message);
                      setScenario(item.scenario);
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <textarea value={message} onChange={(event) => setMessage(event.target.value)} />
              <button
                className="primary"
                disabled={busy || !message.trim()}
                onClick={() => run(() => api.createCase(message, scenario))}
              >
                <Play size={18} /> 提交并运行图
              </button>
            </section>

            <section className="panel list-panel">
              <h2>Case 列表</h2>
              <div className="case-list">
                {cases.map((item) => (
                  <button
                    key={item.case_id}
                    className={detail?.case_id === item.case_id ? "selected" : ""}
                    onClick={() => run(() => api.getCase(item.case_id))}
                  >
                    <span>{item.title}</span>
                    <small>
                      {item.case_id} · {item.priority} · {item.status}
                    </small>
                  </button>
                ))}
              </div>
            </section>
          </aside>

          <section className="right-pane">
            {detail ? (
              <CaseDetail detail={detail} busy={busy} run={run} />
            ) : (
              <section className="empty-state">
                <GitBranch size={32} />
                <p>选择样例或提交自然语言工单后，这里会展示状态图、工具调用、审批和报告。</p>
              </section>
            )}
          </section>
        </section>
      ) : (
        <ModelConfigPage />
      )}
    </main>
  );
}

function CaseDetail({ detail, busy, run }: { detail: any; busy: boolean; run: (fn: () => Promise<any>) => void }) {
  const state = detail.state;
  const timeline = detail.timeline || [];
  const toolCalls = timeline.filter((item: any) => item.event_type === "tool_call");
  const lastNode = useMemo(() => {
    const ended = [...timeline].reverse().find((item: any) => item.event_type === "node_end");
    return ended?.node_name || "receive_input";
  }, [timeline]);

  return (
    <div className="detail-grid">
      <section className="panel summary-panel">
        <div className="summary-head">
          <div>
            <h2>{state.title}</h2>
            <p>{state.case_id}</p>
          </div>
          <StatusBadge status={state.status} />
        </div>
        <div className="summary-metrics">
          <Metric label="类型" value={state.case_type} />
          <Metric label="优先级" value={state.priority} />
          <Metric label="风险" value={state.risk_level} />
          <Metric label="审批" value={state.approval_status} />
        </div>
        {state.pending_question && (
          <div className="notice">
            <CircleDot size={18} />
            <span>{state.pending_question}</span>
          </div>
        )}
        <NextAction state={state} busy={busy} run={run} caseId={detail.case_id} />
      </section>

      <section className="panel">
        <h2>
          <GitBranch size={18} /> 状态图
        </h2>
        <div className="node-grid">
          {graphNodes.map((node) => (
            <div
              key={node}
              className={[
                "node",
                node === lastNode ? "current" : "",
                timeline.some((item: any) => item.node_name === node) ? "visited" : "",
              ].join(" ")}
            >
              {node}
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>
          <Wrench size={18} /> 工具调用
        </h2>
        <JsonList items={toolCalls} empty="尚未调用工具。" />
      </section>

      <section className="panel">
        <h2>
          <ShieldAlert size={18} /> 诊断与审批
        </h2>
        <Diagnosis state={state} />
      </section>

      <section className="panel timeline-panel">
        <h2>
          <ClipboardList size={18} /> 时间线
        </h2>
        <Timeline items={timeline} />
      </section>

      <section className="panel report-panel">
        <h2>
          <FileText size={18} /> 最终报告
        </h2>
        {detail.report ? <pre className="markdown">{detail.report}</pre> : <p className="muted">关闭或升级后生成报告。</p>}
      </section>
    </div>
  );
}

function NextAction({ state, busy, run, caseId }: { state: any; busy: boolean; run: any; caseId: string }) {
  const [supplement, setSupplement] = useState("账号 zhangsan，提示密码过期，今天在家庭 Wi-Fi 上。");
  const [comment, setComment] = useState("");
  const [modifiedJson, setModifiedJson] = useState("");

  if (state.status === "waiting_user") {
    return (
      <div className="action-box">
        <textarea value={supplement} onChange={(event) => setSupplement(event.target.value)} />
        <button
          className="primary"
          disabled={busy || !supplement.trim()}
          onClick={() => run(() => api.continueCase(caseId, supplement))}
        >
          <Send size={18} /> 补充并继续
        </button>
      </div>
    );
  }

  if (state.status === "waiting_approval") {
    const proposed = JSON.stringify(state.proposed_actions || [], null, 2);
    return (
      <div className="action-box">
        <label>审批意见</label>
        <input value={comment} onChange={(event) => setComment(event.target.value)} placeholder="填写批准、拒绝或修改原因" />
        <details>
          <summary>修改动作参数 JSON</summary>
          <textarea
            className="json-editor"
            value={modifiedJson || proposed}
            onChange={(event) => setModifiedJson(event.target.value)}
          />
        </details>
        <div className="approval-buttons">
          <button
            className="primary"
            disabled={busy}
            onClick={() => run(() => api.approveCase(caseId, { decision: "approved", comment }))}
          >
            <CheckCircle2 size={18} /> 批准
          </button>
          <button
            disabled={busy}
            onClick={() => {
              const parsed = JSON.parse(modifiedJson || proposed);
              return run(() => api.approveCase(caseId, { decision: "modified", comment, modified_actions: parsed }));
            }}
          >
            <Wrench size={18} /> 修改并批准
          </button>
          <button
            className="danger"
            disabled={busy}
            onClick={() => run(() => api.approveCase(caseId, { decision: "rejected", comment }))}
          >
            <XCircle size={18} /> 拒绝
          </button>
        </div>
      </div>
    );
  }

  return <p className="muted">当前状态无需人工输入。</p>;
}

function Diagnosis({ state }: { state: any }) {
  return (
    <div className="diagnosis">
      <p>{state.diagnosis?.user_facing_summary || "尚未生成诊断。"}</p>
      {state.proposed_actions?.length > 0 && (
        <pre>{JSON.stringify(state.proposed_actions, null, 2)}</pre>
      )}
      {state.approval_status === "rejected" && (
        <div className="warning">
          <AlertTriangle size={18} />
          <span>审批拒绝，高风险动作未执行。</span>
        </div>
      )}
    </div>
  );
}

function Timeline({ items }: { items: any[] }) {
  if (!items.length) return <p className="muted">暂无时间线。</p>;
  return (
    <div className="timeline">
      {items.map((item) => (
        <details key={item.id} className={item.event_type}>
          <summary>
            <span>{item.node_name}</span>
            <small>
              {item.event_type}
              {item.route_to ? ` -> ${item.route_to}` : ""} · {item.duration_ms || 0}ms
            </small>
          </summary>
          <pre>{JSON.stringify({ input: item.input, output: item.output }, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function JsonList({ items, empty }: { items: any[]; empty: string }) {
  if (!items.length) return <p className="muted">{empty}</p>;
  return (
    <div className="json-list">
      {items.map((item) => (
        <details key={item.id} open>
          <summary>
            {item.node_name}
            <small>{item.created_at}</small>
          </summary>
          <pre>{JSON.stringify({ input: item.input, output: item.output }, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status ${status}`}>{status}</span>;
}

function ModelConfigPage() {
  const [configs, setConfigs] = useState<any[]>([]);
  const [form, setForm] = useState({
    name: "公司网关 GPT",
    provider: "openai_compatible",
    base_url: "",
    model_name: "gpt-4.1",
    api_key: "",
    temperature: 0.2,
    timeout_seconds: 30,
  });
  const [message, setMessage] = useState("");

  async function load() {
    setConfigs(await api.listModelConfigs());
  }

  useEffect(() => {
    load().catch((err) => setMessage(err.message));
  }, []);

  async function submit() {
    await api.createModelConfig(form);
    setForm({ ...form, api_key: "" });
    await load();
  }

  async function run(fn: () => Promise<any>) {
    setMessage("");
    try {
      const result = await fn();
      setMessage(result?.message || "操作完成");
      await load();
    } catch (err: any) {
      setMessage(err.message);
    }
  }

  return (
    <section className="model-layout">
      <section className="panel">
        <h2>新增模型配置</h2>
        <div className="form-grid">
          <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="配置名称" />
          <select value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })}>
            <option value="openai_compatible">openai_compatible</option>
            <option value="azure_openai">azure_openai</option>
            <option value="custom_gateway">custom_gateway</option>
          </select>
          <input value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="Base URL" />
          <input value={form.model_name} onChange={(event) => setForm({ ...form, model_name: event.target.value })} placeholder="模型名" />
          <input
            type="password"
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
            placeholder="API Key"
          />
          <input
            type="number"
            value={form.temperature}
            step="0.1"
            onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })}
            placeholder="温度"
            aria-label="温度"
          />
        </div>
        <button className="primary" onClick={() => run(submit)}>
          <Settings size={18} /> 保存配置
        </button>
        {message && <p className="notice-text">{message}</p>}
      </section>

      <section className="panel">
        <h2>配置列表</h2>
        <div className="config-list">
          {configs.map((item) => (
            <div className="config-row" key={item.id}>
              <div>
                <strong>{item.name}</strong>
                <small>
                  {item.provider} · {item.model_name} · {item.api_key_masked || "无 Key"} · {item.last_test_status}
                </small>
              </div>
              <StatusBadge status={item.is_active ? "active" : "inactive"} />
              <button onClick={() => run(() => api.testModelConfig(item.id))}>测试</button>
              {!item.is_active && <button onClick={() => run(() => api.activateModelConfig(item.id))}>启用</button>}
              {!item.is_active && <button className="danger" onClick={() => run(() => api.deleteModelConfig(item.id))}>删除</button>}
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
