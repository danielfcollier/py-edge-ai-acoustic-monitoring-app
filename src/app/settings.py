"""
Extended settings loading Env Vars and YAML Policy.
Handles auto-disabling of services if credentials are missing.
Supports YAML Variable Interpolation.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict
from umik_base_app.settings import Settings as BaseSettings

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)


# --- System Metrics Utility ---


class SystemMetrics:
    """Helper to retrieve hardware telemetry (CPU, RAM, Temp, Core ROM, Attached ROM)."""

    @staticmethod
    def get_stats() -> tuple[float, float, float, float, float, float]:
        """
        Returns a tuple of (cpu_percent, ram_percent, temp_celsius, disk_percent, disk_attached_percent).
        Handles missing libraries or sensors gracefully.
        """
        cpu = 0.0
        ram = 0.0
        temp = 0.0
        disk = 0.0
        disk_attached = 0.0

        if psutil:
            # CPU: interval=None makes it non-blocking (returns since last call)
            cpu = psutil.cpu_percent(interval=None)

            # RAM
            ram = psutil.virtual_memory().percent

            # Disk Usage for Root Partition (SD Card) and Attached Disk (USB SSD)
            try:
                disk = psutil.disk_usage("/").percent
                disk_attached = psutil.disk_usage("/").percent
            except Exception:
                disk = 0.0
                disk_attached = 0.0

            # Temperature: Try standard sensors first
            try:
                temps = psutil.sensors_temperatures()
                # 'cpu_thermal' is standard for Raspberry Pi
                if "cpu_thermal" in temps:
                    temp = temps["cpu_thermal"][0].current
                # 'coretemp' is common on Intel Linux
                elif "coretemp" in temps:
                    temp = temps["coretemp"][0].current
            except Exception:
                pass

        # Fallback: Read thermal zone file directly (Linux/RPi specific)
        if temp == 0.0:
            try:
                if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                    with open("/sys/class/thermal/thermal_zone0/temp") as f:
                        # Value is usually in millidegrees
                        temp = float(f.read().strip()) / 1000.0
            except Exception:
                pass

        return cpu, ram, temp, disk, disk_attached


# --- YAML Schema Models (Logic Only) ---


class ReportingLimits(BaseModel):
    day_db: float = 55.0
    night_db: float = 50.0


class ReportingConfig(BaseModel):
    days_to_report: int = 30
    limits: ReportingLimits = ReportingLimits()
    category_mapping: dict[str, str] = Field(default_factory=dict)


class PolicyRule(BaseModel):
    name: str
    description: str | None = None
    condition: str
    actions: list[Literal["telegram_alert", "cloud_upload", "log_metadata", "record_evidence"]]
    ignore_privacy: bool = False


class FeatureExtractorConfig(BaseModel):
    use_tflite: bool = True
    model_path_lite: str = "src/yamnet/yamnet.tflite"
    model_path_full: str = "src/yamnet/model"
    class_map_path: str = "src/yamnet/class_map/yamnet_class_map.csv"

    force_cpu: bool = True

    # Audio Processing Constants
    inference_interval_ms: int = 975
    target_sample_rate: int = 16000
    model_input_size: int = 15600  # 0.975s @ 16kHz

    # Thresholds
    logging_confidence_threshold: float = 0.3
    sad_threshold_rms: float = 0.002
    sad_threshold_flux: float = 5.0
    sad_threshold_dbspl: float = 45.0


class CloudConfig(BaseModel):
    provider: Literal["magalu", "aws", "gcp"] = "magalu"
    bucket_name: str = "acoustic-logs"

    # S3 / Magalu / AWS Credentials
    aws_access_key: str | None = Field(None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_key: str | None = Field(None, alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str | None = Field("us-east-1", alias="AWS_REGION")
    s3_endpoint: str | None = None  # For Magalu/MinIO

    # GCP Credentials
    gcp_credentials_path: str | None = Field(None, alias="GOOGLE_APPLICATION_CREDENTIALS")


class ServiceConfig(BaseModel):
    cloud: CloudConfig = CloudConfig()
    internet_enabled: bool = True
    telegram_enabled: bool = True
    cloud_storage_enabled: bool = True

    # 🆕 Recorder Logic: Save Calibrated (Heavy) or Raw (Light)
    save_calibrated_wave: bool = False

    # 🆕 Storage Path (Default: ./recordings)
    recording_output_path: Path = Path("recordings")

    # Operational Settings
    retry_attempts: int = 3
    retry_delay_seconds: int = 5
    heartbeat_interval_seconds: int = 60
    alert_cooldown_seconds: int = 60

    # Time Settings (Defining Day/Night)
    day_start_hour: int = 6  # 6 AM
    night_start_hour: int = 22  # 10 PM

    # Hardware / Health
    gpio_heartbeat_pin: int = 17
    hc_ping_url: str | None = None


class HardwareConfig(BaseModel):
    calibration_file: str | None = None


class AppConfig(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)

    hardware: HardwareConfig = HardwareConfig()
    feature_extractor: FeatureExtractorConfig
    policies: list[PolicyRule]
    services: ServiceConfig = ServiceConfig()
    reporting: ReportingConfig = ReportingConfig()


# --- Main Settings Class ---


class AppSettings(BaseSettings):
    """
    Combines Env Vars (Secrets/Logs) and YAML (Logic).
    """

    # --- Secrets ---
    TELEGRAM_BOT_TOKEN: str | None = Field(None, alias="TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID: str | None = Field(None, alias="TELEGRAM_CHAT_ID")
    MAGALU_ACCESS_KEY: str | None = Field(None, alias="MAGALU_KEY")
    MAGALU_SECRET_KEY: str | None = Field(None, alias="MAGALU_SECRET")
    MAGALU_BUCKET: str = "acoustic-logs"

    # --- Logging Levels (Controlled via .env) ---
    LOG_LEVEL_MAIN: str = "INFO"
    LOG_LEVEL_POLICY_ENGINE: str = "DEBUG"
    LOG_LEVEL_FEATURE_EXTRACTOR: str = "DEBUG"
    LOG_LEVEL_SMART_RECORDER: str = "DEBUG"
    LOG_LEVEL_TELEGRAM: str = "INFO"
    LOG_LEVEL_SERVICES: str = "INFO"

    # --- Configuration ---
    CONFIG: AppConfig | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def _inject_variables(self, node: Any, variables: dict[str, Any]) -> Any:
        """Recursively injects variables into strings."""
        if isinstance(node, dict):
            return {k: self._inject_variables(v, variables) for k, v in node.items()}
        elif isinstance(node, list):
            return [self._inject_variables(i, variables) for i in node]
        elif isinstance(node, str):
            try:
                return node.format(**variables)
            except (KeyError, ValueError):
                return node
        else:
            return node

    def load_policy_file(self, path: str = "security_policy.yaml"):
        """Loads YAML, merges ENV vars, and validates."""
        load_dotenv()

        p = Path(path)
        if not p.exists():
            logger.warning(f"⚠️ Policy file '{path}' not found. Using defaults.")
            default_yaml = {"feature_extractor": {}, "policies": []}
            self.CONFIG = AppConfig.model_validate(default_yaml)
        else:
            with open(p) as f:
                raw_data = yaml.safe_load(f) or {}

            # 1. Variable Substitution
            yaml_vars = raw_data.get("variables", {})
            combined_vars = {**os.environ, **yaml_vars}
            processed_data = self._inject_variables(raw_data, combined_vars)

            # 2. Validate
            self.CONFIG = AppConfig.model_validate(processed_data)

        # 3. Apply Log Levels (from Env Vars, not YAML)
        self._apply_logging_config()
        self._validate_services()

    def inject_hardware_settings(self, base_args: Any):
        """
        Injects hardware-specific settings (like calibration file) into the base app arguments.
        This triggers auto-detection mechanisms in the base app (e.g., UMIK-1).
        """
        if not self.CONFIG or not self.CONFIG.hardware:
            return

        cal_path = self.CONFIG.hardware.calibration_file

        if cal_path:
            logger.info(f"🎤 Injecting Calibration File: '{cal_path}'")

            if not Path(cal_path).exists():
                logger.warning(f"⚠️ WARNING: Calibration file at '{cal_path}' does not exist! App may crash.")

            base_args.calibration_file = cal_path

    def _apply_logging_config(self):
        """Sets log levels based on .env variables defined in this class."""

        # Map .env fields to module paths
        log_map = {
            "__main__": self.LOG_LEVEL_MAIN,
            "app.sinks.feature_extractor_sink": self.LOG_LEVEL_FEATURE_EXTRACTOR,
            "app.sinks.policy_engine_sink": self.LOG_LEVEL_POLICY_ENGINE,
            "app.sinks.smart_recorder_sink": self.LOG_LEVEL_SMART_RECORDER,
            "app.services.telegram_bot_client": self.LOG_LEVEL_TELEGRAM,
            "app.services": self.LOG_LEVEL_SERVICES,
            "umik_base_app": self.LOG_LEVEL_MAIN,
        }

        for module_name, level_str in log_map.items():
            logger_instance = logging.getLogger(module_name)
            level_value = getattr(logging, level_str.upper(), logging.INFO)
            logger_instance.setLevel(level_value)
            logger.debug(f"🔧 Log Level set to {level_str} for '{module_name}'")

    def _validate_services(self):
        """Auto-disables services if credentials are missing."""
        svcs = self.CONFIG.services
        if svcs.telegram_enabled and (not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_CHAT_ID):
            logger.warning("⚠️ Telegram credentials missing. Disabling Telegram Service.")
            svcs.telegram_enabled = False


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()


settings = get_settings()
