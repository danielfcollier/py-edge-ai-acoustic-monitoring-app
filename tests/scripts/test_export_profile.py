"""
Tests for scripts.recognition.export_profile.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from scripts.recognition.export_profile import main
from scripts.recognition.mfcc_core import load_profile


def _write_labels(tmp_path: Path, entries: dict) -> Path:
    p = tmp_path / ".mfcc_labels.json"
    p.write_text(json.dumps(entries))
    return p


class TestExportProfileMain:
    def test_exits_if_recordings_dir_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            sys.argv = ["prog", "--recordings", str(tmp_path / "nope")]
            main()

    def test_exits_if_no_target_examples(self, tmp_path):
        _write_labels(tmp_path, {})
        with pytest.raises(SystemExit):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--target-label", "dog"]
            main()

    def test_creates_npz_file(self, tmp_path):
        _write_labels(tmp_path, {"a.wav": "dog", "b.wav": "other"})

        fake_vecs = [np.ones(162), np.zeros(162)]
        with patch("scripts.recognition.export_profile.build_target_vectors", return_value=fake_vecs):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--target-label", "dog"]
            main()

        output = tmp_path / "dog.mfcc.npz"
        assert output.exists()

    def test_npz_contains_correct_vectors(self, tmp_path):
        _write_labels(tmp_path, {"a.wav": "dog"})
        fake_vecs = [np.full(162, 0.5)]

        with patch("scripts.recognition.export_profile.build_target_vectors", return_value=fake_vecs):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--target-label", "dog"]
            main()

        loaded = load_profile(tmp_path / "dog.mfcc.npz")
        assert len(loaded) == 1
        np.testing.assert_array_equal(loaded[0], fake_vecs[0])

    def test_custom_output_path(self, tmp_path):
        _write_labels(tmp_path, {"a.wav": "dog"})
        out = tmp_path / "custom.npz"

        with patch("scripts.recognition.export_profile.build_target_vectors", return_value=[np.ones(162)]):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--target-label", "dog", "--output", str(out)]
            main()

        assert out.exists()

    def test_exits_if_no_readable_wav_files(self, tmp_path):
        _write_labels(tmp_path, {"a.wav": "dog"})

        with (
            patch("scripts.recognition.export_profile.build_target_vectors", return_value=[]),
            pytest.raises(SystemExit),
        ):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--target-label", "dog"]
            main()
