# 当前评测

评测实现随 Python Agent 服务迁入 `services/agent-runtime/evaluations`，文档集中保存在此处。当前保留两组直接服务对话链路的评测：

- [`evidence_qa/`](../../services/agent-runtime/evaluations/evidence_qa/)：13 个法规/用户文档 Claim-Citation 样本，覆盖结构引用、独立语义支持、来源漂移、授权材料范围和有限过滤。数据版本为 2.0。
- [`chunk_params/`](../../services/agent-runtime/evaluations/chunk_params/)：检索切块参数实验工具，需要自行提供真实材料及模型服务，不作为运行通过证据。

从仓库根目录运行离线协议自检：

```bash
make evidence-eval PY=.venv/bin/python
```

`oracle_self_check` 使用 gold 标签，必须标记 `production_evidence=false`。生产或真实验证器预测与 oracle 分开报告；真实结果需要显式输入预测文件或使用 `run_verifier --live` 生成。这里的 `case_id` 表示评测样本编号，不是已删除的企业案件模型。

当前数据集只用于有限协议回归，不能替代真实对话效果评测。
