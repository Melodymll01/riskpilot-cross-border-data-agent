"""RAG 系统性能基准评估脚本。

覆盖三大维度：
  1. 检索延迟：各步骤耗时拆解（改写/向量检索/混合检索/去重/重排/上下文扩展）+ 端到端 P50/P95/P99
  2. 检索准确率：命中率 / 平均距离 / Top-K 覆盖率
  3. 端到端 QA 质量：回答忠实度 / 引用准确性 / 拒答率

用法：
  # 仅跑延迟 & 准确率（不调 LLM，低成本）
  python eval_benchmark.py

  # 加上 QA 质量评估（需要调 LLM，会产生 API 费用）
  python eval_benchmark.py --with-qa

  # 指定运行轮次（取平均值，减少波动）
  python eval_benchmark.py --rounds 5

  # 只跑单个维度
  python eval_benchmark.py --latency-only
  python eval_benchmark.py --accuracy-only

输出：
  终端表格 + logs/benchmark_report.json
"""

import argparse
import json
import os
import sys
import time
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

# 允许从任意 CWD 运行：把项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(_PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 只读 RAG 引擎门面（Step 029：替代已删除的 v1 service.KnowledgeService）
# ═══════════════════════════════════════════════════════════════════════════════
# 仅为本 benchmark 提供 retriever / vector_store / retrieve / ask 四个入口，直接装配
# retrieval/ 引擎模块（与 v2 检索同源：build_reranker + Retriever + QAChain）。


@dataclass
class _RetrievalResult:
    chunks: list[dict[str, Any]]
    query_used: list[str]


class BenchmarkRagService:
    """benchmark 专用只读 RAG 门面（无 Web 依赖，纯引擎装配）。"""

    def __init__(self) -> None:
        from config import settings
        from retrieval.generation.qa_chain import QAChain
        from retrieval.search.embedder import Embedder
        from retrieval.search.query_rewriter import QueryRewriter
        from retrieval.search.reranker import build_reranker
        from retrieval.search.retriever import Retriever
        from retrieval.search.vector_store import VectorStore

        self._settings = settings
        self.embedder = Embedder()
        self.vector_store = VectorStore()
        self.retriever = Retriever(
            embedder=self.embedder,
            vector_store=self.vector_store,
            query_rewriter=QueryRewriter(),
            reranker=build_reranker(),
        )
        self.qa_chain = QAChain()

    def retrieve(
        self, query: str, top_k: int = 5, category: str | None = None
    ) -> _RetrievalResult:
        top_k = max(1, min(top_k, self._settings.max_top_k))
        queries = self.retriever.query_rewriter.rewrite(query)
        if query not in queries:
            queries.insert(0, query)
        results = self.retriever.retrieve(query, top_k=top_k)
        if category:
            results = [
                r
                for r in results
                if r.get("metadata", {}).get("category", "") == category
            ]
        return _RetrievalResult(chunks=results, query_used=queries)

    def ask(self, question: str, top_k: int = 5, category: str | None = None):
        retrieval = self.retrieve(question, top_k=top_k, category=category)
        return self.qa_chain.generate(question, retrieval.chunks)




# ═══════════════════════════════════════════════════════════════════════════════
# 评估数据集
# ═══════════════════════════════════════════════════════════════════════════════

# 检索评估用例：question = 问题，must_contain = 正确答案中必须包含的关键词列表
# expected_source = 期望命中的来源文档（可选，用于 source 级准确率）
RETRIEVAL_TEST_CASES = [
    {
        "question": "数据出境安全评估的适用条件是什么？",
        "must_contain": ["关键信息基础设施", "重要数据"],
        "expected_source": None,
    },
    {
        "question": "标准合同备案需要哪些材料？",
        "must_contain": ["个人信息保护影响评估"],
        "expected_source": None,
    },
    {
        "question": "个人信息出境的三条合法路径",
        "must_contain": ["安全评估", "标准合同", "保护认证"],
        "expected_source": None,
    },
    {
        "question": "处理多少人个人信息需要申报安全评估？",
        "must_contain": ["100万", "万人"],
        "expected_source": None,
    },
    {
        "question": "数据出境安全评估的有效期是多长？",
        "must_contain": ["2年", "两年"],
        "expected_source": None,
    },
]

