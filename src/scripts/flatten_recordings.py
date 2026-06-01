"""
Flatten the recordings directory: move all WAVs from subdirectories into the
root of recordings/, deduplicating by filename (keeping the oldest copy when
multiple files share the same name). Updates .neighbor_dog_labels.json paths too.

Subdirectories listed in --exclude-dirs (default: clips) are left untouched.
The clips/ directory is excluded by default because it holds intentional MFCC
training clips that carry their own label entries and must keep their relative paths.

Usage:
    ai-acoustic-monitor-flatten-recordings [--recordings DIR] [--labels FILE]
                                            [--exclude-dirs DIR ...] [--dry-run]
"""

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path


def _load_labels(labels_path: Path) -> dict:
    if labels_path.exists():
        return json.loads(labels_path.read_text())
    return {}


def _save_labels(labels_path: Path, labels: dict) -> None:
    labels_path.write_text(json.dumps(labels, indent=2, sort_keys=True))


def _is_excluded(wav: Path, recordings_dir: Path, excluded: set[str]) -> bool:
    """Return True if wav lives inside any top-level excluded subdirectory."""
    try:
        rel = wav.relative_to(recordings_dir)
    except ValueError:
        return False
    return rel.parts[0] in excluded


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flatten recordings/ subdirectories into root, deduplicating by filename.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--recordings", default="recordings", help="Recordings directory")
    parser.add_argument(
        "--labels", default=None, help="Labels JSON file (default: <recordings>/.neighbor_dog_labels.json)"
    )
    parser.add_argument(
        "--exclude-dirs",
        nargs="*",
        default=["clips"],
        metavar="DIR",
        help="Top-level subdirectories to leave untouched (default: clips)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    args = parser.parse_args()

    recordings_dir = Path(args.recordings).resolve()
    if not recordings_dir.is_dir():
        print(f"Error: recordings directory not found: {recordings_dir}", file=sys.stderr)
        sys.exit(1)

    excluded: set[str] = set(args.exclude_dirs or [])

    labels_path = Path(args.labels).resolve() if args.labels else recordings_dir / ".neighbor_dog_labels.json"
    labels = _load_labels(labels_path)
    dry = args.dry_run

    if dry:
        print("DRY RUN — no changes will be made.\n")
    if excluded:
        print(f"Excluding subdirectories: {', '.join(sorted(excluded))}\n")

    # Collect all WAVs: root-level and in non-excluded subdirectories
    root_wavs = {f.name: f for f in recordings_dir.glob("*.wav")}
    sub_wavs: dict[str, list[Path]] = defaultdict(list)
    for wav in recordings_dir.rglob("*.wav"):
        if wav.parent != recordings_dir and not _is_excluded(wav, recordings_dir, excluded):
            sub_wavs[wav.name].append(wav)

    all_names = set(root_wavs) | set(sub_wavs)
    print(f"Found {len(root_wavs)} WAV(s) at root, {sum(len(v) for v in sub_wavs.values())} in subdirectories.")
    print(f"Unique filenames: {len(all_names)}\n")

    moved = deleted = skipped = 0

    for name in sorted(all_names):
        copies: list[Path] = list(sub_wavs.get(name, []))
        if name in root_wavs:
            copies.append(root_wavs[name])

        if len(copies) == 1 and copies[0].parent == recordings_dir:
            continue  # already at root, nothing to do

        # Sort by mtime ascending — oldest first
        copies.sort(key=lambda p: p.stat().st_mtime)
        keeper = copies[0]
        duplicates = copies[1:]

        dest = recordings_dir / name

        if keeper.parent != recordings_dir:
            if dest.exists():
                # root already has a copy — compare mtimes
                if dest.stat().st_mtime <= keeper.stat().st_mtime:
                    # root copy is older or equal — keep it, delete keeper
                    duplicates.append(keeper)
                    keeper = dest
                    duplicates = [p for p in duplicates if p != dest]
                else:
                    # keeper from subdir is older — replace root copy
                    duplicates.append(dest)

            if keeper != dest:
                print(f"  MOVE  {keeper.relative_to(recordings_dir)} → {name}")
                if not dry:
                    shutil.move(str(keeper), str(dest))
                moved += 1

        for dup in duplicates:
            if dup == dest:
                continue
            print(f"  DEL   {dup.relative_to(recordings_dir)}  (duplicate of {name})")
            if not dry:
                dup.unlink()
            deleted += 1

    # Remove empty subdirectories (deepest first)
    subdirs = sorted(
        [d for d in recordings_dir.rglob("*") if d.is_dir()],
        key=lambda d: len(d.parts),
        reverse=True,
    )
    removed_dirs = 0
    for d in subdirs:
        if d == recordings_dir:
            continue
        try:
            if not any(d.iterdir()):
                print(f"  RMDIR {d.relative_to(recordings_dir)}")
                if not dry:
                    d.rmdir()
                removed_dirs += 1
        except Exception:
            pass

    print(f"\nSummary: {moved} moved, {deleted} duplicates deleted, {removed_dirs} empty dirs removed.")

    if labels and not dry:
        # Remap label paths: replace any non-excluded subdirectory path with the root-level filename.
        # Paths inside excluded directories (e.g. clips/) are kept verbatim.
        updated = {}
        conflicts: list[str] = []
        for old_path, label in labels.items():
            p = Path(old_path)
            if p.parts[0] in excluded:
                new_path = old_path  # preserve clips/ paths as-is
            else:
                new_path = p.name  # flatten to root filename
            if new_path in updated and updated[new_path] != label:
                conflicts.append(f"  CONFLICT {new_path}: '{updated[new_path]}' vs '{label}'")
            else:
                updated[new_path] = label
        if conflicts:
            print("\nLabel conflicts (manual review needed):")
            for c in conflicts:
                print(c)
        _save_labels(labels_path, updated)
        old_count = len(labels)
        new_count = len(updated)
        print(f"Labels: {old_count} entries → {new_count} (deduped paths saved to {labels_path.name})")
    elif dry and labels:
        # Show what label remapping would look like
        remapped = sum(
            1
            for p in labels
            if (Path(p).is_absolute() or Path(p).parent != Path(".")) and Path(p).parts[0] not in excluded
        )
        print(f"Labels: {remapped} path(s) would be remapped ({len(excluded)} excluded dir(s) left unchanged).")

    if skipped:
        print(f"Skipped: {skipped}")


if __name__ == "__main__":
    main()
