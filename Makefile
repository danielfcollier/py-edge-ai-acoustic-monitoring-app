SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# --- Variables ---
PYTHON  := .venv/bin/python3
UV      := uv
BASE_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
SRC_DIR  := $(BASE_DIR)/src
APP_DIR  := $(SRC_DIR)/app
SCRIPTS_DIR := $(SRC_DIR)/scripts

# Styling
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
NC     := \033[0m # No Color

# --- Main Targets ---

.PHONY: help
help: ## Show this help message.
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

.PHONY: install setup venv lock
install: setup venv ## Install all dependencies (Prod + Dev).
	@echo -e "$(GREEN)>>> Installing dependencies with uv...$(NC)"
	@$(UV) sync --group dev
	@echo -e "$(GREEN)>>> Installation complete. Run 'make setup-models' next.$(NC)"

setup: ## Install system libraries (Ubuntu/Debian).
	@echo -e "$(GREEN)>>> Installing system audio libraries...$(NC)"
	@sudo apt update && sudo apt install -y libportaudio2 libsndfile1 ffmpeg

venv: ## Create virtual environment using Python 3.11
	@echo -e "$(GREEN)>>> Creating .venv with Python 3.11...$(NC)"
	@$(UV) venv --python 3.11

lock: ## Update uv.lock file.
	@$(UV) lock

# --- Development & Quality ---

.PHONY: lint format check test coverage clean clean-all
lint: ## Run Ruff linter.
	@echo -e "$(GREEN)>>> Linting source...$(NC)"
	@$(PYTHON) -m ruff check $(SRC_DIR)

format: ## Format code with Ruff.
	@echo -e "$(GREEN)>>> Formatting source...$(NC)"
	@$(PYTHON) -m ruff format $(SRC_DIR)
	@$(PYTHON) -m ruff check $(SRC_DIR) --fix

check: lint test ## Run lint and tests.
	@echo -e "$(GREEN)>>> All checks passed.$(NC)"

test: ## Run unit tests (excludes e2e).
	@echo -e "$(GREEN)>>> Running tests...$(NC)"
	@$(PYTHON) -m pytest -v

test-e2e: ## Run end-to-end tests (requires credentials in .env). Skips heartbeat tests.
	@echo -e "$(GREEN)>>> Running e2e tests...$(NC)"
	@mkdir -p reports
	@$(PYTHON) -m pytest tests/e2e -v -m "e2e and not heartbeat" --junitxml=reports/e2e-results.xml

test-e2e-heartbeat: ## Run heartbeat/health-monitoring e2e tests only.
	@echo -e "$(GREEN)>>> Running heartbeat e2e tests...$(NC)"
	@mkdir -p reports
	@$(PYTHON) -m pytest tests/e2e/test_heartbeat.py -v -m "e2e and heartbeat" --junitxml=reports/e2e-heartbeat-results.xml

coverage: ## Generate test coverage report.
	@mkdir -p reports
	@$(PYTHON) -m pytest --cov=src --cov-report=term-missing --cov-report=html --cov-report=xml:reports/coverage.xml

clean: ## Remove python cache files.
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -exec rm -rf {} +
	@rm -rf .pytest_cache .coverage htmlcov

clean-all: clean clean-deb ## Remove venv and build artifacts.
	@echo -e "$(GREEN)>>> Full cleanup...$(NC)"
	@rm -rf .venv .ruff_cache .mypy_cache build dist *.egg-info src/app/vendor/

# --- Project Setup & Models ---

.PHONY: setup-models download-models
setup-models: ## Download YAMNet models and class maps.
	@echo -e "$(GREEN)>>> Downloading AI Models (YAMNet)...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/setup_yamnet.py

# --- Application Runners ---

.PHONY: run run-default run-sim report
run: ## Run Edge Monitor (Auto-detect config).
	@echo -e "$(GREEN)>>> Starting Edge Monitor...$(NC)"
	@$(UV) run ai-acoustic-monitor-run

run-default: ## Run with default PC microphone (No UMIK-1).
	@echo -e "$(GREEN)>>> Starting with Default Microphone...$(NC)"
	@$(UV) run ai-acoustic-monitor-run --device "default"

run-sim: ## Run in simulation mode (if supported by base app).
	@$(UV) run ai-acoustic-monitor-run --config "security_policy.yaml" --device "sysdefault"


report: ## Generate PDF report from cloud metrics.
	@echo -e "$(GREEN)>>> Generating Analytics Report...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/generate_report.py
	@echo -e "$(GREEN)>>> Report saved to reports/report.pdf$(NC)"

# --- Cloud Bucket Utilities ---
# Default bucket from .env; override on the command line: make bucket-download BUCKET=my-bucket
AUDIO_BUCKET ?= $(shell grep '^MAGALU_BUCKET=' .env 2>/dev/null | cut -d= -f2 | tr -d '"')

.PHONY: bucket-download bucket-clean

bucket-download: ## Download all objects from the bucket to ./downloads (override: BUCKET=name DEST=path)
	$(eval _BUCKET := $(or $(BUCKET),$(AUDIO_BUCKET)))
	@if [ -z "$(_BUCKET)" ]; then echo -e "$(RED)Error: set MAGALU_BUCKET in .env or pass BUCKET=name$(NC)"; exit 1; fi
	$(eval _DEST   := $(or $(DEST),downloads))
	@echo -e "$(GREEN)>>> Downloading s3://$(_BUCKET) → $(_DEST)/$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/bucket_ops.py download --bucket $(_BUCKET) --dest $(_DEST)

bucket-clean: ## Delete ALL objects from the bucket (override: BUCKET=name). Prompts for confirmation.
	$(eval _BUCKET := $(or $(BUCKET),$(AUDIO_BUCKET)))
	@if [ -z "$(_BUCKET)" ]; then echo -e "$(RED)Error: set MAGALU_BUCKET in .env or pass BUCKET=name$(NC)"; exit 1; fi
	@printf "$(YELLOW)⚠️  Delete ALL objects from s3://$(_BUCKET)? [y/N] $(NC)" && read ans && [ "$$ans" = "y" ] || { echo "Aborted."; exit 0; }
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/bucket_ops.py clean --bucket $(_BUCKET)

# --- Hardware Utilities (Pi) ---

.PHONY: fan-test list-devices
fan-test: ## Test Raspberry Pi fan control.
	@echo -e "$(GREEN)>>> Running Fan Test...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/raspberrypi/fan_test.py

list-devices: ## List available audio input devices.
	@echo -e "$(GREEN)>>> Listing Audio Devices...$(NC)"
	@$(PYTHON) -c "import sounddevice as sd; print(sd.query_devices())"

# ==============================================================================
# VERSION BUMPING
# ==============================================================================
CURRENT_VERSION := $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)"/\1/')
MAJOR := $(word 1,$(subst ., ,$(CURRENT_VERSION)))
MINOR := $(word 2,$(subst ., ,$(CURRENT_VERSION)))
PATCH := $(word 3,$(subst ., ,$(CURRENT_VERSION)))

