# Step 003 - .gitignore 工程审查

## 1. 本步骤目标

回应用户问题「`docs/decisions` 是什么？是不是该被忽略？」，对 `.gitignore` 做一次工程审查：
- 澄清 `docs/decisions/` 与 `docs/process/` 的工程语义（**必须进 git**）
- 补齐 Python 项目 `.gitignore` 标配缺项
- 在 `.gitignore` 内显式声明哪些文件夹是"受跟踪档案"，防止后续误改

本步骤为什么存在：

- 用户对 `docs/decisions` / `docs/process` 的工程定位不清楚，怀疑应该忽略。
- 原 `.gitignore` 缺 `.tox/` / `.nox/` / `.hypothesis/` / `coverage.lcov` / `*.prof` / `*.orig` / `*.rej` 等标配。
- 没有在 `.gitignore` 内说明"哪些反而必须跟踪"，未来贡献者可能误加 `docs/` 或 `evaluations/` 到 ignore 列表。

## 2. 修改文件

- `.gitignore`（改）：3 处补充：
  1. Python 段：新增 `.tox/` / `.nox/` / `.hypothesis/` / `coverage.lcov` / `*.prof` / `pip-log.txt` / `pip-delete-this-directory.txt`
  2. 虚拟环境段：新增 `.envrc`（direnv）/ `.python-version`（pyenv）
  3. 文件尾部新增「合并/补丁残留」段 `*.orig` / `*.rej`，以及「显式跟踪」注释块声明 `docs/decisions/` / `docs/process/` / `docs/architecture/` / `evaluations/**/datasets/` 为受跟踪 + `!.gitkeep`

## 3. 设计决策

| 文件夹 | 是否进 git | 工程定位 |
|---|---|---|
| `docs/decisions/` | ✅ 必须进 | ADR（Architecture Decision Records）决策快照，write-once，写完就不动 |
| `docs/process/` | ✅ 必须进 | 开发过程留痕，每步一个 `step_NNN_*.md`，面试复盘材料 |
| `docs/architecture/` | ✅ 必须进 | 架构图与跨模块设计 |
| `evaluations/**/datasets/` | ✅ 必须进 | 评测输入数据集，可复现性的一部分 |
| `evaluations/**/reports/` | ❌ 不进 | 评测输出，每次重跑会变 |
| `data/uploads/` | ❌ 不进 | 用户上传内容，含 PII |
| `data/chroma_db/` | ❌ 不进 | 向量库 SQLite，每次 ingest 会变 |

决策原则：**"为什么这么选"的文档进 git；"每次运行会变的产物"不进 git。**

## 4. 核心契约 / 接口

无。

## 5. 与外部服务的关系

无。

## 6. 当前实现范围

已实现：

- `.gitignore` 3 处补充全部落地
- 通过 `git check-ignore -v` 验证 `docs/decisions/*.md` / `docs/process/*.md` / `docs/architecture/*.md` 均未被忽略

## 7. 暂未实现 / TODO

无。

## 8. 测试与验证

```powershell
git check-ignore -v docs/decisions/ADR-001-no-langchain.md `
                    docs/process/step_001_design_v1_freeze.md `
                    docs/experiment_v1.md `
                    docs/architecture/overview.md
# 期望：所有 4 个文件均"未匹配任何 ignore 规则"（exit code 1）
```

实际输出：

```text
OK: 上述文档均未被忽略
```

验证通过。`git status --short` 显示 `docs/` 在 untracked 列表，待 commit 即跟踪。
