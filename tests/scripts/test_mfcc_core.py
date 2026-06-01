"""
Tests for scripts.recognition.mfcc_core — save/load profile and MfccScorer.from_profile_file.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.recognition.mfcc_core import (
    MfccScorer,
    load_profile,
    save_profile,
)


def _fake_vectors(n: int = 3, dim: int = 162) -> list[np.ndarray]:
    rng = np.random.default_rng(42)
    return [rng.random(dim) for _ in range(n)]


class TestSaveLoadProfile:
    def test_roundtrip_preserves_vectors(self, tmp_path):
        vecs = _fake_vectors(5)
        out = tmp_path / "profile.npz"
        save_profile(out, vecs)
        loaded = load_profile(out)
        assert len(loaded) == 5
        for orig, back in zip(vecs, loaded):
            np.testing.assert_array_equal(orig, back)

    def test_file_is_created(self, tmp_path):
        save_profile(tmp_path / "p.npz", _fake_vectors(2))
        assert (tmp_path / "p.npz").exists()

    def test_single_vector_roundtrip(self, tmp_path):
        vecs = _fake_vectors(1)
        out = tmp_path / "single.npz"
        save_profile(out, vecs)
        loaded = load_profile(out)
        assert len(loaded) == 1
        np.testing.assert_array_equal(vecs[0], loaded[0])


class TestMfccScorerFromProfileFile:
    def test_ready_with_valid_profile(self, tmp_path):
        vecs = _fake_vectors(3)
        p = tmp_path / "p.npz"
        save_profile(p, vecs)
        scorer = MfccScorer.from_profile_file(p, "dog")
        assert scorer.ready is True
        assert scorer.target_label == "dog"
        assert len(scorer._target_vecs) == 3

    def test_scores_audio(self, tmp_path):
        vecs = _fake_vectors(3)
        p = tmp_path / "p.npz"
        save_profile(p, vecs)
        scorer = MfccScorer.from_profile_file(p, "dog")
        audio = np.zeros(22050, dtype=np.float32)
        # score_audio may return None for silent audio — we just check it doesn't raise
        result = scorer.score_audio(audio, 22050)
        assert result is None or isinstance(result, float)

    def test_method_respected(self, tmp_path):
        vecs = _fake_vectors(3)
        p = tmp_path / "p.npz"
        save_profile(p, vecs)
        scorer = MfccScorer.from_profile_file(p, "dog", method="centroid")
        assert scorer._method == "centroid"

    def test_not_ready_with_empty_profile(self, tmp_path):
        p = tmp_path / "empty.npz"
        save_profile(p, [])
        scorer = MfccScorer.from_profile_file(p, "dog")
        assert scorer.ready is False
