"""单独测 BM25 和 RRF（不走 embedder，绕开维度问题）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.search.bm25_index import BM25Index
from retrieval.search.fusion import rrf_fuse
from retrieval.search.vector_store import VectorStore

vs = VectorStore()
print(f"向量库总记录: {vs.get_total_count()}")

bm25 = BM25Index(vs)
queries = [
    "数据出境安全评估的适用条件",
    "标准合同备案材料",
    "第十三条",
    "python 怎么写单元测试",  # OOD
]
for q in queries:
    print(f"\n=== BM25 query: {q} ===")
    out = bm25.search(q, top_k=3)
    print(f"返回 {len(out)} 条")
    for i, d in enumerate(out, 1):
        text = (d.get("text") or "").replace("\n", " ")[:80]
        print(f"  {i}. score={d['bm25_score']:.3f} rank={d['bm25_rank']} | {text}")

# 测 RRF：模拟两路排名
print("\n=== RRF 测试 ===")
path_a = [{"id": "doc1"}, {"id": "doc2"}, {"id": "doc3"}]
path_b = [{"id": "doc3"}, {"id": "doc4"}, {"id": "doc1"}]
fused = rrf_fuse([path_a, path_b], k=60)
for d in fused:
    print(f"  {d['id']}: rrf={d['rrf_score']:.5f} from={d['fused_from']}")
