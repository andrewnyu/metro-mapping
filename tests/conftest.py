"""Make `src/` importable so `import metro...` works under a bare `pytest`.

The scripts insert `src/` into sys.path themselves; the test suite needs the
same until the project is packaged with a pyproject.toml (see README Roadmap).
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
