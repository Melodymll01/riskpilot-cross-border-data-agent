# Go / Python 内部合同

RiskPilot 的对外业务入口是 `services/control-plane`，内部 AI 执行入口是 `services/agent-runtime`。浏览器只访问 Go control plane；Go 拥有业务数据、run 和最终终态，Python 只接受不可变执行快照并返回有序事件或结果提案。

## v1 Schema

- [服务身份声明](../../contracts/internal/v1/service-principal.schema.json)：`X-Riskpilot-Identity` JWT 的 `iss`、`aud`、`sub`、时效、nonce、方法和目标路径。
- [运行执行快照](../../contracts/internal/v1/run-execute.schema.json)：不可变的 run、attempt、thread、owner、消息、附件版本、记忆快照与预算。
- [有序运行事件](../../contracts/internal/v1/run-event.schema.json)：按 `run_id + attempt_id + event_seq` 标识的事件和 payload。
- [运行控制命令](../../contracts/internal/v1/run-control.schema.json)：取消当前 runtime attempt 时使用的 run、attempt 与原因。恢复通过新的 `attempt_id` 和 `run-execute.resume=true` 重新发送完整快照。
- [材料处理命令](../../contracts/internal/v1/document-process.schema.json)：Go 授权的不可变材料版本及 ingest/delete 计算请求。
- [材料处理结果](../../contracts/internal/v1/document-result.schema.json)：Python 返回的解析、索引状态与数量，不包含文件目录写入。

所有合同都显式携带 `schema_version`。运行重试使用新的 `attempt_id`，迟到事件不能覆盖当前 attempt；事件序号只能单调前进。

## 身份与代理边界

Control plane 从已验证会话签发内部身份。服务 JWT 使用独立密钥，绑定 HTTP 方法和 escaped request target，短时有效并携带一次性 nonce；浏览器没有访问 agent runtime 网络端口的路径。

该身份只证明内部调用者和快照作用域，不证明模型回答正确。Agent runtime 只能在 Go 提供的 owner、附件、记忆和预算快照内执行；事实性回答仍需通过来源回读和 Claim-Citation 验证，最终业务权限与版本由 Go 在提交前复核。

本地部署中 Python 绑定内部端口；Compose 不向宿主发布该端口。TLS 或受信本地网络负责传输保护。

## 验证入口

- [Go 认证与代理测试](../../services/control-plane/internal/api/server_test.go)
- [Python internal v1 合同测试](../../services/agent-runtime/tests/api/test_internal_v1.py)

这些测试覆盖身份头清理、签名、作用域、内部快照、事件序号和控制命令，不等同于真实 GitHub OAuth、完整浏览器链路或远端模型联调。
