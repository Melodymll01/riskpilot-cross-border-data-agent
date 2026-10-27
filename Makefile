PY ?= python
GO ?= go
PORT ?= 8765
AGENT_RUNTIME_DIR := services/agent-runtime
CONTROL_PLANE_DIR := services/control-plane
AGENT_RUNTIME_ENV := PYTHONSAFEPATH=1 PYTHONPATH=$(abspath $(AGENT_RUNTIME_DIR))
.PHONY: help install install-dev lint format type-check test test-cov evidence-eval serve serve-python serve-stop serve-status go-test ci docker-build docker-up docker-down docker-smoke
help:
	@echo "install-dev | lint | type-check | test | evidence-eval | serve | docker-up"
install:
	$(PY) -m pip install -r $(AGENT_RUNTIME_DIR)/requirements.txt
install-dev:
	$(PY) -m pip install -r $(AGENT_RUNTIME_DIR)/requirements-dev.txt
lint:
	$(PY) -m ruff check --config $(AGENT_RUNTIME_DIR)/pyproject.toml $(AGENT_RUNTIME_DIR) scripts
	$(PY) -m ruff format --check --config $(AGENT_RUNTIME_DIR)/pyproject.toml $(AGENT_RUNTIME_DIR) scripts
format:
	$(PY) -m ruff check --fix --config $(AGENT_RUNTIME_DIR)/pyproject.toml $(AGENT_RUNTIME_DIR) scripts
	$(PY) -m ruff format --config $(AGENT_RUNTIME_DIR)/pyproject.toml $(AGENT_RUNTIME_DIR) scripts
type-check:
	$(AGENT_RUNTIME_ENV) $(PY) -m mypy --config-file $(AGENT_RUNTIME_DIR)/pyproject.toml \
		$(AGENT_RUNTIME_DIR)/domain $(AGENT_RUNTIME_DIR)/app $(AGENT_RUNTIME_DIR)/infra \
		$(AGENT_RUNTIME_DIR)/observability_context.py
test:
	$(AGENT_RUNTIME_ENV) $(PY) -m pytest -c $(AGENT_RUNTIME_DIR)/pytest.ini $(AGENT_RUNTIME_DIR)/tests -q
test-cov:
	$(AGENT_RUNTIME_ENV) $(PY) -m pytest -c $(AGENT_RUNTIME_DIR)/pytest.ini \
		--cov-config=$(AGENT_RUNTIME_DIR)/pyproject.toml --cov --cov-report=term-missing \
		$(AGENT_RUNTIME_DIR)/tests
evidence-eval:
	$(AGENT_RUNTIME_ENV) $(PY) -m evaluations.evidence_qa.run --oracle-self-check --no-write
serve-python:
	$(AGENT_RUNTIME_ENV) $(PY) -m uvicorn main:app --host 127.0.0.1 --port $(PORT)
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
	./scripts/compose.sh build agent-runtime control-plane
docker-up:
	$(PY) scripts/dev_stack.py prepare
	RISKPILOT_UID=$$(id -u) RISKPILOT_GID=$$(id -g) ./scripts/compose.sh up -d
docker-down:
	./scripts/compose.sh down
docker-smoke:
	./scripts/compose_smoke.sh
