# RiskPilot V2 迁移基线

> 本文件记录 2026-08-06 重构开始时的历史快照，不代表当前实现状态。当前进度见
> `docs/design/riskpilot-v2.md` 第 18 节和 `docs/architecture/overview.md`。

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
OPENAI_API_BASE=http://127.0.0.1:9/v1 \
CHAT_API_KEY=sk-test-fake \
CHAT_API_BASE=http://127.0.0.1:9/v1 \
LLM_PROVIDER=api \
RISK_EVIDENCE_PROVIDER=mock \
ENABLE_RERANKER=false \
HF_HUB_OFFLINE=1 \
TRANSFORMERS_OFFLINE=1 \
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
1260 passed
1 skipped
```

离线隔离：

- API 测试容器显式注入 `FakeResearch`，research 模式不会构造
  `AgenticResearchAdapter`、Embedder 或真实模型客户端；
- CI 使用假凭据只用于配置校验，OpenAI 兼容端点固定为本机丢弃端口，且 Hugging Face /
  Transformers 强制离线，任何测试都不得向公网 API 发请求；
- `tests/eval_ood.py` 是显式 live 评测，`tests/smoke_bm25_rrf.py` 是重 IO smoke，
  两者不属于离线 pytest 门禁。

后续提交的验证规则：

1. 新增模块先运行对应的聚焦测试；
2. 再运行离线全量测试；
3. 不允许通过 deselect 具体测试用例维持绿灯；
4. 如果出现真实网络调用或失败，当前步骤不得提交；
5. live / smoke 脚本只能按文件级明确排除并记录原因。

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

## 2026-08-11 进度检查点

- `/api/v2` 仍可用，`/api/v3` 已覆盖 Workspace、Case、Document、Evidence、Fact、
  Policy、Assessment 和 Assessment Run；
- 上传文件已经成为案件级、可版本化证据；
- 已实现只消费 confirmed facts 的版本化规则引擎；
- 已实现不可变 Assessment、Reviewer/Admin 审批和 Case 状态原子同步；
- 已实现 AgentRun、RunCheckpoint、RunEvent、乐观锁和连续事件；
- 已接入 LangGraph 1.x 与 SQLite checkpointer，支持中断恢复、失败重试、取消和
  进程重建后继续；
- 已实现 `/api/v3/qa`，支持公共法规、Workspace Knowledge、Case Evidence 和
  Assessment 四类授权范围，并执行结构覆盖与独立语义支持双校验；
- 已实现文档 Fact 提议：字段白名单、当前版本证据复核、同字段冲突检测、批量原子写入
  和 Reviewer/Admin 唯一确认；
- 已实现 Assessment 引用闭包：Finding 关联 Fact / Evidence / Clause 快照，生成和批准前
  重新验证 Fact 版本、DocumentVersion、SHA、quote 和 offset；
- 已实现原生 V3 案件工作台最小闭环：按 Case ID 加载 Run 中断、生成 Fact 候选、
  展示证据、Reviewer 确认并继续运行；
- 最新离线回归为 `1260 passed, 1 skipped`，无具体用例 deselect，research 模式已由
  `FakeResearch` 完整隔离真实 embeddings / LLM 外呼；
- GitHub Actions 已恢复 `main` push / pull request 自动触发，Ruff 覆盖 Domain、App、
  V2/V3 API、QA/Workflow 适配器、Evidence QA 评测器及对应测试。
