# Sound Recognition

The app has two complementary recognition layers:

| Layer | What it identifies | How |
|---|---|---|
| **YAMNet** | Sound *class* (bark, glass, speech, …) | Pre-trained 521-class classifier |
| **MFCC Profile** | Specific *instance* of a class (this dog, this speaker, this alarm) | Few-shot MFCC cosine-similarity |

YAMNet answers "what kind of sound is this?". The MFCC profile answers "is this the specific sound source I care about?".



## 1. YAMNet Real-Time Classification

See [YAMNET.md](YAMNET.md) for model details.

### Pipeline

Recognition runs in three stages inside the hot path:

1. **SAD Gate** (`SADGatewaySink`) — blocks inference when audio is below the noise floor
2. **Feature Extraction** (`FeatureExtractorSink`) — buffers ~975 ms of audio, resamples to 16 kHz, runs YAMNet
3. **Policy Engine** (`PolicyEngineSink`) — maps the predicted label to actions via YAML rules

### Tuning the SAD Gate

Two-stage filter controlled in `security_policy.yaml`:

```yaml
feature_extractor:
  sad_threshold_rms: 0.002     # Stage 1: minimum signal energy
  sad_threshold_flux: 3.0      # Stage 1: minimum spectral change
  sad_threshold_dbspl: 45.0    # Stage 2: minimum loudness (requires calibrated mic)
```

Stage 2 only activates when the microphone is calibrated (i.e., a calibration file is provided and loaded). Without calibration, only Stage 1 runs.

### Excluding Classes

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

### Writing Policy Rules

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



## 2. MFCC Profile Recognition

YAMNet tells you a sound belongs to a class. The MFCC profile layer goes one level deeper: it learns a compact acoustic fingerprint from a small number of labeled examples and identifies whether a recording matches that specific source.

**Use cases:**
- 🐕 **Individual animal** — identify a specific neighbour's dog among all "Dog" detections
- 🗣️ **Speaker identity** — flag recordings containing a known speaker's voice
- 🔔 **Specific device** — distinguish one smoke alarm model from another
- 🌲 **Acoustic signature** — identify a particular machine, vehicle, or instrument

### How it works

1. You label a set of recordings as **target** (the source you care about) or **other**
2. The tool computes a 162-dimensional MFCC feature vector for each recording:
   ```
   [mean_mfcc(40), std_mfcc(40), mean_delta(40), std_delta(40), f0_mean(1), f0_std(1)]
   ```
   This captures timbral shape, rate of change, and fundamental pitch.
3. At inference time, a new recording is scored by **cosine similarity** to the target profile — either nearest-neighbour (default) or centroid distance.
4. Leave-one-out cross-validation (`validate_profile`) finds the threshold that maximises F1 on your labeled set.

### Workflow

```
label_profile → review_profile → validate_profile
    ↑___________________________|
          iterate until stable
```

#### Step 1 — Seed labels

Browse unlabeled recordings and mark each as target or other:

```bash
ai-acoustic-monitor-label-profile \
  --recordings recordings \
  --target-label neighbor-dog \
  --other-label other-sounds \
  --play
```

Prompts: `[y] neighbor-dog  [n] other-sounds  [m] mixed  [s] skip  [q] quit`

Use **mixed** for recordings that contain the target sound alongside significant noise or other events — these are excluded from both training and scoring by default.

#### Step 2 — Iterative review

Once you have a seed set, the review tool ranks unlabeled recordings by MFCC similarity to your current profile and presents the most likely candidates first:

```bash
ai-acoustic-monitor-review-profile \
  --recordings recordings \
  --target-label neighbor-dog \
  --top 20 \
  --play
```

Each candidate shows its similarity score. Repeat until similarity scores stabilise across runs.

To go back and correct existing labels:

```bash
ai-acoustic-monitor-review-profile \
  --relabel --relabel-filter neighbor-dog --play
```

#### Step 3 — Validate

Leave-one-out cross-validation shows you the best F1 threshold and which files are misclassified:

```bash
ai-acoustic-monitor-validate-profile \
  --recordings recordings \
  --target-label neighbor-dog
```

