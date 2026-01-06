"""
Feature Extractor Sink.
Supports both TFLite (Edge) and Full TensorFlow (Dev/High-Power) models.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import csv
import logging
from pathlib import Path

import numpy as np
import resampy
from umik_base_app import AudioSink, AudioMetrics

from ..context import PipelineContext
from ..settings import settings

logger = logging.getLogger(__name__)


class FeatureExtractorSink(AudioSink):
    """
    Analyzes audio chunks using YAMNet and updates the context.
    """

    def __init__(self, context: PipelineContext):
        self._context = context
        self._config = settings.CONFIG.feature_extractor

        self._classes = []
        self._model = None

        # Buffers
        self._target_sr = self._config.target_sample_rate
        self._model_input_size = 15600  # YAMNet: 0.975s @ 16kHz
        self._raw_buffer = []
        self._input_sr = int(settings.AUDIO.SAMPLE_RATE)
        
        # Add a threshold (adjust based on your noise floor)
        # 0.002 is roughly -54dB
        self._sad_threshold = 0.002

        self._load_classes()

        if not self._model_exists():
            logger.info("⬇️ First run detected. Downloading YAMNet models...")
            self._download_models()

        if self._config.use_tflite:
            self._init_tflite()
        else:
            self._init_tensorflow()

        logger.info(
            f"Feature Extractor Ready ({'TFLite' if self._config.use_tflite else 'Full TF'}). "
            f"Input: {self._input_sr}Hz -> Model: {self._target_sr}Hz"
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
        csv_path = self._config.class_map_path
        if not Path(csv_path).exists():
            logger.warning(f"Class map not found at {csv_path}. Using fallbacks.")
            self._classes = ["Unknown"] * 521
            return

        try:
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._classes.append(row["display_name"])
            logger.info(f"Loaded {len(self._classes)} classes.")
        except Exception as e:
            logger.error(f"Failed to load class map: {e}")
            self._classes = ["Unknown"] * 521

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

        model_path = self._config.model_path_full
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Full Model not found at {model_path}")

        logger.info(f"Loading Full TensorFlow model: {model_path}")
        self._tf_model = tf.saved_model.load(model_path)

    def handle_audio(self, audio_chunk: np.ndarray, timestamp) -> None:
        self._context.audio_pre_buffer.append(audio_chunk)
        
        # 2. SAD Optimization: Check RMS before processing
        rms = AudioMetrics(audio_chunk)
        
        # If quiet, skip AI inference for this chunk
        if rms < self._sad_threshold:
            # We must still clear the classification so the Policy Engine doesn't 
            # react to "stale" data from 1 second ago.
            self._context.current_event_label = "Silence"
            self._context.current_confidence = 0.0
            
            # Clear buffer so we don't process old audio later
            self._raw_buffer = [] 
            return
        
        self._raw_buffer.append(audio_chunk)

        current_size = sum(len(c) for c in self._raw_buffer)
        samples_needed = int(self._model_input_size * (self._input_sr / self._target_sr))

        if current_size >= samples_needed:
            self._process_inference_batch()

    def _process_inference_batch(self):
        raw_audio = np.concatenate(self._raw_buffer)
        resampled = resampy.resample(raw_audio, self._input_sr, self._target_sr)

        if len(resampled) > self._model_input_size:
            input_data = resampled[: self._model_input_size]
        else:
            input_data = np.pad(resampled, (0, self._model_input_size - len(resampled)))

        input_data = input_data.astype(np.float32)

        if self._config.use_tflite:
            self._predict_tflite(input_data)
        else:
            self._predict_tensorflow(input_data)

        self._raw_buffer = []

    def _predict_tflite(self, input_data):
        self._interpreter.set_tensor(self._input_details[0]["index"], input_data)
        self._interpreter.invoke()
        scores = self._interpreter.get_tensor(self._output_index)[0]
        self._update_context(scores)

    def _predict_tensorflow(self, input_data):
        import tensorflow as tf

        input_tensor = tf.convert_to_tensor(input_data, dtype=tf.float32)
        scores, _, _ = self._tf_model(input_tensor)

        scores_np = scores.numpy()
        avg_scores = np.mean(scores_np, axis=0)
        self._update_context(avg_scores)

    def _update_context(self, scores):
        prediction_index = scores.argmax()
        label = self._classes[prediction_index] if prediction_index < len(self._classes) else "Unknown"
        confidence = float(scores[prediction_index])

        self._context.current_event_label = label
        self._context.current_confidence = confidence

        if confidence > 0.3:
            logger.info(f"👂 Heard: {label} ({confidence:.2f})")
