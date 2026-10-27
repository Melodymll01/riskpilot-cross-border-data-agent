# 第一阶段清理与可运行基线

日期：2026-10-06。这是迁移前的历史验证记录，范围以现已迁至 `docs/reviews/current.md` 的对话产品目标为准。清理前工作区仅有未跟踪的评审文档；本次按要求更新该文件，没有提交/改写 Git 历史，没有清空数据库或删除用户原始上传材料。

## 清理前

执行 `.venv/bin/python -m pytest -q`：**1425 passed, 4 skipped**，59.82 秒。跳过项分别要求真实 PostgreSQL、真实 MinIO、PostgreSQL 并发服务和显式开启的 live RAG；默认测试不调用真实模型。

## 实际删除和解耦

| 范围 | 主要删除位置 | 共享能力处理 |
| --- | --- | --- |
| 企业 Workspace、Case、审批、Assessment、报告/异步产物 | `api/v3/`、原业务用例/领域模块、`app/agent_tools/`、`app/renderers/`、`infra/workflows/`、对应 repositories | 文档、Evidence QA 改为用户 owner 授权 |
| 独立研究、外部风险画像、图片向量召回 | `infra/research/`、`infra/risk_profile/`、`infra/visual/`、专用 API/模型/工具 | 保留核心 Copilot 的三种检索工具和内部工具循环 |
| 长期记忆 | fact/profile/consolidation/settings adapters、相关 API/前端/评测 | 保留当前会话最近消息、滚动摘要及 owner 校验 |
| 备用生产部署与旧会话实现 | `infra/storage/sqlalchemy/`、`migrations/`、S3/Celery/checkpoint adapters、`data/chat_db.py` | 沿用 SQLite、Chroma、本地对象文件；版本化附件使用新的 `chat_*` 表 |
| 配套资料 | 旧企业 Demo/轨迹评测/浏览器流程、旧设计/ADR/阶段报告和失效指南 | 保留当前检索、引用校验评测，重写运行和架构文档 |

SQLite 的 `CREATE TABLE IF NOT EXISTS` 仍负责当前建库。删除 PostgreSQL 迁移的原因是其后端及企业 schema 已退出当前运行范围；没有向用户数据库执行 DROP 或删除数据。没有建立 archive/legacy 或兼容 API。

## 清理后检查

最终入口：`make ci PY=.venv/bin/python`。

| 检查 | 结果与实际覆盖 |
| --- | --- |
| pytest | **797 passed, 1 skipped**；保留的 live RAG 需要 `RUN_LIVE=1` 与真实配置 |
| Ruff + format | 通过，249 个 Python 文件格式检查 |
| mypy | 通过，84 个 source files；沿用仓库 domain/app/infra 检查范围 |
| Evidence QA oracle | 13 条协议样本通过全部 gates，`production_evidence=false` |
| 真实 SQLite / 文件链路 | 原生解析器、本地原件、后台线程、页级索引、任务状态、消息持久化和重新读取均通过；embedding 与模型为 Fake |
| 真实 Chroma / BM25 / RRF | 公共法规、本人私人材料、其他用户三个范围隔离通过；查询向量和查询改写为本地确定性测试实现 |
| 实际应用导入/装配/启动 | 隔离临时目录中运行真实 `uvicorn main:app`；readiness 200、29 个 OpenAPI paths，退出日志包含 `Application shutdown complete.` |
| Headless Chromium | 聊天、附件入口、新会话和事件渲染通过，JavaScript 错误 0；SSE 内容由浏览器拦截模拟，不是模型回答 |
| Compose | `config --no-env-resolution` 解析通过，只剩 app，持久目录及启动配置契约通过 |
| 静态一致性 | 内部模块导入、前端模块引用、Markdown 相对链接、`.env.example` 字段与 Git diff 空白检查通过 |

两个可复现的新增集成用例：

- 当时的对话与附件基线：实际 LangChain 工具调用使用 Fake 模型；覆盖真实 SQLite、原生文件解析、异步处理、附件下载、多轮上下文、工具落库、跨用户禁止访问与消息重新读取。该旧测试文件已随双服务职责收敛删除，不作为当前路径入口。
- [真实混合检索基线](../../services/agent-runtime/tests/retrieval/test_conversation_retrieval_baseline.py)：真实 Chroma/BM25/RRF 和 owner 过滤，避免将 user_docs 当作 law 备用分支。

保留功能测试中的旧权限夹具已迁移到 owner，引用篡改/来源漂移/伪造引用/结构与语义校验仍有回归；仅删除退出范围的专用测试。原始基线数量和最终数量代表不同产品范围，不能用数量差计算覆盖率提升。

## 未验证项与原因

- **真实模型对话及答案质量**：仓库没有 `.env`，当前启动配置检查明确缺少 `CHAT_API_KEY/OPENAI_API_KEY` 和 embedding key。隔离启动显式使用 local 通道，只验证服务装配，未调用模型服务。live RAG 测试保持跳过。
- **Docker 镜像构建/容器运行**：Docker daemon 不可用，`docker info` 返回 `/var/run/docker.sock: no such file or directory`。已验证配置解析，未启动 Docker Desktop 或宣称容器成功。
- **真实 GitHub OAuth、外部网页搜索和追踪平台**：本阶段使用 Fake/本地协议测试；未进行真实外部认证、搜索或 trace 出站。
- **真实模型 OCR/重排服务效果**：保留解析、OCR、重排的现有回归，但未新增真实模型下载或效果评测。附件实测基线采用 UTF-8 文本；现有 PDF/DOCX 解析测试单独保留。

最后保留的五条 pytest warning 来自 PyMuPDF SWIG 类型的 DeprecationWarning，不是用例失败。

## 运行与后续边界

本地启动：配置 `.env` 后 `make serve PY=.venv/bin/python`，默认 `http://127.0.0.1:8765`。当前 `.env.example` 中的 key 是占位值，直接启动会明确拒绝缺失凭据；健康检查就绪不证明模型或法规语料可用。

当时记录的后续工作包括真实来源绑定、完整引用校验接入、会话附件持久关联、精确检索范围、多轮/摘要质量、正文增量、取消与全局时间预算，以及小规模真实效果评测。当前交付边界见[评审记录](../reviews/current.md)。

本阶段没有模型驱动的 CLI 调用；没有运行用户的 standalone codex。
