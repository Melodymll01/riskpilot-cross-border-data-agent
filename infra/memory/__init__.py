"""记忆系统基础设施层（Step 030）。

S-030a 仅实现 L1 短期记忆（``TaskBackedMemory``），
L2/L3/L4 留待 S-030b/c/d。
"""

from infra.memory.task_memory import TaskBackedMemory

__all__ = ["TaskBackedMemory"]
