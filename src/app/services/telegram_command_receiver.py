"""
Telegram Command Receiver.
Background thread that polls Telegram for bot commands and dispatches them.
Only accepts messages from the configured TELEGRAM_CHAT_ID.

Supported commands:
  /privacy on <duration>   activate privacy mode (e.g. 2h, 30m, 1d)
  /privacy off             deactivate privacy mode
  /privacy status          report current privacy state
"""

import asyncio
import logging
import threading

from telegram import Bot

from ..services.privacy_mode import PrivacyMode
from ..services.telegram_bot_client import TelegramBotClient
from ..settings import settings

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SEC = 5  # short enough for responsive shutdown


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

    def __init__(self):
        self._enabled = settings.CONFIG.services.telegram_enabled
        self._authorized_chat_id = str(settings.TELEGRAM_CHAT_ID or "")
        self._bot_client = TelegramBotClient()
        self._offset = 0
        self._stop_event = threading.Event()

    def start(self):
        if not self._enabled:
            logger.info("📱 Telegram disabled — Command Receiver not started.")
            return
        threading.Thread(
            target=self._run, name="TelegramCmdReceiver", daemon=True
        ).start()
        logger.info("📱 Telegram Command Receiver started.")

    def stop(self):
        self._stop_event.set()

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
        else:
            logger.debug(f"Unhandled command: {cmd}")

    def _handle_privacy(self, args: list[str]) -> None:
        pm = PrivacyMode()

        if not args:
            self._reply(
                "Usage: /privacy on <duration> | /privacy off | /privacy status\n"
                "Duration examples: 30m · 2h · 1d"
            )
            return

        sub = args[0].lower()

        if sub == "on":
            duration_str = args[1] if len(args) > 1 else "1h"
            duration_sec = _parse_duration(duration_str)
            if duration_sec is None:
                self._reply(
                    f"❌ Invalid duration '{duration_str}'. Use: 30m, 2h, 1d"
                )
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

    def _reply(self, text: str) -> None:
        self._bot_client.send_message_sync(text)
