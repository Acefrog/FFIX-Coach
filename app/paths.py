"""Where everything lives. The program is in this app folder; your own things sit one
folder up, next to FFIX Coach.pyw, so they're easy to find, copy and back up."""

from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent

CONFIG = ROOT / "config.json"            # settings and the game's memory addresses
LEARNED = ROOT / "learned.json"          # what the coach has learned (screens, fights)
ROUTES = ROOT / "routes"                 # one file per route
PROGRESS = ROOT / "progress"             # where each route was left (only if resume_progress is on)
BACKUPS = ROOT / "backups"               # older copies of routes, kept by the editor
FIGHTS_CUSTOM = ROOT / "fights_custom.json"   # fights you saved to the editor's fight list
STOP_FILE = ROOT / "stop_coach"           # made by the launcher's Stop coach button
LOGS = ROOT / "logs"                     # what the coach saw, for checking a run afterwards


def write_atomic(path: Path, text: str) -> None:
    """Write a file all at once (a temporary file swapped in), so the coach, the editor and
    the launcher never read half of one. Windows can refuse the swap for a moment while
    another program is reading the file: tried again, then written directly."""
    import os
    import time
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    path.write_text(text, encoding="utf-8")
    try:
        tmp.unlink()
    except OSError:
        pass


def log(name: str) -> Path:
    LOGS.mkdir(exist_ok=True)
    return LOGS / name
