# ADR-004: Mock-first 离线测试策略

- 状态: accepted
- 日期: 2026-06-04

## 背景

项目依赖多个外部服务：智谱 / OpenAI LLM、Embedding API、HuggingFace Reranker、Evidence vLLM 服务、Web 搜索、GitHub OAuth。每个都需要密钥或 GPU。如果测试依赖这些，CI 无法跑、贡献者无法贡献、开发体验差。

## 决策

**所有外部跨进程边界 100% 用 Fake 实现，所有自己写的代码 100% 测真实路径。**

具体规则：
- ✅ Mock：OpenAI/智谱 API、HF Reranker 加载、Evidence HTTP、Web 搜索、GitHub OAuth、`requests` 网页抓取
- ❌ 不 Mock：BM25Index、Splitter、Retriever 编排、RuleEngine、Cleaner

所有 Fake 集中在 `tests/fakes/`，所有离线数据集中在 `tests/fixtures/`。

## 后果

**正面**：
- `pytest -q` 一行命令跑全部测试，不需要任何 secret
- CI 无需配置环境变量
- 测试稳定、快速、可重复
- 体现"工程纪律"，是开源项目门面

**负面**：
- Fake 与真实实现可能漂移（缓解：用 `responses` 在 integration 层验证真实 HTTP 客户端）
- 检索质量评估必须依赖 evaluations/ 体系单独跑，不能进 CI

## 备选方案

- **依赖真实服务 + skip 标记**：CI 不可用，贡献门槛高，否决
- **录制-回放（VCR.py）**：录制脆弱、维护重，复杂度不值

## 关联

- `tests/conftest.py`、`tests/fakes/`、`tests/fixtures/`
- `experiment_v1.md` §7
