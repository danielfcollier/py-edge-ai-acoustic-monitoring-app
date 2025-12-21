"""
Defines the protocols for audio processing components and data sinks.

This module establishes the contracts for AudioProcessor (transformers) and
AudioSink (consumers) to ensure modularity and type safety in the audio pipeline.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2025
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

import numpy as np

from abc import ABC, abstractmethod
from typing import Any, List, Optional

class AudioRecorder(ABC):
    """
    Interface for any device that records audio.
    Crucial: This knows NOTHING about 'sounddevice' or USB IDs.
    """
    @abstractmethod
    def start_stream(self) -> None:
        pass

    @abstractmethod
    def get_data(self) -> Any:
        pass

class AudioClassifier(ABC):
    """
    Interface for any AI model that classifies audio.
    Crucial: This knows NOTHING about TensorFlow or Yamnet files.
    """
    @abstractmethod
    def classify(self, audio_data: Any) -> List[Any]:
        pass

@runtime_checkable
class AudioProcessor(Protocol):
    """
    Protocol for components that transform audio data (e.g., Calibrator, Filter).
    Input: Raw Audio -> Output: Processed Audio
    """

    def process_audio(self, audio_chunk: np.ndarray) -> np.ndarray: ...


@runtime_checkable
class AudioSink(Protocol):
    """
    Protocol for components that consume audio data (e.g., Recorder, Meter, GUI).
    Input: Final Audio -> Output: None (Side Effect)
    """

    def handle_audio(self, audio_chunk: np.ndarray, timestamp: datetime) -> None: ...
