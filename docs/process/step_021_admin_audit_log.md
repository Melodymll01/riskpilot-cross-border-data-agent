# Step 021 — admin 操作审计日志（AuditLogPort + SqliteAuditLogRepo + KbManagement hook + `/api/v2/audit/logs`）

> 状态：**Done**
> Commit：（本提交）
> 范围：domain → infra → app → api/v2 全套；写入路径覆盖 KB 三个写操作（delete / ingest_file / ingest_web）；admin-only 只读查询端点。
> 测试：**519 passed**（基线 483 + 本步 36）/ scoped ruff 0 / 不动 mypy job（与 Step 020 一致）

---

## 1. 目标

把 admin 在 `/api/v2/documents/*` 上的写操作（删除文档 / 上传文件 / 抓网页）落成结构化、可追溯、可按字段过滤的审计流水：

- **不可篡改**：审计端口只暴露 `record` + `list_recent`，不开 update/delete API
- **结构化**：固定字段（actor_id / action / resource / timestamp / success / error）+ 自由 `extra_json` 容纳上下文（chunk_count / category / deleted_count）
- **副作用语义**：审计写失败**不**回滚业务、**不**影响响应；仅 `logger.warning`
- **admin-only 查询**：审计内容可能涉及他人操作，普通用户/匿名都看不到（即使是自己产生的）

> 与散点 `logger.info` 的边界：散点日志面向开发者 / 运维做现场排错；审计端口面向合规、按字段索引、长期留存。

---

## 2. 改动文件清单

### 新增（4）

| 文件 | 用途 |
|---|---|
| `infra/audit/__init__.py` | 导出 `SqliteAuditLogRepo` |
| `infra/audit/sqlite_audit_repo.py` | `AuditLogPort` 的 SQLite 实现，构造时幂等执行 `_SCHEMA_AUDIT`（表 + 2 索引） |
| `tests/fakes/fake_audit_log.py` | `FakeAuditLogRepo` in-memory 同语义实现 |
| `api/v2/audit.py` | `build_audit_routes(container)` —— 单端点 `GET /audit/logs` |
| `tests/infra/test_sqlite_audit_repo.py` | 6 用例：round_trip / failure / request_id / order DESC / limit / filter+combine / 幂等迁移 / Protocol 契约 |
| `tests/api/test_audit.py` | 6 用例：401 / 403 / 200 empty / DESC / filter action / filter actor / limit 边界 |
| `docs/process/step_021_admin_audit_log.md` | 本文档 |

### 修改（10）

| 文件 | 改动 |
|---|---|
| `domain/models.py` | + `AuditEntry`（BaseDomainModel 子类 = frozen + extra="forbid"） + `AuditAction` 常量类（KB_DELETE/KB_INGEST_FILE/KB_INGEST_WEB） |
| `domain/ports.py` | + `AuditLogPort`（runtime_checkable Protocol，2 方法） |
| `domain/__init__.py` | 导出 `AuditEntry`/`AuditAction`/`AuditLogPort` |
| `app/factories.py` | + `build_audit_log(settings, *, pool=None) -> AuditLogPort` |
| `app/container.py` | docstring `10→11 个 Port`；ctor 新增 `audit_log: AuditLogPort \| None = None`；池 gating 加入 `audit_log is None`；`self.audit_log = ...` 装配；`KbManagementUseCase(...)` 透传 `audit_log=self.audit_log` |
| `app/use_cases/kb_management.py` | ctor 加可选 `audit_log: AuditLogPort \| None = None`；3 个写操作各自在成功/失败分支调 `_record_audit`；`_record_audit` 内 `audit_log is None` 早返；audit 写失败仅 `logger.warning(exc_info=True)` 不抛 |
| `api/v2/schemas.py` | + `AuditEntryOut` + `AuditLogListResponse`（HTTP 层 schema，与 domain 解耦） |
| `api/v2/router.py` | wire `build_audit_routes(container)` |
| `api/v2/documents.py` | 3 个写端点 `_admin_id` → `admin_id` 并透传 `actor_id=admin_id` 到 use case |
| `tests/api/conftest.py` | `container` fixture 注入 `audit_log=FakeAuditLogRepo()` |
| `tests/fakes/__init__.py` | export `FakeAuditLogRepo` |
| `tests/domain/test_models.py` | + `TestAuditEntry`（8 用例）|
| `tests/infra/test_fakes.py` | + `FakeAuditLogRepo isinstance AuditLogPort` 契约 |
| `tests/app/test_kb_management.py` | + `TestAuditHooks`（8 用例）：bypass / delete success / delete idempotent / ingest_file success / loader 异常 / 空文档 / ingest_web success / 默认 actor |
| `tests/app/test_container.py` | docstring `10→11 个`，导入 `FakeAuditLogRepo`/`AuditLogPort` 并断言 |
| `tests/app/test_factories.py` | + `test_audit_log_satisfies_port` |
| `.github/workflows/ci.yml` | scoped ruff 路径列表 +`infra/audit` |
| `docs/process/README.md` | 追加 Step 021 行 |

