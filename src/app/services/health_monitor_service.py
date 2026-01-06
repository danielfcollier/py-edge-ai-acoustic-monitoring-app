"""
Health Monitor Service.
Sends heartbeats to Local Log, GPIO LED, and Healthchecks.io.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
import threading
import time

import httpx

from ..settings import settings

logger = logging.getLogger(__name__)


class HealthMonitorService:
    def __init__(self):
        self._config = settings.CONFIG.services
        self._stop_event = threading.Event()
        self._led = None

        try:
            from gpiozero import LED

            self._led = LED(self._config.gpio_heartbeat_pin)
            logger.info(f"🔌 GPIO Heartbeat enabled on Pin {self._config.gpio_heartbeat_pin}")
        except ImportError:
            logger.info("⚠️ GPIO not available. Skipping physical heartbeat.")
        except Exception as e:
            logger.error(f"❌ GPIO Init Error: {e}")

    def start(self):
        threading.Thread(target=self._worker, name="HealthMonitor", daemon=True).start()
        logger.info("💓 Health Monitor Started.")

    def stop(self):
        self._stop_event.set()
        if self._led:
            self._led.close()

    def _worker(self):
        url = self._config.hc_ping_url
        interval = self._config.heartbeat_interval_seconds

        while not self._stop_event.is_set():
            logger.info("💓 System Heartbeat: ALIVE")

            if self._led:
                self._blink_led()

            if self._config.internet_enabled and url:
                self._send_ping(url)

            if self._stop_event.wait(interval):
                break

    def _blink_led(self):
        try:
            self._led.on()
            time.sleep(0.1)
            self._led.off()
            time.sleep(0.1)
            self._led.on()
            time.sleep(0.1)
            self._led.off()
        except Exception:
            pass

    def _send_ping(self, url):
        try:
            with httpx.Client(timeout=10.0) as client:
                client.get(url)
        except httpx.RequestError as e:
            logger.warning(f"📡 Heartbeat Ping Failed: {e}")
