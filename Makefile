# Every target runs through the project venv so the workflow never depends on
# PATH order (bare `python` on this machine is system 3.9, which cannot run this code).

VENV := .venv
PY := $(VENV)/bin/python
BASE_PY ?= python3.12

.DEFAULT_GOAL := help

.PHONY: help venv install install-packaging test lint format format-check check run desktop wheels package-create package-build package package-run clean

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
	$(PY) -m storywell.cli $(ARGS)

desktop: ## Launch the desktop GUI from source (pass ARGS=--headed to watch the browser)
	$(PY) -m storywell.desktop $(ARGS)

# Briefcase installs app requirements binary-only (--only-binary :all:), but a few
# transitive deps (audible's pbkdf2/pyaes, pywebview's proxy_tools) ship sdist-only.
# We build local wheels for those and point pip at them via PIP_FIND_LINKS.
SDIST_ONLY_DEPS := pbkdf2 pyaes proxy_tools
package-create package-build package package-run: export PIP_FIND_LINKS = $(abspath wheels)

install-packaging: venv ## Install Briefcase (the per-OS installer toolchain)
	$(PY) -m pip install -e ".[packaging]"

wheels: venv ## Build sdist-only deps into a local wheelhouse Briefcase can install from
	$(PY) -m pip wheel $(SDIST_ONLY_DEPS) -w wheels

package-create: wheels ## Scaffold the native app bundle for this OS (briefcase create)
	$(PY) -m briefcase create

package-build: wheels ## Compile the native app bundle (briefcase build)
	$(PY) -m briefcase build

package: wheels ## Build an unsigned installer for local testing (signed release: see docs/packaging.md)
	$(PY) -m briefcase package --adhoc-sign

package-run: wheels ## Run the packaged app
	$(PY) -m briefcase run

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
