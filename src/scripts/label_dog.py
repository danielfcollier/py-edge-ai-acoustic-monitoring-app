"""
Seed labeling tool for individual dog recognition.

Browse unlabeled WAV files in the recordings directory and mark them
as 'neighbour-dog' (the tracked dog) or 'other-dogs'. Labels are stored in a JSON
file inside the recordings directory.

Usage:
    ai-acoustic-monitor-label-dog [--recordings DIR] [--labels FILE] [--play]
"""

import argparse
import json
import sys
from pathlib import Path

import librosa
import numpy as np

LABEL_neighbour-dog = "neighbour-dog"
LABEL_NOT_neighbour-dog = "other-dogs"
LABEL_MIXED = "mixed"


def _load_labels(labels_path: Path) -> dict:
    if labels_path.exists():
        return json.loads(labels_path.read_text())
    return {}


def _save_labels(labels_path: Path, labels: dict) -> None:
    labels_path.write_text(json.dumps(labels, indent=2, sort_keys=True))


def _play(wav_path: Path) -> None:
    try:
        import sounddevice as sd
        audio, sr = librosa.load(str(wav_path), sr=None, mono=True)
        sd.play(audio, sr)
        input("  ↵ Enter to stop playback…")
        sd.stop()
    except Exception as e:
        print(f"  [playback error: {e}]")


def _prompt(wav_path: Path, idx: int, total: int, do_play: bool, current_label: str | None = None) -> str | None:
    """Returns 'neighbour-dog', 'other-dogs', or None (skip). Raises KeyboardInterrupt on quit."""
    tag = f"  [labeled: {current_label}]" if current_label else ""
    print(f"\n[{idx}/{total}] {wav_path.name}{tag}")
    if do_play:
        _play(wav_path)
    while True:
        ch = input("  [y] neighbour-dog  [n] other-dogs  [m] mixed  [s] skip  [r] replay  [q] quit: ").strip().lower()
        if ch == "y":
            return LABEL_neighbour-dog
        if ch == "n":
            return LABEL_NOT_neighbour-dog
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
        description="Label WAV files as 'neighbour-dog' or 'other-dogs' for dog recognition.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--recordings", default="recordings", help="Recordings directory")
    parser.add_argument("--labels", default=None, help="Labels JSON file (default: <recordings>/.neighbour-dog_labels.json)")
    parser.add_argument("--play", action="store_true", help="Play each WAV before prompting")
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    labels_path = Path(args.labels).resolve() if args.labels else recordings_dir / ".neighbour-dog_labels.json"
    labels = _load_labels(labels_path)

    all_wavs = sorted(recordings_dir.rglob("*.wav"))
    unlabeled = [w for w in all_wavs if str(w) not in labels]

    neighbour-dog_count = sum(1 for v in labels.values() if v == LABEL_neighbour-dog)
    mixed_count = sum(1 for v in labels.values() if v == LABEL_MIXED)
    not_neighbour-dog_count = sum(1 for v in labels.values() if v == LABEL_NOT_neighbour-dog)
    print(f"Recordings: {recordings_dir}")
    print(f"Total WAV files: {len(all_wavs)}  |  Unlabeled: {len(unlabeled)}")
    print(f"Already labeled: {neighbour-dog_count} neighbour-dog, {not_neighbour-dog_count} other-dogs, {mixed_count} mixed")

    if not unlabeled:
        print("Nothing to label.")
        return

    labeled_this_session = 0
    try:
        for idx, wav in enumerate(unlabeled, 1):
            label = _prompt(wav, idx, len(unlabeled), args.play)
            if label is not None:
                labels[str(wav)] = label
                _save_labels(labels_path, labels)
                labeled_this_session += 1
                print(f"  → {label}  (saved)")
    except KeyboardInterrupt:
        print("\nStopped.")

    neighbour-dog_count = sum(1 for v in labels.values() if v == LABEL_neighbour-dog)
    mixed_count = sum(1 for v in labels.values() if v == LABEL_MIXED)
    print(f"\nLabeled this session: {labeled_this_session}")
    print(f"Total labels: {len(labels)}  ({neighbour-dog_count} neighbour-dog, {mixed_count} mixed)")


if __name__ == "__main__":
    main()
