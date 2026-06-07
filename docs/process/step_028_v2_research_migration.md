# Step 028 — v1 检索武器迁移到 v2（二）：深度研究（research）能力迁移

## 1. 本步骤目标

v1 退役迁移三步走的第二步。审计发现：v2 的 `research` 模式**名存实亡**——
`RunCopilotUseCase.stream()` 里 `qa` 与 `research` 走的是**同一个** `ComplianceCopilotAgent.run()`
（ReAct 会话循环），没有任何差异化。而 v1 真正的「深度研究」能力在
`retrieval/agent/agentic_rag.py:AgenticRAGAgent` 里：

```
问题分类(含 OOD 软判决) → 查询变换 → 多轮检索+证据充分性判定 → 联网补齐 → ReportGenerator 生成长报告
```

本步把这套已验证的 Agentic 研究引擎，通过新增 `ResearchPort` 接进 v2，让
`mode == "research"` 真正产出**长篇结构化报告**（区别于 `qa` 的会话式回答）。

设计上**完全镜像已有的 `profile` 分支**：profile 模式跳过 agent 直接调
`RiskProfilePort.assess()`；research 模式跳过 agent 直接调 `ResearchPort.research()`。

关键风险：v1 `AgenticRAGAgent.__init__` 会 new 一串组件，且本步注入 `build_reranker()`
（Step 027）会**同步加载 ~1GB CrossEncoder**。沿用 Step 027 的**懒加载**策略：
适配器首次 `research()` 才装配 v1 引擎，避免容器构造 / `from main import app` 阻塞。

## 2. 修改文件

| 文件 | 说明 |
|---|---|
| `domain/models.py` | 新增 `ResearchStep`（决策步骤）+ `ResearchReport`（报告正文 + `Citation` + 元数据：question_type / rounds / web_used / refused / steps） |
| `domain/ports.py` | 新增 `ResearchPort` Protocol：`research(query, *, top_k=8, enable_web_search=True) -> ResearchReport` |
| `infra/research/agentic_research.py` | 新建 `AgenticResearchAdapter`：**懒加载**包装 v1 `AgenticRAGAgent`，注入 `build_reranker()`；`AgenticRAGResult → ResearchReport` 映射（citation 字段缺失兜底非空） |
| `infra/research/__init__.py` | 导出 `AgenticResearchAdapter` |
| `app/factories.py` | 新增 `build_research()` 工厂 + `ResearchPort` 导入/导出 |
| `app/container.py` | 装配 `self.research` Port，注入 `RunCopilotUseCase`（Port 数 11→12） |
| `app/use_cases/run_copilot.py` | `stream()` 新增 `mode == "research"` 分支 → `_run_research()`：决策步骤渲染成 `thought`、报告正文渲染成 `answer`（带 citations） |
| `tests/fakes/fake_research.py` | 新建 `FakeResearch`（预设 ResearchReport + 记录调用） |
| `tests/app/test_run_copilot.py` | 新增 `TestResearchMode` 3 用例 |
| `tests/infra/test_agentic_research.py` | 新建 5 用例（result→report 映射 / 透传 report 模式 / citation 兜底 / 懒加载） |

## 3. 设计决策

- **D1 镜像 profile 分支，不改 ReAct agent**：v2 已有 `profile` 模式跳过 agent 直接调 Port
  的先例。research 复用同一模式（新 Port + use case 分支），与既有架构一致，零侵入 agent 主循环。
- **D2 复用 v1 引擎而非重写（Strangler Fig）**：v1 `AgenticRAGAgent` 的分类/OOD/证据分级/
  报告生成是经长期验证的核心资产。与 Step 027 复用 `reranker.py` 同理——v2 拥有**编排**
  （Port/Adapter），复用**引擎**（retrieval/agent 模块）。这些引擎模块 Step 029 不删。
- **D3 懒加载（沿用 Step 027）**：适配器 `_ensure_agent()` 首次 `research()` 才 new v1 引擎 +
  `build_reranker()`，避免 `from main import app`（live + 生产 lifespan）被 1GB 模型阻塞。
  注入 agent（测试）则零模型加载。
- **D4 决策步骤可视化**：v1 `AgenticRAGResult.steps` 翻译成 `thought` 事件流，前端能看到
  「分类 → 改写 → 多轮检索 → 证据检查 → 生成」的推理链路，而非干等一个长报告。
- **D5 不做持久化（对齐 profile）**：profile 分支只 yield answer 不写 task_repo；research
  亦然，保持本步范围最小。research 报告入对话历史可作后续增强（须给 use case 注入 task_repo）。

## 4. 验证

| 项 | 命令 | 结果 |
|---|---|---|
| 新增单测 | `pytest tests/infra/test_agentic_research.py tests/app/test_run_copilot.py -q` | 19 passed |
| 全量 | `pytest -q` | **642 passed, 1 skipped**（较 Step 027 +8） |
| 静态 | `ruff check`（改动文件） | All checks passed |
| Live 端到端 | `RUN_LIVE=1` 经容器 `run_copilot.stream(mode="research")` | ✅ 事件序列 `task_created → 9×thought → answer`；报告 3830 字、2 条引用、标题「# 数据出境安全评估制度深度解析与合规路径研究报告」 |

Live 证明：`mode="research"` 经 `ResearchPort → AgenticResearchAdapter → v1 引擎 →
ResearchReport → AgentEvent`，端到端产出长篇结构化报告，与 `qa` 会话式回答显著区分。

## 5. 后续

- Step 029：删 v1 `api/routes.py` 检索/会话端点 + `service.py` + `KnowledgeService`
  （v1 `/api/research` 入口随之删除；本步已让 v2 接管深度研究能力）。
- 可选增强：research 报告持久化进对话历史；research 模式 live 用例进 `tests/live/`。
