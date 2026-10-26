"""Explicit live smoke: real Traex GPT, local embeddings, account memory and risk.

All application data is isolated. Identity is a synthetic fixture, not live OAuth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


class TimedChat:
    """Record operation timings without logging prompts, credentials or model output."""

    def __init__(self, chat):
        self.chat_adapter = chat
        self.operations = []

    def _invoke(self, method, messages, **kwargs):
        entry = {"operation": kwargs.get("operation", "chat"), "status": "running"}
        self.operations.append(entry)
        print(json.dumps({"phase_started": entry["operation"]}), flush=True)
        started = time.monotonic()
        try:
            result = getattr(self.chat_adapter, method)(messages, **kwargs)
            entry["status"] = "completed"
            return result
        except Exception as exc:
            entry.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            entry["duration_seconds"] = round(time.monotonic() - started, 2)
            print(json.dumps(entry), flush=True)

    def chat(self, messages, **kwargs):
        return self._invoke("chat", messages, **kwargs)

    def chat_with_usage(self, messages, **kwargs):
        return self._invoke("chat_with_usage", messages, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to execute authenticated Traex GPT calls")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix="riskpilot-live-features-") as directory:
        temp = Path(directory)
        os.environ.update(
            LLM_PROVIDER="traex",
            EMBED_PROVIDER="sentence_transformer",
            ENABLE_QUERY_REWRITE="false",
            ENABLE_RERANKER="false",
            SQLITE_DB_PATH=str(temp / "app.sqlite3"),
            LANGGRAPH_CHECKPOINT_DB_PATH=str(temp / "checkpoints.sqlite3"),
            CHROMA_PERSIST_DIR=str(temp / "chroma"),
            OBJECT_STORE_DIR=str(temp / "objects"),
            UPLOAD_DIR=str(temp / "uploads"),
            MEMORY_SUMMARY_ENABLED="false",
            RISK_PILOT_LANGSMITH_ENABLED="false",
            OTEL_ENABLED="false",
            CONVERSATION_RUN_TIMEOUT_SECONDS="480",
            CONVERSATION_MODEL_CALL_TIMEOUT_SECONDS="100",
            CONVERSATION_TOOL_CALL_TIMEOUT_SECONDS="210",
            TRAEX_TIMEOUT_SECONDS="95",
        )
        from app.container import AppContainer
        from app.factories import build_chat
        from config import Settings
        from domain.models import KbChunk, User

        settings = Settings(_env_file=None)
        timed_chat = TimedChat(build_chat(settings))
        container = AppContainer(settings, chat=timed_chat)
        owner = "github:riskpilot-synthetic-demo"
        now = time.time()
        container.user_repo.upsert(
            User(
                user_id=owner,
                provider="github",
                provider_id="synthetic-demo",
                display_name="合成验收用户",
                created_at=now,
                last_active_at=now,
            )
        )
        try:
            container.account_memory.set_enabled(owner, True)
            memory = container.account_memory.create(
                owner,
                content="回答用简洁中文，并列出引用来源",
                kind="preference",
            )
            law = json.loads((root / "assets/demo/pipl_article19.json").read_text())
            chunk = KbChunk(
                chunk_id="demo-pipl-article-19",
                text=law["text"],
                source_type="web",
                source_name=law["title"],
                title=law["title"],
                source_url=law["source_url"],
                chunk_index=0,
                owner_id=None,
            )
            vectors = container.embedder.embed([chunk.text])
            container.kb_repo.add_chunks([chunk], vectors)
            uploaded = container.document_management.upload(
                owner,
                filename="overseas_crm.txt",
                content=(root / "assets/demo/overseas_crm.txt").read_bytes(),
            )
            until = time.monotonic() + 60
            while time.monotonic() < until:
                detail = container.document_management.get_detail(
                    uploaded.document.document_id, owner
                )
                if detail.document.status in {"ready", "failed"}:
                    break
                time.sleep(0.05)
            assert detail.document.status == "ready", detail
            question = (
                "请进行风险评估：仅分析所选材料的无限期保存条款与《个人信息保护法》第十九条"
                "的最短必要期限原则是否存在风险。请先分别检索该法规和所选材料，再调用assess_risk。"
                "不判断出境申报路径。"
            )
            started = time.monotonic()
            stream = container.run_copilot.stream(
                owner_id=owner,
                task_id=None,
                user_message=question,
                attachment_doc_ids=[uploaded.document.document_id],
            )
            events = []
            for event in stream:
                events.append(event)
                print(
                    json.dumps(
                        {
                            "event": event.event_type.value,
                            "stage": event.payload.get("stage"),
                            "status": event.payload.get("status"),
                        }
                    ),
                    flush=True,
                )
            run = container.conversation_run_repo.get(stream.run_id, owner)
            risk = container.risk_assessment.for_run(owner, stream.run_id)
            answer = next(
                (event.payload for event in reversed(events) if event.event_type.value == "answer"),
                None,
            )
            refs = container.account_memory_repo.references(stream.run_id, owner)
            report = {
                "verified_at": datetime.now(UTC).isoformat(),
                "cli": "traex",
                "provider": "trae",
                "requested_model": settings.traex_model,
                "embedding_model": settings.local_sentence_embedding_model,
                "embedding_dimensions": len(vectors[0]),
                "identity_mode": "synthetic authenticated-account fixture; OAuth not exercised",
                "material_mode": "synthetic contract + verified official article excerpt",
                "langsmith_live": False,
                "duration_seconds": round(time.monotonic() - started, 2),
                "operation_timings": timed_chat.operations,
                "run_status": run.status if run else None,
                "end_reason": run.end_reason if run else None,
                "error_type": run.error_type if run else None,
                "events": [
                    {
                        "type": event.event_type.value,
                        "tool": event.payload.get("tool_name"),
                        "error": event.payload.get("error"),
                    }
                    for event in events
                    if event.event_type.value in {"tool_call", "tool_error"}
                ],
                "model_calls": run.model_call_count if run else None,
                "tool_calls": run.tool_call_count if run else None,
                "input_tokens": run.input_tokens if run else None,
                "output_tokens": run.output_tokens if run else None,
                "memory_recalled": memory.memory_id in refs,
                "risk_status": risk.status if risk else None,
                "risk_level": risk.risk_level if risk else None,
                "risk_findings": len(risk.findings) if risk else 0,
                "answer": answer,
            }
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            assert run is not None and run.status == "completed", report
            assert answer and risk and risk.findings and report["memory_recalled"], report
        finally:
            container.close()


if __name__ == "__main__":
    main()
