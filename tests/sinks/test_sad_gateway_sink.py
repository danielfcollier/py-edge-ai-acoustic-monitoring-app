"""Tests for SADGatewaySink (Batch C)."""

from unittest.mock import MagicMock, patch

import pytest

from app.sinks.sad_gateway_sink import SADGatewaySink


def _make_settings(rms=0.002, flux=5.0, dbspl=45.0):
    m = MagicMock()
    m.CONFIG.feature_extractor.sad_threshold_rms = rms
    m.CONFIG.feature_extractor.sad_threshold_flux = flux
    m.CONFIG.feature_extractor.sad_threshold_dbspl = dbspl
    return m


def _make_sink(context, **thresholds):
    mock_settings = _make_settings(**thresholds)
    with (
        patch("app.sinks.sad_gateway_sink.settings", mock_settings),
        patch("app.sinks.sad_gateway_sink.PrometheusService"),
    ):
        sink = SADGatewaySink(context)
    sink._metrics = MagicMock()
    return sink


def _ctx(can_dbspl=False):
    m = MagicMock()
    m.can_calculate_dbspl.return_value = can_dbspl
    return m


class TestSADGatewaySink:
    def test_blocks_when_rms_and_flux_both_below_threshold(self, app_context):
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.001, "flux": 1.0, "dbspl": 0.0}
        sink.handle(_ctx())

        assert not app_context.should_infer

    def test_passes_stage1_when_rms_above_threshold(self, app_context):
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.01, "flux": 1.0, "dbspl": 0.0}
        sink.handle(_ctx(can_dbspl=False))

        assert app_context.should_infer

    def test_passes_stage1_when_flux_above_threshold(self, app_context):
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.001, "flux": 10.0, "dbspl": 0.0}
        sink.handle(_ctx(can_dbspl=False))

        assert app_context.should_infer

    def test_stage2_blocks_when_dbspl_below_threshold_and_calibrated(self, app_context):
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.01, "flux": 10.0, "dbspl": 30.0}  # below 45 dB threshold
        sink.handle(_ctx(can_dbspl=True))

        assert not app_context.should_infer

    def test_stage2_passes_when_dbspl_above_threshold_and_calibrated(self, app_context):
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.01, "flux": 10.0, "dbspl": 60.0}  # above 45 dB threshold
        sink.handle(_ctx(can_dbspl=True))

        assert app_context.should_infer

    def test_stage2_skipped_when_not_calibrated(self, app_context):
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.01, "flux": 10.0, "dbspl": 30.0}  # would fail stage2
        sink.handle(_ctx(can_dbspl=False))  # but calibration not available → stage2 skipped

        assert app_context.should_infer

    def test_silence_resets_label_and_confidence(self, app_context):
        app_context.current_event_label = "Dog"
        app_context.current_confidence = 0.9
        sink = _make_sink(app_context)
        app_context.metrics = {"rms": 0.001, "flux": 1.0, "dbspl": 0.0}
        sink.handle(_ctx())

        assert app_context.current_event_label == "Silence"
        assert app_context.current_confidence == 0.0
