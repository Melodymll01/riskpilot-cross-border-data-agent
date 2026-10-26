"""Parse explicit memory commands without model inference or fuzzy target selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_PREFIX = r"(?:请)?(?:帮我)?"
_SEPARATOR = r"[：:,，\s]*"


@dataclass(frozen=True)
class MemoryCommand:
    action: Literal["remember", "update", "forget", "clear", "list", "enable", "disable"]
    content: str = ""
    selector: str = ""


def parse_memory_command(text: str) -> MemoryCommand | None:
    text = text.strip()
    if re.fullmatch(_PREFIX + r"(?:开启|启用|打开)(?:账号)?记忆[。！!]?", text):
        return MemoryCommand("enable")
    if re.fullmatch(_PREFIX + r"(?:关闭|停用)(?:账号)?记忆[。！!]?", text):
        return MemoryCommand("disable")
    if re.fullmatch(
        _PREFIX
        + r"(?:记住|忘记|删除记忆|更新记忆|修改记忆|更新这条偏好|更新这条背景)"
        + _SEPARATOR,
        text,
    ):
        raise ValueError("请提供要保存、更新或忘记的记忆内容")
    if re.fullmatch(_PREFIX + r"(?:查看|列出)(?:我的)?(?:账号)?记忆[。！!]?", text):
        return MemoryCommand("list")
    if re.fullmatch(_PREFIX + r"清空(?:我的)?(?:全部|所有)?(?:账号)?记忆[。！!]?", text):
        return MemoryCommand("clear")
    if re.fullmatch(_PREFIX + r"(?:删除|忘记)(?:我的)?(?:全部|所有)(?:账号)?记忆[。！!]?", text):
        return MemoryCommand("clear")

    quoted_update = re.fullmatch(
        _PREFIX
        + r"把(?:账号)?记忆[“「\"](.+?)[”」\"]\s*"
        + r"(?:改为|改成|更新为|更新成)\s*[“「\"](.+?)[”」\"][。！!]?",
        text,
        flags=re.DOTALL,
    )
    if quoted_update:
        return MemoryCommand(
            "update", content=quoted_update[2].strip(), selector=quoted_update[1].strip()
        )
    update = re.fullmatch(
        _PREFIX
        + r"(?:更新|修改)(记忆|这条偏好|这条背景|我的姓名|我的名字|我的职业|我的身份|我的所属组织)[：:,，]\s*(.+)",
        text,
        flags=re.DOTALL,
    )
    if update:
        if update[1] != "记忆":
            return MemoryCommand("update", content=update[2].strip(), selector=update[1])
        parts = re.split(r"\s*(?:=>|->|→)\s*", update[2])
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError("请指定原文和新内容，例如：请更新记忆：回答简洁 => 回答详细")
        return MemoryCommand("update", content=parts[1].strip(), selector=parts[0].strip())

    forget = re.fullmatch(
        _PREFIX + r"(?:忘记|删除记忆)" + _SEPARATOR + r"(.+)", text, flags=re.DOTALL
    )
    if forget:
        return MemoryCommand("forget", selector=forget[1].strip())
    remember = re.fullmatch(_PREFIX + r"记住" + _SEPARATOR + r"(.+)", text, flags=re.DOTALL)
    if remember:
        return MemoryCommand("remember", content=remember[1].strip())
    return None
