# Step 016a — KbDocumentRepoPort + ChromaKbRepo（KB 管理重构第 1 阶段）

> 对应即将提交的 commit（本步与代码同 commit）
> 计划标题：`feat(infra): KbDocumentRepoPort + ChromaKbRepo + Fake（PR-8a / 016a）`

## 1. 本步骤目标

启动 [Step 016 "KB 管理面重构"](README.md) 的**第 1 阶段（infra 层）**：把"知识库
文档管理"这块过去散落在 `service.py` / `retrieval/search/vector_store.py` /
旧 API 路由的能力，按 DDD 分层重做。

本阶段只做"端口 + 实现 + 测试 + 装配"四件事，不动 API、不动 use case、不动
前端、不删任何 v1 代码：

- **domain 层**：定义 `KbDocument` / `KbChunk` / `KbSourceType` 三个 frozen
  model，定义 `KbDocumentRepoPort` 5 方法 Protocol
- **infra 层**：实现 `ChromaKbRepo`（包 `VectorStore`，"先删后插"幂等语义）
- **测试 fakes**：实现 `FakeKbRepo`（in-memory，行为与 ChromaKbRepo 对齐）
- **DI 装配**：`app/factories.build_kb_repo` + `AppContainer.kb_repo` 接入

下一步（016b）才在 use case 层引入 `KbManagementUseCase`，再下一步（016c）
切 API，最后（016d）删除 v1。本步骤所有改动都**只增不删**，对现有 413 条
测试零回归（440 passed = 413 + 27 新增）。

## 2. 修改文件

| 文件 | +/- | 关键改动 |
|---|---|---|
| [domain/models.py](../../domain/models.py) | +28 | 新增 `KbSourceType = Literal["file", "web"]`；`KbDocument`（source_name / source_type / title / source_url / chunk_count / category，frozen + extra="forbid"）；`KbChunk`（额外含 chunk_id / text / chunk_index，text min_length=1，chunk_index ≥ 0） |
| [domain/ports.py](../../domain/ports.py) | +35 | 导入 `KbChunk, KbDocument`；新增 `@runtime_checkable Protocol KbDocumentRepoPort` 5 方法：`list_documents() -> list[KbDocument]` / `get_document(source_name) -> KbDocument \| None` / `count_chunks() -> int` / `delete_document(source_name) -> int` / `add_chunks(chunks, embeddings) -> None`，并在 docstring 里写死"先删后插"幂等契约 |
| [domain/__init__.py](../../domain/__init__.py) | +6 | 公开导出 `KbChunk` / `KbDocument` / `KbSourceType` / `KbDocumentRepoPort`，加入 `__all__` |
| [infra/kb/__init__.py](../../infra/kb/__init__.py) | +3 | 新建子包，公开 `ChromaKbRepo` |
| [infra/kb/chroma_kb_repo.py](../../infra/kb/chroma_kb_repo.py) | +110 | `ChromaKbRepo(vector_store)` 实现 `KbDocumentRepoPort`；`_to_kb_document` 把老 `get_all_sources()` dict 兜底转 `KbDocument`（包括 `"unknown"` → `"file"`、空字符串 url → None）；`_to_chunk_with_metadata` 1:1 映射 `KbChunk → ChunkWithMetadata`；`add_chunks` 验证长度后按 `{c.source_name for c in chunks}` 做先删后插 |
| [tests/fakes/fake_kb_repo.py](../../tests/fakes/fake_kb_repo.py) | +95 | 新建 `FakeKbRepo`（`defaultdict[str, list[KbChunk]]` 存储 + `calls` / `written_chunks` 副作用记录），与 ChromaKbRepo 同语义 |
| [tests/fakes/__init__.py](../../tests/fakes/__init__.py) | +2 | 公开 `FakeKbRepo` |
| [tests/infra/test_chroma_kb_repo.py](../../tests/infra/test_chroma_kb_repo.py) | +145 | 12 个用例：协议契合 1 / 读侧 4 / 写侧 7，覆盖字段映射、unknown 兜底、length mismatch 抛错、空 noop、先删后插、多源批量删 |
| [tests/infra/test_fakes.py](../../tests/infra/test_fakes.py) | +60 | 加 `FakeKbRepo` Port 契合检查 + 5 个行为用例（初始空 / add+list / 同 source 覆盖 / delete 计数 / length mismatch） |
| [tests/domain/test_models.py](../../tests/domain/test_models.py) | +90 | `TestKbDocument` 5 用例 + `TestKbChunk` 5 用例：覆盖 happy path / Literal 校验 / ge 约束 / min_length / JSON round-trip |
| [app/factories.py](../../app/factories.py) | +13 | 新增 `build_kb_repo(settings) -> KbDocumentRepoPort` 工厂；导入 `KbDocumentRepoPort` + `ChromaKbRepo`；加入 `__all__` |
| [app/container.py](../../app/container.py) | +5 | `__init__` 增 `kb_repo: KbDocumentRepoPort \| None = None` 参数；装配 `self.kb_repo = kb_repo or build_kb_repo(settings)`；TYPE_CHECKING 导入；docstring "8 个 Port" → "9 个 Port" |
| [tests/app/test_container.py](../../tests/app/test_container.py) | +4 | 全 fake 容器注入 `FakeKbRepo`；断言 `isinstance(c.kb_repo, KbDocumentRepoPort)` |

