"""Celery Worker 的最小文档处理 composition root。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.factories import (
    build_document_parser,
    build_embedder,
    build_evidence_chunker,
    build_evidence_index,
    build_metrics,
    build_object_store,
    build_sqlalchemy_database,
    build_trace,
)
from app.workers import (
    DocumentOcrWorker,
    DocumentPipelineWorker,
    DocumentProcessingWorker,
    EvidenceIndexWorker,
)
from config import Settings
from infra.document_processing import RapidOcrDocumentAdapter
from infra.storage.sqlalchemy import SqlAlchemyDocumentRepo

if TYPE_CHECKING:
    from domain.ports import MetricsPort, TracePort


@dataclass
class WorkerRuntime:
    pipeline: DocumentPipelineWorker
    document_repo: SqlAlchemyDocumentRepo
    database: object
    trace: TracePort
    metrics: MetricsPort

    def close(self) -> None:
        dispose = getattr(self.database, "dispose", None)
        if callable(dispose):
            dispose()
        shutdown = getattr(self.trace, "shutdown", None)
        if callable(shutdown):
            shutdown()


def build_worker_runtime(settings: Settings | None = None) -> WorkerRuntime:
    settings = settings or Settings()
    if settings.task_backend != "celery":
        raise RuntimeError("Celery Worker 必须使用 TASK_BACKEND=celery")
    database = build_sqlalchemy_database(settings)
    document_repo = SqlAlchemyDocumentRepo(database)
    object_store = build_object_store(settings)
    evidence_index = build_evidence_index(settings, database=database)
    trace = build_trace(settings)
    metrics = build_metrics(settings)
    pipeline = DocumentPipelineWorker(
        document_repo=document_repo,
        parser=DocumentProcessingWorker(
            document_repo=document_repo,
            object_store=object_store,
            parser=build_document_parser(settings),
            clock=time.time,
        ),
        ocr=DocumentOcrWorker(
            document_repo=document_repo,
            object_store=object_store,
            ocr=RapidOcrDocumentAdapter(clock=time.time),
            clock=time.time,
        ),
        indexer=EvidenceIndexWorker(
            document_repo=document_repo,
            chunker=build_evidence_chunker(settings),
            evidence_index=evidence_index,
            embedder=build_embedder(settings),
            clock=time.time,
        ),
    )
    return WorkerRuntime(
        pipeline=pipeline,
        document_repo=document_repo,
        database=database,
        trace=trace,
        metrics=metrics,
    )
