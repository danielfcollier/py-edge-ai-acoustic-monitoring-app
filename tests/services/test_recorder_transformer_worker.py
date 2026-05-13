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
