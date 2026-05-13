"""
Feature Extractor Sink.
Analysis Stage: Calculates Physics (RMS, Flux, dBSPL) and AI (Yamnet) metrics.
Populates the PipelineContext for the Policy Engine.
Implements a Two-Stage SAD (Sound Activity Detection) for CPU efficiency.

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
from umik_base_app.core.audio_metrics import AudioMetrics

from ..context import PipelineContext
from ..services.prometheus_service import PrometheusService
from ..settings import FeatureExtractorConfig, settings

logger = logging.getLogger(__name__)


DBSPL_SILENCE_LEVEL = 30.0


class FeatureExtractorSink(AudioSink):
    """
    The "Senses" of the application.
    1. Physics: Calculates real-world loudness (dBSPL) and texture (Flux).
    2. Intelligence: Classifies audio using YAMNet (AI).
    3. Gate: Filters out silence (SAD) to save CPU.
    """

    def __init__(self, context: PipelineContext):
        """
        Initializes the Feature Extractor.
        """
        self._context = context
        self._config: FeatureExtractorConfig = settings.CONFIG.feature_extractor

        # Services & Resources
        self._metrics = PrometheusService()
        self._classes = []
        self._excluded_indices = []
        self._model = None

        # Audio Configuration
        self._input_sr = int(settings.AUDIO.SAMPLE_RATE)
        self._target_sr = self._config.target_sample_rate
        self._model_input_size = self._config.model_input_size
        self._raw_buffer = []

        # Constants from Settings
        self._logging_threshold = self._config.logging_confidence_threshold

        # SAD Thresholds
        self._sad_threshold_rms = self._config.sad_threshold_rms
        self._sad_threshold_flux = self._config.sad_threshold_flux
        self._sad_threshold_dbspl = self._config.sad_threshold_dbspl

        self._linear_gain: float | None = None  # lazy-init from ctx on first handle call

        # Setup
        self._load_classes()
        self._resolve_excluded_indices()

        if not self._model_exists():
            logger.info("⬇️ First run detected. Downloading YAMNet models...")
            self._download_models()

        if self._config.use_tflite:
            self._init_tflite()
        else:
            self._init_tensorflow()

        logger.info(
            f"📌 Feature Extractor Ready. Input: {self._input_sr}Hz -> Model: {self._target_sr}Hz. "
            f"SAD Gate: [RMS>{self._sad_threshold_rms} | Flux>{self._sad_threshold_flux}] "
            f"-> [dBSPL>{self._sad_threshold_dbspl} (If Calibrated)]"
        )

    def _model_exists(self) -> bool:
        """Checks if the configured model file exists."""
        if self._config.use_tflite:
            return Path(self._config.model_path_lite).exists()
        return Path(self._config.model_path_full).exists()

    def _download_models(self):
        """Triggers the setup script to fetch assets."""
        from scripts import setup_models

        setup_models.main()

    def _load_classes(self):
        """Loads the YAMNet class map CSV."""
        csv_path = self._config.class_map_path
        if not Path(csv_path).exists():
            error = f"Class map not found at {csv_path}."
            logger.error(error)
            raise FileNotFoundError(error)

        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._classes.append(row["display_name"])
            logger.info(f"Loaded {len(self._classes)} classes.")
        except Exception as e:
            error = f"Failed to load class map: {e}"
            logger.error(error)
            raise FileExistsError(error)

    def _resolve_excluded_indices(self):
        """Maps 'exclude_classes' to their YAMNet indices for masking."""
        excluded_names = set(self._config.exclude_classes)
        self._excluded_indices = [i for i, name in enumerate(self._classes) if name in excluded_names]
        if self._excluded_indices:
            logger.info(f"🚫 Exclusion Active. Muting {len(self._excluded_indices)} classes: {list(excluded_names)}")

    def _init_tflite(self):
        """Initializes the TFLite runtime interpreter."""
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
        """Initializes the standard TensorFlow SavedModel."""
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
        """Process incoming audio: Physics -> SAD Gate -> AI Inference."""

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

        # 1. Update Pre-Roll Buffer (Crucial for evidence recording)
        self._context.audio_pre_buffer.append(ctx.audio)

        # 2. Stage 1: Basic Physics (Cheap)
        rms = AudioMetrics.rms(ctx.audio)
        flux = AudioMetrics.flux(ctx.audio, self._input_sr)

        self._context.metrics["rms"] = rms
        self._context.metrics["flux"] = flux
        self._context.metrics["dbspl"] = 0.0  # Default/Floor

        # SAD Stage 1: Noise Gate
        is_active = (rms > self._sad_threshold_rms) or (flux > self._sad_threshold_flux)

        if not is_active:
            self._handle_silence(dbspl_val=DBSPL_SILENCE_LEVEL, rms_val=rms)
            return

        # 3. Stage 2: Precision Physics (Expensive & Calibrated)
        dBSPL = 0.0
        if ctx.can_calculate_dbspl():
            dBFS = AudioMetrics.dBFS(ctx.audio)
            dBSPL = AudioMetrics.dBSPL(dBFS, ctx.sensitivity_dbfs, ctx.reference_dbspl)

            # SAD Stage 2: SPL Filter
            if dBSPL < self._sad_threshold_dbspl:
                self._handle_silence(dbspl_val=dBSPL, rms_val=rms)
                return

            self._context.metrics["dbspl"] = dBSPL
        else:
            # Uncalibrated: Skip SAD Stage 2 and SPL calculation
            pass

        # 4. Update Prometheus
        self._metrics.update_audio(dBSPL if dBSPL > 0 else DBSPL_SILENCE_LEVEL, rms, flux)

        # 5. Accumulate for AI Inference
        self._raw_buffer.append(ctx.audio)
        current_size = sum(len(c) for c in self._raw_buffer)

        # Ratio correction for resampling (e.g. 48k -> 16k requires 3x samples)
        samples_needed = int(self._model_input_size * (self._input_sr / self._target_sr))

        if current_size >= samples_needed:
            self._process_inference_batch()

    def _handle_silence(self, dbspl_val: float, rms_val: float):
        """Helper to reset state and update monitors during silence."""
        # Context
        self._context.current_event_label = "Silence"
        self._context.current_confidence = 0.0

        # Buffers
        self._raw_buffer = []

        # Live Monitors (Needle drops to floor/value)
        self._metrics.update_audio(dbspl_val, rms_val, 0.0)
        self._metrics.update_ai_status("Silence", 0.0)

    def _process_inference_batch(self):
        """Runs the AI model on the buffered audio."""
        # Merge & Resample
        raw_audio = np.concatenate(self._raw_buffer)
        resampled = resampy.resample(raw_audio, self._input_sr, self._target_sr)

        # Pad or Crop to exact model input size
        if len(resampled) > self._model_input_size:
            input_data = resampled[: self._model_input_size]
        else:
            input_data = np.pad(resampled, (0, self._model_input_size - len(resampled)))

        # Convert to Float32
        input_data = input_data.astype(np.float32)

        # 🆕 APPLY GAIN BOOST (Normalization)
        # This makes the audio "louder" for the AI without expensive FIR filtering
        input_data *= self._linear_gain

        # Clip to safe range [-1.0, 1.0] to prevent distortion artifacts
        input_data = np.clip(input_data, -1.0, 1.0)

        # Run Inference
        if self._config.use_tflite:
            self._predict_tflite(input_data)
        else:
            self._predict_tensorflow(input_data)

        # Reset Buffer
        self._raw_buffer = []

    def _predict_tflite(self, input_data):
        """Runs TFLite inference."""
        self._interpreter.set_tensor(self._input_details[0]["index"], input_data)
        self._interpreter.invoke()
        scores = self._interpreter.get_tensor(self._output_index)[0]
        self._update_context(scores)

    def _predict_tensorflow(self, input_data):
        """Runs TensorFlow inference."""
        import tensorflow as tf

        input_tensor = tf.convert_to_tensor(input_data, dtype=tf.float32)
        scores, _, _ = self._tf_model(input_tensor)

        scores_np = scores.numpy()
        # Average scores if model returns multiple frames
        avg_scores = np.mean(scores_np, axis=0)
        self._update_context(avg_scores)

    def _update_context(self, scores):
        """Updates Context with results, filtering excluded classes."""

        # 1. Mask Excluded Classes
        if self._excluded_indices:
            scores[self._excluded_indices] = 0.0

        # 2. Get Top-1
        prediction_index = scores.argmax()
        label = self._classes[prediction_index] if prediction_index < len(self._classes) else "Unknown"
        confidence = float(scores[prediction_index])

        # 3. DEBUG: Top 5 (Clean Loop)
        sorted_indices = np.argsort(scores)[::-1]
        debug_parts = ["🔍 YAMNet Top 5:"]
        count = 0
        for idx in sorted_indices:
            if count >= 5:
                break
            if idx in self._excluded_indices:
                continue

            cls_name = self._classes[idx] if idx < len(self._classes) else "Unknown"
            score = scores[idx]
            debug_parts.append(f"[{cls_name}: {score:.2f}]")
            count += 1

        logger.debug(" ".join(debug_parts))

        # 4. Update Pipeline Context
        self._context.current_event_label = label
        self._context.current_confidence = confidence

        # 5. Update Live Status Gauge (Needle)
        self._metrics.update_ai_status(label, confidence)

        # 6. Log Significant Events
        if confidence > self._logging_threshold:
            rms = self._context.metrics.get("rms", 0.0)
            flux = self._context.metrics.get("flux", 0.0)

            dBSPL = self._context.metrics.get("dbspl", 0.0)

            if dBSPL > 0:
                logger.info(
                    f"rms={rms:.4f} flux={flux:05.1f} dBSPL={dBSPL:05.1f} | 👂 Heard: {label} ({confidence:.2f})"
                )
            else:
                logger.info(f"rms={rms:.4f} flux={flux:05.1f} | 👂 Heard: {label} ({confidence:.2f})")
