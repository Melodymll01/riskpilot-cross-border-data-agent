# ADR-013: 审计端口副作用语义 + extra_json 自由 dict

- 状态: accepted
- 日期: 2026-06-05（追溯 Step 021 落地决策）
- 关联：[ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)、[ADR-012: Admin RBAC](ADR-012-admin-rbac-allowlist.md)

## 背景

Step 021 要给 admin 在 `/api/v2/documents/*` 上的写操作（delete / ingest_file / ingest_web）落审计。设计空间：

1. 审计是**主流程的一部分**（写失败 = 业务失败回滚）还是**副作用**（写失败仅打日志）？
2. 审计字段是**固定 schema**（按字段建索引）还是**自由 JSON**（容纳任意上下文）？
3. 审计**只 admin 可读**还是**用户可看自己**？
4. 是否要从一开始留 `request_id` / `trace_id` 字段？
5. KB use case 现有调用者（测试 + v1）不传 audit_log 怎么办？

## 决策

### D1：审计是「副作用」而非「主流程」

```python
def delete_document(self, source_name, *, actor_id=None) -> int:
    try:
        deleted = self._repo.delete_document(source_name)
    except Exception as exc:
        self._record_audit(success=False, error=str(exc), ...)
        raise  # 业务异常正常抛
    self._record_audit(success=True, extra={"deleted_count": deleted}, ...)
    return deleted

def _record_audit(self, ...):
    if self._audit is None:
        return  # bypass
    try:
        self._audit.record(AuditEntry(...))
    except Exception:
        logger.warning("audit log write failed ...", exc_info=True)
        # 不抛！
```

**审计写失败 ≠ 业务失败**。理由：
- 审计基础设施挂掉不应让业务也挂（OS 磁盘满、SQLite 锁竞争）
- 可观察性优先于完整性：宁可丢一条审计也别丢一条业务请求
- 业务异常本身仍会冒泡到 HTTP 层，由 API 错误处理器响应

### D2：固定 schema（6 字段）+ 自由 `extra_json: dict[str, Any]`

```python
class AuditEntry(BaseDomainModel):
    actor_id: str         # 索引
    action: str           # 索引（kb.delete / kb.ingest_file / kb.ingest_web）
    resource: str         # 字符串描述（source_name / url）
    timestamp: float = Field(default_factory=time.time)
    request_id: str | None = None  # 留位（见 D4）
    success: bool
    error: str | None = None
    extra_json: dict[str, Any] = Field(default_factory=dict)  # 自由！
```

固定字段保证可索引、可按字段过滤；`extra_json` 容纳调用方上下文（`deleted_count` / `chunk_count` / `category`），不强制 schema。

SQLite 存储：`extra_json` 列 `TEXT` + `json.dumps` / `json.loads`。

### D3：`/audit/logs` 是 admin-only

```python
@router.get("/logs", response_model=AuditLogListResponse)
def list_audit_logs(..., _admin_id: str = Depends(require_admin)):
    ...
```

普通用户/匿名拿 401/403。理由：
- 审计内容可能涉及他人操作（即使本人产生的也涉及"我被记录了什么"）
- 普通用户看到自己的审计 = 零业务价值 + 多一份"我是不是被监控"焦虑
- "user-facing 登录历史"是另一套独立设计（actor=自己 + 子集字段 + 可选导出），将来单独立 ADR

### D4：`request_id` 字段先「留位」不强制透传

```python
request_id: str | None = None  # 当前总是 None
```

HTTP 层目前**不**强制注入。理由：
- 当前 FastAPI 无中间件给每个请求生成 `request_id`
- 硬接需引入 middleware + 改 deps + 改 logger format（大改动）
- 字段位置预留，未来某 Step 做 distributed tracing 时**无须改 schema 也无须 migration**

### D5：`audit_log=None` 静默跳过（向后兼容）

```python
class KbManagementUseCase:
    def __init__(self, *, ..., audit_log: AuditLogPort | None = None) -> None:
        self._audit = audit_log

    def _record_audit(self, ...):
        if self._audit is None:
            return
        ...
```

Step 016b 写好的 `KbManagementUseCase` 测试用例（不传 audit_log）保持零修改通过。新调用方（如 v2 容器）传入 `audit_log=container.audit_log`。

### D6：「删除不存在的资源」use case 视角 = 成功幂等，API 层判 404

```python
deleted = uc.delete_document("nope", actor_id=...)
# → 0，audit success=True，extra={"deleted_count": 0}

# API 层
if deleted == 0:
    raise HTTPException(404, ...)
```

理由：
- `delete` 在领域里是幂等操作（"目标状态：不存在"），0 表示"本次没改"是 SQL DELETE 语义
- HTTP 404 是协议层友好提示，不污染领域语义
- 审计站 use case 视角：操作完成无异常 = `success=True`，细节在 `extra_json.deleted_count` 自助判断

## 后果

**正面**：
- **业务零依赖审计**：审计 SQLite 表掉了，KB 操作照常
- **可观察性强**：成功 + 失败双路径都落审计，可后期复盘任何 admin 操作
- **schema 演化友好**：`extra_json` 写自由，新增上下文不需要 migration
- **向后兼容**：老 use case 测试零回归（519 → 519 passed 验证）
- **future-proof**：`request_id` 字段已留，distributed tracing 可零侵入接入
- **隐私边界清晰**：admin-only 读取，避免用户隐私顾虑

**负面**：
- 审计可能丢条目（虽极少）—— 不适合做"强合规"项目；本项目定位"展示 + 可追溯"足矣
- `extra_json` 自由 = 调用方约定不严格（如 `deleted_count` vs `count` 不一致），需文档约定
- `request_id` 字段当前总是 null，对 user 端来说是噪音字段（admin 不在意）

## 不变量（端口契约）

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

- **不**暴露 `update` / `delete` API（审计要求可追溯不可变）
- `record` 同步阻塞；写失败抛异常（由调用方决定如何兜底，典型策略是吞错 + warning）
- `list_recent` 按 `timestamp` 倒序；过滤参数 None 表示不过滤

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 审计是主流程（写失败回滚业务） | 审计基础设施挂掉不应让业务挂 |
| 完全固定 schema | 上下文（chunk_count / category / ...）爆炸式增长，schema 迁移噩梦 |
| 完全自由 JSONB | 核心字段无索引，按 actor / action 查询慢 |
| 普通用户可看自己审计 | 隐私顾虑 + 零业务价值 + 与 admin 视图重复 |
| 强制 `request_id` 必填 | 当前无 middleware 生成 ID；硬上需大改 |
| 强制 KbManagement 必须接 audit_log | 老测试需大量重写，向后兼容差 |

## 关联

- [ADR-006: 4 层架构](ADR-006-4-layer-architecture.md)
- [ADR-012: Admin RBAC](ADR-012-admin-rbac-allowlist.md)
- 实现：`domain/{models,ports}.py`、`infra/audit/sqlite_audit_repo.py`、`app/use_cases/kb_management.py`、`api/v2/audit.py`
- 过程：[Step 021](../process/step_021_admin_audit_log.md)
