"""
Telegram Command Receiver.
Background thread that polls Telegram for bot commands and dispatches them.
Only accepts messages from the configured TELEGRAM_CHAT_ID.

Supported commands:
  /privacy on <duration>   activate privacy mode (e.g. 2h, 30m, 1d)
  /privacy off             deactivate privacy mode
  /privacy status          report current privacy state
  /status                  system snapshot (label, privacy, queues, CPU/RAM/temp/disk)
  /dog                     manually register a neighbour dog bark missed by the detector
  /noise [duration]        log a noise disturbance; start extended monitoring (default 3h)
  /audio <hash>            send a recorded evidence file as .ogg (hash = 8-char ID from alert)
"""

import asyncio
import csv
import io
import logging
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

import soundfile
from telegram import Bot
from telegram.error import TimedOut

from .. import __version__
from ..context import PipelineContext
from ..services.noise_monitor_session import NoiseMonitorSession
from ..services.privacy_mode import PrivacyMode
from ..services.telegram_bot_client import TelegramBotClient
from ..settings import SystemMetrics, settings

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SEC = 5  # short enough for responsive shutdown

_METRICS_CSV_HEADER = [
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


def _append_metrics_csv(csv_path: Path, row: list) -> None:
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(_METRICS_CSV_HEADER)
        w.writerow(row)


def _parse_event_datetime(args: list[str]) -> datetime | None:
    """Parse optional datetime args from /dog command. Returns None if no args provided."""
    if not args:
        return None
    s = " ".join(args).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _parse_duration(s: str) -> int | None:
    """Parse '2h', '30m', '1d' to seconds. Plain integer = minutes."""
    s = s.strip().lower()
    multipliers = {"h": 3600, "m": 60, "d": 86400}
    suffix = s[-1:] if s else ""
    if suffix in multipliers:
        try:
            return int(s[:-1]) * multipliers[suffix]
        except ValueError:
            return None
    try:
        return int(s) * 60
    except ValueError:
        return None


class TelegramCommandReceiver:
    """
    Polls the Telegram Bot API for commands and dispatches them.
    Runs in a daemon thread; harmless to skip if Telegram is disabled.
    """

    def __init__(
        self,
        context: PipelineContext | None = None,
        raw_queue: queue.Queue | None = None,
        upload_queue: queue.Queue | None = None,
    ):
        self._context = context
        self._raw_queue = raw_queue
        self._upload_queue = upload_queue

        svc = settings.CONFIG.services
        self._output_dir: Path = svc.recording_output_path
        self._csv_path: Path = self._output_dir / svc.metrics_csv_buffer_file

        self._noise_session: NoiseMonitorSession | None = None

        self._enabled = svc.telegram_enabled
        self._authorized_chat_id = str(settings.TELEGRAM_CHAT_ID or "")
        self._bot_client = TelegramBotClient()
        self._offset = 0
        self._stop_event = threading.Event()

    def start(self):
        if not self._enabled:
            logger.info("📱 Telegram disabled — Command Receiver not started.")
            return
        threading.Thread(target=self._run, name="TelegramCmdReceiver", daemon=True).start()
        logger.info("📱 Telegram Command Receiver started.")

    def stop(self):
        self._stop_event.set()
        if self._noise_session is not None:
            self._noise_session.stop()

    # --- Internal ---

    def _run(self):
        asyncio.run(self._poll_loop())

    async def _poll_loop(self):
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        while not self._stop_event.is_set():
            try:
                updates = await bot.get_updates(
                    offset=self._offset,
                    timeout=_POLL_TIMEOUT_SEC,
                    allowed_updates=["message"],
                )
                for update in updates:
                    self._offset = update.update_id + 1
                    if update.message and update.message.text:
                        self._dispatch(update.message)
            except TimedOut:
                continue  # normal long-poll expiry — server closed idle connection
            except Exception as e:
                logger.exception(f"❌ Telegram poll error: {e}")
                await asyncio.sleep(5)

    def _dispatch(self, message) -> None:
        chat_id = str(message.chat_id)
        if chat_id != self._authorized_chat_id:
            logger.warning(f"🚫 Ignoring message from unauthorized chat {chat_id}")
            return

        parts = message.text.strip().split(maxsplit=3)
        if not parts or not parts[0].startswith("/"):
            return

        cmd = parts[0].lstrip("/").lower()
        args = parts[1:]

        if cmd == "privacy":
            self._handle_privacy(args)
        elif cmd == "status":
            self._handle_status()
        elif cmd == "dog":
            self._handle_dog(args)
        elif cmd == "noise":
            self._handle_noise(args)
        elif cmd == "audio":
            self._handle_audio(args)
        else:
            logger.debug(f"Unhandled command: {cmd}")

    def _handle_privacy(self, args: list[str]) -> None:
        pm = PrivacyMode()

        if not args:
            self._reply(
                "Usage: /privacy on <duration> | /privacy off | /privacy status\nDuration examples: 30m · 2h · 1d"
            )
            return

        sub = args[0].lower()

        if sub == "on":
            duration_str = args[1] if len(args) > 1 else "4h"
            duration_sec = _parse_duration(duration_str)
            if duration_sec is None:
                self._reply(f"❌ Invalid duration '{duration_str}'. Use: 30m, 2h, 1d")
                return
            pm.activate(duration_sec)
            self._reply(f"🔒 Privacy mode ON for {duration_str}.")

        elif sub == "off":
            pm.deactivate()
            self._reply("🔓 Privacy mode OFF.")

        elif sub == "status":
            active = pm.is_active()
            if active:
                self._reply("🔒 Privacy mode is active.")
            else:
                self._reply("🔓 Privacy mode is inactive.")

        else:
            self._reply(f"Unknown subcommand '{sub}'. Use: on, off, status")

    def _handle_status(self) -> None:
        lines = [f"📊 AI Acoustic Monitor v{__version__}", ""]

        # Last detected event (skip Silence)
        if self._context is not None:
            label = self._context.current_event_label
            conf = self._context.current_confidence
            if label and label.lower() != "silence":
                lines.append(f"👂 {label} ({conf:.2f})")

        # Privacy mode
        pm_active = PrivacyMode().is_active()
        icon = "🔒" if pm_active else "🔓"
        lines.append(f"{icon} Privacy: {'active' if pm_active else 'inactive'}")

        # Queue depths + disk
        raw_depth = self._raw_queue.qsize() if self._raw_queue is not None else 0
        upload_depth = self._upload_queue.qsize() if self._upload_queue is not None else 0
        cpu, ram, temp, disk, _ = SystemMetrics.get_stats()
        lines += [
            "",
            f"📤 Raw: {raw_depth} · Upload: {upload_depth} · Disk {disk:.0f}%",
            f"🖥️ CPU {cpu:.0f}% · RAM {ram:.0f}% · {temp:.0f}°C",
        ]

        self._reply("\n".join(lines))

    def _handle_dog(self, args: list[str]) -> None:
        event_dt = _parse_event_datetime(args)
        if args and event_dt is None:
            self._reply("❌ Invalid datetime. Use: /dog 2026-05-31 16:05")
            return

        ts = event_dt or datetime.now()
        row = [
            str(uuid.uuid4()),
            ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "NeighborDog",
            "1.00",
            "0.0000",
            "0.0",
            "0.0",  # rms, dbspl, flux
            "0.0",
            "0.0",
            "0.0",
            "0.0",
            "0.0",  # cpu, ram, temp, disk, disk_attached
        ]
        label = ts.strftime("%Y-%m-%d %H:%M") if event_dt else ts.strftime("%H:%M:%S")
        try:
            _append_metrics_csv(self._csv_path, row)
            self._reply(f"🐕 Neighbor dog registered — {label}")
        except Exception as e:
            logger.error(f"❌ /dog write error: {e}")
            self._reply("❌ Failed to register neighbor dog.")

    def _handle_noise(self, args: list[str]) -> None:
        duration_str = args[0] if args else "3h"
        duration_sec = _parse_duration(duration_str)
        if duration_sec is None:
            self._reply(f"❌ Invalid duration '{duration_str}'. Use: 30m, 1h, 3h")
            return

        # Always log the immediate entry regardless of privacy
        now = datetime.now()
        row = [
            str(uuid.uuid4()),
            now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "NoiseEvent",
            "1.00",
            "0.0000",
            "0.0",
            "0.0",
            "0.0",
            "0.0",
            "0.0",
            "0.0",
            "0.0",
        ]
        try:
            _append_metrics_csv(self._csv_path, row)
        except Exception as e:
            logger.error(f"❌ /noise write error: {e}")

        if PrivacyMode().is_active():
            self._reply("🔊 Noise logged. Monitoring blocked (privacy active).")
            return

        if self._noise_session is not None:
            self._noise_session.reset(duration_sec)
            self._reply(f"🔊 Noise logged. Monitoring extended to {duration_str} → {self._noise_session.csv_filename}")
        else:
            self._noise_session = NoiseMonitorSession(
                context=self._context,
                output_dir=self._output_dir,
                duration_sec=duration_sec,
                on_done=self._on_noise_done,
            )
            self._noise_session.start()
            self._reply(
                f"🔊 Noise logged. Monitoring for {duration_str} (30s windows) → {self._noise_session.csv_filename}"
            )

    def _on_noise_done(self, csv_filename: str, windows: int) -> None:
        self._noise_session = None
        if windows >= 0:
            self._reply(f"📊 Noise monitor done — {windows} windows → {csv_filename}")

    def _handle_audio(self, args: list[str]) -> None:
        if not args:
            self._reply("Usage: /audio <hash>\nHash is the 8-char ID shown in the evidence alert.")
            return

        hash_prefix = args[0].lower()
        wav_path = self._find_recording(hash_prefix)
        if wav_path is None:
            self._reply(f"❌ No local recording found for ID '{hash_prefix}'.")
            return

        try:
            audio_data, sample_rate = soundfile.read(str(wav_path))
            ogg_buffer = io.BytesIO()
            soundfile.write(ogg_buffer, audio_data, sample_rate, format="OGG", subtype="VORBIS")
            ogg_buffer.seek(0)
            filename = wav_path.stem + ".ogg"
            self._bot_client.send_audio_sync(ogg_buffer.read(), filename)
        except Exception as e:
            logger.error(f"❌ /audio error for {hash_prefix}: {e}")
            self._reply("❌ Failed to send audio.")

    def _find_recording(self, hash_prefix: str) -> Path | None:
        for f in self._output_dir.glob("evidence-*.wav"):
            # UUID is always the last 36 chars of the stem: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
            file_uuid = f.stem[-36:]
            if file_uuid.startswith(hash_prefix):
                return f
        return None

    def _reply(self, text: str) -> None:
        self._bot_client.send_message_sync(text)
