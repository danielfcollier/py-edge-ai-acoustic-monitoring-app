"""
Cloud Uploader Service.
Hybrid Worker:
1. Online: Streams audio RAM -> Cloud (S3/Magalu) with Metadata Headers.
2. Offline: Spills audio RAM -> Disk (DLQ).
3. Background: Rotates CSV logs and retries failed uploads.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import io
import logging
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from scipy.io import wavfile

from ..settings import settings
from .cloud_storage_providers import S3Provider
from .telegram_bot_client import TelegramBotClient

logger = logging.getLogger(__name__)

# --- Configuration Constants ---
METRICS_CSV_BUFFER_FILE = "metrics_buffer.csv"
CSV_ROTATION_SIZE_MB = 1.0
CSV_ROTATION_AGE_SECONDS = 24 * 3600  # 24 Hours

# --- Interval Constants ---
WORKER_TIMEOUT_SECONDS = 1.0
CSV_CHECK_INTERVAL_SECONDS = 10
RETRY_INTERVAL_SECONDS = 60

# --- File & Path Templates ---
FILENAME_OFFLINE_RECORDING = "evidence-{timestamp}-{uuid}.wav"
FILENAME_ROTATED_CSV = "metrics-{date}.csv"

# --- Cloud Key Templates ---
S3_KEY_RECORDING = "recordings/evidence-{timestamp}-{uuid}.wav"
S3_KEY_METRICS = "metrics/{filename}"


class CloudUploaderService:
    """
    Manages the reliable upload of audio evidence and system metrics to cloud storage.
    Implements a hybrid online/offline strategy with automatic retries for resilience.
    """

    def __init__(self, upload_queue: queue.Queue, output_path: Path):
        """
        Initialize the uploader service.

        :param upload_queue: Queue receiving 'EventObjects' (dicts) from the Recorder Sink.
        :param output_path: Local directory path for buffering offline files and CSV logs.
        """
        self._queue = upload_queue
        self._recordings_dir = output_path
        self._config = settings.CONFIG.services
        self._cloud_cfg = self._config.cloud

        self._stop_event = threading.Event()
        self._telegram = TelegramBotClient()
        self._provider = self._init_provider()

        self._recordings_dir.mkdir(parents=True, exist_ok=True)

    def _init_provider(self):
        """
        Initializes the appropriate cloud storage provider based on settings.
        """
        cfg = self._cloud_cfg
        if cfg.provider == "magalu":
            # MAGALU_URL in .env is an explicit override; otherwise derive from region.
            endpoint = settings.MAGALU_URL or f"https://{cfg.region}.magaluobjects.com"
            logger.info(f"☁️  Using Magalu Cloud — {endpoint}")
            return S3Provider(
                access_key=settings.MAGALU_ACCESS_KEY,
                secret_key=settings.MAGALU_SECRET_KEY,
                bucket_name=cfg.bucket_name,
                endpoint_url=endpoint,
            )
        elif cfg.provider == "aws":
            logger.info(f"☁️  Using AWS S3 — region: {cfg.region}")
            # Credentials are read from env automatically by boto3
            # (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).
            return S3Provider(
                access_key=None,
                secret_key=None,
                bucket_name=cfg.bucket_name,
                region=cfg.region,
            )
        elif cfg.provider == "gcp":
            import os

            from .cloud_storage_providers import GCPStorageProvider

            creds = cfg.gcp_credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            logger.info("☁️  Using GCP Cloud Storage")
            return GCPStorageProvider(credentials_path=creds, bucket_name=cfg.bucket_name)
        return None

    def start(self):
        """Spawns the background worker threads."""
        threading.Thread(target=self._stream_worker, name="UploaderStream", daemon=True).start()
        threading.Thread(target=self._csv_batch_worker, name="UploaderCSV", daemon=True).start()
        threading.Thread(target=self._retry_worker, name="UploaderRetry", daemon=True).start()
        logger.info(f"☁️  Cloud Uploader Started. Storage: {self._recordings_dir}")

    def stop(self):
        """Signals all workers to stop."""
        self._stop_event.set()

    def _stream_worker(self):
        """
        Main loop: Consumes audio events from the queue.
        Attempts direct cloud upload; falls back to disk if offline or failed.
        """
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=WORKER_TIMEOUT_SECONDS)
            except queue.Empty:
                continue

            uuid_str = event["uuid"]

            try:
                wav_buffer = io.BytesIO()
                wavfile.write(wav_buffer, event["sample_rate"], event["audio_data"])
                wav_buffer.seek(0)
            except Exception as e:
                logger.error(f"❌ Audio Conversion Failed for {uuid_str}: {e}")
                continue

            is_online = self._config.internet_enabled and self._provider is not None
            success = False

            if is_online:
                success = self._attempt_direct_upload(wav_buffer, event)

            if not success:
                self._save_offline_fallback(wav_buffer, uuid_str, event["timestamp"])

    def _attempt_direct_upload(self, wav_buffer: io.BytesIO, event: dict) -> bool:
        """
        Uploads an in-memory WAV file directly to S3/Magalu.
        Returns True if successful, False otherwise.
        """
        key = S3_KEY_RECORDING.format(timestamp=event["timestamp"], uuid=event["uuid"])
        meta = event.get("metadata", {})

        s3_metadata = {
            "label": str(meta.get("label", "unknown")),
            "confidence": str(meta.get("confidence", "0.0")),
            "calibrated": str(meta.get("calibrated", False)),
            "uuid": str(event["uuid"]),
            "timestamp": str(event["timestamp"]),
        }

        try:
            if hasattr(self._provider, "upload_fileobj"):
                self._provider.upload_fileobj(wav_buffer, key, extra_args={"Metadata": s3_metadata})
            elif hasattr(self._provider, "client"):
                self._provider.client.upload_fileobj(
                    wav_buffer, self._provider.bucket_name, key, ExtraArgs={"Metadata": s3_metadata}
                )
            else:
                return False

            logger.info(f"⬆️ Stream Upload Success: {key}")
            if self._config.telegram_enabled:
                self._send_telegram_alert(event)
            return True
        except Exception as e:
            logger.warning(f"☁️ Stream Upload Failed ({e}). Triggering fallback.")
            wav_buffer.seek(0)
            return False

    def _save_offline_fallback(self, wav_buffer: io.BytesIO, uuid_str: str, timestamp_iso: str):
        """
        Saves the WAV file to local disk (Dead Letter Queue) for later retry.
        """
        try:
            dt = datetime.fromisoformat(timestamp_iso)
        except ValueError:
            dt = datetime.now()

        formatted_time = dt.strftime("%Y%m%d_%H%M%S")

        filename = FILENAME_OFFLINE_RECORDING.format(timestamp=formatted_time, uuid=uuid_str)
        path = Path(self._recordings_dir, filename)

        try:
            with open(path, "wb") as f:
                f.write(wav_buffer.getbuffer())
            logger.info(f"💾 Saved Offline Evidence: {filename}")
        except Exception as e:
            logger.error(f"❌ Disk Save Failed! Evidence Lost: {e}")

    def _csv_batch_worker(self):
        """
        Background loop: Monitors the CSV metrics file.
        Rotates and uploads it if it exceeds size or age limits.
        """
        csv_path = Path(self._recordings_dir, METRICS_CSV_BUFFER_FILE)
        last_check = time.time()

        while not self._stop_event.is_set():
            if self._stop_event.wait(CSV_CHECK_INTERVAL_SECONDS):
                break

            if not csv_path.exists():
                continue

            now = time.time()
            should_rotate = False

            try:
                size_mb = csv_path.stat().st_size / (1024 * 1024)
                if size_mb > CSV_ROTATION_SIZE_MB:
                    should_rotate = True
                elif (now - last_check) > CSV_ROTATION_AGE_SECONDS:
                    should_rotate = True
            except Exception:
                continue

            if should_rotate:
                self._rotate_and_upload_csv(csv_path)
                last_check = now

    def _rotate_and_upload_csv(self, csv_path: Path):
        """
        Renames the current CSV log and attempts to upload it.
        Deletes the file upon successful upload.
        """
        datestamp = datetime.now().strftime("%Y%m%d")
        rotated_name = FILENAME_ROTATED_CSV.format(date=datestamp)
        rotated_path = Path(self._recordings_dir, rotated_name)

        try:
            shutil.move(str(csv_path), str(rotated_path))
        except Exception as e:
            logger.error(f"CSV Rotate Failed: {e}")
            return

        if self._config.internet_enabled and self._provider:
            try:
                key = S3_KEY_METRICS.format(filename=rotated_name)
                if self._provider.upload(str(rotated_path), key):
                    logger.info(f"📊 Batch Upload Success: {rotated_name}")
                    rotated_path.unlink()
                else:
                    logger.warning("📊 Batch Upload Failed. Keeping local copy.")
            except Exception as e:
                logger.error(f"Batch Upload Error: {e}")

    def _retry_worker(self):
        """
        Background loop: Scans local disk for offline .wav files and retries upload.
        """
        while not self._stop_event.is_set():
            if self._stop_event.wait(RETRY_INTERVAL_SECONDS):
                break

            if not self._config.internet_enabled or not self._provider:
                continue

            offline_files = list(self._recordings_dir.glob("evidence-*.wav"))
            if not offline_files:
                continue

            logger.info(f"🔄 Retry Loop: Found {len(offline_files)} pending uploads.")
            for wav_path in offline_files:
                if self._stop_event.is_set():
                    break
                try:
                    parts = wav_path.stem.split("-", 2)
                    file_timestamp, file_uuid = parts[1], parts[2]
                    key = S3_KEY_RECORDING.format(timestamp=file_timestamp, uuid=file_uuid)

                    if self._provider.upload(str(wav_path), key):
                        logger.info(f"✅ Retry Success: {wav_path.name}")
                        wav_path.unlink()
                    else:
                        logger.warning(f"❌ Retry Failed: {wav_path.name}")
                except Exception as e:
                    logger.error(f"Retry Error: {e}")

    def _send_telegram_alert(self, event):
        """
        Formats and sends a notification to the configured Telegram Chat.
        Conditionally includes dBSPL only if calibration was active.
        """
        meta = event.get("metadata", {})
        is_cal = meta.get("calibrated", False)

        lines = [
            "📁 **New Evidence Uploaded**",
            f"🏷️ Label: {meta.get('label', 'unknown')}",
            f"🎯 Conf: {float(meta.get('confidence', 0.0)):.2f}",
        ]

        if is_cal:
            dbspl = meta.get("dbspl")
            if dbspl is not None:
                lines.append(f"🔊 dBSPL: {float(dbspl):.1f} dB")

        lines.append(f"⏱️ Duration: {event['duration_sec']:.1f}s")

        msg = "\n".join(lines)
        self._telegram.send_message_sync(msg)
