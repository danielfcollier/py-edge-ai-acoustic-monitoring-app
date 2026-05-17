# Upstream Contribution Candidates (umik-base-app)

The following components built in this app are generic enough to contribute back to the `umik-base-app` library.


### 1. System Telemetry Module (`umik_base_app.utils.system_metrics`)

A robust `SystemMetrics` helper that handles optional `psutil` and reads CPU, RAM, temperature, and disk usage across platforms (Raspberry Pi, generic Linux, macOS).

- `get_stats()` returning `(cpu_percent, ram_percent, temp_celsius, disk_percent, disk_attached_percent)`
- Graceful fallback to `/sys/class/thermal/thermal_zone0/temp` when psutil temperature sensors are unavailable


### 2. SmartBufferSink (`umik_base_app.sinks`)

A generic state-machine recorder sink:

- Pre-roll ring buffer management
- State machine: Idle → Recording → Post-roll fade → Stop
- Pushes `RawEventObject` to a queue; calibration happens downstream
