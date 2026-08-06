"""内存版 UserRepo / TaskRepo Fake，用于单测。"""

from __future__ import annotations

import time

from domain.cases import Case
from domain.document_content import DocumentParseSnapshot
from domain.documents import CaseDocument, Document, DocumentVersion, ProcessingJob
from domain.facts import CaseFact, CaseFactEvidence
from domain.models import Artifact, Message, Task, ToolCall, User
from domain.policies import PolicyRule
from domain.workspaces import Workspace, WorkspaceMembership


class InMemoryUserRepo:
    """`UserRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def upsert(self, user: User) -> None:
        self._users[user.user_id] = user

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)

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
        self._artifacts: list[Artifact] = []

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
        return True

    def append_message(self, msg: Message) -> None:
        self._messages.setdefault(msg.task_id, []).append(msg)
        t = self._tasks.get(msg.task_id)
        if t is not None:
            self._tasks[msg.task_id] = t.model_copy(
                update={"updated_at": msg.created_at}
            )

    def list_messages(self, task_id: str) -> list[Message]:
        return list(self._messages.get(task_id, []))

    def append_tool_call(self, call: ToolCall) -> None:
        self._tool_calls[call.tool_call_id] = call

    def append_artifact(self, art: Artifact) -> None:
        self._artifacts.append(art)


class InMemoryWorkspaceRepo:
    """`WorkspaceRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._memberships: dict[tuple[str, str], WorkspaceMembership] = {}

    def create(
        self,
        workspace: Workspace,
        creator_membership: WorkspaceMembership,
    ) -> None:
        self._workspaces[workspace.workspace_id] = workspace
        self._memberships[
            (creator_membership.workspace_id, creator_membership.user_id)
        ] = creator_membership

    def get(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def list_for_user(self, user_id: str, limit: int = 50) -> list[Workspace]:
        workspace_ids = {
            workspace_id
            for workspace_id, member_user_id in self._memberships
            if member_user_id == user_id
        }
        items = [
            workspace
            for workspace_id, workspace in self._workspaces.items()
            if workspace_id in workspace_ids
        ]
        items.sort(key=lambda workspace: workspace.updated_at, reverse=True)
        return items[:limit]

    def get_membership(
        self, workspace_id: str, user_id: str
    ) -> WorkspaceMembership | None:
        return self._memberships.get((workspace_id, user_id))

    def upsert_membership(self, membership: WorkspaceMembership) -> None:
        self._memberships[(membership.workspace_id, membership.user_id)] = membership

    def list_memberships(self, workspace_id: str) -> list[WorkspaceMembership]:
        return [
            membership
            for (member_workspace_id, _), membership in self._memberships.items()
            if member_workspace_id == workspace_id
        ]


class InMemoryCaseRepo:
    """`CaseRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}

    def create(self, case: Case) -> None:
        self._cases[case.case_id] = case

    def get(self, case_id: str) -> Case | None:
        return self._cases.get(case_id)

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[Case]:
        items = [
            case
            for case in self._cases.values()
            if case.workspace_id == workspace_id
            and (include_archived or case.status != "archived")
        ]
        items.sort(key=lambda case: case.updated_at, reverse=True)
        return items[:limit]

    def update(self, case: Case) -> None:
        if case.case_id in self._cases:
            self._cases[case.case_id] = case


