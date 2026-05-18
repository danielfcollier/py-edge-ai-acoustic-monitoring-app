"""
Calibration Setup Module.
Handles the injection of hardware settings and synchronization of calibration data.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import argparse
import logging

from umik_base_app import AppArgs
from umik_base_app.app_config import AppConfig

from .settings import settings

logger = logging.getLogger(__name__)


def setup_calibration(base_args: argparse.Namespace) -> AppConfig:
    """
    Orchestrates the calibration setup process:
    1. Injects hardware settings (file path) from YAML/Env into CLI args.
    2. Validates arguments to trigger microphhone auto-detection.
    3. Syncs the calculated sensitivity values back to the global settings.

    :param base_args: The raw arguments parsed from the command line.
    :return: The fully validated AppConfig object.
    """
    settings.inject_hardware_settings(base_args)

    # This detects hardware and loads the calibration file values into 'app_config'
    app_config = AppArgs.validate_args(base_args)

    if app_config.calibration is not None:
        cal = app_config.calibration
        logger.info(f"🔧 Syncing calibration to global settings: {cal.sensitivity_dbfs:.2f} dB")
        settings.HARDWARE.NOMINAL_SENSITIVITY_DBFS = cal.sensitivity_dbfs
        settings.HARDWARE.REFERENCE_DBSPL = cal.reference_dbspl

    return app_config
