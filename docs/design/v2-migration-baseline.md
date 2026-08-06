# RiskPilot V2 迁移基线

## 记录时间

2026-08-06

## Git 基线

- 分支：`main`
- 起始提交：`37e828f`
- 工作树：开始重构前干净

## 本地环境

- 使用 `uv` 创建 `.venv`
- Python：3.12.13
- 开发依赖：`requirements-dev.txt`
- `.venv/` 已被 `.gitignore` 忽略

## 测试命令

```bash
OPENAI_API_KEY=sk-test-fake \
LLM_PROVIDER=api \
RISK_EVIDENCE_PROVIDER=mock \
ENABLE_RERANKER=false \
AUTH_PROVIDERS_ENABLED=github,anonymous \
GITHUB_CLIENT_ID=fake_ci_client_id \
GITHUB_CLIENT_SECRET=fake_ci_client_secret \
JWT_SECRET=ci-test-jwt-secret-do-not-use-in-prod-32chars-min \
ADMIN_USER_IDS='' \
.venv/bin/python -m pytest -q \
  --ignore=tests/eval_ood.py \
  --ignore=tests/smoke_bm25_rrf.py
```

## 基线结果

```text
796 passed
1 skipped
1 failed
```

存量失败：

```text
tests/api/test_copilot.py::TestChatMode::test_explicit_research_mode_persisted
```

原因：

- 用例进入真实 `AgenticResearchAdapter`；
- 测试配置中的假 API Key 被用于请求智谱 embeddings；
- 服务返回 401；
- 这是重构开始前已存在的离线测试隔离问题，不属于 V2 新增回归。

后续提交的验证规则：

1. 新增模块先运行对应的聚焦测试；
2. 再运行离线全量测试；
3. 不把上述存量失败计为新回归；
4. 如果失败数量增加，当前步骤不得提交；
5. 存量失败应在独立提交中修复，避免混入 V2 业务实现。

## 当前架构事实

- `/api/v2` 是现行 API；
- QA 使用 `ComplianceCopilotAgent` 自研 ReAct；
- Research 使用旧 `AgenticRAGAgent`；
- Profile 默认绑定 `StubRiskProfileService`；
- 用户已经可以上传 PDF、TXT、DOCX 到私人知识库；
- 上传文件尚未成为案件级、可版本化证据；
- `HybridRetrieverAdapter` 尚未真正下推 `corpus` 和 `filters`；
- `ToolSpec.timeout_s` 已定义但未实际执行；
- `TaskState` 已定义但没有驱动完整业务状态机；
- README 引用的旧 `docs/` 曾从 Git 中移除。

## 迁移守则

- `/api/v2` 在替代门槛达成前保持可用；
- `/api/v3` 只装配已完成且已测试的切片；
- 一个可验证步骤对应一个中文 commit；
- 不在功能提交中顺手清理无关遗留问题；
- 领域层不得依赖 FastAPI、LangGraph 和具体数据库。