class InMemoryDocumentRepo:
    """`DocumentRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._versions: dict[str, DocumentVersion] = {}
        self._bindings: dict[tuple[str, str], CaseDocument] = {}
        self._jobs: dict[str, ProcessingJob] = {}
        self._snapshots: dict[str, DocumentParseSnapshot] = {}

    def create_upload(
        self,
        document: Document,
        version: DocumentVersion,
        binding: CaseDocument,
        job: ProcessingJob,
    ) -> None:
        self._documents[document.document_id] = document
        self._versions[version.version_id] = version
        self._bindings[(binding.case_id, binding.document_id)] = binding
        self._jobs[job.job_id] = job

    def get(self, document_id: str) -> Document | None:
        return self._documents.get(document_id)

    def get_version(self, version_id: str) -> DocumentVersion | None:
        return self._versions.get(version_id)

    def list_versions(self, document_id: str) -> list[DocumentVersion]:
        versions = [
            version
            for version in self._versions.values()
            if version.document_id == document_id
        ]
        versions.sort(key=lambda version: version.version_number, reverse=True)
        return versions

    def get_binding(self, case_id: str, document_id: str) -> CaseDocument | None:
        return self._bindings.get((case_id, document_id))

    def list_bindings_for_document(self, document_id: str) -> list[CaseDocument]:
        return [
            binding
            for (_, binding_document_id), binding in self._bindings.items()
            if binding_document_id == document_id
        ]

    def list_for_case(
        self,
        case_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[Document]:
        document_ids = {
            document_id
            for binding_case_id, document_id in self._bindings
            if binding_case_id == case_id
        }
        documents = [
            document
            for document_id, document in self._documents.items()
            if document_id in document_ids
            and (include_deleted or document.status != "deleted")
        ]
        documents.sort(key=lambda document: document.updated_at, reverse=True)
        return documents

    def get_job(self, job_id: str) -> ProcessingJob | None:
        return self._jobs.get(job_id)

    def update_document(self, document: Document) -> None:
        if document.document_id in self._documents:
            self._documents[document.document_id] = document

    def update_job(self, job: ProcessingJob) -> None:
        if job.job_id in self._jobs:
            self._jobs[job.job_id] = job

    def update_processing_state(
        self,
        document: Document,
        job: ProcessingJob,
    ) -> None:
        self.update_document(document)
        self.update_job(job)

    def save_parse_result(
        self,
        version: DocumentVersion,
        snapshot: DocumentParseSnapshot,
        document: Document,
        job: ProcessingJob,
    ) -> None:
        self._versions[version.version_id] = version
        self._snapshots[version.version_id] = snapshot
        self.update_document(document)
        self.update_job(job)

    def get_parse_snapshot(
        self, document_version_id: str
    ) -> DocumentParseSnapshot | None:
        return self._snapshots.get(document_version_id)


class InMemoryCaseFactRepo:
    """`CaseFactRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._facts: dict[str, CaseFact] = {}
        self._versions: dict[tuple[str, int], CaseFact] = {}
        self._evidence: dict[str, CaseFactEvidence] = {}

    def create(
        self,
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None:
        self._facts[fact.fact_id] = fact
        self._versions[(fact.fact_id, fact.version)] = fact
        self._evidence.update({item.evidence_id: item for item in evidence})

    def get(self, fact_id: str) -> CaseFact | None:
        return self._facts.get(fact_id)

    def get_version(self, fact_id: str, version: int) -> CaseFact | None:
        return self._versions.get((fact_id, version))

    def list_for_case(
        self,
        case_id: str,
        *,
        statuses: set[str] | None = None,
    ) -> list[CaseFact]:
        facts = [
            fact
            for fact in self._facts.values()
            if fact.case_id == case_id
            and (not statuses or fact.status in statuses)
        ]
        facts.sort(key=lambda fact: (fact.updated_at, fact.fact_id), reverse=True)
        return facts

    def list_evidence(
        self,
        fact_id: str,
        *,
        fact_version: int | None = None,
    ) -> list[CaseFactEvidence]:
        return [
            evidence
            for evidence in self._evidence.values()
            if evidence.fact_id == fact_id
            and (fact_version is None or evidence.fact_version == fact_version)
        ]

    def save_revision(
        self,
        fact: CaseFact,
        evidence: list[CaseFactEvidence],
    ) -> None:
        self.create(fact, evidence)

    def update_status(self, fact: CaseFact) -> None:
        self._facts[fact.fact_id] = fact
        self._versions[(fact.fact_id, fact.version)] = fact


class InMemoryPolicyRuleRepo:
    """`PolicyRuleRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._rules: dict[tuple[str, str, str], PolicyRule] = {}

    def create(self, rule: PolicyRule) -> None:
        key = (rule.workspace_id, rule.rule_id, rule.ruleset_version)
        if key in self._rules:
            raise ValueError("规则版本已存在")
        self._rules[key] = rule

    def get(
        self,
        workspace_id: str,
        rule_id: str,
        ruleset_version: str,
    ) -> PolicyRule | None:
        return self._rules.get((workspace_id, rule_id, ruleset_version))

    def list_rules(
        self,
        *,
        workspace_id: str,
        ruleset_version: str | None = None,
        jurisdiction: str | None = None,
        status: str | None = None,
    ) -> list[PolicyRule]:
        rules = [
            rule
            for rule in self._rules.values()
            if rule.workspace_id == workspace_id
            and (ruleset_version is None or rule.ruleset_version == ruleset_version)
            and (jurisdiction is None or rule.jurisdiction == jurisdiction)
            and (status is None or rule.status == status)
        ]
        rules.sort(key=lambda rule: (rule.ruleset_version, rule.rule_id))
        return rules

    def update_status(self, rule: PolicyRule) -> None:
        self._rules[(rule.workspace_id, rule.rule_id, rule.ruleset_version)] = rule
