"""
E2E tests for HealthMonitorService and SystemHeartbeatService.

What is tested:
  - healthchecks.io ping succeeds with a real HTTP GET (requires hc_ping_url in YAML).
  - HealthMonitorService._send_ping does not raise on a valid URL.
  - SystemHeartbeatService._log_heartbeat writes a valid CSV row with correct columns.
  - The CSV row contains real system metrics (CPU, RAM, temp).
"""

import csv
import time

import httpx
import pytest

from app.services.health_monitor_service import HealthMonitorService
from app.services.system_heartbeat_service import SystemHeartbeatService


# ---------------------------------------------------------------------------
# healthchecks.io ping
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.heartbeat
def test_hc_ping_returns_2xx(require_hc_url):
    """Direct HTTP GET to the healthchecks.io URL returns a 2xx status."""
    response = httpx.get(require_hc_url, timeout=10.0)
    assert response.status_code < 300, (
        f"HC ping returned {response.status_code} — check the URL in security_policy.yaml"
    )


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_health_monitor_send_ping_no_raise(require_hc_url):
    """HealthMonitorService._send_ping does not raise on a valid URL."""
    svc = HealthMonitorService()
    try:
        svc._send_ping(require_hc_url)
    finally:
        svc.stop()


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_health_monitor_start_stop():
    """HealthMonitorService starts a background thread and stops cleanly."""
    svc = HealthMonitorService()
    svc.start()
    time.sleep(0.2)
    svc.stop()  # must not hang or raise


# ---------------------------------------------------------------------------
# CSV heartbeat logging
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = 12  # id, timestamp, label, confidence, rms, dbspl, flux, cpu, ram, temp, disk, disk_attached


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_heartbeat_writes_csv_row(tmp_path, monkeypatch, e2e_settings):
    """SystemHeartbeatService._log_heartbeat appends one valid row to the CSV."""
    csv_path = tmp_path / "metrics_buffer.csv"
    csv_path.touch()

    svc = SystemHeartbeatService(interval_seconds=3600)
    svc._csv_path = csv_path  # override after init

    svc._log_heartbeat()

    rows = list(csv.reader(csv_path.read_text().splitlines()))
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    assert len(rows[0]) == _EXPECTED_COLUMNS, f"Expected {_EXPECTED_COLUMNS} columns, got {len(rows[0])}"


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_heartbeat_csv_label_is_systemcheck(tmp_path):
    """The label column in the heartbeat CSV row is always 'SystemCheck'."""
    csv_path = tmp_path / "metrics_buffer.csv"
    csv_path.touch()

    svc = SystemHeartbeatService(interval_seconds=3600)
    svc._csv_path = csv_path

    svc._log_heartbeat()

    rows = list(csv.reader(csv_path.read_text().splitlines()))
    label = rows[0][2]
    assert label == "SystemCheck"


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_heartbeat_csv_metrics_are_floats(tmp_path):
    """CPU, RAM, temp, disk columns in the heartbeat row are parseable floats."""
    csv_path = tmp_path / "metrics_buffer.csv"
    csv_path.touch()

    svc = SystemHeartbeatService(interval_seconds=3600)
    svc._csv_path = csv_path

    svc._log_heartbeat()

    rows = list(csv.reader(csv_path.read_text().splitlines()))
    # columns: id(0), ts(1), label(2), conf(3), rms(4), dbspl(5), flux(6),
    #          cpu(7), ram(8), temp(9), disk(10), disk_attached(11)
    for col_index, col_name in [(7, "cpu"), (8, "ram"), (9, "temp"), (10, "disk")]:
        val = float(rows[0][col_index])
        assert val >= 0.0, f"{col_name} should be non-negative, got {val}"


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_heartbeat_skips_when_csv_missing(tmp_path):
    """_log_heartbeat does nothing (no error) when the CSV file does not exist."""
    csv_path = tmp_path / "metrics_buffer.csv"
    # intentionally NOT creating the file

    svc = SystemHeartbeatService(interval_seconds=3600)
    svc._csv_path = csv_path

    svc._log_heartbeat()  # must not raise

    assert not csv_path.exists()


@pytest.mark.e2e
@pytest.mark.heartbeat
def test_heartbeat_accumulates_rows(tmp_path):
    """Multiple _log_heartbeat calls append rows without overwriting."""
    csv_path = tmp_path / "metrics_buffer.csv"
    csv_path.touch()

    svc = SystemHeartbeatService(interval_seconds=3600)
    svc._csv_path = csv_path

    svc._log_heartbeat()
    svc._log_heartbeat()
    svc._log_heartbeat()

    rows = list(csv.reader(csv_path.read_text().splitlines()))
    assert len(rows) == 3
