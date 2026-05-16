"""
Shared fixtures for e2e tests.

Tests in this package hit live external services (Telegram API, S3, healthchecks.io).
They are excluded from the regular `make test` run and require real credentials in .env.

Run with:
    make test-e2e
    # or
    pytest tests/e2e -v
"""

import os

import pytest
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end tests that hit live external APIs (require credentials in .env)",
    )
    config.addinivalue_line(
        "markers",
        "heartbeat: heartbeat and health-monitoring e2e tests (skipped from the default test-e2e run)",
    )


@pytest.fixture(scope="session", autouse=True)
def _load_env():
    """Load .env once before any e2e test runs."""
    load_dotenv()


@pytest.fixture(scope="session")
def e2e_settings():
    """
    Return the real AppSettings loaded from .env + security_policy.yaml.
    Shared across the entire e2e session.
    """
    from app.settings import settings

    if not settings.CONFIG:
        settings.load_policy_file("security_policy.yaml")
    return settings


# ---------------------------------------------------------------------------
# Credential guards — tests that need a specific service call these fixtures.
# The fixture skips the test automatically when credentials are absent.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def require_telegram(e2e_settings):
    """Skip if Telegram credentials are missing."""
    if not e2e_settings.TELEGRAM_BOT_TOKEN or not e2e_settings.TELEGRAM_CHAT_ID:
        pytest.skip("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set in .env")
    return e2e_settings


@pytest.fixture(scope="session")
def require_magalu(e2e_settings):
    """Skip if Magalu credentials are missing (used by Magalu-specific tests)."""
    if not e2e_settings.MAGALU_ACCESS_KEY or not e2e_settings.MAGALU_SECRET_KEY:
        pytest.skip("MAGALU_KEY / MAGALU_SECRET not set in .env")
    return e2e_settings


@pytest.fixture(scope="session")
def require_cloud(e2e_settings):
    """Skip if credentials for the configured cloud provider are missing."""
    cfg = e2e_settings.CONFIG.services.cloud
    if cfg.provider == "magalu":
        if not e2e_settings.MAGALU_ACCESS_KEY or not e2e_settings.MAGALU_SECRET_KEY:
            pytest.skip("MAGALU_KEY / MAGALU_SECRET not set in .env")
    elif cfg.provider == "aws":
        if not os.environ.get("AWS_ACCESS_KEY_ID"):
            pytest.skip("AWS_ACCESS_KEY_ID not set in environment")
    elif cfg.provider == "gcp":
        creds = cfg.gcp_credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds:
            pytest.skip("GOOGLE_APPLICATION_CREDENTIALS not set")
    return e2e_settings


@pytest.fixture(scope="session")
def require_hc_url(e2e_settings):
    """Skip if healthchecks.io URL is not configured."""
    url = e2e_settings.CONFIG.services.hc_ping_url
    if not url:
        pytest.skip("hc_ping_url not set in security_policy.yaml")
    return url


# ---------------------------------------------------------------------------
# Cloud bucket helpers
# ---------------------------------------------------------------------------

def _s3_create_bucket(client, bucket_name: str, provider: str, region: str | None):
    """Create an S3-compatible bucket, handling AWS region constraints."""
    if provider == "aws" and region and region != "us-east-1":
        client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    else:
        client.create_bucket(Bucket=bucket_name)


def _s3_delete_bucket(client, bucket_name: str):
    """Empty and delete an S3-compatible bucket."""
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            objects = page.get("Contents", [])
            if objects:
                client.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
                )
        client.delete_bucket(Bucket=bucket_name)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bucket fixture — provider-agnostic
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cloud_test_bucket(require_cloud):
    """
    Create a dedicated test bucket ({bucket_name}-test), yield (settings, bucket_name),
    then empty and delete the bucket on teardown.

    Supports magalu, aws, and gcp based on the configured provider.
    """
    from app.services.cloud_storage_providers import S3Provider

    cfg = require_cloud.CONFIG.services.cloud
    test_bucket = f"{cfg.bucket_name}-test"

    if cfg.provider in ("magalu", "aws"):
        if cfg.provider == "magalu":
            endpoint = require_cloud.MAGALU_URL or f"https://{cfg.region}.magaluobjects.com"
            client = S3Provider(
                access_key=require_cloud.MAGALU_ACCESS_KEY,
                secret_key=require_cloud.MAGALU_SECRET_KEY,
                bucket_name=test_bucket,
                endpoint_url=endpoint,
            ).client
        else:
            client = S3Provider(
                access_key=None,
                secret_key=None,
                bucket_name=test_bucket,
                region=cfg.region,
            ).client

        _s3_create_bucket(client, test_bucket, cfg.provider, cfg.region)
        yield require_cloud, test_bucket
        _s3_delete_bucket(client, test_bucket)

    elif cfg.provider == "gcp":
        from google.cloud import storage

        creds = cfg.gcp_credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        gcs_client = storage.Client.from_service_account_json(creds)
        bucket = gcs_client.create_bucket(test_bucket)
        yield require_cloud, test_bucket
        try:
            bucket.delete(force=True)
        except Exception:
            pass
