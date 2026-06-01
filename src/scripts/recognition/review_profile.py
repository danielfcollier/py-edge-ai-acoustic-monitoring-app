"""
Iterative MFCC-based review tool for audio profile recognition.

Builds a profile from labeled target-class WAV files (mean MFCC vector or
nearest-neighbor), ranks all unlabeled WAVs by cosine similarity, and presents
the top candidates for interactive accept/reject.  Run after seeding labels
with 'ai-acoustic-monitor-label-profile'. Repeat until the profile stabilises.

This tool is class-agnostic.  Common use cases:

  * Individual dog identification   --target-label neighbor-dog
  * Speaker identification          --target-label known-speaker
  * Specific alarm model            --target-label smoke-alarm-x

Feature vector (162 dims):
    [mean_mfcc(40), std_mfcc(40), mean_delta(40), std_delta(40), f0_mean(1), f0_std(1)]

Similarity scoring methods:
  nearest  — max cosine similarity to any single target-class example (default)
  centroid — cosine similarity to the mean of all target-class vectors

Usage:
    ai-acoustic-monitor-review-profile [--recordings DIR] [--labels FILE]
                                        [--target-label LABEL] [--other-label LABEL]
                                        [--method nearest|centroid] [--top N]
                                        [--bottom N] [--min-sim FLOAT]
                                        [--relabel] [--relabel-filter LABEL]
                                        [--play]
"""

import argparse
import sys
from pathlib import Path

import librosa

from .mfcc_core import (
    _DEFAULT_LABELS_FILE,
    LABEL_MIXED,
    build_target_vectors as _build_target_vectors,
    load_labels as _load_labels,
    mfcc_vector_from_file as _mfcc_vector,
    resolve_labels_path as _resolve_labels_path,
    save_labels as _save_labels,
    score_centroid as _score_centroid,
    score_nearest as _score_nearest,
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
    similarity: float | None = None,
    current_label: str | None = None,
) -> str | None:
    if current_label is not None:
        info = f"  [labeled: {current_label}]"
    elif similarity is not None:
        info = f"  (similarity: {similarity:.3f})"
    else:
        info = ""
    print(f"\n[{idx}/{total}] {wav_path.name}{info}")
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
        description="Rank unlabeled WAVs by MFCC similarity to a target-class profile for iterative labeling.",
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
    parser.add_argument(
        "--method",
        default="nearest",
        choices=["nearest", "centroid"],
        help="Scoring method: nearest=max similarity to any target file, centroid=similarity to mean",
    )
    parser.add_argument("--top", type=int, default=20, help="Candidates to review per run")
    parser.add_argument("--bottom", type=int, default=0, help="Review least similar N candidates (for other labeling)")
    parser.add_argument("--min-sim", type=float, default=0.0, help="Skip candidates below this similarity")
    parser.add_argument("--play", action="store_true", help="Play each WAV before prompting")
    parser.add_argument("--relabel", action="store_true", help="Review already-labeled files instead of unlabeled ones")
    parser.add_argument(
        "--relabel-filter",
        default="all",
        help="Which labeled files to review (default: all; or pass a specific label value)",
    )
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    label_target: str = args.target_label
    label_other: str = args.other_label

    labels_path = _resolve_labels_path(args.labels, recordings_dir)
    labels = _load_labels(labels_path, recordings_dir)

    target_count = sum(1 for v in labels.values() if v == label_target)
    mixed_count = sum(1 for v in labels.values() if v == LABEL_MIXED)
    other_count = sum(1 for v in labels.values() if v == label_other)
    print(f"Recordings: {recordings_dir}")
    print(f"Labeled: {target_count} {label_target}, {other_count} {label_other}, {mixed_count} {LABEL_MIXED}")

    labeled_this_session = 0

    if args.relabel:
        filt = args.relabel_filter
        pool = [(p, v) for p, v in labels.items() if filt == "all" or v == filt]
        pool.sort()
        if not pool:
            print(f"No labeled files matching filter '{filt}'.")
            return
        print(f"\nReviewing {len(pool)} labeled file(s) (filter: {filt}).\n")
        try:
            for idx, (path_str, current_label) in enumerate(pool, 1):
                new_label = _prompt(
                    recordings_dir / path_str,
                    idx,
                    len(pool),
                    args.play,
                    label_target,
                    label_other,
                    current_label=current_label,
                )
                if new_label is not None and new_label != current_label:
                    labels[path_str] = new_label
                    _save_labels(labels_path, labels)
                    labeled_this_session += 1
                    print(f"  → {new_label}  (updated)")
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        if target_count == 0:
            print(f"\nNo {label_target} examples yet. Run 'ai-acoustic-monitor-label-profile' first.", file=sys.stderr)
            sys.exit(1)

        print(f"Loading {target_count} {label_target} vector(s) [{args.method} method]…")
        target_vecs = _build_target_vectors(labels, label_target, recordings_dir)
        if not target_vecs:
            print(f"Could not read any {label_target} files.", file=sys.stderr)
            sys.exit(1)

        score_fn = _score_nearest if args.method == "nearest" else _score_centroid

        all_wavs = sorted(recordings_dir.rglob("*.wav"))
        unlabeled = [w for w in all_wavs if str(w.relative_to(recordings_dir)) not in labels]
        print(f"Scoring {len(unlabeled)} unlabeled files…")

        scored: list[tuple[float, Path]] = []
        for wav in unlabeled:
            vec = _mfcc_vector(wav)
            if vec is not None:
                sim = score_fn(vec, target_vecs)
                if sim >= args.min_sim:
                    scored.append((sim, wav))

        scored.sort(reverse=True)

        if args.bottom:
            candidates = scored[-args.bottom :][::-1]
            mode_label = f"bottom {len(candidates)} (least similar)"
        else:
            candidates = scored[: args.top]
            mode_label = f"top {len(candidates)}"

        if not candidates:
            print("No candidates above the similarity threshold.")
            return

        sims = [s for s, _ in scored]
        print(f"\nSimilarity range across {len(scored)} scoreable files: {min(sims):.3f} – {max(sims):.3f}")
        print(f"Reviewing {mode_label} (similarity: {candidates[0][0]:.3f} – {candidates[-1][0]:.3f}).\n")

        try:
            for idx, (sim, wav) in enumerate(candidates, 1):
                label = _prompt(wav, idx, len(candidates), args.play, label_target, label_other, similarity=sim)
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
    if labeled_this_session > 0 and not args.relabel:
        print("Re-run to update the profile with newly accepted examples.")


if __name__ == "__main__":
    main()
