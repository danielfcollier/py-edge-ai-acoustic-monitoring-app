"""
Telegram Bot Client.
Provides a synchronous wrapper for sending alerts with retry logic.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import asyncio
import logging
import threading
import time

from telegram import Bot
from telegram.error import NetworkError

from ..settings import settings

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 10  # seconds to wait for a send to complete


class TelegramBotClient:
    def __init__(self):
        self._enabled = settings.CONFIG.services.telegram_enabled
        if self._enabled:
            self._token = settings.TELEGRAM_BOT_TOKEN
            self._chat_id = settings.TELEGRAM_CHAT_ID
            self._bot = Bot(token=self._token)
            # Dedicated loop in a daemon thread — safe to call from any context,
            # including from inside another running event loop (e.g. the command receiver).
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True, name="TelegramSendLoop").start()

    def send_message_sync(self, text: str) -> bool:
        if not self._enabled:
            return True

        retries = settings.CONFIG.services.retry_attempts
        delay = settings.CONFIG.services.retry_delay_seconds

        for attempt in range(1, retries + 2):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._bot.send_message(chat_id=self._chat_id, text=text),
                    self._loop,
                )
                future.result(timeout=_SEND_TIMEOUT)
                return True
            except NetworkError:
                if attempt <= retries:
                    logger.warning(f"⚠️ Telegram Connection Error. Retry {attempt}/{retries} in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error("❌ Telegram failed after max retries.")
            except Exception as e:
                logger.error(f"❌ Telegram Error: {e}")
                return False

        return False
