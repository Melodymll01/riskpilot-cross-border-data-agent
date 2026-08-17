"""HTTP OpenTelemetry span 与 Prometheus 指标 middleware。"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def install_observability_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _observability(request, call_next):  # type: ignore[no-untyped-def]
        container = getattr(request.app.state, "container", None)
        if container is None:
            return await call_next(request)
        started = time.perf_counter()
        with container.trace.span(
            "riskpilot.http.request",
            metadata={
                "http.request.method": request.method,
            },
        ) as span:
            try:
                response = await call_next(request)
            except Exception as exc:
                duration = time.perf_counter() - started
                route = _route_template(request)
                span.add_metadata(
                    {
                        "http.route": route,
                        "http.response.status_code": 500,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "duration_ms": duration * 1000,
                    }
                )
                container.metrics.observe_http(
                    method=request.method,
                    route=route,
                    status_code=500,
                    duration_seconds=duration,
                )
                raise
            duration = time.perf_counter() - started
            route = _route_template(request)
            span.add_metadata(
                {
                    "http.route": route,
                    "http.response.status_code": response.status_code,
                    "status": "completed",
                    "duration_ms": duration * 1000,
                }
            )
            container.metrics.observe_http(
                method=request.method,
                route=str(route),
                status_code=response.status_code,
                duration_seconds=duration,
            )
            return response


def _route_template(request) -> str:  # type: ignore[no-untyped-def]
    path = str(request.scope.get("path") or request.url.path)
    path_params = request.scope.get("path_params", {})
    if isinstance(path_params, dict) and path_params:
        template = path
        for name, value in sorted(
            path_params.items(),
            key=lambda item: len(str(item[1])),
            reverse=True,
        ):
            segment = re.compile(rf"(?<=/){re.escape(str(value))}(?=/|$)")
            template, replaced = segment.subn(f"{{{name}}}", template, count=1)
            if replaced == 0:
                template = ""
                break
        if template:
            return template
    route = getattr(request.scope.get("route"), "path", None)
    if isinstance(route, str) and route:
        return path
    return "unmatched"
