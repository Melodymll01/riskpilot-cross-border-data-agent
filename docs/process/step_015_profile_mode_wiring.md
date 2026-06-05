# Step 015 — profile 模式联调闭环（RunCopilot 短路 + RiskProfilePort 装配）

> 对应即将提交的 commit（本步与代码同 commit）
> 计划标题：`feat(copilot): profile 模式接通 RiskProfilePort 联调闭环（短路 agent + markdown 渲染）`

## 1. 本步骤目标

把 [Step 013](step_013_admin_modes_risk_profile_port.md) 的 `RiskProfilePort` 与
[Step 014](step_014_frontend_3mode_tabs.md) 的前端 3-Tab 真正接通：

`mode == "profile"` 的请求不再走 ReAct Agent —— 直接调
`RiskProfilePort.assess(target=user_message)`，把结果（或 `RiskProfileNotReady`
"敬请期待"）格式化成 markdown，沿同一条 SSE 通道以 `answer` 事件返回。

至此，profile 模式的端到端管道从前端选 Tab → 后端短路 → 占位响应 → 前端
markdown 渲染**全程联通**；等 `schema-evidence-risk-profiling` 仓库的
evidence-state v1 模型部署，只需在 [`app/factories.py`](../../app/factories.py)
的 `build_risk_profile()` 里把 `StubRiskProfileService` 换成 `HttpRiskProfileClient`，
本步骤的所有逻辑无需改动。

## 2. 修改文件

| 文件 | +/- | 关键改动 |
|---|---|---|
| [app/use_cases/run_copilot.py](../../app/use_cases/run_copilot.py) | +90 | `__init__` 增 `risk_profile: RiskProfilePort \| None = None`；`stream` 在 `task_created` 后判断 `if mode == "profile": yield from self._run_profile(target=user_message); return`；新增私有方法 `_run_profile`（处理 None / 捕 `RiskProfileNotReady` / 调 `assess` / 渲染）；新增模块级 `_STATE_EMOJI` / `_STATE_LABEL` 常量；新增工具函数 `_format_risk_profile_md(rp)` |
| [app/container.py](../../app/container.py) | +1 | `RunCopilotUseCase(... , risk_profile=self.risk_profile)` 透传容器装好的端口 |
| [tests/app/test_run_copilot.py](../../tests/app/test_run_copilot.py) | +90 | 新辅助 `_make_uc_with_risk_profile(risk_profile) -> (uc, repo)`；新测试类 `TestProfileMode` 4 个用例 |

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **profile 分流放 use case 而非 API 路由** | 在 `api/v2/copilot.py` 里按 mode 二选一调不同后端 | use case 是业务边界；API 层只关心"反序列化请求 + 流式输出"；mode 分流是业务规则不是协议规则 |
| **`risk_profile` 设为 `Optional[RiskProfilePort]`** | 必填依赖 | 容器装配出错时 use case 仍可工作于 qa/research 模式；profile 路径独立兜底为友好提示而非整体崩 |
| **mode 判断在 `task_created` 之后短路** | 在 `task_created` 之前就分流 | task 必须先创建（不创建前端拿不到 task_id 没法回放/列表显示）；profile 任务也是合法 task |
| **`_run_profile` 是私有方法 + 显式 generator** | 内联在 `stream` 主体 | profile 路径有 3 个分支（None / RiskProfileNotReady / 成功）；抽出来单测可读，主路径保持简洁 |
| **None 兜底返回 friendly answer 而非 raise** | 抛 `RuntimeError` / 让 SSE 输出 `error` 事件 | 用户视角："为什么我点了画像 Tab 看到红色错误？"；运维视角应当在 `app.state.container` 装配阶段就发现 None；运行时保守降级到友好提示 |
| **catch `RiskProfileNotReady`，不 catch `Exception`** | 兜底所有异常 | `RiskProfileNotReady` 是占位场景的"预期非错误"；其他异常（网络/序列化/Bug）应继续向上抛由 SSE 错误处理器转 `error` 事件 |
| **markdown 在 use case 渲染** | 前端拿 RiskProfile JSON 自渲染 | use case 决定"输出形态"——这是聊天界面，markdown 是合适的对话原子单元；前端已经有 `marked.parse()` |
| **`_STATE_EMOJI` / `_STATE_LABEL` 在模块级** | 在函数内每次构造 | 5 个分类 × 字符串字典，纯静态；模块级变量减少重复构造 |
| **emoji + label 双显示** | 仅 emoji 或仅 label | emoji 一眼快读，label 给出明确语义（防止 emoji 被误读为情绪）；面试演示更清晰 |
| **`evidence_spans` 渲染为 markdown 列表** | 表格 / 单段落 | 列表是 marked.js 默认支持的最稳定结构；将来 spans 数量增长不会撑爆布局 |
| **`(字符 12-48)` 偏移注释为可选** | 强制显示 | PrivacyQA 等数据集没有字符偏移；start/end 二者皆有时才显示；与 [Step 013](step_013_admin_modes_risk_profile_port.md) `EvidenceSpan` 字段约束对齐 |
| **复用同一条 SSE 通道（answer 事件），不另开端点** | 新增 `/api/v2/risk_profile/assess` REST 端点 | profile 是聊天形态的另一种回答，不是独立功能；保持单一对话 UX、单一历史回放路径；前端已无视事件来源是 agent 还是 use case 直注，统一渲染 |

