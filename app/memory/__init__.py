"""记忆装配层（Step 030）。

``MemoryAssembler`` 是预算感知的"记忆编排器"骨架：
S-030a 只装配 L1 短期历史；后续 L2/L3/L4 在此汇聚并按 token 预算裁剪。
"""

from app.memory.assembler import MemoryAssembler

__all__ = ["MemoryAssembler"]
