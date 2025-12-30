# Raspberry Pi: The Complete Command-Line Guide

This document organizes common Raspberry Pi commands by function, provides essential troubleshooting tools, and includes scripts for remote access and hardware control.


htop
sox

## 1. System Reference & Troubleshooting

### 1.1 Commands by Function

Here are common commands grouped into logical categories with explanations.

#### Networking

These commands are used to scan, configure, and inspect your Wi-Fi and network interfaces.

| Command | Description |
|---|---|
| `sudo iwlist wlan0 scan` | Performs a detailed scan of all visible Wi-Fi networks on the `wlan0` interface, showing signal strength, encryption, etc. |
| `sudo iwlist wlan0 scan \| grep ESSID` | A more practical version of the above. It scans for networks but filters the output to show only their names (ESSIDs). |
| `sudo nano /etc/wpa_supplicant/wpa_supplicant.conf` | Opens the main configuration file for Wi-Fi connections in a text editor. This is where you manually add or edit network credentials. |
| `sudo wpa_cli -i wlan0 reconfigure` | Forces the `wpa_supplicant` service (which manages Wi-Fi) to reload its configuration from the file you just edited. Use this after changing `wpa_supplicant.conf` to apply changes without rebooting. |
| `ip a` or `ip a show wlan0` | The modern command to show the IP addresses and status of all network interfaces (`ip a`) or just the `wlan0` interface. This is how you check if you have successfully connected and received an IP address. |

#### System Performance & Stress Testing

Used to test the stability and thermal performance of your Raspberry Pi's CPU.

| Command | Description |
|---|---|
| `sudo apt install stress` | Installs the `stress` utility, a simple tool to put a high, predictable load on system components. |
| `stress --cpu 4 --timeout 60s` | Puts a 100% load on all 4 CPU cores for 60 seconds. This is excellent for testing cooling solutions and checking for thermal throttling. |
| `vcgencmd measure_temp` | A Raspberry Pi-specific command that reports the current temperature of the CPU/GPU core. **Crucial to run this while `stress` is active.** |

#### Hardware Information & Audio

Commands to query the system's hardware, especially audio devices.

| Command | Description |
|---|---|
| `aplay --list-devices` | Lists all available **playback** (output) audio devices, like HDMI audio or the 3.5mm jack. |
| `arecord --list-devices` | Lists all available **capture** (input) audio devices, such as USB microphones (like your UMIK-1). This is essential for getting the correct card number for ALSA configuration. |

#### System Configuration

The primary tool for system-wide Raspberry Pi settings.

| Command | Description |
|---|---|
| `sudo raspi-config` | Opens the main Raspberry Pi configuration tool. This text-based interface is used for essential tasks like setting the hostname, enabling SSH/VNC, configuring interfaces (camera, I2C), and setting the locale. |

#### Package Management & System Updates

Commands for installing, removing, and updating software.

| Command | Description |
|---|---|
| `sudo apt install magic-wormhole` | Installs a specific package (`magic-wormhole`, a tool for securely sending files). |
| `sudo apt full-upgrade` | Upgrades all installed packages to their latest versions, including kernel and system-level changes. It's more thorough than `apt upgrade`. |
| `sudo apt purge <package-name>` | This command does everything remove does, but it also deletes the system-wide configuration files. This is a complete, clean uninstallation. |
| `sudo apt autoremove --purge` | Removes packages that were installed as dependencies but are no longer needed by any installed software. Good for freeing up space. |
| `sudo apt clean` | Clears the local cache of downloaded package files (`.deb` files). This can free up a significant amount of disk space. |

#### File & System Operations

General-purpose commands for managing files and the system state.

| Command | Description |
|---|---|
| `tar -xvf <file>.tar.gz` | Extracts (`-x`) the contents of a gzipped tar archive (`.tar.gz`) verbosely (`-v`), from the specified file (`-f`). |
| `df -lh` | **D**isk **F**ree. Shows the usage of all mounted filesystems in a **l**ocal, **h**uman-readable format (e.g., MB, GB). Essential for checking if your SD card is full. |
| `sudo reboot` | Restarts the system immediately. |

