"""FFIX Coach: double-click this file. A small window to start the coach or open the
route editor (no command window)."""
import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

HERE = Path(__file__).resolve().parent
APP = HERE / "app"
sys.path.insert(0, str(APP))
import routes  # noqa: E402

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# Run the others with pythonw, so they don't open a command window either.
PY = Path(sys.executable)
if PY.name.lower() == "python.exe" and (PY.parent / "pythonw.exe").exists():
    PY = PY.parent / "pythonw.exe"

procs = {}


def start(name, script):
    p = procs.get(name)
    if p is not None and p.poll() is None:
        status.set(f"The {name} is already open.")
        return
    procs[name] = subprocess.Popen([str(PY), str(APP / script)], cwd=str(HERE), creationflags=NO_WINDOW)
    status.set({"coach": "Coach started. F10 quits it.",
                "editor": "Editor opened. Saved changes reach the coach by themselves.",
                "setup": "Memory setup opened (only for other releases)."}[name])


def refresh():
    c = procs.get("coach")
    coach_btn.config(text="Coach running" if c is not None and c.poll() is None else "Start coach")
    names, cur = routes.names(), routes.active()        # the editor may have added or picked one
    if list(route_pick.cget("values")) != names:
        route_pick.configure(values=names)
    if route_pick.get() != cur:
        route_pick.set(cur)
    root.after(1000, refresh)


CONFIG = HERE / "config.json"


def show_next_setting() -> bool:
    try:
        return bool(json.loads(CONFIG.read_text(encoding="utf-8")).get("overlay", {}).get("show_next", False))
    except (OSError, ValueError):
        return False


def set_show_next():
    """Tick box: show the next step under the current one on the panel (config.json)."""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        cfg.setdefault("overlay", {})["show_next"] = bool(show_next.get())
        from paths import write_atomic              # all at once: the coach reads it while running
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        status.set("The panel shows the next step underneath." if show_next.get() else
                   "The panel shows only the step you're on.")
    except (OSError, ValueError) as e:
        status.set(f"Couldn't change config.json: {e}")


def pick_route(_e=None):
    routes.set_active(route_pick.get())
    status.set(f"Route: {route_pick.get()}. A running coach switches to it.")


root = tk.Tk()
root.title("FFIX Coach")
root.resizable(False, False)
root.configure(padx=18, pady=14)
tk.Label(root, text="FFIX Coach", font=("Segoe UI", 14, "bold")).pack(pady=(0, 6))
tk.Label(root, text="Route", font=("Segoe UI", 9), fg="#555").pack(anchor="w")
route_pick = ttk.Combobox(root, values=routes.names(), state="readonly", width=34)
route_pick.set(routes.active())
route_pick.pack(pady=(0, 10))
route_pick.bind("<<ComboboxSelected>>", pick_route)
def stop_coach():
    """Close the coach: it sees this file within a moment and quits (works for a coach
    started earlier too). One this window started is closed for sure after 3 seconds."""
    try:
        (HERE / "stop_coach").write_text("stop")
    except OSError as e:
        status.set(f"Couldn't stop it: {e}")
        return
    status.set("Stopping the coach...")
    p = procs.get("coach")

    def make_sure():
        if p is not None and p.poll() is None:
            p.terminate()
        try:
            (HERE / "stop_coach").unlink()
        except OSError:
            pass
        status.set("Coach stopped.")
    root.after(3000, make_sure)


coach_row = tk.Frame(root)
coach_row.pack(pady=3)
coach_btn = tk.Button(coach_row, text="Start coach", width=15, font=("Segoe UI", 11),
                      command=lambda: start("coach", "run_coach.pyw"))
coach_btn.pack(side="left", padx=(0, 4))
tk.Button(coach_row, text="Stop", width=6, font=("Segoe UI", 11), command=stop_coach).pack(side="left")
tk.Button(root, text="Edit route", width=22, font=("Segoe UI", 11),
          command=lambda: start("editor", "editor.py")).pack(pady=3)
small = tk.Frame(root)
small.pack(pady=(3, 0))
tk.Button(small, text="Memory setup", width=13, font=("Segoe UI", 9),
          command=lambda: start("setup", "setup_finder.py")).pack(side="left", padx=2)


def open_help():
    """The README: how to use it (opens in the usual app for .md files, else Notepad)."""
    readme = HERE / "README.md"
    try:
        import os
        os.startfile(str(readme))                       # Windows
    except (AttributeError, OSError):
        try:
            subprocess.Popen(["notepad.exe", str(readme)])
        except OSError:
            status.set(f"Open {readme.name} in this folder for help.")


tk.Button(small, text="Help", width=8, font=("Segoe UI", 9), command=open_help).pack(side="left", padx=2)
show_next = tk.BooleanVar(value=show_next_setting())
tk.Checkbutton(root, text="Show the next step underneath", variable=show_next, font=("Segoe UI", 9),
               command=set_show_next).pack(anchor="w", pady=(8, 0))
status = tk.StringVar(value="Start the game in DuckStation first.")
tk.Label(root, textvariable=status, font=("Segoe UI", 9), fg="#555").pack(pady=(10, 0))
tk.Label(root, text="Closing this window leaves the coach running.", font=("Segoe UI", 8), fg="#888").pack()
refresh()
root.mainloop()
