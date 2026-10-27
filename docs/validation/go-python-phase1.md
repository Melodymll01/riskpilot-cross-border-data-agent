# Go / Python 服务迁移历史验证记录

日期：2026-10-26；2026-10-27 按最终所有权更新。本文保留当时的测试数字，现行架构以[双服务边界](../architecture/go-python-migration.md)为准：Go 拥有业务数据和运行终态，Python 只负责内部 AI 执行。

## 最终职责

- `services/control-plane`：Go 1.26、Gin、认证与授权、会话/消息/记忆/文件目录、run、取消/恢复、SSE，以及最终消息和终态原子提交。
- `services/agent-runtime`：内部身份验证、Agent、模型调用、RAG、材料计算、Evidence QA、AI checkpoint 和有序结果提案。
- Go 是业务表唯一写入方；Python 不写会话、记忆、文件目录、最终消息或业务 run 终态。
- 启动器管理Go 8765和Python 8766，创建独立内部密钥，支持后台启动、状态与停止。
- Compose只发布Go端口，Python在内部网络运行。Compose模型验证通过，本轮没有执行完整容器镜像构建或容器部署。

## 内部身份

Go清除外部的内部身份头、Cookie、Authorization和转发头，并根据已验证会话签发新的服务身份。JWT使用独立密钥、30秒有效期、方法与原始请求目标绑定和nonce。

Python 检查服务签名、issuer、audience、期限和请求目标，只在 Go 提供的不可变快照范围内执行。账号、资源授权、业务版本和最终提交由 Go 检查。

JWT只作为内部身份合同，不证明模型回答正确，也不替代正文和资源授权校验。模型仍通过项目的 LangChain 工厂与 API 适配器调用。

## 自动化验证

```bash
RUN_LIVE=0 make ci PY=.venv/bin/python
```

最终结果：

- Ruff、格式与mypy通过。
- Go `vet`与`test -race ./...`通过。
- pytest **1055 passed, 1 skipped**；跳过默认关闭的外部live RAG测试。
- 13条Evidence QA oracle协议自检通过，不作为真实模型准确率证据。

Go专项验证：

- 匿名登录、当前身份、退出与原Python格式session兼容。
- OAuth浏览器state绑定、状态持久化、一次性消费和用户名变更后owner保留。
- provider身份冲突不覆盖既有账号。
- 服务JWT绑定、浏览器头清理和上游Cookie阻断。
- SSE首帧立即可读、客户端取消传递至上游。
- 限流、并发上限、跨Origin写入拒绝和上游不可用错误。

Go/Python 集成验证使用独立存储和 Fake 模型，覆盖 Go 记忆保存与开关、不可变运行快照、Python 回答提案、SSE、run 查询、跨账号隔离和前端操作。GitHub token/user 响应在 Go 单测中使用 HTTP fixture，没有执行真实 GitHub OAuth 回调或远端模型。

## 既有附件并发问题

完整回归稳定复现了两个首次附件建索引线程同时初始化 `evidence_embedding_profile.singleton`，导致 `UNIQUE constraint failed`。修复为原子 `INSERT OR IGNORE` 后回读并校验模型身份和维度。

`services/agent-runtime/tests/infra/test_embedding_profile_concurrency.py` 使用 Barrier 复现竞争，覆盖相同模型均成功及不同模型只允许一个通过。原有模型与维度不匹配规则保留。

## 本地切换证据

- 数据库在切换前备份到 `data/backups/rag-before-go-phase1-20261026-040822.sqlite3`。
- 启动后所有原有表的条数和内容SHA-256与切换前一致；Go新增表单独登记。
- `http://127.0.0.1:8765/api/v2/health`报告`backend=go`；ready报告Go数据库和Python Agent就绪。
- 现有账号的 session 经 Go 验证后，由 Go 读取账号记忆并构造签名的内部执行快照。
- 从Go入口实际提问“我叫什么”“我的职业是什么”，分别回答“马乐乐”“学生”，附记忆原话引用。
- 直接向 8766 发送浏览器 Cookie 会被拒绝；Python 内部服务不提供浏览器业务登录或记忆写入入口。

上述HTTP请求为本地验证，使用同一签名配置的临时Cookie；没有读取用户浏览器Cookie，也没有重新执行GitHub授权。验证生成了少量会话记录。

## 本地操作

```bash
make serve PY=.venv/bin/python
# 或后台启动
.venv/bin/python scripts/dev_stack.py start --detach
make serve-status PY=.venv/bin/python
make serve-stop PY=.venv/bin/python
```

日志为 `logs/dev-stack.log`，状态为 `data/dev-stack.json`，Go构建产物和内部密钥位于忽略的`data/`子路径。

本机Apple编译工具与默认MacOSX27 SDK不兼容，本轮使用本地`.env`中的`GO_SDKROOT`选择已安装的MacOSX26.5 SDK。该设置仅作用于项目Go命令，未修改系统工具链。Linux CI无需此项。

仓库目录随后已完成双服务收敛；本页只保留 2026-10-26 的验证快照，不再描述实施计划。当前职责与路径以[双服务架构](../architecture/go-python-migration.md)为准。
