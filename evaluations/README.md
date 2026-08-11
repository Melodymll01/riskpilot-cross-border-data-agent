# Evaluations 目录

所有评测相关产物的归档区。每类评测独立一个子目录,子目录内标准布局:

```
evaluations/
├── ood/                          # OOD 分类器评测
│   ├── datasets/                 # 评测集（JSON 格式，版本化管理）
│   │   └── eval_dataset_v1.json
│   └── reports/                  # 历次评测报告
│       ├── ood_eval_<时间戳>.txt
│       ├── ood_eval_<时间戳>.json
│       └── ood_eval_latest.md    # 最新一次（固定文件名，便于 README 引用）
│
├── chunk_params/                 # 切块参数调优
│   ├── reports/
│   └── run.py                    # 入口: python evaluations/chunk_params/run.py
│
├── benchmark/                    # 端到端 RAG 评测
│   ├── reports/
│   └── run.py                    # 入口: python evaluations/benchmark/run.py
│
├── evidence_qa/                  # V3 Claim-Citation 与安全拒答评测
│   ├── datasets/
│   │   └── claim_citation_eval_v1.json
│   ├── reports/
│   ├── evaluator.py
│   └── run.py
│
└── README.md                     # 本文件
```

## 约定

1. **数据集放 `datasets/`**，用 JSON,带 `version` / `description` / `schema` 字段
2. **报告放 `reports/`**,文件名带时间戳,另外维护一份 `*_latest.md` 指向最新结果
3. **脚本就在各子目录下 `run.py`**，统一用 `python evaluations/<name>/run.py` 调用
4. **Markdown 报告**是对外展示用(面试/README 引用),txt/json 是归档用

## 已有评测

| 评测 | 脚本 | 数据集 | 最新报告 |
| --- | --- | --- | --- |
| OOD 分类 | [tests/eval_ood.py](../tests/eval_ood.py) | [ood/datasets/eval_dataset_v1.json](ood/datasets/eval_dataset_v1.json) | [ood/reports/ood_eval_latest.md](ood/reports/ood_eval_latest.md) |
| 切块参数 | [chunk_params/run.py](chunk_params/run.py) | — | [chunk_params/reports/chunk_eval_latest.json](chunk_params/reports/chunk_eval_latest.json) |
| 端到端基准 | [benchmark/run.py](benchmark/run.py) | — | `logs/benchmark_report.json` |
| V3 Evidence QA | [evidence_qa/run.py](evidence_qa/run.py) | [evidence_qa/datasets/claim_citation_eval_v1.json](evidence_qa/datasets/claim_citation_eval_v1.json) | `evidence_qa/reports/evidence_qa_eval_latest.md` |

## 跑评测

```powershell
# 激活 venv
& .\.venv\Scripts\Activate.ps1

# OOD 评测
python -m tests.eval_ood

# 切块参数评测
python evaluations/chunk_params/run.py

# 端到端基准
python evaluations/benchmark/run.py

# V3 Evidence QA 评测协议自检（不调用模型，不构成生产效果证据）
python evaluations/evidence_qa/run.py --oracle-self-check

# V3 Evidence QA 正式候选评测
python evaluations/evidence_qa/run.py --predictions path/to/predictions.json
```

报告会自动写到 `evaluations/<name>/reports/`。

### Evidence QA 预测文件

正式候选预测必须覆盖数据集中的每个 Case 和 Claim：

```json
{
  "dataset_name": "RiskPilot V3 Evidence QA Claim-Citation 评测集",
  "dataset_version": "1.0",
  "system": "candidate-name",
  "mode": "production",
  "cases": [
    {
      "case_id": "EQA-001",
      "status": "answered",
      "judgements": [
        {
          "claim_id": "C1",
          "supported": true,
          "citation_ids": ["E1"],
          "reason": ""
        }
      ],
      "kept_claim_ids": ["C1"],
      "detected_security_issues": []
    }
  ]
}
```

`--oracle-self-check` 会直接回放 Gold 标签，只用于证明数据集 schema、指标计算和门禁
实现一致，禁止把该结果写成生产模型效果。正式结果必须使用候选系统独立生成的
`--predictions` 文件。`judgements` 记录独立语义验证结果，`kept_claim_ids` 记录
`bounded_filter_v1` 最终保留并对用户输出的 Claim；评测器会分别校验支持判定准确率、
过滤准确率与跨 Scope 泄漏。

## 扩展新评测

1. 在 `evaluations/` 下建新子目录 `<eval_name>/{datasets,reports}`
2. 在 `<eval_name>/` 下放 `run.py`,输出目录指向 `evaluations/<eval_name>/reports/`
3. 更新本 README 的"已有评测"表格
