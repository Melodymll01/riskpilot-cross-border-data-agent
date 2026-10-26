"""Exercise safe answer rendering through both live SSE and saved history."""

import json

import pytest
from langchain_core.messages import AIMessage

from domain.qa import EvidenceQAClaim, EvidenceQADraft
from tests.browser.test_phase3_browser import CHROME, _serve
from tests.fakes import FakeEvidenceQAGenerator, FakeToolCallingModel
from tests.infra.test_conversation_checkpoint_recovery import _container, _settings

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")

PAYLOAD = """**正常内容**

需要申报数据出境安全评估。[SRC-law][SRC-evidence-2]

<img src=x onerror="window.auditExecuted=true">
<svg onload="window.auditExecuted=true"></svg>

[危险链接](javascript:window.auditExecuted=true)
[正常链接](https://example.test/law)

- 列表

```html
<b>代码</b>
```
"""

V1_FLOW = {
    "schema_version": "1.0",
    "title": "历史 v1 安全评估流程",
    "nodes": [
        {"id": "collect", "label": "收集材料", "citation_ids": ["SRC-v1-collect"]},
        {"id": "review", "label": "人工复核", "citation_ids": ["SRC-v1-review"]},
    ],
    "edges": [
        {
            "from": "collect",
            "to": "review",
            "label": "进入下一步",
            "citation_ids": ["SRC-v1-review"],
        }
    ],
}

V2_FLOW = {
    "schema_version": "2.0",
    "title": "v2 分支与汇合流程",
    "coverage": "partial",
    "nodes": [
        {"id": "start", "kind": "start", "label": "开始", "citation_ids": ["SRC-start"]},
        {
            "id": "collect",
            "kind": "action",
            "label": "收集材料 <script>window.auditExecuted=true</script>",
            "citation_ids": ["SRC-collect"],
        },
        {
            "id": "decide",
            "kind": "decision",
            "label": "是否满足条件 <foreignObject onload=window.auditExecuted=true>",
            "citation_ids": ["SRC-decision"],
        },
        {
            "id": "approve",
            "kind": "action",
            "label": "执行安全评估",
            "citation_ids": ["SRC-approve"],
        },
        {
            "id": "merge",
            "kind": "merge",
            "label": "汇合",
            "citation_ids": ["SRC-merge"],
        },
        {
            "id": "finish",
            "kind": "terminal",
            "label": "结束 <a href=https://evil.example>外链</a>",
            "citation_ids": ["SRC-finish"],
        },
    ],
    "edges": [
        {
            "from": "start",
            "to": "collect",
            "condition": "",
            "label": "应先收集申报材料。",
            "citation_ids": ["SRC-collect"],
        },
        {
            "from": "collect",
            "to": "decide",
            "condition": "材料已收集",
            "label": "随后判断是否达到申报条件。",
            "citation_ids": ["SRC-decision"],
        },
        {
            "from": "decide",
            "to": "approve",
            "condition": "是 <script>window.auditExecuted=true</script>",
            "label": "达到条件后进入评估 <image href=https://evil.example/payload>。",
            "citation_ids": ["SRC-approve"],
        },
        {
            "from": "decide",
            "to": "merge",
            "condition": "否",
            "label": "未达到条件时记录判断依据。",
            "citation_ids": ["SRC-skip"],
        },
        {
            "from": "approve",
            "to": "merge",
            "condition": "评估完成",
            "label": "完成评估后汇合处理结果。",
            "citation_ids": ["SRC-merge"],
        },
        {
            "from": "merge",
            "to": "finish",
            "condition": "结果已记录",
            "label": "记录结果后结束流程。",
            "citation_ids": ["SRC-finish"],
        },
    ],
}


