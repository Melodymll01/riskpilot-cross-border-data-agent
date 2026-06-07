# Step 027 — v1 检索武器迁移到 v2（一）：CrossEncoder 精排接入

## 1. 本步骤目标

v1 退役迁移三步走的第一步。审计发现：v2 生产检索路径
`HybridRetrieverAdapter` 实例化 `Retriever` 时**只传 `Embedder + VectorStore`**，
reranker 退化为 `Retriever` 默认的 `DistanceThresholdReranker`（仅按余弦距离过滤）；
而 `.env` 里 `ENABLE_RERANKER=true` + `RERANKER_MODEL=BAAI/bge-reranker-base` 的配置
**根本没被 v2 遵守**——只有 v1 `KnowledgeService.__init__` 里那段内联选择逻辑在用。

本步把 v1 的「reranker 选择逻辑」提取成工厂 `build_reranker()`，接进 v2，让 v2 检索
真正用上 CrossEncoder 精排。**迁移动作本身即提质**：v2 答案相关性直接提升。

关键风险：CrossEncoder 构造会**同步加载 ~1GB 模型**。若在容器构造时加载，会阻塞
`from main import app`（live 测试 + 生产启动全卡）。故采用**懒加载**：首次 `retrieve`
才构造 Retriever 并加载模型。

## 2. 修改文件

| 文件 | 说明 |
|---|---|
| `retrieval/search/reranker.py` | 新增 `build_reranker() -> BaseReranker` 工厂：`enable_reranker=True` → `CrossEncoderReranker`（加载失败回退 `DistanceThresholdReranker`）；`False` → `DistanceThresholdReranker` |
| `infra/search/hybrid_retriever.py` | `HybridRetrieverAdapter` 改**懒加载**：`__init__` 不再立即 new Retriever；新增 `_ensure_retriever()` 首次 `retrieve` 时构造并注入 `build_reranker()` |
| `tests/retrieval/test_reranker_factory.py` | 新建 4 单测：disabled→距离阈值 / enabled→CrossEncoder(假) / 透传 settings / 加载失败回退 |
| `tests/infra/test_hybrid_retriever.py` | 新增 `TestLazyRetriever` 2 用例：构造不触发懒构造（`_retriever is None`）/ 注入 stub 直接用不懒构造 |

## 3. 设计决策

- **D1 懒加载而非容器构造即加载**：CrossEncoder 同步加载 1GB 模型，必须延迟到首次检索，
  否则 `from main import app` 阻塞（live 测试、生产启动全卡）。`_ensure_retriever()` 在
  首个 `retrieve` 调用时一次性构造。
- **D2 提取 `build_reranker()` 工厂而非复制逻辑**：v1 `KnowledgeService` 那段「enable →
  CrossEncoder / 失败回退」逻辑提取为单一工厂，v2 复用。CrossEncoder 加载失败必须回退
  （无模型/无网/无 torch 时不能让检索整体挂掉）。
- **D3 不动 v1 `service.py`**：v1 将在 Step 029 整体删除，现在不重构它的内联逻辑（范围最小化，
  避免无谓改动即将删除的代码）。v1 那段重复逻辑随 v1 一起删。
- **D4 query_rewriter / BM25-RRF 不需迁移**：审计确认 v2 的 `Retriever` 默认值已含
  `QueryRewriter`，BM25-RRF 由 `settings.enable_bm25_rrf=True` 在 `Retriever` 内部启用。
  唯一缺口就是 CrossEncoder，本步补齐。
- **D5 不扩散修 `reranker.py` 既有 ruff 债**：该文件有 20 处 `typing.List`/`zip` 旧式写法
  （早于 ruff scoped 清扫，不在 CI scope）；本步只新增干净的 `build_reranker`，不顺手重构
  旧注解（避免触碰 CrossEncoder 核心逻辑 + 无关 churn）。

## 4. 核心契约 / 接口

- `build_reranker() -> BaseReranker`：读 `settings.{enable_reranker, reranker_model,
  reranker_device, reranker_score_threshold}`，返回 CrossEncoder 或 DistanceThreshold。
- `HybridRetrieverAdapter._ensure_retriever() -> _RetrieverLike`：懒构造单例。注入式
  （`retriever=` 非 None）完全绕过，测试 0 模型加载。

## 5. 与外部服务的关系

- **reranker 模型**：`BAAI/bge-reranker-base`（HuggingFace，~1GB），首次检索从 HF 下载/缓存加载；
  `RERANKER_DEVICE=auto` → 检测到 CUDA 走 GPU。live 实测 `device=cuda`，重排 17 候选，
  分数区间 [0.194, 0.999]。
- 不影响 embedding（智谱）/ chat（百炼 GLM-5）通道。

## 6. 当前实现范围

- 已实现：v2 检索接入 CrossEncoder 精排 + 懒加载防启动阻塞 + 6 新测试。
- 按设计未做：v1 `service.py` 内联 reranker 逻辑（留 Step 029 随 v1 删除）；research 能力迁移
  （Step 028）。

## 7. 暂未实现 / TODO

- Step 028：v1 `AgenticRAGAgent` 深度研究能力迁移到 v2 `research` mode（当前 research 与 qa
  走同一 agent 循环，名存实亡）。
- Step 029：v1 检索/conversations 端点 + `service.py` + `KnowledgeService` 整体删除
  （需先处理 `evaluations/benchmark` 对 `KnowledgeService` 的依赖）。

## 8. 测试与验证（命令 + 输出）

```powershell
# 新增/改动测试
.\.venv\Scripts\python.exe -m pytest tests/retrieval/test_reranker_factory.py tests/infra/test_hybrid_retriever.py -q
# → 16 passed

# 默认全量（live 自动 skip）
.\.venv\Scripts\python.exe -m pytest -q
# → 634 passed, 1 skipped（较 026e +7：4 reranker 工厂 + 2 懒加载 + 1 原有）

# live 真服务端到端（需真 key）：验证 CrossEncoder 真接入
$env:RUN_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -m live -q -s
# → 1 passed in 87s（日志佐证：Cross-Encoder 重排序 17→17 条 device=cuda 分数 [0.194,0.999]）

# 静态检查（新增代码）
.\.venv\Scripts\python.exe -m ruff check infra/search/hybrid_retriever.py tests/retrieval/test_reranker_factory.py tests/infra/test_hybrid_retriever.py
# → All checks passed（reranker.py 20 处既有旧式注解债不在本步范围）
```
