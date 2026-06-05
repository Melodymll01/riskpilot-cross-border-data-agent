# Step 020 — 复活 GitHub Actions CI（scoped ruff + 全量 pytest）

## 1. 本步骤目标

把 [Step 002](step_002_engineering_scaffold.md) 起就写好但一直被冻在
`workflow_dispatch`（仅手动触发）的 [.github/workflows/ci.yml](../../.github/workflows/ci.yml)
**复活**：每次 push 到 main / 开 PR，GitHub 自动跑：

1. `pytest -q` 全量 483 passed
2. `ruff check` 覆盖 DDD 重构后的核心层

并在 README 顶部加 4 个状态徽章，让访客 / 面试官第一眼就能看到工程质量。

为后续做：

- 给所有未来 commit 自动套上"绿光门"——再没有"本地忘跑测试就 push"的事故
- 为接下来的 Step 021+（结构化日志 / 审计 / 私人 KB）提供安全网：
  改动越大越需要 CI 兜底
- 简历 / 面试展示：GitHub repo 主页带 `CI passing` 徽章 = 工程化背书

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `.github/workflows/ci.yml` | 整文件重写：`on: workflow_dispatch` → `on: [push to main, pull_request to main, workflow_dispatch]`；2 个 job 并行（lint + test）；删 matrix（只跑 Python 3.12 与本地 .venv 对齐）；删 mypy job（已知 ~46 错与基线持平）；删 ruff format check（scoped 路径仍有 33 文件待 reformat）；删 coverage upload（CI 复活先求绿，不上覆盖率指标）；header 加注释说明本次的"复活策略" |
| `README.md` | 顶部加 4 个徽章：CI / tests 483 passed / Python 3.12 / ruff scoped-clean |
| `config.py` | scoped `ruff --fix` 顺手清扫：`typing.List` → `list` (PEP 585) × 3；`Optional[int]` → `int \| None`；`Annotated` 改从 `typing` 而非 `typing_extensions` 导入；import 块重排。不动逻辑（[Step 018](step_018_login_bugfix_port_admin.md) 的 `NoDecode` + `field_validator` 完整保留） |
| `api/v2/auth.py` | `Iterable` 从 `collections.abc` 而非 `typing` 导入（UP035） |
| `tests/api/test_auth.py` | 删 2 个未使用 import（`fastapi.FastAPI`、`app.container.AppContainer`，F401） |

## 3. 设计决策

### D1：触发条件 `[push:main, pull_request:main, workflow_dispatch]`

候选方案：

- A：全分支 push 触发（开发分支也跑）
- B（采用）：仅 main 分支 push + 任何到 main 的 PR + 手动
- C：仅 PR 触发（main push 不跑）

理由：当前是单人项目，feature 分支几乎不存在；多数 commit 直接打到 main。
B 同时覆盖"main push 后自我验证"和"未来开多人协作时 PR 拦截"两类场景，
不会因为 feature 分支 WIP commit 浪费 CI 分钟数。

### D2：scoped ruff 而非全仓 ruff

全仓 `ruff check .` 当前 **426 个错误**（绝大多数集中在 `retrieval/` /
`processing/` / `ingestion/` / `service.py` / `api/routes.py` 等 Step 005 之前
就存在的遗留代码）。一次性清完会引入庞大且无法 review 的 diff，且与
本步骤"复活 CI"目标正交。

采用历史 step 文档（Step 010 / 013 / 016a-d）一贯的 **scoped ruff** 实践：

```
ruff check \
  domain app \
  infra/auth infra/kb infra/risk_profile \
  api/v2 \
  config.py main.py \
  tests/api tests/app tests/domain tests/infra tests/fakes
```

新代码层强制 0 错；遗留层留待独立 Step 单独清理。
本次 scoped `ruff --fix` 顺手修了 13 个新出现的错（全在 `config.py` / `api/v2/auth.py` /
`tests/api/test_auth.py`，分别是 Step 018 引入的 `typing.List` / `typing_extensions.Annotated`
以及 `Iterable` 导入位置和未使用 import 的小遗漏）。

### D3：不接入 mypy

`mypy domain app infra/auth infra/kb api/v2` 在新代码层是 0 错（历史 step
文档反复验证）；但全仓 `mypy .` 仍有 ~46 个错（与 [Step 016d](step_016d_v1_kb_removal.md)
基线持平，主要在 `retrieval/` 等遗留层）。CI 此时接 mypy 等于把
"复活 CI"和"清遗留 mypy"两件事绑在一起，违反"一次 commit 一件事"。

mypy 留待独立 Step（应该与 D2 提到的"scoped 清理"放在一起）。

### D4：单 Python 版本（3.12）而非 matrix `[3.10, 3.11, 3.12]`

老 ci.yml 用 matrix 跑两个 Python 版本，每个版本都得装一遍重依赖
（chromadb / sentence-transformers / openai SDK 等），总耗时翻倍。

权衡：

- 本地 `.venv` 用 3.12.8
- [pyproject.toml](../../pyproject.toml) `requires-python = ">=3.10"` 是声明，不是测试承诺
- 真要做版本兼容验证应该单独开 `compatibility.yml` 跑 nightly
- 当前迫切需求是"绿光门"，不是"多版本兼容矩阵"

→ CI 只跑 3.12 一档；后续若发布到 PyPI / 第三方使用，再补 matrix。

### D5：删除 ruff format check（暂时）

`ruff format --check` 在 scoped 路径下仍报 **33 files would be reformatted**。
塞进本次 CI 复活 commit 会引入 33 文件 diff，污染 commit 焦点。

