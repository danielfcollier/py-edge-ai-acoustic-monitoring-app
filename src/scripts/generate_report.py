"""
Analytics Report Generator.
Fetches metrics from Cloud Storage, analyzes acoustic data,
and generates PDF reports with charts.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import io
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure 'app' module can be imported regardless of run location
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:  # noqa: E402
    sys.path.append(str(PROJECT_ROOT))

import boto3  # noqa: E402
import markdown  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
from matplotlib.dates import DateFormatter  # noqa: E402
from tabulate import tabulate  # noqa: E402
from weasyprint import CSS, HTML  # noqa: E402

from app.settings import settings  # noqa: E402
from scripts.reporting import process_data  # noqa: E402

# --- CONFIGURATION ---
REPORT_DIR = Path("reports")
ASSETS_DIR = REPORT_DIR / "assets"
REPORT_DIR.mkdir(exist_ok=True)
ASSETS_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Analytics")


def fetch_data_from_s3(days):
    """Downloads metrics CSVs from S3."""
    # Note: This currently assumes Magalu/S3 config.
    # To support GCP/Multi-cloud here, you'd replicate the Provider logic
    # from CloudUploaderService, but for now direct boto3 is fine for analysis.

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.MAGALU_ACCESS_KEY,
        aws_secret_access_key=settings.MAGALU_SECRET_KEY,
        endpoint_url=f"https://br-ne1.magaluobjects.com/{settings.MAGALU_BUCKET}"
        if settings.CONFIG.services.cloud.provider == "magalu"
        else None,
    )
    bucket = settings.MAGALU_BUCKET
    prefix = "metrics/"

    cutoff_date = datetime.now() - timedelta(days=days)
    logger.info(f"📥 Fetching data since {cutoff_date.date()}...")

    dfs = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if "Contents" not in page:
                continue
            for obj in page["Contents"]:
                if obj["LastModified"].replace(tzinfo=None) < cutoff_date:
                    continue
                if not obj["Key"].endswith(".csv"):
                    continue

                try:
                    response = s3.get_object(Bucket=bucket, Key=obj["Key"])
                    df = pd.read_csv(io.BytesIO(response["Body"].read()))
                    dfs.append(df)
                except Exception as e:
                    logger.warning(f"Skipping {obj['Key']}: {e}")
    except Exception as e:
        logger.error(f"S3 Error: {e}")
        return pd.DataFrame()

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def generate_charts(df):
    chart_paths = {}
    sns.set_theme(style="whitegrid", context="paper")

    # 1. Timeline Chart
    plt.figure(figsize=(10, 5))
    sns.scatterplot(data=df, x=df.index, y="dbspl", hue="Category", alpha=0.7)

    limits = settings.CONFIG.reporting.limits
    plt.axhline(limits.day_db, color="orange", ls="--", label=f"Day Limit ({limits.day_db}dB)")
    plt.axhline(limits.night_db, color="red", ls="--", label=f"Night Limit ({limits.night_db}dB)")

    plt.gca().xaxis.set_major_formatter(DateFormatter("%d/%m %Hh"))
    plt.title("Noise Events vs. Regulatory Limits")
    plt.ylabel("dBSPL")
    plt.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.tight_layout()

    chart_paths["timeline"] = ASSETS_DIR / "timeline.png"
    plt.savefig(chart_paths["timeline"])
    plt.close()

    # 2. System Metrics Chart
    if "cpu_percent" in df.columns:
        sys_df = df[["cpu_percent", "temp_c"]].resample("1h").mean()
        fig, ax1 = plt.subplots(figsize=(10, 4))

        ax1.set_xlabel("Time")
        ax1.set_ylabel("CPU (%)", color="tab:blue")
        ax1.plot(sys_df.index, sys_df["cpu_percent"], color="tab:blue")

        ax2 = ax1.twinx()
        ax2.set_ylabel("Temp (°C)", color="tab:red")
        ax2.plot(sys_df.index, sys_df["temp_c"], color="tab:red", ls="--")

        plt.title("System Health")
        plt.tight_layout()
        chart_paths["system"] = ASSETS_DIR / "system.png"
        plt.savefig(chart_paths["system"])
        plt.close()

    return chart_paths


def create_markdown_report(df, charts):
    config = settings.CONFIG.reporting
    total = len(df)
    violations = df["violation"].sum()

    cat_counts = df["Category"].value_counts().reset_index()
    cat_counts.columns = ["Source", "Count"]
    table = tabulate(cat_counts, headers="keys", tablefmt="pipe", showindex=False)

    md = f"""
# 📊 Acoustic Environment Audit
**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Period:** Last {config.days_to_report} Days

## 1. Compliance Summary
| Metric | Value |
| :--- | :--- |
| **Total Events** | {total} |
| **Violations** | {violations} ({(violations / total) * 100:.1f}%) |
| **Max Peak** | {df["dbspl"].max():.1f} dB |
| **Day Limit** | {config.limits.day_db} dB |
| **Night Limit** | {config.limits.night_db} dB |

## 2. Noise Timeline
![Timeline]({charts["timeline"].resolve().as_uri()})

## 3. Sources of Noise
Based on AI Classification (YAMNet + Custom Models).

{table}

## 4. System Telemetry
![System]({charts.get("system", Path("no_data")).resolve().as_uri()})
"""
    return md


def save_report(md_content):
    md_path = REPORT_DIR / "report.md"
    pdf_path = REPORT_DIR / "report.pdf"

    with open(md_path, "w") as f:
        f.write(md_content)

    html = markdown.markdown(md_content, extensions=["tables"])
    css = CSS(
        string="""
        body { font-family: sans-serif; padding: 20px; font-size: 12px; }
        h1 { border-bottom: 2px solid #333; }
        img { max-width: 100%; margin: 10px 0; border: 1px solid #ddd; }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th, td { border: 1px solid #ccc; padding: 6px; text-align: left; }
        th { background: #eee; }
    """
    )
    HTML(string=html, base_url=str(REPORT_DIR)).write_pdf(pdf_path, stylesheets=[css])
    logger.info(f"✅ Generated: {pdf_path}")


if __name__ == "__main__":
    days = settings.CONFIG.reporting.days_to_report
    df = fetch_data_from_s3(days)
    if not df.empty:
        df = process_data(df, settings.CONFIG.reporting)
        charts = generate_charts(df)
        md = create_markdown_report(df, charts)
        save_report(md)
    else:
        logger.warning("No data found.")
