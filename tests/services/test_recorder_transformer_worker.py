"""
Tests for RecorderTransformerWorker (Batch B).
Focuses on _transform() logic and thread handoff.
"""

import queue
import threading
from unittest.mock import MagicMock, patch, sentinel

from app.services.recorder_transformer_worker import RecorderTransformerWorker


def _raw_event(audio=None):
    return {
        "uuid": "aaaabbbb-cccc-dddd-eeee-ffffffffffff",  # str required (sliced in logs)
        "timestamp": sentinel.TIMESTAMP,
        "duration_sec": sentinel.DURATION,
        "sample_rate": sentinel.SAMPLE_RATE,
        "audio_data": audio if audio is not None else sentinel.RAW_AUDIO,
        "metadata": {
            "label": sentinel.LABEL,
            "confidence": sentinel.CONFIDENCE,
            "calibrated": False,
        },
    }


def _make_settings(save_calibrated=False, cal_file=sentinel.CAL_FILE):
    m = MagicMock()
    m.CONFIG.services.save_calibrated_wave = save_calibrated
    m.CONFIG.hardware.calibration_file = cal_file
    m.CONFIG.hardware.fir_num_taps = sentinel.FIR_TAPS
    m.AUDIO.SAMPLE_RATE = 48000  # int() called on this in _init_calibration
    m.HARDWARE.NOMINAL_SENSITIVITY_DBFS = sentinel.SENSITIVITY_DBFS
    m.HARDWARE.REFERENCE_DBSPL = sentinel.REFERENCE_DBSPL
    return m


def _make_worker(save_calibrated=False, mock_calibrator=None):
    raw_q = queue.Queue()
    upload_q = queue.Queue()
    mock_settings = _make_settings(save_calibrated=save_calibrated)

    with (
        patch("app.services.recorder_transformer_worker.settings", mock_settings),
        patch("app.services.recorder_transformer_worker.CalibratorTransformer") as MockCal,
    ):
        if mock_calibrator is not None:
            MockCal.return_value = mock_calibrator
        worker = RecorderTransformerWorker(raw_q, upload_q)

    return worker, raw_q, upload_q


class TestTransform:
    def test_passthrough_without_calibration(self):
        worker, _, _ = _make_worker(save_calibrated=False)
        event = _raw_event()

        result = worker._transform(event)

        assert result is event  # same object, untouched

    def test_applies_calibration_and_sets_flag(self):
        mock_cal = MagicMock()
        mock_cal.apply.return_value = sentinel.CAL_AUDIO

        worker, _, _ = _make_worker(save_calibrated=True, mock_calibrator=mock_cal)
        event = _raw_event()

        result = worker._transform(event)

        mock_cal.reset_state.assert_called_once()
        mock_cal.apply.assert_called_once_with(sentinel.RAW_AUDIO)
        assert result["metadata"]["calibrated"] is True
        assert result["audio_data"] is sentinel.CAL_AUDIO

    def test_preserves_existing_metadata_on_calibration(self):
        mock_cal = MagicMock()
        mock_cal.apply.return_value = sentinel.CAL_AUDIO

        worker, _, _ = _make_worker(save_calibrated=True, mock_calibrator=mock_cal)
        event = _raw_event()

        result = worker._transform(event)

        assert result["metadata"]["label"] is sentinel.LABEL
        assert result["metadata"]["confidence"] is sentinel.CONFIDENCE

    def test_falls_back_to_raw_on_calibration_error(self):
        mock_cal = MagicMock()
        mock_cal.apply.side_effect = RuntimeError("FIR failed")

        worker, _, _ = _make_worker(save_calibrated=True, mock_calibrator=mock_cal)
        event = _raw_event()

        result = worker._transform(event)

        assert result is event  # original event returned unchanged
        assert result["metadata"]["calibrated"] is False


