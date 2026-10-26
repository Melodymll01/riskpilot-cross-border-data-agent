# 当前评测

保留两组直接服务对话链路的评测：

- `evidence_qa/`：13 个法规/用户文档 Claim-Citation 样本，覆盖结构引用、独立语义支持、来源漂移、授权材料范围和有限过滤。数据版本为 2.0。
- `chunk_params/`：检索切块参数实验工具，需要自行提供真实材料及模型服务；不作为本阶段运行通过证据。

运行离线协议自检：

```bash
python -m evaluations.evidence_qa.run --oracle-self-check --no-write
```

`oracle_self_check` 使用 gold 标签，必须标记 `production_evidence=false`。生产/真实验证器预测与 oracle 分开报告；真实结果需要显式输入预测文件或用 `run_verifier --live` 生成。这里的 `case_id` 表示评测样本编号，不是已删除的企业案件模型。

企业 Assessment 轨迹、Visual Evidence、长期记忆评测及其旧报告已删除。当前数据集只用于有限协议回归，不可替代下一阶段的真实对话效果评测。
