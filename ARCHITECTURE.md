# Architecture

## Overview

The system separates real-time latency-sensitive work (the "hot path") from heavy background processing (the "cold path").

- **Hot path** — runs synchronously on every ~100 ms audio chunk. Must stay fast. No FIR convolution.
- **Cold path** — background threads. Handles FIR calibration, cloud upload, and Telegram I/O.

## Hot Path: Real-Time Audio Pipeline

Five sinks execute in order on every chunk. Each sink reads/writes the shared `PipelineContext`.

```mermaid
flowchart TD
    Source[Audio Source] --> M[BasicMetricsSink]
    M --> |"context.metrics: rms, flux, dBSPL"| S[SADGatewaySink]
    S --> |"context.should_infer"| F[FeatureExtractorSink]
    F --> |"context.current_event_label"| P[PolicyEngineSink]
    P --> |"context.actions_to_take"| B[SmartBufferSink]

    style M fill:#e8f5e9,stroke:#388e3c
    style S fill:#e8f5e9,stroke:#388e3c
    style F fill:#fff3e0,stroke:#f57c00
    style P fill:#e3f2fd,stroke:#1976d2
    style B fill:#fce4ec,stroke:#c62828
```

| Sink | Responsibility |
|---|---|
| **BasicMetricsSink** | Computes RMS, Flux, dBSPL on every chunk. Maintains pre-roll buffer. Updates Prometheus. |
| **SADGatewaySink** | Two-stage noise gate: Stage 1 (RMS/Flux), Stage 2 (dBSPL if mic is calibrated). Sets `should_infer`. |
| **FeatureExtractorSink** | Runs YAMNet (TFLite or full TF) when `should_infer=True`. Buffers chunks until ~0.975 s is collected. |
| **PolicyEngineSink** | Evaluates YAML policy rules against context metrics. Respects privacy mode (`ignore_privacy` flag). |
| **SmartBufferSink** | State-machine recorder. On trigger: copies pre-roll + live audio into a buffer, pushes `RawEventObject` to `raw_queue` when post-roll expires or max duration is hit. |

## Cold Path: Background Workers

```mermaid
flowchart TD
    B[SmartBufferSink] --> |RawEventObject| RQ[(raw_queue)]
    RQ --> RT[RecorderTransformerWorker]
    RT --> |"save_calibrated_wave?"| Gate{FIR?}
    Gate -- Yes --> FIR[Apply FIR Convolution]
    Gate -- No --> Pass[Pass Raw Audio]
    FIR --> UQ[(upload_queue)]
    Pass --> UQ
    UQ --> CU[CloudUploaderService]
    CU --> |Online| Cloud[Cloud Storage]
    CU --> |Offline| Disk[Disk Fallback]

    style RT fill:#f9f,stroke:#333,stroke-width:2px
    style FIR fill:#ffaaaa,stroke:#333
```

| Worker | Responsibility |
|---|---|
| **RecorderTransformerWorker** | Reads from `raw_queue`. If `save_calibrated_wave=true`, applies FIR filter via `CalibratorTransformer`. On failure, forwards raw audio unchanged (evidence never dropped). |
| **CloudUploaderService** | Reads from `upload_queue`. Uploads WAV + CSV to S3/Magalu/GCP. Falls back to disk on network error. |
| **HealthMonitorService** | GPIO heartbeat pin + healthchecks.io ping on a timer. |
| **SystemHeartbeatService** | Logs system metrics (CPU, RAM, temp, disk) to CSV. |
| **TelegramCommandReceiver** | Long-polls Telegram for `/privacy` commands. Replies via `TelegramBotClient`. |

## Privacy Mode Flow

```mermaid
sequenceDiagram
    participant User
    participant Bot as TelegramCommandReceiver
    participant RAM as /dev/shm/privacy_mode
    participant Policy as PolicyEngineSink

    User->>Bot: /privacy on 2h
    Bot->>RAM: Write expiry timestamp (now + 7200s)
    Bot->>User: "🔒 Privacy mode ON for 2h."

    loop Every audio chunk
        Policy->>RAM: is_active()?
        RAM-->>Policy: True (timestamp not expired)

        alt rule.ignore_privacy = False
            Policy->>Policy: Skip rule
        else rule.ignore_privacy = True
            Policy->>Policy: Evaluate and fire
        end
    end

    Note over RAM: File auto-deleted on expiry or reboot (/dev/shm)
```

## Data Flow Summary

```mermaid
flowchart TD
    subgraph Pipeline [Real-Time Pipeline]
        M[BasicMetricsSink]
        B[SmartBufferSink]
    end

    subgraph Workers [Background Workers]
        RQ[(raw_queue)]
        RT[RecorderTransformerWorker]
        UQ[(upload_queue)]
        CU[CloudUploaderService]
    end

    subgraph Monitoring [Observability]
        Prom[Prometheus Exporter]
        CSV[("metrics_buffer.csv")]
        Grafana[Grafana]
    end

    subgraph External
        Cloud[Cloud Storage]
        Telegram[Telegram API]
    end

    M -- "Update gauges" --> Prom
    B -- "Log row" --> CSV
    B -- "Raw audio" --> RQ
    RQ --> RT
    RT --> UQ
    UQ --> CU
    CSV -. "Batch upload" .-> CU
    CU --> Cloud
    CU -- "On upload success" --> Telegram
    Prom --> Grafana
```

## Run Modes

The app supports three run modes (passed via `--run-mode`):

| Mode | Description |
|---|---|
| `monolithic` | All sinks and workers in one process. Default for single-Pi deployment. |
| `producer` | Only captures audio and pushes via ZMQ. Low-latency, minimal CPU. |
| `consumer` | Only pulls from ZMQ, runs inference and upload workers. |

The producer/consumer split is useful on dual-process Raspberry Pi deployments where the audio capture process has elevated scheduling priority.
