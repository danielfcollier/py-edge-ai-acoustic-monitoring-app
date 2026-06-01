"""
Interactive labeling tool for MFCC-based audio profile recognition.

Browse unlabeled WAV files in the recordings directory and mark each as the
target class, a counter-example, or mixed.  Labels are stored in a JSON file
inside the recordings directory and consumed by review_profile and
validate_profile.

This tool is class-agnostic.  Common use cases:

  * Individual dog identification   --target-label neighbor-dog
  * Speaker identification          --target-label known-speaker
  * Specific alarm model            --target-label smoke-alarm-x
  * Chainsaw vs other machinery     --target-label chainsaw

Usage:
    ai-acoustic-monitor-label-profile [--recordings DIR] [--labels FILE]
                                       [--target-label LABEL] [--other-label LABEL]
                                       [--play]
"""

import argparse
import sys
from pathlib import Path

import librosa

from .mfcc_core import (
    _DEFAULT_LABELS_FILE,
    LABEL_MIXED,
    load_labels as _load_labels,
    resolve_labels_path as _resolve_labels_path,
    save_labels as _save_labels,
)


def _play(wav_path: Path) -> None:
    try:
        import sounddevice as sd

        audio, sr = librosa.load(str(wav_path), sr=None, mono=True)
        sd.play(audio, sr)
        input("  ↵ Enter to stop playback…")
        sd.stop()
    except Exception as e:
        print(f"  [playback error: {e}]")


def _prompt(
    wav_path: Path,
    idx: int,
    total: int,
    do_play: bool,
    label_target: str,
    label_other: str,
    current_label: str | None = None,
) -> str | None:
    """Return label_target, label_other, LABEL_MIXED, or None (skip). Raises KeyboardInterrupt on quit."""
    tag = f"  [labeled: {current_label}]" if current_label else ""
    print(f"\n[{idx}/{total}] {wav_path.name}{tag}")
    if do_play:
        _play(wav_path)
    while True:
        ch = (
            input(f"  [y] {label_target}  [n] {label_other}  [m] {LABEL_MIXED}  [s] skip  [r] replay  [q] quit: ")
            .strip()
            .lower()
        )
        if ch == "y":
            return label_target
        if ch == "n":
            return label_other
        if ch == "m":
            return LABEL_MIXED
        if ch == "s":
            return None
        if ch == "q":
            raise KeyboardInterrupt
        if ch == "r":
            if do_play:
                _play(wav_path)
            else:
                print("  (--play not set, cannot replay)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Label WAV files for MFCC-based audio profile recognition.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--recordings", default="recordings", help="Recordings directory")
    parser.add_argument(
        "--labels", default=None, help=f"Labels JSON file (default: <recordings>/{_DEFAULT_LABELS_FILE})"
    )
    parser.add_argument(
        "--target-label",
        default="target",
        help="Label string for the target class (e.g. 'neighbor-dog', 'known-speaker')",
    )
    parser.add_argument("--other-label", default="other", help="Label string for the counter-example class")
    parser.add_argument("--play", action="store_true", help="Play each WAV before prompting")
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    label_target: str = args.target_label
    label_other: str = args.other_label

    labels_path = _resolve_labels_path(args.labels, recordings_dir)
    labels = _load_labels(labels_path, recordings_dir)

    all_wavs = sorted(recordings_dir.rglob("*.wav"))
    unlabeled = [w for w in all_wavs if str(w.relative_to(recordings_dir)) not in labels]

    target_count = sum(1 for v in labels.values() if v == label_target)
    mixed_count = sum(1 for v in labels.values() if v == LABEL_MIXED)
    other_count = sum(1 for v in labels.values() if v == label_other)
    print(f"Recordings: {recordings_dir}")
    print(f"Total WAV files: {len(all_wavs)}  |  Unlabeled: {len(unlabeled)}")
    print(f"Already labeled: {target_count} {label_target}, {other_count} {label_other}, {mixed_count} {LABEL_MIXED}")

    if not unlabeled:
        print("Nothing to label.")
        return

    labeled_this_session = 0
    try:
        for idx, wav in enumerate(unlabeled, 1):
            label = _prompt(wav, idx, len(unlabeled), args.play, label_target, label_other)
            if label is not None:
                labels[str(wav.relative_to(recordings_dir))] = label
                _save_labels(labels_path, labels)
                labeled_this_session += 1
                print(f"  → {label}  (saved)")
    except KeyboardInterrupt:
        print("\nStopped.")

    target_count = sum(1 for v in labels.values() if v == label_target)
    mixed_count = sum(1 for v in labels.values() if v == LABEL_MIXED)
    print(f"\nLabeled this session: {labeled_this_session}")
    print(f"Total labels: {len(labels)}  ({target_count} {label_target}, {mixed_count} {LABEL_MIXED})")


if __name__ == "__main__":
    main()
