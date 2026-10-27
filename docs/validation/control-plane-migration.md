# Go control plane 与 Python agent runtime 迁移验证

日期：2026-10-28。

## 完成边界

- 对外服务为 `services/control-plane`，显式实现认证、会话、消息、记忆、文件目录、知识库目录、run、SSE 与最终提交。
- 私有服务为 `services/agent-runtime`，只暴露版本化的运行执行、取消和材料处理接口。
- 根目录旧 Python 包、旧 `services/api-go`、通配反向代理、Python `/api/v2` 和业务表写入实现均已删除，没有 shim、软链接或双写开关。
- 仓库 Markdown 全部位于 `docs/`。

## 数据迁移

迁移前使用 SQLite 在线备份保存：

- `data/backups/rag-before-control-plane-migration-20261028.sqlite3`
- `data/backups/control-plane-before-kb-catalog-migration-20261028.sqlite3`

Go 权威业务库为 `data/control-plane.sqlite3`，迁移后完整性检查为 `ok`：

| 数据 | 数量 |
| --- | ---: |
| users | 189 |
| tasks | 25 |
| messages | 71 |
| conversation runs | 39 |
| account memories | 2 |
| KB sources | 2 |

公共 Chroma 索引的 76 个 chunk 对应 2 个来源；其目录元数据已重建到 Go `kb_documents`，向量和正文索引未改写。Python 工件库为 `data/agent-runtime-artifacts.sqlite3`，只包含 `parse_artifacts`、`evidence_chunks` 和 `evidence_embedding_profile`。

旧 `data/rag.sqlite3` 仅作为未修改的恢复来源保留，运行配置不再引用它；这不是旧代码或第二写入方。

## 自动化门禁

- Ruff check 与 format：通过。
- Mypy：57 个 Python 源文件，0 error。
- Python tests：378 passed，只有第三方 SWIG 类型的 5 条弃用警告。
- Go vet 与 `go test -race ./...`：通过。
- Evidence QA oracle：13 个 case，全部阈值 gate 通过，跨范围泄漏和 verifier error 均为 0。
- Compose 配置解析：通过。
- 内部 JSON Schema：全部为 Draft 2020-12 且可解析。
- Markdown 本地链接：260 个目标，0 broken。

## 真实进程 smoke

通过仓库启动器实际启动 Go 与 Python 后验证：

- Go `/` 与 `/api/v2/health/ready`：200。
- 匿名登录：201；随后 `/auth/me`、`/tasks`、公共 `/documents`：200。
- 直接访问 Python 的旧 `/api/v2`：401，浏览器不能绕过 control plane。
- smoke 创建的匿名用户和审计记录已删除；用户数恢复为迁移前的 189。
- 旧 `riskpilot-api` 进程与二进制已停止并删除，新栈验证后也已正常停机。

Docker Compose 文件和镜像路径已通过静态合同检查；本机 Docker daemon 未运行，因此没有把镜像构建结果写成已验证事实。
