Current:

```mermaid
flowchart TD
    subgraph Pipeline [Real-Time Audio Pipeline]
        Source[Audio Source] --> |Raw Audio| FeatureSink[Feature Extractor Sink]
        FeatureSink --> |Adds Metrics & AI Class Label| PolicySink[Policy Engine Sink]
        PolicySink --> |Adds Flags: Record/Upload| RecorderSink[Smart Recorder Sink]
    end

    subgraph RecorderLogic [Smart Recorder Internal Logic]
        RecorderSink -- Triggered --> Buffer[RAM Buffer]
        Buffer -- Event Ends --> CheckFlag{Calibrate?}
        
        CheckFlag -- Yes --> LoadFIR[Load FIR Filter]
        LoadFIR --> ApplyFIR[Apply FIR Convolution]
        ApplyFIR --> CalcLUFS_Cal[Calc LUFS Calibrated]
        
        CheckFlag -- No --> CalcLUFS_Raw[Calc LUFS Raw]
        
        CalcLUFS_Cal --> Bundle[Create Event Object]
        CalcLUFS_Raw --> Bundle
    end

    subgraph Async [Async Workers]
        Bundle --> Queue[(Upload Queue)]
        Queue --> Uploader[Cloud Uploader Service]
        Uploader --> |Online| Cloud[Cloud Storage]
        Uploader --> |Offline| Disk[Disk Fallback]
    end

    style RecorderSink fill:#f9f,stroke:#333,stroke-width:2px
    style ApplyFIR fill:#ffaaaa,stroke:#333,stroke-width:2px
```

```mermaid
flowchart TD
    subgraph App [Application Process]
        Recorder[Smart Recorder Sink]
        Queue[(Upload Queue)]
        
        %% Data Generation
        Recorder -- "Bundle Event" --> Queue
        Recorder -- "Append Row" --> CSV[("metrics_buffer.csv")]
        
        %% Metrics
        Recorder -. "Update Gauges" .-> Prometheus[Prometheus Exporter]

        %% Services
        subgraph Services [Background Services]
            Uploader[Cloud Uploader Service]
        end
        
        Queue --> Uploader
        CSV -. "3. Rotate & Batch" .-> Uploader
    end

    subgraph External [External World]
        Cloud[Cloud Storage]
        Telegram[Telegram API]
        Grafana[Prometheus Server]
    end

    %% Actions
    Uploader -- "Upload WAV" --> Cloud
    Uploader -- "Upload CSV" --> Cloud
    Uploader -- "On Success" --> Telegram
    
    Prometheus -- "Scrape" --> Grafana
    
    style Recorder fill:#f9f,stroke:#333
    style Uploader fill:#bbf,stroke:#333
    style CSV fill:#eee,stroke:#333,stroke-dasharray: 5 5
```

Future Plan:

Here is the updated **Future Architectural Plan** incorporating the **FIR Calibration** requirement.

This architecture solves the conflict between "Low Latency" (for detection) and "High Fidelity" (for evidence) by splitting them into two separate paths.

### The Philosophy: "Fast Trigger, Precision Record"

We apply **Scalar Calibration** (Simple Gain) in the real-time loop for speed, and **Vector Calibration** (FIR Convolution) in the background worker for audio quality.

### 1. The Real-Time Pipeline (The "Hot Path")

*Running synchronously on every audio chunk. Optimization Goal: **Latency**.*

#### **Stage 1: Basic Metrics Sink (The "Physics Engine")**

* **Calibration Type:** **Scalar Only** (Sensitivity Gain).
* **Action:**
* Apply `Gain = 10^(Sensitivity/20)` to the chunk.
* Calculate **dBSPL** and **Flux** based on this simple gain.
* *Why?* Convolution (FIR) is too heavy to run 24/7 on every single frame just for a trigger check. Simple gain is 99% accurate for loudness detection.


* **Output:** Populates `context.metrics`.

#### **Stage 2: SAD Gateway Sink**

* **Action:** Checks `context.metrics` (Scalar dBSPL) against thresholds.
* **Output:** Sets `skip_inference` flag.

#### **Stage 3: Feature Extractor Sink**

* **Action:** Runs AI model (YAMNet) if SAD passes.

#### **Stage 4: Policy Engine Sink**

* **Action:** Decides to record based on Metrics + AI.

#### **Stage 5: Smart Buffer Sink (The "Vault")**

