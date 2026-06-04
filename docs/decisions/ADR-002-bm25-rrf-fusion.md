# ADR-002: 检索采用 BM25 + 向量 + RRF 融合 + Reranker

- 状态: accepted
- 日期: 2026-06-04

## 背景

法规检索场景对术语精确匹配（如"第三十九条"、"个人信息处理者"）非常敏感，单一向量检索容易漏召；同时纯关键词检索缺乏语义泛化能力。

## 决策

采用三段式混合检索：

1. **召回**：BM25（rank-bm25 + jieba 中文分词）与向量（Chroma + embedding-3）并行 top-K
2. **融合**：Reciprocal Rank Fusion（RRF）合并两路结果
3. **精排**：BAAI/bge-reranker-base 对融合 top-N 重排

## 后果

**正面**：
- 关键词/法条号精确召回有保证
- 语义改写（"我可以出境吗" ↔ "数据出境的合规要求"）能命中
- RRF 不需要分数归一化，鲁棒性强
- Reranker 提供查询-段落细粒度交互，质量提升明显

**负面**：
- 增加一次模型前向（Reranker），延迟约 +100~300ms
- 多了 BM25 索引维护（增量更新需小心同步）

## 备选方案

- **纯向量检索**：法条号召回差，否决
- **纯 BM25**：语义弱，否决
- **ColBERT**：late interaction，复杂度高，留作 v2 实验

## 关联

- `retrieval/search/fusion.py`、`retrieval/search/reranker.py`
- 评估：`evaluations/benchmark/run.py`
