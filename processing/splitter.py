"""文本切分模块：将清洗后的文本按递归分句策略切分为 chunk 列表。"""

import re
import logging
from typing import List

from config import settings

logger = logging.getLogger(__name__)

# 递归拆分的分隔符优先级：优先在段落边界切，其次句号/问号/叹号，再次分号/换行，最后空格
SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", "，", ", ", " "]


class TextSplitter:
    """递归分句感知的文本切分器。

    切分策略：
    1. 按当前优先级最高的分隔符拆分文本
    2. 将拆分片段累积拼接，达到 chunk_size 后切出一个 chunk
    3. 若单个片段仍超出 chunk_size，则降级到下一优先级的分隔符递归切分
    4. 相邻 chunk 之间保留 overlap（按句子级别回溯，而非字符截断）
    """

    def __init__(
        self,
        chunk_size: int = settings.chunk_size,
        chunk_overlap: int = settings.chunk_overlap,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str) -> List[str]:
        """将文本切分为多个 chunk。"""
        if not text.strip():
            return []

        # 保护代码块和表格：临时替换为占位符，切分后还原
        text, protected = self._protect_structures(text)

        chunks = self._recursive_split(text, SEPARATORS)

        # 还原被保护的结构
        chunks = [self._restore_structures(c, protected) for c in chunks]

        # 合并过短的尾部 chunk
        merged = self._merge_small_chunks(chunks)

        # 切分质量统计
        sizes = [len(c) for c in merged]
        logger.info(
            f"切分完成，共 {len(merged)} 个 chunk，"
            f"长度范围 [{min(sizes)}, {max(sizes)}]，"
            f"平均 {sum(sizes) // len(sizes)}"
        )
        return merged

    def _protect_structures(self, text: str) -> tuple:
        """将代码块和表格替换为占位符，防止被分隔符切断。

        注意：仅保护长度 <= chunk_size 的结构。超长结构不保护，
        让它走正常的递归切分流程，避免还原后 chunk 远超 chunk_size。
        """
        protected = {}
        counter = 0

        def replace_block(match):
            nonlocal counter
            content = match.group(0)
            # 超长结构不保护：占位符长度远小于实际内容，
            # 还原后会撑爆 chunk；不如让它进入 _force_split 兜底切分。
            if len(content) > self.chunk_size:
                logger.warning(
                    f"结构长度 {len(content)} 超过 chunk_size {self.chunk_size}，"
                    f"放弃保护，将按字符强制切分（可能破坏表格/代码块结构）"
                )
                return content
            # 占位符做等长填充：让切分阶段按真实长度计算，
            # 防止 overlap 回溯时把占位符截半。
            key = f"\x00P{counter:04d}\x00"
            pad = max(0, len(content) - len(key))
            padded_key = key + ("\x00" * pad)
            protected[padded_key] = content
            counter += 1
            return padded_key

        # 保护 Markdown 代码块 ```...```
        text = re.sub(r'```[\s\S]*?```', replace_block, text)

        # 保护 Markdown 表格（连续的以 | 开头和结尾的行）
        text = re.sub(r'(?:^\|.+\|$\n?){2,}', replace_block, text, flags=re.MULTILINE)

        return text, protected

    def _restore_structures(self, text: str, protected: dict) -> str:
        """将占位符还原为原始结构。"""
        for key, value in protected.items():
            text = text.replace(key, value)
        return text

    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        """核心递归拆分：尝试当前分隔符，拆不动则降级。"""
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        # 找到当前可用的分隔符
        sep = ""
        remaining_seps = separators
        for i, s in enumerate(separators):
            if s in text:
                sep = s
                remaining_seps = separators[i + 1:]
                break

        # 如果没有任何分隔符可用，按字符强制截断
        if not sep:
            return self._force_split(text)

        # 用当前分隔符拆分
        parts = text.split(sep)

        chunks: List[str] = []
        current_parts: List[str] = []
        current_len = 0

        sep_len = len(sep)
        last_idx = len(parts) - 1

        for idx, part in enumerate(parts):
            # 最后一个片段后面原文没有分隔符，不应多算
            part_len = len(part) + (sep_len if idx < last_idx else 0)

            # 单个片段就超过 chunk_size → 递归降级拆分
            if part_len > self.chunk_size:
                # 先把累积的内容存入
                if current_parts:
                    chunks.append(sep.join(current_parts))
                    overlap_parts = self._get_overlap_parts(current_parts, sep)
                    current_parts = []
                    current_len = 0
                else:
                    overlap_parts = []
                # 对超长片段用更细粒度的分隔符递归
                sub_chunks = self._recursive_split(part, remaining_seps)
                # 将 overlap 拼入第一个 sub_chunk，保持上下文连续
                if overlap_parts and sub_chunks:
                    overlap_text = sep.join(overlap_parts) + sep
                    combined = overlap_text + sub_chunks[0]
                    if len(combined) <= self.chunk_size:
                        sub_chunks[0] = combined
                chunks.extend(sub_chunks)
                continue

            # 累积拼接：用 join 的实际长度判断（n 个 part 产生 n-1 个 sep）
            joined_len = current_len + (sep_len if current_parts else 0) + len(part)
            if joined_len > self.chunk_size and current_parts:
                chunks.append(sep.join(current_parts))

                # overlap: 从末尾回溯保留若干片段，直到累积长度接近 overlap
                overlap_parts = self._get_overlap_parts(current_parts, sep)
                current_parts = overlap_parts + [part]
                current_len = self._joined_len(current_parts, sep_len)
            else:
                current_parts.append(part)
                current_len = self._joined_len(current_parts, sep_len)

        # 收尾
        if current_parts:
            tail = sep.join(current_parts).strip()
            if tail:
                chunks.append(tail)

        return chunks

    def _get_overlap_parts(self, parts: List[str], sep: str) -> List[str]:
        """从 parts 末尾向前取片段，累积长度不超过 chunk_overlap。"""
        if self.chunk_overlap <= 0:
            return []

        overlap_parts = []
        total = 0
        for part in reversed(parts):
            added = len(part) + len(sep)
            if total + added > self.chunk_overlap:
                break
            overlap_parts.insert(0, part)
            total += added

        return overlap_parts

    @staticmethod
    def _joined_len(parts: List[str], sep_len: int) -> int:
        """计算 sep.join(parts) 的真实长度：各 part 长度 + (n-1) 个 sep。"""
        if not parts:
            return 0
        return sum(len(p) for p in parts) + sep_len * (len(parts) - 1)

    def _force_split(self, text: str) -> List[str]:
        """无可用分隔符时，按字符强制切分。"""
        chunks = []
        step = self.chunk_size - self.chunk_overlap
        if step <= 0:
            step = self.chunk_size
        for i in range(0, len(text), step):
            piece = text[i: i + self.chunk_size]
            if piece.strip():
                chunks.append(piece)
        return chunks

    def _merge_small_chunks(self, chunks: List[str]) -> List[str]:
        """将过短的尾部 chunk 合并到前一个 chunk（若合并后不超限）。"""
        if len(chunks) <= 1:
            return chunks

        min_size = self.chunk_size // 4  # 低于 1/4 chunk_size 认为过短
        merged = [chunks[0]]

        for chunk in chunks[1:]:
            if len(chunk) < min_size and len(merged[-1]) + len(chunk) + 2 <= self.chunk_size:
                merged[-1] = merged[-1] + "\n\n" + chunk
            else:
                merged.append(chunk)

        return merged
