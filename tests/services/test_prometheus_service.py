"""Tests for PrometheusService."""

from unittest.mock import MagicMock, call, patch

import pytest

from app.services.prometheus_service import (
    MAX_CONF,
    MAX_DBSPL,
    MAX_FLUX,
    MAX_RMS,
    PrometheusService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Isolate each test — destroy the singleton so __init__ runs fresh."""
    PrometheusService._instance = None
    yield
    PrometheusService._instance = None


@pytest.fixture
def svc():
    """PrometheusService with all prometheus_client instruments mocked out."""
    with (
        patch("app.services.prometheus_service.Gauge", side_effect=lambda *a, **kw: MagicMock()),
        patch("app.services.prometheus_service.Counter", side_effect=lambda *a, **kw: MagicMock()),
        patch("app.services.prometheus_service.start_http_server"),
    ):
        service = PrometheusService()
        # Speed up syncer tests — no real sleep needed
        service._reset_interval = 0
        yield service


def _one_syncer_tick(svc):
    """Run exactly one iteration of the syncer loop without sleeping."""
    svc._stop_event.is_set = MagicMock(side_effect=[False, True])
    svc._stop_event.wait = MagicMock(return_value=False)
    svc._syncer_loop()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_same_instance_returned(self):
        with (
            patch("app.services.prometheus_service.Gauge", side_effect=lambda *a, **kw: MagicMock()),
            patch("app.services.prometheus_service.Counter", side_effect=lambda *a, **kw: MagicMock()),
            patch("app.services.prometheus_service.start_http_server"),
        ):
            a = PrometheusService()
            b = PrometheusService()
        assert a is b

    def test_init_not_re_run_on_second_call(self):
        with (
            patch("app.services.prometheus_service.Gauge", side_effect=lambda *a, **kw: MagicMock()) as MockGauge,
            patch("app.services.prometheus_service.Counter", side_effect=lambda *a, **kw: MagicMock()),
            patch("app.services.prometheus_service.start_http_server"),
        ):
            PrometheusService()
            gauge_calls_after_first = MockGauge.call_count
            PrometheusService()
            assert MockGauge.call_count == gauge_calls_after_first


# ---------------------------------------------------------------------------
# update_audio — max-hold semantics
# ---------------------------------------------------------------------------


class TestUpdateAudio:
    def test_higher_rms_replaces_buffer(self, svc):
        svc.update_audio(0.1, 5.0)
        svc.update_audio(0.5, 3.0)
        assert svc._max_rms == pytest.approx(0.5)

    def test_lower_rms_does_not_replace_buffer(self, svc):
        svc.update_audio(0.5, 5.0)
        svc.update_audio(0.1, 3.0)
        assert svc._max_rms == pytest.approx(0.5)

    def test_higher_flux_replaces_buffer(self, svc):
        svc.update_audio(0.1, 5.0)
        svc.update_audio(0.1, 20.0)
        assert svc._max_flux == pytest.approx(20.0)

    def test_lower_flux_does_not_replace_buffer(self, svc):
        svc.update_audio(0.1, 20.0)
        svc.update_audio(0.1, 5.0)
        assert svc._max_flux == pytest.approx(20.0)

    def test_higher_dbspl_replaces_buffer(self, svc):
        svc.update_audio(0.1, 5.0, dbspl=70.0)
        svc.update_audio(0.1, 5.0, dbspl=85.0)
        assert svc._max_dbspl == pytest.approx(85.0)

    def test_lower_dbspl_does_not_replace_buffer(self, svc):
        svc.update_audio(0.1, 5.0, dbspl=85.0)
        svc.update_audio(0.1, 5.0, dbspl=60.0)
        assert svc._max_dbspl == pytest.approx(85.0)

    def test_dbspl_none_leaves_buffer_at_floor(self, svc):
        svc.update_audio(0.1, 5.0, dbspl=None)
        assert svc._max_dbspl == MAX_DBSPL

    def test_dbspl_omitted_leaves_buffer_at_floor(self, svc):
        svc.update_audio(0.1, 5.0)
        assert svc._max_dbspl == MAX_DBSPL


# ---------------------------------------------------------------------------
# update_ai_status — max-hold semantics
# ---------------------------------------------------------------------------


class TestUpdateAiStatus:
    def test_higher_confidence_replaces_buffer(self, svc):
        svc.update_ai_status("Dog", 0.6)
        svc.update_ai_status("Dog", 0.9)
        assert svc._max_conf == pytest.approx(0.9)

    def test_lower_confidence_does_not_replace_buffer(self, svc):
        svc.update_ai_status("Dog", 0.9)
        svc.update_ai_status("Dog", 0.4)
        assert svc._max_conf == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# record_event — counter label filtering
# ---------------------------------------------------------------------------


class TestRecordEvent:
    def test_real_event_increments_counter(self, svc):
        svc.record_event("Dog")
        svc._c_events.labels(category="Dog").inc.assert_called_once()

    def test_silence_not_counted(self, svc):
        svc.record_event("Silence")
        svc._c_events.labels.assert_not_called()

    def test_unknown_not_counted(self, svc):
        svc.record_event("Unknown")
        svc._c_events.labels.assert_not_called()

    def test_multiple_categories_each_get_their_own_label(self, svc):
        svc.record_event("Dog")
        svc.record_event("GlassBreak")
        assert svc._c_events.labels.call_count == 2
        calls = [c.kwargs for c in svc._c_events.labels.call_args_list]
        assert {"category": "Dog"} in calls
        assert {"category": "GlassBreak"} in calls


# ---------------------------------------------------------------------------
# update_system — direct gauge writes
# ---------------------------------------------------------------------------


class TestUpdateSystem:
    def test_all_gauges_set(self, svc):
        svc.update_system(cpu=55.0, temp=62.0, ram=40.0, disk=30.0, disk_attached=20.0)

        svc._g_cpu.set.assert_called_once_with(55.0)
        svc._g_temp.set.assert_called_once_with(62.0)
        svc._g_ram.set.assert_called_once_with(40.0)
        svc._g_disk.set.assert_called_once_with(30.0)
        svc._g_disk_attached.set.assert_called_once_with(20.0)

    def test_optional_args_default_to_zero(self, svc):
        svc.update_system(cpu=10.0, temp=45.0)

        svc._g_ram.set.assert_called_once_with(0.0)
        svc._g_disk.set.assert_called_once_with(0.0)
        svc._g_disk_attached.set.assert_called_once_with(0.0)


# ---------------------------------------------------------------------------
# _syncer_loop — flush behaviour
# ---------------------------------------------------------------------------


class TestSyncerLoop:
    def test_rms_and_flux_always_flushed(self, svc):
        svc.update_audio(0.3, 12.0)
        _one_syncer_tick(svc)

        svc._g_rms.set.assert_called_once_with(pytest.approx(0.3))
        svc._g_flux.set.assert_called_once_with(pytest.approx(12.0))

    def test_dbspl_gauge_set_when_calibrated_data_arrived(self, svc):
        svc.update_audio(0.1, 5.0, dbspl=75.0)
        _one_syncer_tick(svc)

        svc._g_dbspl.set.assert_called_once_with(pytest.approx(75.0))

    def test_dbspl_gauge_not_set_when_no_calibrated_data(self, svc):
        svc.update_audio(0.1, 5.0)  # no dbspl
        _one_syncer_tick(svc)

        svc._g_dbspl.set.assert_not_called()

    def test_confidence_flushed(self, svc):
        svc.update_ai_status("Dog", 0.88)
        _one_syncer_tick(svc)

        svc._g_conf.set.assert_called_once_with(pytest.approx(0.88))

    def test_buffers_reset_to_floor_after_flush(self, svc):
        svc.update_audio(0.5, 20.0, dbspl=80.0)
        svc.update_ai_status("Dog", 0.9)
        _one_syncer_tick(svc)

        assert svc._max_rms == MAX_RMS
        assert svc._max_flux == MAX_FLUX
        assert svc._max_dbspl == MAX_DBSPL
        assert svc._max_conf == MAX_CONF

    def test_stop_event_exits_loop(self, svc):
        svc._stop_event.is_set = MagicMock(return_value=True)
        svc._stop_event.wait = MagicMock(return_value=False)
        svc._syncer_loop()

        svc._stop_event.wait.assert_not_called()
