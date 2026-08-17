"""跨 app/infra 的轻量可观测性 ContextVar，不依赖任何业务或框架。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

run_id_var: ContextVar[str | None] = ContextVar("run_id_var", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id_var", default=None)
case_id_var: ContextVar[str | None] = ContextVar("case_id_var", default=None)
node_var: ContextVar[str | None] = ContextVar("node_var", default=None)
tool_var: ContextVar[str | None] = ContextVar("tool_var", default=None)


@dataclass(frozen=True)
class RuntimeObservabilityContext:
    run_id: str | None
    workspace_id: str | None
    case_id: str | None
    node: str | None
    tool: str | None


def get_runtime_observability_context() -> RuntimeObservabilityContext:
    return RuntimeObservabilityContext(
        run_id=run_id_var.get(),
        workspace_id=workspace_id_var.get(),
        case_id=case_id_var.get(),
        node=node_var.get(),
        tool=tool_var.get(),
    )


@contextmanager
def observability_context(
    *,
    run_id: str | None = None,
    workspace_id: str | None = None,
    case_id: str | None = None,
    node: str | None = None,
    tool: str | None = None,
) -> Iterator[None]:
    tokens = (
        (run_id_var, run_id_var.set(run_id if run_id is not None else run_id_var.get())),
        (
            workspace_id_var,
            workspace_id_var.set(
                workspace_id if workspace_id is not None else workspace_id_var.get()
            ),
        ),
        (case_id_var, case_id_var.set(case_id if case_id is not None else case_id_var.get())),
        (node_var, node_var.set(node if node is not None else node_var.get())),
        (tool_var, tool_var.set(tool if tool is not None else tool_var.get())),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