class MarkupGenerator(FakeEvidenceQAGenerator):
    def generate(self, **kwargs):
        citation_id = kwargs["citations"][0].citation_id
        self.draft = EvidenceQADraft(
            status="answered",
            claims=[
                EvidenceQAClaim(
                    claim_id="C1",
                    text=PAYLOAD,
                    citation_ids=[citation_id],
                )
            ],
        )
        return super().generate(**kwargs)


def test_live_and_restored_answers_sanitize_markup(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_law",
                        "args": {"query": "出境条件"},
                        "id": "law",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="候选"),
        ]
    )
    container = _container(_settings(tmp_path), model, generator=MarkupGenerator())
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(executable_path=str(CHROME), headless=True)
        try:
            page = browser.new_page()
            page.goto(base_url)
            page.wait_for_function("document.documentElement.dataset.appReady === 'true'")
            page.locator("#composer-input").fill("出境条件是什么？")
            page.locator("#btn-send").click()
            page.wait_for_selector(".answer")
            for restored in [False, True]:
                if restored:
                    page.evaluate("""async () => {
                        const chat = await import('/static/chat.js?v=20261023-flow7');
                        await chat.loadTask(chat.getCurrentTaskId());
                    }""")
                answer = page.locator(".answer").last
                assert answer.locator("img, script, [onerror], [onload]").count() == 0
                assert answer.locator('a[href^="javascript:"]').count() == 0
                assert answer.locator("strong").text_content() == "正常内容"
                citation_texts = answer.locator(".inline-citation").all_text_contents()
                assert citation_texts[:2] == ["[SRC-law]", "[SRC-evidence-2]"]
                assert all(text.startswith("[SRC-") for text in citation_texts)
                citation_style = answer.locator(".inline-citation").first.evaluate(
                    "element => ({fontSize: getComputedStyle(element).fontSize, "
                    "color: getComputedStyle(element).color})"
                )
                assert float(citation_style["fontSize"].removesuffix("px")) < 14
                assert answer.locator("pre code").text_content().strip() == "<b>代码</b>"
                assert answer.locator('a[href="https://example.test/law"]').count() == 1
                assert not page.evaluate("Boolean(window.auditExecuted)")
            assert page.evaluate("""async () => {
                const { renderSafeMarkdown } = await import('/static/markdown.js?v=20261023-flow7');
                window.DOMPurify = undefined;
                const element = document.createElement('div');
                renderSafeMarkdown(element, '<img src=x onerror=alert(1)>');
                return element.children.length === 0 && element.textContent.includes('<img');
            }""")
        finally:
            browser.close()


def _flow_markdown(*flows):
    return "\n\n".join(
        f"```riskpilot-flow\n{json.dumps(flow, ensure_ascii=False, separators=(',', ':'))}\n```"
        for flow in flows
    )


