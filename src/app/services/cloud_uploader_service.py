"""
Cloud Uploader Service.
Uploads WAVs and triggers Telegram notifications using metadata.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import json
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from ..settings import settings
from .cloud_storage_providers import GCPStorageProvider, S3Provider
from .telegram_bot_client import TelegramBotClient

logger = logging.getLogger(__name__)


class CloudUploaderService:
    def __init__(self):
        self._stop_event = threading.Event()
        self._recordings_dir = Path("recordings")
        self._config = settings.CONFIG.services
        self._cloud_cfg = self._config.cloud

        # Services
        self._telegram = TelegramBotClient()

        self._provider = self._init_provider()

    def _init_provider(self):
        """Initializes the correct provider based on settings."""
        cfg = self._cloud_cfg

        if cfg.provider == "magalu":
            logger.info("☁️ Using Magalu Cloud (S3 Compatible)")
            return S3Provider(
                access_key=settings.MAGALU_ACCESS_KEY or cfg.aws_access_key,
                secret_key=settings.MAGALU_SECRET_KEY or cfg.aws_secret_key,
                bucket_name=cfg.bucket_name,
                endpoint_url="https://s3.magaluobjects.com",
            )

        elif cfg.provider == "aws":
            logger.info("☁️ Using AWS S3")
            return S3Provider(
                access_key=cfg.aws_access_key,
                secret_key=cfg.aws_secret_key,
                bucket_name=cfg.bucket_name,
                region=cfg.aws_region,
            )

        elif cfg.provider == "gcp":
            logger.info("☁️ Using Google Cloud Storage")
            return GCPStorageProvider(credentials_path=cfg.gcp_credentials_path, bucket_name=cfg.bucket_name)

        return None

    def start(self):
        threading.Thread(target=self._worker, name="CloudUploader", daemon=True).start()
        logger.info("☁️ Cloud Uploader Started.")

    def stop(self):
        self._stop_event.set()

    def _worker(self):
        csv_path = self._recordings_dir / "metrics_buffer.csv"
        last_upload_time = time.time()
        upload_interval = 24 * 60 * 60  # 24 Hours
        row_limit = 1000

        self._enforce_disk_limits()

        while not self._stop_event.is_set():
            # 1. Process Recordings (WAV + JSON)
            meta_files = sorted(list(self._recordings_dir.glob("*.json")))

            for meta_path in meta_files:
                if self._stop_event.is_set():
                    break

                wav_path = meta_path.with_suffix(".wav")
                if not wav_path.exists():
                    logger.warning(f"Found metadata but missing WAV: {meta_path.name}")
                    meta_path.unlink()
                    continue

                if self._process_recording(wav_path, meta_path):
                    wav_path.unlink()
                    meta_path.unlink()
                    logger.info(f"✅ Processed & Deleted: {wav_path.name}")
                else:
                    time.sleep(self._config.retry_delay_seconds)
                    break

            # 2. Check CSV Batch Upload
            should_rotate = False

            if csv_path.exists():
                if (time.time() - last_upload_time) > upload_interval:
                    should_rotate = True
                    logger.info("⏳ Daily CSV upload triggered.")
                elif csv_path.stat().st_size > 50000:  # ~50KB
                    with open(csv_path) as f:
                        row_count = sum(1 for _ in f)
                    if row_count >= row_limit:
                        should_rotate = True
                        logger.info("📦 Size limit CSV upload triggered.")

            if should_rotate:
                self._rotate_and_upload(csv_path)
                last_upload_time = time.time()

            time.sleep(5)

    def _rotate_and_upload(self, csv_path):
        """Renames the current CSV and uploads it."""
        if not csv_path.exists():
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rotated_name = f"metrics_{timestamp}.csv"
        rotated_path = self._recordings_dir / rotated_name

        csv_path.rename(rotated_path)

        if self._provider:
            try:
                key = f"metrics/{rotated_name}"
                if self._provider.upload(rotated_path, key):
                    logger.info(f"📊 Uploaded Metrics Batch: {rotated_name}")
                    rotated_path.unlink()
                else:
                    logger.warning("Failed to upload CSV batch (Provider Error).")
            except Exception as e:
                logger.error(f"Failed to upload CSV batch: {e}")

    def _process_recording(self, wav_path, meta_path) -> bool:
        """Returns True if ALL enabled steps succeed."""
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            meta = {}

        if self._provider:
            key = f"recordings/{wav_path.name}"
            success = self._provider.upload(wav_path, key)
            if success:
                logger.info(f"⬆️ Uploaded {wav_path.name}")
            else:
                return False

        if self._config.telegram_enabled:
            alerts = ", ".join(meta.get("minor_alerts", []))
            duration = meta.get("duration_sec", 0)
            timestamp = meta.get("timestamp", "Unknown")

            msg = (
                f"📁 **New Recording Uploaded**\n"
                f"📅 Time: {timestamp}\n"
                f"⏱️ Duration: {duration:.1f}s\n"
                f"🔍 Events Detected: {alerts}\n"
                f"📊 Max Confidence: {meta.get('max_confidence', 0):.2f}"
            )

            success = self._telegram.send_message_sync(msg)
            if not success:
                return False

        return True

    def _enforce_disk_limits(self):
        """Emergency cleanup: Deletes oldest WAVs if disk > 90% full."""
        try:
            usage = shutil.disk_usage(self._recordings_dir)
            percent_used = (usage.used / usage.total) * 100

            if percent_used > 90:
                logger.warning(f"⚠️ Disk Full ({percent_used:.1f}%). Triggering emergency cleanup.")
                wavs = sorted(list(self._recordings_dir.glob("*.wav")), key=lambda f: f.stat().st_mtime)

                for w in wavs[:10]:
                    try:
                        w.unlink()
                        w.with_suffix(".json").unlink(missing_ok=True)
                        logger.info(f"🗑️ Emergency Delete: {w.name}")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Disk check failed: {e}")
