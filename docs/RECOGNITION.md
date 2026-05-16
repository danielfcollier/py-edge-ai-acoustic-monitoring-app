# Sound Recognition

The app uses **YAMNet** for general-purpose audio classification. See [YAMNET.md](YAMNET.md) for model details.


## Pipeline

Recognition runs in three stages inside the hot path:

1. **SAD Gate** (`SADGatewaySink`) — blocks inference when audio is below the noise floor
2. **Feature Extraction** (`FeatureExtractorSink`) — buffers ~975 ms of audio, resamples to 16 kHz, runs YAMNet
3. **Policy Engine** (`PolicyEngineSink`) — maps the predicted label to actions via YAML rules

## Tuning the SAD Gate

Two-stage filter controlled in `security_policy.yaml`:

```yaml
feature_extractor:
  sad_threshold_rms: 0.002     # Stage 1: minimum signal energy
  sad_threshold_flux: 3.0      # Stage 1: minimum spectral change
  sad_threshold_dbspl: 45.0    # Stage 2: minimum loudness (requires calibrated mic)
```

Stage 2 only activates when the microphone is calibrated (i.e., a calibration file is provided and loaded). Without calibration, only Stage 1 runs.

## Excluding Classes

Add class names to `exclude_classes` to suppress noisy or irrelevant YAMNet outputs:

```yaml
feature_extractor:
  exclude_classes:
    - "Silence"
    - "Inside, small room"
    - "Wind noise"
    - "White noise"
```

Excluded classes have their score zeroed before `argmax` — they will never appear as the top prediction.


## Writing Policy Rules

Rules are evaluated as Python expressions against `PipelineContext` fields:

| Variable | Type | Description |
|---|---|---|
| `current_event_label` | `str` | Top YAMNet class name |
| `current_confidence` | `float` | Score of the top class (0.0–1.0) |
| `metrics["rms"]` | `float` | Signal RMS |
| `metrics["dbspl"]` | `float` | Calibrated dB SPL (0 if uncalibrated) |
| `metrics["flux"]` | `float` | Spectral flux |

Example rule:

```yaml
policies:
  - name: "Loud Dog Bark"
    condition: "current_event_label == 'Dog' and current_confidence > 0.65 and metrics['dbspl'] > 55"
    actions: ["telegram_alert", "record_evidence", "cloud_upload"]
    ignore_privacy: false
```

Available actions: `telegram_alert`, `record_evidence`, `cloud_upload`, `log_metadata`.
