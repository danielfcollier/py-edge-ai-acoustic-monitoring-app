"""
Leave-one-out cross-validation for the neighbour-dog MFCC identification profile.

For each labeled file:
  - neighbour-dog    → centroid built from all OTHER neighbour-dog files; score should be high
  - other-dogs → centroid built from ALL neighbour-dog files; score should be low
  - mixed   → excluded by default (--include-mixed treats them as other-dogs)

Outputs similarity statistics, threshold sweep, confusion matrix at the best
F1 threshold, and a list of misclassified files.

Usage:
    ai-acoustic-monitor-validate-dog [--recordings DIR] [--labels FILE]
                                     [--threshold FLOAT] [--include-mixed]
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
_F0_FMIN = 60.0
_F0_FMAX = 2000.0


def _load_labels(labels_path: Path) -> dict:
    if labels_path.exists():
        return json.loads(labels_path.read_text())
    return {}


def _mfcc_vector(wav_path: Path) -> np.ndarray | None:
    """Feature vector: [mean_mfcc, std_mfcc, mean_delta, std_delta, f0_mean, f0_std] — 162 dims."""
    try:
        audio, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        if len(audio) < 512:
            return None

        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=N_MFCC)
        delta = librosa.feature.delta(mfcc)

        mean_mfcc = np.mean(mfcc, axis=1)
        std_mfcc = np.std(mfcc, axis=1)
        mean_delta = np.mean(delta, axis=1)
        std_delta = np.std(delta, axis=1)

        f0 = librosa.yin(audio, fmin=_F0_FMIN, fmax=_F0_FMAX, sr=sr)
        voiced = f0[f0 > _F0_FMIN * 1.1]
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


def _centroid(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    return np.mean(vectors, axis=0)


def _stats(values: list[float]) -> str:
    if not values:
        return "n/a"
    a = np.array(values)
    return f"mean={a.mean():.3f}  std={a.std():.3f}  min={a.min():.3f}  max={a.max():.3f}"


def _precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Leave-one-out cross-validation for neighbour-dog MFCC profile.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--recordings", default="recordings", help="Recordings directory")
    parser.add_argument("--labels", default=None, help="Labels JSON file (default: <recordings>/.neighbour-dog_labels.json)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Similarity threshold to evaluate (default: best F1 threshold)")
    parser.add_argument("--include-mixed", action="store_true",
                        help="Treat 'mixed' files as other-dogs in validation")
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    labels_path = Path(args.labels).resolve() if args.labels else recordings_dir / ".neighbour-dog_labels.json"
    labels = _load_labels(labels_path)

    # Partition labels
    neighbour-dog_paths = [Path(p) for p, v in labels.items() if v == LABEL_neighbour-dog]
    not_neighbour-dog_paths = [Path(p) for p, v in labels.items() if v == LABEL_NOT_neighbour-dog]
    mixed_paths = [Path(p) for p, v in labels.items() if v == LABEL_MIXED]

    negative_paths = not_neighbour-dog_paths + (mixed_paths if args.include_mixed else [])

    print(f"Labels: {len(neighbour-dog_paths)} neighbour-dog  |  {len(not_neighbour-dog_paths)} other-dogs  |  {len(mixed_paths)} mixed"
          + (" (included as other-dogs)" if args.include_mixed and mixed_paths else " (excluded)"))

    if len(neighbour-dog_paths) < 2:
        print("\nNeed at least 2 neighbour-dog examples for leave-one-out validation.", file=sys.stderr)
        sys.exit(1)
    if not negative_paths:
        print("\nNo other-dogs examples to evaluate against.", file=sys.stderr)
        sys.exit(1)

    # Pre-compute all MFCC vectors
    print("\nComputing MFCC vectors…")

    neighbour-dog_vecs: list[tuple[Path, np.ndarray]] = []
    for p in neighbour-dog_paths:
        v = _mfcc_vector(p)
        if v is not None:
            neighbour-dog_vecs.append((p, v))
        else:
            print(f"  [skip] {p.name} — could not read")

    neg_vecs: list[tuple[Path, np.ndarray]] = []
    for p in negative_paths:
        v = _mfcc_vector(p)
        if v is not None:
            neg_vecs.append((p, v))
        else:
            print(f"  [skip] {p.name} — could not read")

    if len(neighbour-dog_vecs) < 2:
        print("Not enough readable neighbour-dog files.", file=sys.stderr)
        sys.exit(1)

    # LOOCV scores
    # neighbour-dog: leave one out, score against centroid of the rest
    neighbour-dog_scores: list[tuple[Path, float]] = []
    for i, (path, vec) in enumerate(neighbour-dog_vecs):
        others = [v for j, (_, v) in enumerate(neighbour-dog_vecs) if j != i]
        c = _centroid(others)
        if c is not None:
            neighbour-dog_scores.append((path, _cosine_sim(vec, c)))

    # other-dogs: score against full neighbour-dog centroid
    all_neighbour-dog_vecs = [v for _, v in neighbour-dog_vecs]
    full_centroid = _centroid(all_neighbour-dog_vecs)
    neg_scores: list[tuple[Path, float]] = [
        (path, _cosine_sim(vec, full_centroid)) for path, vec in neg_vecs
    ]

    # Similarity statistics
    rs = [s for _, s in neighbour-dog_scores]
    ns = [s for _, s in neg_scores]

    print(f"\nSimilarity statistics ({len(rs)} neighbour-dog, {len(ns)} other-dogs evaluated):")
    print(f"  neighbour-dog     : {_stats(rs)}")
    print(f"  other-dogs : {_stats(ns)}")

    overlap = sum(1 for s in rs if s <= max(ns)) + sum(1 for s in ns if s >= min(rs))
    print(f"  overlap zone [{min(rs):.3f} – {max(ns):.3f}]: {overlap} file(s) in ambiguous range")

    # Threshold sweep
    all_scores = rs + ns
    thresholds = sorted(set(round(s, 3) for s in all_scores))
    # Add midpoints between adjacent unique values for finer sweep
    sweep = sorted(set(thresholds + [round((a + b) / 2, 3) for a, b in zip(thresholds, thresholds[1:])]))

    best_f1, best_thresh = -1.0, 0.0
    results = []
    for t in sweep:
        tp = sum(1 for s in rs if s >= t)
        fn = sum(1 for s in rs if s < t)
        fp = sum(1 for s in ns if s >= t)
        tn = sum(1 for s in ns if s < t)
        p, r, f1 = _precision_recall_f1(tp, fp, fn)
        results.append((t, tp, fp, fn, tn, p, r, f1))
        if f1 > best_f1:
            best_f1, best_thresh = f1, t

    print("\nThreshold sweep (top 10 by F1):")
    print(f"  {'thresh':>6}  {'prec':>5}  {'rec':>5}  {'F1':>5}  {'TP':>3}  {'FP':>3}  {'FN':>3}  {'TN':>3}")
    top10 = sorted(results, key=lambda x: -x[7])[:10]
    for t, tp, fp, fn, tn, p, r, f1 in sorted(top10, key=lambda x: x[0]):
        marker = " ◀ best F1" if t == best_thresh else ""
        print(f"  {t:>6.3f}  {p:>5.2f}  {r:>5.2f}  {f1:>5.2f}  {tp:>3}  {fp:>3}  {fn:>3}  {tn:>3}{marker}")

    # Evaluate at chosen threshold
    chosen = args.threshold if args.threshold is not None else best_thresh
    print(f"\nConfusion matrix at threshold={chosen:.3f}:")
    tp_files = [(p, s) for p, s in neighbour-dog_scores if s >= chosen]
    fn_files = [(p, s) for p, s in neighbour-dog_scores if s < chosen]
    fp_files = [(p, s) for p, s in neg_scores if s >= chosen]
    tn_files = [(p, s) for p, s in neg_scores if s < chosen]

    col = 18
    print(f"  {'':>{col}}  {'pred neighbour-dog':>10}  {'pred other-dogs':>13}")
    print(f"  {'actual neighbour-dog':>{col}}  {len(tp_files):>10}  {len(fn_files):>13}")
    print(f"  {'actual other-dogs':>{col}}  {len(fp_files):>10}  {len(tn_files):>13}")

    p, r, f1 = _precision_recall_f1(len(tp_files), len(fp_files), len(fn_files))
    acc = (len(tp_files) + len(tn_files)) / (len(neighbour-dog_scores) + len(neg_scores))
    print(f"\n  Accuracy={acc:.2f}  Precision={p:.2f}  Recall={r:.2f}  F1={f1:.2f}")

    misclassified = [(p, s, "FN — neighbour-dog scored below threshold") for p, s in fn_files] + \
                    [(p, s, "FP — other-dogs scored above threshold") for p, s in fp_files]
    if misclassified:
        print(f"\nMisclassified ({len(misclassified)} file(s)):")
        for path, sim, reason in sorted(misclassified, key=lambda x: -x[1]):
            print(f"  [{reason}]  {path.name}  sim={sim:.3f}")
    else:
        print("\nNo misclassifications at this threshold.")

    print(f"\nRecommended threshold for production: {chosen:.3f}")


if __name__ == "__main__":
    main()
