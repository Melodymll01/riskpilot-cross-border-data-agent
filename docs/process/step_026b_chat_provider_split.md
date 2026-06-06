# Step 026b — Chat 通道独立 API key / base url 配置

> Step 026a 完成审计 CSV 导出后，下一步是端到端验证 RAG 链路（Phase 2）。
> 但你只能用一家 provider 同时承载 chat + embedding 调用，会撞额度池。
> 本步把 chat 通道从 `OPENAI_API_KEY` / `OPENAI_API_BASE` 解耦出来，可独立
> 切到另一家（如阿里云百炼 GLM-5 / 通义 Qwen 拿免费额度），embedding 留
> 智谱 BigModel `embedding-3` 不动 —— ChromaDB 既有向量库 0 重建。

## 1. 目标

- `Settings` 加 `chat_api_key: str | None = None` + `chat_api_base: str | None = None`
  两个**可选**字段
- `effective_chat_api_key` / `effective_chat_base_url` 优先用 chat_*，回退 openai_*
- **embedding 不动**：`effective_embed_api_key` / `effective_embed_base_url` 仍读 openai_*
- 既有用户零迁移成本（不改 .env 还是单 key 模式）
- 顺手修 `chat_client.py` 一致性 bug（旧实现绕过了 `effective_*` 属性，违反封装）

## 2. 改动清单

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `config.py` | 修改 | +`chat_api_key` / `chat_api_base` 字段；`effective_chat_*` 优先链改成 `chat_* or openai_*` |
| `retrieval/generation/chat_client.py` | 修改 | `__init__` 改用 `settings.effective_chat_api_key` / `effective_chat_base_url` / `effective_chat_model`；删 `if/else local-vs-api` 分支（属性已封装好）；顺手清 ruff 旧式 typing（`List`/`Dict`/`Optional`/`Tuple`/`Type` → 内建 + `X \| None`） |
| `tests/test_config_chat_override.py` | 新建 | 11 用例覆盖 chat_*/embed_* 优先级 + ChatClient 实际拿到覆盖值 |
| `.env` | 修改 | 拆 4 段（embed/chat/检索/存储/...），加 `CHAT_API_KEY` + `CHAT_API_BASE` + `CHAT_MODEL=glm-5`；顺便修复历史 GBK→UTF-8 双重编码乱码 |

## 3. 关键决策

### D1 — 单边覆盖：只加 `CHAT_API_*`，不加 `EMBED_API_*`（YAGNI）

| 候选 | 优 | 劣 |
| --- | --- | --- |
| **A. 只加 chat_*** ✅ | 1 个用户场景 1 套字段；最小变更 | 将来 embed 也想分家时还要再加一对（ABI 改动可控） |
| B. 对称加 chat_* + embed_* | 设计完整 | YAGNI：embed 暂无切家场景；4 个新字段语义负担 |
| C. 重构为单一 `provider_overrides: dict[str, ProviderConfig]` | 最灵活 | 大重构；与「最小可用」冲突 |

理由：用户实际场景就是 chat 切百炼、embed 留智谱；embed 反向场景（chat 留智谱、embed 切别家）不存在 —— 切 embed 必然要重建 ChromaDB，没人会主动去做。等真有需求再对称加 `embed_api_*`，迁移成本可忽略。

### D2 — 优先链 `chat_* or openai_*`，空串 = 未设

```python
self.chat_api_key or self.openai_api_key   # falsy 都回退
```

- `None` 回退（默认）→ ✅
- 空串 `""` 回退 → ✅（避免 `.env` 里写 `CHAT_API_KEY=` 留空被误读为有效值）
- 非空字符串 → 覆盖

测试 `test_empty_string_treated_as_falsy_and_falls_back` 锁死这个不变式。

### D3 — 顺手修 `chat_client.py` 的封装 bug

旧代码：

```python
if self.provider == "local":
    self.client = OpenAI(api_key="ollama", base_url=settings.ollama_api_base)
    self.model = settings.local_chat_model
else:
    self.client = OpenAI(
        api_key=settings.openai_api_key,        # ← 绕过 effective_*！
        base_url=settings.openai_api_base,
    )
    self.model = settings.chat_model
```

`effective_chat_*` 属性早就考虑了 local/api 分支，但 `chat_client.py`
没用，**直接读 `openai_api_*`**。本步把这段简化到 3 行：

```python
self.client = OpenAI(
    api_key=settings.effective_chat_api_key,
    base_url=settings.effective_chat_base_url,
)
self.model = settings.effective_chat_model
```

不修这个 bug 的话，`CHAT_API_KEY` 覆盖无效（chat_client 仍读 openai_api_key）。
本步必修。

### D4 — 测试用 autouse fixture 清 `os.environ`

```python
@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
```

`Settings(_env_file=None)` 只禁用 .env 加载，**不**禁用 `os.environ`。前序
测试通过 `monkeypatch.setenv` / `load_dotenv` 注入到 process env 的变量
仍会被 pydantic-settings 注入。修复路径有 3 个：

1. `Settings(_env_file=None, _env_prefix="...", _env_nested_delimiter="...")` —— 麻烦
2. 全局 conftest 清场 —— 影响范围太大
3. **本测试文件 autouse fixture** ✅ —— 最局部、最显式

整体跑前 11/11 单跑通过、整轮 8 失败正是这个污染。修完整轮 615 passed。

### D5 — `.env` 顺便修历史乱码

编辑器在 `.env` 里写中文注释时若用 GBK 编码（PowerShell 5.1 默认），跨工具读时会被当 UTF-8 解出乱码（`鏅鸿氨` 这种）。本步重写 `.env` 用 UTF-8 无 BOM，所有中文注释回到正确字符。

`.env` 不入 git，但写入文件本身是「工程质量」一部分（用户每次打开都看一次乱码也不舒服）。

## 4. 验收

- 单独跑 `pytest tests/test_config_chat_override.py` → 11 passed
- 整轮 `pytest -q --ignore=eval_ood --ignore=smoke_bm25_rrf` → **615 passed**
  （基线 604 + 11 新单测）
- scoped ruff 3 路径：0 errors（顺手修 `chat_client.py` 历史 typing 警告 14 处）
- 手动验收（用户填 key 后）：
  1. `uvicorn` 起服务，`/health/ready` 200
  2. anon 登录 → 上传 `data/uploads/个人信息保护法.txt` → 等任务 → 提问
  3. 后端日志看到 chat 请求打到 `dashscope.aliyuncs.com`、embedding 请求打到 `bigmodel.cn`

## 5. 未做 / 后续

- Phase 2 端到端验证（待用户填 key 后启动）
- Step 026c 候选：`EMBED_API_KEY` / `EMBED_API_BASE` 对称对开（**仅当**真出现 embed 切家需求时做）
- 不在本步：CI 怎么注入两组 key（GitHub Actions 加 secret 即可，无代码改动）
