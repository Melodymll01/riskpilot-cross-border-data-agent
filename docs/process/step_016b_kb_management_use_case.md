# Step 016b — KbManagementUseCase + DocumentLoaderPort（KB 管理重构第 2 阶段）

> 对应即将提交的 commit（本步与代码同 commit）
> 计划标题：`feat(app): KbManagementUseCase + DocumentLoaderPort（PR-8b / Step 016b）`

## 1. 本步骤目标

在 [Step 016a](step_016a_kb_document_repo_port.md) 把 `KbDocumentRepoPort` 装好
但**没人调用**的基础上，引入 app 层的 `KbManagementUseCase` 真正"接通"该端口
——把 v1 `service.py:KnowledgeService` 的 KB 管理面四件事（列出 / 详情 / 删除
/ 入库）业务编排迁移到 use case，并补一个 `DocumentLoaderPort` 端口让 use case
不直接依赖 `ingestion/` / `processing/` 模块。

至此 KB 管理面的"领域 + 业务编排"两层全部就位；后面 [Step 016c] 只需把
`api/v2/documents.py` 切到 `container.kb_management` 即可端到端联通，v1 删除
延迟到 [Step 016d]。本步骤继续保持"只增不删"，对 016a 基线 440 测试零回归
（465 = 440 + 25）。

## 2. 修改文件

| 文件 | +/- | 关键改动 |
|---|---|---|
| [domain/ports.py](../../domain/ports.py) | +33 | 新增 `@runtime_checkable Protocol DocumentLoaderPort` 2 方法：`load_file(file_path, *, original_filename=None, category=None) -> list[KbChunk]` / `load_web(url, *, category=None) -> list[KbChunk]`；docstring 写死"加载 + 切分一体、不做 embed / 不做写库"边界 |
| [domain/__init__.py](../../domain/__init__.py) | +2 | 公开导出 `DocumentLoaderPort` |
| [infra/kb/__init__.py](../../infra/kb/__init__.py) | rewritten | 新版 docstring 同时介绍 016a/016b 两个适配器；导出 `UnifiedLoaderAdapter` |
| [infra/kb/unified_loader_adapter.py](../../infra/kb/unified_loader_adapter.py) | +98 | 新建 `UnifiedLoaderAdapter` 包 v1 `UnifiedLoader + build_chunks`；`_normalize_source_type`（"unknown"→"file"）与 `_normalize_url`（""→None）两个 boundary 转换器；空文档（`build_chunks==[]`）返回 `[]` 不抛 |
| [app/use_cases/kb_management.py](../../app/use_cases/kb_management.py) | +148 | 新建 `KbManagementUseCase`（6 个 public 方法）+ `@dataclass(frozen=True) KbIngestResult`；私有 `_ingest_chunks` 共用"embed → repo.add_chunks"两步；不验证 admin（由 API 层 `make_require_admin` 守门） |
| [app/use_cases/__init__.py](../../app/use_cases/__init__.py) | +3 | 导出 `KbIngestResult`, `KbManagementUseCase` |
| [app/factories.py](../../app/factories.py) | +13 | 新增 `build_document_loader(settings) -> DocumentLoaderPort` 工厂；导入 `DocumentLoaderPort` + `UnifiedLoaderAdapter`；加入 `__all__` |
| [app/container.py](../../app/container.py) | +18 | 加 `document_loader: DocumentLoaderPort \| None = None` 参数；装配 `self.document_loader`；新增 `self.kb_management = KbManagementUseCase(...)` use case；docstring "9 个 Port / 4 个 use case" → "10 个 Port / 5 个 use case" |
| [tests/fakes/fake_document_loader.py](../../tests/fakes/fake_document_loader.py) | +101 | 新建 `FakeDocumentLoader`（预置 chunks / empty / 默认 2-chunk 模式 + `calls` 记录） |
| [tests/fakes/__init__.py](../../tests/fakes/__init__.py) | +2 | 导出 `FakeDocumentLoader` |
| [tests/app/test_kb_management.py](../../tests/app/test_kb_management.py) | +195 | 18 个用例：4 读 / 2 删 / 4 ingest_file / 3 ingest_web / 1 KbIngestResult frozen；用例覆盖空 source / 空文档不写库 / 覆盖语义 |
| [tests/infra/test_unified_loader_adapter.py](../../tests/infra/test_unified_loader_adapter.py) | +147 | 11 个用例：用 `_StubLoader + _StubRawDoc` 替代真 IO；覆盖 Protocol 契合、字段映射、source_type unknown 兜底、category 入参覆盖、空 source_url 归一为 None、空 content 返回 `[]` |
| [tests/infra/test_fakes.py](../../tests/infra/test_fakes.py) | +5 | 加 `FakeDocumentLoader` 的 `isinstance(DocumentLoaderPort)` 契合断言 |
| [tests/app/test_container.py](../../tests/app/test_container.py) | +15 | 全 fake 容器注入 `FakeDocumentLoader`；断言 `document_loader` Port 契合 + `kb_management` use case 装好 + 3 个依赖共享同一引用 |

