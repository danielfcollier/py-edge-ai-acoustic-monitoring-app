"""
E2E tests for Telegram bot communication.

Requires: TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in .env.

What is tested:
  - TelegramBotClient can send a real message (API connectivity + credentials).
  - _handle_status produces a correctly structured reply.
  - _handle_status message reaches Telegram (real send).
  - /privacy on / off cycle sends correct acknowledgement messages.
"""

from unittest.mock import patch

import pytest

from app.context import PipelineContext
from app.services.telegram_bot_client import TelegramBotClient
from app.services.telegram_command_receiver import TelegramCommandReceiver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receiver(settings, context=None, raw_queue=None, upload_queue=None):
    with patch("app.services.telegram_command_receiver.settings", settings):
        receiver = TelegramCommandReceiver(context=context, raw_queue=raw_queue, upload_queue=upload_queue)
    return receiver


def _capture_reply(receiver):
    """Patch _reply and return the list of captured messages."""
    messages = []
    original = receiver._reply

    def _capture(text):
        messages.append(text)
        return original(text)

    receiver._reply = _capture
    return messages


# ---------------------------------------------------------------------------
# Bot connectivity
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_send_message_returns_true(require_telegram):
    """TelegramBotClient.send_message_sync succeeds against the live API."""
    client = TelegramBotClient()
    result = client.send_message_sync("🧪 [E2E] ai-acoustic-monitor connectivity check — ignore")
    assert result is True


# ---------------------------------------------------------------------------
# /status command
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_status_reply_structure(require_telegram):
    """_handle_status sends a message containing all expected sections."""
    ctx = PipelineContext()
    ctx.current_event_label = "Dog"
    ctx.current_confidence = 0.87

    receiver = _make_receiver(require_telegram, context=ctx)

    captured = []

    with (
        patch(
            "app.services.telegram_command_receiver.SystemMetrics.get_stats",
            return_value=(25.0, 42.0, 52.5, 38.0, 38.0),
        ),
        patch(
            "app.services.telegram_command_receiver.PrivacyMode",
            return_value=type("PM", (), {"is_active": lambda self: False})(),
        ),
        patch.object(receiver._bot_client, "send_message_sync", side_effect=lambda t: captured.append(t) or True),
    ):
        receiver._handle_status()

    assert len(captured) == 1
    msg = captured[0]
    assert "Dog" in msg,       "label missing"
    assert "0.87" in msg,      "confidence missing"
    assert "Privacy" in msg,   "privacy section missing"
    assert "Raw:" in msg,      "queue depth missing"
    assert "CPU" in msg,       "CPU metric missing"
    assert "Disk" in msg,      "disk metric missing"


@pytest.mark.e2e
def test_status_sends_to_real_telegram(require_telegram):
    """_handle_status delivers a real message to the configured Telegram chat."""
    ctx = PipelineContext()
    ctx.current_event_label = "E2E-Test"
    ctx.current_confidence = 1.0

    receiver = _make_receiver(require_telegram, context=ctx)

    with (
        patch(
            "app.services.telegram_command_receiver.SystemMetrics.get_stats",
            return_value=(0.0, 0.0, 0.0, 0.0, 0.0),
        ),
        patch(
            "app.services.telegram_command_receiver.PrivacyMode",
            return_value=type("PM", (), {"is_active": lambda self: False})(),
        ),
    ):
        receiver._handle_status()  # raises on API failure


@pytest.mark.e2e
def test_status_without_context(require_telegram):
    """_handle_status does not raise when context is None."""
    receiver = _make_receiver(require_telegram, context=None)

    with (
        patch(
            "app.services.telegram_command_receiver.SystemMetrics.get_stats",
            return_value=(0.0, 0.0, 0.0, 0.0, 0.0),
        ),
        patch(
            "app.services.telegram_command_receiver.PrivacyMode",
            return_value=type("PM", (), {"is_active": lambda self: False})(),
        ),
    ):
        receiver._handle_status()


# ---------------------------------------------------------------------------
# /privacy command
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_privacy_on_sends_confirmation(require_telegram, tmp_path, monkeypatch):
    """_handle_privacy('on', '30m') sends a confirmation and activates the mode."""
    state_file = tmp_path / "privacy_mode"
    monkeypatch.setattr(require_telegram.CONFIG.services, "privacy_mode_state_file", state_file)

    receiver = _make_receiver(require_telegram)
    captured = _capture_reply(receiver)

    with patch.object(receiver._bot_client, "send_message_sync", side_effect=lambda t: captured.append(t) or True):
        receiver._handle_privacy(["on", "30m"])

    assert any("30m" in m or "ON" in m or "on" in m.lower() for m in captured)
    assert state_file.exists()


@pytest.mark.e2e
def test_privacy_off_sends_confirmation(require_telegram, tmp_path, monkeypatch):
    """_handle_privacy('off') sends a confirmation and deactivates the mode."""
    state_file = tmp_path / "privacy_mode"
    state_file.write_text(str(9_999_999_999.0))  # far-future expiry
    monkeypatch.setattr(require_telegram.CONFIG.services, "privacy_mode_state_file", state_file)

    receiver = _make_receiver(require_telegram)
    captured = []

    with patch.object(receiver._bot_client, "send_message_sync", side_effect=lambda t: captured.append(t) or True):
        receiver._handle_privacy(["off"])

    assert any("OFF" in m or "off" in m.lower() or "inactive" in m.lower() for m in captured)
    assert not state_file.exists()
