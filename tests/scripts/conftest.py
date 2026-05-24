import sys
from pathlib import Path

# Ensure src/ is on the path before test_reporting.py is imported.
# Required because --import-mode=importlib loads test files before the parent
# conftest sys.path insertion takes effect for this subdirectory.
_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
