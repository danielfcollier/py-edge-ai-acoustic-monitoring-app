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

CONFIG_DIR = Path("/etc/edge-monitor")

def setup_config_files(args):
    """Copies configuration files to /etc/edge-monitor."""
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
    venv_bin = Path(sys.executable).parent
    working_dir = Path.cwd().resolve()
    
    definitions = []
    
    if mode == "monolith":
        definitions.append(("edge-monitor", "src/setup/edge-monitor.service"))
    elif mode == "distributed":
        definitions.append(("edge-producer", "src/setup/edge-producer.service"))
        definitions.append(("edge-consumer", "src/setup/edge-consumer.service"))
        
    print(f"⚙️  Installing services for mode: {mode}")

    for svc_name, template_path in definitions:
        t_path = Path(template_path)
        if not t_path.exists():
            print(f"❌ Missing template: {t_path}")
            continue

        with open(t_path, "r") as f:
            template = f.read()
            
        content = template.format(
            user=user,
            group=user,
            working_dir=working_dir,
            venv_bin=venv_bin,
            config_path=policy_path,
            env_path=env_path
        )
        
        dest = Path(f"/etc/systemd/system/{svc_name}.service")
        with open(dest, "w") as f:
            f.write(content)
            
        print(f"   ✅ Created {dest}")
        subprocess.run(["systemctl", "enable", svc_name], check=True)
        
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print("✅ Systemd Reloaded.")

def main():
    if os.geteuid() != 0:
        print("❌ Run as sudo.")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--calib")
    parser.add_argument("--mode", choices=["monolith", "distributed"], default="monolith")
    
    args = parser.parse_args()
    
    try:
        p_path, e_path = setup_config_files(args)
        install_services(args.mode, p_path, e_path)
        print("\n🎉 Done! Start with: sudo systemctl start edge-[monitor|producer|consumer]")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()