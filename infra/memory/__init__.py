"""记忆系统基础设施层（Step 030）。

S-030a 实现 L1 短期记忆（``TaskBackedMemory``）；
S-030b 补 L2 滚动摘要 + TTL + 后台调度（``ThreadPoolMemoryScheduler``）；
S-030c 补 L4 语义事实固化（``ChromaFactStore`` + ``ConsolidationWorker``）；
L3 留待 S-030d。
"""

from infra.memory.consolidation import (
    ConsolidationWorker,
    MemoryCandidate,
    build_memory_extraction_episode,
    contains_prompt_injection,
    contains_sensitive_memory_content,
    contains_transient_or_hypothetical_content,
    validate_memory_candidate,
)
from infra.memory.fact_store import ChromaFactStore
from infra.memory.scheduler import ThreadPoolMemoryScheduler
from infra.memory.task_memory import TaskBackedMemory

__all__ = [
    "ChromaFactStore",
    "ConsolidationWorker",
    "MemoryCandidate",
    "TaskBackedMemory",
    "ThreadPoolMemoryScheduler",
    "build_memory_extraction_episode",
    "contains_prompt_injection",
    "contains_sensitive_memory_content",
    "contains_transient_or_hypothetical_content",
    "validate_memory_candidate",
]
