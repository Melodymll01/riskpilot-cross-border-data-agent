# ADR-010: Strangler Fig：v1 / v2 双 API 并存策略

- 状态: accepted
- 日期: 2026-06-05（追溯 Step 010-011 落地决策）
- 关联：[ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)（被本 ADR 增强）、[ADR-009: Closure Router + Container DI](ADR-009-closure-router-container-di.md)

## 背景

Step 001-006 完成 4 层架构后，老代码 `service.KnowledgeService` + `api/routes.py` 仍然挂在 `main.py` 服务用户。要把所有功能直接迁移到 `api/v2/*` 风险太大：

- 老路由还有 `/api/qa` `/api/upload` `/api/tasks` 多端点在用
- 前端老代码（`frontend/*.legacy.*`）依赖老 API 契约
- 评测脚本（`evaluations/`）调老接口

直接全量替换 = 长 PR + 大爆炸；需要"边开边修"的渐进迁移策略。

## 决策

采用 [Martin Fowler 的 Strangler Fig 模式](https://martinfowler.com/bliki/StranglerFigApplication.html)：**新功能只走 `/api/v2/*`，老功能保持原状直到自然死亡，两套并存于同一 FastAPI 进程**。

### 路径前缀分离

```python
# main.py
app.include_router(legacy_router, prefix="/api")          # 老
app.include_router(build_v2_router(container), prefix="/api/v2")  # 新
```

### 异常处理器按 path_prefix 守门

```python
# api/v2/errors.py
def install_exception_handlers(app: FastAPI, *, path_prefix: str = "/api/v2") -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request, exc):
        if not request.url.path.startswith(path_prefix):
            raise exc  # 老路径继续走 FastAPI 默认 {"detail": ...} 契约
        return JSONResponse(...)  # 新路径走 ErrorResponse{error_code, message}
```

老 v1 不被 `DomainError → JSONResponse` 改造影响；新 v2 走结构化错误。

### 老代码冻结策略

- `service.py` / `api/routes.py` **不**重构，**不**加新功能
- 修 bug 只在影响生产时；新需求一律走 `/api/v2`
- 前端逐步迁移，老前端文件 `git mv` 到 `*.legacy.*` 保留 1-2 个 Step 作为参考（Step 012）
- 评测脚本不动（`evaluations/` 走老 v1）

### 共享层

- 数据库（SQLite + Chroma）共享 —— 同一份 owner_id 既被老 v1 写、也被 v2 读
- 配置 `config.Settings` 共享
- domain 层共享（v1 老 service 直接用 `User` / `Task` 等 frozen 模型）

## 后果

**正面**：
- **零停机迁移**：每个 Step 都是可独立部署的小 PR
- **风险隔离**：v2 出问题 v1 兜底；v1 在用的端点不被 v2 重构波及
- **决策可逆**：发现 v2 设计偏差，回滚单步成本低
- **演进可见**：path 前缀直观区分新旧，运维 / 监控可分通道
- **前端可双轨**：Step 012 时新前端用 v2，老前端保留 *.legacy.* 即时回滚

**负面**：
- 短期内 main.py 同时挂两套路由，初读者会疑惑"为什么有两个 /tasks"
- 共享数据库要求 v2 不破坏 v1 假设（如 schema 列只能加不能删；通过幂等迁移落实）
- 老代码长期不维护可能积技术债（已通过 Step 020 CI 复活把测试钉死，防止 v1 静默坏掉）

## Strangler Fig 步骤记录

| Step | 动作 | v1 状态 | v2 端点 |
|---|---|---|---|
| 010 | v2 路由 + SSE 落地 | 全活 | auth/tasks/copilot/health |
| 011 | main.py 接 v2 + path_prefix 守门 | 全活 | + 集成测试 |
| 012 | 前端切 v2，老前端 legacy 化 | 全活 | 同上 |
| 013-015 | admin / mode / risk_profile（仅 v2） | 全活 | + 三模式 |
| 016a-c | KB 重构（仅 v2 走 KbDocumentRepoPort） | KB 老逻辑保留 | + documents |
| 017-019 | KB 前端 + 权限拆分 | 不动 | documents 权限矩阵 |
| 020 | CI 复活（pytest 同时跑 v1 + v2） | CI 守门 | CI 守门 |
| 021 | 审计端口 + /api/v2/audit | 不动 | + audit |
| 022+ | （计划）逐个清算 v1 端点 | TBD | TBD |

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 直接全量重写 | 长周期 + 大爆炸 + 期间用户不可用 |
| Feature Flag 路径切换 | 仍需保留老代码；增加运行时分支复杂度 |
| 网关层分流（Nginx）| 单 FastAPI 进程内已能解决；引网关复杂度过剩 |
| 完全冻结 v1（不修 bug） | 评测仍依赖 v1，短期不能停 |

## 退出条件

当满足以下条件，进入"Strangler 收割"阶段（删 v1）：

1. 所有评测脚本迁移到 v2 调用
2. 前端无 `*.legacy.*` 引用
3. v2 持续 N 周（待定）零生产 bug
4. 老 `service.py` / `api/routes.py` 无独立功能（已全部 mirror 到 v2）

当前（Step 021）三条件均未满足，v1 继续保留。

## 关联

- [ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)
- [ADR-009: Closure Router + Container DI](ADR-009-closure-router-container-di.md)
- 实现：`main.py`、`api/v2/router.py`、`api/v2/errors.py`
- 过程：[Step 010](../process/step_010_pr6_api_layer.md)、[Step 011](../process/step_011_pr6_main_integration.md)、[Step 012](../process/step_012_pr7_frontend_copilot_ui.md)
