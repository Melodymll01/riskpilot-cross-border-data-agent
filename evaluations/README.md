# Evaluations 目录

所有评测相关产物的归档区。每类评测独立一个子目录,子目录内标准布局:

```
evaluations/
├── chunk_params/                 # 切块参数调优
│   ├── reports/
│   └── run.py                    # 入口: python evaluations/chunk_params/run.py
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
├── memory_recall/                # AI 长期记忆混合排序与安全过滤门禁
│   ├── datasets/
│   │   └── memory_recall_eval_v1.json
│   ├── evaluator.py
│   └── run.py
├── visual_retrieval/             # Chinese-CLIP 小规模图片召回
│   ├── generate_dataset.py       # 生成 12 张合成图片
│   ├── evaluator.py
│   └── run.py                    # --live 才下载并运行模型
├── agent_runs/                   # Case Assessment Agent 完整轨迹评测
│   ├── datasets/
│   │   └── agent_runs_eval_v1.json
│   ├── reports/
│   │   ├── latest.json
│   │   └── latest.md
│   ├── models.py
│   ├── executor.py
│   ├── evaluator.py
│   └── run.py                    # 默认 offline；--live 才调用模型
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
| 切块参数 | [chunk_params/run.py](chunk_params/run.py) | — | [chunk_params/reports/chunk_eval_latest.json](chunk_params/reports/chunk_eval_latest.json) |
| V3 Evidence QA | [evidence_qa/run.py](evidence_qa/run.py) | [evidence_qa/datasets/claim_citation_eval_v1.json](evidence_qa/datasets/claim_citation_eval_v1.json) | `evidence_qa/reports/evidence_qa_eval_latest.md` |
| AI 记忆提取协议 | [memory_extraction/run.py](memory_extraction/run.py) | [memory_extraction/datasets/memory_extraction_eval_v1.json](memory_extraction/datasets/memory_extraction_eval_v1.json) | 标准输出 |
| AI 记忆召回协议 | [memory_recall/run.py](memory_recall/run.py) | [memory_recall/datasets/memory_recall_eval_v1.json](memory_recall/datasets/memory_recall_eval_v1.json) | 标准输出 |
| 图片召回 | [visual_retrieval/run.py](visual_retrieval/run.py) | 运行生成 12 张合成图片 | 标准输出 |
| Agent Run | [agent_runs/run.py](agent_runs/run.py) | [agent_runs/datasets/agent_runs_eval_v1.json](agent_runs/datasets/agent_runs_eval_v1.json) | [agent_runs/reports/latest.md](agent_runs/reports/latest.md) |

## 跑评测

```powershell
# 激活 venv
& .\.venv\Scripts\Activate.ps1

# 切块参数评测
python evaluations/chunk_params/run.py

# V3 Evidence QA 评测协议自检（不调用模型，不构成生产效果证据）
python evaluations/evidence_qa/run.py --oracle-self-check

# V3 Evidence QA 正式候选评测
python evaluations/evidence_qa/run.py --predictions path/to/predictions.json

# 实测当前生产 independent_llm_v1（会调用真实模型并产生费用）
python evaluations/evidence_qa/run_verifier.py --live

# AI 长期记忆确定性协议自检（零模型调用、零密钥）
python evaluations/memory_extraction/run.py

# AI 长期记忆召回排序自检（零模型调用、零密钥）
python evaluations/memory_recall/run.py

# 生成小规模合成图片，并实测 Chinese-CLIP（首次会下载模型）
python evaluations/visual_retrieval/generate_dataset.py
python evaluations/visual_retrieval/run.py --live

# Case Assessment Agent 39 案件离线轨迹评测
python -m evaluations.agent_runs.run

# 显式真实模型 Planner 评测（会产生费用）
python -m evaluations.agent_runs.run --live
```

报告会自动写到 `evaluations/<name>/reports/`。

### Case Assessment Agent 轨迹评测

`agent_runs` 使用 39 个合成案件覆盖完整材料、材料/事实缺失、冲突、引用漂移、规则版本、
工具失败、非法 Schema、Prompt Injection、跨 Workspace、Reviewer 拒绝、Worker retry 和
checkpoint 恢复。Offline runner 真实执行 16 节点 LangGraph、interrupt/resume、SQLite
checkpoint 重建以及 Typed Tool Registry/Pydantic/Tool Policy，但不调用模型或网络。

数据集将 `scenario` 和 `gold` 分离；executor 只接收 `expand_scenario()`，测试会篡改 Gold
并验证 prediction 不变。报告记录 dataset、model、prompt、tool schema 和 evaluator 版本。
`--live` 或 `RUN_LIVE=1` 才使用生产 LangChain Planner；普通 CI 只跑 offline，并在任何安全
门禁失败时返回非零退出码。

### AI 长期记忆提取协议

`memory_extraction/run.py` 覆盖用户来源过滤、逐字 quote 接地、助手污染、伪造引用、
提示注入、API Key、个人标识符、高敏属性和一次性请求。该入口直接调用生产确定性
校验器，验证 fail-closed 门禁，不调用模型，也不构成生产模型抽取准确率证据。

### AI 长期记忆召回协议

`memory_recall/run.py` 直接调用生产 `hybrid_v1` 排序策略，覆盖语义相关性、可信事实
重排、低相关拒绝、TTL、冲突遗忘和 owner 隔离。数据集给定语义候选分数，因此该入口
证明的是确定性排序与过滤门禁，不构成真实 embedding 的端到端召回准确率证据。

### Chinese-CLIP 图片召回

默认数据集只有 12 张程序生成的示意图和 12 个中文查询，不含真实企业信息。
CI 验证数据生成、Schema、Recall@1/3 指标计算和门禁；只有显式 `--live` 才下载
`OFA-Sys/chinese-clip-vit-base-patch16` 并产出真实图片召回指标。

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
