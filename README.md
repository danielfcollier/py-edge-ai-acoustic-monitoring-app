# 🎙️ AI Acoustic Monitor

Real-time sound intelligence on any Linux machine — Raspberry Pi, server, or desktop. Classifies 521 sound events using YAMNet AI, applies your detection rules, and sends Telegram alerts with cloud evidence uploads.

No coding required. Configure with a wizard, run as a `systemd` service.


## 📦 Install

```bash
# Add the repository
curl -fsSL https://br-se1.magaluobjects.com/ai-acoustic-monitor/ai-acoustic-monitor/pubkey.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/ai-acoustic-monitor.gpg

echo "deb [signed-by=/usr/share/keyrings/ai-acoustic-monitor.gpg] \
  https://br-se1.magaluobjects.com/ai-acoustic-monitor/ai-acoustic-monitor \
  $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/ai-acoustic-monitor.list

sudo apt-get update && sudo apt-get install ai-acoustic-monitor
```

> **Requirements**: Debian/Ubuntu (`bookworm` or `noble`), `libportaudio2`, `libsndfile1`.
> These are installed automatically as package dependencies.


## 🚀 Setup

Everything goes through the `ai-acoustic-monitor` wizard:

```bash
# 1. Set up Telegram and cloud credentials
ai-acoustic-monitor --configure credentials

# 2. Choose a profile and generate your config file
ai-acoustic-monitor --configure

# 3. Validate that all services are wired up correctly
ai-acoustic-monitor --test

# 4. Install as a systemd service (starts on boot)
sudo ai-acoustic-monitor --install

# View the full user manual at any time
ai-acoustic-monitor --manual
```

**Where files live**

| Step | File | Location |
|---|---|---|
| `--configure credentials` | Credentials | `~/.config/ai-acoustic-monitor/.env` |
| `--configure` | Policy YAML | `./security_policy.yaml` (current directory) |
| `--install` | Copies both to | `/etc/ai-acoustic-monitor/` (service reads from here) |
| `--install` (with calibration) | Calibration file | `/etc/ai-acoustic-monitor/<filename>.txt` |

> If you change anything with the wizard after installation, re-run `sudo ai-acoustic-monitor --install` to apply the update to the service.

### Deployment modes

When you run `--install`, you choose how to deploy:

| Mode | Command | Description |
|---|---|---|
| **Monolith** | `sudo ai-acoustic-monitor --install` | Single process — audio capture, AI, upload. Default. |
| **Distributed** | `sudo ai-acoustic-monitor --install distributed` | Producer captures audio via ZMQ; consumer handles recording and upload. Use when capture device and processing server are separate. |


## 🗂️ Profiles

The wizard offers four ready-made detection profiles. Pick one and customise from there.

### 🏠 Home Security & Peace

Detects break-in sounds at any time, suspicious activity at night, and logs pet behavior.

- Glass break / shatter / gunshot — immediate alert (ignores privacy mode)
- Knocks, footsteps, doors — alert only at night
- Dog barking — silent log for later review

### 👶 Baby / Child Monitor

Tuned for nursery use with low confidence thresholds to catch early distress.

- Baby crying / sobbing / whimpering — immediate alert
- Any sudden loud noise above 70 dBSPL — immediate alert
- Babbling, laughter, speech — logged without alerting

### 🌳 Forest / Outdoor Monitor

Detects human intrusion and machinery in natural or remote outdoor spaces.

- Chainsaw, engine, vehicle sounds — alert and record
- Gunshots or explosions — immediate alert
- Wildlife sounds — logged for behavioral analysis

### 🏭 Industrial / Server Room

Monitors for equipment failure and safety events in machine-heavy environments.

- Fire alarms, sirens, buzzers — immediate alert
- Grinding, hammering, impact sounds — alert and log
- Unusual silence (e.g. fan failure) — alert when dBSPL drops below threshold


## ⚙️ Configuration

The wizard generates a complete `security_policy.yaml`. The most common things to adjust:

### Detection sensitivity

```yaml
feature_extractor:
  sad_threshold_rms: 0.002      # lower = more sensitive to quiet sounds
  sad_threshold_dbspl: 45.0     # minimum dBSPL to pass to AI (calibrated mic only)
```

### Adding or editing a detection rule

```yaml
policies:
  - name: "Dog Bark"
    condition: "current_event_label in ['Dog', 'Bark'] and current_confidence > 0.6"
    actions:
      - "telegram_alert"
      - "record_evidence"
      - "cloud_upload"
    ignore_privacy: false   # set true to fire even when privacy mode is active
```

**Condition variables**

| Variable | Type | Description |
|---|---|---|
| `current_event_label` | str | Top YAMNet classification (e.g. `"Dog"`, `"Glass"`) |
| `current_confidence` | float | Confidence score, 0.0–1.0 |
| `metrics['dbspl']` | float | Sound level in dB SPL (calibrated mic required) |
| `metrics['rms']` | float | RMS amplitude of the audio frame |
| `metrics['flux']` | float | Spectral flux — spikes on sudden sound events |
| `is_day` / `is_night` | bool | Time-of-day driven by `day_start_hour` / `night_start_hour` |

**Available actions**

| Action | Description |
|---|---|
| `telegram_alert` | Send Telegram message (subject to `alert_cooldown_seconds`) |
| `record_evidence` | Capture WAV file to `recording_output_path` |
| `cloud_upload` | Upload WAV + metadata CSV to cloud storage |
| `log_metadata` | Write CSV entry only — no audio capture or upload |

### Offline mode

