"""``AuditLogPort`` Fake：in-memory list 存储（Step 021）。

设计目标：
- 满足 ``AuditLogPort`` Protocol 的 isinstance 契约检查
- 暴露 ``entries`` 让测试直接断言副作用
- 行为与 ``SqliteAuditLogRepo`` 语义对齐：list_recent 按 timestamp 倒序、
  同时间戳后写入的在前
"""

from __future__ import annotations

from copy import deepcopy

from domain.models import AuditEntry


class FakeAuditLogRepo:
    """in-memory ``AuditLogPort`` 实现。"""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        # deepcopy 防外部 mutate（虽然 AuditEntry frozen，extra_json dict 内部仍可变）
        self.entries.append(deepcopy(entry))

    def list_recent(
        self,
        *,
        limit: int = 50,
        action: str | None = None,
        actor_id: str | None = None,
    ) -> list[AuditEntry]:
        # 倒序：先按 timestamp 倒序；同 timestamp 按写入顺序倒序（后写入的在前）
        # 用 enumerate 索引作 secondary key，避免 sort 不稳定
        indexed = list(enumerate(self.entries))
        indexed.sort(key=lambda p: (p[1].timestamp, p[0]), reverse=True)
        out: list[AuditEntry] = []
        for _idx, e in indexed:
            if action is not None and e.action != action:
                continue
            if actor_id is not None and e.actor_id != actor_id:
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out
