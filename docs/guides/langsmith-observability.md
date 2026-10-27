# LangSmith 追踪与隐私边界

LangSmith 通过 `TracePort` 接入当前对话链。默认 `NoopTraceAdapter` 不联网；显式启用后，同一轮运行形成以下父子关系：

```text
riskpilot.copilot.run
├─ LangChain / LangGraph model 与 tools 子调用
├─ search_law | search_user_docs | web_search
└─ riskpilot.citation.verify
```

可观察内容包括：哈希后的 owner/task/run/thread、阶段、工具名、调用次数、耗时、证据数量、最终 answered/partially_answered/refused、失败类型，以及模型实际返回时才存在的 token usage。

不可观察内容包括：用户问题正文、模型回答、附件正文、检索原文、Prompt、凭证、可逆用户标识和内部思维链。适配器在出站前清空 inputs/outputs、serialized、events 和 attachments，并把错误文本替换为固定字符串。因而 trace 成功只表示运行完成，不表示答案正确，也不能用作完整内容回放。

```ini
RISK_PILOT_LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=<真实密钥>
LANGSMITH_PROJECT=riskpilot
LANGSMITH_SAMPLING_RATE=0.1
LANGSMITH_HASH_SALT=<至少16字符的独立随机值>
```

不要设置 `LANGSMITH_TRACING` 或 `LANGCHAIN_TRACING_V2`；配置门禁会拒绝这些绕过项目适配器的全局开关。

本地协议验证：

```bash
.venv/bin/python -m pytest -q \
  services/agent-runtime/tests/infra/test_langsmith_tracing.py \
  services/agent-runtime/tests/infra/test_otel_observability.py \
  services/agent-runtime/tests/infra/test_prometheus_metrics.py
```

这些测试使用 Fake client 或内存 exporter，不代表已经连接真实 LangSmith。真实平台联调必须单独记录项目、时间、公开问题、模型身份与可见 span，并确认未上传正文。