## 4. 核心契约 / 接口

### `RunCopilotUseCase` 新签名

```python
class RunCopilotUseCase:
    def __init__(
        self,
        *,
        agent: ComplianceCopilotAgent,
        task_management: TaskManagementUseCase,
        risk_profile: RiskProfilePort | None = None,  # ← 新增
    ) -> None: ...

    def stream(
        self,
        *,
        owner_id: str,
        task_id: str | None,
        user_message: str,
        attachment_doc_ids: list[str] | None = None,
        mode: TaskMode = "qa",  # 已在 Step 013 加，本步首次真正按 mode 分流
    ) -> Iterator[AgentEvent]:
        # 1) 创任务（不变）
        # 2) if mode == "profile": yield from self._run_profile(...); return  ← 新增
        # 3) 否则进 agent.run()（不变）
```

### profile 分流的 3 条路径

```python
def _run_profile(self, *, target: str) -> Iterator[AgentEvent]:
    if self._risk_profile is None:                                         # 路径 A
        yield AgentEvent.answer("⚠️ 风险画像服务未在容器中装配，请联系运维。")
        return
    try:
        result = self._risk_profile.assess(target=target)
    except RiskProfileNotReady as exc:                                      # 路径 B
        yield AgentEvent.answer(
            f"⏳ **风险画像模型尚未上线**\n\n{exc}\n\n"
            "目前 `📊 风险画像` Tab 以接口预留形态运行；"
            "`schema-evidence-risk-profiling` 仓库的 evidence-state v1 "
            "模型完成训练后会自动接入此处。"
        )
        return
    yield AgentEvent.answer(_format_risk_profile_md(result))                # 路径 C
```

### markdown 渲染规则

```
## 风险画像评估

**目标命题**：{rp.target}

**证据状态**：{emoji} {label}                # 5 分类 × emoji + 中文说明

**解释**：{rp.explanation}                   # 仅在非空时输出

### 关键证据                                  # 仅在 spans 非空时输出
- {span.text}（字符 {start}-{end}）          # 偏移可选
- ...
```

## 5. 与外部服务的关系

- **`schema-evidence-risk-profiling`**（隔壁仓 `D:\py\schema-evidence-risk-profiling`）—— 模型仍在训练；本步只与 `Stub*` 占位实现联调，未发起任何外部请求
- **SSE 通道** —— 复用 [api/v2/copilot.py](../../api/v2/copilot.py) 已有的 `chat/stream` 端点；`AgentEvent.answer(text)` 经 `format_event(...)` 序列化为 SSE 帧，与 agent 输出走同一格式
- **前端** —— [Step 014](step_014_frontend_3mode_tabs.md) 已把 mode 字段塞进 payload；前端 `chat.js` 的 `renderAnswer()` 用 `marked.parse()` 自动渲染 markdown，无需改动

## 6. 当前实现范围

✅ 已实现：

