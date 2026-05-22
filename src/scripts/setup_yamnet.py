"""
Utility to download YAMNet models (Full SavedModel + ONNX) and Class Maps.
Sets up the complete asset directory for both development and edge inference.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
import sys
import tarfile
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("setup_models")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_SRC = SCRIPT_DIR.parent
BASE_DIR = PROJECT_SRC / "yamnet"

URLS = {
    "class_map": "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv",
    "full_model": "https://tfhub.dev/google/yamnet/1?tf-hub-format=compressed",
    "onnx_model": "https://github.com/danielfcollier/py-edge-ai-acoustic-monitoring-app/releases/latest/download/yamnet.onnx",
}


def download_file(url: str, dest: Path):
    if dest.exists():
        logger.info(f"✅ {dest.name} already exists. Skipping.")
        return

    logger.info(f"⬇️ Downloading {dest.name}...")
    try:
        with httpx.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
        logger.info(f"✨ Saved to {dest}")
    except Exception as e:
        logger.error(f"❌ Failed to download {dest.name}: {e}")
        sys.exit(1)


def extract_tar(tar_path: Path, extract_to: Path):
    logger.info(f"📦 Extracting {tar_path.name} to {extract_to}...")
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            tar.extractall(path=extract_to)
        logger.info("✅ Extraction complete.")
    except Exception as e:
        logger.error(f"❌ Failed to extract archive: {e}")
        sys.exit(1)


def main():
    # src/yamnet/
    # ├── class_map/
    # ├── model/       (Full SavedModel — optional, for development)
    # └── yamnet.onnx  (edge inference)

    class_map_dir = BASE_DIR / "class_map"
    full_model_dir = BASE_DIR / "model"

    for d in [class_map_dir, full_model_dir]:
        d.mkdir(parents=True, exist_ok=True)

    csv_dest = class_map_dir / "yamnet_class_map.csv"
    download_file(URLS["class_map"], csv_dest)

    onnx_dest = BASE_DIR / "yamnet.onnx"
    download_file(URLS["onnx_model"], onnx_dest)

    if not (full_model_dir / "saved_model.pb").exists():
        tar_dest = BASE_DIR / "yamnet_full.tar.gz"
        download_file(URLS["full_model"], tar_dest)
        extract_tar(tar_dest, full_model_dir)
        tar_dest.unlink(missing_ok=True)
    else:
        logger.info("✅ Full Model already extracted.")

    logger.info("\n--- Setup Complete ---")
    logger.info(f"📂 Assets located in: {BASE_DIR.resolve()}")


if __name__ == "__main__":
    main()
