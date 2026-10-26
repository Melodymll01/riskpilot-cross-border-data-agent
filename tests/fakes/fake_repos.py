"""内存版 UserRepo / TaskRepo Fake，用于单测。"""

from __future__ import annotations

import time

from domain.document_content import DocumentParseSnapshot
from domain.documents import Document, DocumentVersion, ProcessingJob
from domain.models import Message, Task, ToolCall, User


class InMemoryUserRepo:
    """`UserRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def upsert(self, user: User) -> None:
        self._users[user.user_id] = user

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def find_by_provider(self, provider: str, provider_id: str) -> User | None:
        return next(
            (
                user
                for user in self._users.values()
                if user.provider == provider and user.provider_id == provider_id
            ),
            None,
        )

    def merge_owner(self, from_id: str, to_id: str) -> int:
        # 内存版无 task 表，配套 InMemoryTaskRepo 才有意义
        return 0

    def touch(self, user_id: str) -> None:
        u = self._users.get(user_id)
        if u is None:
            return
        self._users[user_id] = u.model_copy(update={"last_active_at": time.time()})


class InMemoryTaskRepo:
    """`TaskRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._messages: dict[str, list[Message]] = {}
        self._tool_calls: dict[str, ToolCall] = {}
        self._attachment_ids: dict[str, list[str]] = {}

    def create(self, task: Task) -> None:
        self._tasks[task.task_id] = task
        self._messages.setdefault(task.task_id, [])

    def get(self, task_id: str, owner_id: str) -> Task | None:
        t = self._tasks.get(task_id)
        if t is None or t.owner_id != owner_id:
            return None
        return t

    def list_for_owner(self, owner_id: str, limit: int = 50) -> list[Task]:
        items = [t for t in self._tasks.values() if t.owner_id == owner_id]
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items[:limit]

    def update(self, task: Task) -> None:
        if task.task_id in self._tasks:
            self._tasks[task.task_id] = task

    def delete(self, task_id: str, owner_id: str) -> bool:
        t = self._tasks.get(task_id)
        if t is None or t.owner_id != owner_id:
            return False
        del self._tasks[task_id]
        self._messages.pop(task_id, None)
        self._attachment_ids.pop(task_id, None)
        return True

    def append_message(self, msg: Message) -> None:
        self._messages.setdefault(msg.task_id, []).append(msg)
        t = self._tasks.get(msg.task_id)
        if t is not None:
            self._tasks[msg.task_id] = t.model_copy(update={"updated_at": msg.created_at})

    def list_messages(self, task_id: str) -> list[Message]:
        return list(self._messages.get(task_id, []))

    def set_attachment_ids(
        self,
        task_id: str,
        owner_id: str,
        document_ids: list[str],
    ) -> None:
        if self.get(task_id, owner_id) is None:
            raise PermissionError("task 不存在或不属于当前 owner")
        self._attachment_ids[task_id] = list(dict.fromkeys(document_ids))

    def list_attachment_ids(self, task_id: str, owner_id: str) -> list[str]:
        if self.get(task_id, owner_id) is None:
            raise PermissionError("task 不存在或不属于当前 owner")
        return list(self._attachment_ids.get(task_id, []))

    def append_tool_call(self, call: ToolCall) -> None:
        self._tool_calls[call.tool_call_id] = call


class InMemoryDocumentRepo:
    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._versions: dict[str, DocumentVersion] = {}
        self._jobs: dict[str, ProcessingJob] = {}
        self._snapshots: dict[str, DocumentParseSnapshot] = {}

    def create_upload(
        self,
        document: Document,
        version: DocumentVersion,
        job: ProcessingJob,
    ) -> None:
        self._documents[document.document_id] = document
        self._versions[version.version_id] = version
        self._jobs[job.job_id] = job

    def get(self, document_id: str) -> Document | None:
        return self._documents.get(document_id)

    def get_version(self, version_id: str) -> DocumentVersion | None:
        return self._versions.get(version_id)

    def list_versions(self, document_id: str) -> list[DocumentVersion]:
        versions = [
            version for version in self._versions.values() if version.document_id == document_id
        ]
        versions.sort(key=lambda version: version.version_number, reverse=True)
        return versions

    def get_job(self, job_id: str) -> ProcessingJob | None:
        return self._jobs.get(job_id)

    def get_latest_job_for_version(
        self,
        document_version_id: str,
    ) -> ProcessingJob | None:
        jobs = [
            job for job in self._jobs.values() if job.document_version_id == document_version_id
        ]
        return max(jobs, key=lambda job: (job.created_at, job.job_id), default=None)

    def update_document(self, document: Document) -> None:
        if document.document_id in self._documents:
            self._documents[document.document_id] = document

    def update_job(
        self,
        job: ProcessingJob,
        *,
        expected_revision: int,
    ) -> None:
        from domain.errors import ProcessingJobConflict

        current = self._jobs.get(job.job_id)
        if (
            current is None
            or current.revision != expected_revision
            or job.revision <= expected_revision
        ):
            raise ProcessingJobConflict(job.job_id)
        self._jobs[job.job_id] = job

    def update_processing_state(
        self,
        document: Document,
        job: ProcessingJob,
        *,
        expected_revision: int,
    ) -> None:
        self.update_job(job, expected_revision=expected_revision)
        self.update_document(document)

    def save_parse_result(
        self,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        document: Document,
        job: ProcessingJob,
        *,
        expected_job_revision: int,
    ) -> None:
        self.update_job(job, expected_revision=expected_job_revision)
        self._versions[version.version_id] = version
        self._snapshots[version.version_id] = snapshot
        self.update_document(document)

    def get_parse_snapshot(self, document_version_id: str) -> DocumentParseSnapshot | None:
        return self._snapshots.get(document_version_id)

    def list_for_owner(self, owner_id: str, *, include_deleted: bool = False) -> list[Document]:
        return sorted(
            [
                d
                for d in self._documents.values()
                if d.owner_id == owner_id and (include_deleted or d.status != "deleted")
            ],
            key=lambda d: d.updated_at,
            reverse=True,
        )

    def list_jobs_for_owner(
        self, owner_id: str, *, statuses: set[str] | None = None, limit: int = 50
    ) -> list[ProcessingJob]:
        ids = {d.document_id for d in self.list_for_owner(owner_id)}
        versions = {v.version_id for v in self._versions.values() if v.document_id in ids}
        return sorted(
            [
                j
                for j in self._jobs.values()
                if j.document_version_id in versions and (not statuses or j.status in statuses)
            ],
            key=lambda j: (j.created_at, j.job_id),
            reverse=True,
        )[:limit]