.PHONY: bump-patch bump-minor bump-major

bump-patch: ## Bump patch version (0.1.0 → 0.1.1)
	$(eval NEW_VERSION := $(MAJOR).$(MINOR).$(shell echo $$(($(PATCH)+1))))
	@sed -i 's/^version = "$(CURRENT_VERSION)"/version = "$(NEW_VERSION)"/' pyproject.toml
	@sed -i 's/__version__ = "$(CURRENT_VERSION)"/__version__ = "$(NEW_VERSION)"/' src/app/__init__.py
	@printf "%s\n" "Bumped version: $(CURRENT_VERSION) → $(NEW_VERSION)"

bump-minor: ## Bump minor version (0.1.0 → 0.2.0)
	$(eval NEW_VERSION := $(MAJOR).$(shell echo $$(($(MINOR)+1))).0)
	@sed -i 's/^version = "$(CURRENT_VERSION)"/version = "$(NEW_VERSION)"/' pyproject.toml
	@sed -i 's/__version__ = "$(CURRENT_VERSION)"/__version__ = "$(NEW_VERSION)"/' src/app/__init__.py
	@printf "%s\n" "Bumped version: $(CURRENT_VERSION) → $(NEW_VERSION)"

bump-major: ## Bump major version (0.1.0 → 1.0.0)
	$(eval NEW_VERSION := $(shell echo $$(($(MAJOR)+1))).0.0)
	@sed -i 's/^version = "$(CURRENT_VERSION)"/version = "$(NEW_VERSION)"/' pyproject.toml
	@sed -i 's/__version__ = "$(CURRENT_VERSION)"/__version__ = "$(NEW_VERSION)"/' src/app/__init__.py
	@printf "%s\n" "Bumped version: $(CURRENT_VERSION) → $(NEW_VERSION)"

