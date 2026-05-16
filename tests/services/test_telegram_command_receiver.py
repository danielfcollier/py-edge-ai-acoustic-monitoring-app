"""Tests for TelegramCommandReceiver — _parse_duration and /status command."""

import queue
from unittest.mock import MagicMock, patch, sentinel

import pytest

from app.services.telegram_command_receiver import (
    TelegramCommandReceiver,
    _parse_duration,
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
