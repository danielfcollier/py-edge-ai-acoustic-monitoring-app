# Cloud Storage

The app supports three cloud storage providers. Select the provider and set the region in `security_policy.yaml`:

```yaml
services:
  cloud:
    provider: "magalu"        # "magalu" | "aws" | "gcp"
    bucket_name: "acoustic-logs"
    region: "br-se1"          # see region tables below
```



## Magalu Object Storage (default)

S3-compatible object storage with data centers in Brazil.

### Regions

| `region` value | Location |
|---|---|
| `br-se1` | São Paulo (Southeast) — default |
| `br-ne1` | Fortaleza (Northeast) |

The endpoint URL is derived automatically: `https://<region>.magaluobjects.com`.
To use a custom endpoint (MinIO, staging), set `MAGALU_URL` in `.env` — it overrides the region.

### Credentials (`.env`)

```ini
MAGALU_KEY=your_access_key
MAGALU_SECRET=your_secret_key
```

### Example config

```yaml
services:
  cloud:
    provider: "magalu"
    bucket_name: "acoustic-logs"
    region: "br-ne1"
```



## AWS S3

### Regions (common values)

| `region` value | Location |
|---|---|
| `us-east-1` | N. Virginia |
| `us-west-2` | Oregon |
| `sa-east-1` | São Paulo |
| `eu-west-1` | Ireland |
| `ap-southeast-1` | Singapore |

### Credentials (`.env`)

boto3 reads the standard AWS environment variables automatically — no extra config needed:

```ini
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
```

### Example config

```yaml
services:
  cloud:
    provider: "aws"
    bucket_name: "acoustic-logs"
    region: "sa-east-1"
```



## GCP Cloud Storage

### Credentials

Set the path to your service account JSON in `.env`:

```ini
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
```

Or specify it directly in `security_policy.yaml` if the path varies per device:

```yaml
services:
  cloud:
    provider: "gcp"
    bucket_name: "acoustic-logs"
    region: "br-se1"                      # unused by GCP; kept for schema consistency
    gcp_credentials_path: "/etc/ai-acoustic-monitor/gcp-key.json"
```



## Upload Structure

```
<bucket>/
  recordings/
    evidence-<timestamp>-<uuid>.wav   ← audio evidence
  metrics/
    metrics-<YYYY-MM-DD>.csv          ← daily system metrics batch
```

Each WAV upload includes S3 object metadata: `label`, `confidence`, `calibrated`, `uuid`, `timestamp`.



## Offline Fallback

When the network is unavailable, `CloudUploaderService` saves WAV files locally under `recording_output_path` (default: `recordings/`) with the prefix `evidence-`. A background retry worker scans for these files every 60 seconds and re-uploads them once connectivity is restored. Successfully retried files are deleted locally.