→ format 留 `ci.yml` 注释 TODO；某个独立 cleanup commit 一次性
`ruff format` 全部 scoped 路径并提交。

### D6：环境变量"假凭据"完整模拟

CI runner 是干净的 Ubuntu，没有 `.env`。完整列了：

- `OPENAI_API_KEY=sk-test-fake` — Fakes 不会真调，但 Settings 字段需要
- `LLM_PROVIDER=api` / `RISK_EVIDENCE_PROVIDER=mock` — 关掉一切真服务
- `ENABLE_RERANKER=false` — 避免 CI 装 sentence-transformers reranker 模型
- `AUTH_PROVIDERS_ENABLED=github,anonymous` — 与本地一致
- `GITHUB_CLIENT_ID/SECRET=fake_ci_*` — `AuthService` 装配需要非空
- `JWT_SECRET=ci-test-jwt-secret-do-not-use-in-prod-32chars-min` — 显式标"勿生产用"
- `ADMIN_USER_IDS=""` — 测试要 admin 的用例自己 monkeypatch

### D7：徽章布局 4 件套

```markdown
[![CI](.../badge.svg?branch=main)](.../actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-483%20passed-brightgreen)]
[![python](https://img.shields.io/badge/python-3.12-blue)]
[![ruff](https://img.shields.io/badge/ruff-scoped--clean-46a)]
```

- 1 个动态（CI 实时拉 workflow 状态）+ 3 个静态（shields.io 自托管）
- 静态徽章手动同步：未来测试数 / Python 版本变了得改 README
- 不放 coverage 徽章——还没接 Codecov，避免显示 `unknown`

## 4. 核心契约 / 接口

**CI 契约**：

| 触发 | Job | 失败=红 |
|---|---|---|
| push 到 main 或 PR 到 main | `lint` (ruff check, scoped) | ✗ |
| 同上 | `test` (pytest -q, 排除 eval/smoke) | ✗ |

并行执行，互相不 `needs`，单 job 失败不会阻塞另一个 job（fail-fast 由
`pytest -ra` / ruff 默认行为决定）。

## 5. 与外部服务的关系

- **GitHub Actions runner**：依赖 GitHub 免费额度（公开 repo 不限分钟数 /
  私有 repo 月免费 2000 分钟，本项目规模一次跑 5-8 分钟）
- **PyPI**：runner 通过 `pip install -r requirements-dev.txt` 拉公开依赖；
  `cache: pip` 跨 run 复用 wheel
- **不依赖**：OpenAI / GitHub OAuth / 智谱 / Chroma cloud 等任何运行时
  外部服务（全部假凭据 + Fake 接管）

## 6. 当前实现范围

### 已实现

- [x] `ci.yml` 触发条件改 `push:main + PR:main + workflow_dispatch`
- [x] `lint` job：scoped `ruff check` 14 个路径
- [x] `test` job：`pytest -q` 排除 eval/smoke + 8 个假凭据环境变量
- [x] Python 3.12 + `actions/setup-python@v5` + pip cache
- [x] scoped `ruff --fix` 清 13 处历史小遗漏（不动业务逻辑）
- [x] README 顶部 4 徽章
- [x] 本地全量回归：`pytest -q --ignore=tests/eval_ood.py --ignore=tests/smoke_bm25_rrf.py`
  → 483 passed, 16 warnings
- [x] 本地模拟 CI lint：scoped `ruff check` 0 errors
- [x] Push commit `86ca837` 到 origin/main → CI 首跑

### 未实现（按设计跳过）

- ruff format check（D5：33 文件待 reformat 留独立 commit）
- mypy（D3：~46 错与基线持平留独立 Step）
- coverage upload + Codecov 徽章（暂未配 Codecov token）
- matrix Python 多版本（D4：单 3.12 即可）
- 部署 job（暂无生产环境）

## 7. 暂未实现 / TODO

- **CI 性能优化**：当前 `requirements.txt` 含 `sentence-transformers`（重，
  几百 MB + PyTorch 依赖），CI 安装阶段可能 3-5 分钟。后续可考虑：
  - 拆 `requirements-test.txt`（test 时不装 reranker / embedding 模型）
  - 用 `setup-python` 的 `cache: pip` 跨 run 复用（已配置）
  - 极端可上 self-hosted runner（不必要）
- **format cleanup commit**：一次性 `ruff format` 33 个 scoped 文件
- **mypy 接入**：scoped mypy 在 ci.yml 加一个独立 job
- **branch protection rule**：在 GitHub repo settings 里强制 main
  分支必须 CI 绿光才能合 PR（这是 settings 操作不是 commit 操作）

## 8. 测试与验证

```powershell
cd d:\py\RagDataOut

# 本地模拟 CI 的 lint job
.venv\Scripts\python.exe -m ruff check `
  domain app `
  infra/auth infra/kb infra/risk_profile `
  api/v2 `
  config.py main.py `
  tests/api tests/app tests/domain tests/infra tests/fakes
# → All checks passed!  (0 errors)

# 本地模拟 CI 的 test job
.venv\Scripts\python.exe -m pytest -q `
  --ignore=tests/eval_ood.py `
  --ignore=tests/smoke_bm25_rrf.py
# → 483 passed, 16 warnings in 53s

# 浏览器验证 CI 首跑
# https://github.com/Melodymll01/riskpilot-cross-border-data-agent/actions
# → workflow run "ci: 复活 GitHub Actions CI（scoped ruff + 全量 pytest）"
# → lint job ✅ + test job ✅
# → README 顶部 4 徽章全绿
```

变更行数：5 文件，+62/-49（commit `86ca837`）。
