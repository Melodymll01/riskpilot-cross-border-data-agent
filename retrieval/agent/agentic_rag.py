"""Agentic RAG 主控 Agent：基于决策的自反思检索助手。

与 v1 的本质区别：v1 是"固定流程多跑几步"，v2 是"每一步都做判断"。

决策流程：

  用户问题                                                 
                                                          
  [决策1] 问题分类  定义/对比/条件/流程/案例？              
      （不同类型走不同路径）                                
  [决策2] 查询变换  根据类型选择改写策略                    
      （definition 只改写不拆解，case 必须拆要素）          
  [决策3] 检索  single / multi_query / decomposed？        
                                                          
  [决策4] 证据够不够？ 具体缺什么？怎么补？                 
     sufficient   直接生成回答                          
     partial      用建议的补充查询再查一轮               
     insufficient  换策略/联网搜索/建议拒答              
      （最多迭代 3 轮，每轮都重新判断）                     
  [决策5] 最终生成  普通回答 or 保守结论 or 拒答            

"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Generator

from config import settings
from retrieval.search.embedder import Embedder
from retrieval.search.vector_store import VectorStore
from retrieval.agent.question_classifier import QuestionClassifier, QuestionAnalysis, QUESTION_TYPES
from retrieval.agent.query_transformer import QueryTransformer, TransformedQuery
from retrieval.agent.quality_grader import EvidenceChecker, EvidenceVerdict, EvidenceCheckResult
from retrieval.agent.web_searcher import WebSearcher
from retrieval.generation.report_generator import ReportGenerator, DeepReport
from retrieval.generation.qa_chain import QAChain, QAResult
from retrieval.search.reranker import BaseReranker, DistanceThresholdReranker

logger = logging.getLogger(__name__)

MAX_RETRIEVAL_ROUNDS = 3

# OOD 相似度校验阈值（distance 越小越相关；Chroma 默认用 L2 或 cosine distance）
# 探针检索时，若最小 distance 大于此值，视为"确实与知识库无关"
OOD_DISTANCE_THRESHOLD = 0.65
OOD_PROBE_TOP_K = 3


@dataclass
class AgentStep:
    """Agent 执行的单个步骤记录。"""
    step_name: str
    description: str
    result_summary: str


@dataclass
class AgenticRAGResult:
    """Agentic RAG 完整执行结果。"""
    answer: str = ""
    report: Optional[DeepReport] = None
    citations: List[Dict[str, Any]] = field(default_factory=list)

    # 决策过程信息
    original_query: str = ""
    question_type: str = ""
    question_type_label: str = ""
    transformed_queries: List[str] = field(default_factory=list)
    retrieval_rounds: int = 0
    total_docs_retrieved: int = 0
    web_search_used: bool = False
    refused: bool = False
    steps: List[AgentStep] = field(default_factory=list)


class AgenticRAGAgent:
    """Agentic RAG 主控 Agent每一步都做决策，不走死流程。"""

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        reranker: Optional[BaseReranker] = None,
    ):
        self.embedder = embedder
        self.vector_store = vector_store
        self.reranker = reranker or DistanceThresholdReranker()

        self.classifier = QuestionClassifier()
        self.transformer = QueryTransformer()
        self.evidence_checker = EvidenceChecker()
        self.web_searcher = WebSearcher()
        self.report_generator = ReportGenerator()
        self.qa_chain = QAChain()

    # ==================== 非流式入口 ====================

    def research(
        self,
        query: str,
        mode: str = "report",
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> AgenticRAGResult:
        """执行完整的 Agentic RAG 研究流程（非流式）。"""
        result = AgenticRAGResult(original_query=query)

        #  决策 1：分类问题 
        analysis = self.classifier.classify(query)
        result.question_type = analysis.question_type
        result.question_type_label = analysis.type_label
        result.steps.append(AgentStep(
            "classify",
            f"识别问题类型: {analysis.type_label}",
            f"类型={analysis.question_type}, "
            f"策略={analysis.retrieval_strategy}, "
            f"需要拆解={analysis.needs_decomposition}",
        ))

        #  OOD 软判决：根据置信度分三档处理（避免"一错到底"）
        if analysis.is_out_of_scope:
            decision = analysis.ood_decision  # hard_refuse / probe / downgrade
            logger.info(f"OOD 检测: decision={decision}, confidence={analysis.confidence:.2f}")

            if decision == "hard_refuse":
                # 高置信 OOD：仍做一次相似度校验，两个独立信号都说 OOD 才真拒答
                really_ood, min_dist, _ = self._ood_similarity_probe(query)
                if really_ood:
                    result.refused = True
                    result.answer = (
                        "这个问题不在我的能力范围内。\n\n"
                        "本系统专注于**数据出境 / 跨境合规 / 个人信息保护**相关法规问答。\n\n"
                        f"**识别依据：** LLM 判定领域外（置信度 {analysis.confidence:.0%}），"
                        f"向量校验最小距离 {min_dist:.2f} > 阈值 {OOD_DISTANCE_THRESHOLD}\n\n"
                        "**建议：** 请尝试提问与数据出境相关的问题。"
                    )
                    result.steps.append(AgentStep(
                        "out_of_scope", "领域外问题（双信号确认），短路拒答",
                        f"LLM confidence={analysis.confidence:.2f}, min_dist={min_dist:.2f}",
                    ))
                    return result
                else:
                    # 向量说有关，LLM 误判 → 降级
                    logger.info(f"OOD 被向量校验否决（min_dist={min_dist:.2f}），降级为 condition")
                    analysis.question_type = "condition"
                    analysis.type_label = QUESTION_TYPES["condition"]
                    analysis.retrieval_strategy = "multi_query"
                    analysis.evidence_threshold = "high"
                    result.steps.append(AgentStep(
                        "ood_downgrade", "LLM 判 OOD 被向量校验否决，降级走正常流程",
                        f"min_dist={min_dist:.2f} <= {OOD_DISTANCE_THRESHOLD}",
                    ))

            elif decision == "probe":
                # 中置信 OOD：跑探针，无相关才拒答
                really_ood, min_dist, _ = self._ood_similarity_probe(query)
                if really_ood:
                    result.refused = True
                    result.answer = (
                        "这个问题可能不在我的能力范围内。\n\n"
                        "本系统专注于**数据出境 / 跨境合规 / 个人信息保护**相关法规问答。\n\n"
                        f"**识别依据：** LLM 低置信 OOD（{analysis.confidence:.0%}），"
                        f"探针检索亦无相关内容（min_dist={min_dist:.2f}）。\n\n"
                        "**建议：** 如果您的问题确属本领域，请换种表述重试。"
                    )
                    result.steps.append(AgentStep(
                        "out_of_scope", "中置信 OOD + 探针确认，短路拒答",
                        f"confidence={analysis.confidence:.2f}, min_dist={min_dist:.2f}",
                    ))
                    return result
                else:
                    logger.info(f"OOD 被探针否决（min_dist={min_dist:.2f}），降级为 condition")
                    analysis.question_type = "condition"
                    analysis.type_label = QUESTION_TYPES["condition"]
                    analysis.retrieval_strategy = "multi_query"
                    analysis.evidence_threshold = "high"
                    result.steps.append(AgentStep(
                        "ood_downgrade", "LLM 中置信 OOD 被探针否决，降级走正常流程",
                        f"min_dist={min_dist:.2f}",
                    ))

            else:  # downgrade
                # 低置信 OOD：直接降级，不浪费探针
                logger.info(f"OOD 置信度过低（{analysis.confidence:.2f}），直接降级")
                analysis.question_type = "condition"
                analysis.type_label = QUESTION_TYPES["condition"]
                analysis.retrieval_strategy = "multi_query"
                analysis.evidence_threshold = "high"
                result.steps.append(AgentStep(
                    "ood_downgrade", "LLM 低置信 OOD，直接降级走正常流程",
                    f"confidence={analysis.confidence:.2f}",
                ))

        #  决策 2：根据类型做查询变换 
        transformed = self.transformer.transform(
            query,
            question_type=analysis.question_type,
            needs_decomposition=analysis.needs_decomposition,
        )
        result.transformed_queries = transformed.all_queries
        result.steps.append(AgentStep(
            "transform",
            f"按[{analysis.type_label}]策略改写查询",
            f"生成 {len(transformed.all_queries)} 个检索查询",
        ))

        #  决策 3~4：迭代检索 + 证据检查循环 
        all_docs = []
        seen_ids = set()
        web_search_used = False
        last_check = None

        for round_num in range(1, MAX_RETRIEVAL_ROUNDS + 1):
            result.retrieval_rounds = round_num

            # 决策 3：确定本轮检索查询
            if round_num == 1:
                queries = transformed.all_queries
            else:
                # 后续轮次：用证据检查器建议的补充查询
                queries = last_check.supplement_queries if last_check else []
                if not queries:
                    break

            # 执行检索
            round_docs = self._retrieve(queries, top_k, seen_ids)
            all_docs.extend(round_docs)

            result.steps.append(AgentStep(
                f"retrieve_{round_num}",
                f"第 {round_num} 轮检索 ({len(queries)} 个查询)",
                f"新增 {len(round_docs)} 条，累计 {len(all_docs)} 条",
            ))

            if not all_docs:
                # 一条都没有，直接尝试联网
                if enable_web_search:
                    web_docs = self._do_web_search(query)
                    if web_docs:
                        all_docs.extend(web_docs)
                        web_search_used = True
                        result.steps.append(AgentStep(
                            "web_search",
                            "知识库无相关内容，联网搜索补充",
                            f"获得 {len(web_docs)} 条",
                        ))
                break

            # 决策 4：证据充分性检查
            last_check = self.evidence_checker.check(
                query, all_docs,
                question_type=analysis.question_type,
                evidence_threshold=analysis.evidence_threshold,
            )
            result.steps.append(AgentStep(
                f"evidence_check_{round_num}",
                f"第 {round_num} 轮证据检查",
                f"判定={last_check.verdict.value}, "
                f"置信度={last_check.confidence:.0%}, "
                f"缺失={last_check.missing_aspects or '无'}",
            ))

            # 只保留有用的文档
            all_docs = last_check.useful_docs

            # 根据判定决定下一步
            if last_check.verdict == EvidenceVerdict.SUFFICIENT:
                logger.info(f"第 {round_num} 轮: 证据充分，结束检索")
                break

            # 证据严重不足且允许联网：优先联网兜底，联网失败再考虑拒答
            if last_check.verdict == EvidenceVerdict.INSUFFICIENT and enable_web_search:
                logger.info(f"第 {round_num} 轮: 证据严重不足，尝试联网搜索补充")
                web_docs = self._do_web_search(query)
                if web_docs:
                    all_docs.extend(web_docs)
                    web_search_used = True
                    result.steps.append(AgentStep(
                        "web_search",
                        "证据严重不足，联网搜索补充",
                        f"获得 {len(web_docs)} 条",
                    ))
                    break
                # 联网拿不到内容，回退到拒答判断
                if last_check.should_refuse:
                    logger.info(f"第 {round_num} 轮: 联网无结果，按建议拒答")
                    result.refused = True
                break

            if last_check.should_refuse:
                logger.info(f"第 {round_num} 轮: 建议拒答")
                result.refused = True
                break

            # partial  如果有补充查询建议，继续下一轮
            if not last_check.supplement_queries:
                logger.info(f"第 {round_num} 轮: partial 但无补充建议，结束")
                break

            logger.info(
                f"第 {round_num} 轮: partial, 补充查询: {last_check.supplement_queries}"
            )

        # 重排序 + 截取
        all_docs = self.reranker.rerank(query, all_docs)
        max_docs = top_k * 2 if mode == "report" else top_k
        all_docs = all_docs[:max_docs]

        result.total_docs_retrieved = len(all_docs)
        result.web_search_used = web_search_used

        #  决策 5：生成输出 
        if result.refused and not all_docs:
            result.answer = (
                "根据现有知识库内容，暂时无法回答该问题。\n\n"
                f"**原因：** {last_check.reasoning}\n\n"
                "**建议：** 请尝试导入更多相关法规文档，或调整问题表述后重试。"
            )
            result.steps.append(AgentStep("refuse", "证据不足，选择拒答", last_check.reasoning))
        elif mode == "report":
            result.steps.append(AgentStep(
                "generate", f"基于 {len(all_docs)} 条文档生成深度报告", "生成中...",
            ))
            report = self.report_generator.generate(
                query, all_docs,
                retrieval_rounds=result.retrieval_rounds,
                web_search_used=web_search_used,
            )
            result.report = report
            result.answer = report.content
            result.citations = [
                {"index": c.index, "source_type": c.source_type,
                 "source_name": c.source_name, "title": c.title,
                 "source_url": c.source_url, "text_snippet": c.text_snippet}
                for c in report.citations
            ]
            result.steps[-1].result_summary = f"{report.word_count} 字, {report.source_count} 个来源"
        else:
            result.steps.append(AgentStep(
                "generate", f"基于 {len(all_docs)} 条文档生成回答", "生成中...",
            ))
            qa_result = self.qa_chain.generate(query, all_docs)
            result.answer = qa_result.answer
            result.citations = [
                {"source_type": c.source_type, "source_name": c.source_name,
                 "title": c.title, "source_url": c.source_url,
                 "text_snippet": c.text_snippet}
                for c in qa_result.citations
            ]
            result.steps[-1].result_summary = "回答生成完成"

        return result

    # ==================== 流式入口 ====================

    def research_stream(
        self,
        query: str,
        mode: str = "report",
        top_k: int = 8,
        enable_web_search: bool = True,
    ) -> Generator:
        """
        流式执行 Agentic RAG 研究流程。

        Yields:
            dict: {"type": "step"/"token"/"result", ...}
        """

        #  决策 1：分类 
        yield {"type": "step", "data": {
            "step": "classify", "status": "running",
            "description": "正在分析问题类型...",
        }}
        analysis = self.classifier.classify(query)
        yield {"type": "step", "data": {
            "step": "classify", "status": "done",
            "description": f"问题类型: {analysis.type_label}  策略: {analysis.retrieval_strategy}",
        }}

        #  OOD 软判决（与非流式版对齐）
        if analysis.is_out_of_scope:
            decision = analysis.ood_decision
            really_ood = False
            min_dist = 1.0

            if decision in ("hard_refuse", "probe"):
                yield {"type": "step", "data": {
                    "step": "ood_probe", "status": "running",
                    "description": f"LLM 判 OOD（{decision}），执行向量相似度校验...",
                }}
                really_ood, min_dist, _ = self._ood_similarity_probe(query)
                yield {"type": "step", "data": {
                    "step": "ood_probe", "status": "done",
                    "description": (
                        f"校验完成：min_dist={min_dist:.2f}"
                        f"{' > ' if really_ood else ' <= '}"
                        f"{OOD_DISTANCE_THRESHOLD} → "
                        f"{'确认 OOD' if really_ood else 'LLM 误判，降级'}"
                    ),
                }}
            # decision == "downgrade" 时 really_ood=False，直接降级

            if really_ood:
                refuse_text = (
                    "这个问题不在我的能力范围内。\n\n"
                    "本系统专注于**数据出境 / 跨境合规 / 个人信息保护**相关法规问答。\n\n"
                    f"**识别依据：** LLM 判定领域外（置信度 {analysis.confidence:.0%}），"
                    f"向量校验最小距离 {min_dist:.2f} > 阈值 {OOD_DISTANCE_THRESHOLD}\n\n"
                    "**建议：** 请尝试提问与数据出境相关的问题。"
                )
                yield {"type": "step", "data": {
                    "step": "out_of_scope", "status": "done",
                    "description": "领域外问题（双信号确认），短路拒答",
                }}
                yield {"type": "token", "content": refuse_text}
                yield {"type": "result", "data": {
                    "retrieval_rounds": 0,
                    "total_docs": 0,
                    "web_search_used": False,
                    "transformed_queries": [],
                    "question_type": analysis.question_type,
                    "question_type_label": analysis.type_label,
                    "refused": True,
                    "citations": [],
                }}
                yield {"type": "done"}
                return
            else:
                # 降级为 condition，继续走正常流程
                analysis.question_type = "condition"
                analysis.type_label = QUESTION_TYPES["condition"]
                analysis.retrieval_strategy = "multi_query"
                analysis.evidence_threshold = "high"
                yield {"type": "step", "data": {
                    "step": "ood_downgrade", "status": "done",
                    "description": "LLM OOD 判断被否决，降级为 condition 走正常流程",
                }}

        #  决策 2：查询变换 
        yield {"type": "step", "data": {
            "step": "transform", "status": "running",
            "description": f"按[{analysis.type_label}]策略改写查询...",
        }}
        transformed = self.transformer.transform(
            query,
            question_type=analysis.question_type,
            needs_decomposition=analysis.needs_decomposition,
        )
        yield {"type": "step", "data": {
            "step": "transform", "status": "done",
            "description": f"生成 {len(transformed.all_queries)} 个检索查询",
            "queries": transformed.all_queries[:6],
        }}

        #  决策 3~4：迭代检索 
        all_docs = []
        seen_ids = set()
        web_search_used = False
        retrieval_rounds = 0
        last_check = None
        refused = False

        for round_num in range(1, MAX_RETRIEVAL_ROUNDS + 1):
            retrieval_rounds = round_num

            if round_num == 1:
                queries = transformed.all_queries
            else:
                queries = last_check.supplement_queries if last_check else []
                if not queries:
                    break

            yield {"type": "step", "data": {
                "step": f"retrieve_{round_num}", "status": "running",
                "description": f"第 {round_num} 轮检索 ({len(queries)} 个查询)...",
            }}

            round_docs = self._retrieve(queries, top_k, seen_ids)
            all_docs.extend(round_docs)

            yield {"type": "step", "data": {
                "step": f"retrieve_{round_num}", "status": "done",
                "description": f"检索完成，新增 {len(round_docs)} 条，累计 {len(all_docs)} 条",
            }}

            if not all_docs:
                if enable_web_search:
                    yield {"type": "step", "data": {
                        "step": "web_search", "status": "running",
                        "description": "知识库无相关内容，联网搜索...",
                    }}
                    web_docs = self._do_web_search(query)
                    if web_docs:
                        all_docs.extend(web_docs)
                        web_search_used = True
                    yield {"type": "step", "data": {
                        "step": "web_search", "status": "done",
                        "description": f"联网搜索完成，获得 {len(web_docs)} 条",
                    }}
                break

            # 证据检查
            yield {"type": "step", "data": {
                "step": f"evidence_{round_num}", "status": "running",
                "description": "正在检查证据是否充分...",
            }}
            last_check = self.evidence_checker.check(
                query, all_docs,
                question_type=analysis.question_type,
                evidence_threshold=analysis.evidence_threshold,
            )

            verdict_desc = {
                EvidenceVerdict.SUFFICIENT: "✓ 证据充分",
                EvidenceVerdict.PARTIAL: " 部分充分",
                EvidenceVerdict.INSUFFICIENT: "✕ 证据不足",
            }[last_check.verdict]
            missing_text = f"，缺失: {', '.join(last_check.missing_aspects)}" if last_check.missing_aspects else ""

            yield {"type": "step", "data": {
                "step": f"evidence_{round_num}", "status": "done",
                "description": f"{verdict_desc} (置信度 {last_check.confidence:.0%}){missing_text}",
                "verdict": last_check.verdict.value,
                "confidence": last_check.confidence,
            }}

            all_docs = last_check.useful_docs

            if last_check.verdict == EvidenceVerdict.SUFFICIENT:
                break

            # 证据严重不足且允许联网：优先联网兜底，联网失败再考虑拒答
            if last_check.verdict == EvidenceVerdict.INSUFFICIENT and enable_web_search:
                yield {"type": "step", "data": {
                    "step": "web_search", "status": "running",
                    "description": "证据严重不足，联网搜索补充...",
                }}
                web_docs = self._do_web_search(query)
                if web_docs:
                    all_docs.extend(web_docs)
                    web_search_used = True
                    yield {"type": "step", "data": {
                        "step": "web_search", "status": "done",
                        "description": f"获得 {len(web_docs)} 条联网结果",
                    }}
                    break
                # 联网无结果
                yield {"type": "step", "data": {
                    "step": "web_search", "status": "done",
                    "description": "联网未获得有效结果",
                }}
                if last_check.should_refuse:
                    refused = True
                break

            if last_check.should_refuse:
                refused = True
                break

            if not last_check.supplement_queries:
                break

        # 重排序
        all_docs = self.reranker.rerank(query, all_docs)
        max_docs = top_k * 2 if mode == "report" else top_k
        all_docs = all_docs[:max_docs]

        #  决策 5：生成 
        if refused and not all_docs:
            refuse_text = (
                "根据现有知识库内容，暂时无法回答该问题。\n\n"
                f"**原因：** {last_check.reasoning if last_check else '证据不足'}\n\n"
                "**建议：** 请尝试导入更多相关法规文档，或调整问题表述后重试。"
            )
            yield {"type": "step", "data": {
                "step": "refuse", "status": "done",
                "description": "证据不足，选择拒答",
            }}
            yield {"type": "token", "content": refuse_text}
            yield {"type": "result", "data": {
                "retrieval_rounds": retrieval_rounds,
                "total_docs": 0,
                "web_search_used": web_search_used,
                "transformed_queries": transformed.all_queries[:6],
                "question_type": analysis.question_type,
                "question_type_label": analysis.type_label,
                "refused": True,
                "citations": [],
            }}
            yield {"type": "done"}
            return

        yield {"type": "step", "data": {
            "step": "generate", "status": "running",
            "description": f"基于 {len(all_docs)} 条参考资料生成{'深度报告' if mode == 'report' else '回答'}...",
        }}

        if mode == "report":
            gen = self.report_generator.generate_stream(
                query, all_docs,
                retrieval_rounds=retrieval_rounds,
                web_search_used=web_search_used,
            )
        else:
            gen = self.qa_chain.generate_stream(query, all_docs)

        final_obj = None
        for item in gen:
            if isinstance(item, (DeepReport, QAResult)):
                final_obj = item
            else:
                yield {"type": "token", "content": item}

        # 构建最终结果
        result_data = {
            "retrieval_rounds": retrieval_rounds,
            "total_docs": len(all_docs),
            "web_search_used": web_search_used,
            "transformed_queries": transformed.all_queries[:6],
            "question_type": analysis.question_type,
            "question_type_label": analysis.type_label,
            "refused": False,
        }

        if isinstance(final_obj, DeepReport):
            result_data["citations"] = [
                {"index": c.index, "source_type": c.source_type,
                 "source_name": c.source_name, "title": c.title,
                 "source_url": c.source_url, "text_snippet": c.text_snippet}
                for c in final_obj.citations
            ]
            result_data["word_count"] = final_obj.word_count
        elif isinstance(final_obj, QAResult):
            result_data["citations"] = [
                {"source_type": c.source_type, "source_name": c.source_name,
                 "title": c.title, "source_url": c.source_url,
                 "text_snippet": c.text_snippet}
                for c in final_obj.citations
            ]
            result_data["has_enough_context"] = final_obj.has_enough_context

        yield {"type": "step", "data": {"step": "generate", "status": "done", "description": "生成完成"}}
        yield {"type": "result", "data": result_data}

    #  内部方法 

    def _retrieve(
        self,
        queries: List[str],
        top_k: int,
        seen_ids: set,
    ) -> List[Dict[str, Any]]:
        """对多个查询检索并合并去重。"""
        new_results = []
        for q in queries:
            q_embedding = self.embedder.embed_query(q)
            results = self.vector_store.query(q_embedding, top_k=top_k)
            for r in results:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    new_results.append(r)
        return new_results

    def _do_web_search(self, query: str) -> List[Dict[str, Any]]:
        """执行联网搜索并返回统一格式的 chunks。"""
        try:
            results = self.web_searcher.search(query, max_results=3)
            return self.web_searcher.results_to_chunks(results)
        except Exception as e:
            logger.warning(f"联网搜索失败: {e}")
            return []

    def _ood_similarity_probe(self, query: str) -> tuple:
        """
        OOD 探针检索：用小 top_k 查向量库，看最小 distance。

        Returns:
            (is_really_ood: bool, min_distance: float, probe_docs: List[Dict])
            - is_really_ood=True：确实与知识库无关，可以真拒答
            - is_really_ood=False：有相关内容，LLM 误判 OOD，应降级走正常流程
        """
        try:
            q_emb = self.embedder.embed_query(query)
            probe_docs = self.vector_store.query(q_emb, top_k=OOD_PROBE_TOP_K)
            if not probe_docs:
                return True, 1.0, []
            min_dist = min(d.get("distance", 1.0) for d in probe_docs)
            is_really_ood = min_dist > OOD_DISTANCE_THRESHOLD
            logger.info(
                f"OOD 探针: min_distance={min_dist:.3f}, "
                f"threshold={OOD_DISTANCE_THRESHOLD}, really_ood={is_really_ood}"
            )
            return is_really_ood, min_dist, probe_docs
        except Exception as e:
            logger.warning(f"OOD 探针失败，保守判真 OOD: {e}")
            return True, 1.0, []
