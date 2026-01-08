"""
Health Monitor Service.
Sends heartbeats to Local Log, GPIO LED, and Healthchecks.io.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
import threading

import httpx

from ..settings import settings

logger = logging.getLogger(__name__)

BLINK_DURATION_SECONDS = 0.1
PING_TIMEOUT_SECONDS = 10.0


class HealthMonitorService:
    """
    Background service that indicates system liveness.

    Mechanisms:
    1. Local Logs: Periodic 'ALIVE' message.
    2. Physical: Blinks an LED on the Raspberry Pi (if GPIO available).
    3. Remote: Sends an HTTP GET to a Healthchecks.io URL (if configured).
    """

    def __init__(self):
        """
        Initializes the Health Monitor.
        Sets up GPIO if running on a compatible device (Raspberry Pi).
        """
        self._config = settings.CONFIG.services
        self._stop_event = threading.Event()
        self._led = None

        # GPIO Setup (Raspberry Pi Specific)
        try:
            from gpiozero import LED

            self._led = LED(self._config.gpio_heartbeat_pin)
            logger.info(f"🔌 GPIO Heartbeat enabled on Pin {self._config.gpio_heartbeat_pin}")
        except ImportError:
            logger.info("⚠️ GPIO not available. Skipping physical heartbeat.")
        except Exception as e:
            logger.error(f"❌ GPIO Init Error: {e}")

    def start(self):
        """Starts the background heartbeat worker thread."""
        threading.Thread(target=self._worker, name="HealthMonitor", daemon=True).start()
        logger.info("💓 Health Monitor Started.")

    def stop(self):
        """Stops the worker thread and cleans up GPIO resources."""
        self._stop_event.set()
        if self._led:
            self._led.close()

    def _worker(self):
        """
        Main loop. Executes heartbeats at the configured interval.
        """
        url = self._config.hc_ping_url
        interval = self._config.heartbeat_interval_seconds

        while not self._stop_event.is_set():
            logger.info("💓 System Heartbeat: ALIVE")

            if self._led:
                self._blink_led()

            if self._config.internet_enabled and url:
                logger.debug(f"••• Sending ping to Heartbeat url: {url}")
                self._send_ping(url)

            # Wait for next interval or stop signal
            if self._stop_event.wait(interval):
                break

    def _blink_led(self):
        """
        Performs a double-blink pattern on the status LED.
        Pattern: ON -> hold -> OFF -> hold -> ON -> hold -> OFF
        """
        try:
            # Blink 1
            self._led.on()
            if self._stop_event.wait(BLINK_DURATION_SECONDS):
                return

            self._led.off()
            if self._stop_event.wait(BLINK_DURATION_SECONDS):
                return

            # Blink 2
            self._led.on()
            if self._stop_event.wait(BLINK_DURATION_SECONDS):
                return

            self._led.off()
        except Exception:
            pass

    def _send_ping(self, url: str):
        """
        Sends a heartbeat signal to the remote Healthchecks.io URL.

        :param url: The ping URL (including UUID).
        """
        try:
            with httpx.Client(timeout=PING_TIMEOUT_SECONDS) as client:
                client.get(url)
        except httpx.RequestError as e:
            logger.warning(f"📡 Heartbeat Ping Failed: {e}")
