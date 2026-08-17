# RagDataOut Makefile
# Usage on Windows PowerShell: `make test` works if you have GNU Make installed.
#                              Otherwise see scripts/* equivalents.

PY ?= python
PIP ?= pip
PORT ?= 8000

.PHONY: help install install-dev lint format type-check test test-cov agent-eval \
        serve clean docker-build docker-up docker-down docker-observability \
        docker-smoke seed-demo hooks ci

help:
	@echo "Available targets:"
	@echo "  install       - install runtime deps"
	@echo "  install-dev   - install dev deps (pytest, ruff, mypy, ...)"
	@echo "  lint          - ruff check"
	@echo "  format        - ruff format"
	@echo "  type-check    - mypy on domain/app/infra"
	@echo "  test          - run pytest (offline)"
	@echo "  test-cov      - run pytest with coverage"
	@echo "  agent-eval    - run offline Agent trajectory evaluation"
	@echo "  serve         - uvicorn dev server on :$(PORT)"
	@echo "  hooks         - install pre-commit hooks"
	@echo "  ci            - lint + type-check + test (mirrors GitHub Actions)"
	@echo "  docker-build  - docker compose build"
	@echo "  docker-up     - docker compose up -d"
	@echo "  docker-down   - docker compose down"
	@echo "  seed-demo     - create idempotent synthetic cases"
	@echo "  docker-observability - start optional Prometheus"
	@echo "  docker-smoke  - verify healthy stack + seeded cases"
	@echo "  clean         - remove caches"

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

type-check:
	mypy domain app infra observability_context.py scripts/seed_demo.py

test:
	pytest -q

test-cov:
	pytest -ra --cov --cov-report=term-missing

agent-eval:
	python -m evaluations.agent_runs.run --no-write

serve:
	uvicorn main:app --reload --port $(PORT)

hooks:
	pre-commit install

ci: lint type-check test agent-eval

docker-build:
	./scripts/compose.sh build app

docker-up:
	./scripts/compose.sh up -d

docker-down:
	./scripts/compose.sh down

docker-observability:
	./scripts/compose.sh --profile observability up -d prometheus

seed-demo:
	./scripts/compose.sh --profile tools run --rm seed

docker-smoke:
	./scripts/compose_smoke.sh

clean:
	@echo "Cleaning caches..."
	-rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	-find . -type d -name __pycache__ -prune -exec rm -rf {} +