### 1.2 Additional Essential Troubleshooting Commands

Here are other commands that are invaluable for diagnosing problems on your Raspberry Pi.

#### System Health & Performance

| Command | Description |
|---|---|
| `htop` | An interactive, real-time process viewer. It's a massive upgrade over the standard `top` command, showing CPU usage per core, memory usage, and a sortable process list in a user-friendly way. **If your Pi is slow, this is the first command to run.** |
| `uptime` | Quickly shows how long the system has been running, how many users are logged in, and the system load averages for the last 1, 5, and 15 minutes. A load average higher than the number of CPU cores (4 on a Pi 4) indicates the system is overloaded. |
| `free -h` | Shows the total, used, and free system memory (RAM) and swap space in a **h**uman-readable format. Use this to check if your system is running out of memory. |
| `watch vcgencmd measure_temp` | Runs the `vcgencmd measure_temp` command every 2 seconds, allowing you to monitor the CPU temperature in real-time. Combine this with a `stress` test to see how effective your cooling is. |

#### Log Inspection

When something goes wrong, the logs are the first place to look for evidence.

| Command | Description |
|---|---|
| `dmesg` or `dmesg -T` | Prints the kernel ring buffer messages. This is the best place to look for hardware-related errors, especially issues with USB devices (like your microphone) during boot-up or connection. The `-T` flag makes timestamps human-readable. |
| `journalctl -f` | **F**ollows the main system log in real-time. This is useful for watching what's happening "live" as you perform an action. |
| `journalctl -p err -b` | Shows all log messages with a priority of **err**or or higher from the current **b**oot. This is the fastest way to find critical errors that have occurred since the last reboot. |

#### Networking

| Command | Description |
|---|---|
| `ping <hostname or IP>` | Sends a small packet to a a target host to see if it's reachable and measures the round-trip time. The fundamental tool for checking network connectivity. (e.g., `ping google.com`). |
| `ss -tuln` | A modern replacement for `netstat`. It shows all listening **t**cp, **u**dp, and **n**umeric network sockets. Use this to check if your application (like the Prometheus exporter on port 8000) is successfully listening for connections. |

### 1.3 Sample Troubleshooting Workflow: "My Pi is running slow!"

Here's how you can combine these commands to diagnose a common problem.

1.  **Check the Load:**
    * Run `uptime`. Is the load average high?
    * Run `htop`. What process is at the top of the list, consuming all the CPU?

2.  **Check for Thermal Throttling:**
    * While `htop` is running, open another terminal and run `watch vcgencmd measure_temp`. Is the temperature consistently above 80°C? If so, the CPU is likely slowing itself down to prevent overheating.

3.  **Check for Memory Issues:**
    * In `htop` or with `free -h`, check the "Swp" (swap) line. Is a significant amount of swap being used? If so, your system is out of physical RAM and is thrashing, which dramatically slows everything down. Use `htop` to see which process is using the most memory.

4.  **Check the SD Card:**
    * Run `df -lh`. Is the root partition (`/`) nearly full? A full disk can cause all sorts of performance problems.
    * Run `dmesg \| grep -i "i/o error"`. Any I/O errors indicate a failing or corrupted SD card, which is a common cause of poor performance.

By following a logical sequence like this, you can quickly narrow down the source of most common Raspberry Pi issues using just a few powerful commands.

## 2. Remote Access via SSH

Connecting to your Raspberry Pi securely and efficiently is the first step for any headless project.

#### The `raspberrypi` `.bashrc` Function

The following function is a convenient alias that simplifies the SSH connection process.

```bash
function raspberrypi() {
    # This function uses ssh with a specific identity file (-i) to connect.
    # Using .local relies on mDNS (Bonjour/Avahi) for name resolution.
    ssh -i /home/<user>/<private.key> "<user>@raspberrypi.local"
}

# This command makes the function available in your current shell and sub-shells.
export -f raspberrypi
```
