"""Unit tests for NoiseMonitorSession."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.noise_monitor_session import NoiseMonitorSession


def _make_context(rms=0.05, flux=10.0, dbspl=65.0):
    ctx = MagicMock()
    ctx.metrics = {"rms": rms, "flux": flux, "dbspl": dbspl}
    return ctx


def _run_session(session, timeout=2.0):
    """Start _worker in a thread, return after it finishes or timeout."""
    t = threading.Thread(target=session._worker)
    t.start()
    t.join(timeout=timeout)
    return t


def _instant_wait(session):
    """Patch stop_event.wait to return False immediately (no sleeping)."""
    session._stop_event.wait = MagicMock(return_value=False)


_FAKE_STATS = (10.0, 50.0, 55.0, 30.0, 0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestNoiseMonitorSessionBasics:
    def test_csv_filename_uses_today(self):
        session = NoiseMonitorSession(MagicMock(), Path("/tmp"), 3600, lambda f, n: None)
        assert session.csv_filename.startswith("noise_")
        assert session.csv_filename.endswith(".csv")

    def test_reset_extends_deadline(self):
        session = NoiseMonitorSession(MagicMock(), Path("/tmp"), 3600, lambda f, n: None)
        before = session._deadline
        time.sleep(0.01)
        session.reset(7200)
        assert session._deadline > before + 3600

    def test_stop_prevents_callback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.noise_monitor_session._WINDOW_SEC", 0)
        done = []
        session = NoiseMonitorSession(_make_context(), tmp_path, 0, lambda f, n: done.append(n))

        # stop immediately — worker should return without calling on_done
        session.stop()

        t = _run_session(session)
        assert not t.is_alive()
        assert done == []


# ---------------------------------------------------------------------------
# Normal expiry
# ---------------------------------------------------------------------------

class TestNormalExpiry:
    def test_writes_csv_and_calls_on_done(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.noise_monitor_session._WINDOW_SEC", 0)
        done = []
        session = NoiseMonitorSession(_make_context(), tmp_path, 0,
                                      lambda f, n: done.append((f, n)))
        _instant_wait(session)

        with (
            patch("app.services.noise_monitor_session.PrivacyMode",
                  return_value=MagicMock(is_active=MagicMock(return_value=False))),
            patch("app.services.noise_monitor_session.SystemMetrics.get_stats",
                  return_value=_FAKE_STATS),
        ):
            t = _run_session(session)

        assert not t.is_alive(), "worker thread did not finish"
        assert len(done) == 1
        filename, windows = done[0]
        assert windows >= 1
        csv_path = tmp_path / filename
        assert csv_path.exists()
        lines = csv_path.read_text().splitlines()
        assert lines[0].startswith("timestamp")  # header
        assert len(lines) >= 2  # at least one data row

    def test_csv_row_contains_metrics(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.noise_monitor_session._WINDOW_SEC", 0)
        ctx = _make_context(rms=0.1234, flux=42.5, dbspl=67.8)
        session = NoiseMonitorSession(ctx, tmp_path, 0, lambda f, n: None)
        _instant_wait(session)

        with (
            patch("app.services.noise_monitor_session.PrivacyMode",
                  return_value=MagicMock(is_active=MagicMock(return_value=False))),
            patch("app.services.noise_monitor_session.SystemMetrics.get_stats",
                  return_value=_FAKE_STATS),
        ):
            _run_session(session)

        csv_path = tmp_path / session.csv_filename
        data_row = csv_path.read_text().splitlines()[1]
        assert "0.1234" in data_row
        assert "42.5" in data_row
        assert "67.8" in data_row


# ---------------------------------------------------------------------------
# Privacy mid-session
# ---------------------------------------------------------------------------

class TestPrivacyStop:
    def test_privacy_stops_session_with_negative_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.noise_monitor_session._WINDOW_SEC", 0)
        done = []
        session = NoiseMonitorSession(_make_context(), tmp_path, 0,
                                      lambda f, n: done.append((f, n)))
        _instant_wait(session)

        with (
            patch("app.services.noise_monitor_session.PrivacyMode",
                  return_value=MagicMock(is_active=MagicMock(return_value=True))),
            patch("app.services.noise_monitor_session.SystemMetrics.get_stats",
                  return_value=_FAKE_STATS),
        ):
            t = _run_session(session)

        assert not t.is_alive()
        assert len(done) == 1
        _, windows = done[0]
        assert windows == -1

    def test_privacy_stop_writes_no_csv_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.noise_monitor_session._WINDOW_SEC", 0)
        session = NoiseMonitorSession(_make_context(), tmp_path, 0, lambda f, n: None)
        _instant_wait(session)

        with (
            patch("app.services.noise_monitor_session.PrivacyMode",
                  return_value=MagicMock(is_active=MagicMock(return_value=True))),
            patch("app.services.noise_monitor_session.SystemMetrics.get_stats",
                  return_value=_FAKE_STATS),
        ):
            _run_session(session)

        csv_path = tmp_path / session.csv_filename
        assert not csv_path.exists()
