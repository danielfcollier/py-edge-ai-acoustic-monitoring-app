# 🎙️ Edge AI Acoustic Monitoring App

A privacy-aware, edge-deployed acoustic monitoring system that runs on **any Linux machine** — Raspberry Pi, server, or desktop — with a microphone. It uses YAMNet (Google's audio classification model) to identify sound events in real time, applies a configurable security policy, records evidence audio, and uploads to cloud storage — while keeping the hot path fast enough for continuous 24/7 monitoring.

> 🍎 🪟 macOS and Windows may work with minor adjustments — GPIO heartbeat and `/dev/shm` privacy state are Linux-specific features; both can be disabled in config.

## ✨ Features

- 🧠 **Real-time AI classification** via YAMNet (521 sound classes: dog barks, glass breaks, voices, etc.)
- 🚪 **Two-stage Sound Activity Detection (SAD)** gate — cheap RMS/Flux check before expensive AI inference
- 🎛️ **FIR calibration** applied async in a background worker, so the recording pipeline never stalls
- 📋 **YAML-driven policy engine** with per-rule `ignore_privacy` flag for critical events
- 🔒 **Privacy mode** — toggle via Telegram command; state survives in-process restarts (stored in `/dev/shm`)
- 📱 **Telegram bot**: receive alerts _and_ control the device with `/privacy` commands
- ☁️ **Cloud upload** to Magalu Object Storage, AWS S3, or GCP (with offline disk fallback)
- 📊 **Prometheus metrics** export + Grafana-ready dashboards
- 💓 **Health monitoring**: GPIO heartbeat pin + healthchecks.io ping

## 🖥️ Hardware

The app runs on **any Linux machine** — Raspberry Pi, server, or desktop. Storage: USB SSD recommended for recordings.

### 🎙️ Microphone support

Three tiers — pick whatever you have:

| Tier | Examples | What you get |
|---|---|---|
| 🖥️ Built-in / system default | Laptop mic, any system audio input | AI classification + recording; no calibrated dBSPL |
| 🔌 Generic USB microphone | Any USB mic | Same as above with better audio quality |
| 🎛️ Calibrated measurement mic | See table below | Accurate dBSPL + FIR frequency correction |

Calibrated microphones are **auto-detected by name** at startup when a calibration file is configured — no manual device ID needed.

#### Supported calibrated microphones (via `umik-base-app`)

| Microphone | Manufacturer | Connection | Sample Rates |
|---|---|---|---|
| UMIK-1 | miniDSP | USB | 48 kHz |
| UMIK-2 | miniDSP | USB | 48 / 96 / 192 kHz |
| UMM-6 | Dayton Audio | USB | 48 kHz |
| XREF 20 | Sonarworks | USB | 48 kHz |
| EMX-7150 | iSEMcon | USB | 48 / 96 kHz |
| MM 1 | Beyerdynamic | Analog (via interface) | 44.1 / 48 / 96 / 192 kHz |
| M23 | Earthworks | Analog (via interface) | 44.1 / 48 / 96 / 192 kHz |
| M30 | Earthworks | Analog (via interface) | 44.1 / 48 / 96 / 192 kHz |
| TM1 Plus | Audix | Analog (via interface) | 44.1 / 48 / 96 kHz |

Analog microphones require an external USB audio interface and `--device-id` to select the interface.

## 🏗️ Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for diagrams. In brief:

```
Audio Source
  └─ BasicMetricsSink       (RMS, Flux, dBSPL → context.metrics)
  └─ SADGatewaySink         (noise gate — skip AI if silent)
  └─ FeatureExtractorSink   (YAMNet inference → context.current_event_label)
  └─ PolicyEngineSink       (evaluate YAML rules → context.actions_to_take)
  └─ SmartBufferSink        (record raw audio → raw_queue)
        │
   raw_queue
        │
  RecorderTransformerWorker (optional FIR calibration → upload_queue)
        │
   upload_queue
        │
  CloudUploaderService      (stream WAV + CSV to cloud)
```

Background services: `HealthMonitorService`, `SystemHeartbeatService`, `TelegramCommandReceiver`.

## 🚀 Quick Start

### 1. 📦 Install dependencies

```bash
# System audio libraries (Ubuntu/Debian/Raspberry Pi OS)
make setup

# Python dependencies (creates .venv with Python 3.11)
make install
```

### 2. 🧠 Download AI models

```bash
make setup-models
```

### 3. ⚙️ Configure

Copy `.env.example` to `.env` and fill in your credentials:

```ini
# Telegram
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Magalu Object Storage (S3-compatible)
MAGALU_KEY=your_access_key
MAGALU_SECRET=your_secret_key
```

Edit `security_policy.yaml` to set calibration file path, detection thresholds, and rules:

```yaml
hardware:
  calibration_file: "src/umik-1/your_serial.txt"   # path to UMIK-1 calibration file

feature_extractor:
  sad_threshold_rms: 0.002
  sad_threshold_dbspl: 45.0

policies:
  - name: "Dog Bark"
    condition: "current_event_label == 'Dog' and current_confidence > 0.6"
    actions: ["telegram_alert", "record_evidence", "cloud_upload"]
    ignore_privacy: false
```

### 4. ▶️ Run

```bash
# Auto-detect microphone and run
make run

# Run with default system microphone
make run-default

# Run with explicit policy and env files
uv run edge-monitor-run --config security_policy.yaml --env .env
```

## 📱 Telegram Commands

The bot both sends alerts and accepts commands from the configured `TELEGRAM_CHAT_ID`.

| Command | Description |
|---|---|
| `/privacy on` | Activate privacy mode for **4 hours** (default — no duration needed) |
| `/privacy on 2h` | Activate for a custom duration |
| `/privacy on 30m` | Activate for 30 minutes |
| `/privacy on 1d` | Activate for a day |
| `/privacy off` | Deactivate immediately |
| `/privacy status` | Report current state and remaining time |

Supported duration formats: `Nh`, `Nm`, `Nd`, or a plain integer (treated as minutes).

Rules marked `ignore_privacy: true` (e.g. glass break, gunshot) fire regardless of privacy state.

## 📊 Prometheus & Grafana

The app exposes real-time metrics on **port 8000** (Prometheus HTTP server). Metrics are updated every second via a max-hold buffer — short transient peaks between scrapes are never lost.

### Metrics exposed

| Metric | Description |
|---|---|
| `audio_dbspl` | Peak dBSPL since last scrape — **only published when a calibrated mic is connected** |
| `audio_rms` | Peak RMS amplitude |
| `audio_spectral_flux` | Spectral flux (change intensity) |
| `ai_confidence` | Max AI classification confidence |
| `audio_event_count_total{category}` | Cumulative event counter per policy category |
| `system_cpu_usage` | CPU % |
| `system_ram_usage` | RAM % |
| `system_temp_celsius` | CPU temperature |
| `system_disk_usage` | Disk % |
| `system_disk_attached_usage` | Attached disk % |

### Prometheus scrape config

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: edge-monitor
    static_configs:
      - targets: ["<device-ip>:8000"]
```

### Grafana dashboard

Import `docs/grafana/edge-monitor-dashboard.json`:

1. Grafana → **Dashboards** → **Import**
2. Upload `edge-monitor-dashboard.json`
3. Map `DS_PROMETHEUS` to your Prometheus datasource
4. Click **Import**

The dashboard includes three rows: 🎙️ Audio Acoustics, 🧠 AI Classification, 🖥️ System Health.

## 🛠️ Development

```bash
make lint        # Ruff linter
make format      # Ruff autoformat
make test        # pytest (unit tests)
make test-e2e    # pytest (e2e tests, requires credentials)
make coverage    # pytest with HTML coverage report
make report      # Generate PDF analytics report from cloud metrics
make list-devices  # Print available audio input devices
```

### Project structure

```
src/
  app/
    sinks/           # Real-time pipeline stages (AudioSink implementations)
    services/        # Background workers and integrations
    settings.py      # Pydantic settings + YAML policy loader
    context.py       # PipelineContext (shared state bus)
    main.py          # Entry point
  scripts/           # Setup, report generation, ZMQ utilities
tests/
  sinks/
  services/
  e2e/
```

## 📋 Requirements

- Python 3.11
- `uv` — [install](https://github.com/astral-sh/uv)
- `libportaudio2`, `libsndfile1` (installed by `make setup`)
- **AI runtime**: `tflite-runtime` is included in the default install (`use_tflite: true`). Full TensorFlow is only needed for dev/SavedModel mode (`use_tflite: false`): `uv sync --extra full`
