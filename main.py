"""Repository-level launcher for the KIS-MSJ auto-trader."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kis_msj.main import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
