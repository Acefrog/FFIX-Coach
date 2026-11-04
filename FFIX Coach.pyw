"""FFIX Coach: double-click this file. One window: the coach (start, stop, pick the route),
the route editor, Memory setup and help. The coach's panel sits over the game."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
import window  # noqa: E402

sys.exit(window.run())
