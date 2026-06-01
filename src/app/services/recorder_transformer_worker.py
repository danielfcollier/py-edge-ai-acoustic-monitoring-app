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
from pathlib import Path

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
        self._scorers: list[tuple] = []  # (MfccProfileConfig, MfccScorer)

        if settings.CONFIG.services.save_calibrated_wave:
            self._init_calibration()
        self._init_mfcc_scorers()

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

    def _init_mfcc_scorers(self):
        try:
            from scripts.recognition.mfcc_core import MfccScorer
        except ImportError:
            return

        output_dir = settings.CONFIG.services.recording_output_path
        for cfg in settings.CONFIG.mfcc_profiles:
            try:
                if cfg.profile_file:
                    p = Path(cfg.profile_file)
                    if not p.is_absolute():
                        p = output_dir / p
                    if not p.exists():
                        logger.warning(f"⚠️ MFCC profile file not found: {p}  (skipping '{cfg.target_label}')")
                        continue
                    scorer = MfccScorer.from_profile_file(p, cfg.target_label, method=cfg.method)
                    source = p.name
                else:
                    p = Path(cfg.labels_file)
                    if not p.is_absolute():
                        p = output_dir / p
                    if not p.exists():
                        logger.warning(f"⚠️ MFCC labels file not found: {p}  (skipping '{cfg.target_label}')")
                        continue
                    scorer = MfccScorer(p, cfg.target_label, method=cfg.method)
                    source = p.name

                if scorer.ready:
                    self._scorers.append((cfg, scorer))
                    logger.info(
                        f"🎯 MFCC scorer loaded: '{cfg.target_label}' from {source}"
                        f" ({len(scorer._target_vecs)} examples)"
                    )
                else:
                    logger.warning(f"⚠️ MFCC scorer for '{cfg.target_label}' has no examples — skipping.")
            except Exception as e:
                logger.error(f"❌ MFCC scorer init failed for '{cfg.target_label}': {e}")

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
        if self._calibrator:
            try:
                self._calibrator.reset_state()
                calibrated_audio = self._calibrator.apply(event["audio_data"])
                event = {
                    **event,
                    "audio_data": calibrated_audio,
                    "metadata": {**event.get("metadata", {}), "calibrated": True},
                }
            except Exception as e:
                logger.error(f"❌ FIR calibration failed for {event['uuid'][:8]}: {e}. Using raw audio.")

        if self._scorers:
            event = self._apply_mfcc_scoring(event)

        return event

    def _apply_mfcc_scoring(self, event: dict) -> dict:
        label = event.get("metadata", {}).get("label", "")
        sample_rate = event.get("sample_rate", 48000)
        audio = event["audio_data"]

        mfcc_scores: dict[str, float] = {}
        effective_actions: set[str] | None = None

        for cfg, scorer in self._scorers:
            if cfg.trigger_on_labels and label not in cfg.trigger_on_labels:
                continue
            score = scorer.score_audio(audio, sample_rate)
            if score is None:
                continue
            mfcc_scores[cfg.target_label] = round(score, 4)
            matched = score >= cfg.threshold
            logger.info(
                f"🎯 MFCC '{cfg.target_label}': {score:.3f} "
                f"({'✅ match' if matched else '❌ no match'}, threshold {cfg.threshold})"
            )
            actions = set(cfg.actions_on_match if matched else cfg.actions_on_no_match)
            effective_actions = actions if effective_actions is None else effective_actions & actions

        if not mfcc_scores:
            return event

        metadata = {
            **event.get("metadata", {}),
            "mfcc_scores": mfcc_scores,
            "effective_actions": list(effective_actions) if effective_actions is not None else None,
        }
        return {**event, "metadata": metadata}
