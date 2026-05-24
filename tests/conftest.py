import collections
import sys
from pathlib import Path

import numpy as np
import pytest

from app.context import PipelineContext

# src/app/vendor/ inserts itself at sys.path[0] when app is imported, which causes
# src/app/vendor/scripts/ to shadow src/scripts/ and break `import scripts.reporting`.
# Force src/ back to position 0 after app imports are done.
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC in sys.path:
    sys.path.remove(_SRC)
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