合计 **+596 / -16**，13 个文件。

## 3. 设计决策

| 选择 | 取代方案 | 原因 |
|---|---|---|
| **方案 B：完整 DDD 重构（分 016a/b/c/d）** | 方案 A：写 `service.py` 薄包装（strangler fig） | 用户明确要求"完整的重构"。Strangler 留一层 service.py 永远没人去掉，反而长期是技术债。分阶段才能每步保持测试绿 + commit 可独立回滚 |
| **infra 层先行（016a），API 层最后切（016c）** | 自顶向下 API → use case → infra | 自底向上每一层都有完整单测，API 切换前 use case 已经能在 fake 上跑通；自顶向下会把 fake 路径推到 API 层造成临时不一致 |
| **`KbChunk` 与 `Chunk` 并存而非合并** | 复用现有 `Chunk` | 两者职责不同：`Chunk` 是检索侧返回值（含 score / metadata 透传）；`KbChunk` 是写侧入库语义（要求 chunk_index ≥ 0、text 非空、不含 score）。合并会让 `Chunk.score` 在写侧变 Optional 污染检索路径 |
| **`KbDocumentRepoPort` 只暴露 5 个方法** | 把 `search_by_source` / `get_chunks_of(source)` 都塞进来 | YAGNI：管理面 4 个操作（列出 / 查看 / 删除 / 上传）只需要这 5 个。检索仍走 `RetrievePort`，写后查走 `count_chunks()` / `list_documents()` 间接验证 |
| **`add_chunks` 内含"先删后插"语义** | 调用方先 `delete_document` 再 `add_chunks` | 上传同名文件是"覆盖"语义而非"追加"；如果让调用方拼这两步，每个调用点都得记得删，第一次有人忘记 = 重复入库 chunks。把契约下沉到 Port 才能保证不变量 |
| **`add_chunks` 用 set 去重 source_name 再删** | 按 chunks 顺序逐个 `pop` | 同一文件的 100 个 chunk 不要触发 100 次 delete；set 推导一次确定要清的源，O(unique sources) 而非 O(chunks) |
| **`add_chunks(chunks, embeddings)` 而非 `add_chunks(chunks)` 内部 embed** | Port 包含 embedding | 单一职责：embed 是 `EmbedPort` 的事，仓储不应耦合到 embedder。同样的 chunks 在 ingest 期间已经 embed 过，进 Repo 不需要再算一次 |
| **`_to_kb_document` 容忍 `"unknown"` 老数据 → `"file"` 兜底** | 严格 raise 拒绝 | 现有 chroma 持久化目录里有 6 条 `unknown` 来源的旧数据；硬 raise 会让历史数据不可见、要求用户重建索引才能用新 API。兜底为 `file` 让旧数据可读、新写入用准确 type |
| **空 url 归一为 `None` 而非保留空串** | 透传 `""` | Pydantic `HttpUrl` 校验空串会失败；domain 模型用 `str \| None` 默认 None；归一在 boundary（infra → domain）做一次，下游永远不用判 `if url and url != ""` |
| **`ChromaKbRepo` 注入 `VectorStore` 而非自己 new** | 内部 `self._vs = VectorStore()` | 测试时无法 mock；遵循"依赖通过构造器注入"原则；工厂 `build_kb_repo` 才是建 VectorStore 的地方 |
| **`build_kb_repo` 每次 new 一个 `VectorStore`** | 与 `HybridRetrieverAdapter` 共享同一个 VectorStore 单例 | chromadb 的 `PersistentClient` 内部按 `persist_dir` 缓存；两个 VectorStore 实例指向同一个 collection，写入对检索可见，无需在 container 层引入 `vector_store` singleton 复杂度 |
| **`FakeKbRepo` 与 `ChromaKbRepo` 同语义** | Fake 只满足类型不管语义 | 测试用 Fake 写 use case 时如果语义不一致（比如 Fake 不"先删后插"），生产切真适配器会出新 bug。Fake 必须是 contract test 的对照组 |
| **`FakeKbRepo.calls` 用 `list[tuple[str, tuple]]`** | 用 `list[dict]` | 与项目里 `FakeWebSearch.calls = list[tuple[str, int]]` 风格一致；tuple 不可变，断言时 `repo.calls[0] == ("delete_document", ("x",))` 更直接 |
| **`FakeKbRepo.written_chunks` 用 `deepcopy`** | 直接持引用 | KbChunk 是 frozen Pydantic model 已经不可变，理论上不用 deepcopy；但 Fake 是测试基础设施，对调用方传入的对象做防御性 copy 是稳妥默认；性能在测试场景无差 |
| **同一 commit 既加 Port 也加 Fake 和工厂** | 拆 4 个微 commit | Port 单独提交，AppContainer 会因为 `kb_repo` 必填挂掉；Port + 工厂提交，未注入 fake 的容器测试会试着真 new VectorStore；4 项必须原子。下一个 commit（016b）只动 use case，依然可拆 |

