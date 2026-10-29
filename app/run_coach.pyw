"""Runs the coach with no command window (started from FFIX Coach.pyw).

Start DuckStation first (it waits for it if not). The panel appears in DuckStation's
black bar. F10 quits. What the command window used to show
goes to logs/coach_output.txt.
"""
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from paths import log  # noqa: E402
out = open(log("coach_output.txt"), "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = out
print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} started ---")


def message(title, text, retry=False):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if retry:
            return messagebox.askretrycancel(title, text, parent=root)
        messagebox.showerror(title, text, parent=root)
    finally:
        root.destroy()


def wait_for_duckstation() -> bool:
    import main
    from memory import DuckStationRam, MemoryError_
    cfg = main.load_config()
    base = cfg.get("ram_base")
    while True:
        try:
            DuckStationRam(cfg.get("process_name", "duckstation"),
                           int(base, 16) if isinstance(base, str) else base).close()
            return True
        except MemoryError_ as e:
            print(f"Can't read DuckStation: {e}")
            if not message("FFIX Coach", "DuckStation isn't running (or no game is loaded).\n\n"
                                         "Start the game in DuckStation, then press Retry.", retry=True):
                return False


try:
    if wait_for_duckstation():
        import main
        sys.argv = [str(HERE / "main.py")] + sys.argv[1:]
        main.main()
except SystemExit:
    pass
except Exception:
    traceback.print_exc()
    message("FFIX Coach stopped", "Something went wrong:\n\n" + traceback.format_exc(limit=3)
            + "\nThe full details are in logs/coach_output.txt.")
