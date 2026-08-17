# Phase 6 实施复盘：Agent 安全与权限

- 状态：已完成
- 日期：2026-08-17
- 前置提交：`e40295e`

## 1. 本阶段目标

在不引入完整企业 IAM、不改变 DDD 依赖方向的前提下，补齐核心 Case Assessment Agent 的
生产安全边界：

1. 全局 Tool Policy 明确允许和禁止的副作用级别；
2. 模型不能通过工具参数修改 Workspace、Case、actor 或角色；
3. 文档中的 Prompt Injection 只能作为不可信证据，不能改变工具权限；
4. 任意 URL 抓取必须阻断 localhost、私网、链路本地地址和危险重定向；
5. 伪造 Citation、跨 Workspace/Case 访问和 Agent 自动审批必须 fail closed；
6. 文件上传继续阻断非法扩展名、MIME/魔数不一致、超大文件和 ZIP Bomb；
7. Trace、checkpoint、RunEvent 和长期记忆不得保存凭证、完整正文或思维链；
8. 默认测试继续零密钥、零网络、零模型费用。

## 2. Phase 6 开始前已有资产

### 2.1 已有权限与多租户基础

- Workspace Membership 与 `viewer/editor/reviewer/admin`；
- Case/Document/Fact/Policy/Assessment Use Case 均在服务端重新鉴权；
- 向量查询下推 `workspace_id + case_id + current version`；
- Typed Tool input 使用 Pydantic `extra="forbid"`；
- `workspace_id/case_id/actor_id` 由 `AgentRuntimeContext` 注入；
- Fact 确认与 Assessment 审批仅允许 Reviewer/Admin。

### 2.2 已有文件与对象安全

- V3 Document 上传限制扩展名、大小、PDF 魔数、UTF-8 文本；
- DOCX 校验必要 ZIP 结构、CRC 和解压体积；
- 图片校验真实解码格式、尺寸、像素数和大小；
- Local/S3 Object Store 拒绝路径穿越和绝对路径。

### 2.3 已有隐私安全

- Run/Checkpoint/Event 禁止 `api_key/password/secret/raw_prompt/chain_of_thought` 等字段；
- LangSmith 默认关闭，启用时隐藏 input/output/error/events/attachments/serialized；
- Trace 只允许白名单元数据，业务 ID 使用 HMAC；
- 长期记忆只从用户消息抽取逐字 quote，过滤凭证和高敏信息。

### 2.4 当前真实缺口

1. `side_effect_level` 目前只是工具元数据，没有统一 Policy 做最终裁决；
2. Registry 没有显式禁止 `privileged_write/forbidden_for_agent`；
3. `WebSearcher._fetch_page_text()` 只检查协议，允许 DNS 指向私网并自动跟随重定向；
4. `ingestion/web_loader.py` 同样只检查协议；
5. 没有一组统一安全回归证明跨租户、Prompt Injection、Citation 伪造和敏感 Trace 为 0；
6. DOCX 虽有限制总解压体积，但缺少文件数量、单文件压缩比和加密条目门禁。

## 3. 威胁模型

| 攻击面 | 攻击方式 | 服务端不变量 |
| --- | --- | --- |
| Tool Calling | 模型传入其他 case/workspace/actor | Scope 不属于输入 Schema，只接受 Runtime Context |
| Tool Side Effect | 在错误阶段调用写工具或调用高权限工具 | stage + role + side-effect policy 三重门禁 |
| Prompt Injection | 文档正文要求忽略规则、泄露数据或调用危险工具 | 文档永远是 data；工具白名单和权限不受正文影响 |
| Cross Tenant | 猜测 Case/Fact/Assessment ID | Use Case 重查成员关系；不存在与越权统一隐藏 |
| Citation Forgery | 模型引用不存在或漂移的 quote/version | 重新读取当前原文、SHA、offset、Fact/Rule snapshot |
| SSRF | localhost、私网、DNS rebinding、重定向到内网 | 每一跳解析 DNS/IP；禁止非公网地址；限制重定向和响应体 |
| File Bomb | ZIP Bomb、超多条目、加密 DOCX、伪 MIME | 大小、魔数、ZIP 条目/压缩比/总解压量、解析资源门禁 |
| Trace Leak | Prompt、正文、Authorization、Cookie、异常文本上传 | 出站白名单、ID 哈希、正文与错误统一裁剪 |
| Memory Poisoning | 文档指令或凭证进入长期记忆 | 只取用户消息、逐字接地、敏感模式过滤 |

## 4. 设计决策

### 4.1 Tool Policy 放在 app 层

domain 定义副作用等级和 Policy 决策契约；app Registry 在执行前统一裁决；具体工具仍复用
Use Case。这样：

