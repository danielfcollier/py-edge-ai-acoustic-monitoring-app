import os
import sys

# Prepend the vendored dependencies directory to sys.path so the .deb install
# works without requiring any system Python packages beyond python3.11.
_vendor = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(_vendor) and _vendor not in sys.path:
    sys.path.append(_vendor)

__version__ = "0.1.1"
