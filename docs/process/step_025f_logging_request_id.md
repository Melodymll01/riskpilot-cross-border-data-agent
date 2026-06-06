# Step 025f — 结构化日志 contextvar 注入（让每条 log 自动带 `request_id`）

> Step 025d 把 `request_id` 注入 contextvar 让审计条目自动带上；但应用 log 行
> 仍然靠 `f"[{request_id}] ..."` 手工拼接——一处漏拼就丢线索。本步把
> `request_id` 提升为 logging 的一等字段，formatter 自动加 `[%(request_id)s]`
> 段，所有 logger 调用（包括第三方库）天然带上前缀。

## 1. 目标

- 所有应用 log 行格式统一为 `<ts> [LEVEL] [request_id] <name>: <msg>`
- 业务代码不再手写 `f"[{request_id}]"`——formatter 透明注入
- 无 contextvar（启动期、CLI 脚本）时优雅降级为 `[-]` 占位，不抛 KeyError
- handler 级 filter 而非 logger 级（filter 不在 logger 间级联，必须挂 handler）
- 幂等：重复 `configure_logging()` 不让 handler 翻倍

## 2. 改动清单

### 后端 2 文件 + 1 测试

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `app/logging_setup.py` | 新建 | `RequestIdLogFilter` + `configure_logging()` |
| `main.py` | 修改 | 启动时调 `configure_logging`；删除 middleware/global handler 里两处手工 `f"[{request_id}]"` |
| `tests/app/test_logging_setup.py` | 新建 | 8 用例覆盖 filter 行为 + 安装幂等 + 端到端 format |

evaluations/{benchmark,chunk_params}/run.py 里独立的 `logging.basicConfig(level=WARNING)`
**不动**——它们是离线评测脚本，与服务进程的 request_id 上下文无关。

## 3. 关键决策

### D1 — Filter 而非 Formatter 子类

stdlib 同等支持两种姿势注入字段：

- **Formatter 子类**：override `format()`，但要兼顾 `%`/`{`/`$` 三种风格 +
  覆盖整个格式逻辑，对其它字段有副作用
- **Filter**：只往 `record` 写属性，对 format 字符串透明；与 logging 文档
  [Filter Cookbook](https://docs.python.org/3/howto/logging-cookbook.html#using-filters-to-impart-contextual-information) 推荐姿势一致

选 Filter——零侵入、风格无关、文档对齐。

### D2 — 缺省哨兵 `"-"` 而非空串/None

`%(request_id)s` 在 contextvar 未设时如果属性缺失会抛 `KeyError`，必须有
默认值。选 `-`（Apache combined log 的固定空位约定）：

- 一字符不污染列宽（`[]` 与 `[abc-1234]` 都比 `[-]` 短不了多少）
- 扫眼立即识别「这条 log 没有 request_id 上下文」，区分于空请求 id
- 与 nginx access log 一致，运维侧识别成本为 0

### D3 — handler 级 filter，不是 logger 级

`logger.addFilter()` 在 stdlib 文档里有明确警告：filter 不级联到 child logger
的 handler。如果只挂 root logger 上：

```python
root.addFilter(RequestIdLogFilter())  # ❌ 错
logging.getLogger("uvicorn").info(...)  # 这条不会过滤！
```

必须挂在 handler 上：

```python
for h in root.handlers:
    h.addFilter(RequestIdLogFilter())  # ✅ 对
```

handler 级 filter 是 record 流向输出的最后一关，无论 logger 路径多深都会过。

### D4 — 幂等检测：handler 上打 `_step025f_owned` 标记

uvicorn / pytest 启动时可能已经在 root logger 挂了它们自己的 StreamHandler。
我们的策略：

1. 不清空 `root.handlers`（不抢人）
2. 但每个我们 *新加* 的 handler 打上 `_step025f_owned = True` 属性
3. 重复调 `configure_logging()` 时按类型 + 标记双重去重，已有 owned handler
   的同类型不再加

这样：
- 测试 fixture 多次调 `configure_logging` 不会翻倍
- uvicorn 自家 handler 我们不动，但它的 record 在我们 handler 这关也会过 filter
  （pytest fixture 也照常工作——见 `tests/app/test_logging_setup.py::test_idempotent_does_not_duplicate_handlers`）

### D5 — extra_handlers 参数仅为测试注入

production 路径只有 stream + 可选 file。`extra_handlers: Iterable[Handler] | None`
的存在纯粹是为单测能塞 `MemoryHandler` 抓 record。不在 main.py 启动路径
使用——不破坏 prod 配置面的最小性。

## 4. 验证

- 单测：8 passed（`tests/app/test_logging_setup.py`）
- 全量回归：595 passed（587 + 8 新增），无回归
- ruff scoped：All checks passed
- 手动观察 `logs/app.log`：每行都含 `[<8 字符 request_id>]` 段，启动期 log
  含 `[-]`

## 5. 后续工作

- Step 025g（待规划）：把 `user_id` 一并注入 contextvar，让 log 行可加
  `[uid:<id>]` 段，便于按用户聚合排障
- 不在本步做的「重构债」（见 conversation summary）：mypy 复活、v1 检索面退役
