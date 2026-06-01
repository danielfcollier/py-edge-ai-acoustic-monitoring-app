"""
Shared MFCC computation and profile-scoring primitives.

Used by label_profile, review_profile, validate_profile (offline CLI tools)
and by RecorderTransformerWorker (runtime B1 scoring).
"""

import json
from pathlib import Path

import librosa
import numpy as np

_DEFAULT_LABELS_FILE = ".mfcc_labels.json"
LABEL_MIXED = "mixed"
_TARGET_SR = 22050
N_MFCC = 40
_F0_FMIN = 60.0
_F0_FMAX = 2000.0


# ---------------------------------------------------------------------------
# Labels I/O (shared by all three CLI tools)
# ---------------------------------------------------------------------------


def load_labels(labels_path: Path, recordings_dir: Path | None = None) -> dict:
    if not labels_path.exists():
        return {}
    raw = json.loads(labels_path.read_text())
    if recordings_dir is None:
        return raw
    out = {}
    for k, v in raw.items():
        p = Path(k)
        if p.is_absolute():
            try:
                k = str(p.relative_to(recordings_dir))
            except ValueError:
                k = p.name
        out[k] = v
    return out


def save_labels(labels_path: Path, labels: dict) -> None:
    labels_path.write_text(json.dumps(labels, indent=2, sort_keys=True))


def resolve_labels_path(labels_arg: str | None, recordings_dir: Path) -> Path:
    if labels_arg is None:
        return recordings_dir / _DEFAULT_LABELS_FILE
    p = Path(labels_arg)
    return p if p.is_absolute() else recordings_dir / p


# ---------------------------------------------------------------------------
# MFCC feature extraction
# ---------------------------------------------------------------------------


def mfcc_vector_from_audio(audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
    """
    Compute a 162-dim feature vector from in-memory audio.

    Vector layout: [mean_mfcc(40), std_mfcc(40), mean_delta(40), std_delta(40), f0_mean, f0_std]
    Captures timbral shape, rate of change, and fundamental pitch.
    Class-agnostic — works for dog barks, speech, alarms, machinery, etc.
    """
    try:
        if sample_rate != _TARGET_SR:
            audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=_TARGET_SR)
        if len(audio) < 512:
            return None

        mfcc = librosa.feature.mfcc(y=audio, sr=_TARGET_SR, n_mfcc=N_MFCC)
        delta = librosa.feature.delta(mfcc)

        f0 = librosa.yin(audio, fmin=_F0_FMIN, fmax=_F0_FMAX, sr=_TARGET_SR)
        voiced = f0[f0 > _F0_FMIN * 1.1]

        return np.concatenate(
            [
                np.mean(mfcc, axis=1),
                np.std(mfcc, axis=1),
                np.mean(delta, axis=1),
                np.std(delta, axis=1),
                [float(np.mean(voiced)) if len(voiced) > 0 else 0.0],
                [float(np.std(voiced)) if len(voiced) > 0 else 0.0],
            ]
        ).astype(np.float64)
    except Exception:
        return None


def mfcc_vector_from_file(wav_path: Path) -> np.ndarray | None:
    """Load a WAV file and compute its 162-dim MFCC vector."""
    try:
        audio, sr = librosa.load(str(wav_path), sr=_TARGET_SR, mono=True)
        return mfcc_vector_from_audio(audio, _TARGET_SR)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cosine similarity scoring
# ---------------------------------------------------------------------------


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def score_nearest(vec: np.ndarray, target_vecs: list[np.ndarray]) -> float:
    return max(cosine_sim(vec, tv) for tv in target_vecs)


def score_centroid(vec: np.ndarray, target_vecs: list[np.ndarray]) -> float:
    return cosine_sim(vec, np.mean(target_vecs, axis=0))


def save_profile(output_path: Path, vectors: list[np.ndarray]) -> None:
    """Serialize pre-computed target vectors to a portable .npz file."""
    np.savez(str(output_path), vectors=np.array(vectors))


def load_profile(profile_path: Path) -> list[np.ndarray]:
    """Load pre-computed target vectors from a .npz file."""
    data = np.load(str(profile_path))
    return list(data["vectors"])


def build_target_vectors(labels: dict, target_label: str, recordings_dir: Path) -> list[np.ndarray]:
    vectors = []
    for p, v in labels.items():
        if v != target_label:
            continue
        vec = mfcc_vector_from_file(recordings_dir / p)
        if vec is not None:
            vectors.append(vec)
    return vectors


# ---------------------------------------------------------------------------
# MfccScorer — profile loader used by the runtime (B1) and score_recordings (B2)
# ---------------------------------------------------------------------------


class MfccScorer:
    """
    Loads a labeled profile from disk and scores audio against it.

    Constructed once at startup (or script init); calling score_audio() or
    score_file() is cheap — the heavy work is loading the reference vectors.

    Two construction paths:
      MfccScorer(labels_file, target_label)         — computes vectors from WAV files (dev machine)
      MfccScorer.from_profile_file(path, label)     — loads pre-computed vectors (Pi deployment)
    """

    def __init__(self, labels_file: Path, target_label: str, method: str = "nearest"):
        labels = load_labels(labels_file, recordings_dir=labels_file.parent)
        self._target_vecs = build_target_vectors(labels, target_label, labels_file.parent)
        self._method = method
        self.target_label = target_label
        self.ready = len(self._target_vecs) >= 1

    @classmethod
    def from_profile_file(cls, profile_path: Path, target_label: str, method: str = "nearest") -> "MfccScorer":
        """Load a pre-exported .npz profile — no WAV files required."""
        instance = cls.__new__(cls)
        instance._target_vecs = load_profile(profile_path)
        instance._method = method
        instance.target_label = target_label
        instance.ready = len(instance._target_vecs) >= 1
        return instance

    def _score(self, vec: np.ndarray) -> float:
        if self._method == "centroid":
            return score_centroid(vec, self._target_vecs)
        return score_nearest(vec, self._target_vecs)

    def score_audio(self, audio: np.ndarray, sample_rate: int) -> float | None:
        """Score in-memory audio. Returns cosine similarity in [0, 1], or None on failure."""
        if not self.ready:
            return None
        vec = mfcc_vector_from_audio(audio, sample_rate)
        return self._score(vec) if vec is not None else None

    def score_file(self, wav_path: Path) -> float | None:
        """Score a WAV file. Returns cosine similarity in [0, 1], or None on failure."""
        if not self.ready:
            return None
        vec = mfcc_vector_from_file(wav_path)
        return self._score(vec) if vec is not None else None