def _assert_safe_flowcharts(page):
    answer = page.locator(".answer").last
    assert answer.locator(".risk-flowchart").count() == 2
    assert answer.locator('.risk-flowchart[data-schema-version="1.0"]').count() == 1
    v2 = answer.locator('.risk-flowchart[data-schema-version="2.0"]')
    assert v2.count() == 1
    assert v2.locator("svg.risk-flowchart-svg").count() == 1
    assert v2.locator(".risk-flowchart-coverage-badge").text_content() == "部分覆盖"
    assert v2.locator('.risk-flow-node[data-kind="start"] rect.risk-flow-node-start').count() == 1
    assert v2.locator('.risk-flow-node[data-kind="action"] rect').count() == 2
    assert v2.locator('.risk-flow-node[data-kind="decision"] polygon').count() == 1
    assert v2.locator('.risk-flow-node[data-kind="merge"] polygon').count() == 1
    assert v2.locator('.risk-flow-node[data-kind="terminal"] rect').count() == 2
    assert (
        "分支判断"
        in v2.locator('.risk-flow-node[data-kind="decision"] .risk-flow-node-label').text_content()
    )
    assert (
        "分支汇合"
        in v2.locator('.risk-flow-node[data-kind="merge"] .risk-flow-node-label').text_content()
    )
    assert v2.locator(".risk-flow-edge-condition").count() == len(V2_FLOW["edges"])
    assert v2.locator('.risk-flow-edge[data-routing="side"]').count() >= 1
    assert "是 <script>window.auditExecuted=true</script>" in v2.text_content()
    assert "<image href=https://evil.example/payload>" in v2.text_content()
    assert "<foreignObject onload=window.auditExecuted=true>" in v2.text_content()
    assert v2.locator("foreignObject, script, a, image, use").count() == 0
    assert answer.locator("pre > code.language-riskpilot-flow").count() == 0
    unsafe_attributes = v2.evaluate("""root =>
        [...root.querySelectorAll('*')].flatMap(element =>
            [...element.attributes]
                .filter(attribute =>
                    attribute.name.toLowerCase().startsWith('on')
                    || ['href', 'xlink:href', 'src'].includes(attribute.name.toLowerCase())
                )
                .map(attribute => `${element.tagName}:${attribute.name}`)
        )
    """)
    assert unsafe_attributes == []
    svg_tags = set(
        v2.locator("svg").evaluate(
            "root => [...root.querySelectorAll('*')].map(element => element.localName)"
        )
    )
    assert svg_tags <= {"g", "path", "polygon", "rect", "text", "title", "tspan"}
    assert not page.evaluate("Boolean(window.auditExecuted)")


