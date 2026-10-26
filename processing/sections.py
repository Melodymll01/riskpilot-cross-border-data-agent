"""轻量 Parent-Child 章节识别：把连续页面映射到稳定章节父块。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from ingestion.unified_loader import RawDocument, RawSegment

PARENT_CHILD_SCHEMA_VERSION = "parent-child-v1"
_CN_NUM = "零一二三四五六七八九十百千万两〇○"
_LEGAL_NUM = _CN_NUM + "0-9"
_HEADING_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(r"^附件\s*\d+\s*$")),
    (1, re.compile(rf"^第[{_LEGAL_NUM}]+章(?:\s|$)")),
    (2, re.compile(rf"^第[{_LEGAL_NUM}]+节(?:\s|$)")),
    (2, re.compile(rf"^第[{_LEGAL_NUM}]+条(?:\s|$)")),
    (1, re.compile(rf"^[{_CN_NUM}]+[、.]\s*\S")),
    (2, re.compile(rf"^（[{_CN_NUM}]+）\s*\S")),
)


@dataclass(frozen=True)
class SectionedSegment:
    content: str
    extra_metadata: dict[str, str]


class SectionSegmenter:
    """按一级/二级标题维护跨页章节状态，不依赖 LLM。"""

    def __init__(self, *, source_name: str, document_title: str) -> None:
        self._source_name = source_name
        self._document_title = document_title.strip() or source_name
        self._headings: dict[int, str] = {}
        self._section_ids: dict[int, str] = {}
        self._occurrences: defaultdict[str, int] = defaultdict(int)
        self._root_id = self._make_id("root", 1)

    def split(self, segments: list[RawSegment]) -> list[SectionedSegment]:
        output: list[SectionedSegment] = []
        for segment in segments:
            output.extend(self._split_segment(segment))
        return output

    def _split_segment(self, segment: RawSegment) -> list[SectionedSegment]:
        output: list[SectionedSegment] = []
        buffer: list[str] = []
        buffer_metadata = self._metadata(segment.extra_metadata)

        def flush() -> None:
            content = "\n".join(buffer).strip()
            if content:
                output.append(SectionedSegment(content=content, extra_metadata=buffer_metadata))
            buffer.clear()

        for raw_line in segment.content.splitlines():
            line = raw_line.strip()
            heading_level = _heading_level(line)
            if heading_level is not None:
                flush()
                self._enter_heading(heading_level, line)
                buffer_metadata = self._metadata(segment.extra_metadata)
            buffer.append(raw_line)
        flush()
        return output

    def _enter_heading(self, level: int, heading: str) -> None:
        for existing_level in [key for key in self._headings if key >= level]:
            self._headings.pop(existing_level, None)
            self._section_ids.pop(existing_level, None)
        self._headings[level] = heading
        path = " > ".join(self._headings[key] for key in sorted(self._headings))
        occurrence = self._occurrences[path] + 1
        self._occurrences[path] = occurrence
        self._section_ids[level] = self._make_id(path, occurrence)

    def _metadata(self, base: dict) -> dict[str, str]:
        ordered_levels = sorted(self._headings)
        headings = [self._headings[level] for level in ordered_levels]
        leaf_level = ordered_levels[-1] if ordered_levels else None
        leaf_id = self._section_ids[leaf_level] if leaf_level is not None else self._root_id
        parent_id = self._section_ids.get(1, leaf_id)
        section_title = (
            self._headings[leaf_level] if leaf_level is not None else self._document_title
        )
        parent_title = self._headings.get(1, section_title)
        path_parts = [self._document_title, *headings]
        return {
            **{str(key): str(value) for key, value in base.items()},
            "section_id": leaf_id,
            "parent_section_id": parent_id,
            "section_path": " > ".join(dict.fromkeys(part for part in path_parts if part)),
            "section_title": section_title,
            "parent_section_title": parent_title,
            "chunk_role": "child",
            "index_schema_version": PARENT_CHILD_SCHEMA_VERSION,
        }

    def _make_id(self, path: str, occurrence: int) -> str:
        material = f"{self._source_name}\x1f{path}\x1f{occurrence}".encode()
        return "sec_" + hashlib.sha256(material).hexdigest()[:16]


def section_segments(doc: RawDocument) -> list[SectionedSegment]:
    raw_segments = getattr(doc, "segments", None) or [RawSegment(content=doc.content)]
    return SectionSegmenter(source_name=doc.source_name, document_title=doc.title).split(
        raw_segments
    )


def _heading_level(line: str) -> int | None:
    if not line or len(line) > 120 or line.startswith("|"):
        return None
    markdown = re.match(r"^(#{1,6})\s+\S", line)
    if markdown:
        return 1 if len(markdown.group(1)) == 1 else 2
    for level, pattern in _HEADING_PATTERNS:
        if pattern.match(line):
            if (line.startswith("（") or re.match(rf"^[{_CN_NUM}]+[、.]", line)) and line[
                -1
            ] in "。；;":
                return None
            return level
    return None
