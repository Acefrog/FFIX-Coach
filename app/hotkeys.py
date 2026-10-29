"""Global hotkeys on Windows via RegisterHotKey (no admin rights or extra packages)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import sys
import threading

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK = {f"F{i}": 0x6F + i for i in range(1, 13)}  # F1 = 0x70
MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4}


def parse_key(name: str) -> tuple[int, int] | None:
    """"Shift+F8" -> (modifier bits, key code). None if it isn't a key we know."""
    *mods, key = [p.strip() for p in name.split("+")]
    if key.upper() not in VK or any(m.lower() not in MODS for m in mods):
        return None
    bits = 0
    for m in mods:
        bits |= MODS[m.lower()]
    return bits, VK[key.upper()]


class Hotkeys:
    """Registers keys on a background thread and puts their names on a queue."""

    def __init__(self, names: list[str]):
        self.events: "queue.Queue[str]" = queue.Queue()
        self.names = names
        self.failed: list[str] = []
        if sys.platform != "win32":
            return
        self._ready = threading.Event()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        self._ready.wait(2)

    def _loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        for i, name in enumerate(self.names, start=1):
            key = parse_key(name)
            if key is None or not user32.RegisterHotKey(None, i, MOD_NOREPEAT | key[0], key[1]):
                self.failed.append(name)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and 1 <= msg.wParam <= len(self.names):
                self.events.put(self.names[msg.wParam - 1])

    def poll(self) -> list[str]:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out