class TestInitMfccScorers:
    def _cfg(self, labels_file=None, profile_file=None, target_label="dog"):
        m = MagicMock()
        m.labels_file = labels_file
        m.profile_file = profile_file
        m.target_label = target_label
        m.method = "nearest"
        return m

    def _make_worker_with_profiles(self, profiles, tmp_path=None):
        raw_q = queue.Queue()
        upload_q = queue.Queue()
        mock_settings = _make_settings()
        mock_settings.CONFIG.mfcc_profiles = profiles
        mock_settings.CONFIG.services.recording_output_path = tmp_path or sentinel.OUTPUT_DIR

        with (
            patch("app.services.recorder_transformer_worker.settings", mock_settings),
            patch("app.services.recorder_transformer_worker.CalibratorTransformer"),
        ):
            worker = RecorderTransformerWorker(raw_q, upload_q)

        return worker

    def test_loads_from_profile_file(self, tmp_path):
        from scripts.recognition.mfcc_core import save_profile
        import numpy as np

        npz = tmp_path / "dog.mfcc.npz"
        save_profile(npz, [np.ones(162), np.zeros(162)])

        cfg = self._cfg(profile_file="dog.mfcc.npz")
        worker = self._make_worker_with_profiles([cfg], tmp_path=tmp_path)

        assert len(worker._scorers) == 1
        assert worker._scorers[0][1].ready is True

    def test_loads_from_labels_file(self, tmp_path):
        import json
        import numpy as np

        (tmp_path / ".mfcc_labels.json").write_text(json.dumps({}))

        cfg = self._cfg(labels_file=".mfcc_labels.json")
        worker = self._make_worker_with_profiles([cfg], tmp_path=tmp_path)

        # No labeled examples → scorer not added (ready=False)
        assert len(worker._scorers) == 0

    def test_skips_missing_profile_file(self, tmp_path):
        cfg = self._cfg(profile_file="missing.npz")
        worker = self._make_worker_with_profiles([cfg], tmp_path=tmp_path)
        assert len(worker._scorers) == 0

    def test_skips_missing_labels_file(self, tmp_path):
        cfg = self._cfg(labels_file="missing.json")
        worker = self._make_worker_with_profiles([cfg], tmp_path=tmp_path)
        assert len(worker._scorers) == 0

    def test_profile_file_takes_precedence_over_labels_file(self, tmp_path):
        from scripts.recognition.mfcc_core import save_profile
        import numpy as np

        npz = tmp_path / "dog.mfcc.npz"
        save_profile(npz, [np.ones(162)])

        # profile_file set → should use it even if labels_file is also set
        cfg = self._cfg(profile_file="dog.mfcc.npz", labels_file="labels.json")
        worker = self._make_worker_with_profiles([cfg], tmp_path=tmp_path)

        assert len(worker._scorers) == 1


