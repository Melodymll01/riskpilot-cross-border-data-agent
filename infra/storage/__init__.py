"""SQLite 持久化适配器：实现 UserRepoPort / TaskRepoPort / SummaryStorePort / ConsolidationStatePort / ProfileStorePort。"""

from infra.storage.sqlite_case_repo import SqliteCaseRepo
from infra.storage.sqlite_consolidation_state import SqliteConsolidationStateStore
from infra.storage.sqlite_document_repo import SqliteDocumentRepo
from infra.storage.sqlite_feedback_repo import SqliteFeedbackRepo
from infra.storage.sqlite_memory_settings import SqliteMemorySettingsStore
from infra.storage.sqlite_profile_store import SqliteProfileStore
from infra.storage.sqlite_summary_store import SqliteSummaryStore
from infra.storage.sqlite_task_repo import SqliteTaskRepo
from infra.storage.sqlite_user_repo import SqliteUserRepo
from infra.storage.sqlite_workspace_repo import SqliteWorkspaceRepo

__all__ = [
    "SqliteCaseRepo",
    "SqliteConsolidationStateStore",
    "SqliteDocumentRepo",
    "SqliteFeedbackRepo",
    "SqliteMemorySettingsStore",
    "SqliteProfileStore",
    "SqliteSummaryStore",
    "SqliteTaskRepo",
    "SqliteUserRepo",
    "SqliteWorkspaceRepo",
]