# QA 质量评估用例：question + reference_answer（用于对比回答质量）
QA_TEST_CASES = [
    {
        "question": "数据出境安全评估的适用条件是什么？",
        "reference_keywords": ["关键信息基础设施", "重要数据", "100万"],
    },
    {
        "question": "标准合同与安全评估的区别是什么？",
        "reference_keywords": ["标准合同", "安全评估", "备案"],
    },
    {
        "question": "什么是量子计算？",  # 知识库无关问题，应拒答
        "reference_keywords": [],
        "should_refuse": True,
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# 检索延迟评估
# ═══════════════════════════════════════════════════════════════════════════════

class LatencyBenchmark:
    """检索管线各步骤延迟评估。"""

    def __init__(self, service):
        self.service = service
        self.retriever = service.retriever

    def run(self, questions: List[str], rounds: int = 3, top_k: int = 5) -> Dict[str, Any]:
        """跑延迟基准测试，返回各步骤和端到端耗时统计。"""
        all_latencies = []

        for round_num in range(1, rounds + 1):
            print(f"  第 {round_num}/{rounds} 轮...")
            for q in questions:
                latency = self._measure_single(q, top_k)
                all_latencies.append(latency)

        return self._aggregate(all_latencies)

    def _measure_single(self, query: str, top_k: int) -> Dict[str, float]:
        """测量单次查询的各步骤耗时（毫秒）。"""
        timings = {}

        # 1. 查询改写
        t0 = time.perf_counter()
        queries = self.retriever.query_rewriter.rewrite(query)
        if query not in queries:
            queries.insert(0, query)
        timings["rewrite"] = (time.perf_counter() - t0) * 1000

        # 2. 多查询向量检索
        t0 = time.perf_counter()
        all_results = []
        seen_ids = set()
        for q in queries:
            q_embedding = self.retriever.embedder.embed_query(q)
            results = self.retriever.vector_store.query(q_embedding, top_k=top_k)
            for r in results:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    all_results.append(r)
        timings["vector_search"] = (time.perf_counter() - t0) * 1000

        # 3. 混合检索（关键词）
        t0 = time.perf_counter()
        keywords = self.retriever._extract_keywords(query)
        if keywords:
            kw_results = self.retriever.vector_store.keyword_search(keywords, top_k=top_k)
            for r in kw_results:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    all_results.append(r)
        timings["keyword_search"] = (time.perf_counter() - t0) * 1000

        # 4. 排序
        t0 = time.perf_counter()
        all_results.sort(key=lambda x: x.get("distance", 1.0))
        timings["sort"] = (time.perf_counter() - t0) * 1000

        # 5. 文本去重
        t0 = time.perf_counter()
        all_results = self.retriever._deduplicate(all_results)
        timings["dedup"] = (time.perf_counter() - t0) * 1000

        # 6. 重排序
        t0 = time.perf_counter()
        all_results = self.retriever.reranker.rerank(query, all_results)
        timings["rerank"] = (time.perf_counter() - t0) * 1000

        # 7. 截取
        all_results = all_results[:top_k]

        # 8. 上下文扩展
        from config import settings as _settings
        t0 = time.perf_counter()
        if _settings.context_window_size > 0:
            all_results = self.retriever._expand_context(all_results)
        timings["ctx_expand"] = (time.perf_counter() - t0) * 1000

        # 端到端
        timings["total"] = sum(timings.values())

        return timings

    @staticmethod
    def _aggregate(latencies: List[Dict[str, float]]) -> Dict[str, Any]:
        """汇总多次测量结果，计算 P50/P95/P99/avg。"""
        if not latencies:
            return {}

        keys = latencies[0].keys()
        result = {}

        for key in keys:
            values = sorted([l[key] for l in latencies])
            n = len(values)
            result[key] = {
                "avg": round(statistics.mean(values), 1),
                "p50": round(values[n // 2], 1),
                "p95": round(values[int(n * 0.95)], 1) if n >= 5 else round(values[-1], 1),
                "p99": round(values[int(n * 0.99)], 1) if n >= 10 else round(values[-1], 1),
                "min": round(min(values), 1),
                "max": round(max(values), 1),
            }

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# 检索准确率评估
# ═══════════════════════════════════════════════════════════════════════════════

class AccuracyBenchmark:
    """检索准确率评估：命中率、Top-K 覆盖率、平均距离。"""

    def __init__(self, service):
        self.service = service

    def run(self, test_cases: List[Dict], top_k: int = 5) -> Dict[str, Any]:
        """跑准确率评估。"""
        results = []

        for case in test_cases:
            result = self._evaluate_single(case, top_k)
            results.append(result)

        return self._aggregate(results)

    def _evaluate_single(self, case: Dict, top_k: int) -> Dict[str, Any]:
        """单条用例评估。"""
        question = case["question"]
        must_contain = case.get("must_contain", [])
        expected_source = case.get("expected_source")

        retrieval = self.service.retrieve(question, top_k=top_k)
        chunks = retrieval.chunks
        query_used = retrieval.query_used

        # 关键词命中率：检查 must_contain 中的每个关键词是否在某个 chunk 中出现
        all_text = " ".join(c.get("text", "") + c.get("original_text", "") for c in chunks)
        keyword_hits = {kw: kw in all_text for kw in must_contain}
        keyword_hit_rate = (sum(keyword_hits.values()) / len(must_contain)) if must_contain else 1.0

        # 来源命中
        source_hit = None
        if expected_source:
            source_hit = any(
                c.get("metadata", {}).get("source_name") == expected_source
                for c in chunks
            )

        # 距离统计
        distances = [c.get("distance", 1.0) for c in chunks]
        avg_distance = statistics.mean(distances) if distances else 1.0
        min_distance = min(distances) if distances else 1.0

        # Top-1 / Top-3 / Top-5 命中（关键词在第几个 chunk 首次出现）
        first_hit_rank = None
        for i, chunk in enumerate(chunks):
            text = chunk.get("text", "") + chunk.get("original_text", "")
            if any(kw in text for kw in must_contain):
                first_hit_rank = i + 1
                break

        return {
            "question": question,
            "num_results": len(chunks),
            "num_queries_used": len(query_used),
            "keyword_hits": keyword_hits,
            "keyword_hit_rate": keyword_hit_rate,
            "source_hit": source_hit,
            "avg_distance": round(avg_distance, 4),
            "min_distance": round(min_distance, 4),
            "first_hit_rank": first_hit_rank,
            "hit_at_1": first_hit_rank == 1 if first_hit_rank else False,
            "hit_at_3": first_hit_rank is not None and first_hit_rank <= 3,
            "hit_at_5": first_hit_rank is not None and first_hit_rank <= 5,
        }

    @staticmethod
    def _aggregate(results: List[Dict]) -> Dict[str, Any]:
        """汇总准确率指标。"""
        n = len(results)
        if not n:
            return {}

        return {
            "total_cases": n,
            "keyword_hit_rate": round(
                statistics.mean(r["keyword_hit_rate"] for r in results), 3
            ),
            "hit_at_1": round(sum(r["hit_at_1"] for r in results) / n, 3),
            "hit_at_3": round(sum(r["hit_at_3"] for r in results) / n, 3),
            "hit_at_5": round(sum(r["hit_at_5"] for r in results) / n, 3),
            "avg_distance": round(
                statistics.mean(r["avg_distance"] for r in results), 4
            ),
            "avg_min_distance": round(
                statistics.mean(r["min_distance"] for r in results), 4
            ),
            "avg_queries_used": round(
                statistics.mean(r["num_queries_used"] for r in results), 1
            ),
            "details": results,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# QA 质量评估
# ═══════════════════════════════════════════════════════════════════════════════

class QABenchmark:
    """端到端 QA 质量评估：回答关键词覆盖率、引用数量、拒答准确性。"""

    def __init__(self, service):
        self.service = service

    def run(self, test_cases: List[Dict], top_k: int = 5) -> Dict[str, Any]:
        """跑 QA 质量评估（会调 LLM API）。"""
        results = []

        for case in test_cases:
            print(f"    Q: {case['question'][:50]}...")
            result = self._evaluate_single(case, top_k)
            results.append(result)

        return self._aggregate(results)

    def _evaluate_single(self, case: Dict, top_k: int) -> Dict[str, Any]:
        """单条 QA 评估。"""
        question = case["question"]
        ref_keywords = case.get("reference_keywords", [])
        should_refuse = case.get("should_refuse", False)

        t0 = time.perf_counter()
        try:
            qa_result = self.service.ask(question, top_k=top_k)
            latency_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            return {
                "question": question,
                "error": str(e),
                "latency_ms": (time.perf_counter() - t0) * 1000,
            }

        answer = qa_result.answer
        citations = qa_result.citations
        has_context = qa_result.has_enough_context

        # 拒答检测
        refuse_indicators = ["无法回答", "暂时无法", "没有找到", "不在知识库", "⚠️"]
        is_refused = any(ind in answer for ind in refuse_indicators) or not has_context

        # 关键词覆盖率（Faithfulness 简易代理）
        keyword_coverage = {}
        for kw in ref_keywords:
            keyword_coverage[kw] = kw in answer
        coverage_rate = (
            sum(keyword_coverage.values()) / len(ref_keywords)
            if ref_keywords else (0.0 if not should_refuse else 1.0)
        )

        # 引用质量
        num_citations = len(citations)
        has_citations = num_citations > 0

        # 拒答准确性
        refuse_correct = None
        if should_refuse:
            refuse_correct = is_refused  # 应该拒答且确实拒答了
        elif not should_refuse:
            refuse_correct = not is_refused  # 不该拒答且确实没拒答

        return {
            "question": question,
            "answer_length": len(answer),
            "keyword_coverage": keyword_coverage,
            "coverage_rate": round(coverage_rate, 3),
            "num_citations": num_citations,
            "has_citations": has_citations,
            "has_enough_context": has_context,
            "is_refused": is_refused,
            "should_refuse": should_refuse,
            "refuse_correct": refuse_correct,
            "latency_ms": round(latency_ms, 1),
        }

    @staticmethod
    def _aggregate(results: List[Dict]) -> Dict[str, Any]:
        """汇总 QA 质量指标。"""
        valid = [r for r in results if "error" not in r]
        n = len(valid)
        if not n:
            return {"total_cases": len(results), "errors": len(results) - len(valid)}

        return {
            "total_cases": len(results),
            "errors": len(results) - len(valid),
            "avg_coverage_rate": round(
                statistics.mean(r["coverage_rate"] for r in valid), 3
            ),
            "avg_citations": round(
                statistics.mean(r["num_citations"] for r in valid), 1
            ),
            "citation_rate": round(
                sum(r["has_citations"] for r in valid) / n, 3
            ),
            "refuse_accuracy": round(
                sum(r["refuse_correct"] for r in valid if r["refuse_correct"] is not None)
                / sum(1 for r in valid if r["refuse_correct"] is not None), 3
            ) if any(r["refuse_correct"] is not None for r in valid) else None,
            "avg_answer_length": round(
                statistics.mean(r["answer_length"] for r in valid), 0
            ),
            "avg_latency_ms": round(
                statistics.mean(r["latency_ms"] for r in valid), 1
            ),
            "p95_latency_ms": round(
                sorted(r["latency_ms"] for r in valid)[int(n * 0.95)] if n >= 5
                else max(r["latency_ms"] for r in valid), 1
            ),
            "details": results,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════════════════════════════════════════════

def print_latency_report(data: Dict[str, Any]):
    """打印延迟表格。"""
    print(f"\n{'='*70}")
    print("检索延迟基准 (单位: ms)")
    print(f"{'='*70}")
    print(f"{'步骤':<18} {'avg':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'min':>8} {'max':>8}")
    print(f"{'-'*70}")

    step_labels = {
        "rewrite": "① 查询改写",
        "vector_search": "② 向量检索",
        "keyword_search": "③ 关键词检索",
        "sort": "④ 排序",
        "dedup": "⑤ 文本去重",
        "rerank": "⑥ 重排序",
        "ctx_expand": "⑦ 上下文扩展",
        "total": "━━ 端到端 ━━",
    }

    for key in ["rewrite", "vector_search", "keyword_search", "sort",
                 "dedup", "rerank", "ctx_expand", "total"]:
        if key not in data:
            continue
        d = data[key]
        label = step_labels.get(key, key)
        if key == "total":
            print(f"{'-'*70}")
        print(f"{label:<18} {d['avg']:>8.1f} {d['p50']:>8.1f} "
              f"{d['p95']:>8.1f} {d['p99']:>8.1f} {d['min']:>8.1f} {d['max']:>8.1f}")


def print_accuracy_report(data: Dict[str, Any]):
    """打印准确率表格。"""
    print(f"\n{'='*70}")
    print("检索准确率评估")
    print(f"{'='*70}")
    print(f"  用例数:           {data['total_cases']}")
    print(f"  关键词命中率:     {data['keyword_hit_rate']:.1%}")
    print(f"  Hit@1:            {data['hit_at_1']:.1%}")
    print(f"  Hit@3:            {data['hit_at_3']:.1%}")
    print(f"  Hit@5:            {data['hit_at_5']:.1%}")
    print(f"  平均最近距离:     {data['avg_min_distance']:.4f}")
    print(f"  平均距离:         {data['avg_distance']:.4f}")
    print(f"  平均查询数:       {data['avg_queries_used']:.1f}")

    print(f"\n{'逐条详情':}")
    print(f"{'问题':<40} {'命中率':>6} {'Hit@':>5} {'最近距离':>8}")
    print(f"{'-'*65}")
    for d in data.get("details", []):
        q = d["question"][:38]
        hit = d.get("first_hit_rank")
        hit_str = str(hit) if hit else "×"
        print(f"{q:<40} {d['keyword_hit_rate']:>5.0%} {hit_str:>5} {d['min_distance']:>8.4f}")


def print_qa_report(data: Dict[str, Any]):
    """打印 QA 质量表格。"""
    print(f"\n{'='*70}")
    print("QA 端到端质量评估")
    print(f"{'='*70}")
    print(f"  用例数:           {data['total_cases']} (错误: {data['errors']})")
    print(f"  关键词覆盖率:     {data['avg_coverage_rate']:.1%}")
    print(f"  引用率:           {data['citation_rate']:.1%}")
    print(f"  平均引用数:       {data['avg_citations']:.1f}")
    if data.get("refuse_accuracy") is not None:
        print(f"  拒答准确率:       {data['refuse_accuracy']:.1%}")
    print(f"  平均回答长度:     {data['avg_answer_length']:.0f} 字")
    print(f"  平均延迟:         {data['avg_latency_ms']:.0f} ms")
    print(f"  P95 延迟:         {data['p95_latency_ms']:.0f} ms")

    print(f"\n{'逐条详情':}")
    print(f"{'问题':<35} {'覆盖':>5} {'引用':>4} {'拒答':>4} {'延迟ms':>8}")
    print(f"{'-'*60}")
    for d in data.get("details", []):
        if "error" in d:
            print(f"{d['question'][:33]:<35} {'ERR':>5}")
            continue
        q = d["question"][:33]
        refuse = "✓" if d["is_refused"] else "×"
        print(f"{q:<35} {d['coverage_rate']:>4.0%} {d['num_citations']:>4} "
              f"{refuse:>4} {d['latency_ms']:>8.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="RAG 系统性能基准评估")
    parser.add_argument("--rounds", type=int, default=3,
                        help="延迟测试轮次（取平均，默认 3）")
    parser.add_argument("--top-k", type=int, default=5,
                        help="检索 top_k（默认 5）")
    parser.add_argument("--with-qa", action="store_true",
                        help="同时评估 QA 质量（需调 LLM API）")
    parser.add_argument("--latency-only", action="store_true",
                        help="仅评估延迟")
    parser.add_argument("--accuracy-only", action="store_true",
                        help="仅评估准确率")
    parser.add_argument("--output", type=str, default="./logs/benchmark_report.json",
                        help="结果输出路径")
    args = parser.parse_args()

    run_latency = not args.accuracy_only
    run_accuracy = not args.latency_only
    run_qa = args.with_qa and not args.latency_only and not args.accuracy_only

    print("=" * 70)
    print("RAG 系统性能基准评估")
    print("=" * 70)
    print(f"  时间:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  轮次:       {args.rounds}")
    print(f"  Top-K:      {args.top_k}")
    print(f"  评估范围:   {'延迟' if run_latency else ''}"
          f"{'+ 准确率' if run_accuracy else ''}"
          f"{'+ QA' if run_qa else ''}")

    # 初始化 RAG 引擎门面
    print("\n正在初始化 RAG 引擎...")
    service = BenchmarkRagService()

    chunk_count = service.vector_store.get_total_count()
    print(f"知识库当前 chunk 数: {chunk_count}")
    if chunk_count == 0:
        print("⚠️  知识库为空，请先导入文档再跑评估。")
        return

    from config import settings
    print(f"配置: reranker={'CrossEncoder' if settings.enable_reranker else '距离阈值'}, "
          f"query_rewrite={settings.enable_query_rewrite}, "
          f"context_window={settings.context_window_size}")

    report = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "rounds": args.rounds,
            "top_k": args.top_k,
            "chunk_count": chunk_count,
            "enable_reranker": settings.enable_reranker,
            "enable_query_rewrite": settings.enable_query_rewrite,
            "context_window_size": settings.context_window_size,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
        },
    }

    # ── 延迟评估 ──────────────────────────────────────────────────────────
    if run_latency:
        print(f"\n▶ 检索延迟评估 ({args.rounds} 轮 × {len(RETRIEVAL_TEST_CASES)} 条)")
        questions = [c["question"] for c in RETRIEVAL_TEST_CASES]
        latency_bench = LatencyBenchmark(service)
        latency_data = latency_bench.run(questions, rounds=args.rounds, top_k=args.top_k)
        report["latency"] = latency_data
        print_latency_report(latency_data)

    # ── 准确率评估 ────────────────────────────────────────────────────────
    if run_accuracy:
        print(f"\n▶ 检索准确率评估 ({len(RETRIEVAL_TEST_CASES)} 条)")
        accuracy_bench = AccuracyBenchmark(service)
        accuracy_data = accuracy_bench.run(RETRIEVAL_TEST_CASES, top_k=args.top_k)
        report["accuracy"] = accuracy_data
        print_accuracy_report(accuracy_data)

    # ── QA 质量评估 ───────────────────────────────────────────────────────
    if run_qa:
        print(f"\n▶ QA 端到端质量评估 ({len(QA_TEST_CASES)} 条)")
        qa_bench = QABenchmark(service)
        qa_data = qa_bench.run(QA_TEST_CASES, top_k=args.top_k)
        report["qa"] = qa_data
        print_qa_report(qa_data)

    # ── 保存报告 ──────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {args.output}")


if __name__ == "__main__":
    main()
