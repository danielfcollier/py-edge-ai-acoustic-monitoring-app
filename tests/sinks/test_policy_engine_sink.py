"""
Tests for PolicyEngineSink — focused on the privacy gate (Batch A).
"""

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
