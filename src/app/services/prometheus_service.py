"""
Prometheus Metrics Service.
Exposes real-time application telemetry for Grafana scraping.
Implements a 'Max-Hold' buffer to capture short-duration peaks between scrapes.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
import threading

from prometheus_client import Counter, Gauge, start_http_server

logger = logging.getLogger(__name__)

# Defaults for "Silence"
MAX_DBSPL = -99.0
MAX_RMS = 0.0
MAX_FLUX = 0.0
MAX_CONF = 0.0

PORT_PROMETHEUS_SERVER = 8000
PROMETHEUS_RESET_INTERVAL = 1.0  # updates for responsive dashboards


class PrometheusService:
    """
    Singleton service that manages Prometheus instruments.
    Uses a background thread to sync high-frequency local metrics
    to the low-frequency Prometheus scrape interface.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PrometheusService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # --- Prometheus Instruments ---

        # 1. Audio Physics (Managed via Max Hold)
        self._g_dbspl = Gauge("audio_dbspl", "Peak Sound Pressure Level (Max over interval)")
        self._g_rms = Gauge("audio_rms", "Peak RMS Amplitude (Max over interval)")
        self._g_flux = Gauge("audio_spectral_flux", "Spectral Flux (Change Intensity)")

        # 2. AI & Events
        self._g_conf = Gauge("ai_confidence", "Max Confidence (Max over interval)")
        self._c_events = Counter("audio_event_count", "Total confirmed events", ["category"])

        # 3. System Health (Direct updates)
        self._g_cpu = Gauge("system_cpu_usage", "CPU Usage Percent")
        self._g_ram = Gauge("system_ram_usage", "RAM Usage Percent")
        self._g_disk = Gauge("system_disk_usage", "ROM Usage Percent")
        self._g_disk_attached = Gauge("system_disk_attached_usage", "ROM Attached Usage Percent")
        self._g_temp = Gauge("system_temp_celsius", "CPU Temperature")

        # --- Internal Buffers ---
        self._lock = threading.Lock()

        # Initialize buffers to silence floor
        self._max_dbspl = MAX_DBSPL
        self._max_rms = MAX_RMS
        self._max_flux = MAX_FLUX
        self._max_conf = MAX_CONF

        # --- Background Reset Thread ---
        self._stop_event = threading.Event()
        self._reset_interval = PROMETHEUS_RESET_INTERVAL

    def start(self, port=PORT_PROMETHEUS_SERVER):
        """
        Starts the Prometheus HTTP server and the sync loop.
        :param port: The HTTP port to expose metrics on.
        """
        try:
            start_http_server(port)
            threading.Thread(target=self._syncer_loop, daemon=True).start()
            logger.info(f"📊 Metrics Service (Max-Hold) started on port {port}")
        except Exception as e:
            logger.error(f"❌ Failed to start metrics server: {e}")

    def update_audio(self, dbspl: float, rms: float, flux: float):
        """
        Thread-safe update for audio physics.
        Keeps the HIGHEST value seen since the last sync.
        """
        with self._lock:
            if dbspl > self._max_dbspl:
                self._max_dbspl = dbspl
            if rms > self._max_rms:
                self._max_rms = rms
            if flux > self._max_flux:
                self._max_flux = flux

    def update_ai_status(self, label: str, confidence: float):
        """
        Updates the Live Status Gauge (Needle).
        """
        with self._lock:
            if confidence > self._max_conf:
                self._max_conf = confidence

    def record_event(self, label: str, duration: float = 0.0):
        """
        Increments the Event Counter.
        """
        if label not in ["Silence", "Unknown"]:
            self._c_events.labels(category=label).inc()

    def update_system(self, cpu: float, temp: float, ram: float = 0.0, disk: float = 0.0, disk_attached: float = 0.0):
        """
        Updates system health gauges directly.
        """
        self._g_cpu.set(cpu)
        self._g_temp.set(temp)
        self._g_ram.set(ram)
        self._g_disk.set(disk)
        self._g_disk_attached.set(disk_attached)

    def _syncer_loop(self):
        """
        Periodically flushes the Local Max buffers to the Prometheus Gauges.
        This ensures that short transient peaks are not lost between scrapes.
        """
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._reset_interval):
                break

            with self._lock:
                # 1. Push the Max value seen in this interval to Prometheus
                self._g_dbspl.set(self._max_dbspl)
                self._g_rms.set(self._max_rms)
                self._g_flux.set(self._max_flux)
                self._g_conf.set(self._max_conf)

                # 2. Reset buffers for the next interval
                # We reset to the "floor" so we can catch new peaks
                self._max_dbspl = MAX_DBSPL
                self._max_rms = MAX_RMS
                self._max_flux = MAX_FLUX
                self._max_conf = MAX_CONF