* **Action:**
* Maintains the Ring Buffer (Pre-roll).
* When triggered, copies **Raw Audio** (Unmodified/Uncalibrated) into an Event Buffer.
* *Crucial:* We capture the *Raw* signal so we can decide how to process it later (or re-process it if calibration changes).


* **Handoff:** Pushes a `RawEventObject` (Audio + Metadata) to the **Processing Queue**.

### 2. The Async Worker (The "Cold Path")

*Running in background threads. Optimization Goal: **Quality**.*

#### **Stage 6: Recorder Transformer (The "Studio Engineer")**

* **This is the new home for the FIR logic.**
* **Input:** `RawEventObject` from the Queue.
* **Logic:**
1. **Check Config:** Is `save_calibrated_wave: true`?
2. **If YES (The Heavy Lift):**
* **Load FIR:** Load the `*_fir_*.npy` filter from disk (cached in RAM).
* **Apply Gain:** Multiply raw audio by Sensitivity Factor.
* **Apply EQ:** Run `scipy.signal.fftconvolve(audio, fir_filter)` to flatten the frequency response.
* **Recalculate Metrics:** Calculate the **Integrated LUFS** on this *new, polished* signal.


3. **If NO:**
* Use the raw audio.
* Calculate LUFS on raw audio.


* **Output:** Creates a `ProcessedEventObject`.

#### **Stage 7: Cloud Uploader Service (The "Courier")**

* **Action:**
* **Online:** Streams the `ProcessedEventObject` (WAV bytes) to S3/Magalu.
* **Offline:** Spills to Disk (DLQ).


```mermaid
flowchart TD
    subgraph Pipeline [Real-Time Audio Pipeline]
        Source[Audio Source] --> MetricSink[Basic Metrics Sink]
        MetricSink --> |Physics: RMS, Flux, dBSPL| SADSink[SAD Gateway Sink]
        SADSink --> |Gate: Skip/Pass| FeatureSink[Feature Extractor Sink]
        FeatureSink --> |AI: Label| PolicySink[Policy Engine Sink]
        PolicySink --> |Decision| BufferSink[Smart Buffer Sink]
    end

    subgraph Handoff [Async Boundary]
        BufferSink --> |Raw Audio Object| InputQueue[(Raw Event Queue)]
    end

    subgraph Workers [Background Processing]
        InputQueue --> RecTransformer[Recorder Transformer Worker]
        
        RecTransformer -- "Feature Flag Check" --> Logic{Calibrate?}
        Logic -- Yes --> FIR[Apply FIR Convolution]
        Logic -- No --> Pass[Pass Raw Audio]
        
        FIR --> OutputQueue[(Processed Queue)]
        Pass --> OutputQueue
        
        OutputQueue --> Uploader[Cloud Uploader Service]
    end
    
    subgraph Storage
        Uploader --> |Online| Cloud[Cloud Storage]
        Uploader --> |Offline| Disk[Disk Fallback]
    end

    style BufferSink fill:#bbf,stroke:#333,stroke-width:2px
    style RecTransformer fill:#f9f,stroke:#333,stroke-width:4px
    style FIR fill:#ffaaaa,stroke:#333,stroke-width:2px
```

```mermaid
flowchart TD
    subgraph Pipeline [Real-Time Pipeline]
        MetricSink[Basic Metrics Sink]
        BufferSink[Smart Buffer Sink]
    end

    subgraph Async [Async Workers]
        RawQueue[(Raw Queue)]
        Transformer[Recorder Transformer Worker]
        ProcessedQueue[(Processed Queue)]
        Uploader[Cloud Uploader Service]
    end

    %% Data Flow
    MetricSink -- "1. Log Row" --> CSV[("metrics_buffer.csv")]
    MetricSink -- "Update" --> Prometheus[Prometheus Exporter]

    BufferSink -- "2. Raw Audio" --> RawQueue
    RawQueue --> Transformer
    Transformer -- "3. FIR Calib" --> ProcessedQueue
    ProcessedQueue --> Uploader
    CSV -. "4. Batch Upload" .-> Uploader

    %% External
    subgraph External [External World]
        Cloud[Cloud Storage]
        Telegram[Telegram API]
        Grafana[Prometheus Server]
    end

    Uploader -- "Upload WAV" --> Cloud
    Uploader -- "Upload CSV" --> Cloud
    Uploader -- "On Success" --> Telegram
    Prometheus --> Grafana

    style Transformer fill:#ffaaaa,stroke:#333,stroke-width:2px
    style Uploader fill:#bbf,stroke:#333
```