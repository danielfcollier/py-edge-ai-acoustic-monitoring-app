"""
Iterative MFCC-based review tool for individual dog recognition.

Builds a centroid profile from labeled 'neighbour-dog' WAV files (mean MFCC vector),
ranks all unlabeled WAVs by cosine similarity to that centroid, and presents
the top candidates for interactive accept/reject. Run after seeding labels
with 'ai-acoustic-monitor-label-dog'. Repeat until the profile stabilises.

Usage:
    ai-acoustic-monitor-review-dog [--recordings DIR] [--labels FILE]
                                    [--top N] [--min-sim FLOAT] [--play]
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
N_MFCC = 40
_F0_FMIN = 60.0   # Hz — below lowest dog bark fundamental
_F0_FMAX = 2000.0  # Hz — above highest dog bark fundamental


def _load_labels(labels_path: Path) -> dict:
    if labels_path.exists():
        return json.loads(labels_path.read_text())
    return {}


def _save_labels(labels_path: Path, labels: dict) -> None:
    labels_path.write_text(json.dumps(labels, indent=2, sort_keys=True))


def _mfcc_vector(wav_path: Path) -> np.ndarray | None:
    """Feature vector: [mean_mfcc, std_mfcc, mean_delta, std_delta, f0_mean, f0_std] — 162 dims."""
    try:
        audio, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        if len(audio) < 512:
            return None

        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=N_MFCC)  # (40, T)
        delta = librosa.feature.delta(mfcc)                           # (40, T)

        mean_mfcc = np.mean(mfcc, axis=1)
        std_mfcc = np.std(mfcc, axis=1)
        mean_delta = np.mean(delta, axis=1)
        std_delta = np.std(delta, axis=1)

        f0 = librosa.yin(audio, fmin=_F0_FMIN, fmax=_F0_FMAX, sr=sr)
        voiced = f0[f0 > _F0_FMIN * 1.1]  # drop frames pinned at fmin (unvoiced)
        f0_mean = float(np.mean(voiced)) if len(voiced) > 0 else 0.0
        f0_std = float(np.std(voiced)) if len(voiced) > 0 else 0.0

        return np.concatenate([
            mean_mfcc, std_mfcc, mean_delta, std_delta,
            [f0_mean, f0_std],
        ]).astype(np.float64)
    except Exception:
        return None


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _build_neighbour-dog_vectors(labels: dict) -> list[np.ndarray]:
    """Returns all readable MFCC vectors for neighbour-dog-labeled files."""
    vectors = []
    for p, v in labels.items():
        if v != LABEL_neighbour-dog:
            continue
        vec = _mfcc_vector(Path(p))
        if vec is not None:
            vectors.append(vec)
    return vectors


def _score_centroid(vec: np.ndarray, neighbour-dog_vecs: list[np.ndarray]) -> float:
    centroid = np.mean(neighbour-dog_vecs, axis=0)
    return _cosine_sim(vec, centroid)


def _score_nearest(vec: np.ndarray, neighbour-dog_vecs: list[np.ndarray]) -> float:
    """Max cosine similarity to any single neighbour-dog example."""
    return max(_cosine_sim(vec, rv) for rv in neighbour-dog_vecs)


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
    similarity: float | None = None,
    current_label: str | None = None,
) -> str | None:
    """Returns 'neighbour-dog', 'other-dogs', or None (skip). Raises KeyboardInterrupt on quit."""
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
        description="Rank unlabeled WAVs by MFCC similarity to neighbour-dog profile for iterative labeling.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--recordings", default="recordings", help="Recordings directory")
    parser.add_argument("--labels", default=None, help="Labels JSON file (default: <recordings>/.neighbour-dog_labels.json)")
    parser.add_argument("--method", default="nearest", choices=["nearest", "centroid"],
                        help="Scoring method: nearest=max similarity to any neighbour-dog file, centroid=similarity to mean (default: nearest)")
    parser.add_argument("--top", type=int, default=20, help="Candidates to review per run")
    parser.add_argument("--bottom", type=int, default=0, help="Review least similar N candidates (for other-dogs labeling)")
    parser.add_argument("--min-sim", type=float, default=0.0, help="Skip candidates below this similarity")
    parser.add_argument("--play", action="store_true", help="Play each WAV before prompting")
    parser.add_argument("--relabel", action="store_true", help="Review already-labeled files instead of unlabeled ones")
    parser.add_argument("--relabel-filter", default="all", choices=["all", "neighbour-dog", "other-dogs", "mixed"],
                        help="Which labeled files to review (default: all)")
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    labels_path = Path(args.labels).resolve() if args.labels else recordings_dir / ".neighbour-dog_labels.json"
    labels = _load_labels(labels_path)

    neighbour-dog_count = sum(1 for v in labels.values() if v == LABEL_neighbour-dog)
    mixed_count = sum(1 for v in labels.values() if v == LABEL_MIXED)
    not_neighbour-dog_count = sum(1 for v in labels.values() if v == LABEL_NOT_neighbour-dog)
    print(f"Recordings: {recordings_dir}")
    print(f"Labeled: {neighbour-dog_count} neighbour-dog, {not_neighbour-dog_count} other-dogs, {mixed_count} mixed")

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
                new_label = _prompt(Path(path_str), idx, len(pool), args.play, current_label=current_label)
                if new_label is not None and new_label != current_label:
                    labels[path_str] = new_label
                    _save_labels(labels_path, labels)
                    labeled_this_session += 1
                    print(f"  → {new_label}  (updated)")
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        if neighbour-dog_count == 0:
            print("\nNo neighbour-dog examples yet. Run 'ai-acoustic-monitor-label-dog' first.", file=sys.stderr)
            sys.exit(1)

        print(f"Loading {neighbour-dog_count} neighbour-dog vector(s) [{args.method} method]…")
        neighbour-dog_vecs = _build_neighbour-dog_vectors(labels)
        if not neighbour-dog_vecs:
            print("Could not read any neighbour-dog files.", file=sys.stderr)
            sys.exit(1)

        score_fn = _score_nearest if args.method == "nearest" else _score_centroid

        all_wavs = sorted(recordings_dir.rglob("*.wav"))
        unlabeled = [w for w in all_wavs if str(w) not in labels]
        print(f"Scoring {len(unlabeled)} unlabeled files…")

        scored: list[tuple[float, Path]] = []
        for wav in unlabeled:
            vec = _mfcc_vector(wav)
            if vec is not None:
                sim = score_fn(vec, neighbour-dog_vecs)
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
                label = _prompt(wav, idx, len(candidates), args.play, similarity=sim)
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
    if labeled_this_session > 0 and not args.relabel:
        print("Re-run to update the centroid with newly accepted examples.")


if __name__ == "__main__":
    main()