- domain 不依赖 LangChain/LangGraph；
- Graph 不直接访问 Repository；
- 所有 Agent 工具共享同一安全门禁；
- 测试可注入严格或定制 Policy。

### 4.2 预期副作用矩阵

| 级别 | Agent 默认 | 额外要求 |
| --- | --- | --- |
| `read_only` | 允许 | role + stage |
| `reversible_write` | 受限允许 | role + stage，必须显式 allowlist，默认不自动 retry |
| `privileged_write` | 禁止 | 只能由显式人工 API 执行 |
| `forbidden_for_agent` | 禁止注册/执行 | 永不暴露给模型 |

### 4.3 SSRF 为什么必须逐跳校验

只在首次请求前检查 URL 不足以防止：

- 公网 URL 302 到 `127.0.0.1`；
- 域名同时解析公网和私网；
- DNS rebinding；
- IPv6 loopback/link-local；
- 十进制或混合格式 IP。

因此安全客户端必须：

1. 只允许 HTTP/HTTPS；
2. 禁止 URL 凭据和非标准危险端口策略；
3. 解析全部 A/AAAA 地址并要求全部为公网；
4. 手动处理重定向，每一跳重新校验；
5. 限制 redirect 次数、timeout、Content-Length 和流式读取字节数；
6. 只接受文本/HTML 等预期内容类型。

### 4.4 为什么 Tool 参数和输出还要二次脱敏

Pydantic 只保证结构合法，不保证内容适合进入 checkpoint/RunEvent。因此 Registry 还会：

- 在 Pydantic 前拒绝任何嵌套 `workspace_id/case_id/actor_id/actor_role/run_id`；
- 把 query 替换为 `[redacted] + query_length`；
- 拒绝正文、Prompt、quote、Cookie、Authorization、凭证、思维链和二进制输出；
- 把 Pydantic ValidationError 转成固定消息，不回显攻击载荷；
- Run 失败只持久化错误类型和固定安全说明，不保存原异常正文。

### 4.5 为什么复用已有安全测试

Phase 6 不重复造已经存在的代理指标。以下既有测试作为本阶段正式门禁：

- SQLite EvidenceIndex 同 Workspace 跨 Case 过滤；
- SQLAlchemy EvidenceIndex 跨 Case 查询为空；
- LangSmith 出站 input/output/error/events/attachments/serialized 裁剪；
- 长期记忆过滤 API Key、密码、手机号、邮箱和证件号；
- Local/S3 ObjectStore 路径穿越；
- Assessment Citation 漂移验证。

新增 `tests/security/` 负责跨模块攻击组合和新增 Tool/SSRF Policy。

## 5. 修改文件与实现说明

| 文件 | 为什么改 | 怎么实现 |
| --- | --- | --- |
| `domain/agent_workflow.py` | 三种副作用等级无法表达绝对禁用 | 增加 `forbidden_for_agent` |
| `app/agent_tools/policy.py` | side effect 过去只是展示元数据 | 注册/执行双门禁；可逆写 allowlist；高权限工具禁止暴露；scope/output 安全检查 |
| `app/agent_tools/registry.py` | Pydantic 错误、query、工具输出可能泄漏 | 固定错误消息、参数脱敏、敏感输出拒绝、Policy 统一裁决 |
| `app/agent_tools/__init__.py` | 提供稳定安全策略入口 | 导出 `AgentToolPolicy` |
| `infra/web/safe_http.py` | 任意 URL 抓取缺少 SSRF 防护 | 校验协议/凭据/DNS/IP；固定已校验 IP 连接；每跳重验；限制重定向/类型/响应体 |
| `infra/web/searcher.py` | 搜索结果正文抓取可跟随到内网 | 正文抓取改用 `SafeHttpClient`；固定搜索后端保留原逻辑 |
| `ingestion/web_loader.py` | 用户提交 URL 仅检查协议 | 改用统一安全客户端，并保存最终安全 URL |
| `app/use_cases/document_management.py` | DOCX 只限制总解压体积 | 增加条目数、加密标志、单条目大小、零压缩信息和压缩比门禁 |
| `app/use_cases/assessment_runs.py` | Run 失败会把任意异常正文写入数据库/API | 持久化固定安全消息，只保留错误类型 |
| `tests/security/test_tool_policy.py` | 直接证明 Agent 不可越权 | 高权限工具、scope、Prompt Injection、敏感输出、错误载荷、恶意文档字段扩张 |
| `tests/security/test_safe_http.py` | SSRF 不能依赖真实网络测试 | Fake Transport 覆盖 localhost/私网/混合 DNS/重定向/类型/大小/固定 IP |
| `tests/security/test_security_regressions.py` | 需要跨模块安全闭环 | 跨 Workspace、伪造 Citation、Agent 自动审批、Trace 敏感字段 |
| `tests/app/test_document_management.py` | 文件攻击需要真实写入前门禁证据 | DOCX 高压缩比和超多条目均零落库 |
| `tests/app/test_assessment_runs.py` | 验证异常正文不持久化 | simulated crash 仍可恢复，但数据库不含原异常 |
| `tests/infra/test_consolidation.py` | 明确覆盖密码 | 新增 password 敏感样例 |
| `docs/implementation/phase-06-security-and-permissions.md` | 每项修改可复习 | 本文 |
| `docs/roadmap/autumn-recruitment-production-plan.md` | 路线与代码同步 | 最终验收后推进 Phase 7 |