---

## 3. 设计决策

### D1 — 审计是「副作用」而非「主流程」

KbManagement use case 的成功 / 失败路径分别记录审计，但都包在 try/except 里：

- 主操作抛异常 → 先记录 `success=False` + `error=str(exc)` → 再 raise（让 API 层走错误码）
- 审计 record 自身抛异常 → 仅 `logger.warning("audit log write failed", exc_info=True)`，**不二次抛**

理由：审计基础设施挂掉不应让业务也挂；可观察性优先于完整性。

### D2 — `extra_json` 保留为开放 dict

`AuditEntry.extra_json: dict[str, Any] = {}`，调用方放任意上下文：

```python
extra={"chunk_count": 4, "category": "法规"}      # ingest
extra={"deleted_count": 3}                         # delete
```

理由：审计字段稳定性（`actor_id` / `action` / `resource` 三件套）必须保证可索引、可过滤；细节（chunk count / category）无穷无尽，硬塞表结构会迭代成噩梦。SQL 持久化用 `json.dumps`，查询时 `json.loads`，前端展示就是 expandable 的 raw object。

### D3 — `request_id` 字段先「留位」不强制透传

`AuditEntry.request_id: str \| None`，HTTP 层目前**不**强制注入，留 `None`。

理由：当前 FastAPI 没装中间件给每个请求生成 request_id；硬接需引入 middleware + 改 deps + 改所有 logger format。本步只把字段位置留出来 + schema 输出，让未来某 Step 单独做 distributed tracing 时无须二次改 schema。

### D4 — 「删除不存在的资源」语义：use case 视角=成功幂等，API 层判 404

```python
deleted = uc.delete_document("nope", actor_id=...)   # → 0，audit success=True，extra={"deleted_count": 0}
if deleted == 0:                                      # ← API 层
    raise HTTPException(404, ...)
```

理由：`delete` 在领域模型里是幂等操作（"目标状态：不存在"），用 0 表示「本次没改任何东西」是 SQL DELETE 语义；HTTP 404 是协议层友好提示，不应污染领域语义。审计层站 use case 视角，所以记 `success=True`（操作完成无异常）+ 在 `extra_json` 暴露 `deleted_count=0` 让查询者可二次判断。

### D5 — `/audit/logs` 是 admin-only 而非 owner-only

虽然审计内容可能也包含「自己」的操作，但：

- 普通用户看到自己的审计 = 多一份「我是不是被监控」焦虑，零业务价值
- admin 看自己的也是 admin 在看，无新增风险
- 等 Step 014 之后真要做 user-facing 审计（如登录历史），那是另一套独立设计：actor=自己 + 子集字段 + 可选导出

所以本步保持最小：admin-only 全审计列表，普通用户/匿名拿到 401/403。

---

## 4. 接口契约

### domain port

```python
@runtime_checkable
class AuditLogPort(Protocol):
    def record(self, entry: AuditEntry) -> None: ...
    def list_recent(
        self,
        *,
        limit: int = 50,
        action: str | None = None,
        actor_id: str | None = None,
    ) -> list[AuditEntry]: ...
```

### HTTP 端点

