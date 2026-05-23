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
from dataclasses import dataclass, field

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
_RECORDING_ACTIONS = frozenset({"record_evidence", "cloud_upload"})


@dataclass
class _CumulativeEntry:
    """Aggregated data for one policy within a cumulative notification window."""
    count: int = 0
    peak_dbspl: float = 0.0
    peak_rms: float = 0.0
    peak_flux: float = 0.0
    best_label: str = ""
    best_confidence: float = 0.0
    top_classes: list = field(default_factory=list)


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

        # Per-recording deduplication: suppress repeated log lines for the same policy
        # within a single recording session. Cleared when recording ends.
        self._logged_this_recording: set[str] = set()

        # Time Constraints
        self._day_start = settings.CONFIG.services.day_start_hour
        self._night_start = settings.CONFIG.services.night_start_hour

        # Notification mode
        svc = settings.CONFIG.services
        self._notification_mode = svc.telegram_notification_mode
        self._cumulative_window = svc.telegram_cumulative_window_minutes * 60.0
        self._min_display_confidence = svc.alert_min_display_confidence
        self._cumulative_buffer: dict[str, _CumulativeEntry] = {}
        self._cumulative_window_start: float = time.time()

        logger.info(
            f"🧠 Policy Engine Initialized. Loaded {len(self._policies)} rules. "
            f"Notifications: {self._notification_mode}"
            + (f" (window {svc.telegram_cumulative_window_minutes}min)" if self._notification_mode == "cumulative" else "")
        )

    def handle(self, ctx: AudioCtx) -> None:
        """
        Evaluates all policies against the current audio context.

        :param audio_chunk: Raw audio data (unused here, but required by interface).
        :param timestamp: The occurrence time of the chunk.
        """
        # Flush cumulative buffer if window has elapsed
        if self._notification_mode == "cumulative":
            if time.time() - self._cumulative_window_start >= self._cumulative_window:
                self._flush_cumulative()

        # Reset actions for this frame (they are recalculated every cycle)
        self._context.actions_to_take = []

        # Clear per-recording dedup set when not recording
        if not self._context.is_recording:
            self._logged_this_recording.clear()

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
                    self._trigger_policy(policy, can_alert, current_time)

            except Exception as e:
                # Log error but don't crash the pipeline
                logger.error(f"❌ Policy '{policy.name}' eval failed: {e}")

    def _should_alert(self, policy_name: str, now: float) -> bool:
        """Returns True if enough time has passed since the last Telegram alert for this policy."""
        last_time = self._last_alert_times.get(policy_name, float("-inf"))
        return (now - last_time) > self._alert_cooldown

    def _trigger_policy(self, policy, can_alert: bool, current_time: float) -> None:
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
            if policy.name not in self._logged_this_recording:
                m = self._context.metrics
                top = self._context.top_classes
                top_str = "  ".join(f"{n}({s:.2f})" for n, s in top)
                logger.info(
                    f"🚨 Policy matched: {policy.name} | "
                    f"{self._context.current_event_label}({self._context.current_confidence:.2f}) | "
                    f"dBSPL={m.get('dbspl', 0):.1f}  RMS={m.get('rms', 0):.4f}  flux={m.get('flux', 0):.1f} | "
                    f"top5: {top_str}"
                )
                self._logged_this_recording.add(policy.name)
            logger.debug(f"   -> Actions: {non_alert_actions}")

        # Alert actions: for recording-associated policies, send only on the first
        # trigger frame (before SmartBufferSink starts the recording). For
        # non-recording policies, use the standard cooldown.
        if can_alert and "telegram_alert" in policy.actions:
            is_recording_policy = any(a in _RECORDING_ACTIONS for a in policy.actions)
            if is_recording_policy and self._context.is_recording:
                return
            if self._notification_mode == "cumulative":
                self._accumulate_alert(policy)
            else:
                self._send_telegram_alert(policy)
            self._last_alert_times[policy.name] = current_time

    def _accumulate_alert(self, policy) -> None:
        """Adds a policy match to the cumulative buffer instead of sending immediately."""
        label = self._context.current_event_label
        conf = self._context.current_confidence
        m = self._context.metrics

        entry = self._cumulative_buffer.setdefault(policy.name, _CumulativeEntry())
        entry.count += 1

        dbspl = m.get("dbspl", 0.0)
        rms = m.get("rms", 0.0)
        flux = m.get("flux", 0.0)

        if dbspl > entry.peak_dbspl:
            entry.peak_dbspl = dbspl
        if rms > entry.peak_rms:
            entry.peak_rms = rms
        if flux > entry.peak_flux:
            entry.peak_flux = flux
        if conf > entry.best_confidence:
            entry.best_confidence = conf
            entry.best_label = label
            entry.top_classes = list(self._context.top_classes)

    def _flush_cumulative(self) -> None:
        """Sends a batched summary of all accumulated alerts and resets the buffer."""
        if not self._cumulative_buffer:
            self._cumulative_window_start = time.time()
            return

        window_min = int(self._cumulative_window // 60)
        lines = [f"🔔 **Alert Summary** (last {window_min} min)", ""]

        for policy_name, entry in self._cumulative_buffer.items():
            lines.append(f"🛡️ {policy_name}  ×{entry.count}")
            lines.append(
                f"   📊 dBSPL: {entry.peak_dbspl:.1f}  RMS: {entry.peak_rms:.4f}  flux: {entry.peak_flux:.1f}"
            )
            if entry.best_confidence >= self._min_display_confidence and entry.best_label:
                top_str = ", ".join(f"{n} ({s:.2f})" for n, s in entry.top_classes[:3])
                lines.append(f"   👂 {entry.best_label} ({entry.best_confidence:.2f})  —  {top_str}")
            lines.append("")

        lines.append(f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self._telegram.send_message_sync("\n".join(lines))

        self._cumulative_buffer.clear()
        self._cumulative_window_start = time.time()

    def _send_telegram_alert(self, policy) -> None:
        """Constructs and sends an immediate Telegram alert for the triggered policy."""
        label = self._context.current_event_label
        conf = self._context.current_confidence
        m = self._context.metrics

        lines = [
            "🚨 **Policy Triggered**",
            f"🛡️ Rule: {policy.name}",
            f"🕐 {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"📊 dBSPL: {m.get('dbspl', 0):.1f}  RMS: {m.get('rms', 0):.4f}  flux: {m.get('flux', 0):.1f}",
        ]

        if conf >= self._min_display_confidence:
            top = self._context.top_classes
            top_lines = "\n".join(f"  {i+1}. {n} ({s:.2f})" for i, (n, s) in enumerate(top))
            lines += [
                "",
                f"👂 Detected: {label} ({conf:.2f})",
                f"🔍 Top classes:\n{top_lines}",
            ]

        self._telegram.send_message_sync("\n".join(lines))
