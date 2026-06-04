# Step 004 - 测试基线建立

## 1. 本步骤目标

在 PR-2（domain 层）动代码前，确认现有原型的可执行基线，并量化 ruff 红字存量，
让后续每个 PR 能区分「我引入的回归」与「存量遗留」。

它服务于所有后续 step（004 之后每一个 step 的 §8 验证都要与本步骤的数字对比）。

## 2. 修改文件

无代码变更。仅环境与文档变更：

- 本文件：`docs/process/step_004_baseline.md`
- 更新 `docs/process/README.md` 索引大表

## 3. 设计决策

1. **不强制安装 `chromadb==0.5.23`**：该版本在 Win + Py3.12 下需要 MSVC 编译 `chroma-hnswlib`。
   改用 `chromadb>=0.5.23 --only-binary=:all:`，实际落地 `chromadb==1.5.9`（有 Windows wheel）。
   遗留：`requirements.txt` 的 pin 与实际安装版本不一致，PR-2/PR-3 处理（候选：升级 pin 或加 constraints 文件）。
2. **不修任何 ruff 红字**：本步骤只统计基线，修复留给对应模块的 PR 自然清理。
3. **CI 仍以 `requirements-dev.txt` 为准**：CI 用 Linux 容器有预编译 chromadb wheel，本地特例不需要传染。

## 4. 核心契约 / 接口

无。

## 5. 与外部服务的关系

无（全部离线）。

## 6. 当前实现范围

已建立：

- venv 可执行基线（详见 §8）
- ruff 存量红字快照
- chromadb 安装策略（仅二进制 wheel）

未实现：

- ❌ `requirements.txt` 与实际 chromadb 版本对齐（PR-2 处理）
- ❌ 修复任何 ruff 红字
- ❌ 跑 mypy（本地存量未类型化，留给 PR-2 仅对 domain/* 启用 strict 后再跑）

## 7. 暂未实现 / TODO

- ⏳ 对齐 `requirements.txt` 的 `chromadb` 版本（或新增 `constraints-win.txt`）
- ⏳ PR-2 起逐步消化 437 条 ruff 红字（高频项：`UP006` 202 / `I001` 54 / `F401` 44）
- ⏳ Windows 用户的 MSVC Build Tools 安装说明加入 `docs/README.md`

## 8. 测试与验证

### 命令

```powershell
pip install "pytest-asyncio>=0.23" "pytest-cov>=5.0" "responses>=0.25" `
            "ruff>=0.6" "mypy>=1.10" "types-requests" "types-python-dateutil" `
            "pre-commit>=3.7"
pip install openai slowapi python-multipart "PyPDF2==3.0.1" pdfplumber `
            python-docx beautifulsoup4 "rank-bm25==0.2.2" jieba diskcache `
            sentence-transformers "pydantic-settings==2.7.1"
pip install "chromadb>=0.5.23" --only-binary=:all:
pytest -q --no-cov
ruff check . --statistics
```

### pytest 输出

```text
88 passed, 4 warnings in 13.14s
```

绿色基线：**88 / 88**。

### ruff 存量（437 条）

| 数量 | 规则 | 说明 |
|---:|---|---|
| 202 | UP006 | `List[X]` → `list[X]`（PEP585） |
| 54 | I001 | import 未排序 |
| 44 | F401 | unused-import |
| 40 | UP035 | deprecated-import |
| 40 | UP045 | `Optional[X]` → `X \| None`（PEP604） |
| 22 | W291 | 行尾空格 |
| 14 | B904 | except 内 raise 缺 `from` |
| 6 | E402 | import 不在文件顶部 |
| 4 | F541 | f-string 无占位符 |
| 3 | E741 | 歧义变量名（如 `l`） |
| 3 | W293 | 空行含空格 |
| 7 | 其他 | B905 / E401 / F403 / F841 / UP015 |

`347 / 437` 可由 `ruff check . --fix` 自动修复，留待 PR-2 起在各自模块 PR 中清理。

### 环境实际版本

```text
Python 3.12.8
chromadb 1.5.9   (requirements.txt 锁的是 0.5.23，本地走 binary-only)
pytest 9.0.3
ruff 0.15.15
mypy 2.1.0
```
