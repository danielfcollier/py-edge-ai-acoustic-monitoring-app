"""
Tests for PolicyEngineSink — privacy gate (Batch A) and alert cooldown.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.context import PipelineContext
from app.settings import PolicyRule
from app.sinks.policy_engine_sink import PolicyEngineSink


def _make_rule(name, condition, actions, ignore_privacy=False):
    return PolicyRule(name=name, condition=condition, actions=actions, ignore_privacy=ignore_privacy)


def _make_sink(context, policies, privacy_active):
    mock_settings = MagicMock()
    mock_settings.CONFIG.policies = policies
    mock_settings.CONFIG.services.alert_cooldown_seconds = 0
    mock_settings.CONFIG.services.day_start_hour = 6
    mock_settings.CONFIG.services.night_start_hour = 22

    with (
        patch("app.sinks.policy_engine_sink.settings", mock_settings),
        patch("app.sinks.policy_engine_sink.TelegramBotClient"),
        patch("app.sinks.policy_engine_sink.PrivacyMode") as MockPrivacy,
    ):
        MockPrivacy.return_value.is_active.return_value = privacy_active
        sink = PolicyEngineSink(context)

    # Keep the patched privacy mock alive on the sink for later calls
    sink._privacy = MagicMock()
    sink._privacy.is_active.return_value = privacy_active
    return sink


def _audio_ctx():
    ctx = MagicMock()
    return ctx


class TestPrivacyGate:
    def test_normal_policy_skipped_when_privacy_active(self, app_context):
        rule = _make_rule("noise", "True", ["record_evidence"], ignore_privacy=False)
        sink = _make_sink(app_context, [rule], privacy_active=True)

        sink.handle(_audio_ctx())

        assert "record_evidence" not in app_context.actions_to_take

    def test_critical_policy_fires_despite_privacy(self, app_context):
        rule = _make_rule("critical", "True", ["record_evidence"], ignore_privacy=True)
        sink = _make_sink(app_context, [rule], privacy_active=True)

        sink.handle(_audio_ctx())

        assert "record_evidence" in app_context.actions_to_take

    def test_all_policies_fire_when_privacy_inactive(self, app_context):
        rules = [
            _make_rule("r1", "True", ["record_evidence"], ignore_privacy=False),
            _make_rule("r2", "True", ["cloud_upload"], ignore_privacy=False),
        ]
        sink = _make_sink(app_context, rules, privacy_active=False)

        sink.handle(_audio_ctx())

        assert "record_evidence" in app_context.actions_to_take
        assert "cloud_upload" in app_context.actions_to_take

    def test_policy_condition_false_does_not_trigger(self, app_context):
        rule = _make_rule("never", "False", ["record_evidence"], ignore_privacy=True)
        sink = _make_sink(app_context, [rule], privacy_active=False)

        sink.handle(_audio_ctx())

        assert "record_evidence" not in app_context.actions_to_take

    def test_actions_reset_each_frame(self, app_context):
        rule = _make_rule("r", "True", ["record_evidence"], ignore_privacy=True)
        sink = _make_sink(app_context, [rule], privacy_active=False)

        sink.handle(_audio_ctx())
        assert "record_evidence" in app_context.actions_to_take

        # Second call resets actions first
        sink.handle(_audio_ctx())
        assert app_context.actions_to_take.count("record_evidence") == 1


def _mock_time(t: float):
    """Returns a time mock fixed at t, with localtime set to daytime (hour=12)."""
    m = MagicMock()
    m.time.return_value = t
    m.localtime.return_value = MagicMock(tm_hour=12)
    return m


class TestAlertCooldown:
    def test_telegram_fires_on_first_match(self, app_context):
        rule = _make_rule("r", "True", ["telegram_alert", "record_evidence"])
        sink = _make_sink(app_context, [rule], privacy_active=False)

        with patch("app.sinks.policy_engine_sink.time", _mock_time(0.0)):
            sink.handle(_audio_ctx())

        sink._telegram.send_message_sync.assert_called_once()
        assert "record_evidence" in app_context.actions_to_take

    def test_telegram_blocked_within_cooldown(self, app_context):
        rule = _make_rule("r", "True", ["telegram_alert", "record_evidence"])
        sink = _make_sink(app_context, [rule], privacy_active=False)
        sink._alert_cooldown = 60

        with patch("app.sinks.policy_engine_sink.time", _mock_time(0.0)):
            sink.handle(_audio_ctx())

        with patch("app.sinks.policy_engine_sink.time", _mock_time(30.0)):
            sink.handle(_audio_ctx())

        assert sink._telegram.send_message_sync.call_count == 1  # only fired once

    def test_recording_actions_always_fire_during_cooldown(self, app_context):
        rule = _make_rule("r", "True", ["telegram_alert", "record_evidence", "cloud_upload"])
        sink = _make_sink(app_context, [rule], privacy_active=False)
        sink._alert_cooldown = 60

        with patch("app.sinks.policy_engine_sink.time", _mock_time(0.0)):
            sink.handle(_audio_ctx())

        with patch("app.sinks.policy_engine_sink.time", _mock_time(30.0)):
            sink.handle(_audio_ctx())

        # Recording actions must fire on every matched frame — not gated by cooldown
        assert "record_evidence" in app_context.actions_to_take
        assert "cloud_upload" in app_context.actions_to_take

    def test_telegram_refires_after_cooldown_expires(self, app_context):
        rule = _make_rule("r", "True", ["telegram_alert", "record_evidence"])
        sink = _make_sink(app_context, [rule], privacy_active=False)
        sink._alert_cooldown = 60

        with patch("app.sinks.policy_engine_sink.time", _mock_time(0.0)):
            sink.handle(_audio_ctx())

        with patch("app.sinks.policy_engine_sink.time", _mock_time(61.0)):
            sink.handle(_audio_ctx())

        assert sink._telegram.send_message_sync.call_count == 2

    def test_cooldown_is_per_policy(self, app_context):
        r1 = _make_rule("r1", "True", ["telegram_alert"])
        r2 = _make_rule("r2", "True", ["telegram_alert"])
        sink = _make_sink(app_context, [r1, r2], privacy_active=False)
        sink._alert_cooldown = 60

        with patch("app.sinks.policy_engine_sink.time", _mock_time(0.0)):
            sink.handle(_audio_ctx())  # both fire

        with patch("app.sinks.policy_engine_sink.time", _mock_time(61.0)):
            # Manually put r1 on cooldown, r2 not
            sink._last_alert_times["r1"] = 60.0  # last fired at t=60, expires at t=120
            sink.handle(_audio_ctx())

        # r1 should be blocked, r2 should fire again
        calls = sink._telegram.send_message_sync.call_count
        assert calls == 3  # 2 at t=0, 1 (r2 only) at t=61
