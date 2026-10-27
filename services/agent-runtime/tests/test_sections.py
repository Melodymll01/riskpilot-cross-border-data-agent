"""轻量 Parent-Child 章节识别与跨页继承测试。"""

from ingestion.unified_loader import RawDocument, RawSegment
from processing.metadata import build_chunks
from processing.sections import PARENT_CHILD_SCHEMA_VERSION, section_segments
from processing.splitter import TextSplitter


def _document() -> RawDocument:
    return RawDocument(
        content="",
        source_type="file",
        source_name="guide.pdf",
        title="申报指南",
        segments=[
            RawSegment(
                content=(
                    "二、申报方式及流程\n"
                    "（一）系统登录与项目申报\n"
                    "1.账号注册与登录。\n"
                    "2.项目新建与申报。\n"
                    "（二）完备性查验与项目受理\n"
                    "数据处理者可查看进展，具体包"
                ),
                extra_metadata={"page_number": "3"},
            ),
            RawSegment(
                content=(
                    "括：\n"
                    "1.省级网信办在五个工作日内完成完备性查验。\n"
                    "2.通过后提请国家网信办受理。\n"
                    "（三）补充更正材料与查询评估进展\n"
                    "国家网信办受理后组织评估。"
                ),
                extra_metadata={"page_number": "4"},
            ),
            RawSegment(
                content="三、申请延长评估结果有效期\n符合条件的可以申请延长。",
                extra_metadata={"page_number": "5"},
            ),
        ],
    )


def test_cross_page_continuation_inherits_parent_and_leaf_section() -> None:
    segments = section_segments(_document())
    continuation = next(segment for segment in segments if segment.content.startswith("括："))
    login = next(segment for segment in segments if "账号注册" in segment.content)
    extension = next(segment for segment in segments if segment.content.startswith("三、"))

    assert continuation.extra_metadata["page_number"] == "4"
    assert continuation.extra_metadata["section_title"] == "（二）完备性查验与项目受理"
    assert continuation.extra_metadata["parent_section_title"] == "二、申报方式及流程"
    assert (
        continuation.extra_metadata["parent_section_id"]
        == login.extra_metadata["parent_section_id"]
    )
    assert continuation.extra_metadata["section_id"] != login.extra_metadata["section_id"]
    assert (
        extension.extra_metadata["parent_section_id"]
        != continuation.extra_metadata["parent_section_id"]
    )
    assert all(
        segment.extra_metadata["index_schema_version"] == PARENT_CHILD_SCHEMA_VERSION
        for segment in segments
    )


def test_numbered_process_steps_are_children_not_new_parent_sections() -> None:
    segments = section_segments(_document())
    login = next(segment for segment in segments if "账号注册" in segment.content)
    assert "1.账号注册与登录" in login.content
    assert "2.项目新建与申报" in login.content


def test_build_chunks_keeps_quote_text_but_augments_embedding_text() -> None:
    chunks = build_chunks(
        _document(),
        splitter=TextSplitter(chunk_size=420, chunk_overlap=84),
    )
    continuation = next(chunk for chunk in chunks if chunk.text.startswith("括："))

    assert continuation.text.startswith("括：")
    assert continuation.embedding_text is not None
    assert "二、申报方式及流程" in continuation.embedding_text
    assert "（二）完备性查验与项目受理" in continuation.embedding_text
    assert continuation.source_metadata["chunk_role"] == "child"
    assert continuation.source_metadata["chunk_index_in_section"] == "1"


def test_article_without_chapter_uses_itself_as_parent() -> None:
    doc = RawDocument(
        content="第三十八条 个人信息出境应具备法定条件。",
        source_type="file",
        source_name="law.txt",
        title="个人信息保护法",
    )
    segment = section_segments(doc)[0]
    assert segment.extra_metadata["section_title"].startswith("第三十八条")
    assert segment.extra_metadata["parent_section_title"] == segment.extra_metadata["section_title"]
    assert segment.extra_metadata["parent_section_id"] == segment.extra_metadata["section_id"]


def test_parenthesized_list_items_are_not_promoted_to_sections() -> None:
    doc = RawDocument(
        content="第五条 申报材料包括：\n（一）申报书；\n（二）风险自评估报告；",
        source_type="web",
        source_name="rule.html",
        title="评估办法",
    )
    segments = section_segments(doc)
    assert len(segments) == 1
    assert "（一）申报书；" in segments[0].content
    assert segments[0].extra_metadata["section_title"].startswith("第五条")
