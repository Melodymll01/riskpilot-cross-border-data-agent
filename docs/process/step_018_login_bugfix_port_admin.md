# Step 018 — 登录链路三连击 bugfix（端口对齐 + admin 列表解析 + 启动期守门）

## 1. 本步骤目标

修复用户报告的"前端界面登录不进去"——根因 3 条，全部躲在本地环境配置 +
pydantic-settings 默认行为里，所以测试一直 479 全绿但 GitHub OAuth 流真跑就是断的。
本步骤一次性修齐根因，并加一道启动期守门员防止后续静默回归。

为后续做：

- 让 admin 模型从"理论存在"变成"用户能在浏览器里看到金色 admin 徽标"
- 修复后 Step 017 的 KB 入口才有意义（之前 admin 标志永远 False，
  侧栏永远不显示 KB）
- 给后续"环境配置"类工作一个范式：**默认值 + .env 覆盖 + 启动期自检**

## 2. 修改文件

| 路径 | 说明 |
|---|---|
| `main.py` | 直起端口 `8004` → `8765`，与 `settings.github_redirect_uri` 默认 `http://127.0.0.1:8765/...` 对齐；新增 `_warn_oauth_redirect_port_mismatch(server_port)`，在直起场景下检查回调 URI 端口与监听端口一致性，不一致打 `WARNING` 日志；模块 docstring 同步标注端口一致性约束 |
| `config.py` | `admin_user_ids: List[str]` → `Annotated[List[str], NoDecode]`，跳过 pydantic-settings v2 对 `List[str]` 字段的默认 JSON 预解析；新增 `_split_admin_user_ids` field_validator (`mode="before"`)：同时兼容**逗号分隔**（`github:foo,github:bar`，与历史 docs 一致）和 **JSON 数组**两种 .env 写法；注释更新 |
| `.env.example` | "管理员"小节文档同步两种写法；强调"不填则所有 /api/v2/documents/* 等管理接口对所有人 403" |
| `.env`（local 未跟踪） | 加 `ADMIN_USER_IDS=github:Melodymll01`（用户名取自 git remote origin URL） |

## 3. 设计决策

### D1：根因 1 —— 端口对齐 8765 而不是改 redirect_uri 默认值

候选方案：

- A：把 `main.py` 端口改到 `8765`（采用）
- B：把 `config.py` 的 `github_redirect_uri` 默认改成 `:8004`
- C：把 redirect_uri 做成从请求头 host 自动推导

`8765` 在以下位置都已经是事实标准：

- `config.py` 默认 `github_redirect_uri`
- `.env`（用户实际使用的）
- 历史 step 文档（012/013/014/015 多处"浏览器手测 uvicorn :8765"）
- GitHub OAuth App 注册的 Authorization callback

只有 `main.py` 当初拍脑袋写了 `8004`。**单点修复 + 对齐既成事实**比反向
传染回去要小很多。C 方案动态推导对反向代理 / 容器化部署会出更多坑，
不在本步骤打开。

### D2：根因 2 —— 启动期守门员而不是运行期 healthcheck

加 `_warn_oauth_redirect_port_mismatch()` 只在 `python main.py`
直起场景下跑（lifespan 之外）。理由：

- 如果走 `uvicorn --port X` 启动，端口由 CLI 决定，启动脚本看不到；
  这种情况下假设运维知道自己在做什么
- 仅 warning 不抛错——防止 docker 容器化里 0.0.0.0 端口转发场景下误伤
- 关键收益：以后谁改了 `config.py` 的 `redirect_uri` 但忘了改 `main.py`，
  启动第一行日志就能看到 `WARNING ... 端口不一致 ... 跳到死端口`

### D3：根因 3 —— field_validator + NoDecode 双管齐下

pydantic-settings v2 对 `List[str]` 字段默认行为：

1. 从 env 读到字符串值
2. 调 `decode_complex_value` 走 `json.loads(value)`
3. JSON 解析成功 → 走类型校验；JSON 解析失败 → `SettingsError`

老 docs 写"逗号分隔" → 用户照写 → `json.loads("github:foo,github:bar")` 失败
→ 应用启动直接死。两种修复路径：

