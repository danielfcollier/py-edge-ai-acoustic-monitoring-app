"""
Tests for SmartBufferSink (Batch B).
"""

import queue
from unittest.mock import MagicMock, patch, sentinel

import numpy as np
import pytest

from app.context import PipelineContext
from app.sinks.smart_buffer_sink import SmartBufferSink


def _make_settings(tmp_path, post_roll=2):
    m = MagicMock()
    m.AUDIO.SAMPLE_RATE = 48000
    svc = m.CONFIG.services
    svc.recording_output_path = tmp_path / "recordings"
    svc.metrics_csv_buffer_file = "metrics_buffer.csv"
    svc.recording_max_seconds = 60
    svc.recording_post_roll_seconds = post_roll
    return m


def _make_sink(tmp_path, app_context, raw_queue, post_roll=2):
    mock_settings = _make_settings(tmp_path, post_roll)
    with (
        patch("app.sinks.smart_buffer_sink.settings", mock_settings),
        patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM,
    ):
        MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
        sink = SmartBufferSink(app_context, raw_queue)
    sink._system_metrics_mock = MockSM
    return sink, mock_settings


def _ctx(audio=None):
    m = MagicMock()
    m.audio = audio if audio is not None else np.zeros(4800, dtype=np.float32)
    return m


class TestSmartBufferSinkRecording:
    def test_no_recording_without_trigger(self, tmp_path, app_context):
        raw_q = queue.Queue()
        sink, _ = _make_sink(tmp_path, app_context, raw_q)
        app_context.actions_to_take = []

        with patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM:
            MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
            with patch("app.sinks.smart_buffer_sink.time") as mock_time:
                mock_time.time.return_value = 0.0
                sink.handle(_ctx())

        assert raw_q.empty()
        assert not sink._is_recording

    def test_starts_recording_on_trigger(self, tmp_path, app_context):
        raw_q = queue.Queue()
        sink, _ = _make_sink(tmp_path, app_context, raw_q)
        app_context.actions_to_take = ["record_evidence"]

        with patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM:
            MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
            with patch("app.sinks.smart_buffer_sink.time") as mock_time:
                mock_time.time.return_value = 0.0
                sink.handle(_ctx())

        assert sink._is_recording
        assert raw_q.empty()  # not stopped yet

    def test_pushes_event_after_post_roll(self, tmp_path, app_context):
        raw_q = queue.Queue()
        sink, _ = _make_sink(tmp_path, app_context, raw_q, post_roll=2)
        chunk = np.ones(4800, dtype=np.float32)

        times = [0.0, 1.0, 4.0]  # start, fade begins, post-roll exceeded
        app_context.actions_to_take = ["record_evidence"]

        with patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM:
            MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
            with patch("app.sinks.smart_buffer_sink.time") as mock_time:
                mock_time.time.side_effect = times
                sink.handle(_ctx(chunk))                    # t=0: start recording
                app_context.actions_to_take = []
                sink.handle(_ctx(chunk))                    # t=1: trigger gone, fade starts
                sink.handle(_ctx(chunk))                    # t=4: post-roll exceeded, stop

        assert not raw_q.empty()

    def test_event_structure(self, tmp_path, app_context):
        raw_q = queue.Queue()
        sink, _ = _make_sink(tmp_path, app_context, raw_q, post_roll=2)
        chunk = np.ones(4800, dtype=np.float32)

        times = [0.0, 1.0, 4.0]
        app_context.actions_to_take = ["record_evidence"]

        with patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM:
            MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
            with patch("app.sinks.smart_buffer_sink.time") as mock_time:
                mock_time.time.side_effect = times
                sink.handle(_ctx(chunk))
                app_context.actions_to_take = []
                sink.handle(_ctx(chunk))
                sink.handle(_ctx(chunk))

        event = raw_q.get_nowait()
        assert "uuid" in event
        assert "audio_data" in event
        assert "sample_rate" in event
        assert event["metadata"]["calibrated"] is False
        assert isinstance(event["audio_data"], np.ndarray)

    def test_stops_after_max_duration(self, tmp_path, app_context):
        raw_q = queue.Queue()
        sink, _ = _make_sink(tmp_path, app_context, raw_q, post_roll=2)
        sink._max_duration_sec = 5  # override for test speed
        chunk = np.ones(4800, dtype=np.float32)

        # t=0: start, t=1: process chunk (populates buffer), t=6: max exceeded → stop
        times = [0.0, 1.0, 6.0]
        app_context.actions_to_take = ["record_evidence"]

        with patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM:
            MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
            with patch("app.sinks.smart_buffer_sink.time") as mock_time:
                mock_time.time.side_effect = times
                sink.handle(_ctx(chunk))   # t=0: start recording
                sink.handle(_ctx(chunk))   # t=1: still triggered, chunk added to buffer
                app_context.actions_to_take = []
                sink.handle(_ctx(chunk))   # t=6: max exceeded → stop → push to queue

        assert not raw_q.empty()

    def test_queue_full_does_not_raise(self, tmp_path, app_context):
        raw_q = queue.Queue(maxsize=1)
        raw_q.put(sentinel.QUEUE_BLOCKER)
        sink, _ = _make_sink(tmp_path, app_context, raw_q, post_roll=2)
        chunk = np.ones(4800, dtype=np.float32)

        app_context.actions_to_take = ["record_evidence"]

        with patch("app.sinks.smart_buffer_sink.SystemMetrics") as MockSM:
            MockSM.get_stats.return_value = (0.0, 0.0, 0.0, 0.0, 0.0)
            with patch("app.sinks.smart_buffer_sink.time") as mock_time:
                mock_time.time.side_effect = [0.0, 1.0, 4.0]
                sink.handle(_ctx(chunk))
                app_context.actions_to_take = []
                sink.handle(_ctx(chunk))
                sink.handle(_ctx(chunk))  # stop → queue full → must not raise
