PY ?= python
GO ?= go
PORT ?= 8765
.PHONY: help install install-dev lint format type-check test test-cov evidence-eval serve serve-python serve-stop serve-status go-test ci docker-build docker-up docker-down docker-smoke
help:
	@echo "install-dev | lint | type-check | test | evidence-eval | serve | docker-up"
install:
	$(PY) -m pip install -r requirements.txt
install-dev:
	$(PY) -m pip install -r requirements-dev.txt
lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
format:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
type-check:
	$(PY) -m mypy domain app infra observability_context.py
test:
	$(PY) -m pytest -q
test-cov:
	$(PY) -m pytest --cov --cov-report=term-missing
evidence-eval:
	$(PY) -m evaluations.evidence_qa.run --oracle-self-check --no-write
serve-python:
	$(PY) -m uvicorn main:app --host 127.0.0.1 --port $(PORT)
serve:
	$(PY) scripts/dev_stack.py start --python $(PY) --go $(GO)
serve-stop:
	$(PY) scripts/dev_stack.py stop
serve-status:
	$(PY) scripts/dev_stack.py status
go-test:
	RISKPILOT_GO=$(GO) $(PY) scripts/go_tool.py vet ./...
	RISKPILOT_GO=$(GO) $(PY) scripts/go_tool.py test -race ./...
ci: lint type-check go-test test evidence-eval
docker-build:
	./scripts/compose.sh build app gateway
docker-up:
	$(PY) scripts/dev_stack.py prepare
	RISKPILOT_UID=$$(id -u) RISKPILOT_GID=$$(id -g) ./scripts/compose.sh up -d
docker-down:
	./scripts/compose.sh down
docker-smoke:
	./scripts/compose_smoke.sh
