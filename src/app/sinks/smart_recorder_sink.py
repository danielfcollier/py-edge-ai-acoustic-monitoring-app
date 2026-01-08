"""
Smart Recorder Sink.
Saves audio recordings to disk and logs metadata/metrics to a CSV buffer.
Supports calibrated SPL calculation if available.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import csv
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import psutil
from scipy.io import wavfile
from umik_base_app import AudioSink

from ..context import PipelineContext
from ..settings import settings

logger = logging.getLogger(__name__)


class SmartRecorderSink(AudioSink):
    """
    Records audio clips based on Policy Engine triggers.
    Includes pre-roll audio, system metrics, and calibrated SPL logging.
    """

    def __init__(self, context: PipelineContext):
        self._context = context
        self._sample_rate = int(settings.AUDIO.SAMPLE_RATE)
        self._output_dir = Path("recordings")
        self._output_dir.mkdir(exist_ok=True)

        # CSV Configuration
        self._csv_path = self._output_dir / "metrics_buffer.csv"
        self._ensure_csv_header()

        # Recording Configuration
        self._silence_timeout = 10.0
        self._max_duration = 60.0

        # State Initialization
        self._is_recording = False
        self._start_time = 0.0
        self._last_trigger_time = 0.0
        self._recording_buffer = []

        # Metrics State
        self._event_max_db = -90.0
        self._event_max_conf = 0.0
        self._detected_label = "Unknown"
        self._minor_alerts = set()
        
        self._last_spectrum = None # For Flux calc

    def _ensure_csv_header(self):
        """Creates the CSV with headers if it doesn't exist."""
        if not self._csv_path.exists():
            with open(self._csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "timestamp",
                        "event_type",
                        "confidence",
                        "duration",
                        "dbspl",
                        "cpu_percent",
                        "ram_percent",
                        "temp_c",
                    ]
                )

    def _get_system_metrics(self):
        """Captures Pi system stats."""
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        temp = 0.0
        try:
            temps = psutil.sensors_temperatures()
            if "cpu_thermal" in temps:
                temp = temps["cpu_thermal"][0].current
        except Exception:
            pass
        return cpu, ram, temp

    def handle_audio(self, audio_chunk: np.ndarray, timestamp) -> None:
        # 1. Check for new Triggers
        if "cloud_upload" in self._context.actions_to_take:
            self._handle_trigger(time.time())
        
        flux, self._last_spectrum = calculate_flux(audio_chunk, self._last_spectrum)
        # Store in Context so Policy Engine can see it
        self._context.metrics['flux'] = flux

        # 2. Process Recording State
        if self._context.is_recording:
            self._process_recording(audio_chunk, time.time())

    def _handle_trigger(self, now):
        """Called when a relevant policy action is detected."""
        label = self._context.current_event_label or "Unknown"
        conf = self._context.current_confidence

        if not self._context.is_recording:
            logger.info(f"🔴 Event '{label}' started recording (with pre-roll).")
            self._start_recording(now)

        # Update Trigger State
        self._last_trigger_time = now
        self._detected_label = label  # Update label to most recent trigger
        self._minor_alerts.add(label)
        self._event_max_conf = max(self._event_max_conf, conf)

    def _start_recording(self, now):
        """Initialize recording state."""
        self._context.is_recording = True
        self._start_time = now
        self._last_trigger_time = now

        # Reset Metrics
        self._event_max_db = -90.0
        self._event_max_conf = 0.0
        self._minor_alerts = set()
        self._detected_label = self._context.current_event_label or "Unknown"

        # Dump Pre-Roll Buffer
        self._context.recording_buffer = list(self._context.audio_pre_buffer)

    def _process_recording(self, audio_chunk, now):
        """Accumulate audio, calculate real-time metrics, and check stop conditions."""
        self._context.recording_buffer.append(audio_chunk)

        # --- A. Metric Calculation ---
        rms = np.sqrt(np.mean(audio_chunk**2))

        # Check for Calibration Data (UMIK-1)
        try:
            sens = getattr(settings.AUDIO, "NOMINAL_SENSITIVITY_DBFS", None)
            ref = getattr(settings.AUDIO, "REFERENCE_DBSPL", 94.0)

            if sens is not None:
                # Calibrated Calculation
                dbfs = 20 * np.log10(rms + 1e-9)
                dbspl = dbfs - sens + ref
            else:
                # Uncalibrated Fallback (Relative dBFS)
                dbspl = 20 * np.log10(rms + 1e-9)
        except AttributeError:
            dbspl = 20 * np.log10(rms + 1e-9)

        self._event_max_db = max(self._event_max_db, dbspl)
        self._event_max_conf = max(self._event_max_conf, self._context.current_confidence)

        if self._context.current_event_label:
            self._minor_alerts.add(self._context.current_event_label)

        # --- B. Stop Conditions ---
        duration = now - self._start_time
        silence_duration = now - self._last_trigger_time

        if duration >= self._max_duration:
            logger.info("⏹️ Max recording duration reached. Stopping.")
            self._stop_and_save()
        elif silence_duration >= self._silence_timeout:
            logger.info(f"⏹️ Silence timeout ({self._silence_timeout}s). Stopping.")
            self._stop_and_save()

    def _stop_and_save(self):
        """Flush buffer to disk and log metrics."""
        if not self._context.recording_buffer:
            self._context.is_recording = False
            return

        try:
            # 1. Flatten Audio & Save WAV
            full_audio = np.concatenate(self._context.recording_buffer)
            timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            wav_filename = f"{timestamp_str}.wav"
            wav_path = self._output_dir / wav_filename

            wavfile.write(wav_path, self._sample_rate, full_audio)

            # 2. Log Metrics to CSV
            cpu, ram, temp = self._get_system_metrics()
            duration = len(full_audio) / self._sample_rate

            # If multiple events occurred, list them or pick the primary
            # We use the label that triggered the recording or the most recent one

            with open(self._csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        self._detected_label,
                        f"{self._event_max_conf:.2f}",
                        f"{duration:.1f}",
                        f"{self._event_max_db:.1f}",
                        cpu,
                        ram,
                        temp,
                    ]
                )

            logger.info(f"💾 Saved Recording: {wav_filename} ({duration:.1f}s)")
            logger.info("📝 Logged event metrics to CSV.")

        except Exception as e:
            logger.error(f"❌ Failed to save recording/metrics: {e}")
        finally:
            # Reset Global State
            self._context.is_recording = False
            self._context.recording_buffer = []
