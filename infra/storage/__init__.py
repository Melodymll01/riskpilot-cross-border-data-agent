"""SQLite 持久化适配器：实现 UserRepoPort / TaskRepoPort / SummaryStorePort / ConsolidationStatePort / ProfileStorePort。"""

from infra.storage.sqlite_agent_run_repo import SqliteAgentRunRepo
from infra.storage.sqlite_assessment_repo import SqliteAssessmentRepo
from infra.storage.sqlite_case_fact_repo import SqliteCaseFactRepo
from infra.storage.sqlite_case_repo import SqliteCaseRepo
from infra.storage.sqlite_consolidation_state import SqliteConsolidationStateStore
from infra.storage.sqlite_document_repo import SqliteDocumentRepo
from infra.storage.sqlite_feedback_repo import SqliteFeedbackRepo
from infra.storage.sqlite_memory_settings import SqliteMemorySettingsStore
from infra.storage.sqlite_policy_rule_repo import SqlitePolicyRuleRepo
from infra.storage.sqlite_profile_store import SqliteProfileStore
from infra.storage.sqlite_summary_store import SqliteSummaryStore
from infra.storage.sqlite_task_repo import SqliteTaskRepo
from infra.storage.sqlite_user_repo import SqliteUserRepo
from infra.storage.sqlite_visual_index import SqliteVisualIndex
from infra.storage.sqlite_workspace_repo import SqliteWorkspaceRepo

__all__ = [
    "SqliteAssessmentRepo",
    "SqliteAgentRunRepo",
    "SqliteCaseRepo",
    "SqliteCaseFactRepo",
    "SqliteConsolidationStateStore",
    "SqliteDocumentRepo",
    "SqliteFeedbackRepo",
    "SqliteMemorySettingsStore",
    "SqlitePolicyRuleRepo",
    "SqliteProfileStore",
    "SqliteSummaryStore",
    "SqliteTaskRepo",
    "SqliteUserRepo",
    "SqliteVisualIndex",
    "SqliteWorkspaceRepo",
]