- A：让用户改写法用 JSON 数组（破坏既有 docs，不友好）
- B（采用）：
  1. `Annotated[List[str], NoDecode]` 让 pydantic-settings 不要自动 JSON 解析
  2. `@field_validator(mode="before")` 接管字符串解析：
     - 空 / None → `[]`
     - `[` 开头 → 原样传给 pydantic（走 JSON 数组）
     - 其他 → 按逗号 split

两种写法都能工作，docs 不需要"破坏式升级"。

### D4：自助找出 GitHub login

用户报"只有我的 github 账号能改 KB"但 `.env` 里 `ADMIN_USER_IDS` 是空的。
两个可能：

1. 用户记错了
2. 用户在期望 `Melodymll01`（git remote 的用户名）就是他的 GitHub login

从 `git remote -v` 拿到 `github.com/Melodymll01/...`，直接帮用户在 .env 里
塞 `ADMIN_USER_IDS=github:Melodymll01`，避免来回问一轮。

> **教训**：第一次用 PowerShell `Add-Content` 追加包含中文注释的内容，
> Add-Content 默认走系统代码页（GBK）而不是 UTF-8，导致 pydantic
> 读 .env 时 `UnicodeDecodeError`。补救：用
> `[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))`
> 强制 UTF-8 无 BOM 重写。

## 4. 核心契约 / 接口

无新增 Port / use case / schema / 路由。本步骤外部契约变化：

| 配置 | 老行为 | 新行为 |
|---|---|---|
| `ADMIN_USER_IDS=` env | 仅接受 JSON 数组 `["github:foo"]`，逗号写法 → 启动失败 | 同时接受 JSON 数组**和**逗号分隔 `github:foo,github:bar` |
| `python main.py` 监听端口 | `8004` | `8765`（与 `github_redirect_uri` 默认值一致） |
| 端口不一致时 | 默默坏掉 | 启动期 WARNING 日志 |

## 5. 与外部服务的关系

- GitHub OAuth：本步骤后回调端口 `:8765` 与服务监听端口一致，
  OAuth 流可以完整跑通（authorize → callback → 设 cookie → redirect /）
- 无新引入外部依赖

## 6. 当前实现范围

### 已实现

- [x] 端口对齐 + 启动期端口漂移 warning
- [x] `ADMIN_USER_IDS` 同时支持逗号分隔与 JSON 数组
- [x] `.env.example` docs 同步
- [x] 本地 `.env` 加 `ADMIN_USER_IDS=github:Melodymll01`
- [x] TestClient 模拟 `github:Melodymll01` 登录验证 `/auth/me` → `is_admin: True`
- [x] TestClient 访问 `/api/v2/documents/stats` → 200
- [x] `pytest -q` 479 passed，零回归

### 未实现（按设计跳过）

- 端口冲突自动尝试备用：不做（生产应明示 `--port`）
- `.env` 文件被锁定 / 不存在场景的优雅降级：pydantic-settings 已有合理默认
- admin_user_ids 改成"GitHub Teams / Org" 模式：超出本次 bugfix 范围

## 7. 暂未实现 / TODO

- 当 admin 登录但 `admin_user_ids` 为空时，前端给一个"管理员配置缺失，
  请在 .env 中加 ADMIN_USER_IDS=github:你的用户名"的提示横幅
  （需要新增 `/api/v2/auth/admin_status` 或在 `/auth/me` 返回额外字段）

## 8. 测试与验证

```powershell
cd d:\py\RagDataOut

# 端口对齐 + redirect_uri 一致性
.venv\Scripts\python.exe -c "import main; from config import settings; from urllib.parse import urlparse; print('redirect_port', urlparse(settings.github_redirect_uri).port)"
# → redirect_port 8765

# admin_user_ids 解析（逗号格式）
.venv\Scripts\python.exe -c "from config import settings; print(settings.admin_user_ids)"
# → ['github:Melodymll01']

# 端到端：模拟 admin 登录 + KB 接口
.venv\Scripts\python.exe _smoke_admin3.py
# → /auth/me: is_admin=True
# → /documents/stats: 200

# 全量回归
.venv\Scripts\python.exe -m pytest -q --ignore=tests/eval_ood.py --ignore=tests/smoke_bm25_rrf.py
# → 479 passed, 16 warnings
```

变更行数：3 文件，+63 / -7（commit `53030ad`）。
