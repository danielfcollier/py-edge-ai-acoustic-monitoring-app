"""
Smart Buffer Sink.
Captures raw audio into a ring buffer, assembles evidence events,
and hands them off to RecorderTransformerWorker via raw_queue.
No calibration here — that lives in the cold path.
"""

import csv
import logging
import queue
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from umik_base_app import AudioSink, PipelineContext as AudioCtx

from ..context import PipelineContext
from ..settings import SystemMetrics, settings

logger = logging.getLogger(__name__)

class SmartBufferSink(AudioSink):
    """
    State-machine recorder. Captures raw pre-roll + live audio,
    bundles it as a RawEventObject, and pushes to raw_queue.
    FIR calibration is handled downstream by RecorderTransformerWorker.
    """

    def __init__(self, context: PipelineContext, raw_queue: queue.Queue):
        self._context = context
        self._raw_queue = raw_queue
        self._sample_rate = int(settings.AUDIO.SAMPLE_RATE)

        self._config = settings.CONFIG.services
        self._output_dir = self._config.recording_output_path
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._csv_path = Path(self._output_dir, self._config.metrics_csv_buffer_file)

        self._max_duration_sec = self._config.recording_max_seconds
        self._post_roll_sec = self._config.recording_post_roll_seconds

        # State Machine
        self._is_recording = False
        self._event_id = None
        self._start_time = 0.0
        self._fade_start_time = 0.0
        self._audio_buffer = []

        self._init_csv()
        logger.info(f"💾 Smart Buffer Ready. Output: {self._output_dir}")

    def _init_csv(self):
        if not self._csv_path.exists():
            try:
                with open(self._csv_path, "w", newline="") as f:
                    csv.writer(f).writerow([
                        "id", "timestamp", "label", "confidence",
                        "rms", "dbspl", "flux",
                        "cpu", "ram", "temp", "disk", "disk_attached",
                    ])
            except Exception as e:
                logger.error(f"Failed to initialize CSV: {e}")

    def handle(self, ctx: AudioCtx) -> None:
        current_time = time.time()

        triggers = ["record_evidence", "cloud_upload"]
        is_triggered = any(action in self._context.actions_to_take for action in triggers)

        if not self._is_recording:
            if is_triggered:
                self._start_recording(current_time)
        else:
            duration = current_time - self._start_time

            if is_triggered:
                self._fade_start_time = 0.0
            elif self._fade_start_time == 0.0:
                self._fade_start_time = current_time

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

            self._process_chunk(ctx.audio, current_time)

    def _start_recording(self, now: float):
        self._event_id = str(uuid.uuid4())
        self._start_time = now
        self._fade_start_time = 0.0
        self._is_recording = True
        self._audio_buffer = list(self._context.audio_pre_buffer)
        logger.info(f"🔴 Recording Started [ID: {self._event_id[:8]}]")

    def _process_chunk(self, chunk: np.ndarray, now: float):
        self._audio_buffer.append(chunk)

        metrics = self._context.metrics
        label = self._context.current_event_label
        conf = self._context.current_confidence
        cpu, ram, temp, disk, disk_attached = SystemMetrics.get_stats()

        self._write_csv([
            self._event_id,
            datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            label,
            f"{conf:.2f}",
            f"{metrics.get('rms', 0.0):.4f}",
            f"{metrics.get('dbspl', 0.0):.1f}",
            f"{metrics.get('flux', 0.0):.1f}",
            f"{cpu:.1f}", f"{ram:.1f}", f"{temp:.1f}",
            f"{disk:.1f}", f"{disk_attached:.1f}",
        ])

    def _write_csv(self, row):
        try:
            with open(self._csv_path, "a", newline="") as f:
                csv.writer(f).writerow(row)
        except Exception:
            pass

    def _stop_recording(self):
        if not self._audio_buffer:
            self._is_recording = False
            return

        full_audio = np.concatenate(self._audio_buffer)
        max_samples = int(self._max_duration_sec * self._sample_rate)

        if len(full_audio) > max_samples:
            logger.warning(
                f"⚠️ Recording exceeded limit ({len(full_audio)} samples). Truncating."
            )
            full_audio = full_audio[:max_samples]

        duration = len(full_audio) / self._sample_rate

        raw_event = {
            "uuid": self._event_id,
            "timestamp": datetime.now().isoformat(),
            "duration_sec": duration,
            "sample_rate": self._sample_rate,
            "audio_data": full_audio,
            "metadata": {
                "label": self._context.current_event_label,
                "confidence": self._context.current_confidence,
                "calibrated": False,
            },
        }

        try:
            self._raw_queue.put(raw_event, block=False)
            logger.info(f"📦 Raw Event Queued | Dur: {duration:.1f}s")
        except queue.Full:
            logger.error("❌ Raw Queue Full! Dropping recording event.")

        self._is_recording = False
        self._audio_buffer = []
        self._event_id = None
