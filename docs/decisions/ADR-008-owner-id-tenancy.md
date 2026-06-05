# ADR-008: 采用 owner_id 作为统一身份键与数据隔离边界

- 状态: accepted（augmented by ADR-012）
- 日期: 2026-06-04
- 后续补充：
  - [ADR-012: Admin RBAC](ADR-012-admin-rbac-allowlist.md)（admin 白名单是 owner_id 体系之上的第二轴守门）

## 背景

匿名身份与登录身份必须能被同一套数据模型表达，否则迁移逻辑会爆炸。同时所有"用户私有数据"（任务、上传文档、记忆）必须严格隔离。

## 决策

**统一身份键 `owner_id: str`，命名空间前缀区分来源**：

- 匿名：`anon:{uuid4}`
- GitHub：`github:{login}`
- 未来 Google：`google:{sub}`
- 未来 Magic Link：`email:{sha256(addr)}`

**所有用户私有数据表都以 `owner_id` 为第一索引**：

```sql
CREATE INDEX idx_tasks_owner ON tasks(owner_id, updated_at DESC);
CREATE INDEX idx_user_docs_owner ON user_documents(owner_id);
CREATE UNIQUE INDEX uq_user_profile ON user_profiles(owner_id);
```

**Chroma `user_docs` collection 的每条 chunk metadata 必含 `owner_id`，所有检索强制 `where={"owner_id": ...}` 过滤**。

**API 层中间件统一注入** `request.state.owner_id`，业务路由通过 FastAPI Depends 强制声明：

```python
def require_owner(request: Request) -> str:
    return request.state.owner_id
```

未传 owner_id 的 RetrievePort 调用应抛 `MissingOwnerError`。

## 后果

**正面**：
- 加新 provider 只需新前缀，不动数据模型
- 数据隔离由数据库索引和检索过滤双重保证
- 匿名 → 登录数据迁移仅需 `UPDATE ... SET owner_id=...`
- 共享知识库（法规 `law_corpus`）与用户私有库（`user_docs`）天然分离

**负面**：
- 需要规范所有代码路径必须传 owner_id（用类型 + 中间件强制）
- Chroma metadata 更新批量迁移开销（大用户可能慢，但匿名期数据通常不大）

## 备选方案

- **整数 user_id（自增主键）**：迁移时主键变化、外部引用断裂，否决
- **email 作为主键**：匿名场景无 email，否决
- **分库隔离**：复杂度爆炸，否决

## 关联

- [ADR-007: GitHub OAuth + 匿名](ADR-007-github-oauth-with-anonymous.md)
- `experiment_v1.md` §5.2、§5.3
