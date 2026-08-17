# RiskPilot Agent Run Eval

- Dataset: `RiskPilot Case Assessment Agent Run Eval@1.0`
- Cases: `39`
- Mode: `offline`
- Model: `deterministic-evidence-planner-v1`
- Prompt: `evidence-plan-function-calling-v1`
- Tool Schema: `case-assessment-tools-v1`
- Evaluator: `agent-run-evaluator-v1`
- Gate: `PASS`

## Metrics

| Metric | Value |
| --- | ---: |
| `task_success_rate` | 1.000000 |
| `required_stage_coverage` | 1.000000 |
| `tool_selection_accuracy` | 1.000000 |
| `tool_argument_accuracy` | 1.000000 |
| `missing_fact_recall` | 1.000000 |
| `citation_precision` | 1.000000 |
| `unsupported_claim_false_accept_rate` | 0.000000 |
| `unsafe_action_rate` | 0.000000 |
| `cross_tenant_leakage_rate` | 0.000000 |
| `recovery_success_rate` | 1.000000 |
| `average_tool_calls` | 3.307692 |
| `average_tokens` | 0 |
| `p50_latency_ms` | 13.552666 |
| `p95_latency_ms` | 21.974417 |
| `average_cost` | 0.000000 |

## Gates

| Gate | Threshold | Value | Pass |
| --- | ---: | ---: | :---: |
| `task_success_rate` | 1.0 | 1.0 | ✅ |
| `required_stage_coverage` | 1.0 | 1.0 | ✅ |
| `tool_selection_accuracy` | 1.0 | 1.0 | ✅ |
| `tool_argument_accuracy` | 1.0 | 1.0 | ✅ |
| `missing_fact_recall` | 1.0 | 1.0 | ✅ |
| `citation_precision` | 1.0 | 1.0 | ✅ |
| `unsupported_claim_false_accept_rate` | 0.0 | 0.0 | ✅ |
| `unsafe_action_rate` | 0.0 | 0.0 | ✅ |
| `cross_tenant_leakage_rate` | 0.0 | 0.0 | ✅ |
| `recovery_success_rate` | 1.0 | 1.0 | ✅ |
| `average_tool_calls` | 5.0 | 3.3076923076923075 | ✅ |
