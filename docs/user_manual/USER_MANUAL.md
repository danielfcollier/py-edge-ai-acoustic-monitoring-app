# 🎙️ Edge Acoustic Monitor — User Manual

This manual covers everything you need to configure, run, and operate the Edge Acoustic Monitor after the software is installed.

## Table of Contents

- [Edge Acoustic Monitor — User Manual](#edge-acoustic-monitor--user-manual)
  - [Table of Contents](#table-of-contents)
  - [1. 🔐 Credentials — `.env` file](#1-credentials--env-file)
  - [2. 📋 Policy File — `security_policy.yaml`](#2-policy-file--security_policyyaml)
    - [2.1 🔧 Variables block](#21-variables-block)
    - [2.2 🖥️ Hardware](#22-hardware)
    - [2.3 🧠 Feature Extractor (AI + SAD)](#23-feature-extractor-ai--sad)
    - [2.4 ⚙️ Services](#24-services)
    - [2.5 ☁️ Cloud](#25-cloud)
    - [2.6 🛡️ Policies (detection rules)](#26-policies-detection-rules)
      - [Policy fields](#policy-fields)
      - [Variables available in `condition`](#variables-available-in-condition)
      - [Available actions](#available-actions)
      - [Condition expression rules](#condition-expression-rules)
      - [Example policies](#example-policies)
    - [2.7 📈 Reporting](#27-reporting)
  - [3. 🚀 Running the App](#3-running-the-app)
    - [CLI options](#cli-options)
    - [Run modes](#run-modes)
    - [What to expect at startup](#what-to-expect-at-startup)
  - [4. 📱 Telegram Bot](#4-telegram-bot)
    - [4.1 Setting up the bot](#41-setting-up-the-bot)
    - [4.2 Commands reference](#42-commands-reference)
      - [`/privacy` — control privacy mode](#privacy--control-privacy-mode)
      - [`/status` — system snapshot](#status--system-snapshot)
    - [4.3 Alerts](#43-alerts)
  - [5. 💓 Health Monitoring](#5-health-monitoring)
    - [GPIO heartbeat](#gpio-heartbeat)
    - [healthchecks.io dead-man's switch](#healthchecksio-dead-mans-switch)
  - [6. 📊 Prometheus & Grafana](#6-prometheus--grafana)
    - [Metrics reference](#metrics-reference)
    - [Prometheus scrape config](#prometheus-scrape-config)
    - [Grafana dashboard](#grafana-dashboard)
  - [7. 🪵 Log Levels](#7-log-levels)
  - [8. 🔧 Troubleshooting](#8-troubleshooting)
    - [Telegram not working](#telegram-not-working)
    - [No audio events detected](#no-audio-events-detected)
    - [Privacy mode not clearing after reboot](#privacy-mode-not-clearing-after-reboot)
    - [Upload queue keeps growing](#upload-queue-keeps-growing)
    - [Disk is filling up](#disk-is-filling-up)
    - [Checking logs on the device](#checking-logs-on-the-device)


## 1. Credentials — `.env` file 🔐

All secrets live in a `.env` file (never committed to git). Copy `.env.example` and fill in the values:

```ini
# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789

# ── Cloud Storage ─────────────────────────────────────────────────────────────
# Magalu Object Storage (S3-compatible, default provider)
MAGALU_KEY=your_access_key
MAGALU_SECRET=your_secret_key

# If you switch to AWS S3 (set services.cloud.provider: aws in YAML):
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION=us-east-1

# If you switch to GCP (set services.cloud.provider: gcp in YAML):
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json

# ── Health Monitoring ─────────────────────────────────────────────────────────
HC_PING_URL=https://hc-ping.com/your-uuid-here

# ── Log Levels (optional, defaults shown) ────────────────────────────────────
LOG_LEVEL_MAIN=INFO
LOG_LEVEL_POLICY_ENGINE=DEBUG
LOG_LEVEL_FEATURE_EXTRACTOR=DEBUG
LOG_LEVEL_SMART_RECORDER=DEBUG
LOG_LEVEL_TELEGRAM=INFO
LOG_LEVEL_SERVICES=INFO
```

**Finding your Telegram IDs**

- `TELEGRAM_TOKEN` — create a bot via [@BotFather](https://t.me/BotFather), it gives you the token.
- `TELEGRAM_CHAT_ID` — send any message to your new bot, then visit:
  `https://api.telegram.org/bot<TOKEN>/getUpdates`
  The `chat.id` field in the JSON response is your chat ID.

If either credential is missing, the app auto-disables Telegram at startup (logged as a warning).


## 2. Policy File — `security_policy.yaml` 📋

This is the main configuration file. Pass it with `--config path/to/file.yaml` (default: `security_policy.yaml` in the working directory).

### 2.1 Variables block 🔧

Define constants here. They are injected by name into any string value elsewhere in the file using Python's `str.format()` syntax (`{name}`). Environment variables from `.env` are also available.

```yaml
variables:
  day_start: 6          # 06:00 AM — used in time-based policy conditions
  night_start: 22       # 10:00 PM
  day_limit: 60.0       # dBSPL threshold for daytime rules
  night_limit: 50.0     # dBSPL threshold for night rules
  calibration_file_path: "src/umik-1/7175488.txt"
```

Any variable you define here can be referenced as `{variable_name}` in any string value in the rest of the file. Environment variables (like `{HC_PING_URL}`) are also substituted automatically.


### 2.2 Hardware 🖥️

```yaml
hardware:
  calibration_file: "src/umik-1/7175488.txt"   # path to calibration file (omit for uncalibrated use)
  fir_num_taps: 1024                            # FIR filter length (default: 1024)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calibration_file` | string | `null` | Path to the microphone calibration file. If omitted, no FIR correction is applied and raw audio is used. |
| `fir_num_taps` | int | `1024` | Number of taps in the FIR filter. Higher = more accurate but more CPU. |

**Microphone tiers** — choose what you have:

| Tier | What you get |
|---|---|
| 🖥️ Built-in / system default (`make run-default`) | AI classification + recording. No calibrated dBSPL. |
| 🔌 Generic USB microphone | Same as above with better audio quality. |
| 🎛️ Calibrated measurement mic (set `calibration_file`) | Accurate dBSPL + FIR frequency correction. Auto-detected at startup. |

**Supported calibrated microphones** (auto-detected by USB name):

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

Analog microphones require an external USB audio interface and `--device-id` to select it.


### 2.3 Feature Extractor (AI + SAD) 🧠

Controls the YAMNet inference pipeline and the Sound Activity Detection (SAD) gate.

```yaml
feature_extractor:
  use_tflite: true                  # true = TFLite (default, lightweight); false = full SavedModel (requires --extra full)

  # ── Sound Activity Detection (SAD) gate ────────────────────────────────────
  # Stage 1: Cheap RMS + Flux check (always runs)
  sad_threshold_rms: 0.002          # Minimum RMS amplitude to consider "active"
  sad_threshold_flux: 5.0           # Minimum spectral flux (change rate)
  # Stage 2: dBSPL gate (only if calibration file is loaded)
  sad_threshold_dbspl: 45.0         # Minimum dB SPL to pass to the AI model

  # ── Inference settings (advanced) ─────────────────────────────────────────
  logging_confidence_threshold: 0.3 # Only log labels above this confidence
  exclude_classes:                   # YAMNet classes that are always suppressed
    - "Silence"
    - "Inside, small room"
    - "Wind"
    - "Wind noise"
    - "White noise"
    - "Mouse"
    - "Mechanical fan"
    - "Camera"
    - "Outside, rural or natural"
    - "Mechanisms"
    - "Sound Effect"
    - "Rustling leaves"
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `use_tflite` | bool | `true` | Use the lightweight TFLite runtime (default). Set `false` for the full TF SavedModel — requires `uv sync --extra full`. |
| `sad_threshold_rms` | float | `0.002` | RMS amplitude gate. Frames below this skip AI inference entirely. Increase to suppress more background noise. |
| `sad_threshold_flux` | float | `5.0` | Spectral flux gate. Guards against DC offsets that pass the RMS check despite silence. |
| `sad_threshold_dbspl` | float | `45.0` | dBSPL gate. Only active when a calibration file is loaded. Frames below this dBSPL do not reach YAMNet. |
| `logging_confidence_threshold` | float | `0.3` | Minimum confidence for a label to appear in logs. Does not affect policy evaluation (policies set their own thresholds). |
| `exclude_classes` | list | (see above) | YAMNet class names to always suppress. Labels in this list are treated as `"Silence"` — they are logged but never trigger policies. |

**Tuning guidance**

- If the device is detecting phantom events in a quiet room, raise `sad_threshold_rms` from `0.002` to `0.005`–`0.01`.
- If you are in a noisy environment and missing real events, lower `sad_threshold_dbspl` from `45.0` to `40.0`.
- Add noisy classes (e.g., `"Conversation"`, `"Speech"`) to `exclude_classes` if they generate too many false positives for your use case.

### 2.4 Services ⚙️

```yaml
services:
  internet_enabled: true
  telegram_enabled: true
  cloud_storage_enabled: true

  recording_output_path: "recordings"       # where WAV evidence files are stored locally
  save_calibrated_wave: false               # true = save FIR-corrected audio (larger files)

  max_pending_uploads: 50                   # max items in the raw/upload queues before blocking
  recording_max_seconds: 60                 # hard cap on any single recording
  recording_post_roll_seconds: 10           # seconds to keep recording after event ends

  alert_cooldown_seconds: 60               # minimum seconds between Telegram alerts per rule
  retry_attempts: 3                         # upload retry count
  retry_delay_seconds: 5                    # seconds between upload retries

  heartbeat_interval_seconds: 60           # how often system stats are logged to CSV
  gpio_heartbeat_pin: 17                   # GPIO pin for LED heartbeat blink
  hc_ping_url: "{HC_PING_URL}"             # healthchecks.io URL (injected from .env)

  day_start_hour: "{day_start}"            # used to determine is_day / is_night in policies
  night_start_hour: "{night_start}"

  privacy_mode_state_file: "/dev/shm/privacy_mode"  # runtime state file — survives in-process restarts

  dbspl_silence_level: 30.0               # reported dBSPL when mic is uncalibrated (metrics floor)

  metrics_csv_buffer_file: "metrics_buffer.csv"
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `internet_enabled` | bool | `true` | Master switch. If `false`, cloud upload and Telegram are skipped regardless of their own settings. |
| `telegram_enabled` | bool | `true` | Enable Telegram alerts and command receiver. Auto-set to `false` if credentials are missing. |
| `cloud_storage_enabled` | bool | `true` | Enable cloud upload via `CloudUploaderService`. |
| `recording_output_path` | path | `"recordings"` | Directory where local WAV evidence files and CSV logs are written. Created automatically. |
| `save_calibrated_wave` | bool | `false` | Save the FIR-corrected WAV instead of raw. Corrected files are larger but acoustically accurate. |
| `max_pending_uploads` | int | `50` | Maximum depth of each queue (raw and upload). If full, `SmartBufferSink` drops new recordings. |
| `recording_max_seconds` | int | `60` | Maximum length of any single evidence recording. |
| `recording_post_roll_seconds` | int | `10` | How many extra seconds to record after the policy stops matching (prevents abrupt cut-off). |
| `alert_cooldown_seconds` | int | `60` | Minimum gap between consecutive Telegram alerts for the same rule. Recording/upload actions are never gated by this. |
| `retry_attempts` | int | `3` | Number of upload retries before an evidence file is moved to a local failure queue. |
| `retry_delay_seconds` | int | `5` | Seconds to wait between retry attempts. |
| `heartbeat_interval_seconds` | int | `60` | How often `SystemHeartbeatService` appends a row to the metrics CSV and pings healthchecks.io. |
| `gpio_heartbeat_pin` | int | `17` | GPIO pin number for the LED heartbeat (Linux only). Set to `null` to disable. |
| `hc_ping_url` | string | `null` | healthchecks.io or similar dead-man's switch URL. Pinged on each heartbeat. |
| `day_start_hour` | int | `6` | Hour (0–23) when daytime begins. Used by `is_day` / `is_night` in policy conditions. |
| `night_start_hour` | int | `22` | Hour (0–23) when nighttime begins. |
| `privacy_mode_state_file` | path | `/dev/shm/privacy_mode` | Where privacy mode state is persisted. `/dev/shm` is a RAM disk — the file is lost on reboot (intended). Change to a real path for a persistent privacy flag. |
| `dbspl_silence_level` | float | `30.0` | The dBSPL value reported when no calibration file is loaded. This is a floor value for metrics only. |


### 2.5 Cloud ☁️

```yaml
services:
  cloud:
    provider: "magalu"            # "magalu" | "aws" | "gcp"
    bucket_name: "acoustic-logs"
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `provider` | string | `"magalu"` | Cloud storage backend. Credentials for the chosen provider must be in `.env`. |
| `bucket_name` | string | `"acoustic-logs"` | S3 bucket (or GCS bucket) where evidence files are uploaded. |

**Provider credential mapping**

| Provider | `.env` variables needed |
|---|---|
| `magalu` | `MAGALU_KEY`, `MAGALU_SECRET` |
| `aws` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` |
| `gcp` | `GOOGLE_APPLICATION_CREDENTIALS` (path to service account JSON) |


### 2.6 Policies (detection rules) 🛡️

Policies are the core of the system. Each rule is evaluated on every audio frame where the SAD gate passes.

```yaml
policies:
  - name: "Critical Intrusion"
    description: "Always alert on glass break, gunshots or screaming."
    condition: >
      current_event_label in ['Glass', 'Gunshot_explosion', 'Shatter', 'Scream']
      and current_confidence > 0.7
    actions:
      - "telegram_alert"
      - "cloud_upload"
    ignore_privacy: true
```

#### Policy fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier shown in logs and Telegram alerts. |
| `description` | string | no | Human-readable description (not used at runtime). |
| `condition` | string (Python expr) | yes | Boolean expression evaluated against the current audio frame. See variables below. |
| `actions` | list | yes | One or more actions to trigger when the condition is true. |
| `ignore_privacy` | bool | no (default `false`) | If `true`, this rule fires even when privacy mode is active. Use only for critical safety events. |

#### Variables available in `condition`

| Variable | Type | Example | Description |
|---|---|---|---|
| `current_event_label` | str | `"Dog"` | Top YAMNet classification label for this frame. |
| `current_confidence` | float | `0.87` | Confidence score for the top label (0.0–1.0). |
| `metrics['dbspl']` | float | `58.3` | Current sound pressure level in dB SPL. |
| `metrics['rms']` | float | `0.012` | RMS amplitude of the audio frame. |
| `metrics['flux']` | float | `7.4` | Spectral flux (frame-to-frame spectral change). |
| `current_hour` | int | `14` | Current local hour (0–23). |
| `is_day` | bool | `True` | True between `day_start_hour` and `night_start_hour`. |
| `is_night` | bool | `False` | Opposite of `is_day`. |

#### Available actions

| Action | Description |
|---|---|
| `telegram_alert` | Send an immediate Telegram message to `TELEGRAM_CHAT_ID`. Subject to `alert_cooldown_seconds`. |
| `record_evidence` | Capture the audio event as a WAV file in `recording_output_path`. |
| `cloud_upload` | Upload the WAV and metadata CSV to cloud storage. |
| `log_metadata` | Write a metadata CSV entry without uploading audio. |

> **Recording note**: `record_evidence` and `cloud_upload` are never gated by `alert_cooldown_seconds` — they fire on every matched frame so `SmartBufferSink` keeps the recording alive for the full duration of the event.

#### Condition expression rules

- Standard Python boolean expressions only.
- Only the variables listed above are in scope (`__builtins__` is disabled for safety).
- Use multi-line YAML block scalars (`>`) for long conditions.
- Variables from the `variables:` block are substituted at load time, not at eval time — so `{day_start}` becomes `6` in the condition string.

#### Example policies

```yaml
policies:
  # Critical — always fires, even in privacy mode
  - name: "Glass Break"
    condition: "current_event_label in ['Glass', 'Shatter'] and current_confidence > 0.7"
    actions: ["telegram_alert", "cloud_upload"]
    ignore_privacy: true

  # Alert + record dog barks during the day with high confidence
  - name: "Dog Bark"
    condition: >
      is_day and
      current_event_label in ['Dog', 'Bark'] and
      current_confidence > 0.6
    actions: ["telegram_alert", "record_evidence", "cloud_upload"]
    ignore_privacy: false

  # Silently record any loud night-time event above 50 dBSPL
  - name: "Night Noise"
    condition: "is_night and metrics['dbspl'] > 50.0"
    actions: ["record_evidence", "cloud_upload"]
    ignore_privacy: false
```


### 2.7 Reporting 📈

Used by the offline PDF report generator (`make report`). Does not affect real-time monitoring.

```yaml
reporting:
  days_to_report: 30          # how many days of history to include in the report

  limits:
    day_db: 60.0              # reference dBSPL line drawn on day charts
    night_db: 50.0            # reference dBSPL line drawn on night charts

  category_mapping:           # maps YAMNet labels to report categories
    "Glass": "Security"
    "Scream": "Security"
    "Speech": "Vocals"
    "Music": "Nuisance"
    "Dog": "Nature"
```


## 3. Running the App 🚀

```bash
# Auto-detect UMIK-1 microphone
make run

# Use the system default microphone (laptop / dev machine)
make run-default

# Explicit arguments
uv run edge-monitor-run --config security_policy.yaml --env .env
```

### CLI options

| Flag | Default | Description |
|---|---|---|
| `--config PATH` | `security_policy.yaml` | Path to the YAML policy file. |
| `--env PATH` | `.env` | Path to the credentials file. |

Additional flags are passed through to the underlying `umik-base-app` (`--device`, `--run-mode`, `--zmq-host`, etc.). Run `uv run edge-monitor-run --help` for the full list.

### Run modes

The underlying `umik-base-app` supports three run modes, which you can select by passing `--run-mode`:

| Mode | Description |
|---|---|
| `monolithic` | (default) One process handles audio capture, AI inference, recording, and upload. |
| `producer` | Audio capture and AI inference only. Sends raw buffers via ZMQ. |
| `consumer` | Receives ZMQ buffers and handles recording + upload. |

For most deployments, use the default `monolithic` mode.

### What to expect at startup

```
2026-05-13 10:00:00 [INFO] Loading secrets from .env
2026-05-13 10:00:00 [INFO] 🚀 Initializing in [MONOLITHIC] mode
2026-05-13 10:00:00 [INFO] 📱 Telegram Command Receiver started.
2026-05-13 10:00:00 [INFO] 🧠 Policy Engine Initialized. Loaded 3 rules.
2026-05-13 10:00:00 [INFO] 🎤 Injecting Calibration File: 'src/umik-1/7175488.txt'
```

If the AI models are not present, the app downloads them on first run:
```
2026-05-13 10:00:00 [INFO] ⬇️ First run detected. Downloading AI models...
```

## 4. Telegram Bot 📱

### 4.1 Setting up the bot 🤖

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow the prompts, and copy the **token**.
3. Start a conversation with your new bot (send it any message).
4. Retrieve your `TELEGRAM_CHAT_ID` by visiting:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Look for `"chat": {"id": 123456789}` in the response.
5. Add both values to your `.env` file.

The bot only accepts commands from the configured `TELEGRAM_CHAT_ID`. Messages from any other chat ID are silently ignored.

### 4.2 Commands reference 📖

#### `/privacy` — control privacy mode

Privacy mode suppresses all policies where `ignore_privacy: false`. Use it when you need to move around the monitored space without generating recordings.

| Command | Description |
|---|---|
| `/privacy on` | Activate privacy mode for **4 hours** (default — no duration needed) |
| `/privacy on <duration>` | Activate for a custom duration |
| `/privacy off` | Deactivate immediately |
| `/privacy status` | Report current state (active/inactive, remaining time if active) |

**Duration formats for `/privacy on`**

| Format | Example | Meaning |
|---|---|---|
| `Nh` | `2h` | N hours |
| `Nm` | `30m` | N minutes |
| `Nd` | `1d` | N days |
| plain number | `45` | N minutes (default unit) |

Examples:
```
/privacy on          → 🔒 Privacy mode ON for 4 hours. (default)
/privacy on 2h       → 🔒 Privacy mode ON for 2 hours.
/privacy on 30m      → 🔒 Privacy mode ON for 30 minutes.
/privacy on 1d       → 🔒 Privacy mode ON for 1 day.
/privacy on 45       → 🔒 Privacy mode ON for 45 minutes.
/privacy off         → 🔓 Privacy mode OFF.
/privacy status      → 🔒 Privacy mode is active. (or 🔓 inactive)
```

> Rules with `ignore_privacy: true` (e.g. glass break, gunshots) always fire regardless of privacy state.

#### `/status` — system snapshot

Returns a summary of the device's current state without requiring SSH access.

```
/status
```

Example reply:
```
📊 Edge Monitor

👂 Dog (0.87)
🔓 Privacy: inactive

📤 Raw: 0 · Upload: 2
🖥️ CPU 23% · RAM 41% · 48°C
💾 Disk 38%
```

| Field | Meaning |
|---|---|
| `👂 Label (confidence)` | Last AI classification seen by the pipeline. |
| `🔒/🔓 Privacy` | Current privacy mode state. |
| `📤 Raw · Upload` | Depth of the raw-audio queue and the upload queue. Non-zero values indicate the background workers are busy processing or uploading. |
| `🖥️ CPU · RAM · °C` | Processor load, memory usage, and CPU temperature. |
| `💾 Disk` | Root filesystem usage. |

### 4.3 Alerts 🚨

When a policy with `telegram_alert` in its `actions` fires, you receive a message like:

```
🚨 Policy Triggered
🛡️ Rule: Critical Intrusion
👂 Detected: Glass (0.91)
```

**Cooldown**: consecutive alerts from the same rule are throttled by `alert_cooldown_seconds` (default: 60 s). If a dog barks for 5 minutes, you get one alert per minute — not one per audio frame.


## 5. Health Monitoring 💓

The device supports two health-check mechanisms that run independently of audio processing.

### GPIO heartbeat 🔴

A GPIO pin (default: pin 17) is toggled on each heartbeat interval. Connect an LED (with a current-limiting resistor) to this pin and GND to get a visual indicator that the process is alive.

To disable, set `gpio_heartbeat_pin: null` in the YAML.

### healthchecks.io dead-man's switch ☠️

Set `hc_ping_url` to a [healthchecks.io](https://healthchecks.io) (or compatible) check URL. The app pings this URL on every heartbeat interval. If the device goes offline or the process crashes, healthchecks.io sends you an alert after the expected ping is missed.

```ini
# .env
HC_PING_URL=https://hc-ping.com/your-uuid-here
```

```yaml
# security_policy.yaml
services:
  hc_ping_url: "{HC_PING_URL}"
  heartbeat_interval_seconds: 60
```


## 6. Prometheus & Grafana 📊

The app exposes real-time telemetry on **port 8000** (Prometheus HTTP). Metrics are buffered using a max-hold pattern — transient peaks between scrapes are never missed.

### Metrics reference

| Metric | Description |
|---|---|
| `audio_dbspl` | Peak dBSPL — **only published when a calibrated mic is connected**; gaps in the graph mean uncalibrated mode |
| `audio_rms` | Peak RMS amplitude since last scrape |
| `audio_spectral_flux` | Spectral flux (change intensity — spikes = sudden sound events) |
| `ai_confidence` | Max AI classification confidence |
| `audio_event_count_total{category}` | Cumulative event counter per policy rule category |
| `system_cpu_usage` | CPU % (updated every heartbeat interval) |
| `system_ram_usage` | RAM % |
| `system_temp_celsius` | CPU temperature in °C |
| `system_disk_usage` | Root disk % |
| `system_disk_attached_usage` | Attached storage % (e.g. USB SSD) |

### Prometheus scrape config

Add this job to your `prometheus.yml` and reload Prometheus:

```yaml
scrape_configs:
  - job_name: edge-monitor
    static_configs:
      - targets: ["<device-ip>:8000"]
    scrape_interval: 5s
```

### Grafana dashboard

A ready-to-import dashboard is provided at `docs/grafana/edge-monitor-dashboard.json`.

**Import steps:**
1. Open Grafana → **Dashboards** → **Import**
2. Upload `edge-monitor-dashboard.json`
3. In the datasource dropdown, map `DS_PROMETHEUS` to your Prometheus instance
4. Click **Import**

The dashboard has three rows:
- 🎙️ **Audio Acoustics** — dBSPL gauge (calibrated mic only), RMS gauge, spectral flux, history chart
- 🧠 **AI Classification** — confidence meter, history, events-by-category bar chart
- 🖥️ **System Health** — CPU / RAM / temp / disk gauges + history


## 7. Log Levels 🪵

Log verbosity is controlled per-module from `.env`. Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

| `.env` variable | Module |
|---|---|
| `LOG_LEVEL_MAIN` | Main entry point and `umik-base-app` |
| `LOG_LEVEL_POLICY_ENGINE` | `PolicyEngineSink` (rule evaluation, cooldown) |
| `LOG_LEVEL_FEATURE_EXTRACTOR` | `FeatureExtractorSink` (YAMNet inference) |
| `LOG_LEVEL_SMART_RECORDER` | `SmartBufferSink` (recording state machine) |
| `LOG_LEVEL_TELEGRAM` | `TelegramBotClient` (API calls) |
| `LOG_LEVEL_SERVICES` | Background services (heartbeat, uploader) |

For production deployments, set `LOG_LEVEL_POLICY_ENGINE=INFO` and `LOG_LEVEL_FEATURE_EXTRACTOR=INFO` to reduce log volume.


## 8. Troubleshooting 🔧

### Telegram not working

1. Check that `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` are correct in `.env`.
2. Look for the startup warning: `⚠️ Telegram credentials missing. Disabling Telegram Service.`
3. Confirm the bot has been started — you must send it at least one message first.
4. Ensure the device has internet access: `ping api.telegram.org`.

### No audio events detected

1. Confirm the microphone is recognised: `make list-devices`.
2. Check the SAD thresholds — if `sad_threshold_rms` is too high, all frames are silently dropped. Lower it and watch the debug logs.
3. Set `LOG_LEVEL_FEATURE_EXTRACTOR=DEBUG` in `.env` to see per-frame SAD decisions.

### Privacy mode not clearing after reboot

By default, privacy mode is stored in `/dev/shm/privacy_mode`, which is a RAM disk and is cleared on reboot. If you set `privacy_mode_state_file` to a real path (e.g. `/var/lib/edge-monitor/privacy_mode`), the state persists across reboots — send `/privacy off` via Telegram to clear it.

### Upload queue keeps growing

The upload queue depth is shown in `/status`. A non-zero `Upload` value means the background worker is busy. Possible causes:

- Network is offline or slow — the worker retries with `retry_delay_seconds` between attempts.
- Cloud credentials are wrong — check `.env` and look for upload error logs.
- Recordings are too long — lower `recording_max_seconds` or raise the SAD thresholds to reduce recording frequency.

### Disk is filling up

Evidence files are written to `recording_output_path` before upload. If upload fails repeatedly, files accumulate locally. Check available space with `df -lh`. Once uploads succeed, successfully uploaded files are removed from the local directory.

### Checking logs on the device 🖥️

```bash
# Follow live output when run as a systemd service
journalctl -fu edge-monitor

# Show errors since last boot
journalctl -p err -b -u edge-monitor

# Watch the metrics heartbeat CSV grow
tail -f metrics_buffer.csv
```
