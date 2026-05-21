import collections
import sys
from pathlib import Path

import numpy as np
import pytest

from app.context import PipelineContext

# Ensure src/ is on the path so `scripts.*` resolves to src/scripts/,
# not the system-level scripts package or the tests/scripts/ namespace.
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


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
