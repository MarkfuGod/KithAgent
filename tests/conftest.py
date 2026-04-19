"""Shared pytest fixtures.

Puts the repo root on sys.path so `import src` and `import agent_sys`
both work whether or not the package is `pip install -e`'d.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
