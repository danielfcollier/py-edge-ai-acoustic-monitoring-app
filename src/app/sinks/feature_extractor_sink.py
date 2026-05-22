"""
Feature Extractor Sink.
AI inference stage: runs YAMNet on buffered audio and populates
context.current_event_label and context.current_confidence.
Only runs when context.should_infer is True (set by SADGatewaySink).

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import csv
import logging
from pathlib import Path

import numpy as np
import resampy
from umik_base_app import AudioSink, PipelineContext as AudioCtx

from ..context import PipelineContext
from ..services.prometheus_service import PrometheusService
from ..settings import FeatureExtractorConfig, settings

logger = logging.getLogger(__name__)


class FeatureExtractorSink(AudioSink):
    """Classifies audio using YAMNet. Gated by context.should_infer."""

    def __init__(self, context: PipelineContext):
        self._context = context
        self._config: FeatureExtractorConfig = settings.CONFIG.feature_extractor
        self._metrics = PrometheusService()
        self._classes = []
        self._excluded_indices = []

        self._input_sr = int(settings.AUDIO.SAMPLE_RATE)
        self._target_sr = self._config.target_sample_rate
        self._model_input_size = self._config.model_input_size
        self._raw_buffer = []

        self._logging_threshold = self._config.logging_confidence_threshold
        self._linear_gain: float | None = None

        self._load_classes()
        self._resolve_excluded_indices()

        if not self._model_exists():
            logger.info("⬇️ First run detected. Downloading YAMNet models...")
            self._download_models()

        self._init_onnx()

        logger.info(f"📌 Feature Extractor Ready. Input: {self._input_sr}Hz -> Model: {self._target_sr}Hz")

    def _model_exists(self) -> bool:
        return Path(self._config.model_path).exists()

    def _download_models(self):
        from scripts import setup_models

        setup_models.main()

    def _load_classes(self):
        csv_path = self._config.class_map_path
        if not Path(csv_path).exists():
            raise FileNotFoundError(f"Class map not found at {csv_path}.")
        try:
            with open(csv_path) as f:
                for row in csv.DictReader(f):
                    self._classes.append(row["display_name"])
            logger.info(f"Loaded {len(self._classes)} classes.")
        except Exception as e:
            raise FileExistsError(f"Failed to load class map: {e}") from e

    def _resolve_excluded_indices(self):
        excluded_names = set(self._config.exclude_classes)
        self._excluded_indices = [i for i, name in enumerate(self._classes) if name in excluded_names]
        if self._excluded_indices:
            logger.info(f"🚫 Exclusion Active. Muting {len(self._excluded_indices)} classes: {list(excluded_names)}")

    def _init_onnx(self):
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError("onnxruntime not found. Install 'onnxruntime'.")

        model_path = self._config.model_path
        if not Path(model_path).exists():
            raise FileNotFoundError(f"ONNX model not found at {model_path}")

        logger.info(f"Loading ONNX model: {model_path}")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(model_path, sess_options=opts)
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name

    def handle(self, ctx: AudioCtx) -> None:
        # Lazy-init gain boost from ctx calibration metadata
        if self._linear_gain is None:
            if not ctx.gain_applied and ctx.sensitivity_dbfs is not None:
                self._linear_gain = 10.0 ** (abs(ctx.sensitivity_dbfs) / 20.0)
                logger.info(
                    f"🔊 AI Input Gain: Applying {self._linear_gain:.2f}x boost "
                    f"(based on Sensitivity {ctx.sensitivity_dbfs}dB)"
                )
            else:
                self._linear_gain = 1.0

        # SAD gate: SADGatewaySink cleared this frame
        if not self._context.should_infer:
            self._raw_buffer = []
            return

        self._raw_buffer.append(ctx.audio)
        current_size = sum(len(c) for c in self._raw_buffer)
        samples_needed = int(self._model_input_size * (self._input_sr / self._target_sr))

        if current_size >= samples_needed:
            self._process_inference_batch()

    def _process_inference_batch(self):
        raw_audio = np.concatenate(self._raw_buffer)
        resampled = resampy.resample(raw_audio, self._input_sr, self._target_sr)
        self._raw_buffer = []

        stride = self._model_input_size
        n = len(resampled)

        if n < stride:
            windows = [np.pad(resampled, (0, stride - n))]
        else:
            windows = [resampled[i : i + stride] for i in range(0, n - stride + 1, stride)]

        all_scores = []
        for window in windows:
            w = window.astype(np.float32) * self._linear_gain
            w = np.clip(w, -1.0, 1.0)
            scores = self._predict_onnx(w)
            if scores is not None:
                all_scores.append(scores)

        if all_scores:
            self._update_context(np.mean(all_scores, axis=0))

    def _predict_onnx(self, input_data: np.ndarray) -> np.ndarray:
        return self._session.run([self._output_name], {self._input_name: input_data})[0][0]

    def _update_context(self, scores):
        if self._excluded_indices:
            scores[self._excluded_indices] = 0.0

        prediction_index = scores.argmax()
        label = self._classes[prediction_index] if prediction_index < len(self._classes) else "Unknown"
        confidence = float(scores[prediction_index])

        # Top 5 (excluding suppressed classes)
        sorted_indices = np.argsort(scores)[::-1]
        top5: list[tuple[str, float]] = []
        for idx in sorted_indices:
            if len(top5) >= 5:
                break
            if idx in self._excluded_indices:
                continue
            cls_name = self._classes[idx] if idx < len(self._classes) else "Unknown"
            top5.append((cls_name, float(scores[idx])))
        logger.debug("🔍 YAMNet Top 5: " + " ".join(f"[{n}: {s:.2f}]" for n, s in top5))

        self._context.current_event_label = label
        self._context.current_confidence = confidence
        self._context.top_classes = top5
        self._metrics.update_ai_status(label, confidence)

        if confidence > self._logging_threshold:
            rms = self._context.metrics.get("rms", 0.0)
            flux = self._context.metrics.get("flux", 0.0)
            dbspl = self._context.metrics.get("dbspl", 0.0)

            if dbspl > 0:
                logger.info(
                    f"rms={rms:.4f} flux={flux:05.1f} dBSPL={dbspl:05.1f} | 👂 Heard: {label} ({confidence:.2f})"
                )
            else:
                logger.info(f"rms={rms:.4f} flux={flux:05.1f} | 👂 Heard: {label} ({confidence:.2f})")
