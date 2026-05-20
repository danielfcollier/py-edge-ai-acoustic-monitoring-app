"""
Magalu/S3 bucket operations: download all objects or delete all objects.

Usage:
  python bucket_ops.py download --bucket <name> [--dest ./downloads]
  python bucket_ops.py clean    --bucket <name>
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _get_client(endpoint: str):
    import boto3

    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["MAGALU_KEY"],
        aws_secret_access_key=os.environ["MAGALU_SECRET"],
        endpoint_url=endpoint,
    )


def cmd_download(client, bucket: str, dest: Path):
    paginator = client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            local_path = dest / key
            local_path.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(local_path))
            print(f"  ⬇  {key}")
            count += 1
    print(f"✅ Downloaded {count} file(s) to {dest}/")


def cmd_clean(client, bucket: str):
    paginator = client.get_paginator("list_objects_v2")
    keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=bucket)
        for obj in page.get("Contents", [])
    ]
    if not keys:
        print("Bucket is already empty.")
        return
    for i in range(0, len(keys), 1000):
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]]},
        )
    print(f"✅ Deleted {len(keys)} object(s) from {bucket}.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Magalu/S3 bucket operations")
    parser.add_argument("command", choices=["download", "clean"])
    parser.add_argument("--bucket", default=os.environ.get("MAGALU_BUCKET", ""))
    parser.add_argument("--dest", default="downloads")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("MAGALU_URL", "https://br-se1.magaluobjects.com"),
    )
    args = parser.parse_args()

    if not args.bucket:
        print("Error: --bucket not set and MAGALU_BUCKET not in environment.")
        sys.exit(1)

    client = _get_client(args.endpoint)

    if args.command == "download":
        cmd_download(client, args.bucket, Path(args.dest))
    elif args.command == "clean":
        cmd_clean(client, args.bucket)


if __name__ == "__main__":
    main()
