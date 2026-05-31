"""
NoiseMonitorSession — background thread that captures 30-second peak windows
during a noise disturbance and writes them to a per-day CSV file.

Lifecycle:
  start()         spawns daemon thread; returns immediately
  reset(sec)      extends (or shortens) the deadline without restarting
  stop()          application shutdown — exits silently, no callback

The on_done callback is invoked with (csv_filename, windows_written).
windows_written == -1 means the session was stopped by privacy activation.
"""

import csv
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..services.privacy_mode import PrivacyMode
from ..settings import SystemMetrics

logger = logging.getLogger(__name__)

_WINDOW_SEC = 30
_NOISE_CSV_HEADER = ["timestamp", "peak_rms", "peak_flux", "peak_dbspl", "cpu", "ram", "temp", "disk"]


class NoiseMonitorSession:
    def __init__(
        self,
        context,
        output_dir: Path,
        duration_sec: int,
        on_done: Callable[[str, int], None],
    ):
        self._context = context
        self._output_dir = output_dir
        self._on_done = on_done
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._deadline = time.time() + duration_sec
        self._csv_filename = f"noise_{datetime.now().strftime('%Y-%m-%d')}.csv"

    @property
    def csv_filename(self) -> str:
        return self._csv_filename

    def start(self) -> None:
        threading.Thread(target=self._worker, name="NoiseMonitor", daemon=True).start()

    def reset(self, duration_sec: int) -> None:
        with self._lock:
            self._deadline = time.time() + duration_sec

    def stop(self) -> None:
        self._stop_event.set()

    def _worker(self) -> None:
        csv_path = self._output_dir / self._csv_filename
        windows_written = 0
        peak_rms = peak_flux = peak_dbspl = 0.0
        window_start = time.time()

        while True:
            if self._stop_event.wait(timeout=1.0):
                return  # application shutdown — no callback

            metrics = self._context.metrics
            peak_rms = max(peak_rms, metrics.get("rms", 0.0))
            peak_flux = max(peak_flux, metrics.get("flux", 0.0))
            peak_dbspl = max(peak_dbspl, metrics.get("dbspl", 0.0))

            now = time.time()
            with self._lock:
                deadline = self._deadline

            if now - window_start < _WINDOW_SEC and now < deadline:
                continue

            # Window boundary (or deadline) reached
            if PrivacyMode().is_active():
                logger.info("🔒 Noise monitor stopped (privacy activated).")
                self._on_done(self._csv_filename, -1)
                return

            self._write_row(
                csv_path,
                [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    f"{peak_rms:.4f}",
                    f"{peak_flux:.1f}",
                    f"{peak_dbspl:.1f}",
                    *self._system_stats(),
                ],
            )
            windows_written += 1
            peak_rms = peak_flux = peak_dbspl = 0.0
            window_start = now

            if now >= deadline:
                break

        self._on_done(self._csv_filename, windows_written)

    def _system_stats(self) -> list[str]:
        try:
            cpu, ram, temp, disk, _ = SystemMetrics.get_stats()
            return [f"{cpu:.1f}", f"{ram:.1f}", f"{temp:.1f}", f"{disk:.1f}"]
        except Exception:
            return ["0.0", "0.0", "0.0", "0.0"]

    def _write_row(self, csv_path: Path, row: list) -> None:
        write_header = not csv_path.exists()
        try:
            with open(csv_path, "a", newline="") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(_NOISE_CSV_HEADER)
                w.writerow(row)
        except Exception as e:
            logger.error(f"❌ Noise monitor write error: {e}")
