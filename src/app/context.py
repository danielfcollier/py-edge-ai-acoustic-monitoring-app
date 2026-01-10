"""
Defines the shared context passed between pipeline stages.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

import collections
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineContext:
    """
    Shared state passed between Sinks in the Pipeline.
    Acts as the 'Bus' for data moving between Analysis, Policy, and Action stages.
    """

    # --- Audio Buffers ---
    # Stores raw audio chunks (numpy arrays) for pre-roll context.
    # Maxlen 50 @ 0.1s/chunk = ~5 seconds of pre-roll history.
    audio_pre_buffer: Any = field(default_factory=lambda: collections.deque(maxlen=50))

    # --- Inference State ---
    current_event_label: str = "Silence"
    current_confidence: float = 0.0
    should_infer: bool = True

    # --- Metrics State ---
    metrics: dict[str, float] = field(default_factory=dict)

    # --- Policy Decisions ---
    # e.g. ["cloud_upload", "record_evidence", "blink_led"]
    actions_to_take: list[str] = field(default_factory=list)
