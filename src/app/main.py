"""
Main Entry Point for AI Acoustic Monitor.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import argparse
import logging
import os
import queue
import sys
from pathlib import Path

# Suppress TensorFlow Logs
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import warnings

from umik_base_app import AppArgs, AudioBaseApp, AudioPipeline
from umik_base_app.core.operational_mode import OperationalMode

from .calibration import setup_calibration
from .context import PipelineContext
from .services.cloud_uploader_service import CloudUploaderService
from .services.health_monitor_service import HealthMonitorService
from .services.prometheus_service import PrometheusService
from .services.recorder_transformer_worker import RecorderTransformerWorker
from .services.system_heartbeat_service import SystemHeartbeatService
from .services.telegram_command_receiver import TelegramCommandReceiver
from .settings import settings
from .sinks.basic_metrics_sink import BasicMetricsSink
from .sinks.feature_extractor_sink import FeatureExtractorSink
from .sinks.policy_engine_sink import PolicyEngineSink
from .sinks.sad_gateway_sink import SADGatewaySink
from .sinks.smart_buffer_sink import SmartBufferSink
from .sinks.top_metrics_sink import TopMetricsSink

warnings.filterwarnings("ignore", category=FutureWarning, module="keras.src.export.tf2onnx_lib")

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
    parser = argparse.ArgumentParser(description="AI Acoustic Monitor", add_help=False)
    parser.add_argument("-c", "--config", type=str, default="security_policy.yaml", help="Path to policy YAML")
    parser.add_argument("-e", "--env", type=str, default=".env", help="Path to .env file")
    parser.add_argument("--top-metrics", action="store_true", help="Print peak RMS/Flux/dBSPL summary periodically")
    parser.add_argument(
        "--top-metrics-interval", type=int, default=60, metavar="SEC", help="Summary interval in seconds (default: 60)"
    )
    parser.add_argument("--help", action="store_true", help="Show help message")
    return parser.parse_known_args()


def ensure_models_present():
    """Checks/Downloads AI models."""
    model_path = Path("src/yamnet/yamnet.onnx")
    if not model_path.exists():
        logger.info("⬇️ First run detected. Downloading AI models...")
        from scripts import setup_models

        setup_models.main()


def main():
    # App Configuration
    args, unknown = parse_cli_args()

    if args.help:
        print("Usage: ai-acoustic-monitor-run [--config PATH] [--env PATH] [Base App Args...]")
        print("Base App Args: --run-mode {monolithic,producer,consumer} --zmq-host ...")
        sys.exit(0)

    if args.env and Path(args.env).exists():
        logger.info(f"Loading secrets from {args.env}")

    settings.load_policy_file(args.config, env_path=args.env)

    svc_cfg = settings.CONFIG.services
    if svc_cfg.prometheus_enabled:
        PrometheusService().start(port=svc_cfg.prometheus_port)

    ensure_models_present()

    sys.argv = [sys.argv[0]] + unknown
    base_args = AppArgs.get_args()

    app_config = setup_calibration(base_args)

    max_pending = settings.CONFIG.services.max_pending_uploads
    raw_queue = queue.Queue(maxsize=max_pending)
    upload_queue = queue.Queue(maxsize=max_pending)

    # Ensure Output Path exists
    output_path = settings.CONFIG.services.recording_output_path
    if not output_path.exists():
        logger.info(f"📁 Creating output directory: {output_path}")
        output_path.mkdir(parents=True, exist_ok=True)

    # Initialization
    logger.info(f"🚀 Initializing in [{app_config.run_mode.value.upper()}] mode")

    # Services Layer
    services = []

    health_monitor = HealthMonitorService()
    health_monitor.start()
    services.append(health_monitor)

    # Service: System Heartbeat (CSV Logger)
    if settings.CONFIG.services.heartbeat_enabled:
        system_heartbeat = SystemHeartbeatService()
        system_heartbeat.start()
        services.append(system_heartbeat)

    _consumer_modes = (OperationalMode.MONOLITHIC, OperationalMode.CONSUMER)

    # Service: Recorder Transformer (FIR calibration, cold path)
    if app_config.run_mode in _consumer_modes:
        transformer = RecorderTransformerWorker(raw_queue=raw_queue, upload_queue=upload_queue)
        transformer.start()
        services.append(transformer)

    # Service: Cloud Upload (Consumer)
    if app_config.run_mode in _consumer_modes:
        uploader = CloudUploaderService(upload_queue=upload_queue, output_path=output_path)
        uploader.start()
        services.append(uploader)

    # Pipeline Setup
    context = PipelineContext()

    # Service: Telegram Command Receiver — started after context so /status has live data
    telegram_cmds = TelegramCommandReceiver(
        context=context,
        raw_queue=raw_queue,
        upload_queue=upload_queue,
    )
    telegram_cmds.start()
    services.append(telegram_cmds)

    pipeline = AudioPipeline(sample_rate=app_config.sample_rate)

    pipeline.add_sink(BasicMetricsSink(context))
    pipeline.add_sink(SADGatewaySink(context))
    pipeline.add_sink(FeatureExtractorSink(context))
    pipeline.add_sink(PolicyEngineSink(context))

    if args.top_metrics:
        pipeline.add_sink(TopMetricsSink(context, interval=args.top_metrics_interval))

    # Sink: Smart Buffer (captures raw audio, hands off to transformer)
    pipeline.add_sink(SmartBufferSink(context, raw_queue))

    # Application Start
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
