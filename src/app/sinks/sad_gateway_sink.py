"""
SAD Gateway Sink.
Sound Activity Detection gate: reads physics metrics from context and
sets context.should_infer to gate the expensive AI inference stage.
Two-stage filter — Stage 1 (RMS/Flux) is cheap; Stage 2 (dBSPL) runs
only when Stage 1 passes and calibration is available.
"""

import logging

from umik_base_app import AudioSink, PipelineContext as AudioCtx

from ..context import PipelineContext
from ..services.prometheus_service import PrometheusService
from ..settings import settings

logger = logging.getLogger(__name__)


class SADGatewaySink(AudioSink):
    """
    Noise gate between BasicMetricsSink and FeatureExtractorSink.
    Sets context.should_infer = True/False each frame.
    """

    def __init__(self, context: PipelineContext):
        self._context = context
        cfg = settings.CONFIG.feature_extractor
        self._threshold_rms = cfg.sad_threshold_rms
        self._threshold_flux = cfg.sad_threshold_flux
        self._threshold_dbspl = cfg.sad_threshold_dbspl
        self._metrics = PrometheusService()

        logger.info(
            f"🔇 SAD Gateway Ready. "
            f"Stage1: [RMS>{self._threshold_rms} | Flux>{self._threshold_flux}] "
            f"Stage2: [dBSPL>{self._threshold_dbspl} (if calibrated)]"
        )

    def handle(self, ctx: AudioCtx) -> None:
        rms = self._context.metrics.get("rms", 0.0)
        flux = self._context.metrics.get("flux", 0.0)
        dbspl = self._context.metrics.get("dbspl", 0.0)

        # Stage 1: cheap RMS / Flux noise gate
        if not ((rms > self._threshold_rms) or (flux > self._threshold_flux)):
            self._set_silence()
            return

        # Stage 2: dBSPL gate (only when mic is calibrated)
        if ctx.can_calculate_dbspl() and dbspl < self._threshold_dbspl:
            self._set_silence()
            return

        self._context.should_infer = True

    def _set_silence(self):
        self._context.should_infer = False
        self._context.current_event_label = "Silence"
        self._context.current_confidence = 0.0
        self._metrics.update_ai_status("Silence", 0.0)
