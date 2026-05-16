# Upstream Contribution Candidates (umik-base-app)

The following components built in this app are generic enough to contribute back to the `umik-base-app` library.


### 1. System Telemetry Module (`umik_base_app.utils.system_metrics`)

A robust `SystemMetrics` helper that handles optional `psutil` and reads CPU, RAM, temperature, and disk usage across platforms (Raspberry Pi, generic Linux, macOS).

- `get_stats()` returning `(cpu_percent, ram_percent, temp_celsius, disk_percent, disk_attached_percent)`
- Graceful fallback to `/sys/class/thermal/thermal_zone0/temp` when psutil temperature sensors are unavailable

### 2. PipelineContext / Event Bus

A typed dataclass shared between pipeline sinks: `current_event_label`, `current_confidence`, `should_infer`, `metrics`, `actions_to_take`, `audio_pre_buffer`. Decouples sink dependencies from positional arguments.


### 3. SmartBufferSink (`umik_base_app.sinks`)

A generic state-machine recorder sink:

- Pre-roll ring buffer management
- State machine: Idle → Recording → Post-roll fade → Stop
- Pushes `RawEventObject` to a queue; calibration happens downstream


### 4. Cloud Storage Abstractions (`umik_base_app.drivers.cloud`)

Clean interfaces for object storage:

- `CloudStorageProvider` protocol
- Concrete implementations: `S3Provider` (boto3, covers AWS + Magalu/MinIO), `GCPStorageProvider`
- Provider selection by config string (`"magalu"`, `"aws"`, `"gcp"`)

### 5. Microphone Auto-Detection (`umik_base_app.hardwares`)

Extend the `Selector` class to match a connected USB device name against the serial number embedded in a calibration file path.

- `find_device_by_calibration_file(path)` — zero-config deployment for UMIK-1 and similar
