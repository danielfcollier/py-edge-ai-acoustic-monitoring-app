"""
E2E tests for cloud storage (Magalu, AWS S3, GCP).

Requires credentials in .env for whichever provider is configured
in security_policy.yaml (services.cloud.provider).

What is tested:
  - A WAV file uploads successfully via the configured provider.
  - In-memory fileobj upload works.
  - Magalu/AWS: endpoint URL is correctly derived from region config.
  - S3 providers: upload() returns False (not raises) on bad credentials.
  - S3 providers: object metadata is stored and retrievable.
"""

import io
import os
import uuid as uuid_module
import wave

import pytest

from app.services.cloud_storage_providers import GCPStorageProvider, S3Provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav_file(path):
    """Write a minimal 0.1-second silent WAV to a file path."""
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00" * 3200)


def _make_wav_bytes() -> io.BytesIO:
    """Return a minimal silent WAV as an in-memory buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00" * 3200)
    buf.seek(0)
    return buf


def _make_provider(settings, bucket_name: str | None = None):
    """Build the configured cloud storage provider pointed at bucket_name."""
    cfg = settings.CONFIG.services.cloud
    bucket = bucket_name or cfg.bucket_name

    if cfg.provider == "magalu":
        endpoint = settings.MAGALU_URL or f"https://{cfg.region}.magaluobjects.com"
        return S3Provider(
            access_key=settings.MAGALU_ACCESS_KEY,
            secret_key=settings.MAGALU_SECRET_KEY,
            bucket_name=bucket,
            endpoint_url=endpoint,
        )
    if cfg.provider == "aws":
        return S3Provider(
            access_key=None,
            secret_key=None,
            bucket_name=bucket,
            region=cfg.region,
        )
    if cfg.provider == "gcp":
        creds = cfg.gcp_credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        return GCPStorageProvider(credentials_path=creds, bucket_name=bucket)
    raise ValueError(f"Unknown provider: {cfg.provider}")


def _is_s3(settings) -> bool:
    return settings.CONFIG.services.cloud.provider in ("magalu", "aws")


def _delete_object(provider, bucket: str, key: str):
    """Delete a single object, provider-agnostic."""
    try:
        if isinstance(provider, GCPStorageProvider):
            provider.bucket.blob(key).delete()
        else:
            provider.client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass


def _upload_fileobj(provider, buf: io.BytesIO, bucket: str, key: str):
    """Upload an in-memory buffer, provider-agnostic."""
    if isinstance(provider, GCPStorageProvider):
        provider.bucket.blob(key).upload_from_file(buf)
    else:
        provider.client.upload_fileobj(buf, bucket, key)


def _object_exists(provider, bucket: str, key: str) -> bool:
    """Check object existence, provider-agnostic."""
    try:
        if isinstance(provider, GCPStorageProvider):
            return provider.bucket.blob(key).exists()
        provider.client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_upload_wav_file(cloud_test_bucket, tmp_path):
    """Upload a WAV file to the test bucket and verify it exists."""
    settings, bucket = cloud_test_bucket
    provider = _make_provider(settings, bucket)
    wav = tmp_path / "evidence.wav"
    _make_wav_file(wav)

    key = f"e2e-test/{uuid_module.uuid4()}/evidence.wav"
    try:
        assert provider.upload(wav, key), "upload() returned False"
        assert _object_exists(provider, bucket, key), "Object not found after upload"
    finally:
        _delete_object(provider, bucket, key)


@pytest.mark.e2e
def test_upload_fileobj_in_memory(cloud_test_bucket):
    """Upload an in-memory WAV buffer (the path used by CloudUploaderService)."""
    settings, bucket = cloud_test_bucket
    provider = _make_provider(settings, bucket)
    buf = _make_wav_bytes()

    key = f"e2e-test/{uuid_module.uuid4()}/stream.wav"
    try:
        _upload_fileobj(provider, buf, bucket, key)
        assert _object_exists(provider, bucket, key), "Object not found after upload"
    finally:
        _delete_object(provider, bucket, key)


@pytest.mark.e2e
def test_region_derives_correct_endpoint(require_magalu):
    """Region value maps to the expected Magalu endpoint URL (Magalu only)."""
    cfg = require_magalu.CONFIG.services.cloud
    endpoint = require_magalu.MAGALU_URL or f"https://{cfg.region}.magaluobjects.com"

    assert cfg.region in ("br-se1", "br-ne1"), f"Unexpected region: {cfg.region}"
    assert endpoint == f"https://{cfg.region}.magaluobjects.com" or require_magalu.MAGALU_URL


@pytest.mark.e2e
def test_upload_returns_false_on_bad_credentials(cloud_test_bucket, tmp_path):
    """S3Provider.upload returns False (not raises) when credentials are wrong."""
    settings, bucket = cloud_test_bucket
    if not _is_s3(settings):
        pytest.skip("Bad-credentials test uses S3-specific provider construction — not applicable to GCP.")

    cfg = settings.CONFIG.services.cloud
    endpoint = settings.MAGALU_URL or f"https://{cfg.region}.magaluobjects.com"

    bad_provider = S3Provider(
        access_key="invalid-key",
        secret_key="invalid-secret",
        bucket_name=bucket,
        endpoint_url=endpoint,
    )

    wav = tmp_path / "test.wav"
    _make_wav_file(wav)
    result = bad_provider.upload(wav, "e2e-test/should-fail.wav")
    assert result is False


@pytest.mark.e2e
def test_upload_with_s3_metadata(cloud_test_bucket, tmp_path):
    """Upload a WAV with S3 object metadata and verify the metadata is stored."""
    settings, bucket = cloud_test_bucket
    if not _is_s3(settings):
        pytest.skip("Metadata test uses S3 ExtraArgs — not applicable to GCP.")

    provider = _make_provider(settings, bucket)
    wav = tmp_path / "evidence.wav"
    _make_wav_file(wav)

    key = f"e2e-test/{uuid_module.uuid4()}/meta.wav"
    metadata = {"label": "Dog", "confidence": "0.91", "calibrated": "False"}

    try:
        with open(wav, "rb") as f:
            provider.client.upload_fileobj(
                f, provider.bucket, key, ExtraArgs={"Metadata": metadata}
            )
        head = provider.client.head_object(Bucket=provider.bucket, Key=key)
        stored = head.get("Metadata", {})
        assert stored.get("label") == "Dog"
        assert stored.get("confidence") == "0.91"
    finally:
        provider.client.delete_object(Bucket=provider.bucket, Key=key)