- profile 模式短路 agent，直调 `RiskProfilePort.assess(target=user_message)`
- 三态兜底（None / NotReady / 成功）全部友好降级
- markdown 渲染：标题 + 目标 + emoji 状态 + 解释 + 证据列表 + 字符偏移
- task_created 事件先于 answer 发出（保证前端能拿到 task_id 入列表）
- task.mode 持久化为 "profile"（继承 [Step 013](step_013_admin_modes_risk_profile_port.md) 链路）
- qa / research 模式零回归（4 用例之一专门验证）
- 容器装配自动接入：`build_risk_profile(settings) → container.risk_profile → RunCopilotUseCase.risk_profile`

❌ 未实现（按规划推迟）：

- **真实模型** —— 等隔壁仓 evidence-state v1 训练完成
- **document 参数** —— 当前调用 `assess(target=...)` 不传 document；未来真实模型接入后由前端"附件 → document_text"、或后端先做检索再传文档（视模型形态）
- **research 模式真分流** —— 仍走 agent，与 qa 共用路径；Step 017+ 切到 `agentic_rag + report_generator`
- **多语言** —— `assess(language="zh")` 默认中文；未来支持英文 evidence 时由前端选项注入
- **流式 markdown** —— answer 是一次性 emit，不做"打字机"效果

## 7. 暂未实现 / TODO

- profile 路径下 SSE 没有 `tool_call` / `thought` 帧；前端"思考流"区域为空，UX 上是合理的（直接出结果），但简历可以提一句"区分两种用户感知节奏"
- `_format_risk_profile_md` 没引用 prompt（不调用 LLM），是纯函数；如果未来真实模型在 `explanation` 中包含 markdown 字符（例如 `*` `_`）需要做一遍转义
- 如果 `evidence_spans` 数量很多（>20），列表会很长；可考虑超过 N 条折叠为"展开剩余 K 条"
- `_run_profile` 没有 `task_id` 参数；如果以后要把"profile 评估快照"持久化到 ToolCall 表方便审计，需要传 task_id 进来再 INSERT 一条 `tool_name="risk_profile.assess"` 的记录
- profile 模式当前不消费 `attachment_doc_ids`；将来真实模型支持传文档时，可把附件 doc 内容拼成 `document` 参数

## 8. 测试与验证

```bash
pytest -q --no-cov tests/app/test_run_copilot.py -k profile
# 4 passed in 0.8s
pytest -q --no-cov
# 413 passed (+4 vs Step 013/014 基线 409)
ruff check app tests/app
# All checks passed
mypy app
# Success: no issues found
```

### 4 个核心用例

| 用例 | 输入 | 期望 |
|---|---|---|
| `test_profile_mode_with_stub_emits_not_ready_answer` | `StubRiskProfileService(mode="raise")` | events[0]=TASK_CREATED；events[1]=ANSWER 文本含 `"尚未上线"`/`"未上线"` + `"schema-evidence"`；task.mode == `"profile"`；事件序列**不含** THOUGHT / TOOL_CALL（agent 未被触发） |
| `test_profile_mode_with_placeholder_renders_markdown` | `StubRiskProfileService(mode="placeholder")` + target `"临床数据出境到德国总部是否需要安全评估"` | answer 文本含 `"## 风险画像评估"` / `"**目标命题**"` / 完整 target 字符串 / `"not_disclosed"` |
| `test_profile_mode_without_risk_profile_falls_back` | `risk_profile=None` | answer 文本含 `"未在容器中装配"` 或 `"未装配"` |
| `test_qa_mode_unchanged_does_not_call_risk_profile` | `_ExplodingRiskProfile()`（assess 抛 AssertionError）+ `mode="qa"` | qa 路径正常出 ANSWER 事件，且 `assess` **从未**被调用（否则测试失败） |

### 端到端冒烟（uvicorn :8765 + 浏览器）

```
1. 顶部点 📊 风险画像 Tab
2. 输入框输入"跨境电商日均向香港传 5 万条订单数据"
3. 发送 → 网络面板：POST /api/v2/copilot/chat/stream，body 含 mode: "profile"
4. SSE 帧序列：
     event: task_created   {task_id: "task_..."}
     event: answer          {text: "⏳ **风险画像模型尚未上线**\n\n..."}
5. 前端 marked 渲染：粗体 + 代码块（schema-evidence-risk-profiling）正确显示
6. 任务列表新增条目，紫色 "画像" 徽标
7. 切到 💬 知识问答 Tab → 自动 newConversation；qa 路径仍走 agent，正常输出
```
