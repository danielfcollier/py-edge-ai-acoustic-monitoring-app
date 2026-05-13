"""
Tests for FeatureExtractorSink (Batch C).
Focuses on the SAD gate, buffer accumulation, and lazy gain init.
Model init (TFLite/TF), class loading, and inference are patched out.
"""

from unittest.mock import MagicMock, patch, sentinel

import numpy as np
import pytest

from app.sinks.feature_extractor_sink import FeatureExtractorSink


def _make_settings():
    m = MagicMock()
    m.AUDIO.SAMPLE_RATE = 48000  # used in int() during __init__
    cfg = m.CONFIG.feature_extractor
    cfg.use_tflite = True
    cfg.model_path_lite = sentinel.MODEL_PATH_LITE    # patched, never accessed
    cfg.model_path_full = sentinel.MODEL_PATH_FULL    # patched, never accessed
    cfg.class_map_path = sentinel.CLASS_MAP_PATH      # patched, never accessed
    cfg.force_cpu = True
    cfg.target_sample_rate = 16000
    cfg.model_input_size = 15600
    cfg.logging_confidence_threshold = sentinel.LOG_THRESHOLD  # never compared in these tests
    cfg.exclude_classes = []
    return m


def _make_sink(context):
    mock_settings = _make_settings()
    with (
        patch("app.sinks.feature_extractor_sink.settings", mock_settings),
        patch("app.sinks.feature_extractor_sink.PrometheusService"),
        patch.object(FeatureExtractorSink, "_load_classes"),
        patch.object(FeatureExtractorSink, "_resolve_excluded_indices"),
        patch.object(FeatureExtractorSink, "_model_exists", return_value=True),
        patch.object(FeatureExtractorSink, "_init_tflite"),
    ):
        sink = FeatureExtractorSink(context)
    sink._metrics = MagicMock()
    sink._classes = ["Silence", "Dog", "Cat"]
    sink._excluded_indices = []
    return sink


def _ctx(gain_applied=True, sensitivity_dbfs=None):
    m = MagicMock()
    m.audio = np.zeros(4800, dtype=np.float32)
    m.gain_applied = gain_applied
    m.sensitivity_dbfs = sensitivity_dbfs
    return m


class TestFeatureExtractorSinkGate:
    def test_clears_buffer_when_not_inferring(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = False
        sink._raw_buffer = [np.zeros(4800)]  # pre-fill to verify it gets cleared
        sink.handle(_ctx())

        assert sink._raw_buffer == []

    def test_accumulates_chunk_when_inferring(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = True
        sink.handle(_ctx())

        assert len(sink._raw_buffer) == 1

    def test_triggers_inference_when_buffer_reaches_threshold(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = True
        sink._process_inference_batch = MagicMock()

        # 15600 samples @ 16kHz → needs 46800 samples @ 48kHz; 10 chunks of 4800
        samples_needed = int(15600 * (48000 / 16000))
        chunks_needed = (samples_needed // 4800) + 1
        for _ in range(chunks_needed):
            sink.handle(_ctx())

        sink._process_inference_batch.assert_called()

    def test_no_inference_below_buffer_threshold(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = True
        sink._process_inference_batch = MagicMock()

        for _ in range(5):
            sink.handle(_ctx())

        sink._process_inference_batch.assert_not_called()


class TestFeatureExtractorSinkGain:
    def test_gain_is_one_when_gain_already_applied(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = False
        sink.handle(_ctx(gain_applied=True, sensitivity_dbfs=-18.0))

        assert sink._linear_gain == 1.0

    def test_gain_is_one_when_no_sensitivity_info(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = False
        sink.handle(_ctx(gain_applied=False, sensitivity_dbfs=None))

        assert sink._linear_gain == 1.0

    def test_gain_computed_from_sensitivity_dbfs(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = False
        sink.handle(_ctx(gain_applied=False, sensitivity_dbfs=-18.0))

        expected = 10.0 ** (18.0 / 20.0)
        assert abs(sink._linear_gain - expected) < 1e-6

    def test_gain_initialized_only_once(self, app_context):
        sink = _make_sink(app_context)
        app_context.should_infer = False
        sink.handle(_ctx(gain_applied=False, sensitivity_dbfs=-18.0))
        first_gain = sink._linear_gain

        sink.handle(_ctx(gain_applied=False, sensitivity_dbfs=-6.0))  # different sensitivity

        assert sink._linear_gain == first_gain  # still the first value
