# ADR-003: 风险画像 evidence 模型作为独立 HTTP 服务

- 状态: accepted
- 日期: 2026-06-04

## 背景

风险画像依赖一个微调过的 Qwen2.5-7B + LoRA 模型（孪生项目 `schema-evidence-risk-profiling`），用于判断"文档对某 target 的证据状态"。该模型：

- 需要 GPU + vLLM 部署
- 与 RagDataOut 主应用生命周期解耦
- 应能被其它项目复用

## 决策

evidence 模型部署为独立 FastAPI + vLLM 服务（默认端口 8001），RagDataOut 通过 `EvidencePort` 抽象调用：

- `infra/evidence/HTTPEvidenceClient`：调真实服务
- `infra/evidence/MockEvidenceClient`：关键词正则确定性 fake，用于离线开发与 CI

两种实现可通过 `RISK_EVIDENCE_PROVIDER=mock|http` 配置切换。

## 后果

**正面**：
- 主应用无需 GPU 即可开发（mock 模式）
- 模型迭代不需要重启主应用
- 体现"模型即服务"的工程思路，面试可讲

**负面**：
- 多了一次跨进程调用（约 +500ms~2s 延迟）
- 部署需 docker-compose 或两台机器

## 备选方案

- **进程内调用**：耦合死，否决
- **gRPC**：性能更好但调试门槛高，v1 暂用 HTTP

## 关联

- `risk/evidence_client.py`
- 孪生项目：`D:\py\schema-evidence-risk-profiling`
- [ADR-004: Mock-first 测试](ADR-004-mock-first-testing.md)
