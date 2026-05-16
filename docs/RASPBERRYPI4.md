# Raspberry Pi 4B — Field Guide

Practical reference for deploying and operating the Edge Acoustic Monitor on a Raspberry Pi 4B.
Assumes Raspberry Pi OS Bookworm (64-bit, headless) or Ubuntu Server 24.04.

## Table of Contents

1. [Hardware Requirements](#1-hardware-requirements)
2. [First-Time Setup (Headless)](#2-first-time-setup-headless)
3. [Microphone Setup (UMIK-1)](#3-microphone-setup-umik-1)
4. [Running as a Systemd Service](#4-running-as-a-systemd-service)
5. [Monitoring & Logs](#5-monitoring--logs)
6. [Performance & Thermal](#6-performance--thermal)
7. [Storage Management](#7-storage-management)
8. [Networking & Remote Access](#8-networking--remote-access)
9. [Troubleshooting](#9-troubleshooting)
10. [Quick Command Reference](#10-quick-command-reference)

## 1. Hardware Requirements

| Component | Recommended | Minimum |
|---|---|---|
| Board | Raspberry Pi 4B (4 GB RAM) | RPi 4B (2 GB RAM) |
| OS storage | 32 GB+ SD card (Class 10) | 16 GB |
| Evidence storage | USB SSD (256 GB+) | SD card (risky for 24/7 writes) |
| Microphone | miniDSP UMIK-1 (USB calibrated) | Any USB mic |
| Cooling | Active fan or heatsink case | Passive heatsink |
| Power | Official 5 V / 3 A USB-C PSU | Any 5 V / 3 A PSU |

**Why a USB SSD for recordings?** WAV files from continuous monitoring wear out SD cards within months. Mount a USB SSD at `/mnt/recordings` and set `recording_output_path: "/mnt/recordings"` in `security_policy.yaml`.


## 2. First-Time Setup (Headless)

### Enable SSH on first boot

Using Raspberry Pi Imager, enable SSH and pre-configure Wi-Fi before flashing. Alternatively, create an empty `ssh` file on the boot partition:

```bash
touch /Volumes/bootfs/ssh       # macOS
# or
touch /boot/ssh                 # Linux
```

### Connect over SSH

```bash
ssh pi@raspberrypi.local        # mDNS — works on most home networks
ssh pi@<ip-address>             # use nmap or router table if mDNS fails
```

Add a shortcut to `~/.bashrc` on your dev machine:

```bash
function pi() {
    ssh -i ~/.ssh/id_rpi pi@raspberrypi.local
}
export -f pi
```

### Initial system update

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

### Set hostname (optional, avoids confusion with multiple Pis)

```bash
sudo raspi-config    # System Options → Hostname
# or
sudo hostnamectl set-hostname edge-monitor-01
```

## 3. Microphone Setup

Ex: UMIK-1

### Verify the mic is detected

Plug in the UMIK-1 and run:

```bash
arecord --list-devices
```

Expected output (card number may differ):

```
card 1: Device [miniDSP UMIK-1], device 0: USB Audio [USB Audio]
```

If it does not appear, check `dmesg` for USB errors:

```bash
dmesg | grep -i "usb\|audio" | tail -20
```

### Make the UMIK-1 the default capture device

Create or edit `/etc/asound.conf`:

```
defaults.pcm.card 1
defaults.ctl.card 1
```

Replace `1` with the actual card number from `arecord --list-devices`.

Verify with a 5-second test recording:

```bash
arecord -D hw:1,0 -f S16_LE -r 16000 -d 5 test.wav && aplay test.wav
```

### Download the calibration file

Download your UMIK-1 calibration file from [miniDSP's portal](https://www.minidsp.com/userdownloads) using the serial number printed on the mic's label. Copy it to the Pi:

```bash
scp 7175488.txt pi@raspberrypi.local:~/edge-monitor/src/umik-1/
```

Set the path in `security_policy.yaml`:

```yaml
hardware:
  calibration_file: "src/umik-1/7175488.txt"
```

## 4. Running as a Systemd Service

The installer script (`edge-monitor-install-service`) copies config files to `/etc/edge-monitor/` and installs the appropriate systemd units.

### Install (monolithic mode — one Pi does everything)

```bash
sudo edge-monitor-install-service \
    --config security_policy.yaml \
    --env .env \
    --calib src/umik-1/7175488.txt \
    --mode monolith
```

### Install (distributed mode — separate producer/consumer Pis)

On the **producer Pi** (mic + AI inference):

```bash
sudo edge-monitor-install-service \
    --config security_policy.yaml \
    --env .env \
    --mode distributed
```

On the **consumer Pi** (recording + cloud upload):

```bash
sudo edge-monitor-install-service \
    --config security_policy.yaml \
    --env .env \
    --mode distributed
```

### Manage the service

```bash
# Start / stop / restart
sudo systemctl start edge-monitor
sudo systemctl stop edge-monitor
sudo systemctl restart edge-monitor

# Check status
sudo systemctl status edge-monitor

# Enable / disable auto-start on boot
sudo systemctl enable edge-monitor
sudo systemctl disable edge-monitor

# View live logs
journalctl -fu edge-monitor

# View errors since last boot
journalctl -p err -b -u edge-monitor
```

### Service configuration notes

The service unit uses `CPUSchedulingPolicy=fifo` (real-time FIFO) at priority 99, which prevents audio buffer underruns. If the Pi is running other demanding services, lower the priority or switch to `CPUSchedulingPolicy=rr`.

To reload config without a full restart (e.g. after editing `security_policy.yaml`):

```bash
sudo systemctl restart edge-monitor
```

Config files installed at `/etc/edge-monitor/`:

```
/etc/edge-monitor/
  security_policy.yaml
  .env                    ← chmod 600 (secrets)
  7175488.txt             ← calibration file (if --calib was passed)
```

## 5. Monitoring & Logs

### Live log tail

```bash
journalctl -fu edge-monitor
```

### Filter by log level

```bash
# Errors only, current boot
journalctl -p err -b -u edge-monitor

# Last 100 lines
journalctl -u edge-monitor -n 100

# Since a specific time
journalctl -u edge-monitor --since "2026-05-13 10:00:00"
```

### System metrics heartbeat

The `SystemHeartbeatService` appends a row to `metrics_buffer.csv` every 60 seconds (configurable). Watch it grow:

```bash
tail -f recordings/metrics_buffer.csv
```

### Check upload queue depth without SSH

Send `/status` to the Telegram bot. The reply shows `Raw` and `Upload` queue depths, CPU, RAM, temperature, and disk usage — no SSH needed.

### Process health

```bash
htop                                   # interactive — find edge-monitor-run
ps aux | grep edge-monitor-run         # quick check
systemctl status edge-monitor          # shows PID, memory, CPU
```

## 6. Performance & Thermal

### Temperature monitoring

```bash
# One-shot
vcgencmd measure_temp

# Continuous (every 2 s)
watch vcgencmd measure_temp
```

Thermal throttling begins at **80°C** and hard-throttles at **85°C**. For 24/7 AI inference, aim to stay below 70°C with active cooling.

### CPU load

```bash
uptime                  # load averages (> 4 = overloaded on Pi 4B)
htop                    # per-core breakdown
```

### Stress test (validate cooling before deployment)

```bash
sudo apt install stress -y
stress --cpu 4 --timeout 60s &
watch vcgencmd measure_temp          # monitor in parallel
```

### GPU memory split

The AI pipeline runs entirely on the CPU. Free the GPU memory:

```bash
sudo raspi-config
# → Performance Options → GPU Memory → set to 16
```

### Swap (prevents OOM on 2 GB models)

```bash
free -h                              # check current swap
sudo dphys-swapfile swapoff
sudo nano /etc/dphys-swapfile        # set CONF_SWAPSIZE=512
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

> Swap on SD card is slow and wears it out. If using a USB SSD, move the swap file there.

## 7. Storage Management

### Mount a USB SSD for recordings

```bash
lsblk                                    # identify the SSD (e.g. /dev/sda)
sudo mkfs.ext4 /dev/sda1                 # format (skip if already formatted)
sudo mkdir -p /mnt/recordings
sudo mount /dev/sda1 /mnt/recordings
```

Add to `/etc/fstab` for auto-mount on boot:

```
UUID=<your-uuid>  /mnt/recordings  ext4  defaults,noatime  0  2
```

Get the UUID with `blkid /dev/sda1`. Then set in `security_policy.yaml`:

```yaml
services:
  recording_output_path: "/mnt/recordings"
```

### Check disk usage

```bash
df -lh                              # all filesystems (look for / and /mnt/recordings)
du -sh /mnt/recordings/             # total size of recordings directory
ls -lhS /mnt/recordings/ | head    # largest files first
```

### Transfer files to your laptop

```bash
# Single file
scp pi@raspberrypi.local:/mnt/recordings/evidence-*.wav ./

# All WAV files from today
scp "pi@raspberrypi.local:/mnt/recordings/$(date +%Y%m%d)_*.wav" ./

# Magic Wormhole (encrypted, no SSH key needed on target)
sudo apt install magic-wormhole -y
wormhole send evidence-12345.wav     # run on Pi, paste code on laptop
wormhole receive <code>              # run on laptop
```

### Convert recordings for WhatsApp before transferring

```bash
edge-monitor-convert /mnt/recordings/ --format ogg --out /mnt/recordings/whatsapp/
```

## 8. Networking & Remote Access

### Check connectivity

```bash
ip a                                    # show all interfaces and IP addresses
ip a show wlan0                         # Wi-Fi only
ping -c 3 api.telegram.org             # verify Telegram API is reachable
ss -tuln                                # show all listening ports
```

### Wi-Fi configuration

```bash
# Scan for networks
sudo iwlist wlan0 scan | grep ESSID

# Edit credentials
sudo nano /etc/wpa_supplicant/wpa_supplicant.conf

# Apply without reboot
sudo wpa_cli -i wlan0 reconfigure
```

### Static IP (recommended for headless deployment)

Edit `/etc/dhcpcd.conf`:

```
interface wlan0
static ip_address=192.168.1.100/24
static routers=192.168.1.1
static domain_name_servers=8.8.8.8
```

Then: `sudo systemctl restart dhcpcd`

### SSH key-based login (skip password every time)

```bash
# On your dev machine
ssh-keygen -t ed25519 -f ~/.ssh/id_rpi
ssh-copy-id -i ~/.ssh/id_rpi.pub pi@raspberrypi.local
```

## 9. Troubleshooting

### App won't start — check the service log first

```bash
journalctl -p err -b -u edge-monitor
```

Common causes:

| Symptom | Likely cause | Fix |
|---|---|---|
| `No such file: security_policy.yaml` | Config not in `/etc/edge-monitor/` | Re-run `edge-monitor-install-service` |
| `TelegramBotClient` credentials error | Missing `.env` or wrong token | Check `/etc/edge-monitor/.env` |
| `Failed to open audio device` | UMIK-1 not recognised | Check `arecord --list-devices`, replug mic |
| Service restarts in a loop | Python exception at startup | Check full log: `journalctl -u edge-monitor -n 200` |
| High memory use / OOM | 2 GB Pi running full TF | Use TFLite (`use_tflite: true`) or add swap |

### Mic not detected after reboot

USB audio devices occasionally lose their card number across reboots. Check with `arecord --list-devices` and update `/etc/asound.conf` if the card number changed.

To make the assignment permanent by USB path, create a udev rule:

```bash
# Find the USB vendor/product ID
lsusb | grep miniDSP
# Example output: Bus 001 Device 003: ID 2752:0011 miniDSP, Ltd UMIK-1

sudo nano /etc/udev/rules.d/99-umik.rules
```

Add:

```
SUBSYSTEM=="sound", ATTRS{idVendor}=="2752", ATTRS{idProduct}=="0011", ATTR{index}="0", SYMLINK+="sound/umik1"
```

### Pi is running slow

1. `uptime` — is load average > 4?
2. `htop` — which process is consuming CPU?
3. `watch vcgencmd measure_temp` — is temperature above 80°C? (thermal throttling)
4. `free -h` — is swap in use? (memory pressure)
5. `df -lh` — is the root partition full?
6. `dmesg | grep -i "i/o error"` — SD card corruption?

### Upload queue keeps growing

```
/status    ← Telegram command shows queue depth
```

If `Upload:` is non-zero and growing:
- Check internet: `ping api.telegram.org`
- Check cloud credentials in `/etc/edge-monitor/.env`
- Check uploader logs: `journalctl -u edge-monitor | grep "Upload\|cloud\|S3"`

## 10. Quick Command Reference

### System health

| Command | What it shows |
|---|---|
| `vcgencmd measure_temp` | CPU temperature |
| `watch vcgencmd measure_temp` | Temperature live (2 s refresh) |
| `uptime` | Load averages (> 4 = overloaded) |
| `htop` | Interactive process viewer |
| `free -h` | RAM and swap usage |
| `df -lh` | Disk usage, all filesystems |

### Logs

| Command | What it shows |
|---|---|
| `journalctl -fu edge-monitor` | Live service log |
| `journalctl -p err -b -u edge-monitor` | Errors since last boot |
| `journalctl -u edge-monitor -n 100` | Last 100 lines |
| `tail -f recordings/metrics_buffer.csv` | Heartbeat metrics live |

### Audio

| Command | What it shows |
|---|---|
| `arecord --list-devices` | Available capture (input) devices |
| `aplay --list-devices` | Available playback (output) devices |
| `dmesg \| grep -i usb` | USB device events (plug/unplug) |
| `arecord -D hw:1,0 -f S16_LE -r 16000 -d 5 test.wav` | 5-second test recording |

### Service management

| Command | Action |
|---|---|
| `sudo systemctl status edge-monitor` | Check if running |
| `sudo systemctl restart edge-monitor` | Restart (e.g. after config change) |
| `sudo systemctl enable edge-monitor` | Enable auto-start on boot |
| `sudo systemctl disable edge-monitor` | Disable auto-start |

### Package management

| Command | Action |
|---|---|
| `sudo apt update && sudo apt full-upgrade` | Update all packages |
| `sudo apt install <package>` | Install a package |
| `sudo apt autoremove --purge` | Remove unused packages |
| `sudo apt clean` | Clear downloaded package cache |
