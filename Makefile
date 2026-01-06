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
	@$(UV) sync --extra dev
	@echo -e "$(GREEN)>>> Installation complete. Run 'make setup-models' next.$(NC)"

setup: ## Install system libraries (Ubuntu/Debian).
	@echo -e "$(GREEN)>>> Installing system audio libraries...$(NC)"
	@sudo apt update && sudo apt install -y libportaudio2 libsndfile1 -y

venv: ## Create virtual environment.
	@echo -e "$(GREEN)>>> Creating .venv...$(NC)"
	@$(UV) venv

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

test: ## Run unit tests.
	@echo -e "$(GREEN)>>> Running tests...$(NC)"
	@$(PYTHON) -m pytest

coverage: ## Generate test coverage report.
	@$(PYTHON) -m pytest --cov=src --cov-report=term-missing --cov-report=html

clean: ## Remove python cache files.
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -exec rm -rf {} +
	@rm -rf .pytest_cache .coverage htmlcov

clean-all: clean ## Remove venv and build artifacts.
	@echo -e "$(GREEN)>>> Full cleanup...$(NC)"
	@rm -rf .venv .ruff_cache .mypy_cache build dist *.egg-info

# --- Project Setup & Models ---

.PHONY: setup-models download-models
setup-models: ## Download YAMNet models and class maps.
	@echo -e "$(GREEN)>>> Downloading AI Models (YAMNet)...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/setup_yamnet.py

# --- Application Runners ---

.PHONY: run run-default run-sim report
run: ## Run Edge Monitor (Auto-detect config).
	@echo -e "$(GREEN)>>> Starting Edge Monitor...$(NC)"
	@$(UV) run edge-monitor

run-default: ## Run with default PC microphone (No UMIK-1).
	@echo -e "$(GREEN)>>> Starting with Default Microphone...$(NC)"
	@$(UV) run edge-monitor --device "default"

run-sim: ## Run in simulation mode (if supported by base app).
	@$(UV) run edge-monitor --config "security_policy.yaml" --device "sysdefault"

report: ## Generate PDF report from cloud metrics.
	@echo -e "$(GREEN)>>> Generating Analytics Report...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/generate_report.py
	@echo -e "$(GREEN)>>> Report saved to reports/report.pdf$(NC)"

# --- Hardware Utilities (Pi) ---

.PHONY: fan-test list-devices
fan-test: ## Test Raspberry Pi fan control.
	@echo -e "$(GREEN)>>> Running Fan Test...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/raspberrypi/fan_test.py

list-devices: ## List available audio input devices.
	@echo -e "$(GREEN)>>> Listing Audio Devices...$(NC)"
	@$(PYTHON) -c "import sounddevice as sd; print(sd.query_devices())"