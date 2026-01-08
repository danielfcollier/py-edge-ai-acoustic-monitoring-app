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

import numpy as np
from umik_base_app import AudioSink

from ..context import PipelineContext
from ..settings import settings

logger = logging.getLogger(__name__)


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

        # Cooldown State Management
        # Prevents spamming alerts for the same event (e.g. Barking for 10 minutes)
        self._alert_cooldown = settings.CONFIG.services.alert_cooldown_seconds
        self._last_trigger_times = {}  # {policy_name: timestamp}

        # Time Constraints
        self._day_start = settings.CONFIG.services.day_start_hour
        self._night_start = settings.CONFIG.services.night_start_hour

        logger.info(f"🧠 Policy Engine Initialized. Loaded {len(self._policies)} rules.")

    def handle_audio(self, audio_chunk: np.ndarray, timestamp: float) -> None:
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

        for policy in self._policies:
            try:
                # 1. Check Condition (Dynamic Eval)
                condition_met = eval(policy.condition, {"__builtins__": None}, eval_scope)

                if condition_met:
                    # 2. Check Cooldown (Only for alerting actions)
                    if self._should_trigger(policy.name, current_time):
                        # 3. Apply Actions
                        self._trigger_policy(policy)

                        # Update Cooldown
                        self._last_trigger_times[policy.name] = current_time
                    else:
                        # 🔍 DEBUG: Condition matched, but cooldown blocked it
                        remaining = int(
                            self._alert_cooldown - (current_time - self._last_trigger_times.get(policy.name, 0))
                        )
                        logger.debug(
                            f"   ⏳ Policy '{policy.name}' MATCHED but matches Cooldown ({remaining}s remaining)."
                        )
                else:
                    # 🔍 DEBUG: Condition failed (Optional: comment out if too verbose)
                    # logger.debug(f"   ❌ Policy '{policy.name}' condition not met.")
                    pass

            except Exception as e:
                # Log error but don't crash the pipeline
                logger.error(f"❌ Policy '{policy.name}' eval failed: {e}")

    def _should_trigger(self, policy_name: str, now: float) -> bool:
        """
        Determines if a policy is allowed to trigger based on cooldowns.

        :param policy_name: The unique name of the policy rule.
        :param now: Current timestamp.
        :return: True if the policy can trigger, False if it's on cooldown.
        """
        last_time = self._last_trigger_times.get(policy_name, 0)
        return (now - last_time) > self._alert_cooldown

    def _trigger_policy(self, policy):
        """
        Executes the side-effects of a matching policy.

        :param policy: The PolicyRule object that matched.
        """
        logger.info(f"🚨 Policy Triggered: {policy.name} [{self._context.current_event_label}]")
        logger.debug(f"   -> Actions Queued: {policy.actions}")

        # Extend the list of actions for downstream sinks (Recorder, Uploader)
        self._context.actions_to_take.extend(policy.actions)
