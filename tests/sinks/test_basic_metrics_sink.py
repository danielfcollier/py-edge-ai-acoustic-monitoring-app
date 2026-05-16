"""Tests for BasicMetricsSink (Batch C)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.sinks.basic_metrics_sink import BasicMetricsSink


def _make_settings():
    m = MagicMock()
    m.AUDIO.SAMPLE_RATE = 48000
    m.CONFIG.services.dbspl_silence_level = 30.0
    return m


def _make_sink(context):
    mock_settings = _make_settings()
    mock_prometheus = MagicMock()
    with (
        patch("app.sinks.basic_metrics_sink.settings", mock_settings),
        patch("app.sinks.basic_metrics_sink.PrometheusService", return_value=mock_prometheus),
    ):
        sink = BasicMetricsSink(context)
    return sink, mock_prometheus


def _ctx(audio=None, can_dbspl=False, sensitivity_dbfs=-18.0, reference_dbspl=94.0):
    m = MagicMock()
    m.audio = audio if audio is not None else np.zeros(4800, dtype=np.float32)
    m.can_calculate_dbspl.return_value = can_dbspl
    m.sensitivity_dbfs = sensitivity_dbfs
    m.reference_dbspl = reference_dbspl
    return m


class TestBasicMetricsSink:
    def test_rms_and_flux_always_populated(self, app_context):
        sink, _ = _make_sink(app_context)
        sink.handle(_ctx(np.ones(4800, dtype=np.float32) * 0.5))

        assert app_context.metrics["rms"] > 0.0
        assert "flux" in app_context.metrics

    def test_dbspl_zero_when_uncalibrated(self, app_context):
        sink, _ = _make_sink(app_context)
        sink.handle(_ctx(can_dbspl=False))

        assert app_context.metrics["dbspl"] == 0.0

    def test_dbspl_populated_when_calibrated(self, app_context):
        sink, _ = _make_sink(app_context)
        audio = np.ones(4800, dtype=np.float32) * 0.5
        sink.handle(_ctx(audio, can_dbspl=True, sensitivity_dbfs=-18.0, reference_dbspl=94.0))

        assert app_context.metrics["dbspl"] > 0.0

    def test_audio_appended_to_pre_buffer(self, app_context):
        sink, _ = _make_sink(app_context)
        before = len(app_context.audio_pre_buffer)
        sink.handle(_ctx(np.ones(4800, dtype=np.float32)))

        assert len(app_context.audio_pre_buffer) == before + 1

    def test_prometheus_omits_dbspl_when_uncalibrated(self, app_context):
        sink, mock_prom = _make_sink(app_context)
        sink.handle(_ctx(can_dbspl=False))

        dbspl_kwarg = mock_prom.update_audio.call_args.kwargs.get("dbspl")
        assert dbspl_kwarg is None

    def test_prometheus_uses_actual_dbspl_when_calibrated(self, app_context):
        sink, mock_prom = _make_sink(app_context)
        audio = np.ones(4800, dtype=np.float32) * 0.5
        sink.handle(_ctx(audio, can_dbspl=True, sensitivity_dbfs=-18.0, reference_dbspl=94.0))

        dbspl_kwarg = mock_prom.update_audio.call_args.kwargs.get("dbspl")
        assert dbspl_kwarg is not None
        assert dbspl_kwarg > 0.0
