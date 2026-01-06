"""
Extended settings loading Env Vars and YAML Policy.
Handles auto-disabling of services if credentials are missing.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict
from umik_base_app.settings import Settings as BaseSettings

logger = logging.getLogger(__name__)


# --- YAML Schema Models ---

class ReportingLimits(BaseModel):
    day_db: float = 55.0
    night_db: 50.0


class ReportingConfig(BaseModel):
    days_to_report: int = 30
    limits: ReportingLimits = ReportingLimits()
    category_mapping: dict[str, str] = Field(default_factory=dict)


class PolicyRule(BaseModel):
    name: str
    description: str | None = None
    condition: str
    actions: list[Literal["telegram_alert", "cloud_upload", "log_metadata"]]
    ignore_privacy: bool = False


class FeatureExtractorConfig(BaseModel):
    use_tflite: bool = True
    model_path_lite: str = "src/yamnet/yamnet.tflite"
    model_path_full: str = "src/yamnet/model"
    class_map_path: str = "src/yamnet/class_map/yamnet_class_map.csv"
    inference_interval_ms: int = 975
    target_sample_rate: int = 16000


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
    retry_attempts: int = 3
    retry_delay_seconds: int = 5
    heartbeat_interval_seconds: int = 60
    gpio_heartbeat_pin: int = 17
    hc_ping_url: str | None = None


class AppConfig(BaseModel):
    target_device: str | None = "UMIK-1"
    feature_extractor: FeatureExtractorConfig
    policies: list[PolicyRule]
    services: ServiceConfig = ServiceConfig()
    reporting: ReportingConfig = ReportingConfig()


# --- Main Settings Class ---

class AppSettings(BaseSettings):
    """
    Combines Env Vars (API Keys) and YAML (Logic).
    """

    # Secrets
    TELEGRAM_BOT_TOKEN: str | None = Field(None, alias="TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID: str | None = Field(None, alias="TELEGRAM_CHAT_ID")
    MAGALU_ACCESS_KEY: str | None = Field(None, alias="MAGALU_KEY")
    MAGALU_SECRET_KEY: str | None = Field(None, alias="MAGALU_SECRET")
    MAGALU_BUCKET: str = "acoustic-logs"

    # Logic (YAML)
    CONFIG: AppConfig | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def load_policy_file(self, path: str = "security_policy.yaml"):
        """Loads YAML. If missing, loads a safe default."""
        p = Path(path)
        if not p.exists():
            logger.warning(f"⚠️ Policy file '{path}' not found. Using defaults.")
            default_yaml = {"feature_extractor": {}, "policies": []}
            self.CONFIG = AppConfig.model_validate(default_yaml)
        else:
            with open(p) as f:
                raw_data = yaml.safe_load(f)
            self.CONFIG = AppConfig.model_validate(raw_data)

        self._validate_services()

    def _validate_services(self):
        """Auto-disables services if credentials are missing."""
        svcs = self.CONFIG.services

        if svcs.telegram_enabled:
            if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_CHAT_ID:
                logger.warning("⚠️ Telegram credentials missing. Disabling Telegram Service.")
                svcs.telegram_enabled = False

        # 2. Check Magalu/S3 (Legacy Env Var Check)
        # TODO: refactor
        # New CloudProvider check happens in the service itself based on provider type
        if svcs.cloud_storage_enabled and svcs.cloud.provider == "magalu":
            if not self.MAGALU_ACCESS_KEY or not self.MAGALU_SECRET_KEY:
                # Fallback to checking CloudConfig internal keys
                if not svcs.cloud.aws_access_key or not svcs.cloud.aws_secret_key:
                    logger.warning("⚠️ Magalu/S3 credentials missing. Disabling Cloud Upload.")
                    svcs.cloud_storage_enabled = False


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()


settings = get_settings()
