"""
Export a trained MFCC profile to a portable .npz file for edge deployment.

Reads labeled WAV files from the recordings directory, computes MFCC vectors
for all target-class examples, and saves them to a compact binary file.
The exported file can be copied to the Raspberry Pi without the training WAVs.

Usage:
    ai-acoustic-monitor-export-profile \\
        [--recordings DIR] [--labels FILE] \\
        [--target-label LABEL] [--output FILE]
"""

import argparse
import sys
from pathlib import Path

from .mfcc_core import (
    _DEFAULT_LABELS_FILE,
    build_target_vectors,
    load_labels,
    resolve_labels_path,
    save_profile,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a trained MFCC profile to a portable .npz file for deployment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--recordings", default="recordings", help="Recordings directory")
    parser.add_argument(
        "--labels",
        default=None,
        help=f"Labels JSON file (default: <recordings>/{_DEFAULT_LABELS_FILE})",
    )
    parser.add_argument(
        "--target-label",
        default="target",
        help="Label string for the target class (e.g. 'neighbor-dog')",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output .npz file path (default: <recordings>/<target-label>.mfcc.npz)",
    )
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    labels_path = resolve_labels_path(args.labels, recordings_dir)
    labels = load_labels(labels_path, recordings_dir)

    target_label = args.target_label
    target_count = sum(1 for v in labels.values() if v == target_label)

    if target_count == 0:
        print(f"Error: no '{target_label}' examples found in {labels_path}.", file=sys.stderr)
        print("Run ai-acoustic-monitor-label-profile first.", file=sys.stderr)
        sys.exit(1)

    print(f"Computing MFCC vectors for {target_count} '{target_label}' example(s)…")
    vectors = build_target_vectors(labels, target_label, recordings_dir)

    if not vectors:
        print("Error: could not read any target WAV files.", file=sys.stderr)
        sys.exit(1)

    if len(vectors) < target_count:
        print(f"  ⚠️  {target_count - len(vectors)} file(s) could not be read and were skipped.")

    output_path = Path(args.output) if args.output else recordings_dir / f"{target_label}.mfcc.npz"
    save_profile(output_path, vectors)

    size_kb = output_path.stat().st_size / 1024
    print(f"✅ Exported {len(vectors)} vector(s) → {output_path}  ({size_kb:.1f} KB)")
    print("\nCopy to the Pi and add to security_policy.yaml:")
    print("  mfcc_profiles:")
    print(f'    - profile_file: "{output_path.name}"')
    print(f'      target_label: "{target_label}"')
    print("      threshold: 0.850  # replace with the value from validate_profile")


if __name__ == "__main__":
    main()
