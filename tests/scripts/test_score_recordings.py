"""
Tests for scripts.recognition.score_recordings.
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.recognition.score_recordings import _parse_since, main


class TestParseSince:
    def test_none_returns_none(self):
        assert _parse_since(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_since("") is None

    def test_invalid_format_returns_none(self):
        assert _parse_since("yesterday") is None

    def test_7d_returns_correct_cutoff(self):
        before = datetime.now() - timedelta(days=7, seconds=1)
        after = datetime.now() - timedelta(days=7) + timedelta(seconds=1)
        result = _parse_since("7d")
        assert before < result < after

    def test_case_insensitive(self):
        assert _parse_since("7D") is not None


class TestScoreRecordingsMain:
    def _make_labels_file(self, tmp_path: Path) -> Path:
        """Write a minimal labels JSON with one 'target' entry pointing at a real wav."""
        import json
        import numpy as np
        import soundfile as sf

        wav = tmp_path / "sample.wav"
        sf.write(str(wav), np.zeros(22050, dtype=np.float32), 22050)

        labels = {"sample.wav": "target"}
        labels_path = tmp_path / ".mfcc_labels.json"
        labels_path.write_text(json.dumps(labels))
        return labels_path

    def test_exits_if_recordings_dir_missing(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            sys.argv = ["prog", "--recordings", str(tmp_path / "nonexistent")]
            main()

    def test_exits_if_no_target_examples(self, tmp_path, capsys):
        import json
        labels_path = tmp_path / ".mfcc_labels.json"
        labels_path.write_text(json.dumps({}))

        mock_scorer = MagicMock()
        mock_scorer.ready = False

        with (
            patch("scripts.recognition.score_recordings.MfccScorer", return_value=mock_scorer),
            pytest.raises(SystemExit),
        ):
            sys.argv = ["prog", "--recordings", str(tmp_path)]
            main()

    def test_creates_output_csv(self, tmp_path):
        mock_scorer = MagicMock()
        mock_scorer.ready = True
        mock_scorer.score_file.return_value = 0.91

        import json
        (tmp_path / "evidence-1.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        (tmp_path / ".mfcc_labels.json").write_text(json.dumps({"evidence-1.wav": "target"}))

        with patch("scripts.recognition.score_recordings.MfccScorer", return_value=mock_scorer):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--target-label", "dog"]
            main()

        output = tmp_path / "mfcc_scores_offline.csv"
        assert output.exists()
        rows = list(csv.reader(output.open()))
        assert rows[0] == ["filename", "mfcc_label", "mfcc_score", "threshold", "matched"]
        assert rows[1][1] == "dog"
        assert rows[1][2] == "0.91"

    def test_since_filter_excludes_old_files(self, tmp_path):
        import json
        import os

        wav = tmp_path / "old.wav"
        wav.write_bytes(b"\x00" * 44)
        old_mtime = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(wav, (old_mtime, old_mtime))

        (tmp_path / ".mfcc_labels.json").write_text(json.dumps({"old.wav": "target"}))

        mock_scorer = MagicMock()
        mock_scorer.ready = True
        mock_scorer.score_file.return_value = 0.9

        with patch("scripts.recognition.score_recordings.MfccScorer", return_value=mock_scorer):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--since", "7d"]
            main()

        # Old file filtered out — scorer never called and no output written
        mock_scorer.score_file.assert_not_called()
        assert not (tmp_path / "mfcc_scores_offline.csv").exists()

    def test_custom_output_path(self, tmp_path):
        import json
        wav = tmp_path / "e.wav"
        wav.write_bytes(b"\x00" * 44)
        (tmp_path / ".mfcc_labels.json").write_text(json.dumps({"e.wav": "target"}))

        mock_scorer = MagicMock()
        mock_scorer.ready = True
        mock_scorer.score_file.return_value = 0.7

        out = tmp_path / "custom_out.csv"
        with patch("scripts.recognition.score_recordings.MfccScorer", return_value=mock_scorer):
            sys.argv = ["prog", "--recordings", str(tmp_path), "--output", str(out)]
            main()

        assert out.exists()
