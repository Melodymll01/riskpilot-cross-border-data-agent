"""``/api/v2/audit/*`` 路由：管理员审计日志只读查询 + CSV 导出（Step 021 / 026a）。

设计要点：
- **admin-only**：所有端点都过 ``make_require_admin``；非管理员看不到他人审计
- **只读**：审计记录不可篡改；不暴露 update / delete
- 业务层走 ``container.audit_log``（``AuditLogPort`` 默认 ``SqliteAuditLogRepo``）
- 过滤参数：``action`` / ``actor_id`` / ``limit``（防大量回拉）

CSV 导出（Step 026a）：
- ``GET /audit/export.csv?action=&actor_id=&max_rows=10000``
- 返回 ``text/csv; charset=utf-8`` + ``Content-Disposition: attachment``
- ``StreamingResponse`` + ``csv.writer`` 流式输出，不一次性 list
- timestamp 双列：ISO 8601 UTC + Unix epoch（方便 Excel 排序 / diff）
- ``extra_json`` 保留原 JSON 字符串（schema 不固定不扯平）

未来扩展（暂不做）：
- 时间范围 ``since`` / ``until`` 过滤（Step 026b 候选）
- cursor 风格分页（当前用 offset，足够 admin 翻看场景）
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from api.v2.deps import make_require_admin
from api.v2.schemas import AuditEntryOut, AuditLogListResponse

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.container import AppContainer
    from domain.models import AuditEntry

# CSV 列顺序稳定，下游 BI 脚本可依赖（增加列只能追加在末尾）
CSV_HEADER: tuple[str, ...] = (
    "timestamp_iso",
    "timestamp_epoch",
    "actor_id",
    "action",
    "resource",
    "request_id",
    "success",
    "error",
    "extra_json",
)

# 一次导出的硬上限：防 admin 误触 limit 取太大把 SQLite/内存打爆
DEFAULT_MAX_ROWS = 10_000
# StreamingResponse 底层按多少行 flush一次，平衡 chunk 头开销与内存占用
CSV_FLUSH_EVERY = 200


def _to_entry_out(e: AuditEntry) -> AuditEntryOut:
    return AuditEntryOut(
        actor_id=e.actor_id,
        action=e.action,
        resource=e.resource,
        timestamp=e.timestamp,
        request_id=e.request_id,
        success=e.success,
        error=e.error,
        extra_json=e.extra_json,
    )


def build_audit_routes(container: AppContainer) -> APIRouter:
    """构造 ``/audit`` 子 router；全部端点 admin-only。"""

    router = APIRouter(prefix="/audit", tags=["audit"])
    require_admin = make_require_admin(container)

    @router.get(
        "/logs",
        response_model=AuditLogListResponse,
        summary="列出审计日志（admin-only · 按时间倒序）",
    )
    def list_audit_logs(
        limit: int = Query(50, ge=1, le=500, description="返回上限，默认 50"),
        offset: int = Query(0, ge=0, description="分页偏移，从 0 开始"),
        action: str | None = Query(
            None, description="按 action 精确过滤，如 'kb.delete'"
        ),
        actor_id: str | None = Query(
            None, description="按 actor_id 精确过滤，如 'github:Melodymll01'"
        ),
        _admin_id: str = Depends(require_admin),
    ) -> AuditLogListResponse:
        entries = container.audit_log.list_recent(
            limit=limit,
            offset=offset,
            action=action,
            actor_id=actor_id,
        )
        return AuditLogListResponse(
            entries=[_to_entry_out(e) for e in entries],
            count=len(entries),
        )

    @router.get(
        "/export.csv",
        summary="导出审计日志为 CSV（admin-only · 流式下载）",
        response_class=StreamingResponse,
        responses={
            200: {
                "content": {
                    "text/csv": {
                        "schema": {
                            "type": "string",
                            "example": "timestamp_iso,timestamp_epoch,...\n",
                        }
                    }
                }
            }
        },
    )
    def export_audit_logs_csv(
        action: str | None = Query(
            None, description="按 action 精确过滤，如 'kb.delete'"
        ),
        actor_id: str | None = Query(
            None,
            description="按 actor_id 精确过滤，如 'github:Melodymll01'",
        ),
        max_rows: int = Query(
            DEFAULT_MAX_ROWS,
            ge=1,
            le=DEFAULT_MAX_ROWS,
            description="一次导出行数硬上限（防内存过载）",
        ),
        _admin_id: str = Depends(require_admin),
    ) -> StreamingResponse:
        """返回 ``text/csv``。

        - 拉取 ``list_recent(limit=max_rows, action=, actor_id=)`` 后按行流式
          写 csv，不一次性在内存里拼出全部负载
        - 文件名含 ISO 日期方便多次导出不覆盖
        """
        entries = container.audit_log.list_recent(
            limit=max_rows,
            offset=0,
            action=action,
            actor_id=actor_id,
        )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        filename = f"audit_export_{stamp}.csv"
        return StreamingResponse(
            _stream_csv(entries),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                # 保护明确告知代理不要缓存业务敏感数据
                "Cache-Control": "no-store",
            },
        )

    return router


def _stream_csv(entries: list[AuditEntry]) -> Iterator[bytes]:
    """流式生成 CSV 字节。

    使用 ``io.StringIO`` 作为 csv.writer 的中间缓冲区，每 ``CSV_FLUSH_EVERY``
    行 flush 一次 yield 出 bytes。起头先 yield UTF-8 BOM——便于 Excel 默认
    以 UTF-8 打开中文不乱码。
    """
    # UTF-8 BOM：Windows Excel 识别编码的事实标准
    yield b"\xef\xbb\xbf"

    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    yield _flush(buf)

    for i, e in enumerate(entries, start=1):
        writer.writerow(_entry_to_row(e))
        if i % CSV_FLUSH_EVERY == 0:
            yield _flush(buf)
    # 尾巴 flush
    tail = _flush(buf)
    if tail:
        yield tail


def _flush(buf: io.StringIO) -> bytes:
    """取走 ``buf`` 当前内容并清空，返回 UTF-8 bytes。"""
    data = buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    return data.encode("utf-8")


def _entry_to_row(e: AuditEntry) -> list[str]:
    """将 ``AuditEntry`` 拆为 CSV 列字符串（与 ``CSV_HEADER`` 同顺序）。"""
    iso = datetime.fromtimestamp(e.timestamp, tz=UTC).isoformat(
        timespec="milliseconds"
    )
    return [
        iso,
        f"{e.timestamp:.3f}",
        e.actor_id,
        e.action,
        e.resource,
        e.request_id or "",
        "1" if e.success else "0",
        e.error or "",
        json.dumps(e.extra_json, ensure_ascii=False),
    ]