## 6. 数据模型变化

不新增业务表。

- `ToolSideEffectLevel` 新增 `forbidden_for_agent`；
- 其他安全策略均为 app/infra Adapter；
- 不把 SQLAlchemy、HTTP 客户端或 Policy 实现引入 domain。

## 7. API 变化

不新增对外 API，不做破坏性兼容修改。

- Tool 参数错误返回固定安全消息，不回显原攻击载荷；
- Run detail 的 `error_message` 不再保存原异常文本；
- Web ingest 的 SSRF 拒绝仍走现有 `INGEST_FAILED` 错误契约。

## 8. Agent 状态变化

不增加新业务节点。Tool Policy 在工具注册和每次执行前生效：

```text
registered tool
→ side-effect registration gate
→ role/stage runtime gate
→ reserved scope key gate
→ Pydantic input
→ executor
→ Pydantic output
→ sensitive output gate
→ sanitized ToolExecutionResult
```

拒绝时 Run fail closed，只记录安全错误类型和固定说明。

## 9. 验收标准

- [x] read_only 工具按角色/阶段执行；
- [x] reversible_write 只能在显式 allowlist 阶段执行且不自动 retry；
- [x] privileged_write 和 forbidden 工具不能被 Agent 注册或执行；
- [x] 模型不能注入 workspace/case/actor/role；
- [x] Prompt Injection 不能改变工具作用域、扩大 Fact 字段或跳过规则；
- [x] 跨 Workspace/Case 泄漏为 0；
- [x] 伪造 Citation 无法通过引用验证；
- [x] Agent/Editor 不能审批正式 Assessment；
- [x] Web Loader 无法访问 localhost、私网或通过重定向绕过；
- [x] 非法 MIME、超大文件和 ZIP Bomb 被拒绝；
- [x] Trace/Checkpoint/Event/Memory 不含凭证、正文和思维链；
- [x] 默认离线安全测试不访问网络；
- [x] 全量 `make ci` 通过。

## 10. 测试结果

### 10.1 Phase 6 聚焦门禁

```text
124 passed, 5 warnings in 3.30s
```

覆盖：

- `tests/security/`；
- Tool Registry；
- Document / Run 安全回归；
- LangSmith 脱敏；
- Web Loader；
- SQLite 跨 Case 检索；
- 长期记忆敏感信息；
- V2 Document API。

静态检查：

```text
Ruff: All checks passed
Format: 404 files already formatted
mypy: Success: no issues found in 145 source files
```

### 10.2 最终全量

```text
$ PATH="$PWD/.venv/bin:$PATH" make ci
Ruff: All checks passed
Format: 404 files already formatted
mypy: Success: no issues found in 145 source files
pytest: 1318 passed, 4 skipped, 5 warnings in 20.19s
```

四项 skip 仍为显式外部环境/live 门禁。Phase 6 的 SSRF 测试全部使用 Fake Transport，不访问
真实网络；Prompt Injection 测试使用 Fake Chat，不产生模型费用。

## 11. 尚未解决的风险

1. 固定 IP + Host/TLS hostname 可以消除连接时 DNS 二次解析窗口，但无法控制目标公网服务
   自身访问其他内网资源；系统只信任返回文本，不执行其中指令；
2. Search backend 的固定 DuckDuckGo/Bing 请求仍用 requests；它们不是用户可控 URL，搜索
   结果正文才进入 SafeHttpClient；
3. Tool timeout 仍不能强杀已进入底层 C 扩展的线程，Phase 8 可结合独立执行器和指标治理；
4. 现有 V2 通用错误协议会返回业务错误说明；核心 Agent/SSRF 路径已固定安全消息，但全站
   错误分类与 WAF/网关策略不属于本阶段；
5. 本阶段验证的是协议级 Prompt Injection 防护，不声称模型永远不会受任何未知提示攻击；
   安全性来自服务端 scope、工具和确定性门禁，而不是相信 Prompt。

## 12. 下一阶段

Phase 6 全部门禁通过，可以进入 Phase 7：版本化 Agent 轨迹评测数据集、协议评测和真实
模型评测分层。
