# Every target runs through the project venv so the workflow never depends on
# PATH order (bare `python` on this machine is system 3.9, which cannot run this code).

VENV := .venv
PY := $(VENV)/bin/python
BASE_PY ?= python3.12

.DEFAULT_GOAL := help

.PHONY: help venv install test lint format format-check check run clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(PY):
	$(BASE_PY) -m venv $(VENV)

venv: $(PY) ## Create the venv if missing

install: venv ## Install the package + dev deps (editable) into the venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test: ## Run the test suite
	$(PY) -m pytest

lint: ## Lint with ruff
	$(PY) -m ruff check .

format: ## Auto-format with ruff
	$(PY) -m ruff format .

format-check: ## Check formatting without writing
	$(PY) -m ruff format --check .

check: lint format-check test ## Full gate: lint + format check + tests

run: ## Run the CLI (pass args via ARGS=...)
	$(PY) -m audible_storygraph_sync.cli $(ARGS)

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
