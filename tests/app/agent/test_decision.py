"""LLM 决策 JSON 解析（``parse_decision``）的容错性测试。"""

from __future__ import annotations

import pytest

from app.agent.decision import AgentDecisionParseError, parse_decision


class TestParseDecisionHappyPath:
    def test_tool_action_minimal(self) -> None:
        raw = '{"thought":"先查法条","action":"tool","tool_name":"search_law","tool_args":{"query":"PIPL"}}'
        d = parse_decision(raw)
        assert d.thought == "先查法条"
        assert d.action == "tool"
        assert d.tool_name == "search_law"
        assert d.tool_args == {"query": "PIPL"}

    def test_final_answer(self) -> None:
        raw = '{"thought":"信息够了","action":"final_answer","answer":"根据 PIPL...","citations":[{"source_name":"PIPL","source_type":"law"}]}'
        d = parse_decision(raw)
        assert d.action == "final_answer"
        assert d.final_text == "根据 PIPL..."
        assert d.citations == [{"source_name": "PIPL", "source_type": "law"}]

    def test_ask_user_with_missing_facts(self) -> None:
        raw = '{"thought":"缺信息","action":"ask_user","question":"用户量？","missing_facts":["user_count"]}'
        d = parse_decision(raw)
        assert d.action == "ask_user"
        assert d.question == "用户量？"
        assert d.missing_facts == ["user_count"]


class TestParseDecisionAliases:
    @pytest.mark.parametrize("alias", ["tool", "tools", "tool_call", "call_tool"])
    def test_tool_aliases(self, alias: str) -> None:
        raw = '{"thought":"x","action":"' + alias + '","tool_name":"web_search","tool_args":{"query":"q"}}'
        assert parse_decision(raw).action == "tool"

    @pytest.mark.parametrize("alias", ["ask", "ask_user", "question"])
    def test_ask_aliases(self, alias: str) -> None:
        raw = '{"thought":"x","action":"' + alias + '","question":"q?"}'
        assert parse_decision(raw).action == "ask_user"

    @pytest.mark.parametrize("alias", ["answer", "final", "final_answer", "done"])
    def test_final_aliases(self, alias: str) -> None:
        raw = '{"thought":"x","action":"' + alias + '","answer":"done"}'
        assert parse_decision(raw).action == "final_answer"


class TestParseDecisionFenceAndJunk:
    def test_strips_markdown_fence_json(self) -> None:
        raw = '```json\n{"thought":"x","action":"final_answer","answer":"hi"}\n```'
        d = parse_decision(raw)
        assert d.final_text == "hi"

    def test_strips_markdown_fence_no_lang(self) -> None:
        raw = '```\n{"thought":"x","action":"final_answer","answer":"hi"}\n```'
        assert parse_decision(raw).final_text == "hi"

    def test_extracts_json_from_surrounding_text(self) -> None:
        raw = '好的我先想想：{"thought":"x","action":"final_answer","answer":"答"}\n谢谢'
        assert parse_decision(raw).final_text == "答"


class TestParseDecisionErrors:
    def test_empty_input(self) -> None:
        with pytest.raises(AgentDecisionParseError, match="empty"):
            parse_decision("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(AgentDecisionParseError, match="empty"):
            parse_decision("   \n  ")

    def test_no_json_object(self) -> None:
        with pytest.raises(AgentDecisionParseError, match="no JSON"):
            parse_decision("just some text without braces")

    def test_invalid_json(self) -> None:
        with pytest.raises(AgentDecisionParseError, match="invalid JSON"):
            parse_decision("{this is not valid json}")

    def test_unknown_action(self) -> None:
        raw = '{"thought":"x","action":"explode"}'
        with pytest.raises(AgentDecisionParseError, match="unknown action"):
            parse_decision(raw)

    def test_tool_without_name(self) -> None:
        raw = '{"thought":"x","action":"tool","tool_args":{}}'
        with pytest.raises(AgentDecisionParseError, match="tool_name"):
            parse_decision(raw)

    def test_tool_args_not_object(self) -> None:
        raw = '{"thought":"x","action":"tool","tool_name":"foo","tool_args":[1,2,3]}'
        with pytest.raises(AgentDecisionParseError, match="tool_args"):
            parse_decision(raw)

    def test_ask_user_without_question(self) -> None:
        raw = '{"thought":"x","action":"ask_user"}'
        with pytest.raises(AgentDecisionParseError, match="ask_user"):
            parse_decision(raw)

    def test_final_answer_without_text(self) -> None:
        raw = '{"thought":"x","action":"final_answer"}'
        with pytest.raises(AgentDecisionParseError, match="final_answer"):
            parse_decision(raw)


class TestParseDecisionDefaults:
    def test_missing_thought_is_blank(self) -> None:
        raw = '{"action":"final_answer","answer":"ok"}'
        assert parse_decision(raw).thought == ""

    def test_invalid_missing_facts_becomes_empty(self) -> None:
        raw = '{"thought":"x","action":"ask_user","question":"q","missing_facts":"not a list"}'
        assert parse_decision(raw).missing_facts == []

    def test_citation_non_dict_items_filtered(self) -> None:
        raw = '{"thought":"x","action":"final_answer","answer":"ok","citations":[{"source_name":"PIPL","source_type":"law"},"junk",42]}'
        d = parse_decision(raw)
        assert len(d.citations) == 1

    def test_only_final_answer_carries_tool_args(self) -> None:
        # tool_args 在 final_answer 模式下会被清空
        raw = '{"thought":"x","action":"final_answer","answer":"ok","tool_name":"x","tool_args":{"a":1}}'
        d = parse_decision(raw)
        assert d.tool_name is None
        assert d.tool_args == {}


class TestParseDecisionUnescapedQuotes:
    """Step 026d：真实 GLM-5 在字符串值内塞未转义 ASCII 双引号的容错修复。"""

    def test_unescaped_quotes_in_thought(self) -> None:
        # 复刻真实失败样本：thought 内 ``第三章"个人信息跨境提供的规则"的相关条款``
        raw = (
            '{"thought":"已检索到第三章"个人信息跨境提供的规则"的条款",'
            '"action":"final_answer","answer":"根据第三十八条，需满足条件之一"}'
        )
        d = parse_decision(raw)
        assert d.action == "final_answer"
        assert d.final_text == "根据第三十八条，需满足条件之一"

    def test_unescaped_quotes_in_answer(self) -> None:
        raw = (
            '{"thought":"ok","action":"final_answer",'
            '"answer":"这属于"安全评估"路径，详见办法"}'
        )
        d = parse_decision(raw)
        assert d.action == "final_answer"
        assert "安全评估" in d.final_text

    def test_repair_preserves_citations(self) -> None:
        raw = (
            '{"thought":"查到第三章"跨境规则"相关","action":"final_answer",'
            '"answer":"需通过"安全评估"等","citations":'
            '[{"source_name":"个人信息保护法","source_type":"law"}]}'
        )
        d = parse_decision(raw)
        assert len(d.citations) == 1
        assert d.citations[0]["source_name"] == "个人信息保护法"

    def test_valid_json_unaffected(self) -> None:
        # 合法 JSON 不应被修复逻辑触碰（escape 正常的引号保留）
        raw = '{"thought":"说\\"你好\\"","action":"final_answer","answer":"ok"}'
        d = parse_decision(raw)
        assert d.thought == '说"你好"'
        assert d.final_text == "ok"