合计 **+780 / -8**，14 个文件。

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **新增 `DocumentLoaderPort` 而非让 use case 直接 import `UnifiedLoader`** | use case `from ingestion.unified_loader import UnifiedLoader` | 严格 DDD：app 层不应依赖 ingestion/processing 这类基础设施模块。Port 化后 use case 只看到 3 个 Port（loader / embedder / repo），测试时全部可替换 |
| **`DocumentLoaderPort` 把"加载 + 切分"打包，而非拆成 `LoaderPort` + `ChunkerPort`** | 拆两个 Port | YAGNI：当前没有"加载完不切分"或"对其他来源切分"的场景；拆开会让 use case 多一次编排步骤。等真出现需要独立切分时再拆 |
| **`load_file/web` 返回 `list[KbChunk]` 而非 `LoadedDocument`** | 返回 DTO 再让 use case 切分 | 切分本身是 IO 链路的纯函数后置，让 adapter 一次走完更内聚；同时 `KbChunk` 已经是 domain 类型，无需引入第二个 DTO |
| **`UnifiedLoaderAdapter` 复用 v1 `UnifiedLoader` + `build_chunks` 而非重写** | 全新实现 | v1 的 file_loader / web_loader / cleaner / splitter 已经稳定运行 + 有测试覆盖；本步骤的目标是"重构上层架构"，不是"重写下层 IO"；wrap 是最小风险路径 |
| **adapter 内部 `from ingestion ... import build_chunks` 顶层 import** | 函数内懒 import | adapter 本就在 infra 层，依赖 infra/ingestion 是合理的；懒 import 只为打破循环引用，本案没有循环 |
| **`KbManagementUseCase` 不验证 admin 权限** | use case 内 `if not user.is_admin: raise PermissionError` | 已经有 `api/v2/deps.py:make_require_admin` 在路由层守门；use case 不应感知 HTTP 身份概念。保持 use case "纯业务"职责 |
| **`ingest_file(file_path: str)` 而非 `ingest_file(file: BinaryIO)`** | use case 直接收文件流 | 文件落地（multipart → 临时目录）是 HTTP 边界关心的事；use case 收 path 让 API 层先 save_upload 再调 use case，职责清晰 |
| **`KbIngestResult` 用 `@dataclass(frozen=True)` 而非 Pydantic** | `BaseDomainModel` 子类 | 这是 use case 的返回值（API 层会再用 Pydantic schema 序列化）；不需要 domain 不变量保护，也不需要 JSON round-trip；dataclass 更轻 |
| **`message: str` 携带中文友好提示** | 只返回 success+source+count 让 API 层拼 message | 同 v1 `service.IngestResult.message` 行为对齐；API 层后续可直接透传给前端 toast，无需再判分支 |
| **空文档（`chunks == []`）返回 `success=False` 而非 raise** | `raise EmptyDocumentError` | 用户视角："上传一个空 PDF 不是错误，只是没东西可学"；同 v1 行为；API 层用 200 + body 返回，比 4xx 更适合"无害但无效"的语义 |
| **`add_chunks` 的"先删后插"靠 016a Port 契约保证，use case 不重复 delete** | use case 显式 `repo.delete_document(name)` 再 `repo.add_chunks(...)` | 016a 已经把契约下沉到 Port 层；use case 重复 delete 会破坏单一职责，也不利于将来 ChromaKbRepo 优化为单事务（如果 chroma 支持的话） |
| **embedder 用 `EmbedPort` 而非新建专门的"入库 embedder"** | 单独 `IngestEmbedPort` | `EmbedPort.embed(texts) -> list[list[float]]` 已经够用；入库与检索的 embedding 必须用同一模型保证向量空间一致——共用 Port 强制这一点 |
| **`FakeDocumentLoader` 提供"默认 2-chunk"+"empty"+"preset chunks" 三种模式** | 只让测试预置 chunks | 默认模式让一些不关心具体内容的测试（如容器装配测试）无需准备数据；`empty=True` 一行就能模拟空文档；preset 给需要精确控制的用例（覆盖 / 多源 / category）用 |
| **`KbManagementUseCase` 的 5 个写方法都做 owner-style 空字符串校验** | 信赖 API 层 schema 校验 | API 层 Pydantic schema 会先校验，但 use case 单测直接调用时（无 API 层）需要自我保护；多一层防御几乎零成本 |
| **`_ingest_chunks` 是私有 helper 而非两个 public 方法各自展开** | 两份相似代码 | ingest_file / ingest_web 的"loader 那一步"不同，但"embed → write"两步完全一致；提取私有 helper 让差异点（loader call）一目了然 |