```
GET /api/v2/audit/logs?limit=50&action=kb.delete&actor_id=github:Melodymll01
  → 200 AuditLogListResponse { entries: [...], count: int }
  → 401 AUTH_REQUIRED
  → 403 ADMIN_REQUIRED
  → 422 limit < 1 或 > 500
```

### 响应字段

```jsonc
{
  "entries": [
    {
      "actor_id": "github:Melodymll01",
      "action": "kb.delete",
      "resource": "PIPL.pdf",
      "timestamp": 1700000000.0,
      "request_id": null,
      "success": true,
      "error": null,
      "extra_json": { "deleted_count": 3 }
    }
  ],
  "count": 1
}
```

---

## 5. 不做（边界守门）

| 不做 | 原因 |
|---|---|
| 写审计的并发锁 | SQLite 默认 `BEGIN IMMEDIATE` + `INSERT` 已串行；无并发 hot path |
| 审计加密签名 / 防内部人篡改 | 当前不是合规级别项目；AuditLogPort 的 contract 写明「不暴露 update/delete」就够了 |
| `since` / `until` 时间范围过滤 | 当前 limit=500 + DESC 已能满足近似 day-range；真要 cron 拉 14 天再说 |
| 审计前端 UI | 留独立 Step；先把数据落下来更重要 |
| 审计指标 / Prometheus 导出 | 本项目无生产 obsv 栈；落地了再接也不晚 |
| KB 之外的端点（auth / tasks / copilot）落审计 | 写流程当前只在 documents；登录审计是另一套用户隐私边界 |
| 异步落盘（celery / queue） | KbManagement 已在 `anyio.to_thread.run_sync` 后台跑；audit 同步串行不增延迟感知 |
| mypy 入 CI | 与 Step 020 一致：基线 ~46 errors，留独立 cleanup Step |

---

## 6. 测试矩阵（+36）

| 层 | 文件 | 用例数 | 关键覆盖 |
|---|---|---|---|
| domain | `tests/domain/test_models.py::TestAuditEntry` | 8 | frozen / extra forbidden / 默认 timestamp / success+failure / extra_json 透传 / request_id / AuditAction 常量 / JSON round-trip |
| infra | `tests/infra/test_sqlite_audit_repo.py` | 9 | 双向序列化 / failure / request_id / DESC ordering / limit / filter action / filter actor / 联合 filter / 幂等迁移 / Protocol 契约 |
| infra | `tests/infra/test_fakes.py` | 1 | `FakeAuditLogRepo isinstance AuditLogPort` |
| app | `tests/app/test_factories.py` | 1 | `build_audit_log` 返回 Port |
| app | `tests/app/test_container.py` | 1 | `container.audit_log isinstance AuditLogPort` |
| app | `tests/app/test_kb_management.py::TestAuditHooks` | 8 | audit_log=None bypass / delete success / delete idempotent / ingest_file success / loader 异常 / 空文档 / ingest_web success / 默认 actor 兜底 |
| api | `tests/api/test_audit.py` | 8 | 401 / 403 / 200 empty / DESC / 双向字段（success+error） / filter action / filter actor / limit 边界 422 |

---

## 7. 验证

```powershell
# 全量回归
.venv\Scripts\python.exe -m pytest -q --ignore=tests/eval_ood.py --ignore=tests/smoke_bm25_rrf.py
# → 519 passed

# scoped ruff（已加 infra/audit）
.venv\Scripts\python.exe -m ruff check `
    domain app `
    infra/auth infra/kb infra/risk_profile infra/audit `
    api/v2 `
    config.py main.py `
    tests/api tests/app tests/domain tests/infra tests/fakes
# → All checks passed!
```

---

## 8. 下一步候选

- **Step 022a**：审计 UI（admin 侧栏第 3 视图：表格 + filter 表单 + extra_json 折叠展示）
- **Step 022b**：私人 KB owner_id 隔离（每个用户上传到自己 namespace；admin 看全部）
- **Step 022c**：mypy 复活（清 ~46 个基线 error 并接 CI）
- **Step 022d**：审计 `since` / `until` 时间过滤 + 分页 cursor
- **Step 022e**：登录审计（auth 端点也落 AuditLogPort）

按用户优先级选下一步。
