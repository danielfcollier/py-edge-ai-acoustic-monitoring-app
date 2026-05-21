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

        if self._config.use_tflite:
            self._init_tflite()
        else:
            self._init_tensorflow()

        logger.info(f"📌 Feature Extractor Ready. Input: {self._input_sr}Hz -> Model: {self._target_sr}Hz")

    def _model_exists(self) -> bool:
        if self._config.use_tflite:
            return Path(self._config.model_path_lite).exists()
        return Path(self._config.model_path_full).exists()

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

    def _init_tflite(self):
        try:
            import tflite_runtime.interpreter as tflite
        except ImportError:
            try:
                import tensorflow.lite as tflite
            except ImportError:
                raise ImportError("TFLite runtime not found. Install 'tflite-runtime'.")

        model_path = self._config.model_path_lite
        if not Path(model_path).exists():
            raise FileNotFoundError(f"TFLite Model not found at {model_path}")

        logger.info(f"Loading TFLite model: {model_path}")
        self._interpreter = tflite.Interpreter(model_path=model_path)
        self._interpreter.allocate_tensors()
        self._input_details = self._interpreter.get_input_details()
        self._output_details = self._interpreter.get_output_details()
        self._output_index = self._output_details[0]["index"]

    def _init_tensorflow(self):
        try:
            import tensorflow as tf
        except ImportError:
            raise ImportError("TensorFlow not found. Install 'tensorflow'.")

        if self._config.force_cpu:
            try:
                gpus = tf.config.list_physical_devices("GPU")
                if gpus:
                    tf.config.set_visible_devices([], "GPU")
                    logger.info("🚫 GPU disabled by configuration (force_cpu=True).")
            except Exception as e:
                logger.warning(f"Failed to force CPU mode: {e}")

        model_path = self._config.model_path_full
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Full Model not found at {model_path}")

        logger.info(f"Loading Full TensorFlow model: {model_path}")
        self._tf_model = tf.saved_model.load(model_path)

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
            scores = self._infer_window(w)
            if scores is not None:
                all_scores.append(scores)

        if all_scores:
            self._update_context(np.mean(all_scores, axis=0))

    def _infer_window(self, input_data: np.ndarray) -> np.ndarray | None:
        if self._config.use_tflite:
            return self._predict_tflite(input_data)
        else:
            return self._predict_tensorflow(input_data)

    def _predict_tflite(self, input_data: np.ndarray) -> np.ndarray:
        self._interpreter.set_tensor(self._input_details[0]["index"], input_data)
        self._interpreter.invoke()
        return self._interpreter.get_tensor(self._output_index)[0]

    def _predict_tensorflow(self, input_data: np.ndarray) -> np.ndarray:
        import tensorflow as tf

        input_tensor = tf.convert_to_tensor(input_data, dtype=tf.float32)
        scores, _, _ = self._tf_model(input_tensor)
        return np.mean(scores.numpy(), axis=0)

    def _update_context(self, scores):
        if self._excluded_indices:
            scores[self._excluded_indices] = 0.0

        prediction_index = scores.argmax()
        label = self._classes[prediction_index] if prediction_index < len(self._classes) else "Unknown"
        confidence = float(scores[prediction_index])

        # Debug: Top 5
        sorted_indices = np.argsort(scores)[::-1]
        debug_parts = ["🔍 YAMNet Top 5:"]
        count = 0
        for idx in sorted_indices:
            if count >= 5:
                break
            if idx in self._excluded_indices:
                continue
            cls_name = self._classes[idx] if idx < len(self._classes) else "Unknown"
            debug_parts.append(f"[{cls_name}: {scores[idx]:.2f}]")
            count += 1
        logger.debug(" ".join(debug_parts))

        self._context.current_event_label = label
        self._context.current_confidence = confidence
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
