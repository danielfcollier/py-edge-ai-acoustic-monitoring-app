# Observability — Prometheus & Grafana

## Prometheus

When `prometheus_enabled: true` (default), the app exposes a metrics endpoint on the configured port.

```yaml
services:
  prometheus_enabled: true
  prometheus_port: 8000        # http://<device-ip>:8000/metrics
```

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: ai-acoustic-monitor
    static_configs:
      - targets: ["<device-ip>:8000"]
    scrape_interval: 5s
```

### Available metrics

| Metric | Description |
|---|---|
| `audio_rms` | RMS amplitude of the current audio frame |
| `audio_spectral_flux` | Spectral flux — spikes on sudden sound events |
| `audio_dbspl` | Sound level in dBSPL (calibrated mic only) |
| `ai_confidence` | Confidence of the top YAMNet classification |
| `audio_event_count_total{category}` | Cumulative event count per policy category |
| `system_cpu_usage` | CPU usage (%) |
| `system_temp_celsius` | SoC temperature in °C |
| `system_ram_usage` | RAM usage (%) |
| `system_disk_usage` | Disk usage (%) |

## Grafana

A ready-made dashboard is included at `docs/grafana/ai-acoustic-monitor-dashboard.json`.

Import it via **Dashboards → Import → Upload JSON file**.

Provisioning files for automated Grafana setup (datasource + dashboard) are in `docs/grafana/provisioning/`.
