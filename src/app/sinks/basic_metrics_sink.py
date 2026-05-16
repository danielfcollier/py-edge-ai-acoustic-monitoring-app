"""
Basic Metrics Sink.
Hot-path physics engine: computes RMS, Flux, and dBSPL on every chunk
and writes them to the shared PipelineContext for downstream sinks.
"""

import logging

from umik_base_app import AudioSink, PipelineContext as AudioCtx
from umik_base_app.core.audio_metrics import AudioMetrics

from ..context import PipelineContext
from ..services.prometheus_service import PrometheusService
from ..settings import settings

logger = logging.getLogger(__name__)


class BasicMetricsSink(AudioSink):
    """
    Runs on every audio chunk regardless of content.
    Populates context.metrics and maintains the pre-roll buffer.
    """

    def __init__(self, context: PipelineContext):
        self._context = context
        self._input_sr = int(settings.AUDIO.SAMPLE_RATE)
        self._metrics = PrometheusService()

    def handle(self, ctx: AudioCtx) -> None:
        # 1. Maintain pre-roll buffer for evidence recording
        self._context.audio_pre_buffer.append(ctx.audio)

        # 2. Cheap physics (always)
        rms = AudioMetrics.rms(ctx.audio)
        flux = AudioMetrics.flux(ctx.audio, self._input_sr)

        self._context.metrics["rms"] = rms
        self._context.metrics["flux"] = flux
        self._context.metrics["dbspl"] = 0.0

        # 3. Precision physics (only when mic is calibrated)
        if ctx.can_calculate_dbspl():
            dbfs = AudioMetrics.dBFS(ctx.audio)
            dbspl = AudioMetrics.dBSPL(dbfs, ctx.sensitivity_dbfs, ctx.reference_dbspl)
            self._context.metrics["dbspl"] = dbspl

        # 4. Prometheus — omit dBSPL when mic is uncalibrated
        dbspl = self._context.metrics["dbspl"]
        self._metrics.update_audio(rms, flux, dbspl=dbspl if dbspl > 0 else None)
