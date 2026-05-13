import collections

import numpy as np
import pytest

from app.context import PipelineContext


@pytest.fixture
def app_context():
    ctx = PipelineContext()
    ctx.current_event_label = "Dog"
    ctx.current_confidence = 0.9
    ctx.metrics = {"rms": 0.05, "flux": 10.0, "dbspl": 65.0}
    return ctx


@pytest.fixture
def audio_chunk():
    return np.zeros(4800, dtype=np.float32)  # 0.1 s @ 48 kHz