Output includes:
- Similarity statistics for both classes (mean, std, min, max)
- Overlap zone — how many files fall in the ambiguous range
- Threshold sweep ranked by F1
- Confusion matrix at the chosen threshold
- List of misclassified files with similarity scores
- Recommended production threshold

```
Similarity statistics (18 neighbor-dog, 42 other-sounds evaluated):
  neighbor-dog        : mean=0.921  std=0.031  min=0.847  max=0.968
  other-sounds        : mean=0.743  std=0.058  min=0.621  max=0.831
  overlap zone [0.847 – 0.831]: 0 file(s) in ambiguous range

Recommended threshold for production: 0.843
```

### Scoring methods

| Method | `--method` | When to use |
|---|---|---|
| Nearest-neighbour | `nearest` (default) | Works well with a diverse training set; robust to outliers in the centroid |
| Centroid | `centroid` | Faster with large sets; better when all examples are clean and consistent |

### Clip-based training

MFCC vectors are computed as a **global time-average** over the entire recording. A 45-second file where the target event occupies 3 seconds dilutes the feature vector with 42 seconds of ambient noise.

For best profile quality, clip recordings to the event of interest before labeling:

```bash
# Trim seconds 4–7 from a recording
audio-clip recordings/2025-10-14_event.wav --start 4 --end 7
# → recordings/clips/2025-10-14_event_4s_7s.wav
```

Then label the clip as target and the original as mixed:

```json
{
  "2025-10-14_event.wav": "mixed",
  "clips/2025-10-14_event_4s_7s.wav": "neighbor-dog"
}
```

The `clips/` subdirectory is preserved by `flatten_recordings` and handled transparently by all three profile tools. Aim for clips of **2–4 seconds** capturing the full event onset, body, and tail.

### Labels file

Labels are stored in `<recordings>/.mfcc_labels.json` as a flat JSON object mapping relative WAV paths to label strings:

```json
{
  "2025-11-01_bark.wav": "other-sounds",
  "clips/2025-11-03_bark_2s_5s.wav": "neighbor-dog",
  "clips/2025-11-07_bark_1s_4s.wav": "neighbor-dog"
}
```

Label strings are arbitrary — whatever you pass as `--target-label` and `--other-label`. The `mixed` label is always reserved for the excluded class.

### CLI reference

All three tools share these common arguments:

| Argument | Default | Description |
|---|---|---|
| `--recordings DIR` | `recordings` | Recordings directory |
| `--labels FILE` | `<recordings>/.mfcc_labels.json` | Labels JSON file |
| `--target-label LABEL` | `target` | Label string for the target class |
| `--other-label LABEL` | `other` | Label string for the counter-example class |
| `--play` | off | Play each WAV before prompting |

`review_profile` additional arguments:

| Argument | Default | Description |
|---|---|---|
| `--method nearest\|centroid` | `nearest` | Scoring method |
| `--top N` | `20` | Candidates to review per run |
| `--bottom N` | `0` | Review N least-similar candidates (for labeling negatives) |
| `--min-sim FLOAT` | `0.0` | Skip candidates below this similarity |
| `--relabel` | off | Review already-labeled files instead of unlabeled ones |
| `--relabel-filter LABEL` | `all` | Filter `--relabel` to a specific label value |

`validate_profile` additional arguments:

| Argument | Default | Description |
|---|---|---|
| `--threshold FLOAT` | best F1 | Evaluate at this threshold instead of the auto-selected one |
| `--include-mixed` | off | Treat `mixed` files as other-class in validation |

### Notes on speech

MFCC features were originally developed for speech processing and work well for speaker identification. Practical considerations:

- **More examples needed** — speech has higher intra-class variance than a single-event sound; aim for 15–20 clips
- **Clip to utterances** — clip to sentence-length segments (~2–4 s), not full recordings
- **f0 is meaningful** — the fundamental frequency component captures speaker pitch, which is a strong discriminating feature
- **Vocabulary-independent** — the profile matches the speaker's voice characteristics, not the words spoken