## 4. 核心契约 / 接口

### `DocumentLoaderPort`

```python
@runtime_checkable
class DocumentLoaderPort(Protocol):
    def load_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
    ) -> list[KbChunk]: ...

    def load_web(
        self,
        url: str,
        *,
        category: str | None = None,
    ) -> list[KbChunk]: ...
```

### `KbManagementUseCase`（6 个 public 方法）

```python
class KbManagementUseCase:
    def __init__(
        self,
        *,
        kb_repo: KbDocumentRepoPort,
        loader: DocumentLoaderPort,
        embedder: EmbedPort,
    ) -> None: ...

    # 读
    def list_documents(self) -> list[KbDocument]: ...
    def get_document(self, source_name: str) -> KbDocument | None: ...
    def count_chunks(self) -> int: ...

    # 写
    def delete_document(self, source_name: str) -> int: ...
    def ingest_file(
        self,
        file_path: str,
        *,
        original_filename: str | None = None,
        category: str | None = None,
    ) -> KbIngestResult: ...
    def ingest_web(
        self,
        url: str,
        *,
        category: str | None = None,
    ) -> KbIngestResult: ...
```

### `KbIngestResult`

```python
@dataclass(frozen=True)
class KbIngestResult:
    success: bool
    source_name: str
    chunk_count: int
    message: str
```

### `UnifiedLoaderAdapter`（boundary 规则）

```python
class UnifiedLoaderAdapter:
    def __init__(self, loader: UnifiedLoader | None = None) -> None: ...

    # RawDocument → list[KbChunk]：
    # - source_type 字符串 → KbSourceType Literal（"unknown" 兜底 "file"）
    # - source_url 空字符串 → None（KbChunk 字段 str | None）
    # - title/category 空 → "" （KbChunk 字段 str = ""）
    # - category 入参非空时覆盖 cwm.category
    # - build_chunks 返回空 → 返回空列表（不抛）
```

## 5. 验证清单

