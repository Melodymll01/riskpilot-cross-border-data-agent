"""local/test profile 的显式 manual Dispatcher。"""

from __future__ import annotations


class ManualJobDispatcher:
    """不创建线程、不伪装异步；只返回确定性 task ID。"""

    def enqueue_document(self, job_id: str, *, attempt: int) -> str:
        if not job_id:
            raise ValueError("job_id 不能为空")
        if attempt < 0:
            raise ValueError("attempt 不能小于 0")
        return f"manual:{job_id}:attempt{attempt}"

    def cancel_document(self, job_id: str, *, attempt: int) -> None:
        if not job_id:
            raise ValueError("job_id 不能为空")
        if attempt < 0:
            raise ValueError("attempt 不能小于 0")
