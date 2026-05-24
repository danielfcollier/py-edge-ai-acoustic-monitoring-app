"""
Top Metrics Sink.
Prints peak RMS, Flux, and (when calibrated) dBSPL at the end of each interval.
Useful for calibrating SAD and dB thresholds in policy YAML files.
Activated with --top-metrics on ai-acoustic-monitor-run.
"""

import logging
import time

from umik_base_app import AudioSink, PipelineContext as AudioCtx

from ..context import PipelineContext

logger = logging.getLogger(__name__)


class TopMetricsSink(AudioSink):
    """Accumulates peak RMS, Flux, and dBSPL and prints a summary every interval."""

    def __init__(self, context: PipelineContext, interval: int):
        self._context = context
        self._interval = interval
        self._reset()
        logger.info(f"📊 Top Metrics active — summary every {interval}s")

    def handle(self, ctx: AudioCtx) -> None:
        rms = self._context.metrics.get("rms", 0.0)
        flux = self._context.metrics.get("flux", 0.0)
        dbspl = self._context.metrics.get("dbspl", 0.0)

        if rms > self._peak_rms:
            self._peak_rms = rms
        if flux > self._peak_flux:
            self._peak_flux = flux
        if dbspl > 0 and dbspl > self._peak_dbspl:
            self._peak_dbspl = dbspl

        if time.monotonic() - self._window_start >= self._interval:
            self._print_summary()
            self._reset()

    def _print_summary(self) -> None:
        if self._peak_rms == 0.0:
            return

        parts = [
            f"RMS {self._peak_rms:.4f}",
            f"Flux {self._peak_flux:.1f}",
        ]
        if self._peak_dbspl > 0:
            parts.append(f"dBSPL {self._peak_dbspl:.1f}")

        logger.info("📊  Peak (%ds) │ %s", self._interval, "  │  ".join(parts))

    def _reset(self) -> None:
        self._peak_rms = 0.0
        self._peak_flux = 0.0
        self._peak_dbspl = 0.0
        self._window_start = time.monotonic()
