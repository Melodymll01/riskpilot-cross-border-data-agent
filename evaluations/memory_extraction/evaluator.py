"""AI 长期记忆提取确定性协议评测器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.models import Message
from infra.memory import (
    build_memory_extraction_episode,
    validate_memory_candidate,
)

ExpectedDecision = Literal["accept", "reject"]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryMessage(EvaluationModel):
    message_id: str = Field(min_length=1, max_length=100)
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1, max_length=4000)


class MemoryExtractionCase(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    messages: list[MemoryMessage] = Field(min_length=1)
    candidate: dict[str, object] | None = None
    expected_decision: ExpectedDecision
    expected_user_message_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_case(self) -> MemoryExtractionCase:
        message_ids = [message.message_id for message in self.messages]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError(f"{self.case_id}: message_id 不能重复")
        if self.expected_decision == "accept" and self.candidate is None:
            raise ValueError(f"{self.case_id}: accept 样本必须提供 candidate")
        return self


class MemoryExtractionThresholds(EvaluationModel):
    decision_accuracy_min: float = Field(ge=0.0, le=1.0)
    unsafe_false_accept_count_max: int = Field(ge=0)
    source_filter_accuracy_min: float = Field(ge=0.0, le=1.0)


class MemoryExtractionDataset(EvaluationModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    thresholds: MemoryExtractionThresholds
    cases: list[MemoryExtractionCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> MemoryExtractionDataset:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id 不能重复")
        return self


def load_dataset(path: str | Path) -> MemoryExtractionDataset:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("评测数据集根节点必须是 JSON 对象")
    return MemoryExtractionDataset.model_validate(data)


def evaluate_protocol(dataset: MemoryExtractionDataset) -> dict[str, object]:
    case_results: list[dict[str, object]] = []
    correct_decisions = 0
    source_filter_matches = 0
    unsafe_false_accept_count = 0

    for case in dataset.cases:
        messages = [
            Message(
                msg_id=message.message_id,
                task_id=f"eval_{case.case_id}",
                role=message.role,
                content=message.content,
            )
            for message in case.messages
        ]
        episode = build_memory_extraction_episode(messages)
        actual_user_message_ids = list(episode.user_messages) if episode is not None else []
        source_filter_correct = actual_user_message_ids == case.expected_user_message_ids
        source_filter_matches += int(source_filter_correct)

        validated = (
            validate_memory_candidate(case.candidate, episode.user_messages)
            if case.candidate is not None and episode is not None
            else None
        )
        actual_decision: ExpectedDecision = "accept" if validated is not None else "reject"
        decision_correct = actual_decision == case.expected_decision
        correct_decisions += int(decision_correct)
        if case.expected_decision == "reject" and actual_decision == "accept":
            unsafe_false_accept_count += 1
        case_results.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "expected_decision": case.expected_decision,
                "actual_decision": actual_decision,
                "decision_correct": decision_correct,
                "source_filter_correct": source_filter_correct,
                "actual_user_message_ids": actual_user_message_ids,
            }
        )

    case_count = len(dataset.cases)
    decision_accuracy = correct_decisions / case_count
    source_filter_accuracy = source_filter_matches / case_count
    thresholds = dataset.thresholds
    gates = {
        "decision_accuracy": {
            "value": decision_accuracy,
            "threshold": thresholds.decision_accuracy_min,
            "passed": decision_accuracy >= thresholds.decision_accuracy_min,
        },
        "unsafe_false_accept_count": {
            "value": unsafe_false_accept_count,
            "threshold": thresholds.unsafe_false_accept_count_max,
            "passed": (unsafe_false_accept_count <= thresholds.unsafe_false_accept_count_max),
        },
        "source_filter_accuracy": {
            "value": source_filter_accuracy,
            "threshold": thresholds.source_filter_accuracy_min,
            "passed": (source_filter_accuracy >= thresholds.source_filter_accuracy_min),
        },
    }
    return {
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "case_count": case_count,
        },
        "mode": "protocol_self_check",
        "production_model_evidence": False,
        "metrics": {
            "decision_accuracy": decision_accuracy,
            "unsafe_false_accept_count": unsafe_false_accept_count,
            "source_filter_accuracy": source_filter_accuracy,
        },
        "gates": gates,
        "passed": all(gate["passed"] for gate in gates.values()),
        "cases": case_results,
    }
