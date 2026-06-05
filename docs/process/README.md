# RagDataOut Process Index

本文档记录 RagDataOut 每一步开发过程，用于复盘、代码评审与面试展示。
每步对应一个 `step_NNN_<topic>.md`，README 本身只维护索引大表。

状态枚举：`Pending` / `In Progress` / `Done` / `Blocked` / `Needs Real Service`。

| Step | Module | Status | Main Files | Test Command | Result | Conclusion | Next |
|---|---|---|---|---|---|---|---|
| [001](step_001_design_v1_freeze.md) | Design v1.1 Freeze | Done | `docs/experiment_v1.md`, `docs/decisions/ADR-001..008.md`, `docs/architecture/overview.md` | — (doc only) | 8 ADR + ~1550-line spec 冻结 | 对话式 Copilot + GitHub OAuth + 匿名双轨 + 4 层架构方案定稿；7 PR 拆分明确 | Step 002 工程基建 |
| [002](step_002_engineering_scaffold.md) | Engineering Scaffold (PR-1) | Done | `requirements-dev.txt`, `pytest.ini`, `pyproject.toml`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `Makefile`, `docs/README.md` | `pip install -r requirements-dev.txt && pytest -q` | 未运行（待 step_004 启动前建立基线） | dev 依赖 / lint / type / format / pre-commit / CI / Makefile / 文档骨架就位；不动业务代码 | Step 003 gitignore 审查 |
| [003](step_003_gitignore_audit.md) | .gitignore Engineering Audit | Done | `.gitignore` | `git check-ignore -v docs/...` | 4 个 docs 文件均未被忽略 | 补齐 Python 标配缺项（.tox/.nox/.hypothesis/coverage.lcov/*.prof/.envrc/.python-version/*.orig/*.rej）；显式声明 docs/decisions / docs/process / docs/architecture / evaluations/**/datasets 为受跟踪 | Step 004 测试基线 |
| [004](step_004_baseline.md) | Test & Lint Baseline | Done | — (env only) | `pytest -q --no-cov` + `ruff check . --statistics` | **88 passed** / 437 ruff errors（347 auto-fixable） | venv 补齐（chromadb 1.5.9 用 binary-only wheel）；存量绿基线 88/88；ruff 高频项 UP006(202)/I001(54)/F401(44) 留给后续模块 PR 清理 | Step 005 PR-2 domain 层 |
| [005](step_005_pr2_domain_layer.md) | PR-2 Domain Layer | Done | `domain/{__init__,errors,models,ports}.py`, `tests/domain/test_models.py` | `pytest -q --no-cov` + `ruff check domain tests/domain` + `mypy domain` | **123 passed**（+35）/ ruff 0 / mypy 0 | 11 models + 9 Ports + 14 errors 全部落地；frozen + extra=forbid + JSON round-trip 不变量验证通过；不动业务代码、零回归 | Step 006 PR-3 infra 层 + 测试基建 |
| [006](step_006_infra_layer.md) | PR-3 Infra Layer + Test Fakes | Done | `infra/{storage,chat,search,web,evidence}/*`, `tests/{fakes,infra}/*`, `requirements.txt` | `pytest -q --no-cov` + `ruff check infra tests/infra tests/fakes` + `mypy infra` | **175 passed**（+52）/ ruff 0 / mypy 0 | 7 个 Port 适配器（SqliteUserRepo/SqliteTaskRepo/OpenAIChat/Embedder/HybridRetriever/DuckDuckGo/MockEvidence）+ 7 个 Fake 全部落地；适配器 isinstance 契约校验通过；老 API 端到端 0 回归；requirements 与 venv 对齐 | Step 007 PR-4 Auth 层（GitHub OAuth + JWT + 匿名） |
| [007](step_007_pr4_auth_layer.md) | PR-4 Auth Layer | Done | `infra/auth/*`, `tests/infra/test_{jwt_issuer,anonymous,github_oauth,auth_service,fake_auth}.py`, `tests/fakes/fake_auth.py` | `pytest -q --no-cov` + `ruff check infra/auth tests/infra tests/fakes/fake_auth.py` + `mypy infra/auth` | **224 passed**（+45）/ ruff 0 / mypy 0 | `AuthService` 组合 `JwtIssuer`+`GitHubOAuthProvider`+`AnonymousProvider`，state 自管（防 CSRF+replay+TTL），JWT 注入 clock 自校 exp，GitHub HTTP 用 `responses` 隔离；FakeAuth/FakeOAuthProvider 双层 Fake 就位 | Step 008 PR-5 App/Use-case 层 |

## 写作规范

- **文件名**：`step_NNN_<snake_topic>.md`，NNN 从 001 起递增、零填充三位
- **每步必含小节**（参考 phonepilot `docs/process/step_001_core_schema.md` 模板）：
  1. 本步骤目标（为什么存在 / 服务于哪层 / 为后续提供什么）
  2. 修改文件（精确路径 + 一句话说明）
  3. 设计决策（关键技术选择 + 替代方案）
  4. 核心契约 / 接口
  5. 与外部服务的关系
  6. 当前实现范围（已实现 / 未实现按设计）
  7. 暂未实现 / TODO
  8. 测试与验证（命令 + 输出）
- **更新 README 大表**：每完成一个 step 追加一行；Status / Result / Conclusion / Next 必填
- **不事后美化**：失败的尝试、走错的路也要写
- **不复述代码**：贴文件路径与命令即可，diff 以 git 为准

## 原则

- 一个用户 prompt 触发的一段实际工作 ≈ 一个 step
- 文档 / ADR / 设计冻结也算 step（无代码可省略 §4 §8 但其他小节齐全）
- 大表是入口，正文是细节，二者必须一致
