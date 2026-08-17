#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE="$ROOT/scripts/compose.sh"

service_health() {
    container_id=$("$COMPOSE" ps -q "$1")
    if [ -z "$container_id" ]; then
        echo "service $1 未运行" >&2
        exit 1
    fi
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id"
}

app_health=$(service_health app)
worker_health=$(service_health worker)
[ "$app_health" = "healthy" ]
[ "$worker_health" = "healthy" ]

demo_cases=$(
    "$COMPOSE" exec -T app python - <<'PY'
import http.cookiejar
import json
import urllib.request

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
with opener.open(
    urllib.request.Request(
        "http://127.0.0.1:8001/api/v2/auth/demo",
        data=b"",
        method="POST",
    ),
    timeout=5,
) as response:
    if response.status != 200:
        raise SystemExit(f"Demo 登录失败: {response.status}")
with opener.open(
    "http://127.0.0.1:8001/api/v3/cases?workspace_id=ws_demo_cross_border",
    timeout=5,
) as response:
    cases = json.load(response)["cases"]
expected = {
    "case_demo_happy_path",
    "case_demo_human_loop",
    "case_demo_failure_recovery",
}
actual = {item["case_id"] for item in cases}
if actual != expected:
    raise SystemExit(f"Demo Case 不完整: {sorted(actual)}")
print(len(cases))
PY
)
[ "$demo_cases" -eq 3 ]

chunks=$(
    "$COMPOSE" exec -T postgres \
        psql -U "${POSTGRES_USER:-riskpilot}" -d "${POSTGRES_DB:-riskpilot}" -Atc \
        "SELECT count(*) FROM evidence_chunks;"
)
runs=$(
    "$COMPOSE" exec -T postgres \
        psql -U "${POSTGRES_USER:-riskpilot}" -d "${POSTGRES_DB:-riskpilot}" -Atc \
        "SELECT count(*) FROM agent_runs WHERE workspace_id='ws_demo_cross_border';"
)
[ "$chunks" -ge 3 ]
[ "$runs" -eq 2 ]

"$COMPOSE" exec -T app python - <<'PY'
import urllib.request

with urllib.request.urlopen("http://worker:9101/metrics", timeout=5) as response:
    payload = response.read().decode()
if "riskpilot_worker_tasks_total" not in payload:
    raise SystemExit("Worker metrics 缺少 task counter")
PY

echo "demo_cases=$demo_cases"
echo "app_health=$app_health"
echo "worker_health=$worker_health"
echo "evidence_chunks=$chunks"
echo "agent_runs=$runs"
echo "compose_smoke=PASS"
