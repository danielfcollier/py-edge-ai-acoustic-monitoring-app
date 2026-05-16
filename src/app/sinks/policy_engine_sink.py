"""
Policy Engine Sink.
Evaluates the acoustic context against user-defined security policies.
Triggers actions (e.g., Alert, Record) if conditions are met.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import logging
import time

from umik_base_app import AudioSink, PipelineContext as AudioCtx

from ..context import PipelineContext
from ..services.privacy_mode import PrivacyMode
from ..services.prometheus_service import PrometheusService
from ..services.telegram_bot_client import TelegramBotClient
from ..settings import settings

logger = logging.getLogger(__name__)

# Only these actions are rate-limited by alert_cooldown_seconds.
# Recording/upload actions must always fire so SmartBufferSink keeps the
# recording alive for the full duration of the event, not just post_roll_seconds.
_ALERT_ACTIONS = frozenset({"telegram_alert"})


class PolicyEngineSink(AudioSink):
    """
    The "Brain" of the pipeline.
    It reads the 'context' populated by the Feature Extractor and decides
    what actions should be taken based on the rules in 'security_policy.yaml'.
    """

    def __init__(self, context: PipelineContext):
        """
        Initializes the Policy Engine.

        :param context: The shared pipeline context containing audio metrics and AI labels.
        """
        self._context = context
        self._policies = settings.CONFIG.policies

        # Services
        self._telegram = TelegramBotClient()
        self._privacy = PrivacyMode()
        self._prometheus = PrometheusService()

        # Cooldown State Management
        # Prevents spamming Telegram alerts for the same event.
        # Only telegram_alert is gated — recording/upload actions are never blocked.
        self._alert_cooldown = settings.CONFIG.services.alert_cooldown_seconds
        self._last_alert_times: dict[str, float] = {}  # {policy_name: last_alert_timestamp}

        # Time Constraints
        self._day_start = settings.CONFIG.services.day_start_hour
        self._night_start = settings.CONFIG.services.night_start_hour

        logger.info(f"🧠 Policy Engine Initialized. Loaded {len(self._policies)} rules.")

    def handle(self, ctx: AudioCtx) -> None:
        """
        Evaluates all policies against the current audio context.

        :param audio_chunk: Raw audio data (unused here, but required by interface).
        :param timestamp: The occurrence time of the chunk.
        """
        # Reset actions for this frame (they are recalculated every cycle)
        self._context.actions_to_take = []

        # If silence, we skip detailed eval, but let's log it for debug parity
        if self._context.current_event_label == "Silence":
            logger.debug("💤 Context is Silence. Skipping policy eval.")
            return

        # Determine Time Context
        now_struct = time.localtime(time.time())
        current_hour = now_struct.tm_hour

        # Check if it is currently "Night" based on settings
        # e.g., if Night starts at 22 and Day starts at 6:
        # Night is >= 22 OR < 6
        is_night = (current_hour >= self._night_start) or (current_hour < self._day_start)

        # Prepare the Evaluation Scope (Variables available in YAML 'condition')
        eval_scope = {
            "current_event_label": self._context.current_event_label,
            "current_confidence": self._context.current_confidence,
            "metrics": self._context.metrics,
            # Time Helpers
            "current_hour": current_hour,
            "is_night": is_night,
            "is_day": not is_night,
        }

        # 🔍 DEBUG: Show exactly what the engine sees this frame
        logger.debug(
            f"🔍 EVAL CONTEXT | Time: {current_hour}h ({'Night' if is_night else 'Day'}) | "
            f"Label: '{eval_scope['current_event_label']}' ({eval_scope['current_confidence']:.2f}) | "
            f"dB: {eval_scope['metrics'].get('dbspl', 0):.1f}"
        )

        current_time = time.time()
        is_privacy_active = self._privacy.is_active()

        for policy in self._policies:
            if is_privacy_active and not policy.ignore_privacy:
                logger.debug(f"🔒 Privacy active — skipping policy '{policy.name}'.")
                continue

            try:
                # 1. Check Condition (Dynamic Eval)
                condition_met = eval(policy.condition, {"__builtins__": None}, eval_scope)

                if condition_met:
                    # 2. Alert-specific cooldown check
                    can_alert = self._should_alert(policy.name, current_time)
                    if not can_alert and "telegram_alert" in policy.actions:
                        remaining = int(
                            self._alert_cooldown - (current_time - self._last_alert_times.get(policy.name, 0))
                        )
                        logger.debug(f"   ⏳ Alert cooldown for '{policy.name}' ({remaining}s remaining).")

                    # 3. Apply Actions (recording/upload always fire; alerts are rate-limited)
                    self._trigger_policy(policy, can_alert)

                    if can_alert and "telegram_alert" in policy.actions:
                        self._last_alert_times[policy.name] = current_time

            except Exception as e:
                # Log error but don't crash the pipeline
                logger.error(f"❌ Policy '{policy.name}' eval failed: {e}")

    def _should_alert(self, policy_name: str, now: float) -> bool:
        """Returns True if enough time has passed since the last Telegram alert for this policy."""
        last_time = self._last_alert_times.get(policy_name, float("-inf"))
        return (now - last_time) > self._alert_cooldown

    def _trigger_policy(self, policy, can_alert: bool) -> None:
        """
        Applies a matched policy's actions to the context.

        Non-alert actions (record_evidence, cloud_upload, log_metadata) are always
        applied so that SmartBufferSink keeps the recording alive for the full event
        duration. Alert actions (telegram_alert) are only sent when can_alert=True.
        """
        self._prometheus.record_event(self._context.current_event_label)

        # Recording/upload actions: never rate-limited
        non_alert_actions = [a for a in policy.actions if a not in _ALERT_ACTIONS]
        if non_alert_actions:
            self._context.actions_to_take.extend(non_alert_actions)
            logger.info(f"🚨 Policy matched: {policy.name} [{self._context.current_event_label}]")
            logger.debug(f"   -> Actions: {non_alert_actions}")

        # Alert actions: rate-limited by cooldown
        if can_alert and "telegram_alert" in policy.actions:
            self._send_telegram_alert(policy)

    def _send_telegram_alert(self, policy):
        """
        Constructs and sends an immediate Telegram alert for the triggered policy.
        """
        label = self._context.current_event_label
        conf = self._context.current_confidence

        lines = [
            "🚨 **Policy Triggered**",
            f"🛡️ Rule: {policy.name}",
            f"👂 Detected: {label} ({conf:.2f})",
        ]

        msg = "\n".join(lines)
        self._telegram.send_message_sync(msg)
