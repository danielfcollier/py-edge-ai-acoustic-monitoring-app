# Contributing to AI Acoustic Monitor

This guide covers development setup, project structure, the testing workflow, and the release process.


## Prerequisites

- **Python 3.11** — [Download](https://www.python.org/downloads/)
- **uv** — fast Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Make** — pre-installed on Linux/macOS
- **libportaudio2**, **libsndfile1** — installed automatically by `make setup`


## Setup

```bash
git clone https://github.com/danielfcollier/py-edge-ai-acoustic-monitoring-app.git
cd py-edge-ai-acoustic-monitoring-app

# Install system audio libraries + Python deps in .venv (Python 3.11)
make install

# Download YAMNet TFLite model and class map
make setup-models
```

To run the app in development:

```bash
make run               # auto-detect microphone
make run-default       # force default PC microphone
make list-devices      # print available audio input devices
```

## Architecture

```
Audio Source  (microphone via umik-base-app AudioPipeline)
  ├─ CalibratorAdapter      (FIR + sensitivity gain — if calibration file configured)
  ├─ BasicMetricsSink       (RMS, Flux, dBSPL → context.metrics)
  ├─ SADGatewaySink         (two-stage noise gate — drops silent frames before AI)
  ├─ FeatureExtractorSink   (YAMNet inference → context.current_event_label)
  ├─ PolicyEngineSink       (evaluate YAML rules → context.actions_to_take)
  ├─ TopMetricsSink         (optional, --top-metrics — logs peak metrics summary, no side effects)
  └─ SmartBufferSink        (state-machine recorder → raw_queue)
         │
    raw_queue
         │
  RecorderTransformerWorker (optional FIR calibration → upload_queue)
         │
    upload_queue
         │
  CloudUploaderService      (stream WAV + CSV to cloud storage)

Background services:
  TelegramCommandReceiver   (/privacy, /status commands)
  SystemHeartbeatService    (GPIO blink + healthchecks.io ping)
  HealthMonitorService      (system metrics CSV)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed diagrams.


## Project Structure

```
src/
  app/
    sinks/            # Real-time pipeline stages (AudioSink implementations)
      basic_metrics_sink.py       # RMS, Flux, dBSPL
      sad_gateway_sink.py         # Two-stage noise gate
      feature_extractor_sink.py   # YAMNet inference
      policy_engine_sink.py       # YAML rule evaluation
      top_metrics_sink.py         # Optional calibration aid (--top-metrics)
      smart_buffer_sink.py        # State-machine audio recorder
    services/         # Background workers and integrations
      telegram_bot_client.py
      telegram_command_receiver.py
      cloud_uploader_service.py
      recorder_transformer_worker.py
      system_heartbeat_service.py
    context.py        # PipelineContext — shared state bus between all sinks
    settings.py       # Pydantic settings + YAML policy loader
    main.py           # Entry point — builds pipeline and starts services
  scripts/
    configure.py      # ai-acoustic-monitor wizard (credentials, profile, manual)
    install_services.py  # ai-acoustic-monitor-install-service systemd installer
    setup_yamnet.py   # ai-acoustic-monitor-setup-models model downloader
    generate_report.py
  setup/
    ai-acoustic-monitor.service    # systemd unit templates
    ai-acoustic-monitor-producer.service
    ai-acoustic-monitor-consumer.service

tests/
  sinks/
  services/
  e2e/               # Live-API tests (require credentials in .env)

docs/
  user_manual/       # Profile YAMLs + USER_MANUAL.md
  roadmap/
  grafana/

pyproject.toml       # Project metadata + tool config (ruff, mypy, pytest)
setup.py             # stdeb packaging entry points and data_files
build_deb.sh         # .deb build script
publish_repo.py      # APT repository publisher (S3-compatible)
```


## Code Quality

```bash
make lint        # Ruff — check for style violations
make format      # Ruff — autoformat + fix imports
make check       # lint + test in one step
```

All code targets Python 3.11 with `line-length = 120`.

**Standards:**
- Type hints on all public functions and class methods
- No comments unless the *why* is non-obvious (hidden constraint, subtle invariant, workaround)
- No module-level magic constants — config values belong in `settings.py`
- Sentinel values in tests for arbitrary pass-through data; real values where arithmetic or branches depend on them


## Testing

```bash
make test            # pytest unit tests (excludes e2e)
make test-e2e        # e2e tests — requires credentials in .env
make coverage        # pytest with term + HTML coverage report (saved to reports/)
```

Tests mirror `src/app/` under `tests/`. When mocking `settings`, always provide concrete values for any field the code under test performs arithmetic or comparison on — returning a `MagicMock` where an `int` is expected will cause silent `TypeError`.


## Version & Release

### Bumping the version

Version is tracked in `pyproject.toml` and `src/app/__init__.py`. Use the Makefile targets to keep them in sync:

```bash
make bump-patch    # 0.1.0 → 0.1.1
make bump-minor    # 0.1.0 → 0.2.0
make bump-major    # 0.1.0 → 1.0.0
```

### Building the .deb package

The package vendors all Python dependencies so the installed system only needs `python3.11 + libportaudio2 + libsndfile1`.

```bash
make build-deb     # clean → vendor deps → build .deb (output: deb_dist/*.deb)
make test-deb      # verify the package in a clean Docker container (DISTRO=bookworm|noble)
```

`build_deb.sh` patches the generated `debian/control` to strip Python package dependencies (vendored), fixes the Python 3.11 shebang in entry points via `postinst`, and validates that key entry points and data files are present in the archive.

### Publishing to the APT repository (Magalu Cloud)

```bash
# One-time: add to .env
DEB_S3_BUCKET=your-bucket-name
GPG_KEY_ID=your-gpg-fingerprint
GPG_KEY_FILE=ai-acoustic-monitor.gpg.key
GPG_PUBKEY_FILE=ai-acoustic-monitor.gpg.pub

# Publish a built .deb
make publish-deb BUCKET=your-bucket-name

# Or run the full release in one step (bump + build + publish)
make release                        # defaults to patch bump
make release VERSION_BUMP=minor
```

`publish_repo.py` builds a fully compliant Debian repository layout (pool, dists, Packages.gz, Release, InRelease, Release.gpg) in the S3 bucket. The script is idempotent — re-publishing the same version is a no-op.

**GPG key setup (one-time):**

```bash
gpg --full-generate-key
gpg --armor --export <KEY_ID> > ai-acoustic-monitor.gpg.pub
gpg --armor --export-secret-keys <KEY_ID> > ai-acoustic-monitor.gpg.key
```


## Submitting a Pull Request

1. Branch: `git checkout -b feat/my-feature`
2. Keep commits focused; one logical change per commit
3. `make check` must pass (lint + tests)
4. Open a PR against `main`

Happy coding! 🎧
