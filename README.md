# ai-projects

本项目是个人用于学习和练习 AI 相关知识的项目集合。

## 企业知识库智能问答与材料生成助手

目录：`enterprise-kb-assistant`

这是根据 PRD 和概要设计实现的 LangChain/RAG Web Demo，支持：

- 管理员统一配置 Chat Model、Embedding Model、Base URL 和 API Key。
- 普通用户直接使用已启用模型，无需配置密钥。
- Markdown、TXT、PDF、DOCX 文档导入与索引。
- 基于知识库的问答、来源引用、未知问题拒答。
- 多轮追问、材料生成、工具调用日志、点赞/点踩反馈。
- 多知识库空间切换和样例资料包导入。

### 启动

```bash
cd enterprise-kb-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

启动后需要管理员先进入“系统配置”页配置 Chat Model。管理员密码默认来自 `ADMIN_PASSWORD`，示例配置为 `admin`。

### 模型配置

- 管理员进入“系统配置”页后，可统一配置 Chat Model、Base URL、API Key 和默认检索参数。
- Chat Model 必须配置；Embedding Model 可选。
- 未配置 Embedding Model 时，系统会自动使用本地 embedding 完成文档向量化和检索，但回答生成仍会调用已配置的 Chat Model。
- 普通用户不需要配置 API Key，也不能查看或修改 API Key。
- 支持 OpenAI-compatible 接口；Azure OpenAI v1 或公司统一模型网关可通过 Base URL 接入。
- 未配置 Chat Model 和 API Key 时，系统不会降级为本地回答，会提示管理员先完成模型配置。

### 验证

```bash
cd enterprise-kb-assistant
python3 scripts/smoke_test.py
```

样例问题：

- 出差住宿费标准是多少？如果超标怎么审批？
- 那试用期员工也适用吗？
- 明年公司是否增加年假？
- 生成“申请差旅报销”的流程清单。

### 已知限制

- MVP 使用轻量管理员密码保护系统配置页，生产环境应接入 SSO/RBAC。
- 当前不接入真实 HR、OA、财务系统，工具调用使用知识库检索类自定义工具。
- 本地 embedding 降级只解决“找资料”，不会替代大模型生成能力。