```powershell
# scoped ruff
ruff check domain/ports.py domain/__init__.py infra/kb \
  app/use_cases/kb_management.py app/use_cases/__init__.py \
  app/factories.py app/container.py \
  tests/fakes/fake_document_loader.py tests/fakes/__init__.py \
  tests/app/test_container.py tests/app/test_kb_management.py \
  tests/infra/test_fakes.py tests/infra/test_unified_loader_adapter.py
# → All checks passed!

# scoped mypy
mypy app/use_cases/kb_management.py infra/kb
# → 新代码 0 错；遗留 42 错全在 retrieval/processing/老 config

# focused tests
pytest -q tests/app/test_kb_management.py \
  tests/infra/test_unified_loader_adapter.py \
  tests/infra/test_fakes.py tests/app/test_container.py
# → 47 passed in 1.10s

# full regression
pytest -q
# → 465 passed（016a 基线 440 + 新增 25），零回归
```

## 6. 与 v1 共存策略（继续延后删除到 016d）

依然**不删任何 v1 代码**：

- `service.py:KnowledgeService.ingest_file/ingest_web/list_sources/delete_source` 仍在；
- `api/routes.py` 旧 `/api/upload/file` `/api/upload/web` `/api/sources` 仍可用；
- 前端"知识库管理"面板仍走老 API；
- 本步骤新增的 `AppContainer.kb_management` 依然只是**挂在那里**，没有 API 调用点。

[Step 016c] 才在 `api/v2/documents.py` 引入新路由调用本 use case；
[Step 016d] 再删除 v1 入口 + 前端切到新 API。

## 7. 已知风险 / 后续工作

| 风险 | 缓解 | 跟进 |
|---|---|---|
| `UnifiedLoaderAdapter` 内部 import `UnifiedLoader` 时仍会构造 `FileLoader + WebLoader`（含 requests session 等） | adapter `__init__` 接受可选 loader 注入；测试不走真 IO | 016c 切真 API 时确认进程启动开销可接受 |
| `KbManagementUseCase` 不验证 admin —— 单元层调用如果绕过 API 可入侵 | 016c 在 API 路由层强制 `require_admin` 守门；项目里所有 use case 调用点都走 container | 长期：可加 `make_require_admin` 拦截 use case 入口的可选 decorator |
| `ingest_file(file_path)` 收的是已落地路径，API 层需要先 save upload；流式大文件场景下 disk IO 多一跳 | 当前文件规模小（PIPL.txt 等 < 1MB）；同 v1 行为 | 大文件场景可在 016c 增加可选 stream 参数 |
| FakeDocumentLoader 与真 adapter 行为对照只靠两个测试文件，未来 schema 变更时容易漂移 | 测试都在 `tests/` 同级目录便于一起 grep | 若 016c/016d 阶段发现漂移，再加 parametrized contract test |
| `_ingest_chunks` 没有"长度不一致回退" —— 真 embedder 若返回 0 vec 会触发 KbDocumentRepoPort `ValueError` | 016a `add_chunks` 已显式 raise；上层 API 层会捕 `ValueError` 翻译为 4xx | 不修；让错误尽早暴露 |

## 8. 下一步

- **Step 016c**：`api/v2/documents.py` 引入 4 个端点（`GET /` 列表 / `GET /{src}` 详情 /
  `DELETE /{src}` 删 / `POST /` 上传文件 + `POST /web` 抓网页），全部走
  `container.kb_management`；保留旧 `/api/v1/upload` 接口并打 deprecation 日志；
  前端"知识库管理"页面切到 `/api/v2/documents`；端到端打通。
- **Step 016d**：删除 v1 —— `service.py:KnowledgeService.ingest_file/ingest_web/
  list_sources/delete_source`、`api/routes.py` 旧 upload/sources 端点、前端旧
  fetch 代码；保留必要迁移测试确认旧端点 410 Gone。

——三步之间各自 commit + 测试绿，整体 016 完成后一次 push。
