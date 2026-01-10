"""
Smart Recorder Sink.
The "Executor" of the pipeline with Optional Calibration Capability.
- Responsibilities: Buffer management, CSV logging, Audio Bundling.
- Features:
    - Pre-roll/Post-roll buffering.
    - Integrated Calibration (Gain + FIR) via Feature Flag.
    - Incremental Processing: Calibrates audio on-the-fly to avoid CPU spikes.
    - Queue Handoff (No blocking Disk IO for WAVs).

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import csv
import logging
import queue
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from umik_base_app import AudioSink
from umik_base_app.transformers.calibrator_transformer import CalibratorTransformer

from ..context import PipelineContext
from ..settings import SystemMetrics, settings

logger = logging.getLogger(__name__)


RECORDING_MAX_SECONDS = 60
RECORDING_POST_ROLL_OUT_SECONDS = 10
METRICS_CSV_BUFFER_FILE = "metrics_buffer.csv"

UMIK1_NOMINAL_SENSITIVITY_DBFS = 0
UMIK1_REFERENCE_DBSPL = 94
CALIBRATION_FIR_NUM_TAPS = 1024


class SmartRecorderSink(AudioSink):
    """
    State-machine based recorder.
    It decides *how* to record (Pre-roll, Post-roll, Metadata aggregation).
    If 'save_calibrated_wave' is True, it applies Gain and FIR incrementally.
    """

    def __init__(self, context: PipelineContext, upload_queue: queue.Queue):
        """
        :param context: Shared pipeline context.
        :param upload_queue: Thread-safe queue to push finished EventObjects to.
        """
        self._context = context
        self._upload_queue = upload_queue
        self._sample_rate = int(settings.AUDIO.SAMPLE_RATE)

        # Configuration
        self._config = settings.CONFIG.services
        self._output_dir = self._config.recording_output_path
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._csv_path = Path(self._output_dir, METRICS_CSV_BUFFER_FILE)

        # Calibration Settings
        self._save_calibrated = self._config.save_calibrated_wave
        self._calibrator = None

        if self._save_calibrated:
            self._init_calibration_assets()

        # Constraints
        self._max_duration_sec = RECORDING_MAX_SECONDS
        self._post_roll_sec = RECORDING_POST_ROLL_OUT_SECONDS

        # State Machine
        self._is_recording = False
        self._event_id = None
        self._start_time = 0.0
        self._fade_start_time = 0.0
        self._audio_buffer = []

        self._init_csv()
        logger.info(f"💾 Smart Recorder Ready. Output: {self._output_dir} | Calibrated: {self._save_calibrated}")

    def _init_calibration_assets(self):
        """
        Loads Sensitivity and instantiates the CalibratorTransformer.
        STRICT MODE: Raises exception if calibration fails to ensure data integrity.
        """
        try:
            cal_file_path = settings.CONFIG.hardware.calibration_file

            if not cal_file_path:
                raise FileNotFoundError("Calibration file path not configured in settings.")

            self._calibrator = CalibratorTransformer(
                calibration_file_path=cal_file_path,
                sample_rate=self._sample_rate,
                num_taps=CALIBRATION_FIR_NUM_TAPS,
                nominal_sensitivity_dbfs=UMIK1_NOMINAL_SENSITIVITY_DBFS,
                reference_dbspl=UMIK1_REFERENCE_DBSPL,
            )
            logger.info("🔊 Calibration Transformer loaded successfully.")

        except (ValueError, FileNotFoundError, RuntimeError) as e:
            # Recoverable errors: File missing, bad format, or empty data.
            # Strategy: Disable calibration feature but allow app to continue.
            logger.critical(f"❌ Calibration Setup Failed: {e}")
            raise e

        except Exception as e:
            logger.error(f"❌ Unexpected Calibration Error: {e}", exc_info=True)
            raise e

    def _init_csv(self):
        """Creates the CSV file with headers if it doesn't exist."""
        if not self._csv_path.exists():
            try:
                with open(self._csv_path, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "id",
                            "timestamp",
                            "label",
                            "confidence",
                            "rms",
                            "dbspl",
                            "flux",
                            "cpu",
                            "ram",
                            "temp",
                            "disk",
                            "disk_attached",
                        ]
                    )
            except Exception as e:
                logger.error(f"Failed to initialize CSV: {e}")

    def handle_audio(self, audio_chunk: np.ndarray, timestamp: float) -> None:
        current_time = time.time()

        triggers = ["record_evidence", "cloud_upload"]
        is_triggered = any(action in self._context.actions_to_take for action in triggers)

        # --- STATE MACHINE ---

        if not self._is_recording:
            if is_triggered:
                self._start_recording(current_time)

        else:
            duration = current_time - self._start_time

            # Manage Fade Out
            if is_triggered:
                self._fade_start_time = 0.0
            elif self._fade_start_time == 0.0:
                self._fade_start_time = current_time

            # Check Stop Conditions
            should_stop = False

            if duration >= self._max_duration_sec:
                logger.info("🛑 Max recording duration reached (60s).")
                should_stop = True

            elif self._fade_start_time > 0 and (current_time - self._fade_start_time) > self._post_roll_sec:
                logger.info("🛑 Post-roll silence complete.")
                should_stop = True

            if should_stop:
                self._stop_recording()
                return

            self._process_chunk(audio_chunk, current_time)

    def _start_recording(self, now: float):
        self._event_id = str(uuid.uuid4())
        self._start_time = now
        self._fade_start_time = 0.0
        self._is_recording = True

        raw_preroll_chunks = list(self._context.audio_pre_buffer)

        if self._save_calibrated and self._calibrator:
            self._calibrator.reset_state()

            if raw_preroll_chunks:
                full_raw_preroll = np.concatenate(raw_preroll_chunks)
                calibrated_preroll = self._calibrator.apply(full_raw_preroll)

                self._audio_buffer = [calibrated_preroll]
            else:
                self._audio_buffer = []
        else:
            self._audio_buffer = raw_preroll_chunks

        logger.info(f"🔴 Recording Started [ID: {self._event_id[:8]}]")

    def _process_chunk(self, chunk: np.ndarray, now: float):
        if self._save_calibrated and self._calibrator:
            processed_chunk = self._calibrator.apply(chunk)
            self._audio_buffer.append(processed_chunk)
        else:
            self._audio_buffer.append(chunk)

        # --- CSV LOGGING (On Raw Metrics) ---
        metrics = self._context.metrics
        rms = metrics.get("rms", 0.0)
        dbspl = metrics.get("dbspl", 0.0)
        flux = metrics.get("flux", 0.0)

        label = self._context.current_event_label
        conf = self._context.current_confidence

        cpu, ram, temp, disk, disk_attached = SystemMetrics.get_stats()

        row = [
            self._event_id,
            datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            label,
            f"{conf:.2f}",
            f"{rms:.4f}",
            f"{dbspl:.1f}",
            f"{flux:.1f}",
            f"{cpu:.1f}",
            f"{ram:.1f}",
            f"{temp:.1f}",
            f"{disk:.1f}",
            f"{disk_attached:.1f}",
        ]

        self._write_csv(row)

    def _write_csv(self, row):
        try:
            with open(self._csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception:
            pass

    def _stop_recording(self):
        if not self._audio_buffer:
            self._is_recording = False
            return

        full_audio = np.concatenate(self._audio_buffer)
        duration = len(full_audio) / self._sample_rate

        event_object = {
            "uuid": self._event_id,
            "timestamp": datetime.now().isoformat(),
            "duration_sec": duration,
            "sample_rate": self._sample_rate,
            "audio_data": full_audio,
            "metadata": {
                "label": self._context.current_event_label,
                "confidence": self._context.current_confidence,
                "calibrated": self._save_calibrated,
            },
        }

        try:
            self._upload_queue.put(event_object, block=False)
            logger.info(f"📦 Event Bundled & Queued | Dur: {duration:.1f}s]")
        except queue.Full:
            logger.error("❌ Upload Queue Full! Dropping recording event.")

        # Cleanup
        self._is_recording = False
        self._audio_buffer = []
        self._event_id = None
