import glob
import os
from setuptools import setup, find_packages


def get_data_files():
    """Bundle service templates, profiles, and user manual into the .deb."""
    data_files = []
    base = "/usr/lib/ai-acoustic-monitor"

    # Service templates → /usr/lib/ai-acoustic-monitor/setup/
    for root, _, files in os.walk("src/setup"):
        if files:
            rel = os.path.relpath(root, "src")
            install_dir = os.path.join(base, rel)
            data_files.append((install_dir, [os.path.join(root, f) for f in files]))

    # Policy profiles → /usr/lib/ai-acoustic-monitor/profiles/
    profiles = glob.glob("docs/user_manual/security_policy_*.yaml")
    if profiles:
        data_files.append((f"{base}/profiles", profiles))

    # YAMNet ONNX model → /usr/lib/ai-acoustic-monitor/yamnet.onnx
    onnx = "src/yamnet/yamnet.onnx"
    if os.path.isfile(onnx):
        data_files.append((base, [onnx]))

    # User manual → /usr/share/doc/ai-acoustic-monitor/
    manual = "docs/user_manual/USER_MANUAL.md"
    if os.path.isfile(manual):
        data_files.append(("/usr/share/doc/ai-acoustic-monitor", [manual]))

    return data_files


setup(
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    data_files=get_data_files(),
    entry_points={
        "console_scripts": [
            "ai-acoustic-monitor-run = app.main:main",
            "ai-acoustic-monitor = scripts.configure:main",
            "ai-acoustic-monitor-setup-models = scripts.setup_yamnet:main",
            "ai-acoustic-monitor-install-service = scripts.install_services:main",
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
