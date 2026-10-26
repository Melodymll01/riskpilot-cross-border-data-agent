"""结构化 Evidence QA 生成与独立支持校验适配器测试。"""

from __future__ import annotations

import pytest

from domain import (
    ClaimSupportVerifierPort,
    EvidenceQACitation,
    EvidenceQAClaim,
    EvidenceQAGeneratorPort,
    UserContextMessage,
)
from infra.qa import StructuredClaimSupportVerifier, StructuredEvidenceQAGenerator
from tests.fakes import FakeChat


def _citation(citation_id: str = "E1") -> EvidenceQACitation:
    return EvidenceQACitation(
        citation_id=citation_id,
        corpus="regulatory",
        source_id=f"source_{citation_id}",
        source_name="个人信息保护法",
        title="第三十八条",
        quote="个人信息处理者向境外提供个人信息，应当具备法定条件之一。",
    )


def _claim() -> EvidenceQAClaim:
    return EvidenceQAClaim(
        claim_id="C1",
        text="个人信息出境应具备法定条件之一。",
        citation_ids=["E1"],
    )


class TestStructuredEvidenceQAGenerator:
    def test_satisfies_port_and_parses_claims(self) -> None:
        chat = FakeChat(
            responses=[
                """
                {
                  "status": "answered",
                  "claims": [
                    {
                      "claim_id": "C1",
                      "text": "个人信息出境应具备法定条件之一。",
                      "citation_ids": ["E1"]
                    }
                  ],
                  "presentation": {
                    "kind": "table",
                    "table": {
                      "title": "出境条件",
                      "rows": [{"label": "法定要求", "claim_ids": ["C1"]}]
                    },
                    "flowchart": null
                  },
                  "refusal_reason": "",
                  "unanswered_aspects": []
                }
                """
            ]
        )
        generator = StructuredEvidenceQAGenerator(chat)
        assert isinstance(generator, EvidenceQAGeneratorPort)

        draft = generator.generate(
            question="个人信息能否出境？",
            citations=[_citation()],
        )

        assert draft.status == "answered"
        assert draft.claims == [_claim()]
        assert draft.presentation is not None
        assert draft.presentation.kind == "table"
        assert chat.calls[0]["temperature"] == 0.0
        assert chat.calls[0]["json_mode"] is True
        assert chat.calls[0]["max_tokens"] == 3072
        user_prompt = chat.calls[0]["messages"][1]["content"]
        assert "[E1]" in user_prompt
        assert "第三十八条" in user_prompt

    def test_no_citation_refuses_without_llm_call(self) -> None:
        chat = FakeChat()
        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="问题",
            citations=[],
        )
        assert draft.status == "refused"
        assert chat.calls == []

    def test_user_conditions_are_separate_and_keep_message_ids(self) -> None:
        chat = FakeChat(
            responses=[
                """{
                  "status":"answered",
                  "claims":[{
                    "claim_id":"C1",
                    "text":"按更正后人数判断。",
                    "citation_ids":["E1"],
                    "condition_message_ids":["msg-correction", "msg-current"]
                  }],
                  "refusal_reason":"",
                  "unanswered_aspects":[]
                }"""
            ]
        )
        context = [
            UserContextMessage(message_id="msg-old", text="人数是2000。"),
            UserContextMessage(message_id="msg-correction", text="更正为3000。"),
            UserContextMessage(message_id="msg-current", text="如果翻倍呢？", is_current=True),
        ]

        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="如果翻倍呢？",
            citations=[_citation()],
            user_context=context,
        )

        assert draft.claims[0].condition_message_ids == ["msg-correction", "msg-current"]
        prompt = chat.calls[0]["messages"][1]["content"]
        assert "【用户条件】" in prompt
        assert "[U:msg-old] 人数是2000。" in prompt
        assert "[U:msg-current]（当前） 如果翻倍呢？" in prompt

    def test_code_fenced_json_is_supported(self) -> None:
        chat = FakeChat(
            responses=[
                """```json
                {
                  "status":"refused",
                  "claims":[],
                  "refusal_reason":"证据不足",
                  "unanswered_aspects":[]
                }
                ```"""
            ]
        )
        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="问题",
            citations=[_citation()],
        )
        assert draft.status == "refused"

    def test_invalid_json_or_schema_rejected(self) -> None:
        for response in ("not json", '{"status":"answered","claims":[]}'):
            generator = StructuredEvidenceQAGenerator(FakeChat(responses=[response]))
            with pytest.raises(ValueError):
                generator.generate(question="问题", citations=[_citation()])

    def test_invalid_presentation_degrades_to_verified_text_contract(self) -> None:
        chat = FakeChat(
            responses=[
                """{
                  "status":"answered",
                  "claims":[{
                    "claim_id":"C1",
                    "text":"个人信息出境应具备法定条件之一。",
                    "citation_ids":["E1"]
                  }],
                  "presentation":{
                    "kind":"flowchart",
                    "table":null,
                    "flowchart":{
                      "nodes":[{"node_id":"N1","claim_ids":["UNKNOWN"]}],
                      "edges":[]
                    }
                  },
                  "refusal_reason":"",
                  "unanswered_aspects":[]
                }"""
            ]
        )
        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="流程是什么？",
            citations=[_citation()],
        )
        assert draft.claims == [_claim()]
        assert draft.presentation is None
        assert "【建议展示方式】" in chat.calls[0]["messages"][1]["content"]
        assert "flowchart" in chat.calls[0]["messages"][1]["content"]

    def test_flowchart_edges_inherit_target_node_claims(self) -> None:
        chat = FakeChat(
            responses=[
                """{
                  "status":"answered",
                  "claims":[
                    {"claim_id":"C1","text":"提交申报材料。","citation_ids":["E1"]},
                    {"claim_id":"C2","text":"随后进入完备性查验。","citation_ids":["E1"]}
                  ],
                  "presentation":{
                    "kind":"flowchart",
                    "table":null,
                    "flowchart":{
                      "title":"申报流程",
                      "nodes":[
                        {"node_id":"N1","claim_ids":["C1"]},
                        {"node_id":"N2","claim_ids":["C2"]}
                      ],
                      "edges":[
                        {"source_node_id":"N1","target_node_id":"N2","claim_ids":["C1"]}
                      ]
                    }
                  },
                  "refusal_reason":"",
                  "unanswered_aspects":[]
                }"""
            ]
        )
        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="申报流程是什么？",
            citations=[_citation()],
        )
        assert draft.presentation is not None
        assert draft.presentation.flowchart is not None
        assert draft.presentation.flowchart.edges[0].claim_ids == ["C2"]
        assert [node.kind for node in draft.presentation.flowchart.nodes] == [
            "start",
            "terminal",
        ]

    def test_generic_v2_flow_normalizes_kinds_and_keeps_conditions(self) -> None:
        chat = FakeChat(
            responses=[
                """{
                  "status":"answered",
                  "claims":[
                    {"claim_id":"C1","text":"系统收到事件后进入分类步骤。","citation_ids":["E1"]},
                    {"claim_id":"C2","text":"系统根据事件级别选择上报路径。","citation_ids":["E1"]},
                    {"claim_id":"C3","text":"事件为高优先级时，立即上报值班负责人。","citation_ids":["E1"]},
                    {"claim_id":"C4","text":"事件为普通优先级时，进入常规处理队列。","citation_ids":["E1"]}
                  ],
                  "presentation":{
                    "kind":"flowchart",
                    "table":null,
                    "flowchart":{
                      "schema_version":"2.0",
                      "title":"事件上报",
                      "nodes":[
                        {"node_id":"N1","kind":"start","claim_ids":["C1"]},
                        {"node_id":"N2","kind":"merge","claim_ids":["C2"]},
                        {"node_id":"N3","kind":"terminal","claim_ids":["C3"]},
                        {"node_id":"N4","kind":"terminal","claim_ids":["C4"]}
                      ],
                      "edges":[
                        {"source_node_id":"N1","target_node_id":"N2","condition":"","claim_ids":["C1"]},
                        {"source_node_id":"N2","target_node_id":"N3","condition":"紧急","claim_ids":["C3"]},
                        {"source_node_id":"N2","target_node_id":"N4","condition":"普通优先级","claim_ids":["C4"]}
                      ]
                    }
                  },
                  "refusal_reason":"",
                  "unanswered_aspects":[]
                }"""
            ]
        )

        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="事件如何上报？",
            citations=[_citation()],
        )

        assert draft.presentation is not None
        assert draft.presentation.flowchart is not None
        flowchart = draft.presentation.flowchart
        assert flowchart.schema_version == "2.0"
        assert flowchart.nodes[1].kind == "decision"
        assert flowchart.edges[0].claim_ids == ["C1"]
        assert flowchart.edges[1].condition == "事件为高优先级时"
        system_prompt = chat.calls[0]["messages"][0]["content"]
        assert "start/action/decision/merge/terminal" in system_prompt
        assert 'schema_version="2.0"' in system_prompt
        assert "数据出境" not in system_prompt

    def test_invalid_cycle_is_repaired_without_changing_claims(self) -> None:
        claims_json = """[
          {"claim_id":"C1","text":"系统收到事件后开始处理。","citation_ids":["E1"]},
          {"claim_id":"C2","text":"系统判断事件是否高优先级。","citation_ids":["E1"]},
          {"claim_id":"C3","text":"高优先级时立即上报。","citation_ids":["E1"]},
          {"claim_id":"C4","text":"普通优先级时进入常规处理。","citation_ids":["E1"]}
        ]"""
        invalid = f"""{{
          "status":"answered",
          "claims":{claims_json},
          "presentation":{{
            "kind":"flowchart","table":null,
            "flowchart":{{
              "schema_version":"2.0","title":"事件上报",
              "nodes":[
                {{"node_id":"N1","kind":"start","claim_ids":["C1"]}},
                {{"node_id":"N2","kind":"decision","claim_ids":["C2"]}},
                {{"node_id":"N3","kind":"terminal","claim_ids":["C3"]}},
                {{"node_id":"N4","kind":"terminal","claim_ids":["C4"]}}
              ],
              "edges":[
                {{"source_node_id":"N1","target_node_id":"N2","condition":"","claim_ids":["C1"]}},
                {{"source_node_id":"N2","target_node_id":"N3","condition":"高优先级","claim_ids":["C3"]}},
                {{"source_node_id":"N2","target_node_id":"N4","condition":"普通优先级","claim_ids":["C4"]}},
                {{"source_node_id":"N3","target_node_id":"N2","condition":"重新判断","claim_ids":["C2"]}}
              ]
            }}
          }},
          "refusal_reason":"","unanswered_aspects":[]
        }}"""
        repaired = """{
          "presentation":{
            "kind":"flowchart","table":null,
            "flowchart":{
              "schema_version":"2.0","title":"事件上报",
              "nodes":[
                {"node_id":"N1","kind":"start","claim_ids":["C1"]},
                {"node_id":"N2","kind":"decision","claim_ids":["C2"]},
                {"node_id":"N3","kind":"terminal","claim_ids":["C3"]},
                {"node_id":"N4","kind":"terminal","claim_ids":["C4"]},
                {"node_id":"N5","kind":"start","claim_ids":["C1"]}
              ],
              "edges":[
                {"source_node_id":"N1","target_node_id":"N2","condition":"","claim_ids":["C1"]},
                {"source_node_id":"N2","target_node_id":"N3","condition":"高优先级","claim_ids":["C3"]},
                {"source_node_id":"N2","target_node_id":"N3","condition":"高优先级","claim_ids":["C3"]},
                {"source_node_id":"N2","target_node_id":"N4","condition":"普通优先级","claim_ids":["C4"]},
                {"source_node_id":"N3","target_node_id":"N2","condition":"重新判断","claim_ids":["C2"]}
              ]
            }
          }
        }"""
        chat = FakeChat(responses=[invalid, repaired])

        draft = StructuredEvidenceQAGenerator(chat).generate(
            question="事件如何上报？",
            citations=[_citation()],
        )

        assert len(chat.calls) == 2
        assert [claim.claim_id for claim in draft.claims] == ["C1", "C2", "C3", "C4"]
        assert draft.presentation is not None
        assert draft.presentation.flowchart is not None
        assert len(draft.presentation.flowchart.edges) == 3
        assert draft.status == "partially_answered"
        assert "DAG" in draft.unanswered_aspects[0]


