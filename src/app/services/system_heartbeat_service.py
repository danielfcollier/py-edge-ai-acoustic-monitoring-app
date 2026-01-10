"""
System Heartbeat Service.
Logs a 'SystemCheck' event periodically to the CSV to ensure
integrity and visibility during long periods of silence.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import csv
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from ..settings import SystemMetrics, settings

logger = logging.getLogger(__name__)


INTERVAL_SECONDS = 3600
METRICS_CSV_BUFFER_FILE = "metrics_buffer.csv"


class SystemHeartbeatService:
    def __init__(self, interval_seconds: int = INTERVAL_SECONDS):
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._output_dir = settings.CONFIG.services.recording_output_path
        self._csv_path = Path(self._output_dir, METRICS_CSV_BUFFER_FILE)

    def start(self):
        """Starts the background heartbeat worker."""
        threading.Thread(target=self._worker, name="SystemHeartbeat", daemon=True).start()
        logger.info(f"💓 Heartbeat Service Started (Interval: {self._interval}s)")

    def stop(self):
        """Stops the service."""
        self._stop_event.set()

    def _worker(self):
        # Wait a bit before the first log to let other services settle
        if self._stop_event.wait(10.0):
            return

        while not self._stop_event.is_set():
            self._log_heartbeat()

            # Wait for next interval or stop signal
            if self._stop_event.wait(self._interval):
                break

    def _log_heartbeat(self):
        """Writes the SystemCheck row to the CSV."""
        if not self._csv_path.exists():
            return

        try:
            cpu, ram, temp, disk, disk_attached = SystemMetrics.get_stats()
            # id, timestamp, label, confidence, rms, dbspl, flux, cpu, ram, temp, rom, rom attached
            row = [
                f"heartbeat-{uuid.uuid4()}",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "SystemCheck",
                "1.00",  # Confidence
                "0.0000",  # RMS
                "0.0",  # dBSPL
                "0.0",  # Flux
                f"{cpu:.1f}",
                f"{ram:.1f}",
                f"{temp:.1f}",
                f"{disk:.1f}",
                f"{disk_attached:.1f}",
            ]

            # 3. Write to CSV
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(row)

            logger.debug(f"📝 System Heartbeat logged (Temp: {temp:.1f}°C)")

        except Exception as e:
            logger.error(f"❌ Failed to log system heartbeat: {e}")
