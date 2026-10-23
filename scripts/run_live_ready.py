"""Launch from the repository without requiring package installation."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kis_msj.live_startup import launch_live_trader


if __name__ == "__main__":
    raise SystemExit(launch_live_trader())
