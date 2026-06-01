"""
Tests for MFCC-related paths in CloudUploaderService:
- _write_mfcc_scores_csv
- _send_telegram_alert with MFCC scores
- effective_actions suppression of telegram alert
"""

import csv
import queue
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.cloud_uploader_service import CloudUploaderService


def _make_service(tmp_path: Path) -> CloudUploaderService:
    mock_settings = MagicMock()
    mock_settings.CONFIG.services.internet_enabled = True
    mock_settings.CONFIG.services.telegram_enabled = True
    mock_settings.CONFIG.services.cloud.provider = "magalu"
    mock_settings.CONFIG.services.cloud.bucket_name = "bucket"
    mock_settings.CONFIG.services.cloud.region = "br-se1"
    mock_settings.CONFIG.mfcc_profiles = []
    mock_settings.MAGALU_ACCESS_KEY = "key"
    mock_settings.MAGALU_SECRET_KEY = "secret"
    mock_settings.MAGALU_URL = None

    with (
        patch("app.services.cloud_uploader_service.settings", mock_settings),
        patch("app.services.cloud_uploader_service.TelegramBotClient"),
        patch("app.services.cloud_uploader_service.S3Provider"),
    ):
        svc = CloudUploaderService(queue.Queue(), tmp_path)

    return svc


def _event(uuid="abcd1234-0000-0000-0000-000000000000",
           timestamp="2026-05-31T10:00:00",
           duration=5.0,
           label="Dog",
           mfcc_scores=None,
           effective_actions=None):
    import numpy as np
    meta = {"label": label, "confidence": 0.9, "calibrated": False}
    if mfcc_scores is not None:
        meta["mfcc_scores"] = mfcc_scores
    if effective_actions is not None:
        meta["effective_actions"] = effective_actions
    return {
        "uuid": uuid,
        "timestamp": timestamp,
        "duration_sec": duration,
        "sample_rate": 48000,
        "audio_data": np.zeros(100),
        "metadata": meta,
    }


class TestWriteMfccScoresCsv:
    def test_no_mfcc_scores_writes_nothing(self, tmp_path):
        svc = _make_service(tmp_path)
        svc._write_mfcc_scores_csv(_event())
        assert not (tmp_path / "mfcc_scores.csv").exists()

    def test_creates_csv_with_header_on_first_write(self, tmp_path):
        svc = _make_service(tmp_path)
        svc._write_mfcc_scores_csv(_event(mfcc_scores={"dog": 0.91}))
        csv_path = tmp_path / "mfcc_scores.csv"
        assert csv_path.exists()
        rows = list(csv.reader(csv_path.open()))
        assert rows[0] == ["uuid", "timestamp", "yamnet_label", "mfcc_label", "mfcc_score"]

    def test_writes_score_row(self, tmp_path):
        svc = _make_service(tmp_path)
        svc._write_mfcc_scores_csv(_event(mfcc_scores={"dog": 0.91}))
        rows = list(csv.reader((tmp_path / "mfcc_scores.csv").open()))
        assert rows[1][3] == "dog"
        assert rows[1][4] == "0.91"

    def test_appends_multiple_labels_as_separate_rows(self, tmp_path):
        svc = _make_service(tmp_path)
        svc._write_mfcc_scores_csv(_event(mfcc_scores={"dog": 0.91, "cat": 0.45}))
        rows = list(csv.reader((tmp_path / "mfcc_scores.csv").open()))
        labels = {r[3] for r in rows[1:]}
        assert labels == {"dog", "cat"}

    def test_no_duplicate_header_on_second_call(self, tmp_path):
        svc = _make_service(tmp_path)
        svc._write_mfcc_scores_csv(_event(mfcc_scores={"dog": 0.9}))
        svc._write_mfcc_scores_csv(_event(mfcc_scores={"dog": 0.8}))
        rows = list(csv.reader((tmp_path / "mfcc_scores.csv").open()))
        header_rows = [r for r in rows if r[0] == "uuid"]
        assert len(header_rows) == 1


class TestSendTelegramAlertMfcc:
    def test_no_mfcc_scores_no_extra_lines(self, tmp_path):
        svc = _make_service(tmp_path)
        svc._send_telegram_alert(_event())
        msg = svc._telegram.send_message_sync.call_args[0][0]
        assert "🐾" not in msg

    def test_mfcc_score_included_in_message(self, tmp_path):
        mock_settings = MagicMock()
        mock_settings.CONFIG.mfcc_profiles = []

        svc = _make_service(tmp_path)
        with patch("app.services.cloud_uploader_service.settings", mock_settings):
            svc._send_telegram_alert(_event(mfcc_scores={"dog": 0.91}))

        msg = svc._telegram.send_message_sync.call_args[0][0]
        assert "🐾 dog: 0.91" in msg

    def test_matched_score_shows_checkmark(self, tmp_path):
        mock_profile = MagicMock()
        mock_profile.target_label = "dog"
        mock_profile.threshold = 0.85
        mock_settings = MagicMock()
        mock_settings.CONFIG.mfcc_profiles = [mock_profile]

        svc = _make_service(tmp_path)
        with patch("app.services.cloud_uploader_service.settings", mock_settings):
            svc._send_telegram_alert(_event(mfcc_scores={"dog": 0.91}))

        msg = svc._telegram.send_message_sync.call_args[0][0]
        assert "✅" in msg

    def test_unmatched_score_shows_cross(self, tmp_path):
        mock_profile = MagicMock()
        mock_profile.target_label = "dog"
        mock_profile.threshold = 0.85
        mock_settings = MagicMock()
        mock_settings.CONFIG.mfcc_profiles = [mock_profile]

        svc = _make_service(tmp_path)
        with patch("app.services.cloud_uploader_service.settings", mock_settings):
            svc._send_telegram_alert(_event(mfcc_scores={"dog": 0.5}))

        msg = svc._telegram.send_message_sync.call_args[0][0]
        assert "❌" in msg


class TestEffectiveActionsGuard:
    def _patched_attempt(self, svc, event):
        """Call _attempt_direct_upload with a mocked provider that always succeeds."""
        import io
        import numpy as np
        wav_buf = io.BytesIO(b"\x00" * 44)
        svc._provider = MagicMock()
        svc._provider.upload_fileobj = MagicMock()
        svc._config.internet_enabled = True

        mock_settings = MagicMock()
        mock_settings.CONFIG.mfcc_profiles = []
        mock_settings.CONFIG.services.telegram_enabled = True

        with patch("app.services.cloud_uploader_service.settings", mock_settings):
            svc._attempt_direct_upload(wav_buf, event)

    def test_no_effective_actions_sends_telegram(self, tmp_path):
        svc = _make_service(tmp_path)
        self._patched_attempt(svc, _event())
        svc._telegram.send_message_sync.assert_called_once()

    def test_telegram_alert_in_effective_actions_sends(self, tmp_path):
        svc = _make_service(tmp_path)
        self._patched_attempt(svc, _event(effective_actions=["telegram_alert", "cloud_upload"]))
        svc._telegram.send_message_sync.assert_called_once()

    def test_telegram_alert_absent_suppresses_send(self, tmp_path):
        svc = _make_service(tmp_path)
        self._patched_attempt(svc, _event(effective_actions=["cloud_upload"]))
        svc._telegram.send_message_sync.assert_not_called()
