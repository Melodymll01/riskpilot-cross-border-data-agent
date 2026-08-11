"""内存版 UserRepo / TaskRepo Fake，用于单测。"""

from __future__ import annotations

import time

from domain.assessments import Assessment, AssessmentBundle
from domain.cases import Case
from domain.document_content import DocumentParseSnapshot
from domain.documents import CaseDocument, Document, DocumentVersion, ProcessingJob
from domain.errors import AgentRunConflict
from domain.facts import CaseFact, CaseFactEvidence
from domain.models import Artifact, Message, Task, ToolCall, User
from domain.policies import PolicyRule
from domain.runs import AgentRun, RunCheckpoint, RunEvent
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
        self.create_many([(fact, evidence)])

    def create_many(
        self,
        items: list[tuple[CaseFact, list[CaseFactEvidence]]],
    ) -> None:
        fact_ids = [fact.fact_id for fact, _ in items]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("批量创建 Fact ID 不能重复")
        if any(fact_id in self._facts for fact_id in fact_ids):
            raise ValueError("Fact 已存在")
        for fact, evidence in items:
            if any(
                item.fact_id != fact.fact_id
                or item.case_id != fact.case_id
                or item.fact_version != fact.version
                for item in evidence
            ):
                raise ValueError("证据必须属于当前事实及其版本")
        for fact, evidence in items:
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
        current = self._facts.get(fact.fact_id)
        if current is None:
            raise ValueError("待修订事实不存在")
        if fact.case_id != current.case_id:
            raise ValueError("事实修订不能跨 Case")
        if fact.version != current.version + 1:
            raise ValueError("事实版本必须单调递增 1")
        if any(
            item.fact_id != fact.fact_id
            or item.case_id != fact.case_id
            or item.fact_version != fact.version
            for item in evidence
        ):
            raise ValueError("证据必须属于当前事实及其版本")
        self._facts[fact.fact_id] = fact
        self._versions[(fact.fact_id, fact.version)] = fact
        self._evidence.update({item.evidence_id: item for item in evidence})

    def update_status(self, fact: CaseFact) -> None:
        self.update_statuses([fact])

    def update_statuses(self, facts: list[CaseFact]) -> None:
        fact_ids = [fact.fact_id for fact in facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("批量状态更新 Fact ID 不能重复")
        if any(fact_id not in self._facts for fact_id in fact_ids):
            raise ValueError("待更新事实不存在")
        if any(
            fact.version != self._facts[fact.fact_id].version for fact in facts
        ):
            raise ValueError("状态更新不能改变事实版本")
        for fact in facts:
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


class InMemoryAssessmentRepo:
    """`AssessmentRepoPort` 的内存实现。"""

    def __init__(self, case_repo: InMemoryCaseRepo | None = None) -> None:
        self._bundles: dict[str, AssessmentBundle] = {}
        self._active: dict[str, str] = {}
        self._case_repo = case_repo

    def create_version(
        self,
        bundle: AssessmentBundle,
        previous: Assessment | None,
        case: Case,
    ) -> None:
        if previous is not None:
            previous_bundle = self._bundles[previous.assessment_id]
            self._bundles[previous.assessment_id] = previous_bundle.model_copy(
                update={"assessment": previous}
            )
        self._bundles[bundle.assessment.assessment_id] = bundle
        self._active[bundle.assessment.case_id] = bundle.assessment.assessment_id
        if self._case_repo is not None:
            self._case_repo.update(case)

    def get(self, assessment_id: str) -> AssessmentBundle | None:
        return self._bundles.get(assessment_id)

    def get_active(self, case_id: str) -> AssessmentBundle | None:
        assessment_id = self._active.get(case_id)
        return None if assessment_id is None else self._bundles.get(assessment_id)

    def list_for_case(self, case_id: str) -> list[Assessment]:
        assessments = [
            bundle.assessment
            for bundle in self._bundles.values()
            if bundle.assessment.case_id == case_id
        ]
        assessments.sort(key=lambda assessment: assessment.version, reverse=True)
        return assessments

    def next_version(self, case_id: str) -> int:
        versions = [
            bundle.assessment.version
            for bundle in self._bundles.values()
            if bundle.assessment.case_id == case_id
        ]
        return max(versions, default=0) + 1

    def save_review(self, assessment: Assessment, case: Case) -> None:
        bundle = self._bundles.get(assessment.assessment_id)
        if bundle is not None:
            self._bundles[assessment.assessment_id] = bundle.model_copy(
                update={"assessment": assessment}
            )
        if self._case_repo is not None:
            self._case_repo.update(case)


class InMemoryAgentRunRepo:
    """`AgentRunRepoPort` 的内存实现。"""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._checkpoints: dict[str, RunCheckpoint] = {}
        self._events: dict[str, list[RunEvent]] = {}

    def create(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        event: RunEvent,
    ) -> None:
        if run.run_id in self._runs:
            raise ValueError("AgentRun 已存在")
        if (
            checkpoint.run_id != run.run_id
            or checkpoint.thread_id != run.thread_id
            or checkpoint.stage != run.current_stage
            or checkpoint.version != 1
            or run.revision != 1
            or run.checkpoint_id != checkpoint.checkpoint_id
        ):
            raise ValueError("初始 AgentRun 与 RunCheckpoint 不一致")
        if (
            event.run_id != run.run_id
            or event.sequence != 1
            or event.event_type != "run_started"
        ):
            raise ValueError("首个 RunEvent 必须是 sequence=1 的 run_started")
        self._runs[run.run_id] = run
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._events[run.run_id] = [event]

    def get(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpoint | None:
        return self._checkpoints.get(checkpoint_id)

    def get_latest_checkpoint(self, run_id: str) -> RunCheckpoint | None:
        checkpoints = [
            checkpoint
            for checkpoint in self._checkpoints.values()
            if checkpoint.run_id == run_id
        ]
        return max(checkpoints, key=lambda item: item.version, default=None)

    def list_for_case(self, case_id: str, *, limit: int = 50) -> list[AgentRun]:
        runs = [run for run in self._runs.values() if run.case_id == case_id]
        runs.sort(key=lambda run: (run.created_at, run.run_id), reverse=True)
        return runs[:limit]

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        return [
            event
            for event in self._events.get(run_id, [])
            if event.sequence > after_sequence
        ][:limit]

    def next_checkpoint_version(self, run_id: str) -> int:
        checkpoint = self.get_latest_checkpoint(run_id)
        return 1 if checkpoint is None else checkpoint.version + 1

    def next_event_sequence(self, run_id: str) -> int:
        events = self._events.get(run_id, [])
        return max((event.sequence for event in events), default=0) + 1

    def save_progress(
        self,
        run: AgentRun,
        checkpoint: RunCheckpoint,
        events: list[RunEvent],
        *,
        expected_revision: int,
    ) -> None:
        current = self._runs.get(run.run_id)
        if current is None or current.revision != expected_revision:
            raise AgentRunConflict(run.run_id)
        if run.revision != expected_revision + 1:
            raise ValueError("AgentRun revision 必须恰好递增 1")
        if (
            run.workspace_id != current.workspace_id
            or run.case_id != current.case_id
            or run.workflow_type != current.workflow_type
            or run.thread_id != current.thread_id
            or run.created_by != current.created_by
            or run.created_at != current.created_at
        ):
            raise ValueError("AgentRun 的归属和创建字段不可修改")
        if (
            checkpoint.run_id != run.run_id
            or checkpoint.thread_id != run.thread_id
            or checkpoint.stage != run.current_stage
            or checkpoint.version != run.revision
            or run.checkpoint_id != checkpoint.checkpoint_id
        ):
            raise ValueError("AgentRun 与 RunCheckpoint 不一致")
        next_sequence = self.next_event_sequence(run.run_id)
        expected_sequences = list(range(next_sequence, next_sequence + len(events)))
        if [event.sequence for event in events] != expected_sequences:
            raise ValueError("RunEvent sequence 必须从当前序号开始连续递增")
        if any(event.run_id != run.run_id for event in events):
            raise ValueError("RunEvent 必须属于当前 AgentRun")
        if checkpoint.checkpoint_id in self._checkpoints:
            raise ValueError("RunCheckpoint 已存在")
        self._runs[run.run_id] = run
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._events.setdefault(run.run_id, []).extend(events)
