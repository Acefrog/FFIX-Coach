"""Memory setup: every finder in one window.

    python app\\setup_finder.py      (or "Memory setup" in FFIX Coach.pyw)

config.json already holds where things are in FFIX's memory for the US (NTSC-U) release.
On another release, or if something never ticks, pick what to find on the left and
follow the instructions on the right: it tells you what to do in the game, and you
answer with the buttons or by typing. Each finder saves to config.json when it's done.
(Same finders as app\\finder.py, which still works from a command window.)
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import finder  # noqa: E402
from paths import CONFIG  # noqa: E402

CHARS = ["zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant"]

# (command, title, where to be in the game, needs a character, what in config shows it's set)
TASKS = [
    ("fights", "When you're in a fight", "On the field, then in fights, then in the menu (about 2 minutes)",
     False, lambda a: len(a.get("battle_flags") or [])),
    ("battle", "HP in battle", "In a fight with 2+ party members: type their HP", False,
     lambda a: a.get("battle_base")),
    ("check", "Check: show the HP it reads", "In a fight: compare with the game (30 seconds)", False, None),
    ("field", "Screen number", "In a town: walk between screens as it asks", False, lambda a: a.get("field_id")),
    ("stats", "Levels, EXP and character records", "Status screen. Do two characters and it works out the rest",
     True, lambda a: len(a.get("chars") or {})),
    ("menu", "When the menu is open", "Field, then menu open, as it asks (about a minute)", False,
     lambda a: len(a.get("menu_flags") or [])),
    ("equip", "Equipment", "That character's Equip screen: type what's on (after Levels)", True,
     lambda a: a.get("equip_off") is not None),
    ("row", "Battle row", "Menu > Order: swap that character's row when asked (after Levels)", True,
     lambda a: a.get("row")),
    ("items", "Item bag", "In a shop: buy or sell one thing, type its name", False, lambda a: a.get("inventory")),
    ("gil", "Gil", "Menu or shop: type your gil, spend or get some, type it again", False, lambda a: a.get("gil")),
    ("config", "Config menu settings", "FF9's Config menu open: switch each setting when asked", False,
     lambda a: len(a.get("config") or {})),
    ("zidane", "Zidane's party position", "Only if he isn't at the top of the party in fights", False,
     lambda a: a.get("party_slots")),
    ("ability", "Support abilities (by hand)", "Rarely needed: worked out from Levels already", True,
     lambda a: len(a.get("ability_bits") or {})),
    ("battleid", "Battle number (optional)", "Three fights (savestates help). Fights are told apart anyway", False,
     lambda a: a.get("battle_id")),
]


class Runner:
    """Runs one finder in a thread, with its questions and messages going to the window."""

    def __init__(self):
        self.out: queue.Queue = queue.Queue()      # ("text", s) / ("ask", prompt) / ("done", code)
        self.answers: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, argv: list[str]) -> None:
        def ask(prompt: str = "") -> str:
            if finder.STOP.is_set():
                raise finder.Stopped()
            self.out.put(("ask", prompt))
            answer = self.answers.get()
            if finder.STOP.is_set():
                raise finder.Stopped()
            return answer

        def say(*parts, sep=" ", end="\n", **_):
            self.out.put(("text", sep.join(str(p) for p in parts) + end))

        def run():
            finder.input, finder.print = ask, say
            finder.STOP.clear()
            try:
                code = finder.main(argv)
            except finder.Stopped:
                say("\nStopped.")
                code = 1
            except Exception as e:                     # shown, not a crash of the window
                say(f"\nStopped: {type(e).__name__}: {e}")
                code = 1
            self.out.put(("done", code))

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def answer(self, text: str) -> None:
        self.answers.put(text)

    def stop(self) -> None:
        """Stop the finder now, waiting for an answer or not."""
        finder.STOP.set()
        self.answers.put("q")


def config_addresses() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("addresses", {})
    except (OSError, ValueError):
        return {}


def status_text(task, addrs: dict) -> str:
    check = task[4]
    if check is None:
        return ""
    v = check(addrs)
    if not v:
        return "not set"
    if isinstance(v, int) and not isinstance(v, bool) and task[0] in ("fights", "stats", "menu", "config", "ability"):
        return f"✓ set ({v})"
    return "✓ set"


def run() -> int:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("FFIX Coach · Memory setup")
    root.geometry("1050x640")
    root.minsize(860, 480)
    runner = Runner()

    top = ttk.Frame(root, padding=(12, 10, 12, 4))
    top.pack(fill="x")
    ttk.Label(top, text="Memory setup", font=("Segoe UI", 13, "bold")).pack(side="left")
    game = ttk.Label(top, text="", font=("Segoe UI", 9))
    game.pack(side="right")
    ttk.Label(root, padding=(12, 0, 12, 6), font=("Segoe UI", 9), foreground="#666", wraplength=1000,
              text="Only needed on a release other than the US one, or when something never ticks. Pick what to "
                   "find, have the game where it says, press Run, and follow the instructions on the right. "
                   "Each one saves to config.json; restart the coach afterwards.").pack(fill="x")

    body = ttk.PanedWindow(root, orient="horizontal")
    body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    left = ttk.Frame(body)
    body.add(left, weight=1)
    tree = ttk.Treeview(left, columns=("what", "status"), show="headings", selectmode="browse", height=16)
    tree.heading("what", text="Find")
    tree.heading("status", text="In config.json")
    tree.column("what", width=260)
    tree.column("status", width=110, anchor="w")
    tree.pack(fill="both", expand=True)
    where = ttk.Label(left, text="", wraplength=380, font=("Segoe UI", 9), foreground="#555", justify="left")
    where.pack(fill="x", pady=(6, 4))
    row = ttk.Frame(left)
    row.pack(fill="x")
    ttk.Label(row, text="Character").pack(side="left")
    who = ttk.Combobox(row, values=[c.capitalize() for c in CHARS], width=10, state="readonly")
    who.set("Zidane")
    who.pack(side="left", padx=6)
    run_btn = ttk.Button(row, text="Run", width=10)
    run_btn.pack(side="right")

    right = ttk.Frame(body)
    body.add(right, weight=2)
    log = tk.Text(right, wrap="word", font=("Consolas", 10), state="disabled", height=20,
                  background="#1e1f22", foreground="#e3e5e8", insertbackground="#e3e5e8", padx=8, pady=6)
    log.tag_configure("ask", foreground="#f5c542")
    log.tag_configure("me", foreground="#7ee787")
    log.tag_configure("head", foreground="#79b8ff", font=("Consolas", 10, "bold"))
    log.pack(fill="both", expand=True)
    bar = ttk.Frame(right)
    bar.pack(fill="x", pady=(6, 0))
    entry = ttk.Entry(bar, font=("Segoe UI", 10))
    entry.pack(side="left", fill="x", expand=True)
    buttons = []

    def write(text: str, tag: str | None = None):
        log.configure(state="normal")
        log.insert("end", text, (tag,) if tag else ())
        log.see("end")
        log.configure(state="disabled")

    def set_answering(on: bool):
        for b in buttons:
            b.state(["!disabled"] if on else ["disabled"])
        entry.state(["!disabled"] if on else ["disabled"])
        if on:
            entry.focus_set()

    def send(text: str):
        if not runner.busy():
            return
        write((text or "(Enter)") + "\n", "me")
        entry.delete(0, "end")
        set_answering(False)
        runner.answer(text)

    for label, value in (("Send / Enter", None), ("Yes", "y"), ("No", "n")):
        b = ttk.Button(bar, text=label, command=(lambda v=value: send(entry.get() if v is None else v)))
        b.pack(side="left", padx=(6, 0))
        buttons.append(b)

    def stop():
        if runner.busy():
            write("(Stop)\n", "me")
            set_answering(False)
            runner.stop()
    stop_btn = ttk.Button(bar, text="Stop", command=stop)
    stop_btn.pack(side="left", padx=(6, 0))
    stop_btn.state(["disabled"])
    entry.bind("<Return>", lambda _e: send(entry.get()))
    set_answering(False)

    def fill():
        addrs = config_addresses()
        sel = tree.selection()
        tree.delete(*tree.get_children())
        for k, t in enumerate(TASKS):
            tree.insert("", "end", iid=str(k), values=(t[1], status_text(t, addrs)))
        tree.selection_set(sel or ("0",))

    def on_pick(_e=None):
        sel = tree.selection()
        if not sel:
            return
        t = TASKS[int(sel[0])]
        where.configure(text=t[2])
        who.state(["!disabled"] if t[3] else ["disabled"])

    def start():
        if runner.busy():
            return
        sel = tree.selection()
        if not sel:
            return
        t = TASKS[int(sel[0])]
        argv = [t[0]] + ([who.get().lower()] if t[3] else [])
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        write(f"{t[1]}" + (f" ({who.get()})" if t[3] else "") + "\n", "head")
        run_btn.state(["disabled"])
        tree.state(["disabled"])
        stop_btn.state(["!disabled"])
        runner.start(argv)

    run_btn.configure(command=start)
    tree.bind("<<TreeviewSelect>>", on_pick)
    tree.bind("<Double-1>", lambda _e: start())

    def poll():
        try:
            while True:
                kind, val = runner.out.get_nowait()
                if kind == "text":
                    write(val)
                elif kind == "ask":
                    write(val, "ask")
                    if not val.endswith("\n"):
                        write("\n")
                    set_answering(True)
                elif kind == "done":
                    set_answering(False)
                    stop_btn.state(["disabled"])
                    run_btn.state(["!disabled"])
                    tree.state(["!disabled"])
                    write("\n" + ("Done." if val == 0 else "Not saved.") + "\n", "head")
                    fill()
        except queue.Empty:
            pass
        root.after(100, poll)

    def check_game():
        if not runner.busy():
            try:
                cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
                finder.open_ram(cfg)
                game.configure(text="● DuckStation found", foreground="#2e7d32")
            except Exception:
                game.configure(text="● DuckStation not found: start it with the game", foreground="#b35c00")
        root.after(3000, check_game)

    fill()
    on_pick()
    poll()
    check_game()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