Set `internet_enabled: false` to run the monitor without any network access. Telegram and cloud upload are disabled. Recordings are saved locally to `recording_output_path`.

```yaml
services:
  internet_enabled: false
  cloud_storage_enabled: false
  telegram_enabled: false
  recording_output_path: "/mnt/usb_ssd/recordings"
```

Use this when the device has no internet connection, is air-gapped, or you want recordings only.

### Storing recordings on external storage

By default recordings go to `./recordings` relative to the working directory. Set an absolute path to write to a USB drive, NFS share, or other storage:

```yaml
services:
  recording_output_path: "/mnt/usb_ssd/recordings"   # USB SSD
  # recording_output_path: "/mnt/nas/ai-acoustic-monitor"   # NFS/CIFS mount
```

The directory is created automatically if it doesn't exist. Make sure the service user has write permission to the mount point.

### Prometheus metrics

Enabled by default on port 8000. Control it in the `services:` block:

```yaml
services:
  prometheus_enabled: true    # set false to disable entirely
  prometheus_port: 8000
```

See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) for the full metrics reference and Grafana dashboard setup.

For the full configuration reference, run `ai-acoustic-monitor --manual`.


## 🔍 Validating your setup

After configuring, run the built-in test suite:

```bash
ai-acoustic-monitor --test
ai-acoustic-monitor --test --config security_policy.yaml --env ~/.config/ai-acoustic-monitor/.env
```

```
════════════════════════════════════════════════════════════════
  🔍 Service Validation  —  security_policy.yaml
════════════════════════════════════════════════════════════════

  ✅ Profile                 3 rule(s), calibration configured
  ✅ Calibration file        src/umik-1/7175488.txt (12 KB)
  ✅ Microphone              UMIK-1 detected (device 3)
  ✅ Sample recording        1.0s, 187 KB
  ✅ Telegram                message sent to chat 123456789
  ✅ Cloud storage           bucket 'acoustic-logs' OK
  ⏭️  Heartbeat (HC ping)   HC_PING_URL not configured (skipped)

────────────────────────────────────────────────────────────────
  All 6 checks passed.
```

## ▶️ Running

```bash
# Run the monitor directly
ai-acoustic-monitor-run --config security_policy.yaml --env ~/.config/ai-acoustic-monitor/.env

# Check the systemd service
sudo systemctl status ai-acoustic-monitor
journalctl -fu ai-acoustic-monitor
```

## 📱 Telegram Bot

Set up a bot via [@BotFather](https://t.me/BotFather) and add the token + chat ID during `ai-acoustic-monitor --configure credentials`.

### Alerts

When a policy fires, you receive:

```
🚨 Policy Triggered
🛡️ Rule: Glass Break
👂 Detected: Glass (0.91)
```

### Commands

| Command | Description |
|---|---|
| `/privacy on` | Suppress non-critical alerts for 4 hours |
| `/privacy on 2h` | Suppress for a specific duration (`Nh`, `Nm`, `Nd`) |
| `/privacy off` | Re-enable all alerts immediately |
| `/privacy status` | Show current state and remaining time |
| `/status` | System snapshot (label, CPU, RAM, temp, disk, queue depth) |
| `/dog` | Register a neighbour dog bark the detector missed |
| `/noise [duration]` | Log a noise disturbance; start 30s-window monitoring (default 3h) |

Rules with `ignore_privacy: true` always fire regardless of privacy state.


## 🎙️ Microphone

The app works with any microphone. A calibrated measurement mic unlocks accurate dBSPL and physics-based triggers.

| Tier | Examples | dBSPL accuracy |
|---|---|---|
| 🖥️ Built-in / system default | Laptop mic | No (AI + RMS/Flux only) |
| 🔌 Generic USB microphone | Any USB mic | No |
| 🎛️ Calibrated measurement mic | See below | ✅ Full calibration |

Calibrated mics are **auto-detected by USB name** when a calibration file is configured — no manual device ID needed.

**Supported calibrated microphones**

| Microphone | Manufacturer | Connection |
|---|---|---|
| UMIK-1 | miniDSP | USB |
| UMIK-2 | miniDSP | USB |
| UMM-6 | Dayton Audio | USB |
| XREF 20 | Sonarworks | USB |
| EMX-7150 | iSEMcon | USB |
| MM 1 | Beyerdynamic | Analog (via interface) |
| M23 / M30 | Earthworks | Analog (via interface) |
| TM1 Plus | Audix | Analog (via interface) |

Set the calibration file path during `ai-acoustic-monitor --configure` or in your YAML:

```yaml
hardware:
  calibration_file: "/etc/ai-acoustic-monitor/7175488.txt"
```


## 💓 Health Monitoring

- **GPIO heartbeat**: connect an LED to the configured pin — it blinks on every heartbeat interval
- **healthchecks.io**: set `HC_PING_URL` in `.env` to get paged when the device goes silent

Both are configured in the `services:` block of your policy YAML and verified by `ai-acoustic-monitor --test`.


## 📖 Further Reading

- **Full configuration reference**: `ai-acoustic-monitor --manual`
- **Sound recognition (YAMNet + MFCC profiles)**: [docs/RECOGNITION.md](docs/RECOGNITION.md)
- **Architecture & internals**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Observability (Prometheus + Grafana)**: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)
- **Contributing / development setup**: [CONTRIBUTING.md](CONTRIBUTING.md)
- **Bug reports**: [GitHub Issues](https://github.com/danielfcollier/py-edge-ai-acoustic-monitoring-app/issues)