## 4. 核心契约 / 接口

### `KbDocument`（管理面视角）

```python
class KbDocument(BaseDomainModel):
    """知识库中一个"文档"的聚合视角（按 source_name 聚合 N 个 chunk）。"""
    source_name: str = Field(min_length=1)
    source_type: KbSourceType  # Literal["file", "web"]
    title: str | None = None
    source_url: str | None = None
    chunk_count: int = Field(ge=0)
    category: str | None = None
```

### `KbChunk`（写侧 chunk）

```python
class KbChunk(BaseDomainModel):
    """写入知识库的单个 chunk（不含 embedding、score）。"""
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_type: KbSourceType
    title: str | None = None
    source_url: str | None = None
    chunk_index: int = Field(ge=0)
    category: str | None = None
```

### `KbDocumentRepoPort`（5 方法）

```python
@runtime_checkable
class KbDocumentRepoPort(Protocol):
    def list_documents(self) -> list[KbDocument]: ...
    def get_document(self, source_name: str) -> KbDocument | None: ...
    def count_chunks(self) -> int: ...
    def delete_document(self, source_name: str) -> int:
        """删除 source_name 下所有 chunk；不存在返回 0（幂等）。"""
    def add_chunks(
        self,
        chunks: list[KbChunk],
        embeddings: list[list[float]],
    ) -> None:
        """
        "先删后插"幂等写入：

        1. 对 `{c.source_name for c in chunks}` 中每个 source，先 delete 旧数据
        2. 再写入 chunks 与 embeddings（长度必须一致，否则抛 ValueError）
        3. 空 chunks 调用是 noop（不删任何东西）
        """
```

### `ChromaKbRepo`（生产实现要点）

```python
class ChromaKbRepo:
    def __init__(self, vector_store: VectorStore) -> None:
        self._vs = vector_store

    def list_documents(self) -> list[KbDocument]:
        return [self._to_kb_document(s) for s in self._vs.get_all_sources()]

    def add_chunks(self, chunks, embeddings) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("...")
        if not chunks:
            return
        for src in {c.source_name for c in chunks}:  # set 去重
            self._vs.delete_by_source(src)
        cwm_list = [self._to_chunk_with_metadata(c) for c in chunks]
        self._vs.add_chunks(cwm_list, embeddings)

    @staticmethod
    def _to_kb_document(raw: dict) -> KbDocument:
        raw_type = raw.get("source_type", "file")
        src_type: KbSourceType = "web" if raw_type == "web" else "file"  # 兜底
        url = raw.get("source_url") or None
        return KbDocument(...)
```

