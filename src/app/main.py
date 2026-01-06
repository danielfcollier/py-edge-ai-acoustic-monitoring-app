"""
Main Entry Point for Edge Acoustic Monitor.
Initializes configuration, services, and the audio processing pipeline.

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

from .context import PipelineContext

from .services.cloud_uploader_service import CloudUploaderService
from .services.health_monitor_service import HealthMonitorService
from .settings import settings
from .sinks.feature_extractor_sink import FeatureExtractorSink
from .sinks.policy_engine_sink import PolicyEngineSink
from .sinks.smart_recorder_sink import SmartRecorderSink

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Main")


def parse_cli_args():
    parser = argparse.ArgumentParser(description="Edge Acoustic Monitor", add_help=False)
    parser.add_argument("-c", "--config", type=str, default="security_policy.yaml", help="Path to policy YAML")
    parser.add_argument("-e", "--env", type=str, default=".env", help="Path to .env file")
    parser.add_argument("-d", "--device", type=str, help="Override input device name (e.g. 'UMIK-1')")
    parser.add_argument("--help", action="store_true", help="Show help message")
    return parser.parse_known_args()


def ensure_models_present():
    model_path = Path("src/yamnet/yamnet.tflite")
    if not model_path.exists():
        logger.info("⬇️ First run detected. Downloading AI models...")
        from scripts import setup_models

        setup_models.main()


def main():
    args, unknown = parse_cli_args()

    if args.help:
        print("Usage: edge-monitor [--config PATH] [--env PATH] [--device NAME]")
        sys.exit(0)

    logger.info("🚀 Initializing Edge Acoustic Monitor...")

    if args.env and Path(args.env).exists():
        logger.info(f"Loading secrets from {args.env}")

    # Load logic
    settings.load_policy_file(args.config)

    # Check dependencies
    ensure_models_present()

    # Determine Audio Device (CLI > Config > Default)
    target_device = args.device or settings.CONFIG.target_device or "UMIK-1"
    os.environ["TARGET_DEVICE_NAME"] = target_device
    logger.info(f"🎤 Target Microphone: '{target_device}'")

    # Clean sys.argv for umik_base_app
    sys.argv = [sys.argv[0]] + unknown

    # Initialize Base App
    base_args = AppArgs.get_args()
    app_config = AppArgs.validate_args(base_args)

    # Start Background Services
    uploader = CloudUploaderService()
    uploader.start()

    health_monitor = HealthMonitorService()
    health_monitor.start()

    # Build Pipeline
    context = PipelineContext()
    pipeline = AudioPipeline()

    pipeline.add_sink(FeatureExtractorSink(context))
    pipeline.add_sink(PolicyEngineSink(context))
    pipeline.add_sink(SmartRecorderSink(context))

    app = AudioBaseApp(app_config=app_config, pipeline=pipeline)

    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        health_monitor.stop()
        uploader.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
