"""
Offline MFCC scoring for a directory of WAV recordings.

Scores each WAV file against a trained label profile and writes results to CSV.
Useful for retrospective analysis, threshold tuning, and auditing.

Usage:
    ai-acoustic-monitor-score-recordings \\
        [--recordings DIR] [--labels FILE] \\
        [--target-label LABEL] [--threshold FLOAT] \\
        [--method nearest|centroid] \\
        [--since Nd] [--output FILE]
"""

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .mfcc_core import (
    _DEFAULT_LABELS_FILE,
    MfccScorer,
    resolve_labels_path,
)


def _parse_since(value: str) -> datetime | None:
    """Parse a '7d' / '30d' style age filter to an absolute cutoff datetime."""
    if not value:
        return None
    value = value.strip().lower()
    if value.endswith("d"):
        try:
            days = int(value[:-1])
            return datetime.now() - timedelta(days=days)
        except ValueError:
            pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score WAV recordings against an MFCC label profile.",
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
        help="Label to score against (e.g. 'neighbor-dog')",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Cosine similarity threshold for a positive match",
    )
    parser.add_argument(
        "--method",
        default="nearest",
        choices=["nearest", "centroid"],
        help="Scoring method",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="Nd",
        help="Only score files modified within the last N days (e.g. '7d')",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: <recordings>/mfcc_scores_offline.csv)",
    )
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    labels_path = resolve_labels_path(args.labels, recordings_dir)
    scorer = MfccScorer(labels_path, args.target_label, method=args.method)
    if not scorer.ready:
        print(
            f"Error: no '{args.target_label}' examples in {labels_path}. Run ai-acoustic-monitor-label-profile first.",
            file=sys.stderr,
        )
        sys.exit(1)

    cutoff = _parse_since(args.since)

    wav_files = sorted(recordings_dir.rglob("*.wav"))
    if cutoff:
        wav_files = [w for w in wav_files if datetime.fromtimestamp(w.stat().st_mtime) >= cutoff]

    if not wav_files:
        print("No WAV files found matching the criteria.")
        return

    output_path = Path(args.output) if args.output else recordings_dir / "mfcc_scores_offline.csv"

    matched = 0
    failed = 0

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["filename", "mfcc_label", "mfcc_score", "threshold", "matched"])

        print(f"Scoring {len(wav_files)} file(s) against '{args.target_label}' (threshold={args.threshold})…")
        for wav in wav_files:
            score = scorer.score_file(wav)
            if score is None:
                failed += 1
                continue
            is_match = score >= args.threshold
            if is_match:
                matched += 1
            writer.writerow([wav.name, args.target_label, round(score, 4), args.threshold, is_match])

    total = len(wav_files) - failed
    print(f"Done. {total} scored, {matched} matched, {failed} unreadable.")
    print(f"Results written to: {output_path}")


if __name__ == "__main__":
    main()
