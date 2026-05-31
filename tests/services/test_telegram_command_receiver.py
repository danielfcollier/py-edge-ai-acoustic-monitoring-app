"""Tests for TelegramCommandReceiver — _parse_duration, /status, /dog, /noise, /audio, poll loop."""


import asyncio
import queue
from pathlib import Path
from unittest.mock import MagicMock, patch, sentinel

import pytest
from telegram.error import TimedOut

from app.services.telegram_command_receiver import (
    TelegramCommandReceiver,
    _parse_duration,
    _parse_event_datetime,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings():
    m = MagicMock()
    m.CONFIG.services.telegram_enabled = True
    m.TELEGRAM_CHAT_ID = "12345"
    m.TELEGRAM_BOT_TOKEN = sentinel.BOT_TOKEN
    return m


def _make_receiver(context=None, raw_queue=None, upload_queue=None):
    mock_settings = _make_settings()
    with (
        patch("app.services.telegram_command_receiver.settings", mock_settings),
        patch("app.services.telegram_command_receiver.TelegramBotClient") as MockTg,
    ):
        receiver = TelegramCommandReceiver(context, raw_queue, upload_queue)
    receiver._bot_client = MockTg.return_value
    return receiver


def _last_reply(receiver) -> str:
    return receiver._bot_client.send_message_sync.call_args[0][0]


# ---------------------------------------------------------------------------
# Unknown commands — must be silently ignored, no reply
# ---------------------------------------------------------------------------

class TestUnknownCommand:
    def _make_message(self, text: str, chat_id: str = "12345"):
        msg = MagicMock()
        msg.chat_id = chat_id
        msg.text = text
        return msg

    def test_unknown_command_sends_no_reply(self, app_context):
        receiver = _make_receiver(context=app_context)
        receiver._dispatch(self._make_message("/unknowncmd"))
        receiver._bot_client.send_message_sync.assert_not_called()

    def test_plain_text_sends_no_reply(self, app_context):
        receiver = _make_receiver(context=app_context)
        receiver._dispatch(self._make_message("hello there"))
        receiver._bot_client.send_message_sync.assert_not_called()

    def test_unauthorized_chat_sends_no_reply(self, app_context):
        receiver = _make_receiver(context=app_context)
        receiver._dispatch(self._make_message("/status", chat_id="99999"))
        receiver._bot_client.send_message_sync.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_hours(self):
        assert _parse_duration("2h") == 7200

    def test_minutes(self):
        assert _parse_duration("30m") == 1800

    def test_days(self):
        assert _parse_duration("1d") == 86400

    def test_plain_number_is_minutes(self):
        assert _parse_duration("45") == 2700

    def test_whitespace_stripped(self):
        assert _parse_duration("  2h  ") == 7200

    def test_invalid_returns_none(self):
        assert _parse_duration("abc") is None
        assert _parse_duration("") is None
        assert _parse_duration("2x") is None


# ---------------------------------------------------------------------------
# _parse_event_datetime
# ---------------------------------------------------------------------------

class TestParseEventDatetime:
    def test_no_args_returns_none(self):
        assert _parse_event_datetime([]) is None

    def test_valid_datetime(self):
        from datetime import datetime as dt
        result = _parse_event_datetime(["2026-05-31", "16:05"])
        assert result == dt(2026, 5, 31, 16, 5)

    def test_invalid_format_returns_none(self):
        assert _parse_event_datetime(["31-05-2026", "16:05"]) is None
        assert _parse_event_datetime(["not-a-date"]) is None


# ---------------------------------------------------------------------------
# /status command
# ---------------------------------------------------------------------------

class TestStatusCommand:
    def _patch_externals(self, privacy_active, stats):
        return (
            patch(
                "app.services.telegram_command_receiver.PrivacyMode",
                return_value=MagicMock(is_active=MagicMock(return_value=privacy_active)),
            ),
            patch(
                "app.services.telegram_command_receiver.SystemMetrics",
                **{"get_stats.return_value": stats},
            ),
        )

    def test_includes_event_label_and_confidence(self, app_context):
        app_context.current_event_label = "Dog"
        app_context.current_confidence = 0.85
        receiver = _make_receiver(context=app_context)

        pm_patch, sm_patch = self._patch_externals(False, (10.0, 45.0, 48.0, 38.0, 38.0))
        with pm_patch, sm_patch:
            receiver._handle_status()

        msg = _last_reply(receiver)
        assert "Dog" in msg
        assert "0.85" in msg

    def test_shows_privacy_active(self, app_context):
        receiver = _make_receiver(context=app_context)

        pm_patch, sm_patch = self._patch_externals(True, (0.0, 0.0, 0.0, 0.0, 0.0))
        with pm_patch, sm_patch:
            receiver._handle_status()

        assert "active" in _last_reply(receiver)
        assert "🔒" in _last_reply(receiver)

    def test_shows_privacy_inactive(self, app_context):
        receiver = _make_receiver(context=app_context)

        pm_patch, sm_patch = self._patch_externals(False, (0.0, 0.0, 0.0, 0.0, 0.0))
        with pm_patch, sm_patch:
            receiver._handle_status()

        assert "inactive" in _last_reply(receiver)
        assert "🔓" in _last_reply(receiver)

    def test_shows_queue_depths(self, app_context):
        raw_q = queue.Queue()
        upload_q = queue.Queue()
        raw_q.put(sentinel.ITEM_A)
        raw_q.put(sentinel.ITEM_B)
        upload_q.put(sentinel.ITEM_C)

        receiver = _make_receiver(context=app_context, raw_queue=raw_q, upload_queue=upload_q)

        pm_patch, sm_patch = self._patch_externals(False, (0.0, 0.0, 0.0, 0.0, 0.0))
        with pm_patch, sm_patch:
            receiver._handle_status()

        msg = _last_reply(receiver)
        assert "Raw: 2" in msg
        assert "Upload: 1" in msg

    def test_shows_system_metrics(self, app_context):
        receiver = _make_receiver(context=app_context)

        pm_patch, sm_patch = self._patch_externals(False, (42.5, 67.3, 51.8, 38.1, 38.1))
        with pm_patch, sm_patch:
            receiver._handle_status()

        msg = _last_reply(receiver)
        assert "42%" in msg   # cpu (42.5 rounds to 42 via banker's rounding)
        assert "67%" in msg   # ram
        assert "52°C" in msg  # temp
        assert "38%" in msg   # disk

    def test_replies_without_context(self):
        receiver = _make_receiver(context=None)

        pm_patch, sm_patch = self._patch_externals(False, (0.0, 0.0, 0.0, 0.0, 0.0))
        with pm_patch, sm_patch:
            receiver._handle_status()  # must not raise

        receiver._bot_client.send_message_sync.assert_called_once()

    def test_replies_without_queues(self, app_context):
        receiver = _make_receiver(context=app_context, raw_queue=None, upload_queue=None)

        pm_patch, sm_patch = self._patch_externals(False, (0.0, 0.0, 0.0, 0.0, 0.0))
        with pm_patch, sm_patch:
            receiver._handle_status()

        msg = _last_reply(receiver)
        assert "Raw: 0" in msg
        assert "Upload: 0" in msg

    def test_includes_version(self, app_context):
        from app import __version__

        receiver = _make_receiver(context=app_context)

        pm_patch, sm_patch = self._patch_externals(False, (0.0, 0.0, 0.0, 0.0, 0.0))
        with pm_patch, sm_patch:
            receiver._handle_status()

        assert __version__ in _last_reply(receiver)


# ---------------------------------------------------------------------------
# /dog command
# ---------------------------------------------------------------------------

class TestDogCommand:
    def test_appends_neighbor_dog_row(self, app_context):
        receiver = _make_receiver(context=app_context)

        with patch("app.services.telegram_command_receiver._append_metrics_csv") as mock_csv:
            receiver._handle_dog([])

        mock_csv.assert_called_once()
        _, row = mock_csv.call_args[0]
        assert row[2] == "NeighborDog"
        assert row[3] == "1.00"
        # all metric fields are zero strings
        for field in row[4:]:
            assert float(field) == 0.0

    def test_reply_confirms_registration(self, app_context):
        receiver = _make_receiver(context=app_context)

        with patch("app.services.telegram_command_receiver._append_metrics_csv"):
            receiver._handle_dog([])

        msg = _last_reply(receiver)
        assert "🐕" in msg
        assert "Neighbor dog registered" in msg

    def test_replies_error_on_write_failure(self, app_context):
        receiver = _make_receiver(context=app_context)

        with patch("app.services.telegram_command_receiver._append_metrics_csv",
                   side_effect=OSError("disk full")):
            receiver._handle_dog([])

        assert "❌" in _last_reply(receiver)

    def test_explicit_datetime_used_as_timestamp(self, app_context):
        receiver = _make_receiver(context=app_context)

        with patch("app.services.telegram_command_receiver._append_metrics_csv") as mock_csv:
            receiver._handle_dog(["2026-05-31", "16:05"])

        _, row = mock_csv.call_args[0]
        assert row[1].startswith("2026-05-31 16:05:00")

    def test_explicit_datetime_shown_in_reply(self, app_context):
        receiver = _make_receiver(context=app_context)

        with patch("app.services.telegram_command_receiver._append_metrics_csv"):
            receiver._handle_dog(["2026-05-31", "16:05"])

        assert "2026-05-31 16:05" in _last_reply(receiver)

    def test_invalid_datetime_replies_error(self, app_context):
        receiver = _make_receiver(context=app_context)

        receiver._handle_dog(["not-a-date"])

        msg = _last_reply(receiver)
        assert "❌" in msg
        assert "Invalid datetime" in msg


# ---------------------------------------------------------------------------
# /noise command
# ---------------------------------------------------------------------------

class TestNoiseCommand:
    def _privacy_patch(self, active: bool):
        return patch(
            "app.services.telegram_command_receiver.PrivacyMode",
            return_value=MagicMock(is_active=MagicMock(return_value=active)),
        )

    def test_always_logs_entry_even_when_privacy_active(self, app_context):
        receiver = _make_receiver(context=app_context)

        with (
            patch("app.services.telegram_command_receiver._append_metrics_csv") as mock_csv,
            self._privacy_patch(True),
        ):
            receiver._handle_noise([])

        mock_csv.assert_called_once()
        _, row = mock_csv.call_args[0]
        assert row[2] == "NoiseEvent"

    def test_privacy_active_blocks_monitoring(self, app_context):
        receiver = _make_receiver(context=app_context)

        with (
            patch("app.services.telegram_command_receiver._append_metrics_csv"),
            self._privacy_patch(True),
        ):
            receiver._handle_noise([])

        msg = _last_reply(receiver)
        assert "blocked" in msg
        assert receiver._noise_session is None

    def test_starts_new_session_when_idle(self, app_context):
        receiver = _make_receiver(context=app_context)
        mock_session = MagicMock()
        mock_session.csv_filename = "noise_2026-05-26.csv"

        with (
            patch("app.services.telegram_command_receiver._append_metrics_csv"),
            self._privacy_patch(False),
            patch("app.services.telegram_command_receiver.NoiseMonitorSession",
                  return_value=mock_session) as MockSession,
        ):
            receiver._handle_noise(["1h"])

        MockSession.assert_called_once()
        mock_session.start.assert_called_once()
        msg = _last_reply(receiver)
        assert "Monitoring for 1h" in msg
        assert "noise_2026-05-26.csv" in msg

    def test_resets_running_session(self, app_context):
        receiver = _make_receiver(context=app_context)
        existing = MagicMock()
        existing.csv_filename = "noise_2026-05-26.csv"
        receiver._noise_session = existing

        with (
            patch("app.services.telegram_command_receiver._append_metrics_csv"),
            self._privacy_patch(False),
        ):
            receiver._handle_noise(["2h"])

        existing.reset.assert_called_once_with(7200)
        assert "extended to 2h" in _last_reply(receiver)

    def test_invalid_duration_replies_error(self, app_context):
        receiver = _make_receiver(context=app_context)

        with patch("app.services.telegram_command_receiver._append_metrics_csv"):
            receiver._handle_noise(["xyz"])

        assert "❌" in _last_reply(receiver)

    def test_on_noise_done_sends_summary(self, app_context):
        receiver = _make_receiver(context=app_context)
        receiver._noise_session = MagicMock()

        receiver._on_noise_done("noise_2026-05-26.csv", 12)

        assert receiver._noise_session is None
        msg = _last_reply(receiver)
        assert "12 windows" in msg
        assert "noise_2026-05-26.csv" in msg

    def test_on_noise_done_privacy_stop_no_message(self, app_context):
        receiver = _make_receiver(context=app_context)
        receiver._noise_session = MagicMock()

        receiver._on_noise_done("noise_2026-05-26.csv", -1)

        assert receiver._noise_session is None
        receiver._bot_client.send_message_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Poll loop — TimedOut handling
# ---------------------------------------------------------------------------

class TestPollLoop:
    def test_timed_out_is_swallowed_silently(self):
        """
        telegram.error.TimedOut is the normal result of a long-poll expiry.
        The loop must continue without logging an error.
        """
        receiver = _make_receiver()
        call_count = 0

        async def fake_get_updates(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimedOut()
            receiver._stop_event.set()  # stop cleanly after second call
            return []

        mock_bot = MagicMock()
        mock_bot.get_updates = fake_get_updates
        mock_settings = _make_settings()

        async def run():
            with (
                patch("app.services.telegram_command_receiver.Bot", return_value=mock_bot),
                patch("app.services.telegram_command_receiver.settings", mock_settings),
            ):
                await receiver._poll_loop()

        with patch("app.services.telegram_command_receiver.logger") as mock_logger:
            asyncio.run(run())

        assert call_count == 2  # re-polled after timeout
        mock_logger.exception.assert_not_called()

    def test_unexpected_exception_is_logged(self):
        """Non-timeout exceptions must still trigger logger.exception."""
        receiver = _make_receiver()
        call_count = 0

        async def fake_get_updates(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("unexpected network error")
            receiver._stop_event.set()
            return []

        mock_bot = MagicMock()
        mock_bot.get_updates = fake_get_updates
        mock_settings = _make_settings()

        async def run():
            with (
                patch("app.services.telegram_command_receiver.Bot", return_value=mock_bot),
                patch("app.services.telegram_command_receiver.settings", mock_settings),
                patch("app.services.telegram_command_receiver.asyncio") as mock_asyncio,
            ):
                mock_asyncio.sleep = asyncio.sleep  # keep real sleep
                await receiver._poll_loop()

        with patch("app.services.telegram_command_receiver.logger") as mock_logger:
            asyncio.run(run())

        mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# _find_recording
# ---------------------------------------------------------------------------

_UUID = "550e8400-e29b-41d4-a716-446655440000"
_HASH = "550e8400"  # first 8 chars of _UUID


class TestFindRecording:
    def _make_wav(self, tmp_path: Path, uuid: str = _UUID) -> Path:
        name = f"evidence-2026-05-31T12-00-00-000000-{uuid}.wav"
        f = tmp_path / name
        f.touch()
        return f

    def test_matches_by_uuid_prefix(self, tmp_path):
        wav = self._make_wav(tmp_path)
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        assert receiver._find_recording(_HASH) == wav

    def test_returns_none_when_no_match(self, tmp_path):
        self._make_wav(tmp_path)
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        assert receiver._find_recording("deadbeef") is None

    def test_ignores_non_wav_files(self, tmp_path):
        (tmp_path / f"evidence-2026-05-31T12-00-00-000000-{_UUID}.ogg").touch()
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        assert receiver._find_recording(_HASH) is None


# ---------------------------------------------------------------------------
# /audio command
# ---------------------------------------------------------------------------

class TestAudioCommand:
    def _make_wav(self, tmp_path: Path, uuid: str = _UUID) -> Path:
        name = f"evidence-2026-05-31T12-00-00-000000-{uuid}.wav"
        f = tmp_path / name
        f.touch()
        return f

    def test_no_args_shows_usage(self, tmp_path):
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        receiver._handle_audio([])

        msg = _last_reply(receiver)
        assert "Usage" in msg
        assert "/audio" in msg

    def test_not_found_replies_error(self, tmp_path):
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        receiver._handle_audio(["deadbeef"])

        msg = _last_reply(receiver)
        assert "❌" in msg
        assert "deadbeef" in msg

    def test_found_sends_ogg(self, tmp_path):
        self._make_wav(tmp_path)
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        fake_audio = b"\x00" * 100

        with patch("app.services.telegram_command_receiver.soundfile") as mock_sf:
            mock_sf.read.return_value = (fake_audio, 44100)
            mock_sf.write = MagicMock(side_effect=lambda buf, *a, **kw: buf.write(fake_audio))
            receiver._handle_audio([_HASH])

        receiver._bot_client.send_audio_sync.assert_called_once()
        _, filename = receiver._bot_client.send_audio_sync.call_args[0]
        assert filename.endswith(".ogg")

    def test_conversion_error_replies_error(self, tmp_path):
        self._make_wav(tmp_path)
        receiver = _make_receiver()
        receiver._output_dir = tmp_path

        with patch("app.services.telegram_command_receiver.soundfile") as mock_sf:
            mock_sf.read.side_effect = RuntimeError("libsndfile error")
            receiver._handle_audio([_HASH])

        msg = _last_reply(receiver)
        assert "❌" in msg
