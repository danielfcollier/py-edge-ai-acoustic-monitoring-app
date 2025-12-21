SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON 	:= .venv/bin/python3
PIP 	:= .venv/bin/pip
UV 		:= uv

CSPELL_VERSION = "latest"

BASE_DIR      := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
SRC_DIR 		:= $(BASE_DIR)/src
APP_DIR         := $(SRC_DIR)/app
SCRIPTS_DIR     := $(BASE_DIR)/scripts

# Calibration file path (MUST be set when calling relevant targets)
# Example: make calibrate-umik F="path/to/cal.txt"
F ?=
OUT ?=

SILENT ?=
HELP   ?=

SAMPLE_RATE		?= 48000
NUM_TAPS    	?= 1024
BUFFER_SECONDS  ?= 3

# Styling
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
NC     := \033[0m # No Color

.PHONY: all default help clean clean-all venv install lint format check test list-audio-devices get-umik-id calibrate-umik spell-check decibel-meter decibel-meter-default-mic decibel-meter-umik-1 record record-default-mic record-umik-1 test coverage

default: help

help: ## Show this help message.
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

all: install ## Install project dependencies.

clean: ## Remove cache
	@.venv/bin/pip cache purge
	@find . -name "*.pyc" | xargs rm -rf
	@find . -name "*.pyo" | xargs rm -rf
	@find . -name "__pycache__" -type d | xargs rm -rf
	@find . -name "*.coverage" | xargs rm -rf

clean-all: clean ## Remove temporary files and directories.
	@echo -e "$(GREEN)>>> Cleaning up...$(NC)"
	@rm -rf .venv
	@rm -rf .ruff_cache
	@rm -rf .pytest_cache
	@rm -rf .mypy_cache
	@rm -rf build dist *.egg-info
	@echo -e "$(GREEN)>>> Cleanup complete.$(NC)"

venv: ## Create a virtual environment.
	@echo -e "$(GREEN)>>> Creating virtual environment in .venv...$(NC)"
	@python3 -m venv .venv
	@echo -e "$(GREEN)>>> Virtual environment created. Activate with 'source .venv/bin/activate'$(NC)"
	@echo -e "$(GREEN)>>> Now run 'make install'$(NC)"

setup: ## Install system dependencies.
	@echo -e "$(GREEN)>>> Installing system dependencies...$(NC)"
	@sudo apt update && sudo apt install -y libportaudio2 libsndfile1 -y
	@echo -e "$(GREEN)>>> System dependencies installed.$(NC)"

install: setup venv ## Install project dependencies from pyproject.toml
	@echo -e "$(GREEN)>>> Installing production dependencies...$(NC)"
	# Change the line below to include the dev group
	@$(UV) sync --extra dev
	@echo -e "$(GREEN)>>> All dependencies installed.$(NC)"
	@$(UV) lock
	@echo -e "$(GREEN)>>> Lock file updated.$(NC)"

lock: ## Update the lock file for dependencies.
	@echo -e "$(GREEN)>>> Updating lock file...$(NC)"
	@$(UV) lock
	@echo -e "$(GREEN)>>> Lock file updated.$(NC)"

lint: ## Check code style and errors with Ruff.
	@echo -e "$(GREEN)>>> Running Ruff linter...$(NC)"
	@$(PYTHON) -m ruff check $(SRC_DIR)

format: ## Format code with Ruff formatter.
	@echo -e "$(GREEN)>>> Running Ruff formatter...$(NC)"
	@$(PYTHON) -m ruff format $(SRC_DIR)
	@$(PYTHON) -m ruff check $(SRC_DIR) --fix

check: lint test ## Run all checks.
	@echo -e "$(GREEN)>>> All checks passed.$(NC)"

test: ## Run unit tests with pytest.
	@echo -e "$(GREEN)>>> Running tests...$(NC)"
	@$(PYTHON) -m pytest

coverage: ## Run tests and generate coverage report.
	@echo -e "$(GREEN)>>> Running tests with coverage...$(NC)"
	@$(PYTHON) -m pytest --cov=src --cov-report=term-missing --cov-report=html

spell-check: ## Spell check project.
	@echo -e "$(GREEN)*** Checking project for miss spellings... ***$(NC)"
	@grep . cspell.txt | sort -u > .cspell.txt && mv .cspell.txt cspell.txt
	@docker run --quiet -v ${PWD}:/workdir ghcr.io/streetsidesoftware/cspell:$(CSPELL_VERSION) lint -c cspell.json --no-progress --unique $(SRC_DIR) *.md
	@echo -e "$(GREEN)*** Project is correctly written! ***$(NC)"

#==========================================================
# Audio Application Targets
#==========================================================

list-audio-devices: ## List available audio input devices.
ifeq ($(SILENT),)
	@echo -e "$(GREEN)>>> Listing audio input devices...$(NC)"
endif
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/umik-1/list_audio_devices.py

get-umik-id: ## Attempt to find and print the ID of the UMIK-1 microphone. Use SILENT=1 for raw output.
ifeq ($(SILENT),)
	@echo -e "$(GREEN)>>> Searching for UMIK-1 device ID...$(NC)"
endif
	# if device not found, return empty string
	@echo -n $(shell
	@$(MAKE) list-audio-devices SILENT=$(SILENT) | grep -i "UMIK-1" | awk '{ print $$2 }' |)
calibrate-umik:  ## Run the calibration test script.
ifndef F
	$(error Calibration file path not set. Use 'make calibrate-umik F="<path/to/calibration_file.txt>" [SAMPLE_RATE=...] [NUM_TAPS=...]')
endif
	@echo -e "$(GREEN)--- Running Calibration Test ---$(NC)"
	@echo "Calibration File: ${F}"
	@echo "Sample Rate     : ${SAMPLE_RATE}"
	@echo "Number of Taps  : ${NUM_TAPS}"
	@echo "--------------------------------"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(APP_DIR)/calibrate.py "${F}" -r ${SAMPLE_RATE} -t ${NUM_TAPS}

#==========================================================
# YAMNet Model Download Targets
#==========================================================

yamnet-model: ## Download the YAMNet model and class map.
	@echo -e "$(GREEN)>>> Downloading YAMNet model and class map...$(NC)"
	@mkdir -p .yamnet
	@wget https://storage.googleapis.com/audioset/yamnet_class_map.csv || wget https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv
	@mkdir -p .yamnet/class_map
	@cp yamnet_class_map.csv .yamnet/class_map
	@rm yamnet_class_map.csv
	@wget "https://tfhub.dev/google/yamnet/1?tf-hub-format=compressed" -O yamnet.tar.gz
	@mkdir -p .yamnet/model
	@tar -zxvf yamnet.tar.gz -C .yamnet/model
	@rm yamnet.tar.gz
	@echo -e "$(GREEN)>>> YAMNet model and class map downloaded to .yamnet/$(NC)"

#==========================================================
# Raspberry Pi Specific Targets
#==========================================================

fan-test:
	@echo -e "$(GREEN)>>> Running Raspberry Pi fan test...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/raspberrypi/fan_test.py
	@echo -e "$(GREEN)>>> Fan test complete.$(NC)"

#==========================================================
# Decibel Meter Targets
#==========================================================

decibel-meter: decibel-meter-umik-1 ## Run the decibel meter using the UMIK-1 (Default alias)

decibel-meter-umik-1: ## Run the decibel meter using the UMIK-1. Requires F=<cal_file>. Use HELP=--help for usage.
ifeq ($(HELP),--help)
	@echo -e "$(YELLOW)>>> Showing help for decibel_meter.py...$(NC)"
	@$(PYTHON) $(SCRIPTS_DIR)/audio/decibel_meter.py --help
else
	@echo -e "$(YELLOW)>>> Attempting to run Decibel Meter with UMIK-1...$(NC)"
	$(eval ID := $(shell $(MAKE) get-umik-id SILENT=1))
	@if [ -z "$(ID)" ]; then \
		echo -e "$(RED)>>> ERROR: Could not automatically find UMIK-1 device ID.$(NC)"; \
		echo -e "$(YELLOW)    Please check 'make list-audio-devices' and ensure the microphone is connected.$(NC)"; \
		exit 1; \
	fi
ifndef F
	$(error Calibration file path not set. Use 'make decibel-meter-umik-1 F="<path/to/calibration_file.txt>"')
endif
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/audio/decibel_meter.py $(HELP) --device-id $(ID) --buffer-seconds $(BUFFER_SECONDS) --calibration-file "$(F)" --num-taps ${NUM_TAPS}
endif

decibel-meter-default-mic: ## Run the decibel meter using the system default microphone. Use HELP=--help for usage.
ifeq ($(HELP),--help)
	@echo -e "$(YELLOW)>>> Showing help for decibel_meter.py...$(NC)"
	@$(PYTHON) $(SCRIPTS_DIR)/audio/decibel_meter.py --help
else
	@echo -e "$(YELLOW)>>> Running Decibel Meter with default system microphone...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/audio/decibel_meter.py $(HELP) --buffer-seconds $(BUFFER_SECONDS)
endif

#==========================================================
# Audio Recording Targets
#==========================================================

record: record-umik-1 ## Record audio using the UMIK-1 (Default alias)

record-umik-1: ## Record audio using the UMIK-1. Requires F=<cal_file>
ifeq ($(HELP),--help)
	@echo -e "$(YELLOW)>>> Showing help for record.py...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/audio/record.py --help
else
	@echo -e "$(YELLOW)>>> Attempting to record with UMIK-1...$(NC)"
	$(eval ID := $(shell $(MAKE) get-umik-id SILENT=1))
	@if [ -z "$(ID)" ]; then \
		echo -e "$(RED)>>> ERROR: Could not automatically find UMIK-1 device ID.$(NC)"; \
		echo -e "$(YELLOW)    Please check 'make list-audio-devices' and ensure the microphone is connected.$(NC)"; \
		exit 1; \
	fi
ifndef F
	$(error Calibration file path not set. Use 'make record-umik-1 F="<path/to/calibration_file.txt>"')
endif
	@echo -e "$(GREEN)>>> Recording to $(OUT)...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/audio/record.py $(HELP) \
		--device-id $(ID) \
		--buffer-seconds $(BUFFER_SECONDS) \
		--calibration-file "$(F)" \
		--num-taps ${NUM_TAPS} 
endif

record-default-mic: ## Record audio using the system default microphone.
ifeq ($(HELP),--help)
	@echo -e "$(YELLOW)>>> Showing help for record.py...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/audio/record.py --help
else
	@echo -e "$(YELLOW)>>> Recording with default system microphone...$(NC)"
	@echo -e "$(GREEN)>>> Recording to $(OUT)...$(NC)"
	@PYTHONPATH=$(BASE_DIR) $(PYTHON) $(SCRIPTS_DIR)/audio/record.py $(HELP) \
		--buffer-seconds $(BUFFER_SECONDS)
endif
