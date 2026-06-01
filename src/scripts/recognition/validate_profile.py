"""
Leave-one-out cross-validation for an MFCC-based audio profile.

For each labeled file:
  - target class  → centroid built from all OTHER target files; score should be high
  - other class   → centroid built from ALL target files; score should be low
  - mixed         → excluded by default (--include-mixed treats them as other)

Outputs similarity statistics, a threshold sweep ranked by F1, a confusion
matrix at the best (or chosen) threshold, and a list of misclassified files.

This tool is class-agnostic.  Common use cases:

  * Individual dog identification   --target-label neighbor-dog
  * Speaker identification          --target-label known-speaker
  * Specific alarm model            --target-label smoke-alarm-x

The recommended production threshold printed at the end can be used as the
similarity cutoff in a downstream classifier or policy rule.

Usage:
    ai-acoustic-monitor-validate-profile [--recordings DIR] [--labels FILE]
                                          [--target-label LABEL] [--other-label LABEL]
                                          [--threshold FLOAT] [--include-mixed]
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from .mfcc_core import (
    _DEFAULT_LABELS_FILE,
    LABEL_MIXED,
    cosine_sim as _cosine_sim,
    load_labels as _load_labels,
    mfcc_vector_from_file as _mfcc_vector,
    resolve_labels_path as _resolve_labels_path,
)


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
        description="Leave-one-out cross-validation for an MFCC-based audio profile.",
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
        "--threshold", type=float, default=None, help="Similarity threshold to evaluate (default: best F1 threshold)"
    )
    parser.add_argument("--include-mixed", action="store_true", help="Treat 'mixed' files as other-class in validation")
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    label_target: str = args.target_label
    label_other: str = args.other_label

    labels_path = _resolve_labels_path(args.labels, recordings_dir)
    labels = _load_labels(labels_path, recordings_dir)

    target_paths = [recordings_dir / p for p, v in labels.items() if v == label_target]
    other_paths = [recordings_dir / p for p, v in labels.items() if v == label_other]
    mixed_paths = [recordings_dir / p for p, v in labels.items() if v == LABEL_MIXED]

    negative_paths = other_paths + (mixed_paths if args.include_mixed else [])

    print(
        f"Labels: {len(target_paths)} {label_target}  |  {len(other_paths)} {label_other}  |  {len(mixed_paths)} mixed"
        + (" (included as other)" if args.include_mixed and mixed_paths else " (excluded)")
    )

    if len(target_paths) < 2:
        print(f"\nNeed at least 2 {label_target} examples for leave-one-out validation.", file=sys.stderr)
        sys.exit(1)
    if not negative_paths:
        print(f"\nNo {label_other} examples to evaluate against.", file=sys.stderr)
        sys.exit(1)

    print("\nComputing MFCC vectors…")

    target_vecs: list[tuple[Path, np.ndarray]] = []
    for p in target_paths:
        v = _mfcc_vector(p)
        if v is not None:
            target_vecs.append((p, v))
        else:
            print(f"  [skip] {p.name} — could not read")

    neg_vecs: list[tuple[Path, np.ndarray]] = []
    for p in negative_paths:
        v = _mfcc_vector(p)
        if v is not None:
            neg_vecs.append((p, v))
        else:
            print(f"  [skip] {p.name} — could not read")

    if len(target_vecs) < 2:
        print(f"Not enough readable {label_target} files.", file=sys.stderr)
        sys.exit(1)

    # LOOCV: for each target file, score against centroid of all others
    target_scores: list[tuple[Path, float]] = []
    for i, (path, vec) in enumerate(target_vecs):
        others = [v for j, (_, v) in enumerate(target_vecs) if j != i]
        c = _centroid(others)
        if c is not None:
            target_scores.append((path, _cosine_sim(vec, c)))

    # Other class: score against full target centroid
    all_target_vecs = [v for _, v in target_vecs]
    full_centroid = _centroid(all_target_vecs)
    neg_scores: list[tuple[Path, float]] = [(path, _cosine_sim(vec, full_centroid)) for path, vec in neg_vecs]

    rs = [s for _, s in target_scores]
    ns = [s for _, s in neg_scores]

    print(f"\nSimilarity statistics ({len(rs)} {label_target}, {len(ns)} {label_other} evaluated):")
    print(f"  {label_target:<20}: {_stats(rs)}")
    print(f"  {label_other:<20}: {_stats(ns)}")

    overlap = sum(1 for s in rs if s <= max(ns)) + sum(1 for s in ns if s >= min(rs))
    print(f"  overlap zone [{min(rs):.3f} – {max(ns):.3f}]: {overlap} file(s) in ambiguous range")

    all_scores = rs + ns
    thresholds = sorted(set(round(s, 3) for s in all_scores))
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

    chosen = args.threshold if args.threshold is not None else best_thresh
    print(f"\nConfusion matrix at threshold={chosen:.3f}:")
    tp_files = [(p, s) for p, s in target_scores if s >= chosen]
    fn_files = [(p, s) for p, s in target_scores if s < chosen]
    fp_files = [(p, s) for p, s in neg_scores if s >= chosen]
    tn_files = [(p, s) for p, s in neg_scores if s < chosen]

    col = max(len(f"actual {label_target}"), len(f"actual {label_other}"), 18)
    pred_t = f"pred {label_target}"
    pred_o = f"pred {label_other}"
    print(f"  {'':>{col}}  {pred_t:>16}  {pred_o:>16}")
    print(f"  {f'actual {label_target}':>{col}}  {len(tp_files):>16}  {len(fn_files):>16}")
    print(f"  {f'actual {label_other}':>{col}}  {len(fp_files):>16}  {len(tn_files):>16}")

    p, r, f1 = _precision_recall_f1(len(tp_files), len(fp_files), len(fn_files))
    acc = (len(tp_files) + len(tn_files)) / (len(target_scores) + len(neg_scores))
    print(f"\n  Accuracy={acc:.2f}  Precision={p:.2f}  Recall={r:.2f}  F1={f1:.2f}")

    misclassified = [(p, s, f"FN — {label_target} scored below threshold") for p, s in fn_files] + [
        (p, s, f"FP — {label_other} scored above threshold") for p, s in fp_files
    ]
    if misclassified:
        print(f"\nMisclassified ({len(misclassified)} file(s)):")
        for path, sim, reason in sorted(misclassified, key=lambda x: -x[1]):
            print(f"  [{reason}]  {path.name}  sim={sim:.3f}")
    else:
        print("\nNo misclassifications at this threshold.")

    print(f"\nRecommended threshold for production: {chosen:.3f}")


if __name__ == "__main__":
    main()
