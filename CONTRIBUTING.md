# Contributing to py-edge-ai-acoustic-monitoring-app

Thank you for your interest in contributing! This guide covers how to set up your environment and the expected workflow.

## Prerequisites

- **Python 3.11** — [Download](https://www.python.org/downloads/)
- **uv** — fast Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Make** — standard build tool (pre-installed on Linux/macOS)
- **libportaudio2** and **libsndfile1** — installed automatically by `make setup`

## Setup

```bash
git clone https://github.com/danielfcollier/py-edge-ai-acoustic-monitoring-app.git
cd py-edge-ai-acoustic-monitoring-app

# Install system libraries and create .venv with Python 3.11
make install
```

## Development Workflow

### Code quality

```bash
make lint        # Ruff — check for style violations and errors
make format      # Ruff — autoformat and fix imports
```

All code uses Ruff with `line-length = 120` and targets Python 3.11.

### Testing

```bash
make test        # Run pytest
make coverage    # pytest with term + HTML coverage report
```

Tests live in `tests/` mirroring the `src/app/` structure. Use `unittest.mock.sentinel` for arbitrary pass-through values; use real numeric values only where the code performs arithmetic on them.

### Running locally

```bash
make run               # Auto-detect microphone
make run-default       # Force default PC microphone
make list-devices      # Print available audio input devices
```

### Other targets

```bash
make setup-models      # Download YAMNet TFLite model and class map
make report            # Generate analytics PDF from cloud metrics
make clean             # Remove .pyc / __pycache__
make clean-all         # Also remove .venv and build artifacts
```

Run `make help` to see all targets with descriptions.

## Project Standards

- **Type hints** on all public functions and class methods
- **No comments** unless the _why_ is non-obvious (hidden constraint, workaround, subtle invariant)
- **No module-level magic constants** — config values belong in `settings.py` (`ServiceConfig`, `FeatureExtractorConfig`, etc.)
- **Sentinel values** in tests for arbitrary pass-through data; real values where arithmetic or `if` branches depend on them

## Submitting a Pull Request

1. Create a branch: `git checkout -b feat/my-feature`
2. Keep commits focused; reference the batch/component in the message
3. Ensure `make lint` and `make test` both pass
4. Open a PR against `main`

Happy coding! 🎧