class TestStructuredClaimSupportVerifier:
    def test_satisfies_port_and_accepts_supported_claim(self) -> None:
        chat = FakeChat(
            responses=[
                """
                {
                  "judgements": [
                    {
                      "claim_id": "C1",
                      "supported": true,
                      "citation_ids": ["E1"],
                      "reason": ""
                    }
                  ]
                }
                """
            ]
        )
        verifier = StructuredClaimSupportVerifier(chat)
        assert isinstance(verifier, ClaimSupportVerifierPort)

        result = verifier.verify([_claim()], [_citation()])

        assert result.valid is True
        assert result.unsupported_claim_ids == []
        assert chat.calls[0]["json_mode"] is True
        assert chat.calls[0]["max_tokens"] == 3072
        assert "声明引用: E1" in chat.calls[0]["messages"][1]["content"]

    def test_unsupported_claim_fails_closed(self) -> None:
        verifier = StructuredClaimSupportVerifier(
            FakeChat(
                responses=[
                    """
                    {
                      "judgements": [
                        {
                          "claim_id": "C1",
                          "supported": false,
                          "citation_ids": [],
                          "reason": "原文未明确支持"
                        }
                      ]
                    }
                    """
                ]
            )
        )
        result = verifier.verify([_claim()], [_citation()])
        assert result.valid is False
        assert result.unsupported_claim_ids == ["C1"]

    def test_verifier_receives_declared_user_condition_sources(self) -> None:
        chat = FakeChat(
            responses=[
                """{
                  "judgements":[{
                    "claim_id":"C1",
                    "supported":true,
                    "citation_ids":["E1"],
                    "reason":""
                  }]
                }"""
            ]
        )
        claim = _claim().model_copy(update={"condition_message_ids": ["msg-current"]})

        result = StructuredClaimSupportVerifier(chat).verify(
            [claim],
            [_citation()],
            user_context=[
                UserContextMessage(
                    message_id="msg-current",
                    text="人数是6000。",
                    is_current=True,
                )
            ],
        )

        assert result.valid is True
        prompt = chat.calls[0]["messages"][1]["content"]
        assert "声明用户条件: msg-current" in prompt
        assert "[U:msg-current]（当前） 人数是6000。" in prompt

    def test_missing_claim_judgement_rejected(self) -> None:
        verifier = StructuredClaimSupportVerifier(FakeChat(responses=['{"judgements":[]}']))
        with pytest.raises(ValueError, match="覆盖全部"):
            verifier.verify([_claim()], [_citation()])

    def test_unknown_or_expanded_citation_rejected(self) -> None:
        for citation_id in ("UNKNOWN", "E2"):
            verifier = StructuredClaimSupportVerifier(
                FakeChat(
                    responses=[
                        f"""
                        {{
                          "judgements": [
                            {{
                              "claim_id": "C1",
                              "supported": true,
                              "citation_ids": ["{citation_id}"],
                              "reason": ""
                            }}
                          ]
                        }}
                        """
                    ]
                )
            )
            citations = [_citation()]
            if citation_id == "E2":
                citations.append(_citation("E2"))
            with pytest.raises(ValueError):
                verifier.verify([_claim()], citations)

    def test_no_claims_short_circuits_without_llm(self) -> None:
        chat = FakeChat()
        result = StructuredClaimSupportVerifier(chat).verify([], [])
        assert result.valid is True
        assert result.judgements == []
        assert chat.calls == []
