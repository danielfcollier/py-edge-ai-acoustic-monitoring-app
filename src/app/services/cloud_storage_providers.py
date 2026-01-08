"""
Storage Providers.
Abstracts the differences between S3 (AWS/Magalu) and Google Cloud Storage.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class StorageProvider(Protocol):
    def upload(self, file_path: Path, object_name: str) -> bool: ...


class S3Provider:
    """Handles AWS S3, Magalu Cloud, MinIO, etc."""

    def __init__(self, access_key, secret_key, bucket_name, region=None, endpoint_url=None):
        import boto3

        self.bucket = bucket_name
        self.client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            endpoint_url=endpoint_url,
        )

    def upload(self, file_path: Path, object_name: str) -> bool:
        try:
            self.client.upload_file(str(file_path), self.bucket, object_name)
            return True
        except Exception as e:
            logger.error(f"❌ S3 Upload Error: {e}")
            return False


class GCPStorageProvider:
    """Handles Google Cloud Storage."""

    def __init__(self, credentials_path, bucket_name):
        from google.cloud import storage

        self.client = storage.Client.from_service_account_json(credentials_path)
        self.bucket = self.client.bucket(bucket_name)

    def upload(self, file_path: Path, object_name: str) -> bool:
        try:
            blob = self.bucket.blob(object_name)
            blob.upload_from_filename(str(file_path))
            return True
        except Exception as e:
            logger.error(f"❌ GCS Upload Error: {e}")
            return False