# ==============================================================================
# DEB PACKAGING
# ==============================================================================
DISTRO ?= bookworm
VERSION_BUMP ?= patch

# Read DEB_S3_BUCKET from .env if not passed on the command line
DEB_BUCKET ?= $(shell grep '^DEB_S3_BUCKET=' .env 2>/dev/null | cut -d= -f2 | tr -d '"')

.PHONY: install-build-deps vendor build-deb clean-deb test-deb publish-deb release

install-build-deps: ## Install system build tools for .deb packaging
	sudo apt-get update
	sudo apt-get install -y build-essential debhelper dh-python python3-all python3-setuptools

vendor: ## Bundle all Python deps into src/app/vendor/ for .deb install
	@printf "%s\n" "📦 Vendoring dependencies from uv.lock..."
	mkdir -p src/app/vendor
	touch src/app/vendor/__init__.py
	uv export --no-dev --frozen --format requirements-txt | grep -v "file://" > requirements.frozen.txt
	uv pip install -r requirements.frozen.txt --target src/app/vendor
	rm requirements.frozen.txt
	@printf "%s\n" "✅ Vendor populated."

clean-deb: ## Remove .deb build artifacts and vendor
	rm -rf deb_dist dist build *.egg-info src/app/vendor/

build-deb: clean-deb vendor ## Build the ai-acoustic-monitor .deb package
	@printf "%s\n" "🚀 Building .deb package..."
	@bash build_deb.sh

test-deb: ## Test .deb in a clean Docker container (DISTRO=bookworm|noble)
	@printf "%s\n" "🧪 Testing package in Docker (debian/ubuntu:$(DISTRO))..."
	@docker run --rm --network=host -v $$(pwd):/dist debian:$(DISTRO) sh -c "\
		export DEBIAN_FRONTEND=noninteractive && \
		apt-get update -qq && \
		apt-get install -y /dist/deb_dist/*.deb && \
		printf '\n--- CLI ---\n' && \
		ai-acoustic-monitor-run --help && \
		printf '\n--- Service templates ---\n' && \
		ls -l /usr/lib/ai-acoustic-monitor/setup/"

publish-deb: ## Publish built .deb to S3 APT repository (bucket from .env DEB_S3_BUCKET, or BUCKET=…)
	$(eval _BUCKET := $(or $(BUCKET),$(DEB_BUCKET)))
	@if [ -z "$(_BUCKET)" ]; then \
		printf "%s\n" "Error: bucket not set. Add to .env: DEB_S3_BUCKET=your-bucket"; \
		printf "%s\n" "  or pass: make publish-deb BUCKET=your-bucket"; \
		exit 1; \
	fi
	@set -a && . ./.env && set +a && \
	$(UV) run --group publish python publish_repo.py \
		"$$(find deb_dist -name '*.deb' -type f | head -1)" $(_BUCKET)

release: ## Bump version, build .deb, publish to MGC (VERSION_BUMP=patch|minor|major)
	@if [ -z "$(DEB_BUCKET)" ]; then \
		printf "%s\n" "Error: DEB_S3_BUCKET not set. Add to .env: DEB_S3_BUCKET=your-bucket"; \
		printf "%s\n" "  or pass: make release DEB_BUCKET=your-bucket"; \
		exit 1; \
	fi
	@printf "%s\n" "🔖 Bumping $(VERSION_BUMP) version..."
	@$(MAKE) bump-$(VERSION_BUMP)
	@printf "%s\n" "📦 Building .deb..."
	@$(MAKE) build-deb
	@printf "%s\n" "🚀 Publishing to s3://$(DEB_BUCKET)..."
	@$(MAKE) publish-deb BUCKET=$(DEB_BUCKET)
	@printf "%s\n" ""
	@printf "%s\n" "✅ Release complete. Install instructions printed above."