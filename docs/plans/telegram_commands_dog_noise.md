# Plan: `/dog` and `/noise` Telegram Commands + Label Rename

Branch: `feat-edge-monitor` (continue after MFCC work is stable)



## 1. Label rename (dog identification scripts)

Done!



## 2. `/dog` command

**Intent:** manual registration when the system missed a neighbor dog bark (YAMNet mislabelled it).

**Behaviour:**
- Appends one row to `metrics_buffer.csv`
- Schema: `id=uuid, timestamp=now, label=NeighborDog, confidence=1.0, rms=0.0, dbspl=0.0, flux=0.0, cpu=0.0, ram=0.0, temp=0.0, disk=0.0, disk_attached=0.0`
- Replies: `🐕 Neighbor dog registered — HH:MM:SS`
- **No privacy gate** — always works

**Files to touch:**
- `src/app/services/telegram_command_receiver.py` — add `elif cmd == "dog": self._handle_dog()`
- New method `_handle_dog()`:
  ```python
  def _handle_dog(self) -> None:
      row = [str(uuid.uuid4()), datetime.now().strftime(...), "NeighborDog", "1.0",
             "0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "0.0", "0.0"]
      _append_csv(self._csv_path, row)
      self._reply(f"🐕 Neighbor dog registered — {datetime.now().strftime('%H:%M:%S')}")
  ```
- The `_csv_path` needs to be passed in from `main.py` (same path as `SmartBufferSink`).



## 3. `/noise [duration]` command

**Intent:** log a noise disturbance and optionally start an extended top-metrics monitoring session.

### 3a. Immediate entry (always, privacy or not)
- Appends one row to `metrics_buffer.csv`: `label=NoiseEvent, confidence=1.0`, all metrics = 0.0
- This entry always fires regardless of privacy state

### 3b. Extended monitoring session (blocked if privacy is active)

**Activation:**
- Starts a `NoiseMonitorSession` background thread
- Default duration: `3h`. Accepts duration arg: `/noise 1h`, `/noise 30m`
- Uses `_parse_duration()` (already exists in the receiver)
- If a session is already running: **reset the timer** to the new duration
- Reply on start: `🔊 Noise logged. Monitoring for 3h (30s windows) → noise_2026-05-24.csv`
- Reply on end: `📊 Noise monitor done — 360 windows → noise_2026-05-24.csv`

**Privacy interaction:**
- If `/privacy on` is active when `/noise` is sent: immediate entry is logged, monitoring does NOT start. Reply: `🔊 Noise logged. Monitoring blocked (privacy active).`
- If `/privacy on` fires mid-session: session stops silently (no Telegram — privacy is on)
- The `NoiseMonitorSession` thread checks `PrivacyMode().is_active()` before each write

**Session CSV:** one file per day — `recordings/noise_YYYY-MM-DD.csv`

Schema:
```
timestamp, peak_rms, peak_flux, peak_dbspl, cpu, ram, temp, disk
```

Each row = one 30s window summary (peaks tracked by polling `context.metrics` each second).

### 3c. `NoiseMonitorSession` class

```python
class NoiseMonitorSession:
    def __init__(self, context, output_dir, duration_sec, on_done_callback):
        ...

    def start(self): ...          # spawns daemon thread
    def reset(self, duration_sec): ...  # resets timer without stopping thread
    def stop(self): ...           # signals thread to exit

    def _worker(self):
        # poll context.metrics every 1s, track peaks per 30s window
        # write row at end of each window
        # check PrivacyMode before each write
        # call on_done_callback() when timer expires
```

**Files to touch:**
- `src/app/services/telegram_command_receiver.py`:
  - Import `NoiseMonitorSession`
  - Add `self._noise_session: NoiseMonitorSession | None = None`
  - Add `elif cmd == "noise": self._handle_noise(args)`
  - New methods: `_handle_noise()`, `_handle_dog()`
- New file: `src/app/services/noise_monitor_session.py`
- `src/app/main.py`: pass `csv_path` and `output_path` to `TelegramCommandReceiver`



## 4. Architecture changes in `main.py`

`TelegramCommandReceiver` currently receives `context`, `raw_queue`, `upload_queue`.
Add `csv_path` and `output_dir` so the new handlers can write files:

```python
telegram_cmds = TelegramCommandReceiver(
    context=context,
    raw_queue=raw_queue,
    upload_queue=upload_queue,
    csv_path=output_path / "metrics_buffer.csv",   # new
    output_dir=output_path,                         # new
)
```



## 5. Testing

- Unit test `_handle_dog()`: mock `_append_csv`, verify row schema and reply
- Unit test `NoiseMonitorSession`: mock `context.metrics`, verify windows written, privacy stops session, timer reset works
- Add mock values to `_make_sink()` pattern (same as `test_policy_engine_sink.py`) if new settings added



## Implementation order

1. Label rename + JSON migration (20 min)
2. `_append_csv` helper + `/dog` handler (30 min)
3. `NoiseMonitorSession` class (1h)
4. `/noise` handler + privacy wiring (30 min)
5. `main.py` wiring (15 min)
6. Tests (45 min)
