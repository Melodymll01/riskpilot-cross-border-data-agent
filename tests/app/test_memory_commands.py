"""Memory command parsing must not intercept ordinary questions or guess a target."""

import pytest

from app.memory.commands import parse_memory_command


@pytest.mark.parametrize(
    "text",
    ["记忆系统应该如何做？", "更新记忆系统该怎么做？", "请解释怎么删除记忆", "你好"],
)
def test_ordinary_messages_are_not_commands(text):
    assert parse_memory_command(text) is None


@pytest.mark.parametrize("text", ["请记住：", "请更新记忆：回答详细解释", "请忘记："])
def test_incomplete_command_requires_explicit_content(text):
    with pytest.raises(ValueError):
        parse_memory_command(text)
