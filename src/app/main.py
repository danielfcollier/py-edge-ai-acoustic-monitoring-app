"""
Main Entry Point for Edge Acoustic Monitor.
Leverages umik-base-app for Producer/Consumer topology.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Suppress TensorFlow Logs
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

from umik_base_app import AppArgs, AudioBaseApp, AudioPipeline

from .calibration import setup_calibration
from .context import PipelineContext
from .services.cloud_uploader_service import CloudUploaderService
from .services.health_monitor_service import HealthMonitorService
from .settings import settings
from .sinks.feature_extractor_sink import FeatureExtractorSink
from .sinks.policy_engine_sink import PolicyEngineSink
from .sinks.smart_recorder_sink import SmartRecorderSink

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Suppress noisy HTTP libraries
for lib in ["httpx", "httpcore"]:
    lib_logger = logging.getLogger(lib)
    lib_logger.setLevel(logging.ERROR)
    lib_logger.propagate = False
    handler = logging.StreamHandler()
    handler.setLevel(logging.ERROR)
    formatter = logging.Formatter("%(asctime)s [ERROR] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    lib_logger.addHandler(handler)


def parse_cli_args():
    """Parses application-specific arguments."""
    parser = argparse.ArgumentParser(description="Edge Acoustic Monitor", add_help=False)
    parser.add_argument("-c", "--config", type=str, default="security_policy.yaml", help="Path to policy YAML")
    parser.add_argument("-e", "--env", type=str, default=".env", help="Path to .env file")
    parser.add_argument("--help", action="store_true", help="Show help message")
    return parser.parse_known_args()


def ensure_models_present():
    """Checks/Downloads AI models."""
    model_path = Path("src/yamnet/yamnet.tflite")
    if not model_path.exists():
        logger.info("⬇️ First run detected. Downloading AI models...")
        from scripts import setup_models

        setup_models.main()


def main():
    # App Configuration
    args, unknown = parse_cli_args()

    if args.help:
        print("Usage: edge-monitor [--config PATH] [--env PATH] [Base App Args...]")
        print("Base App Args: --run-mode {monolithic,producer,consumer} --zmq-host ...")
        sys.exit(0)

    if args.env and Path(args.env).exists():
        logger.info(f"Loading secrets from {args.env}")

    settings.load_policy_file(args.config)

    ensure_models_present()

    sys.argv = [sys.argv[0]] + unknown
    base_args = AppArgs.get_args()

    app_config = setup_calibration(base_args)

    # Initialization
    logger.info(f"🚀 Initializing in [{app_config.run_mode.upper()}] mode")

    # Services Layer
    services = []

    health_monitor = HealthMonitorService()
    health_monitor.start()
    services.append(health_monitor)

    if app_config.run_mode in ["monolithic", "consumer"]:
        uploader = CloudUploaderService()
        uploader.start()
        services.append(uploader)

    # Sinks Layer
    context = PipelineContext()
    pipeline = AudioPipeline()

    pipeline.add_sink(FeatureExtractorSink(context))
    pipeline.add_sink(PolicyEngineSink(context))
    pipeline.add_sink(SmartRecorderSink(context))

    # Application Run
    app = AudioBaseApp(app_config=app_config, pipeline=pipeline)

    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        for svc in services:
            svc.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
