"""Run the PQL-A00 simulator from a repository checkout."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from highend_server.simulation.__main__ import main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(main())