class TestApplyMfccScoring:
    def _worker_with_scorers(self, scorers):
        worker, _, _ = _make_worker()
        worker._scorers = scorers
        return worker

    def _cfg(self, target_label="dog", threshold=0.8, trigger_on_labels=None,
              actions_on_match=None, actions_on_no_match=None):
        m = MagicMock()
        m.target_label = target_label
        m.threshold = threshold
        m.trigger_on_labels = trigger_on_labels or []
        m.actions_on_match = actions_on_match or ["telegram_alert", "cloud_upload"]
        m.actions_on_no_match = actions_on_no_match or ["cloud_upload"]
        return m

    def test_no_scorers_returns_event_unchanged(self):
        worker = self._worker_with_scorers([])
        event = _raw_event()
        result = worker._apply_mfcc_scoring(event)
        assert result is event

    def test_score_above_threshold_sets_matched_actions(self):
        scorer = MagicMock()
        scorer.score_audio.return_value = 0.9
        cfg = self._cfg(threshold=0.8, actions_on_match=["telegram_alert", "cloud_upload"])
        worker = self._worker_with_scorers([(cfg, scorer)])

        result = worker._apply_mfcc_scoring(_raw_event())

        meta = result["metadata"]
        assert meta["mfcc_scores"] == {"dog": 0.9}
        assert set(meta["effective_actions"]) == {"telegram_alert", "cloud_upload"}

    def test_score_below_threshold_sets_no_match_actions(self):
        scorer = MagicMock()
        scorer.score_audio.return_value = 0.5
        cfg = self._cfg(threshold=0.8, actions_on_no_match=["cloud_upload"])
        worker = self._worker_with_scorers([(cfg, scorer)])

        result = worker._apply_mfcc_scoring(_raw_event())

        meta = result["metadata"]
        assert meta["mfcc_scores"] == {"dog": 0.5}
        assert meta["effective_actions"] == ["cloud_upload"]

    def test_trigger_on_labels_filters_non_matching_yamnet_label(self):
        scorer = MagicMock()
        cfg = self._cfg(trigger_on_labels=["Dog"])
        worker = self._worker_with_scorers([(cfg, scorer)])

        event = _raw_event()
        event["metadata"]["label"] = "Glass break"
        result = worker._apply_mfcc_scoring(event)

        scorer.score_audio.assert_not_called()
        assert result is event

    def test_trigger_on_labels_passes_matching_yamnet_label(self):
        scorer = MagicMock()
        scorer.score_audio.return_value = 0.85
        cfg = self._cfg(trigger_on_labels=["Dog"])
        worker = self._worker_with_scorers([(cfg, scorer)])

        event = _raw_event()
        event["metadata"]["label"] = "Dog"
        result = worker._apply_mfcc_scoring(event)

        scorer.score_audio.assert_called_once()
        assert "mfcc_scores" in result["metadata"]

    def test_score_none_skips_scorer(self):
        scorer = MagicMock()
        scorer.score_audio.return_value = None
        cfg = self._cfg()
        worker = self._worker_with_scorers([(cfg, scorer)])

        result = worker._apply_mfcc_scoring(_raw_event())
        assert result["metadata"].get("mfcc_scores") is None

    def test_multiple_scorers_intersect_actions(self):
        s1 = MagicMock()
        s1.score_audio.return_value = 0.9  # match
        cfg1 = self._cfg(target_label="dog", threshold=0.8,
                         actions_on_match=["telegram_alert", "cloud_upload"])

        s2 = MagicMock()
        s2.score_audio.return_value = 0.3  # no match
        cfg2 = self._cfg(target_label="cat", threshold=0.8,
                         actions_on_no_match=["cloud_upload"])

        worker = self._worker_with_scorers([(cfg1, s1), (cfg2, s2)])
        result = worker._apply_mfcc_scoring(_raw_event())

        meta = result["metadata"]
        assert "dog" in meta["mfcc_scores"]
        assert "cat" in meta["mfcc_scores"]
        # intersection: {"telegram_alert","cloud_upload"} & {"cloud_upload"} = {"cloud_upload"}
        assert meta["effective_actions"] == ["cloud_upload"]


class TestWorkerThread:
    def test_event_forwarded_to_upload_queue(self):
        worker, raw_q, upload_q = _make_worker(save_calibrated=False)
        event = _raw_event()

        done = threading.Event()
        original_transform = worker._transform

        def transform_and_signal(e):
            result = original_transform(e)
            done.set()
            return result

        worker._transform = transform_and_signal
        worker.start()

        raw_q.put(event)
        done.wait(timeout=2)
        worker.stop()

        assert not upload_q.empty()
        assert upload_q.get() is event

    def test_upload_queue_full_does_not_crash(self):
        worker, raw_q, upload_q = _make_worker(save_calibrated=False)
        upload_q.maxsize = 1
        upload_q.put(sentinel.QUEUE_BLOCKER)

        done = threading.Event()
        original_transform = worker._transform

        def transform_and_signal(e):
            result = original_transform(e)
            done.set()
            return result

        worker._transform = transform_and_signal
        worker.start()

        raw_q.put(_raw_event())
        done.wait(timeout=2)
        worker.stop()  # must not raise
