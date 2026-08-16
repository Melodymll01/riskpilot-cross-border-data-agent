# LangSmith 可观测性与隐私边界

## 定位

LangSmith 是 RiskPilot 的**可选 AI 可观测性 Adapter**，不是领域依赖，也不是业务数据
存储。`domain.ports.TracePort` 定义框架无关契约：

```text
Copilot / Deep Research / Case Assessment / Risk Profile
  → TracePort
    ├─ NoopTraceAdapter（默认）
    └─ LangSmithTraceAdapter（显式启用）
```

默认 `NoopTraceAdapter` 不记录、不联网。更换 OpenTelemetry 或自建平台时，不需要改动
领域模型和应用用例。

## 覆盖链路

| 链路 | 根 Trace | 记录内容 |
| --- | --- | --- |
| LangChain Copilot | `riskpilot.copilot.run` | 哈希 owner/task、消息长度、工具次数、引用数、状态 |
| LangGraph Deep Research | `riskpilot.deep_research.run` | 哈希 owner、节点、检索轮次、文档数、Web Search 分支、状态 |
| LangGraph Case Assessment | `riskpilot.case_assessment.start/resume` | 哈希 workspace/case/actor/thread、材料/缺失事实计数、中断类型、阶段、状态 |
| 风险评估模型 | `riskpilot.risk_profile.assess` | target/document 长度、是否配置、evidence state、证据数、状态 |

## 隐私策略

LangSmith Client 在本地出站前执行以下保护：

1. `inputs={}`、`outputs={}`，不上传 Prompt、案件正文、检索证据或模型回答；
2. 删除序列化模型/Prompt 模板、事件和附件；
3. 异常只保留 `error_type`，异常文本和 traceback 统一替换为固定占位符；
4. metadata 采用字段白名单，未知字段直接丢弃；
5. owner、workspace、case、task、run、thread、assessment、actor 等 ID 使用
   `HMAC-SHA256` 截断哈希，不上传原值；
6. 不保存文档正文、记忆原文、图片、Embedding、凭证或思维链；
7. 默认采样率 `0.1`，默认关闭，不配置不会发起 LangSmith 网络请求。

## 启用

在 `.env` 中配置：

```ini
RISK_PILOT_LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=<your-langsmith-api-key>
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=riskpilot
LANGSMITH_SAMPLING_RATE=0.1
LANGSMITH_HASH_SALT=<at-least-16-random-characters>
```

`LANGSMITH_HASH_SALT` 应使用独立随机值，不要复用 JWT、模型或数据库密钥。生产环境应通过
Secret Manager 注入。

不要设置 SDK 标准全局开关 `LANGSMITH_TRACING`。RiskPilot 使用项目专属开关
`RISK_PILOT_LANGSMITH_ENABLED`，保证所有 Trace 都经过 `LangSmithTraceAdapter`
的隐私策略。

## 验证

```bash
uv run pytest -q tests/infra/test_langsmith_tracing.py
```

测试覆盖默认关闭、启用配置门禁、业务 ID 哈希、metadata 白名单，以及输入、输出、
异常、事件、附件和序列化模板的出站裁剪。