def test_v1_v2_flowcharts_render_live_and_restore_safely(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    container = _container(
        _settings(tmp_path), FakeToolCallingModel(responses=[AIMessage(content="done")])
    )
    markdown = _flow_markdown(V1_FLOW, V2_FLOW)
    with _serve(container) as base_url, playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(executable_path=str(CHROME), headless=True)
        try:
            page = browser.new_page()
            page.goto(base_url)
            page.wait_for_function("document.documentElement.dataset.appReady === 'true'")

            stream = "".join(
                [
                    'event: run_started\ndata: {"run_id":"flow-live"}\n\n',
                    'event: run_status\ndata: {"status":"completed","stage":"completed"}\n\n',
                    "event: answer\ndata: "
                    + json.dumps(
                        {"text": markdown, "msg_id": "flow-live-answer"}, ensure_ascii=False
                    )
                    + "\n\n",
                ]
            )
            page.route(
                "**/api/v2/copilot/chat/stream",
                lambda route: route.fulfill(
                    status=200,
                    headers={
                        "Content-Type": "text/event-stream; charset=utf-8",
                        "Cache-Control": "no-store",
                    },
                    body=stream,
                ),
            )
            page.locator("#composer-input").fill("展示 v2 流程")
            page.locator("#btn-send").click()
            page.wait_for_selector('.answer .risk-flowchart[data-schema-version="2.0"]')
            _assert_safe_flowcharts(page)

            history = {
                "task": {"task_id": "flowchart-history", "title": "流程历史"},
                "messages": [
                    {
                        "msg_id": "flow-history-answer",
                        "role": "assistant",
                        "content": markdown,
                        "citations": [],
                        "created_at": 1,
                    }
                ],
                "attachment_doc_ids": [],
                "latest_run": None,
            }
            page.route(
                "**/api/v2/tasks/flowchart-history",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(history, ensure_ascii=False),
                ),
            )
            page.evaluate("""async () => {
                const chat = await import('/static/chat.js?v=20261023-flow7');
                await chat.loadTask('flowchart-history');
            }""")
            page.wait_for_selector('.answer .risk-flowchart[data-schema-version="2.0"]')
            _assert_safe_flowcharts(page)

            oversized_nodes = [
                {
                    "id": f"node{index}",
                    "kind": "start" if index == 0 else "terminal" if index == 24 else "action",
                    "label": f"节点 {index}",
                    "citation_ids": [f"SRC-node-{index}"],
                }
                for index in range(25)
            ]
            oversized_edges = [
                {
                    "from": f"node{index}",
                    "to": f"node{index + 1}",
                    "condition": "继续",
                    "label": "有证据支持继续。",
                    "citation_ids": [f"SRC-{index}"],
                }
                for index in range(24)
            ]
            cyclic_v2 = json.loads(json.dumps(V2_FLOW))
            cyclic_v2["title"] = "v2 无效环图"
            cyclic_v2["edges"].append(
                {
                    "from": "finish",
                    "to": "start",
                    "condition": "重新开始",
                    "label": "非法返回起点。",
                    "citation_ids": ["SRC-cycle"],
                }
            )
            invalid_flows = [
                {
                    "schema_version": "1.0",
                    "title": "无效环图",
                    "nodes": [
                        {"id": "a", "label": "A", "citation_ids": []},
                        {"id": "b", "label": "B", "citation_ids": []},
                    ],
                    "edges": [
                        {"from": "a", "to": "b", "label": "下一步", "citation_ids": []},
                        {"from": "b", "to": "a", "label": "返回", "citation_ids": []},
                    ],
                },
                {key: value for key, value in V2_FLOW.items() if key != "coverage"},
                {**V2_FLOW, "coverage": "unknown"},
                {**V2_FLOW, "unexpected": True},
                cyclic_v2,
                {
                    "schema_version": "2.0",
                    "title": "超过节点上限",
                    "nodes": oversized_nodes,
                    "edges": oversized_edges,
                },
                {
                    "schema_version": "2.0",
                    "title": "非法单分支 decision",
                    "nodes": [
                        {
                            "id": "s",
                            "kind": "start",
                            "label": "开始",
                            "citation_ids": ["SRC-s"],
                        },
                        {
                            "id": "d",
                            "kind": "decision",
                            "label": "判断",
                            "citation_ids": ["SRC-d"],
                        },
                        {
                            "id": "t",
                            "kind": "terminal",
                            "label": "结束",
                            "citation_ids": ["SRC-t"],
                        },
                    ],
                    "edges": [
                        {
                            "from": "s",
                            "to": "d",
                            "condition": "继续",
                            "label": "进入判断。",
                            "citation_ids": ["SRC-d"],
                        },
                        {
                            "from": "d",
                            "to": "t",
                            "condition": "是",
                            "label": "结束。",
                            "citation_ids": ["SRC-t"],
                        },
                    ],
                },
            ]
            assert page.evaluate(
                """async invalidFlows => {
                    const { renderSafeMarkdown } = await import(
                        '/static/markdown.js?v=20261023-flow7'
                    );
                    const host = document.createElement('div');
                    for (const flow of invalidFlows) {
                        const item = document.createElement('div');
                        renderSafeMarkdown(
                            item,
                            '```riskpilot-flow\\n' + JSON.stringify(flow) + '\\n```',
                        );
                        host.appendChild(item);
                    }
                    return host.querySelectorAll('pre > code.language-riskpilot-flow').length
                            === invalidFlows.length
                        && host.querySelectorAll('.risk-flowchart').length === 0;
                }""",
                invalid_flows,
            )
            complete_flow = {**V2_FLOW, "coverage": "complete"}
            assert page.evaluate(
                """async completeFlow => {
                    const { renderSafeMarkdown } = await import(
                        '/static/markdown.js?v=20261023-flow7'
                    );
                    const element = document.createElement('div');
                    renderSafeMarkdown(
                        element,
                        '```riskpilot-flow\\n' + JSON.stringify(completeFlow) + '\\n```',
                    );
                    const flow = element.querySelector(
                        '.risk-flowchart[data-schema-version="2.0"][data-coverage="complete"]'
                    );
                    return flow !== null
                        && flow.querySelector('.risk-flowchart-coverage-badge') === null;
                }""",
                complete_flow,
            )
        finally:
            browser.close()
