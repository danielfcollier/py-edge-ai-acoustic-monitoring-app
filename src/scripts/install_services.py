"""
Edge Monitor System Installer.
Installs systemd services for Monolithic or Distributed (Producer/Consumer) topologies.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path("/etc/ai-acoustic-monitor")

# Service template search order: installed .deb → dev checkout
_INSTALL_TEMPLATES = Path("/usr/lib/ai-acoustic-monitor/setup")
_DEV_TEMPLATES = Path(__file__).resolve().parent.parent / "setup"


def _templates_dir() -> Path:
    if _INSTALL_TEMPLATES.is_dir():
        return _INSTALL_TEMPLATES
    if _DEV_TEMPLATES.is_dir():
        return _DEV_TEMPLATES
    raise FileNotFoundError(f"Service templates not found. Checked:\n  {_INSTALL_TEMPLATES}\n  {_DEV_TEMPLATES}")


def _installed_bin() -> Path:
    """Return the directory that contains the ai-acoustic-monitor-run binary."""
    # Installed via apt: /usr/bin/ai-acoustic-monitor-run
    if Path("/usr/bin/ai-acoustic-monitor-run").exists():
        return Path("/usr/bin")
    # Running from a venv (dev/uv install)
    return Path(sys.executable).parent


def setup_config_files(args):
    """Copies configuration files to /etc/ai-acoustic-monitor."""
    print(f"📂 Setting up configuration in {CONFIG_DIR}...")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    src_policy = Path(args.config).resolve()
    dst_policy = CONFIG_DIR / "security_policy.yaml"
    shutil.copy(src_policy, dst_policy)

    src_env = Path(args.env).resolve()
    dst_env = CONFIG_DIR / ".env"
    shutil.copy(src_env, dst_env)
    os.chmod(dst_env, 0o600)

    if args.calib:
        src_calib = Path(args.calib).resolve()
        dst_calib = CONFIG_DIR / src_calib.name
        shutil.copy(src_calib, dst_calib)

    return dst_policy, dst_env


def install_services(mode, policy_path, env_path):
    """Installs systemd units based on the selected mode."""
    user = os.environ.get("SUDO_USER", getpass.getuser())
    venv_bin = _installed_bin()
    working_dir = Path.cwd().resolve()
    templates = _templates_dir()

    definitions = []
    if mode == "monolith":
        definitions.append(("ai-acoustic-monitor", templates / "ai-acoustic-monitor.service"))
    elif mode == "distributed":
        definitions.append(("ai-acoustic-monitor-producer", templates / "ai-acoustic-monitor-producer.service"))
        definitions.append(("ai-acoustic-monitor-consumer", templates / "ai-acoustic-monitor-consumer.service"))

    print(f"⚙️  Installing services for mode: {mode}")

    for svc_name, template_path in definitions:
        if not template_path.exists():
            print(f"❌ Missing template: {template_path}")
            continue

        content = template_path.read_text().format(
            user=user,
            group=user,
            working_dir=working_dir,
            venv_bin=venv_bin,
            config_path=policy_path,
            env_path=env_path,
        )

        dest = Path(f"/etc/systemd/system/{svc_name}.service")
        dest.write_text(content)
        print(f"   ✅ Created {dest}")
        subprocess.run(["systemctl", "enable", svc_name], check=True)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print("✅ Systemd reloaded.")


def main():
    if os.geteuid() != 0:
        print("❌ Run as sudo.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Install ai-acoustic-monitor systemd service(s)")
    parser.add_argument("--config", required=True, help="Path to security_policy.yaml")
    parser.add_argument("--env", required=True, help="Path to .env credentials file")
    parser.add_argument("--calib", help="Path to microphone calibration file (optional)")
    parser.add_argument("--mode", choices=["monolith", "distributed"], default="monolith")

    args = parser.parse_args()

    try:
        p_path, e_path = setup_config_files(args)
        install_services(args.mode, p_path, e_path)
        print("\n🎉 Done! Start with: sudo systemctl start ai-acoustic-monitor")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
