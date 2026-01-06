"""
Policy Engine Sink.
The 'Brain' of the system. Evaluates audio context against security policies.
Supports hot-reloading of configuration.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import datetime
import logging
import time
from pathlib import Path

import rule_engine
from umik_base_app import AudioSink

from ..context import PipelineContext
from ..settings import settings

logger = logging.getLogger(__name__)


class PolicyEngineSink(AudioSink):
    """
    Evaluates context against security policies using Rule Engine.
    """

    def __init__(self, context: PipelineContext):
        self._context = context
        self._policies = settings.CONFIG.policies
        self._compiled_rules = []

        # Privacy Flag File (in RAM Disk for speed/security)
        self._privacy_file = Path("/dev/shm/privacy_mode_active")

        # Hot Reload State
        self._last_config_check = 0.0
        try:
            self._last_policy_mtime = Path("security_policy.yaml").stat().st_mtime
        except FileNotFoundError:
            self._last_policy_mtime = 0.0

        self._compile_rules()
        logger.info("🧠 Policy Engine Initialized.")

    def _compile_rules(self):
        """Pre-compiles rule strings for performance."""
        self._compiled_rules = []  # Reset list
        for policy in self._policies:
            try:
                # Create a rule object from the string (e.g., "confidence > 0.8")
                rule = rule_engine.Rule(policy.condition)
                self._compiled_rules.append((policy, rule))
                logger.debug(f"Compiled Rule '{policy.name}': {policy.condition}")
            except rule_engine.RuleSyntaxError as e:
                logger.error(f"❌ Invalid Syntax in Policy '{policy.name}': {e}")

    def _check_environment_state(self):
        """Updates environment flags (Night Time, Privacy Mode)."""
        # 1. Check Privacy
        self._context.privacy_mode_active = self._privacy_file.exists()

        # 2. Check Night Time (10PM - 6AM)
        now = datetime.datetime.now().time()
        start = datetime.time(22, 0)
        end = datetime.time(6, 0)

        if start <= end:
            is_night = start <= now <= end
        else:
            is_night = start <= now or now <= end

        self._context.is_night_time = is_night

    def _check_config_update(self):
        """Reloads security_policy.yaml if changed."""
        if time.time() - self._last_config_check < 5.0:
            return

        try:
            current_mtime = Path("security_policy.yaml").stat().st_mtime
            if current_mtime > self._last_policy_mtime:
                logger.info("🔄 Policy file changed. Reloading...")
                settings.load_policy_file("security_policy.yaml")
                self._policies = settings.CONFIG.policies
                self._compile_rules()
                self._last_policy_mtime = current_mtime
                logger.info("✅ Policies reloaded successfully.")
        except FileNotFoundError:
            pass  # File might be temporarily missing during edit
        except Exception as e:
            logger.error(f"❌ Failed to reload policy: {e}")

        self._last_config_check = time.time()

    def handle_audio(self, audio_chunk, timestamp) -> None:
        # 1. Maintenance
        self._check_config_update()
        self._check_environment_state()

        # 2. Build Facts for Rule Engine
        facts = {
            "current_event_label": self._context.current_event_label,
            "current_confidence": self._context.current_confidence,
            "metrics": self._context.metrics,
            "is_night_time": self._context.is_night_time,
            "privacy_mode_active": self._context.privacy_mode_active,
        }

        # 3. Evaluate Rules
        triggered_actions = set()

        for policy, rule in self._compiled_rules:
            if self._context.privacy_mode_active and not policy.ignore_privacy:
                continue

            try:
                if rule.matches(facts):
                    logger.info(f"🚨 Rule Triggered: {policy.name}")
                    triggered_actions.update(policy.actions)
            except Exception:
                pass

        # 4. Update Context
        self._context.actions_to_take = list(triggered_actions)

        if self._context.actions_to_take:
            logger.info(f"⚡ Actions Queued: {self._context.actions_to_take}")
