import os
from setuptools import setup, find_packages


def get_data_files():
    """Bundle src/setup/ service templates into /usr/lib/ai-acoustic-monitor/setup/."""
    data_files = []
    base_install_path = "/usr/lib/ai-acoustic-monitor"
    for root, _, files in os.walk("src/setup"):
        if files:
            rel = os.path.relpath(root, "src")
            install_dir = os.path.join(base_install_path, rel)
            file_paths = [os.path.join(root, f) for f in files]
            data_files.append((install_dir, file_paths))
    return data_files


setup(
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    data_files=get_data_files(),
    entry_points={
        "console_scripts": [
            "edge-monitor-run = app.main:main",
            "edge-monitor-setup-models = scripts.setup_yamnet:main",
            "edge-monitor-install-service = scripts.install_services:main",
            "edge-monitor-convert = scripts.convert_audio:main",
        ]
    },
    description="Edge AI Acoustic Monitoring App",
    long_description=(
        "Real-time acoustic monitoring for Raspberry Pi using YAMNet AI.\n"
        "\n"
        "Features:\n"
        " * YAMNet AI sound classification (521 classes)\n"
        " * YAML-driven security policy engine\n"
        " * Telegram bot alerts and remote control (/status, /privacy)\n"
        " * Privacy mode with per-rule critical override\n"
        " * Cloud evidence upload (Magalu / AWS S3 / GCP)\n"
        " * GPIO heartbeat and healthchecks.io dead-man switch\n"
    ),
)
