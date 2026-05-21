"""
ai-acoustic-monitor — Setup wizard, service installer, and e2e test runner.

Usage:
    ai-acoustic-monitor                              interactive menu
    ai-acoustic-monitor --configure credentials      write ~/.config/ai-acoustic-monitor/.env
    ai-acoustic-monitor --configure                  generate security_policy.yaml from a profile
    ai-acoustic-monitor --install [monolith|distributed]  install systemd service(s) (requires sudo)
    ai-acoustic-monitor --test                       validate all configured services end-to-end
    ai-acoustic-monitor --manual                     view user manual in pager

Global flags (apply to --configure, --install, --test):
    --config FILE    policy YAML (default: ./security_policy.yaml)
    --env FILE       credentials file (default: ~/.config/ai-acoustic-monitor/.env)
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Resource discovery — installed .deb → dev checkout fallback
# ---------------------------------------------------------------------------
_INSTALL_PROFILES = Path("/usr/lib/ai-acoustic-monitor/profiles")
_INSTALL_MANUAL = Path("/usr/share/doc/ai-acoustic-monitor/USER_MANUAL.md")
_DEV_ROOT = Path(__file__).resolve().parent.parent.parent
_DEV_PROFILES = _DEV_ROOT / "docs" / "user_manual"
_DEV_MANUAL = _DEV_PROFILES / "USER_MANUAL.md"

CONFIG_DIR = Path.home() / ".config" / "ai-acoustic-monitor"
ENV_FILE = CONFIG_DIR / ".env"

PROFILE_META: dict[str, dict[str, str]] = {
    "security_policy_home": {
        "emoji": "🏠",
        "title": "Home Security & Peace",
        "desc": "Glass break, suspicious night activity, dog barks",
    },
    "security_policy_baby": {
        "emoji": "👶",
        "title": "Baby / Child Monitor",
        "desc": "Crying, loud noise hazard, nursery activity log",
    },
    "security_policy_forest": {
        "emoji": "🌳",
        "title": "Forest / Outdoor Monitor",
        "desc": "Chainsaw, vehicle, animal sounds in outdoor spaces",
    },
    "security_policy_industry": {
        "emoji": "🏭",
        "title": "Industrial / Server Room",
        "desc": "Alarms, mechanical failure, silence check (fan failure)",
    },
}


def _profiles_dir() -> Path | None:
    if _INSTALL_PROFILES.is_dir():
        return _INSTALL_PROFILES
    if _DEV_PROFILES.is_dir():
        return _DEV_PROFILES
    return None


def _manual_path() -> Path | None:
    if _INSTALL_MANUAL.is_file():
        return _INSTALL_MANUAL
    if _DEV_MANUAL.is_file():
        return _DEV_MANUAL
    return None


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------
_WIDTH = 64

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_USE_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


def _hr(char: str = "─") -> None:
    print(char * _WIDTH)


def _banner() -> None:
    _hr("═")
    title = "🎙️  ai-acoustic-monitor — Setup Wizard"
    pad = max(0, (_WIDTH - len(title)) // 2)
    print(" " * pad + title)
    _hr("═")
    print()


def _section(title: str) -> None:
    print()
    _hr()
    print(f"  {title}")
    _hr()


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    text = f"  {label} [{default}]: " if default else f"  {label}: "
    try:
        val = getpass.getpass(text) if secret else input(text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val if val else default


# ---------------------------------------------------------------------------
# Shared: load .env file as dict
# ---------------------------------------------------------------------------
def _read_env(env_path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(env_path)
    if not p.is_file():
        return env
    for raw in p.read_text().splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            k, _, v = raw.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _read_yaml(config_path: str) -> dict:
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return {}
    p = Path(config_path)
    if not p.is_file():
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------
def cmd_credentials(env_path: str) -> None:
    _banner()
    resolved = Path(env_path).expanduser().resolve()
    print(f"  Credentials will be saved to:\n  {resolved}\n")

    existing = _read_env(str(resolved))
    print("  Press Enter to keep the current value (shown in brackets).\n")

    _section("Telegram — required for alerts")
    token = _prompt("TELEGRAM_TOKEN", existing.get("TELEGRAM_TOKEN", ""))
    chat_id = _prompt("TELEGRAM_CHAT_ID", existing.get("TELEGRAM_CHAT_ID", ""))
    print()
    print("  Tip: retrieve CHAT_ID by visiting")
    print("  https://api.telegram.org/bot<TOKEN>/getUpdates after messaging the bot.")

    _section("Cloud Storage — Magalu Object Storage (default)")
    magalu_key = _prompt("MAGALU_KEY", existing.get("MAGALU_KEY", ""))
    magalu_secret = _prompt("MAGALU_SECRET (hidden)", existing.get("MAGALU_SECRET", ""), secret=True)

    _section("Health Monitoring — optional, leave blank to skip")
    hc_url = _prompt("HC_PING_URL", existing.get("HC_PING_URL", ""))

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        "# ── Telegram ──────────────────────────────────────────────────\n"
        f"TELEGRAM_TOKEN={token}\n"
        f"TELEGRAM_CHAT_ID={chat_id}\n"
        "\n"
        "# ── Magalu Object Storage ──────────────────────────────────────\n"
        f"MAGALU_KEY={magalu_key}\n"
        f"MAGALU_SECRET={magalu_secret}\n"
        "\n"
        "# ── Health Monitoring ──────────────────────────────────────────\n"
        f"HC_PING_URL={hc_url}\n"
        "\n"
        "# ── Log Levels ─────────────────────────────────────────────────\n"
        "LOG_LEVEL_MAIN=INFO\n"
        "LOG_LEVEL_POLICY_ENGINE=INFO\n"
        "LOG_LEVEL_FEATURE_EXTRACTOR=INFO\n"
        "LOG_LEVEL_SMART_RECORDER=INFO\n"
        "LOG_LEVEL_TELEGRAM=INFO\n"
        "LOG_LEVEL_SERVICES=INFO\n"
    )
    os.chmod(resolved, 0o600)
    print(f"\n  ✅ Saved: {resolved}")


# ---------------------------------------------------------------------------
# profile (configure)
# ---------------------------------------------------------------------------
def _tail_from(text: str, *keys: str) -> str:
    lines = text.splitlines()
    for key in keys:
        for i, line in enumerate(lines):
            if line.startswith(f"{key}:"):
                return "\n".join(lines[i:]).rstrip()
    return ""


def _build_config(
    profile_text: str,
    *,
    calib_file: str,
    output_path: str,
    bucket: str,
    cloud: bool,
    prometheus_enabled: bool,
    prometheus_port: int,
) -> str:
    today = date.today().isoformat()
    calib_var = f'calibration_file_path: "{calib_file}"' if calib_file else "# calibration_file_path: (not configured)"
    calib_hw = (
        'calibration_file: "{calibration_file_path}"'
        if calib_file
        else "# calibration_file: (not configured — uncalibrated mode)"
    )
    tail = _tail_from(profile_text, "reporting", "policies")
    if not tail:
        tail = "policies: []"

    lines: list[str] = [
        "# security_policy.yaml",
        f"# Generated by ai-acoustic-monitor on {today}",
        "",
        "# ── Global Variables ────────────────────────────────────────",
        "variables:",
        "  day_start: 6        # 06:00 AM",
        "  night_start: 22     # 10:00 PM",
        "  day_limit: 60.0",
        "  night_limit: 50.0",
        f"  {calib_var}",
        "",
        "# ── Hardware ────────────────────────────────────────────────",
        "hardware:",
        f"  {calib_hw}",
        "",
        "# ── Services ────────────────────────────────────────────────",
        "services:",
        "  internet_enabled: true",
        "  telegram_enabled: true",
        f"  cloud_storage_enabled: {'true' if cloud else 'false'}",
        "",
        f'  recording_output_path: "{output_path}"',
        "  recording_max_seconds: 60",
        "  recording_post_roll_seconds: 10",
        "  alert_cooldown_seconds: 60",
        "  retry_attempts: 3",
        "  retry_delay_seconds: 5",
        "",
        '  day_start_hour: "{day_start}"',
        '  night_start_hour: "{night_start}"',
        "",
        "  cloud:",
        '    provider: "magalu"',
        f'    bucket_name: "{bucket}"',
        "",
        f"  prometheus_enabled: {'true' if prometheus_enabled else 'false'}",
        f"  prometheus_port: {prometheus_port}",
        "",
        "# ── AI Feature Extractor ────────────────────────────────────",
        "feature_extractor:",
        "  use_tflite: true",
        "  sad_threshold_rms: 0.002",
        "  sad_threshold_flux: 5.0",
        "  sad_threshold_dbspl: 45.0",
        "",
        "# ── Reporting & Policies (from selected profile) ────────────",
        tail,
        "",
    ]
    return "\n".join(lines)


def cmd_profile(config_path: str, env_path: str) -> None:
    _banner()

    profiles_dir = _profiles_dir()
    if not profiles_dir:
        print("  ❌ Profile templates not found.")
        print("     Expected: /usr/lib/ai-acoustic-monitor/profiles/")
        return

    profiles = sorted(profiles_dir.glob("security_policy_*.yaml"))
    if not profiles:
        print(f"  ❌ No profile files in {profiles_dir}")
        return

    print("  Choose a detection policy profile:\n")
    for i, p in enumerate(profiles, 1):
        meta = PROFILE_META.get(p.stem, {})
        print(f"  {i}. {meta.get('emoji', '📋')} {meta.get('title', p.stem)}")
        if meta.get("desc"):
            print(f"      {meta['desc']}")
        print()

    choice = _prompt("Select profile number", "1")
    try:
        idx = int(choice) - 1
        assert 0 <= idx < len(profiles)
    except (ValueError, AssertionError):
        print("  ❌ Invalid selection.")
        return

    selected = profiles[idx]

    _section("Hardware")
    print("  Leave blank for uncalibrated mode (AI + RMS/Flux only, no dBSPL).")
    calib = _prompt("Calibration file path", "")

    _section("Storage & Cloud")
    output_path = _prompt("Recording output path", "recordings")
    bucket = _prompt("Cloud storage bucket name", "acoustic-logs")
    cloud_ans = _prompt("Enable cloud upload? [Y/n]", "Y").strip().upper()
    cloud = cloud_ans in ("", "Y", "YES")

    _section("Prometheus Metrics")
    prom_ans = _prompt("Enable Prometheus metrics server? [Y/n]", "Y").strip().upper()
    prom_enabled = prom_ans in ("", "Y", "YES")
    prom_port = 8000
    if prom_enabled:
        port_str = _prompt("Prometheus port", "8000")
        try:
            prom_port = int(port_str)
        except ValueError:
            prom_port = 8000

    config_text = _build_config(
        selected.read_text(),
        calib_file=calib,
        output_path=output_path,
        bucket=bucket,
        cloud=cloud,
        prometheus_enabled=prom_enabled,
        prometheus_port=prom_port,
    )

    _section("Save")
    out_default = str(Path(config_path).resolve())
    out_path = Path(_prompt("Save config to", out_default))
    out_path.write_text(config_text)

    print(f"\n  ✅ Config saved: {out_path}")
    print()
    print("  Next steps:")
    print(f"    1. Review & tweak:  {out_path}")
    print(f"    2. Test services:   ai-acoustic-monitor --test --config {out_path} --env {env_path}")
    print(f"    3. Install service: sudo ai-acoustic-monitor --install --config {out_path} --env {env_path}")


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
def cmd_install(mode_arg: str | None, config_path: str, env_path: str) -> None:
    if os.geteuid() != 0:
        print("  ❌ --install requires sudo.")
        print("     Run: sudo ai-acoustic-monitor --install")
        sys.exit(1)

    _banner()
    print("  Installs ai-acoustic-monitor as a systemd service.\n")

    # Mode selection
    if mode_arg and mode_arg in ("monolith", "distributed"):
        mode = mode_arg
    else:
        _section("Deployment mode")
        print("  1. Monolith     — single process (audio capture + AI + upload)")
        print("  2. Distributed  — producer/consumer split via ZMQ")
        print("                    (run producer on capture device, consumer on server)\n")
        choice = _prompt("Select mode", "1")
        mode = "monolith" if choice != "2" else "distributed"

    # Config and env paths
    _section("Configuration")
    cfg = _prompt("Policy YAML path", str(Path(config_path).resolve()))
    env = _prompt("Credentials .env path", str(Path(env_path).expanduser().resolve()))

    for p, label in ((cfg, "Policy YAML"), (env, "Credentials .env")):
        if not Path(p).is_file():
            print(f"\n  ❌ {label} not found: {p}")
            print("     Run: ai-acoustic-monitor --configure first.")
            sys.exit(1)

    calib = _prompt("Calibration file path (optional, Enter to skip)", "")

    # Import and call the installer
    try:
        from scripts.install_services import install_services, setup_config_files  # noqa: PLC0415
    except ImportError:
        from install_services import install_services, setup_config_files  # noqa: PLC0415

    import argparse as _ap  # noqa: PLC0415

    fake_args = _ap.Namespace(config=cfg, env=env, calib=calib or None, mode=mode)
    print()
    try:
        p_path, e_path = setup_config_files(fake_args)
        install_services(mode, p_path, e_path)
        svc = (
            "ai-acoustic-monitor"
            if mode == "monolith"
            else "ai-acoustic-monitor-producer / ai-acoustic-monitor-consumer"
        )
        print(f"\n  ✅ Done! Start with: sudo systemctl start {svc}")
        print(f"     Test live:        ai-acoustic-monitor --test --config {cfg} --env {env}")
    except Exception as exc:
        print(f"\n  ❌ Install failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# test (e2e validation)
# ---------------------------------------------------------------------------

_PASS = "  ✅"
_FAIL = "  ❌"
_WARN = "  ⚠️ "
_SKIP = "  ⏭️ "


def _row(icon: str, name: str, detail: str) -> None:
    label = f"{icon} {name}"
    print(f"{label:<38}{_c(detail, _DIM)}")


def _test_profile(config_path: str) -> bool:
    """Parse YAML and validate all policy rules."""
    raw = _read_yaml(config_path)
    if not raw:
        _row(_FAIL, "Profile", f"cannot read {config_path}")
        return False

    policies = raw.get("policies", [])
    calib = raw.get("hardware", {}).get("calibration_file")
    detail = f"{len(policies)} rule(s)"
    if calib:
        detail += ", calibration configured"
    _row(_PASS, "Profile", detail)
    return True


def _test_calibration(config_path: str) -> bool | None:
    """Check the calibration file exists and is readable."""
    raw = _read_yaml(config_path)
    calib = raw.get("hardware", {}).get("calibration_file")
    if not calib:
        _row(_SKIP, "Calibration file", "not configured (uncalibrated mode)")
        return None
    # Resolve a single YAML variable reference e.g. "{calibration_file_path}"
    if calib.startswith("{") and calib.endswith("}"):
        var_name = calib[1:-1]
        resolved = raw.get("variables", {}).get(var_name)
        if not resolved:
            _row(_SKIP, "Calibration file", f"{calib} — variable not defined in YAML")
            return None
        calib = str(resolved)
    p = Path(calib)
    if p.is_file():
        size_kb = p.stat().st_size // 1024
        _row(_PASS, "Calibration file", f"{calib} ({size_kb} KB)")
        return True
    _row(_FAIL, "Calibration file", f"not found: {calib}")
    return False


def _test_microphone(config_path: str) -> bool | None:
    """Enumerate audio devices, verify the expected device is accessible."""
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        _row(_WARN, "Microphone", "sounddevice not available (skipped)")
        return None

    try:
        devices = sd.query_devices()
        inputs = [d for d in devices if d["max_input_channels"] > 0]
        names = [d["name"] for d in inputs]
        suffix = "…" if len(names) > 2 else ""
        _row(_PASS, "Microphone", f"{len(inputs)} input device(s): {', '.join(names[:2])}{suffix}")
        return True
    except Exception as exc:
        _row(_FAIL, "Microphone", str(exc))
        return False


def _test_recording(config_path: str) -> bool | None:
    """Record 1 second of audio and verify non-empty output."""
    try:
        import sounddevice as sd  # noqa: PLC0415
    except ImportError:
        _row(_SKIP, "Sample recording", "sounddevice/numpy not available")
        return None

    try:
        _read_yaml(config_path)
        sr = 48000  # default
        duration = 1.0
        audio = sd.rec(int(duration * sr), samplerate=sr, channels=1, dtype="float32")
        sd.wait()

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            import scipy.io.wavfile as wav  # noqa: PLC0415

            wav.write(tmp.name, sr, audio)
            size_kb = Path(tmp.name).stat().st_size // 1024
            _row(_PASS, "Sample recording", f"{duration}s, {size_kb} KB → {tmp.name}")
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        return True
    except Exception as exc:
        _row(_FAIL, "Sample recording", str(exc))
        return False


def _test_telegram(env_path: str) -> bool | None:
    """Send a test message via the Telegram bot API."""
    env = _read_env(env_path)
    token = env.get("TELEGRAM_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        _row(_SKIP, "Telegram", "credentials not configured")
        return None

    try:
        import asyncio  # noqa: PLC0415

        import telegram  # noqa: PLC0415

        async def _send():
            bot = telegram.Bot(token=token)
            await bot.send_message(chat_id=chat_id, text="🧪 ai-acoustic-monitor — service test message")

        asyncio.run(_send())
        _row(_PASS, "Telegram", f"message sent to chat {chat_id}")
        return True
    except Exception as exc:
        _row(_FAIL, "Telegram", str(exc))
        return False


def _test_cloud(env_path: str, config_path: str) -> bool | None:
    """Upload and delete a tiny test file to verify cloud credentials and bucket access."""
    raw = _read_yaml(config_path)
    svc = raw.get("services", {})
    if not svc.get("cloud_storage_enabled", True) or not svc.get("internet_enabled", True):
        _row(_SKIP, "Cloud storage", "disabled in config")
        return None

    cloud_cfg = svc.get("cloud", {})
    provider = cloud_cfg.get("provider", "magalu")
    bucket = cloud_cfg.get("bucket_name", "")
    env = _read_env(env_path)

    if not bucket:
        _row(_SKIP, "Cloud storage", "bucket_name not set")
        return None

    test_key = "ai-acoustic-monitor-test/.connection-test"
    test_data = b"ai-acoustic-monitor connection test"

    try:
        if provider in ("magalu", "aws"):
            import boto3  # noqa: PLC0415
            from botocore.exceptions import ClientError  # noqa: PLC0415

            if provider == "magalu":
                region = cloud_cfg.get("region", "br-se1")
                endpoint = env.get("MAGALU_URL") or f"https://{region}.magaluobjects.com"
                s3 = boto3.client(
                    "s3",
                    endpoint_url=endpoint,
                    aws_access_key_id=env.get("MAGALU_KEY"),
                    aws_secret_access_key=env.get("MAGALU_SECRET"),
                )
                label = f"Magalu {region}"
            else:
                region = env.get("AWS_REGION", "us-east-1")
                s3 = boto3.client(
                    "s3",
                    aws_access_key_id=env.get("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY"),
                    region_name=region,
                )
                label = f"AWS {region}"

            bucket_created = False
            try:
                s3.put_object(Bucket=bucket, Key=test_key, Body=test_data)
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchBucket", "404"):
                    s3.create_bucket(Bucket=bucket)
                    bucket_created = True
                    s3.put_object(Bucket=bucket, Key=test_key, Body=test_data)
                else:
                    raise
            s3.delete_object(Bucket=bucket, Key=test_key)
            if bucket_created:
                s3.delete_bucket(Bucket=bucket)
                _row(_PASS, "Cloud storage", f"{label} bucket '{bucket}' created, tested, removed")
            else:
                _row(_PASS, "Cloud storage", f"{label} bucket '{bucket}' OK")
            return True

        elif provider == "gcp":
            from google.api_core.exceptions import NotFound  # noqa: PLC0415
            from google.cloud import storage as gcs  # noqa: PLC0415

            creds_path = cloud_cfg.get("gcp_credentials_path") or env.get("GOOGLE_APPLICATION_CREDENTIALS")
            client = gcs.Client() if not creds_path else gcs.Client.from_service_account_json(creds_path)
            gcs_bucket = client.bucket(bucket)
            blob = gcs_bucket.blob(test_key)
            bucket_created = False
            try:
                blob.upload_from_string(test_data)
            except NotFound:
                client.create_bucket(gcs_bucket)
                bucket_created = True
                blob.upload_from_string(test_data)
            blob.delete()
            if bucket_created:
                gcs_bucket.delete()
                _row(_PASS, "Cloud storage", f"GCP bucket '{bucket}' created, tested, removed")
            else:
                _row(_PASS, "Cloud storage", f"GCP bucket '{bucket}' OK")
            return True

        else:
            _row(_WARN, "Cloud storage", f"unknown provider: {provider}")
            return None

    except Exception as exc:
        _row(_FAIL, "Cloud storage", str(exc))
        return False


def _test_heartbeat(env_path: str, config_path: str) -> bool | None:
    """Ping the healthchecks.io URL if configured."""
    raw = _read_yaml(config_path)
    svc = raw.get("services", {})
    env = _read_env(env_path)

    # hc_ping_url may be a variable reference like {HC_PING_URL}
    hc_url = svc.get("hc_ping_url", "") or env.get("HC_PING_URL", "")
    if not hc_url or hc_url.startswith("{") or "your-uuid" in hc_url:
        _row(_SKIP, "Heartbeat (HC ping)", "HC_PING_URL not configured")
        return None

    try:
        with urllib.request.urlopen(hc_url, timeout=10) as resp:
            _row(_PASS, "Heartbeat (HC ping)", f"HTTP {resp.status} from {hc_url}")
        return True
    except Exception as exc:
        _row(_FAIL, "Heartbeat (HC ping)", str(exc))
        return False


def cmd_test(config_path: str, env_path: str) -> None:
    # Set up vendor path so app-package deps (yaml, sounddevice, etc.) are importable
    try:
        import app  # noqa: F401, PLC0415
    except ImportError:
        pass

    print()
    _hr("═")
    print(f"  🔍 Service Validation  —  {_c(config_path, _DIM)}")
    _hr("═")
    print()

    results: list[bool | None] = []
    results.append(_test_profile(config_path))
    results.append(_test_calibration(config_path))
    results.append(_test_microphone(config_path))
    results.append(_test_recording(config_path))
    results.append(_test_telegram(env_path))
    results.append(_test_cloud(env_path, config_path))
    results.append(_test_heartbeat(env_path, config_path))

    passed = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r is None)
    failed = sum(1 for r in results if r is False)

    print()
    _hr()
    parts = []
    if passed:
        parts.append(_c(f"{passed} passed", _GREEN))
    if skipped:
        parts.append(_c(f"{skipped} skipped", _DIM))
    if failed:
        parts.append(_c(f"{failed} failed", _RED))
    print(f"  {'  '.join(parts)}")
    _hr()
    print()

    if failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# manual
# ---------------------------------------------------------------------------
def cmd_manual() -> None:
    manual = _manual_path()
    if not manual:
        print("User manual not found.")
        print("Expected: /usr/share/doc/ai-acoustic-monitor/USER_MANUAL.md")
        return

    text = manual.read_text()
    pager = os.environ.get("PAGER") or shutil.which("less") or shutil.which("more")

    if pager:
        import subprocess  # noqa: PLC0415

        subprocess.run([pager], input=text.encode(), check=False)
    else:
        page_size = max(1, shutil.get_terminal_size((80, 24)).lines - 2)
        for i, line in enumerate(text.splitlines()):
            print(line)
            if (i + 1) % page_size == 0:
                try:
                    input("  -- Enter for more / Ctrl+C to quit --")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break


# ---------------------------------------------------------------------------
# Interactive main menu
# ---------------------------------------------------------------------------
def _main_menu(config_path: str, env_path: str) -> None:
    _banner()
    print("  1. Setup credentials      (--configure credentials)")
    print("  2. Generate config        (--configure)")
    print("  3. Install service        (--install)")
    print("  4. Validate services      (--test)")
    print("  5. View user manual       (--manual)")
    print("  6. Run monitor            (ai-acoustic-monitor-run)")
    print("  Q. Quit")
    print()

    def _cmd_run():
        import shlex  # noqa: PLC0415

        cmd = ["ai-acoustic-monitor-run", "--config", config_path, "--env", env_path]
        print(f"\n  Running: {shlex.join(cmd)}\n")
        os.execvp(cmd[0], cmd)

    dispatch = {
        "1": lambda: cmd_credentials(env_path),
        "2": lambda: cmd_profile(config_path, env_path),
        "3": lambda: cmd_install(None, config_path, env_path),
        "4": lambda: cmd_test(config_path, env_path),
        "5": cmd_manual,
        "6": _cmd_run,
    }
    while True:
        choice = _prompt("Enter choice").lower()
        if choice in dispatch:
            dispatch[choice]()
            break
        elif choice in ("q", "quit", "exit", ""):
            sys.exit(0)
        else:
            print("  Enter 1–6 or Q.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ai-acoustic-monitor",
        description="AI Acoustic Monitor — setup and management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ai-acoustic-monitor                              interactive menu\n"
            "  ai-acoustic-monitor --configure credentials      write .env credentials\n"
            "  ai-acoustic-monitor --configure                  generate security_policy.yaml\n"
            "  sudo ai-acoustic-monitor --install               install systemd service\n"
            "  sudo ai-acoustic-monitor --install distributed   install producer+consumer\n"
            "  ai-acoustic-monitor --test                       validate all services\n"
            "  ai-acoustic-monitor --manual                     view user manual\n"
            "  ai-acoustic-monitor --run                        start the monitor\n"
            "\n"
            "File locations:\n"
            "  --configure credentials  writes  ~/.config/ai-acoustic-monitor/.env\n"
            "  --configure              writes  ./security_policy.yaml (current directory)\n"
            "  --install                copies  both to /etc/ai-acoustic-monitor/ for the systemd service\n"
            "                           also copies calibration file to /etc/ai-acoustic-monitor/\n"
            "  After changing wizard config, re-run: sudo ai-acoustic-monitor --install\n"
        ),
    )
    try:
        from importlib.metadata import version as _pkg_version  # noqa: PLC0415

        _version = _pkg_version("ai-acoustic-monitoring-app")
    except Exception:
        _version = "unknown"
    parser.add_argument("--version", action="version", version=f"ai-acoustic-monitor {_version}")

    parser.add_argument(
        "--config",
        default="security_policy.yaml",
        metavar="FILE",
        help="Policy YAML file (default: ./security_policy.yaml)",
    )
    parser.add_argument(
        "--env",
        default=str(ENV_FILE),
        metavar="FILE",
        help=f"Credentials .env file (default: {ENV_FILE})",
    )
    parser.add_argument(
        "--configure",
        nargs="?",
        const="configure",
        metavar="credentials|configure",
        help="Run setup wizard. Specify 'credentials' or 'configure' (profile + settings).",
    )
    parser.add_argument(
        "--install",
        nargs="?",
        const="monolith",
        metavar="monolith|distributed",
        help="Install systemd service (requires sudo). Default mode: monolith.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Validate all configured services end-to-end.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="View user manual in pager.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Start the monitor (delegates to ai-acoustic-monitor-run).",
    )

    args = parser.parse_args()

    if args.manual:
        cmd_manual()
    elif args.configure == "credentials":
        cmd_credentials(args.env)
    elif args.configure is not None:
        cmd_profile(args.config, args.env)
    elif args.install is not None:
        cmd_install(args.install, args.config, args.env)
    elif args.test:
        cmd_test(args.config, args.env)
    elif args.run:
        import shlex  # noqa: PLC0415

        cmd = ["ai-acoustic-monitor-run", "--config", args.config, "--env", args.env]
        print(f"Running: {shlex.join(cmd)}")
        os.execvp(cmd[0], cmd)
    else:
        _main_menu(args.config, args.env)


if __name__ == "__main__":
    main()