## 5. 验证清单

```powershell
# scoped ruff
ruff check domain/models.py domain/ports.py domain/__init__.py infra/kb \
  tests/fakes/fake_kb_repo.py tests/fakes/__init__.py \
  tests/infra/test_chroma_kb_repo.py tests/infra/test_fakes.py \
  tests/domain/test_models.py app/factories.py app/container.py \
  tests/app/test_container.py
# → All checks passed!

# scoped mypy
mypy domain infra/kb app/factories.py app/container.py
# → 新代码 0 错；遗留 42 错全在 retrieval/processing/config，本步不涉及

# focused tests
pytest -q tests/domain/test_models.py tests/infra/test_fakes.py \
  tests/infra/test_chroma_kb_repo.py tests/app/test_container.py
# → 85 passed in 3.67s

# full regression
pytest -q
# → 440 passed (基线 413 + 新增 27)，零回归
```

## 6. 与 v1 共存策略

本步骤**没有删除任何 v1 代码**：

- `service.py` 旧的知识库操作仍在
- `retrieval/search/vector_store.py:VectorStore` 仍是检索 + 旧 API 共用
- 旧 `/api/v1/sources` 路由仍可用
- 前端的"知识库管理"页面没有动

新增的 `AppContainer.kb_repo` 只是**挂在那里**，没有任何调用点 —— 直到 [Step 016b]
引入 `KbManagementUseCase` 把它"接通"。这保证：

1. 本 commit 不破任何 v1 调用方
2. 即使 016b/c 中途出问题需要回滚，只回滚 016b/c 即可，016a 单独可独立存在
3. v1 删除集中放到 [Step 016d]，那时 016c 的 API/前端已经全切完，删除安全可控

## 7. 已知风险 / 后续工作

| 风险 | 缓解 | 跟进 |
|---|---|---|
| `ChromaKbRepo` 在生产环境会和 `HybridRetrieverAdapter` 各持有一份 VectorStore 实例 | chromadb `PersistentClient` 按 path 单例缓存，写入对检索可见 | 真实端到端测试在 016c |
| 老 chroma 持久化目录里残留 `source_type == "unknown"` 的脏数据 | `_to_kb_document` 兜底为 `"file"`；不影响读、新写入会用准确 type | 不修；旧数据自然衰减 |
| `KbDocument.source_url: str \| None` 而非 `HttpUrl` | 后期想加 URL 校验时需要数据迁移；当前与 chroma 旧 metadata 容忍空字符串/未填值对齐 | 留待 v2 schema 升级时考虑（不在 016 范围） |
| `add_chunks` 不在事务内 —— delete 完后 add 报错会留下被删空但未重建的源 | chromadb 客户端本身不支持事务；本步骤维持现有 v1 行为，不引入新问题 | 长期：在 use case 层做幂等重试 |
| 没加 contract test "FakeKbRepo 与 ChromaKbRepo 行为对照" | 当前手写两个测试文件，要靠 reviewer 对照 | 后续 016b/c 如果发现 fake 漂移再加 parametrize 共享用例 |

## 8. 下一步

- **Step 016b**：`KbManagementUseCase` —— 在 app 层定义"列出文档 / 删除文档 /
  上传文件并入库"3 个 use case 方法，编排 `KbDocumentRepoPort` + `EmbedPort` +
  ingestion（loader / splitter / metadata）；写 use case 单测（全 fake）
- **Step 016c**：`api/v2/documents.py` 切到新 use case；旧 `/api/v1/sources`
  保留并打 deprecation 日志；前端"知识库管理"页面切到新 API；端到端打通
- **Step 016d**：删除 v1 —— `service.py` 中 KB 相关方法、旧 API 路由、旧
  前端 fetch 代码；保留必要的迁移测试确认旧端点 410 Gone

——三步之间各自 commit + 测试绿，整体 016 完成后一次 push。
