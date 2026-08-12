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
│   ├── run.py
│   └── run_verifier.py            # 显式 --live 实测生产 independent_llm_v1
│
├── memory_extraction/            # AI 长期记忆来源/接地/隐私协议门禁
│   ├── datasets/
│   │   └── memory_extraction_eval_v1.json
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
| AI 记忆提取协议 | [memory_extraction/run.py](memory_extraction/run.py) | [memory_extraction/datasets/memory_extraction_eval_v1.json](memory_extraction/datasets/memory_extraction_eval_v1.json) | 标准输出 |

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

# 实测当前生产 independent_llm_v1（会调用真实模型并产生费用）
python evaluations/evidence_qa/run_verifier.py --live

# AI 长期记忆确定性协议自检（零模型调用、零密钥）
python evaluations/memory_extraction/run.py
```

报告会自动写到 `evaluations/<name>/reports/`。

### AI 长期记忆提取协议

`memory_extraction/run.py` 覆盖用户来源过滤、逐字 quote 接地、助手污染、伪造引用、
提示注入、API Key、个人标识符、高敏属性和一次性请求。该入口直接调用生产确定性
校验器，验证 fail-closed 门禁，不调用模型，也不构成生产模型抽取准确率证据。

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

### 生产验证器实测

`run_verifier.py --live` 固定使用评测集中的 Claim 与 Citation，只调用当前生产
`StructuredClaimSupportVerifier + OpenAIChatAdapter`。模型请求中不会包含：

- `gold.claim_support`；
- `expected_status`；
- `difficulty` / `category`；
- `security_issues`。

运行后会同时保留：

- `evidence_qa_predictions_<timestamp>.json`：逐 Case 原始 judgement、最终保留 Claim
  和验证器错误；
- `evidence_qa_eval_<timestamp>.json`：指标与门禁；
- `evidence_qa_eval_latest.md`：可展示报告。

该模式标记为 `production_verifier`，只把下列指标作为硬门禁：

- `supported_claim_recall`；
- `unsupported_claim_false_accept_rate`；
- `claim_filter_accuracy`；
- `verifier_error_count`。

生成状态、引用漂移和跨 Scope 隔离不属于本次模型调用的职责，报告仍展示这些指标，
但门禁标记为 `N/A`。完整 Evidence QA 端到端生产实测仍应通过独立 predictions 文件
使用 `run.py --predictions ...` 评分。

## 扩展新评测

1. 在 `evaluations/` 下建新子目录 `<eval_name>/{datasets,reports}`
2. 在 `<eval_name>/` 下放 `run.py`,输出目录指向 `evaluations/<eval_name>/reports/`
3. 更新本 README 的"已有评测"表格
