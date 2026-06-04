# Documentation Index

本目录是 **RagDataOut** 的设计与决策档案。所有重大设计、技术选型、过程留痕统一在此。

## 子目录

| 路径 | 用途 |
|---|---|
| [`experiment_v1.md`](experiment_v1.md) | v1 重构主设计方案（活文档） |
| [`architecture/`](architecture/) | 架构总览与子系统设计 |
| [`decisions/`](decisions/) | ADR（Architecture Decision Records）—— 一项一篇 |
| [`process/`](process/) | 开发过程留痕 —— 每个 PR / 阶段一篇 |
| `evaluations/` | 评估报告总结（链接到 `evaluations/`） |

## 阅读顺序建议

1. 想了解项目目标 → [experiment_v1.md §1](experiment_v1.md#1-项目定位与产品形态)
2. 想了解整体架构 → [architecture/overview.md](architecture/overview.md)
3. 想知道为什么这样选型 → [decisions/](decisions/)（按编号读）
4. 想看实施进度 → [process/README.md](process/README.md)

## 文档约定

- **ADR**：一旦合并不再修改；过时的用 `superseded by ADR-XXX` 标记。
- **process 留痕**：写实记录，包含背景 / 决策 / 实现 / 验证 / 遗留 / 时长。
- **architecture**：图文并茂，跟随重构同步更新。
