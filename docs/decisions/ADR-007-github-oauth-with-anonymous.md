# ADR-007: GitHub OAuth + 匿名身份（双轨）

- 状态: accepted（augmented by ADR-012）
- 日期: 2026-06-04
- 后续补充：
  - [ADR-012: Admin RBAC](ADR-012-admin-rbac-allowlist.md)（Step 013/018/019 落地 admin 白名单 + 401/403 二段守门）

## 背景

记忆系统、任务历史必须绑定到某个"身份"才有意义。但作为面向技术招聘面试官 + 法律从业者演示的项目：

- **不能**强制注册（劝退试用）
- **不能**走邮箱 + 密码（合规、安全成本高，与项目主题无关）
- 又需要展示一个 production-grade 的认证流程

参考 OpenWebUI 等开源项目，常见方案是 email/password + OAuth + LDAP 全套。本项目选最贴合目标的子集。

## 决策

**双轨身份系统**：

1. **匿名身份**（默认）：首次访问由后端发一个 `anon:{uuid}`，JWT 写入 httpOnly cookie；前端额外存一份到 localStorage 用于跨 cookie 清理恢复
2. **GitHub OAuth**（升级）：一键登录，回调时把当前匿名身份的所有数据 `merge_owner` 到 `github:{login}` 下

JWT：HS256 + 30 天有效期；统一 `owner_id` 字段流转。

OAuth provider 抽象为 `AuthPort` + `infra/auth/providers/{github,anonymous}`，未来可插入 Google / Magic Link / 企业 SSO。

## 后果

**正面**：
- 零门槛试用，演示友好
- 登录后跨设备同步，体验完整
- 设计模式可复用：所有 OAuth provider 走同一接口
- 与开发者（GitHub 用户群）天然契合
- 测试用 `FakeOAuth` 完全离线

**负面**：
- 数据迁移逻辑复杂（多表 UPDATE + Chroma metadata 更新），需仔细测试
- 不支持企业内部用户（无 GitHub 账号场景），需未来加 SSO
- JWT 黑名单未实现（注销靠 cookie 删除，token 自身 30 天后过期）

## 备选方案

- **仅 session_id（无认证）**：记忆无法跨设备、无法多用户隔离演示，否决
- **邮箱 + 密码**：合规成本高，与项目主题无关，否决
- **仅 OAuth，无匿名**：劝退试用，否决

## 关联

- [ADR-008: owner_id 统一身份键](ADR-008-owner-id-tenancy.md)
- `infra/auth/`、`api/middleware/auth.py`、`experiment_v1.md` §1.4~§1.5
