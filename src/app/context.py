"""
Defines the shared context passed between pipeline stages.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2026
"""

from collections import deque
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PipelineContext:
    """
    Holds the state for a single processing cycle and shared buffers.
    Injected into every Sink.
    """

    # --- Per-Chunk State (Reset every cycle) ---
    current_event_label: str | None = None
    current_confidence: float = 0.0

    # Policy decisions (e.g., ["telegram_alert", "cloud_upload"])
    actions_to_take: list[str] = field(default_factory=list)

    # Audio metrics (RMS, dBFS, Flux)
    metrics: dict = field(default_factory=dict)

    # --- Shared Buffers (Persist across cycles) ---
    # Stores last N chunks of raw audio for pre-roll
    audio_pre_buffer: deque = field(default_factory=lambda: deque(maxlen=100))

    # Accumulates 32-bit audio for recording
    recording_buffer: list[np.ndarray] = field(default_factory=list)
    is_recording: bool = False

    # --- System Flags ---
    privacy_mode_active: bool = False
    is_night_time: bool = False
