"""
Recorder Transformer Worker.
Cold-path async worker: reads raw audio events from raw_queue,
optionally applies FIR calibration, then forwards to upload_queue.

This is the "Studio Engineer" stage — the heavy FIR convolution runs
here in a background thread so the real-time pipeline is never blocked.
"""

import logging
import queue
import threading

from umik_base_app.transformers.calibrator_transformer import CalibratorTransformer

from ..settings import settings

logger = logging.getLogger(__name__)

WORKER_TIMEOUT_SECONDS = 1.0


class RecorderTransformerWorker:
    """
    Bridges the raw_queue (from SmartBufferSink) to the upload_queue
    (consumed by CloudUploaderService). If save_calibrated_wave is True,
    applies gain + FIR correction to the raw audio before forwarding.
    On calibration failure, forwards the raw audio unchanged so evidence
    is never silently dropped.
    """

    def __init__(self, raw_queue: queue.Queue, upload_queue: queue.Queue):
        self._raw_queue = raw_queue
        self._upload_queue = upload_queue
        self._stop_event = threading.Event()
        self._calibrator = None

        if settings.CONFIG.services.save_calibrated_wave:
            self._init_calibration()

    def _init_calibration(self):
        try:
            cal_file_path = settings.CONFIG.hardware.calibration_file
            if not cal_file_path:
                raise FileNotFoundError("Calibration file path not configured in settings.")

            hw = settings.HARDWARE
            self._calibrator = CalibratorTransformer(
                calibration_file_path=cal_file_path,
                sample_rate=int(settings.AUDIO.SAMPLE_RATE),
                num_taps=settings.CONFIG.hardware.fir_num_taps,
                nominal_sensitivity_dbfs=getattr(hw, "NOMINAL_SENSITIVITY_DBFS", 0),
                reference_dbspl=getattr(hw, "REFERENCE_DBSPL", 94),
            )
            logger.info("🔊 FIR Calibration transformer ready (async worker).")
        except Exception as e:
            logger.critical(f"❌ Calibration init failed: {e}. Evidence will be uploaded uncalibrated.")
            self._calibrator = None

    def start(self):
        threading.Thread(target=self._worker, name="RecorderTransformer", daemon=True).start()
        logger.info("🎛️  Recorder Transformer Worker started.")

    def stop(self):
        self._stop_event.set()

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                raw_event = self._raw_queue.get(timeout=WORKER_TIMEOUT_SECONDS)
            except queue.Empty:
                continue

            processed = self._transform(raw_event)

            try:
                self._upload_queue.put(processed, block=False)
            except queue.Full:
                logger.error(f"❌ Upload queue full — dropping event {processed['uuid'][:8]}.")

    def _transform(self, event: dict) -> dict:
        if not self._calibrator:
            return event

        try:
            self._calibrator.reset_state()
            calibrated_audio = self._calibrator.apply(event["audio_data"])
            return {
                **event,
                "audio_data": calibrated_audio,
                "metadata": {**event.get("metadata", {}), "calibrated": True},
            }
        except Exception as e:
            logger.error(f"❌ FIR calibration failed for {event['uuid'][:8]}: {e}. Using raw audio.")
            return event
