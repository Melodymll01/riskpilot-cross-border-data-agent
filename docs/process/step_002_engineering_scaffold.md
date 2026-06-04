# Step 002 - PR-1 工程基建

## 1. 本步骤目标

不动业务代码，只搭工程脚手架：依赖 / 测试 / lint / type / format / pre-commit / CI / Makefile / 文档骨架。
让后续每个 PR 落地前都有可执行的本地与 CI 验证基线。

本步骤为什么存在：

- 当前仓库只有 `requirements.txt` 与 `pytest` 配置，缺 dev 依赖、缺 lint/type、缺 CI、缺 pre-commit。
- ADR 与过程留痕文件夹尚未建立，决策无处归档。
- 不先把基建打好，后续 PR 的 diff 会同时包含「业务变更」与「工程踩坑」，难以代码评审、难以面试展示。

它服务于 RagDataOut 的全局工程层（贯穿所有目录）。

它为后续步骤提供的依赖：

- step_004+ 所有代码 PR：`pytest -q` / `ruff check` / `mypy` / `pre-commit` 本地与 CI 全绿。
- step_005+ 文档迭代：`docs/process/step_NNN_*.md` 已有索引（README.md）与目录约定。

## 2. 修改文件

17 新增 + 1 修改：

- `requirements-dev.txt`（新）：`-r requirements.txt` + `pytest>=8.0` / `pytest-asyncio>=0.23` / `pytest-cov>=5.0` / `httpx>=0.27` / `responses>=0.25` / `ruff>=0.6` / `mypy>=1.10` / `types-requests` / `types-python-dateutil` / `pre-commit>=3.7` / `PyJWT>=2.8`。
- `pytest.ini`（新）：`testpaths=tests` + `python_files=test_*.py smoke_*.py` + markers `unit/integration/e2e/slow` + `filterwarnings` 屏蔽 pydantic/protobuf 噪音。
- `pyproject.toml`（改）：移除旧 `[tool.pytest.ini_options]`；新增 `[project]` 元信息；新增 `[tool.ruff]`（line-length=100, target py310, selects `E/F/W/I/B/UP/SIM/C4`, extend-exclude `.venv/data/logs/evaluations/*/reports/frontend/interview_doc/prompts`）；`[tool.ruff.lint.per-file-ignores]` 放宽 tests/evaluations；`[tool.ruff.format]` 双引号；`[tool.mypy]` py3.10 lenient + 排除 evaluations/frontend/tests；`[[tool.mypy.overrides]]` 对 `domain.*` / `app.*` / `infra.auth.*` 启用 strict；`[tool.coverage.run]` branch=true source=`risk/domain/app/infra/api`。
- `.pre-commit-config.yaml`（新）：`pre-commit-hooks v4.6.0` + `ruff-pre-commit v0.6.9`（ruff --fix + format）+ `mirrors-mypy v1.11.2`（`files=^(domain|app|infra/auth)/`，避免对存量代码报噪音）。
- `.github/workflows/ci.yml`（新）：3 job（lint / type-check `continue-on-error` / test matrix py3.10+3.11）；CI env 全是假值：`OPENAI_API_KEY=sk-test-fake` / `LLM_PROVIDER=api` / `RISK_EVIDENCE_PROVIDER=mock` / `ENABLE_RERANKER=false` / `AUTH_PROVIDERS_ENABLED=github,anonymous` / `GITHUB_CLIENT_ID=fake_id` / `GITHUB_CLIENT_SECRET=fake_secret` / `JWT_SECRET=ci_test_secret_change_me`；跑 `pytest -ra --cov`，上传 `coverage.xml`。
- `Makefile`（新）：targets `help/install/install-dev/lint/format/type-check/test/test-cov/serve/clean/docker-*/hooks/ci`。
- `docs/README.md`（新）：文档目录导航 + 阅读顺序 + 写作约定。
- `docs/architecture/overview.md`（新）：一句话定位 + 4 层 + Agent + Auth 架构图 + 核心抽象表 + 身份模型 + ADR 索引 + 技术栈。
- `docs/decisions/ADR-001..008.md`（8 新）：见 step_001。
- `docs/process/README.md`（新）：本目录索引。

## 3. 设计决策

1. **`pytest.ini` 与 `pyproject.toml` 分家**：测试配置走 `pytest.ini`，避免 pyproject 节点冲突；其他工具仍走 pyproject 单一来源。
2. **mypy 渐进式 strict**：仅对新写的 `domain/` / `app/` / `infra/auth/` strict，存量目录 lenient，防止 PR-1 引入大面积红字。
3. **CI 全离线**：env 全为假值，`RISK_EVIDENCE_PROVIDER=mock`、`ENABLE_RERANKER=false`，确保 CI 不依赖任何外部模型 / 网络服务。
4. **Makefile 优先 / PowerShell 兜底**：Windows 下 `make` 默认不可用，记为遗留问题，PR-7 之前补 `scripts/*.ps1`。
5. **ADR 与 process 文件夹必须进 git**：通过 `.gitignore` 「显式跟踪」注释块固化（详见 step_003）。

## 4. 核心契约 / 接口

无代码契约。工程契约：

| 任务 | 命令 |
|---|---|
| 安装 | `pip install -r requirements-dev.txt` |
| 跑测 | `pytest -q` |
| lint | `ruff check .` |
| 格式化 | `ruff format .` |
| 类型 | `mypy .` |
| 启动服务 | `make serve` 或 `uvicorn main:app --reload` |
| 本地 CI | `make ci` |

## 5. 与外部服务的关系

不涉及外部服务。CI 的假 env 已经替代所有外部依赖。

## 6. 当前实现范围

已实现：

- 17 新文件 + 1 文件修改
- CI workflow 写好（实际触发待 PR-1 提交后验证）

未实现：

- ❌ `pip install -r requirements-dev.txt`（未在本地执行）
- ❌ `pytest -q` 基线（未运行；预计 PR-2 启动前跑一次）
- ❌ `ruff check .` 基线（未运行；预计首次会暴露存量代码 lint，PR-2 解决）
- ❌ Windows PowerShell 等价脚本

## 7. 暂未实现 / TODO

- ⏳ 跑一次 `pip install -r requirements-dev.txt` 与 `pytest -q` 建立基线
- ⏳ 跑一次 `ruff check .` 评估存量代码 lint 修复量
- ⏳ Windows 下 `scripts/*.ps1`（PR-7 处理）

## 8. 测试与验证

本步骤未跑代码测试。文件级验证：

- ✅ `git status` 显示 17 untracked + 2 modified（`.gitignore` 与 `pyproject.toml`）
- ✅ `docs/` 子目录齐备：`architecture/` / `decisions/` / `process/`
- ✅ `.github/workflows/ci.yml` 语法可被 GitHub Actions 解析（凭经验，未推送验证）
