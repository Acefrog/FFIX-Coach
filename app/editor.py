"""Route editor: a window for making and editing routes (one file each in routes/).

    python editor.py      (or Edit route in FFIX Coach.pyw)

Left: every step in order (search and filter by disc). Middle: edit the selected
step (fight steps are written the same way, plus their HP). Right: how it looks on
the coach panel, the line types, and checks.

Ctrl+S saves (the old route is copied to backups/ first, the last 30 kept).
The coach picks up a saved route by itself, even mid-run.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import routes  # noqa: E402
from syntax import expand_headers, classify, line_segments, steal_targets, find_item, story_parts, substep_call  # noqa: E402
from coach import parse_when, when_label, when_text, step_numbers, OPS, AP_OPS, ap_subjects, ap_subject  # noqa: E402

from paths import BACKUPS, LEARNED, write_atomic  # noqa: E402
KEEP_BACKUPS = 30
LIMIT = 60                     # longer lines wrap on the coach panel
CHARS = ["zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant"]
KIND_NAME = {"move": "Walk", "menu": "Menu", "boss": "Fight"}
KIND_OF = {v: k for k, v in KIND_NAME.items()}
THEMES = {
    "light": {"bg": "#ececec", "field": "#ffffff", "fg": "#1e1e1e", "muted": "#6b6b6b", "border": "#b5b5b5",
              "button": "#e0e0e0", "button_hi": "#d0d0d0", "sel_bg": "#3d6fa8", "sel_fg": "#ffffff",
              "warn": "#b35c00", "good": "#2e7d32", "boss": "#c0392b", "menu": "#1f6fb2", "move": "#2e7d32",
              "here": "#fff3c4", "bad": "#fde2e2", "drop": "#cfe3ff", "remove": "#c0392b", "cursor": "#000000",
              "kinds": {"pickup": "#2e7d32", "buy": "#1f6fb2", "sell": "#1f6fb2", "synth": "#1f6fb2",
                        "equip": "#7b3fb5", "row": "#00838f", "config": "#8d6e00", "steal": "#b35c00",
                        "note": "#333333", "comment": "#6b6b6b", "ap": "#0b7285", "story": "#b0306f", "substep": "#b35c00"}},
    "dark": {"bg": "#1e1f22", "field": "#2b2d31", "fg": "#e3e5e8", "muted": "#9aa0a8", "border": "#3f4248",
             "button": "#313338", "button_hi": "#404249", "sel_bg": "#3d6fa8", "sel_fg": "#ffffff",
             "warn": "#f0a050", "good": "#5fd38d", "boss": "#ff7b72", "menu": "#79b8ff", "move": "#7ee787",
             "here": "#4a4020", "bad": "#4d2626", "drop": "#24406b", "remove": "#ff7b72", "cursor": "#ffffff",
             "kinds": {"pickup": "#7ee787", "buy": "#79b8ff", "sell": "#79b8ff", "synth": "#79b8ff",
                       "equip": "#d2a8ff", "row": "#56d4dd", "config": "#e3b341", "steal": "#ffa657",
                       "note": "#e3e5e8", "comment": "#aab4bf", "ap": "#7dd3fc", "story": "#f0a6ca", "substep": "#ffa657"}},
}

KIND_COLOURS = {"pickup": "#2e7d32", "buy": "#1f6fb2", "sell": "#1f6fb2", "synth": "#1f6fb2",
                "equip": "#7b3fb5", "row": "#00838f", "config": "#8d6e00", "steal": "#b35c00",
                "note": "#333333", "comment": "#6b6b6b", "ap": "#0b7285", "story": "#b0306f", "substep": "#b35c00"}


# ----------------------------------------------------------------------
# Route file: checking and saving (no window needed; tested on their own)
# ----------------------------------------------------------------------
def check_route(route: dict) -> list[str]:
    """Problems that would stop the coach loading the route (empty = fine)."""
    problems = []
    if not isinstance(route, dict) or not isinstance(route.get("steps"), list):
        return ["The route needs a list of steps."]
    if not route["steps"]:
        problems.append("The route has no steps.")
    ids = set()
    for i, s in enumerate(route["steps"], 1):
        t = s.get("type")
        if t not in ("move", "menu", "boss"):
            problems.append(f"Step {i}: unknown type {t!r}.")
        if not isinstance(s.get("lines"), list):
            problems.append(f"Step {i}: needs a list of lines.")
        if not isinstance(s.get("disc"), int):
            problems.append(f"Step {i}: disc must be a number.")
        if t == "boss":
            if s.get("id") in ids:
                problems.append(f"Step {i}: two fights share the id {s.get('id')!r}.")
            ids.add(s.get("id"))
            hp = s.get("hp")
            if hp is not None and not (isinstance(hp, list) and all(isinstance(h, int) for h in hp)):
                problems.append(f"Step {i}: HP must be whole numbers.")
    return problems


def modernise(route: dict) -> int:
    """Old "Zidane off: Silk Shirt" lines written the new way ("Zidane: -Silk Shirt")."""
    from coach import old_off_line
    n = 0
    for s in route.get("steps", []):
        if isinstance(s.get("lines"), list):
            new = [old_off_line(l) for l in s["lines"]]
            n += sum(a != b for a, b in zip(new, s["lines"]))
            s["lines"] = new
    for b in route.get("bosses", {}).values():
        if isinstance(b.get("turns"), list):
            new = [old_off_line(l) for l in b["turns"]]
            n += sum(a != b2 for a, b2 in zip(new, b["turns"]))
            b["turns"] = new
    return n


def save_route(route: dict, path: Path, backups: Path = BACKUPS) -> str:
    modernise(route)
    backups.mkdir(exist_ok=True)
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (backups / f"{path.stem} {stamp}.json").write_bytes(path.read_bytes())
        # Only this route's own backups ("Muttski 20261029-001319"), not those of a route
        # whose name merely starts the same ("Muttski 2026").
        mine = re.compile(re.escape(path.stem) + r" \d{8}-\d{6}\.json", re.I)
        own = [f for f in backups.glob(f"{glob_escape(path.stem)} 2*.json") if mine.fullmatch(f.name)]
        for f in sorted(own)[:-KEEP_BACKUPS]:
            try:
                f.unlink()
            except OSError:
                pass
    write_atomic(path, json.dumps(route, indent=1, ensure_ascii=False))   # the coach never reads half a file
    return time.strftime("%H:%M:%S")


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def step_key(s: dict) -> str:
    """Same identity the coach uses to remember your place."""
    if s.get("type") == "boss":
        return f"boss:{s.get('id') or s.get('boss')}"
    lines = s.get("lines") or [""]
    return f"{s.get('type')}:{s.get('title', '')}:{lines[0]}"


def step_label(route: dict, s: dict) -> str:
    return s.get("title") or (s.get("lines") or ["(untitled)"])[0]


def find_problems(route: dict) -> list[tuple[int | None, str | None, str]]:
    """(step index, None, message) for the Checks list."""
    out = []
    prev_disc = 1
    for i, s in enumerate(route["steps"]):
        name = f"Step {i + 1} ({step_label(route, s)})"
        d = s.get("disc", 1) if isinstance(s.get("disc"), int) else 1
        if d < prev_disc:
            out.append((i, None, f"{name}: Disc {d} after Disc {prev_disc} steps (out of order?)"))
        prev_disc = max(prev_disc, d)
        if s.get("inside") and i == 0:
            out.append((i, None, f"{name}: set to be inside the step above, but it's the first step"))
        if s.get("alt") and i == 0:
            out.append((i, None, f"{name}: an alternative, but it's the first step"))
        calls = [substep_call(l) for l in s.get("lines", []) if substep_call(l)]
        if calls:
            mine, j = set(), i + 1
            while j < len(route["steps"]) and route["steps"][j].get("inside"):
                mine.add((route["steps"][j].get("title") or "").strip().lower())
                j += 1
            for c in calls:
                if c.lower() not in mine:
                    out.append((i, None, f"{name}: [{c}] isn't one of its sub-steps"))
        if s.get("when"):
            for part in re.split(r"\s+and\s+", s["when"].strip()):
                w = parse_when(part)
                if not w:
                    out.append((i, None, f"{name}: \"{part}\" isn't understood"))
                elif w[0] == "cmp" and ":" in w[1]:
                    if not ap_subject(w[1]):
                        out.append((i, None, f"{name}: \"{w[1]}\" isn't a character's ability"))
                elif w[0] == "cmp" and not find_item(w[1]):
                    out.append((i, None, f"{name}: \"{w[1]}\" isn't an item"))
        lines = s.get("lines", [])
        if not any(l.strip() for l in lines):
            out.append((i, None, f"{name}: no {'actions' if s.get('type') == 'boss' else 'instructions'}"))
        for l in lines:
            if len(l) > LIMIT and not l.strip().startswith("#"):
                out.append((i, None, f"{name}: line over {LIMIT} characters"))
                break
        if s.get("type") != "boss":
            for l in expand_headers(lines):
                info = classify(l)
                if info.problem and info.kind != "note":
                    out.append((i, None, f"{name}: \"{l[:30]}\" - {info.problem}"))
        else:
            for l in lines:                        # @ AP lines work on fight steps too
                info = classify(l)
                if info.kind == "ap" and info.problem:
                    out.append((i, None, f"{name}: \"{l[:30]}\" - {info.problem}"))
    return out


# ----------------------------------------------------------------------
# Comparing two routes
# ----------------------------------------------------------------------
COMPARE_FIELDS = (("disc", "disc"), ("lines", "lines"), ("backup", "backup note"), ("hp", "HP"),
                  ("hp_names", "HP names"), ("fights", "fights"), ("lv5", "LV5 warning"),
                  ("inside", "inside the step above"), ("when", "taken when"), ("alt", "alternative"))


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def _align_key(s: dict) -> tuple[str, str]:
    """Kind and title, loosely: "Steiner II" = "steiner 2", "Bomb (Steiner 3)" = "bomb"."""
    t = re.sub(r"\([^)]*\)", " ", (s.get("title") or "").lower())
    words = re.sub(r"[^a-z0-9]+", " ", t).split()
    return s.get("type", ""), " ".join(_ROMAN.get(w, w) for w in words if w not in ("the", "pt"))


def _pair_block(sa, sb, i1, i2, j1, j2):
    """Inside a stretch where titles differ, pair steps that are clearly the same one
    (same kind, similar title), keeping the order."""
    import difflib
    out, j = [], j1
    for i in range(i1, i2):
        ka = _align_key(sa[i])
        best, best_r = None, 0.6
        for jj in range(j, j2):
            kb = _align_key(sb[jj])
            if kb[0] != ka[0]:
                continue
            r = difflib.SequenceMatcher(None, ka[1], kb[1]).ratio()
            if r > best_r:
                best, best_r = jj, r
        if best is None:
            out.append((i, None))
            continue
        out += [(None, jj) for jj in range(j, best)]
        out.append((i, best))
        j = best + 1
    out += [(None, jj) for jj in range(j, j2)]
    return out


def step_differences(a: dict, b: dict) -> list[str]:
    """What differs between two versions of a step ("lines", "HP", ...)."""
    out = []
    if a.get("type") != b.get("type"):
        out.append("kind")
    if (a.get("title") or "") != (b.get("title") or ""):
        out.append("title")
    for key, name in COMPARE_FIELDS:
        va, vb = a.get(key), b.get(key)
        if key == "lines":
            va, vb = [l.rstrip() for l in va or []], [l.rstrip() for l in vb or []]
        if (va or None) != (vb or None):
            out.append(name)
    return out


def compare_routes(a: dict, b: dict) -> list[dict]:
    """Steps of two routes lined up. Each row: status ("same", "changed", "only_a" or
    "only_b"), a / b (step index or None), diff (what differs, title included)."""
    sa, sb = a.get("steps", []), b.get("steps", [])
    import difflib
    sm = difflib.SequenceMatcher(None, [_align_key(s) for s in sa], [_align_key(s) for s in sb],
                                 autojunk=False)
    rows = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                d = step_differences(sa[i], sb[j])
                rows.append({"status": "changed" if d else "same", "a": i, "b": j, "diff": d})
        elif op == "replace":
            for i, j in _pair_block(sa, sb, i1, i2, j1, j2):
                if i is not None and j is not None:
                    d = step_differences(sa[i], sb[j])
                    rows.append({"status": "changed" if d else "same", "a": i, "b": j, "diff": d})
                else:
                    rows.append({"status": "only_a" if j is None else "only_b", "a": i, "b": j, "diff": []})
        elif op == "delete":
            rows += [{"status": "only_a", "a": i, "b": None, "diff": []} for i in range(i1, i2)]
        else:
            rows += [{"status": "only_b", "a": None, "b": j, "diff": []} for j in range(j1, j2)]
    return rows


def take_step(route: dict, other: dict, row: dict) -> int:
    """Put the other route's step from a compare row into this route: over this route's
    version if it has one, otherwise after the step it follows. Returns where it went."""
    rows = compare_routes(route, other)
    new = copy.deepcopy(other["steps"][row["b"]])
    new.pop("boss", None)
    steps = route["steps"]
    if row.get("a") is not None:
        old = steps[row["a"]]
        if new.get("type") == "boss":
            # Keep this route's fight id when it's the same fight (what the coach learned for it)
            new["id"] = old["id"] if old.get("type") == "boss" and old.get("id") else \
                routes.step_id(route, new.get("title", ""))
        steps[row["a"]] = new
        return row["a"]
    k = next((n for n, r in enumerate(rows) if r.get("b") == row["b"]), len(rows))
    before = [r["a"] for r in rows[:k] if r.get("a") is not None]
    at = before[-1] + 1 if before else 0
    if new.get("type") == "boss":
        new["id"] = routes.step_id(route, new.get("title", ""))
    steps.insert(at, new)
    return at


def text_lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip()]


def step_text_lines(text: str) -> list[str]:
    """A step's lines as typed: an empty line is a gap on the panel (one gap at most in a
    row, none at the start or end); a line starting with spaces or a Tab is indented
    (no bullet of its own; in a fight it goes with the action above it)."""
    out = []
    for raw in text.splitlines():
        l = raw.strip()
        if l and raw[:1] in (" ", "\t") and out:
            l = "  " + l
        if l or (out and out[-1]):
            out.append(l)
    while out and not out[-1]:
        out.pop()
    return out


def hp_values(text: str) -> list[int]:
    return parse_hp(text)[0]


def parse_hp(text: str) -> tuple[list[int], dict[str, str]]:
    """"Zorn 229, Thorn 472" -> ([229, 472], {"229": "Zorn", "472": "Thorn"}).
    Plain numbers work too ("229, 472"); "2,700" (no space) is one number."""
    # Split at ";" and at commas followed by a space or a letter: "2,700" stays one number.
    parts = [p.strip() for p in re.split(r";|,(?=\s)|,(?=[^\d])", text)]
    hp, names = [], {}
    for p in parts:
        nums = list(re.finditer(r"\d[\d,]*", p))
        if not nums:
            continue
        m = nums[-1]                                   # "Black Waltz 1 229": the last number is the HP
        v = int(m.group(0).replace(",", ""))
        hp.append(v)
        name = (p[:m.start()] + " " + p[m.end():]).strip(" :-=")
        if name:
            names[str(v)] = name
    return hp, names


def format_hp(hp: list[int], names: dict | None) -> str:
    names = names or {}
    return ", ".join(f"{names[str(h)]} {h}" if str(h) in names else str(h) for h in hp)


# ----------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------
def tooltip(widget, text: str):
    """A small note that shows while the mouse rests on the widget. Returns the widget."""
    import tkinter as tk
    old = getattr(widget, "_tip_state", None)
    if old is not None:                            # a second tooltip on it: replaces the first
        old["text"] = text
        return widget
    state = {"tip": None, "job": None, "text": text}
    widget._tip_state = state
    def show():
        state["job"] = None
        if state["tip"] is not None or not widget.winfo_exists():
            return
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=state["text"], bg="#2b2d31", fg="#e3e5e8", font=("Segoe UI", 9), padx=6, pady=3,
                 relief="solid", borderwidth=1).pack()
        tip.update_idletasks()
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tip.wm_geometry(f"+{x}+{y}")
        state["tip"] = tip
    def enter(_e):
        if state["job"] is None:
            state["job"] = widget.after(450, show)
    def leave(_e):
        if state["job"] is not None:
            widget.after_cancel(state["job"])
            state["job"] = None
        if state["tip"] is not None:
            state["tip"].destroy()
            state["tip"] = None
    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", leave, add="+")
    return widget


def run() -> int:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk

    try:                                   # sharp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    if not routes.names():
        routes.create("My route")

    class Editor:
        def __init__(self, root: tk.Tk):
            self.root = root
            self.name = routes.active()
            self.route = routes.inline_cards(json.loads(self.file().read_text(encoding="utf-8")))
            self.saved = json.dumps(self.route, sort_keys=True)
            self.mtime = self.file().stat().st_mtime
            self.undo_stack: list[str] = []
            self.redo_stack: list[str] = []
            self.sel = 0
            self.here = self._here_key()
            self.snap_taken = False        # one undo point per field you type in
            self.multi: set[int] = set()   # extra steps picked with Ctrl/Shift-click
            self.building = False

            root.title("Route Editor")
            root.geometry("1280x800")
            root.minsize(980, 600)
            root.protocol("WM_DELETE_WINDOW", self.close)
            self._build()
            i = next((k for k, s in enumerate(self.route["steps"]) if step_key(s) == self.here), 0)
            self.select(i)
            self.root.after(2000, self._watch_file)

        # -- layout -----------------------------------------------------
        def _build(self):
            root = self.root
            self.style = ttk.Style()
            self.dark = tk.BooleanVar(value=bool(routes._config().get("editor_dark", True)))
            self.apply_theme(rebuild=False)
            # top: the route, its options, saving
            top = ttk.Frame(root, padding=(8, 6))
            top.pack(fill="x")
            ttk.Label(top, text="Route", font=("Segoe UI", 10, "bold")).pack(side="left")
            self.route_pick = ttk.Combobox(top, values=routes.names(), state="readonly", width=36)
            self.route_pick.set(self.name)
            tooltip(self.route_pick, "The coach follows the route picked here").pack(side="left", padx=6)
            self.route_pick.bind("<<ComboboxSelected>>", lambda e: self.open_route(self.route_pick.get()))
            for text, cmd, tip in (("New…", self.new_route, "Start a new, empty route"),
                                   ("Copy…", self.copy_route, "Make a copy of this route"),
                                   ("Rename…", self.rename_route, "Rename this route"),
                                   ("Delete", self.delete_route, "Delete this route (a copy goes to backups)"),
                                   ("Compare…", self.open_compare, "Compare this route with another one")):
                tooltip(ttk.Button(top, text=text, command=cmd), tip).pack(side="left", padx=2)
            self.b_save = ttk.Button(top, text="Save", command=self.save, width=8)
            tooltip(self.b_save, "Save (Ctrl+S)").pack(side="right")
            tooltip(ttk.Button(top, text="Reload", command=self.reload),
                    "Load the route file again").pack(side="right", padx=4)
            ttk.Checkbutton(top, text="Dark mode", variable=self.dark,
                            command=self.toggle_dark).pack(side="right", padx=(10, 8))
            self.status = ttk.Label(top, text="", style="Muted.TLabel")
            self.status.pack(side="right", padx=10)

            # bottom: problems found in the route, a small strip across the whole window
            bottom = ttk.Frame(root, padding=(8, 0, 8, 6))
            bottom.pack(side="bottom", fill="x")
            self.checks_label = ttk.Label(bottom, text="Checks", font=("Segoe UI", 9, "bold"), width=9)
            tooltip(self.checks_label, "Problems in the route. Double-click one to go to its step").pack(
                side="left", anchor="n", pady=(2, 0))
            self.checks = tk.Listbox(bottom, activestyle="none", relief="flat", highlightthickness=1, height=3)
            self._colour_tk(self.checks)
            csb = ttk.Scrollbar(bottom, orient="vertical", command=self.checks.yview)
            self.checks.configure(yscrollcommand=csb.set)
            self.checks.pack(side="left", fill="x", expand=True)
            csb.pack(side="left", fill="y")
            self.checks.bind("<Double-Button-1>", self._on_check)

            body = ttk.PanedWindow(root, orient="horizontal")
            body.pack(fill="both", expand=True)

            # left: the step list
            left = ttk.Frame(body, padding=(8, 0, 4, 8))
            body.add(left, weight=2)
            row = ttk.Frame(left)
            row.pack(fill="x", pady=(0, 6))
            self.search = tk.StringVar()
            self.search.trace_add("write", lambda *_: self.fill_list())
            ttk.Label(row, text="Search ").pack(side="left")
            ent = ttk.Entry(row, textvariable=self.search)
            self.search_entry = ent
            ent.pack(side="left", fill="x", expand=True)
            tooltip(ent, "Find steps by title or line")
            ttk.Label(row, text=" Disc ").pack(side="left")
            self.disc_filter = tk.StringVar(value="All")
            cb = ttk.Combobox(row, textvariable=self.disc_filter, values=["All", "1", "2", "3", "4"],
                              width=4, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self.fill_list())
            tools = ttk.Frame(left)
            tools.pack(fill="x", pady=(0, 2))
            tools2 = ttk.Frame(left)
            tools2.pack(fill="x", pady=(0, 6))
            def small(parent, text, cmd, tip, side="left"):
                btn = ttk.Button(parent, text=text, command=cmd, style="Small.TButton")
                tooltip(btn, tip).pack(side=side, padx=(0, 3) if side == "left" else (3, 0))
                return btn
            small(tools, "+ Walk", lambda: self.add("move"), "New walk step after this one (Ctrl+1)")
            small(tools, "+ Menu", lambda: self.add("menu"), "New menu step after this one (Ctrl+2)")
            small(tools, "+ Fight", lambda: self.add("boss"), "New fight after this one (Ctrl+3)")
            small(tools, "\u25b2", lambda: self.move(-1), "Move up (Ctrl+\u2191)")
            small(tools, "\u25bc", lambda: self.move(1), "Move down (Ctrl+\u2193)")
            self.b_redo = small(tools, "Redo", self.redo, "Redo (Ctrl+Y)", side="right")
            self.b_undo = small(tools, "Undo", self.undo, "Undo (Ctrl+Z)", side="right")
            small(tools2, "Duplicate", self.duplicate, "Duplicate (Ctrl+D)")
            small(tools2, "Copy", self.copy_steps, "Copy (Ctrl+C) \u00b7 Ctrl/Shift-click picks several")
            small(tools2, "Cut", self.cut_steps, "Cut (Ctrl+X)")
            small(tools2, "Paste", self.paste_steps, "Paste after this step (Ctrl+V)")
            small(tools2, "Delete", self.delete, "Delete (Del)")
            small(tools2, "Rename", self._focus_title, "Rename the step (F2)")
            frame = ttk.Frame(left)
            frame.pack(fill="both", expand=True)
            self.tree = ttk.Treeview(frame, columns=("n", "kind", "name"), show="headings", selectmode="extended")
            for col, txt, w, st in (("n", "#", 44, False), ("kind", "Kind", 60, False), ("name", "Step", 260, True)):
                self.tree.heading(col, text=txt, anchor="w")
                self.tree.column(col, width=w, stretch=st, anchor="w")
            sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self._tree_colours()
            self.tree.bind("<<TreeviewSelect>>", self._on_tree)
            self.tree.bind("<ButtonPress-1>", self._drag_start, add="+")
            self.tree.bind("<B1-Motion>", self._drag_motion, add="+")
            self.tree.bind("<ButtonRelease-1>", self._drag_end, add="+")
            self.root.bind("<Escape>", self._drag_cancel, add="+")
            self.drag = None

            # middle: the step editor (rebuilt per step)
            self.mid = ttk.Frame(body, padding=(12, 0, 12, 8))
            body.add(self.mid, weight=4)

            # right: the step as the coach panel shows it
            right = ttk.Frame(body, padding=(4, 0, 8, 8))
            body.add(right, weight=2)
            ttk.Label(right, text="On the coach panel", font=("Segoe UI", 9, "bold")).pack(anchor="w")
            self.preview = tk.Text(right, height=14, wrap="word", bg="black", fg="#e7e9ee", relief="flat",
                                   font=("Segoe UI", 10), padx=8, pady=6, state="disabled", cursor="arrow")
            self.preview.pack(fill="both", expand=True, pady=(2, 0))
            self.preview.tag_configure("head", foreground="#9097a3", font=("Segoe UI", 9))
            self.preview.tag_configure("title", font=("Segoe UI", 12, "bold"))
            self.preview.tag_configure("boss", foreground="#ff7a6b")
            self.preview.tag_configure("menu", foreground="#6bb8ff")
            self.preview.tag_configure("hl", background="#f5c542", foreground="#111111", font=("Segoe UI", 10, "bold"))
            self.preview.tag_configure("note", foreground="#ffb347", font=("Segoe UI", 9))
            self.preview.tag_configure("remove", foreground="#f87171")
            self.preview.tag_configure("gap", font=("Segoe UI", 4))
            self.preview.tag_configure("remove_hl", foreground="#b91c1c")
            self.preview.tag_configure("ap", foreground="#7dd3fc")
            self.preview.tag_configure("story", foreground="#f0a6ca")
            self.preview.tag_configure("substep", foreground="#ffa657")
            self.preview.tag_configure("comment", foreground="#b8c4d0",
                                       font=("Segoe UI", 8), lmargin1=22, lmargin2=22)

            def place_sashes(tries=[0]):
                tries[0] += 1
                if root.winfo_width() < 900 and tries[0] < 20:
                    root.after(100, place_sashes)       # not laid out yet
                    return
                root.update_idletasks()
                w = max(root.winfo_width(), 980)
                left_w = max(400, int(w * 0.30))       # room for the step buttons
                body.sashpos(0, left_w)
                body.sashpos(1, max(left_w + 380, int(w * 0.72)))
            root.after(150, place_sashes)

            root.bind_all("<Control-s>", lambda e: (self.save(), "break")[1])
            root.bind_all("<Delete>", self._key(self.delete))
            root.bind_all("<Control-c>", self._key(self.copy_steps))
            root.bind_all("<Control-x>", self._key(self.cut_steps))
            root.bind_all("<Control-v>", self._key(self.paste_steps))
            root.bind_all("<Control-d>", self._key(self.duplicate))
            root.bind_all("<Control-Up>", self._key(lambda: self.move_selected(-1)))
            root.bind_all("<Control-Down>", self._key(lambda: self.move_selected(1)))
            root.bind_all("<Control-Key-1>", self._key(lambda: self.add("move")))
            root.bind_all("<Control-Key-2>", self._key(lambda: self.add("menu")))
            root.bind_all("<Control-Key-3>", self._key(lambda: self.add("boss")))
            root.bind_all("<Control-f>", lambda e: (self.search_entry.focus_set(), "break")[1])
            root.bind_all("<F2>", lambda e: (self._focus_title(), "break")[1])
            root.bind_all("<Escape>", lambda e: (self.tree.focus_set(), "break")[1])
            root.bind_all("<Alt-Up>", lambda e: (self.move_selected(-1), "break")[1])
            root.bind_all("<Alt-Down>", lambda e: (self.move_selected(1), "break")[1])
            root.bind_all("<Control-z>", self._ctrl_z)
            root.bind_all("<Control-y>", self._ctrl_y)

        # -- light / dark ---------------------------------------------------
        def apply_theme(self, rebuild=True):
            th = self.th = THEMES["dark" if self.dark.get() else "light"]
            st = self.style
            try:
                st.theme_use("clam")               # the one theme that takes colours everywhere
            except tk.TclError:
                pass
            st.configure(".", background=th["bg"], foreground=th["fg"], fieldbackground=th["field"],
                         bordercolor=th["border"], lightcolor=th["bg"], darkcolor=th["bg"],
                         troughcolor=th["bg"], insertcolor=th["cursor"], selectbackground=th["sel_bg"],
                         selectforeground=th["sel_fg"])
            st.configure("TButton", background=th["button"], foreground=th["fg"], padding=(6, 2), width=-3)
            st.map("TButton", background=[("disabled", th["bg"]), ("active", th["button_hi"])],
                   foreground=[("disabled", th["muted"])])
            st.configure("Small.TButton", background=th["button"], foreground=th["fg"], padding=(4, 0),
                         width=-1, font=("Segoe UI", 9))
            st.map("Small.TButton", background=[("disabled", th["bg"]), ("active", th["button_hi"])],
                   foreground=[("disabled", th["muted"])])
            st.configure("TCheckbutton", background=th["bg"], foreground=th["fg"], indicatorbackground=th["field"])
            st.map("TCheckbutton", background=[("active", th["bg"])])
            st.configure("TEntry", fieldbackground=th["field"], foreground=th["fg"])
            st.configure("TCombobox", fieldbackground=th["field"], foreground=th["fg"], background=th["button"],
                         arrowcolor=th["fg"])
            st.map("TCombobox", fieldbackground=[("readonly", th["field"])], foreground=[("readonly", th["fg"])])
            st.configure("Treeview", background=th["field"], fieldbackground=th["field"], foreground=th["fg"],
                         rowheight=24)
            st.map("Treeview", background=[("selected", th["sel_bg"])], foreground=[("selected", th["sel_fg"])])
            st.configure("Treeview.Heading", background=th["button"], foreground=th["fg"])
            st.map("Treeview.Heading", background=[("active", th["button_hi"])])
            st.configure("TScrollbar", background=th["button"], arrowcolor=th["fg"])
            st.configure("Muted.TLabel", foreground=th["muted"])
            st.configure("Warn.TLabel", foreground=th["warn"])
            st.configure("Good.TLabel", foreground=th["good"])
            self.root.configure(bg=th["bg"])
            self.root.option_add("*TCombobox*Listbox.background", th["field"])
            self.root.option_add("*TCombobox*Listbox.foreground", th["fg"])
            self.root.option_add("*TCombobox*Listbox.selectBackground", th["sel_bg"])
            if rebuild:
                self._tree_colours()
                self._colour_tk(self.checks)
                self.select(self.sel)               # the step editor, in the new colours

        def toggle_dark(self):
            cfg = routes._config()
            cfg["editor_dark"] = bool(self.dark.get())
            write_atomic(routes.CONFIG, json.dumps(cfg, indent=2))
            self.apply_theme()

        def _colour_tk(self, w):
            th = self.th
            w.configure(bg=th["field"], fg=th["fg"], highlightbackground=th["border"],
                        highlightcolor=th["sel_bg"], selectbackground=th["sel_bg"], selectforeground=th["sel_fg"])
            if isinstance(w, tk.Text):
                w.configure(insertbackground=th["cursor"])

        def _tree_colours(self):
            th = self.th
            for tag in ("boss", "menu", "move"):
                self.tree.tag_configure(tag, foreground=th[tag])
            for tag in ("here", "bad", "drop"):
                self.tree.tag_configure(tag, background=th[tag])

        # -- list -----------------------------------------------------------
        def visible(self, s: dict) -> bool:
            d = self.disc_filter.get()
            if d != "All" and str(s.get("disc")) != d:
                return False
            q = self.search.get().strip().lower()
            if not q:
                return True
            hay = " ".join([step_label(self.route, s), *(s.get("lines") or []), s.get("backup", "")]).lower()
            return q in hay

        def _report(self):
            """What the coach knows for each step (from learned.json), read fresh."""
            import report
            try:
                self.learned = report.load_learned()
                r = dict(self.route, name=self.name)
                self.rep = report.step_report(r, self.learned)
            except Exception:                       # a half-written learned.json: show nothing
                self.rep = [{"ok": True, "how": "", "screen": None}] * len(self.route["steps"])
            return self.rep

        def fill_list(self):
            bad = {i for i, _, _ in find_problems(self.route) if i is not None}
            rep = self._report()
            nums = step_numbers(self.route["steps"])
            self.tree.delete(*self.tree.get_children())
            for i, s in enumerate(self.route["steps"]):
                if not self.visible(s):
                    continue
                tags = [s["type"]]
                label = step_label(self.route, s)
                if i < len(rep) and not rep[i]["ok"]:
                    label = "\u2691 " + label                  # can't move on / be recognised by itself yet
                if s.get("inside") and i > 0:                  # done in the middle of the step above (2.1)
                    j = i
                    while j > 0 and self.route["steps"][j].get("inside"):
                        j -= 1
                    label = ("        " if self.route["steps"][j].get("alt") else "    ") + "\u21b3 " + label
                elif s.get("alt") and i > 0:                   # another way: instead of the step above (2a)
                    label = "    \u2514 " + label
                if s.get("when"):
                    label += f"   [{when_label(s['when'])}]"
                if step_key(s) == self.here:
                    tags.append("here")
                    label += "   ● you are here"
                if i in bad:
                    tags.append("bad")
                self.tree.insert("", "end", iid=str(i), values=(nums[i], KIND_NAME[s["type"]], label), tags=tags)
            kids = self.tree.get_children()
            if kids and not self.tree.exists(str(self.sel)):
                # The picked step is hidden by the search or disc filter: pick the first one
                # shown, so new and pasted steps go where you're looking.
                self.root.after_idle(lambda: self.select(int(kids[0])))
                return
            if self.tree.exists(str(self.sel)):
                self.building = True
                want = [str(i) for i in sorted(self.multi | {self.sel}) if self.tree.exists(str(i))]
                self.tree.selection_set(want)
                self.tree.focus(str(self.sel))
                self.tree.see(str(self.sel))
                self.building = False

        # -- drag a step to a new place ----------------------------------------
        # Dragging is deliberate: hold the button on a step for half a second (the pointer
        # changes) before it moves. A quick click or a slip of the mouse never moves a step.
        DRAG_HOLD = 0.5

        def _drag_start(self, e):
            row = self.tree.identify_row(e.y)
            self._drag_clear()
            if not row:
                self.drag = None
                return
            self.drag = {"from": int(row), "to": None, "y": e.y, "t": time.monotonic(), "armed": False}
            self._drag_timer = self.root.after(int(self.DRAG_HOLD * 1000), self._drag_arm)

        def _drag_arm(self):
            self._drag_timer = None
            if self.drag and not self.drag["armed"]:
                self.drag["armed"] = True
                self.tree.configure(cursor="fleur")
                self.status.configure(text=f"Moving step {self.drag['from'] + 1}: let go on the row to put it "
                                           "there · Esc or let go outside the list to cancel")

        def _drag_clear(self):
            t = getattr(self, "_drag_timer", None)
            if t:
                self.root.after_cancel(t)
            self._drag_timer = None
            d = getattr(self, "drag", None)
            if d and d.get("to") is not None and self.tree.exists(str(d["to"])):
                tags = [x for x in self.tree.item(str(d["to"]), "tags") if x != "drop"]
                self.tree.item(str(d["to"]), tags=tags)
            self.tree.configure(cursor="")

        def _drag_cancel(self, _e=None):
            if getattr(self, "drag", None):
                self._drag_clear()
                self.drag = None
                self.update_status()

        def _drag_motion(self, e):
            if not self.drag:
                return
            if not self.drag["armed"]:
                if abs(e.y - self.drag["y"]) > 4:     # moved before holding: an ordinary select
                    self._drag_clear()
                    self.drag = None
                return
            row = self.tree.identify_row(e.y)
            h = self.tree.winfo_height()
            if e.y < 10:
                self.tree.yview_scroll(-1, "units")     # scroll while dragging near the edges
            elif e.y > h - 10:
                self.tree.yview_scroll(1, "units")
            old = self.drag["to"]
            if old is not None and self.tree.exists(str(old)):
                tags = [t for t in self.tree.item(str(old), "tags") if t != "drop"]
                self.tree.item(str(old), tags=tags)
            if not row or int(row) == self.drag["from"]:
                self.drag["to"] = None
                return "break"
            self.drag["to"] = int(row)
            self.tree.item(row, tags=list(self.tree.item(row, "tags")) + ["drop"])
            return "break"                              # no rubber-band selecting while dragging

        def _drag_end(self, e):
            d = self.drag
            self._drag_clear()
            self.drag = None
            if not d or not d["armed"]:
                return
            self.update_status()
            inside = 0 <= e.x < self.tree.winfo_width() and 0 <= e.y < self.tree.winfo_height()
            if d["to"] is None or not inside:
                return                                  # let go outside the list or on itself: nothing moves
            i, j = d["from"], d["to"]
            if i == j:
                return
            def do():
                st = self.route["steps"]
                step = st.pop(i)
                st.insert(j, step)                      # takes the place of the row it was dropped on
                self.sel = j
            self.change(do)

        def _on_tree(self, _e=None):
            if self.building or (self.drag and self.drag.get("to") is not None):
                return
            sel = [int(x) for x in self.tree.selection()]
            focus = self.tree.focus()
            self.multi = set(sel) if len(sel) > 1 else set()
            cur = int(focus) if focus and int(focus) in sel else (sel[0] if sel else None)
            if cur is not None and cur != self.sel:
                self.select(cur)

        def selected(self) -> list[int]:
            """The steps picked in the list (Ctrl/Shift-click picks several)."""
            n = len(self.route["steps"])
            return sorted(i for i in (self.multi | {self.sel}) if 0 <= i < n) if n else []

        def select(self, i: int):
            self.sel = max(0, min(i, len(self.route["steps"]) - 1))
            self.snap_taken = False
            self.fill_list()
            self.build_editor()
            self.refresh_side()

        # -- step editor ----------------------------------------------------
        def build_editor(self):
            for w in self.mid.winfo_children():
                w.destroy()
            if not self.route["steps"]:
                ttk.Label(self.mid, text="No steps. Use + Walk / Menu / Fight step to add one.").pack(anchor="w")
                return
            s = self.route["steps"][self.sel]
            head = ttk.Frame(self.mid)
            head.pack(fill="x", pady=(0, 8))
            ttk.Label(head, text=f"Step {step_numbers(self.route['steps'])[self.sel]}",
                      font=("Segoe UI", 11, "bold")).pack(side="left")
            ttk.Label(head, text="   Disc").pack(side="left")
            disc = ttk.Combobox(head, values=["1", "2", "3", "4"], width=3, state="readonly")
            disc.set(str(s.get("disc", 1)))
            disc.pack(side="left", padx=4)
            disc.bind("<<ComboboxSelected>>", lambda e: self.change(lambda: s.update(disc=int(disc.get()))))
            ttk.Label(head, text="   Kind").pack(side="left")
            kind = ttk.Combobox(head, values=list(KIND_NAME.values()), width=7, state="readonly")
            kind.set(KIND_NAME[s["type"]])
            kind.pack(side="left", padx=4)
            kind.bind("<<ComboboxSelected>>", lambda e: self.change_kind(s, KIND_OF[kind.get()]))

            fight = s["type"] == "boss"
            self._learned_line(s)
            self._label("Fight name" if fight else "Title")
            title = ttk.Entry(self.mid)
            title.insert(0, s.get("title", ""))
            title.pack(fill="x")
            self.title_entry = title
            def set_title():
                old, new = s.get("title", ""), title.get()
                s["title"] = new
                if s.get("inside") and self.sel > 0 and old.strip() and old != new:
                    parent = self.route["steps"][self._parent_of(self.sel)]      # "[Baku]" in its step follows
                    parent["lines"] = [f"{l[:len(l) - len(l.lstrip())]}[{new}]"
                                       if (substep_call(l) or "").lower() == old.strip().lower() else l
                                       for l in parent.get("lines", [])]
            self._live(title, set_title)
            self._label("Branches")
            if self._sub_row(s):
                pass
            else:
                self._alt_row(s)
            if fight:
                self._fight_pick(s)
            self._label(("Actions" if fight else "Instructions") + ":  # note · @Zidane: Ability · @Lv Zidane")
            lines = self._text(s.get("lines", []), height=10 if fight else 14, fight=fight)
            warn = ttk.Label(self.mid, style="Warn.TLabel")
            warn.pack(anchor="w")
            def apply_lines():
                s["lines"] = step_text_lines(lines.get("1.0", "end"))
                self._warn(warn, s["lines"], kinds=not fight)
            self._live(lines, apply_lines)
            self._warn(warn, s.get("lines", []), kinds=not fight)
            self._label("Backup note")
            note = self._text(s["backup"].split("\n") if s.get("backup") else [], height=4)
            def apply_backup():
                # Line breaks, gaps and indents are kept, like the instructions.
                v = "\n".join(step_text_lines(note.get("1.0", "end")))
                if v:
                    s["backup"] = v
                else:
                    s.pop("backup", None)
            self._live(note, apply_backup)
            if fight:
                self._fight_extras(s)

        def _learned_line(self, s: dict):
            """How the coach moves on from this step (or recognises this fight) with what it
            has learned so far, and a button to forget what it learned here."""
            rep = getattr(self, "rep", None) or self._report()
            if self.sel >= len(rep):
                return
            info = rep[self.sel]
            row = ttk.Frame(self.mid)
            row.pack(fill="x", pady=(0, 6))
            fight = s["type"] == "boss"
            text = ("✓ " + ("Recognised" if fight else "Moves on by itself") if info["ok"] else
                    "\u2691 " + ("F8 in this fight once" if fight else "F8 here once"))
            if info.get("screen") is not None and s["type"] != "boss":
                from field_names import field_name
                text += f"  · starts on {field_name(info['screen'])}"
            lab = ttk.Label(row, text=text, style="Good.TLabel" if info["ok"] else "Warn.TLabel",
                            wraplength=max(260, self.mid.winfo_width() - 110), justify="left")
            learned_here = info.get("screen") is not None or (
                s["type"] == "boss" and any(self.learned.formation(k) or self.learned.fingerprint(k)
                                            for k in self._fight_keys(s)))
            if learned_here:
                b = ttk.Button(row, text="Forget", width=7, command=lambda: self._forget(s))
                b.pack(side="right")
                tooltip(b, "Forget what was learned here")
            lab.pack(side="left", fill="x", expand=True)
            tooltip(lab, info["how"])

        @staticmethod
        def _fight_keys(s: dict) -> list[str]:
            sid = s.get("id") or s.get("boss")
            n = int(s.get("fights") or 1)
            return [sid] + [f"{sid}#{k}" for k in range(2, n + 1)] if sid else []

        def _forget(self, s: dict):
            import report
            from coach import Coach
            st = report._expanded(dict(self.route, name=self.name))["steps"][self.sel]
            learned = report.load_learned()
            if learned.path is None:
                return
            learned.forget_step(Coach.step_key(st), self.name,
                                self._fight_keys(s) if s["type"] == "boss" else None)
            self.status.configure(text="Forgotten: the coach learns this step again next run")
            self.fill_list()
            self.build_editor()
            self.refresh_side()

        # -- branches: a step (14) and what to do instead (14a) --------------
        PICK = "(Shift+F5 only)"

        @staticmethod
        def _when_parts(s: dict) -> list[str]:
            return [p for p in re.split(r"\s+and\s+", (s.get("when") or "").strip()) if p] or [""]

        def _set_when_part(self, s: dict, k: int, v: str | None):
            """Change (or with None, remove) the k-th check of a step's "when"."""
            parts = self._when_parts(s)
            if v is None:
                parts.pop(k)
            elif k < len(parts):
                parts[k] = v
            else:
                parts.append(v)
            joined = " and ".join(p for p in parts if p)
            if (s.get("when") or "") == joined:
                return
            self.change(lambda: s.__setitem__("when", joined) if joined else s.pop("when", None))

        def _when_fields(self, row, s: dict, allow_pick: bool, k: int = 0):
            """Item, comparison and number (Broadsword < 2): the k-th check of s["when"]."""
            from screen import ITEMS
            parts = self._when_parts(s)
            cur = parse_when(parts[k] if k < len(parts) else "")
            item = ttk.Combobox(row, values=([self.PICK] if allow_pick else []) + sorted(ITEMS) + ap_subjects(),
                                width=24)
            item.set(self.PICK if cur and cur[0] == "pick" else cur[1] if cur else "")
            item.pack(side="left", padx=(6, 0))
            op = ttk.Combobox(row, values=OPS, width=10, state="readonly")
            op.set(cur[2] if cur and cur[0] == "cmp" else "<")
            num = ttk.Spinbox(row, from_=0, to=999, width=4)
            num.set(cur[3] if cur and cur[0] == "cmp" else 1)
            def show():
                for w in (op, num):
                    w.pack_forget()
                if item.get() == self.PICK:
                    return
                is_ap = ":" in item.get()
                op.configure(values=OPS + AP_OPS if is_ap else OPS)
                if not is_ap and op.get() in AP_OPS:
                    op.set("<")
                op.pack(side="left", padx=4)
                if op.get() not in AP_OPS:
                    num.pack(side="left")
            show()
            def commit(_e=None):
                show()
                name = item.get().strip()
                if not name:
                    return
                if name == self.PICK:
                    v = "pick"
                else:
                    try:
                        n = max(0, int(num.get()))
                    except ValueError:
                        n = 1
                    v = when_text(name, op.get(), n)
                if v == "pick":
                    if (s.get("when") or "") != "pick":
                        self.change(lambda: s.__setitem__("when", "pick"))
                    return
                self._set_when_part(s, k, v)
            for w in (item, op):
                w.bind("<<ComboboxSelected>>", commit)
            for w in (item, num):
                w.bind("<Return>", commit)
                w.bind("<FocusOut>", commit)
            num.configure(command=commit)
            tooltip(item, "Item got this run, or Zidane: Flee AP")
            return item

        def _alt_row(self, s: dict):
            steps = self.route["steps"]
            nums = step_numbers(steps)
            p = self._head(self.sel)
            end = self._branch_end(p)
            alt_steps = [k for k in range(p + 1, end) if steps[k].get("alt") and not steps[k].get("inside")]
            def close(first, last):
                """Remove steps first..last-1 (a way and its sub-steps, or all of them)."""
                def do():
                    del steps[first:last]
                    self.sel = p
                self.change(do)
                self.status.configure(text=f"Branch closed: {last - first} step(s) removed · Ctrl+Z to undo")
            def button(parent, text, cmd, tip):
                b = ttk.Button(parent, text=text, command=cmd)
                b.pack(side="left", padx=(0, 6))
                tooltip(b, tip)
            def cond_rows(label: str | None, allow_pick: bool):
                """The checks of s["when"]: the first after `label`, more under it ("and")."""
                row = ttk.Frame(self.mid)
                row.pack(fill="x", pady=(4, 0))
                if label:
                    ttk.Label(row, text=label).pack(side="left")
                first = self._when_fields(row, s, allow_pick=allow_pick)
                if not label:
                    first.pack_configure(padx=(0, 0))
                parts = self._when_parts(s)
                extra = getattr(self, "_adding_check", None) == self.sel
                self._adding_check = None
                for k in range(1, len(parts) + (1 if extra else 0)):   # more checks: all must hold
                    more = ttk.Frame(self.mid)
                    more.pack(fill="x", pady=(2, 0))
                    ttk.Label(more, text="and").pack(side="left")
                    self._when_fields(more, s, allow_pick=False, k=k)
                    x = ttk.Button(more, text="\u2715", width=3,
                                   command=(lambda k=k: self._set_when_part(s, k, None)) if k < len(parts)
                                   else (lambda: self.select(self.sel)))
                    x.pack(side="left", padx=6)
                    tooltip(x, "Remove this check")
                return first

            def add_check():
                self._adding_check = self.sel
                self.select(self.sel)

            if s.get("alt") and self.sel > 0:
                first_item = cond_rows(None, allow_pick=True)
                tooltip(first_item, f"When to do {nums[self.sel]} instead of {nums[p]}")
                row2 = ttk.Frame(self.mid)
                row2.pack(fill="x", pady=(4, 0))
                if s.get("when") and s["when"] != "pick":
                    button(row2, "+ Item", add_check, "Add another check")
                way_end = next((k for k in alt_steps if k > self.sel), end)
                button(row2, "+ Sub-step", self.add_substep, f"Add a step inside {nums[self.sel]}")
                button(row2, "Close branch", lambda: close(self.sel, way_end),
                       f"Remove {nums[self.sel]} (Ctrl+Z undoes)")
                return
            # A step shown only if something holds: its checks first, laid out like a branch's.
            adding = getattr(self, "_adding_cond", None) == self.sel
            self._adding_cond = None
            conditioned = not alt_steps and (bool(s.get("when")) or adding)
            if conditioned:
                first_item = cond_rows(None, allow_pick=False)
                tooltip(first_item, "Only show this step when this holds")
            row = ttk.Frame(self.mid)
            row.pack(fill="x", pady=(4, 0))
            button(row, "+ New Branch" if alt_steps else "+ Branch", self.add_alternative,
                   f"Add another way instead of {nums[self.sel]}")
            if alt_steps:
                button(row, "Close branches", lambda: close(alt_steps[0], end),
                       f"Remove {', '.join(nums[k] for k in alt_steps)} (Ctrl+Z undoes)")
            button(row, "+ Sub-step", self.add_substep, f"Add a step inside {nums[self.sel]}")
            if alt_steps:
                return
            if conditioned:
                if s.get("when"):
                    button(row, "+ Item", add_check, "Add another check")
                button(row, "Remove condition", lambda: self.change(lambda: s.pop("when", None)),
                       "Always show this step")
            else:
                def add_cond():
                    self._adding_cond = self.sel
                    self.select(self.sel)
                button(row, "+ Condition", add_cond, "Only show this step when…")

        def _ways(self) -> list[tuple[str, str]]:
            """The ways of the route's branches, as (label, condition): "12a's way (Broadsword < 2)"
            and "12's way (Broadsword >= 2)"."""
            from coach import _NOT
            steps = self.route["steps"]
            nums = step_numbers(steps)
            out, seen = [], set()
            for k, st in enumerate(steps):
                w = parse_when(st.get("when", "")) if st.get("alt") else None
                if not w or w[0] != "cmp":
                    continue
                p = self._head(k)
                if not any(steps[j].get("alt") and not steps[j].get("inside") for j in range(p + 1, k)):
                    for label, cond in ((f"{nums[k]}'s way", when_text(w[1], w[2], w[3])),
                                        (f"{nums[p]}'s way", when_text(w[1], _NOT[w[2]], w[3]))):
                        if cond not in seen:
                            seen.add(cond)
                            out.append((f"{label} ({cond})", cond))
            return out

        # -- sub-steps: done in the middle of a step, then back to it (11.1) ----
        def _sub_row(self, s: dict) -> bool:
            """For a sub-step: its buttons (nothing about branches), and an optional
            condition (only done when it holds). True if it is one."""
            steps = self.route["steps"]
            if not (s.get("inside") and self.sel > 0):
                return False
            nums = step_numbers(steps)
            adding = getattr(self, "_adding_cond", None) == self.sel
            self._adding_cond = None
            conditioned = bool(s.get("when")) or adding
            if conditioned:
                first = self._cond_rows_for(s)
                tooltip(first, "Only do this sub-step when this holds")
            row = ttk.Frame(self.mid)
            row.pack(fill="x", pady=(4, 0))
            def button(text, cmd, tip):
                b = ttk.Button(row, text=text, command=cmd)
                b.pack(side="left", padx=(0, 6))
                tooltip(b, tip)
            button("+ Sub-step", self.add_substep, f"Another sub-step of {nums[self._parent_of(self.sel)]}")
            button("Not a sub-step", lambda: self.change(lambda: s.pop("inside", None)), "Make it a normal step")
            if conditioned:
                if s.get("when"):
                    def add_check():
                        self._adding_check = self.sel
                        self.select(self.sel)
                    button("+ Item", add_check, "Add another check")
                button("Remove condition", lambda: self.change(lambda: s.pop("when", None)), "Always do it")
            else:
                def add_cond():
                    self._adding_cond = self.sel
                    self.select(self.sel)
                button("+ Condition", add_cond, "Only do this sub-step when…")
            return True

        def _cond_rows_for(self, s: dict):
            """The checks of s["when"]: the first, then more under it ("and")."""
            row = ttk.Frame(self.mid)
            row.pack(fill="x", pady=(4, 0))
            first = self._when_fields(row, s, allow_pick=False)
            first.pack_configure(padx=(0, 0))
            parts = self._when_parts(s)
            extra = getattr(self, "_adding_check", None) == self.sel
            self._adding_check = None
            for k in range(1, len(parts) + (1 if extra else 0)):
                more = ttk.Frame(self.mid)
                more.pack(fill="x", pady=(2, 0))
                ttk.Label(more, text="and").pack(side="left")
                self._when_fields(more, s, allow_pick=False, k=k)
                x = ttk.Button(more, text="\u2715", width=3,
                               command=(lambda k=k: self._set_when_part(s, k, None)) if k < len(parts)
                               else (lambda: self.select(self.sel)))
                x.pack(side="left", padx=6)
                tooltip(x, "Remove this check")
            return first

        def _parent_of(self, k: int) -> int:
            steps = self.route["steps"]
            while k > 0 and steps[k].get("inside"):
                k -= 1
            return k

        def add_substep(self):
            steps = self.route["steps"]
            p = self.sel
            while p > 0 and steps[p].get("inside"):
                p -= 1
            at = p + 1
            while at < len(steps) and steps[at].get("inside"):
                at += 1
            disc = steps[p].get("disc", 1)
            def do():
                taken = {(t.get("title") or "").lower() for t in steps}
                name, n = "New sub-step", 2
                while name.lower() in taken:
                    name, n = f"New sub-step {n}", n + 1
                steps.insert(at, {"disc": disc, "type": "move", "title": name,
                                  "lines": ["First instruction"], "inside": True})
                steps[p].setdefault("lines", []).append(f"[{name}]")   # where it comes in the step
                self.sel = at
            self.change(do)
            self.root.after(50, self._focus_title)
            self.status.configure(text="Sub-step added: when it's done the coach goes back to the step above "
                                       "(change Kind to Fight for a fight in the middle, like Baku)")

        def _head(self, i: int) -> int:
            """The step a branch starts at: back past its alternatives and sub-steps."""
            steps = self.route["steps"]
            while i > 0 and (steps[i].get("alt") or steps[i].get("inside")):
                i -= 1
            return i

        def _branch_end(self, p: int) -> int:
            """Where a step's branch ends: after its sub-steps and alternatives (and theirs)."""
            steps = self.route["steps"]
            at = p + 1
            while at < len(steps) and (steps[at].get("alt") or steps[at].get("inside")):
                at += 1
            return at

        def add_alternative(self):
            """A new way for the step: a copy of it with its sub-steps (Crash Site with Baku),
            after its other ways."""
            steps = self.route["steps"]
            p = self._head(self.sel)
            at = self._branch_end(p)
            block = [p] + [k for k in range(p + 1, at) if steps[k].get("inside") and self._parent_of(k) == p]
            def do():
                new = []
                for n, k in enumerate(block):
                    st = copy.deepcopy(steps[k])
                    st.pop("when", None)
                    if n == 0:
                        st["alt"] = True
                    if st.get("type") == "boss":
                        st["id"] = routes.step_id(self.route, st.get("title", ""),
                                                  {x.get("id") for x in steps + new if x.get("type") == "boss"})
                    new.append(st)
                steps[at:at] = new
                self.sel = at
            self.change(do)
            self.status.configure(text="New branch: set when it's taken, and change it to what to do instead")

        def _fight_pick(self, s: dict):
            """Pick the fight from the ready-made list (or your own); Custom is anything typed."""
            import fights_data
            row = ttk.Frame(self.mid)
            row.pack(fill="x", pady=(6, 0))
            ttk.Label(row, text="Fight", style="Muted.TLabel").pack(side="left")
            fights = fights_data.all_fights()
            labels = ["Custom"] + [fights_data.label(f) for f in fights]
            pick = ttk.Combobox(row, values=labels, state="readonly", width=34, height=24)
            current = next((fights_data.label(f) for f in fights if f["name"] == s.get("preset")), "Custom")
            pick.set(current)
            tooltip(pick, "Pick a fight to fill in its name and HP, or keep Custom and type your own").pack(
                side="left", padx=6)
            def chosen(_e=None):
                v = pick.get()
                if v == "Custom":
                    self.change(lambda: s.pop("preset", None))
                    return
                f = fights[labels.index(v) - 1]
                self.change(lambda: fights_data.apply(s, f))
            pick.bind("<<ComboboxSelected>>", chosen)
            def save_mine():
                name = (s.get("title") or "").strip()
                if not name:
                    return
                f = {"name": name, "disc": s.get("disc", 1)}
                for key in fights_data.FIELDS:
                    if s.get(key):
                        f[key] = s[key]
                fights_data.save_custom(f)
                self.change(lambda: s.__setitem__("preset", name))
                self.status.configure(text=f"Saved {name} to your fight list")
            tooltip(ttk.Button(row, text="Save to list", style="Small.TButton", command=save_mine),
                    "Add this fight (name, HP, fights, LV5) to your own list, to pick in other routes").pack(
                side="left")

        def _fight_extras(self, s: dict):
            """What only fights have: the HP the coach recognises them by, LV5 warnings."""
            groups = s.get("groups") or []
            if not groups:
                row = ttk.Frame(self.mid)
                row.pack(fill="x", pady=(10, 0))
                ttk.Label(row, text="Starting HP", style="Muted.TLabel").pack(side="left")
                hp = ttk.Entry(row, width=30)
                hp.insert(0, format_hp(s.get("hp", []), s.get("hp_names")))
                hp.pack(side="left", padx=6)
                def apply_hp():
                    v, names = parse_hp(hp.get())
                    s.pop("preset", None)              # typed by hand: a custom fight now
                    if v:
                        s["hp"] = v
                    else:
                        s.pop("hp", None)
                    if names:
                        s["hp_names"] = names
                    else:
                        s.pop("hp_names", None)
                self._live(hp, apply_hp)
                ttk.Label(row, text="with names: Zorn 229, Thorn 472",
                          style="Muted.TLabel").pack(side="left")
                row2 = ttk.Frame(self.mid)
                row2.pack(fill="x", pady=(8, 0))
                ttk.Label(row2, text="Fights", style="Muted.TLabel").pack(side="left")
                count = ttk.Spinbox(row2, from_=1, to=20, width=4)
                count.set(str(s.get("fights") or 1))
                count.pack(side="left", padx=6)
                def apply_count():
                    try:
                        n = int(count.get())
                    except ValueError:
                        return
                    if n > 1:
                        s["fights"] = n
                    else:
                        s.pop("fights", None)
                self._live(count, apply_count)
                count.configure(command=lambda: self.change(apply_count))
                ttk.Label(row2, text="in a row", style="Muted.TLabel").pack(side="left", padx=(0, 10))
                def to_groups():
                    def do():
                        s["groups"] = [{"name": s.get("title") or "Fight", "hp": list(s.get("hp") or []),
                                        "count": int(s.get("fights") or 1)},
                                       {"name": "", "hp": [], "count": 1}]
                        s.pop("fights", None)
                    self.change(do)
                b = ttk.Button(row2, text="+ Fight type", command=to_groups)
                b.pack(side="left")
                tooltip(b, "Another kind of fight on this step (soldiers and mages)")
            else:
                self._label("Fights (any order)")
                for k, g in enumerate(groups):
                    row = ttk.Frame(self.mid)
                    row.pack(fill="x", pady=(2, 0))
                    name = ttk.Entry(row, width=16)
                    name.insert(0, g.get("name", ""))
                    name.pack(side="left")
                    tooltip(name, "Name, shown on the panel with how many are done")
                    ttk.Label(row, text=" HP", style="Muted.TLabel").pack(side="left")
                    hpe = ttk.Entry(row, width=14)
                    hpe.insert(0, ", ".join(str(h) for h in g.get("hp", [])))
                    hpe.pack(side="left", padx=4)
                    tooltip(hpe, "The enemies' starting HP in this kind of fight")
                    ttk.Label(row, text="\u00d7", style="Muted.TLabel").pack(side="left")
                    cnt = ttk.Spinbox(row, from_=1, to=20, width=3)
                    cnt.set(str(g.get("count") or 1))
                    cnt.pack(side="left", padx=4)
                    tooltip(cnt, "How many of these fights")
                    def save(_e=None, g=g, name=name, hpe=hpe, cnt=cnt):
                        try:
                            c = max(1, int(cnt.get()))
                        except ValueError:
                            c = 1
                        hps = [int(x) for x in re.findall(r"\d+", hpe.get())]
                        new = {"name": name.get().strip(), "hp": hps, "count": c}
                        if {k: g.get(k) for k in new} != new:
                            self.change(lambda: (g.update(new), s.pop("preset", None)))
                    for w in (name, hpe, cnt):
                        w.bind("<FocusOut>", save)
                        w.bind("<Return>", save)
                    cnt.configure(command=save)
                    def drop(k=k):
                        def do():
                            del s["groups"][k]
                            if len(s["groups"]) == 1:              # one kind left: a plain fight again
                                g0 = s.pop("groups")[0]
                                if g0.get("hp"):
                                    s["hp"] = g0["hp"]
                                if int(g0.get("count") or 1) > 1:
                                    s["fights"] = int(g0["count"])
                        self.change(do)
                    x = ttk.Button(row, text="\u2715", width=3, command=drop)
                    x.pack(side="left", padx=4)
                    tooltip(x, "Remove this kind")
                b = ttk.Button(self.mid, text="+ Fight type",
                               command=lambda: self.change(lambda: s["groups"].append({"name": "", "hp": [], "count": 1})))
                b.pack(anchor="w", pady=(4, 0))
                tooltip(b, "Another kind of fight on this step")
            n = int(s.get("fights") or 1)
            if n > 1:
                keys = self._fight_keys(s)
                got = sum(1 for k in keys if self._learned_fight(k))
                ttk.Label(self.mid, style="Good.TLabel" if got == n else "Muted.TLabel",
                          text=(f"✓ All {n} fights learned" if got == n else
                                f"{got} of {n} fights learned: each is learned when you win it "
                                "(F8 during one the coach missed)")).pack(anchor="w", pady=(8, 0))
            lv5 = tk.BooleanVar(value=bool(s.get("lv5")))
            def set_lv5():
                if lv5.get():
                    s["lv5"] = True
                else:
                    s.pop("lv5", None)
            cb = ttk.Checkbutton(self.mid, variable=lv5, command=lambda: self.change(set_lv5),
                                 text="LV5 Death warning")
            cb.pack(anchor="w", pady=(8, 0))
            tooltip(cb, "During this fight the panel warns about anyone in the party whose level is a multiple of 5")
            extra = f"Fight id: {s.get('id', '?')}"
            if s.get("needs"):
                extra += f" · has {len(s['needs'])} HP check(s), kept as they are"
            ttk.Label(self.mid, text=extra, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        @staticmethod
        def _learned_fight(step_id):
            try:
                return step_id in json.loads(LEARNED.read_text()).get("fingerprints", {})
            except (OSError, ValueError):
                return False

        @staticmethod
        def _learned_battle_id(step_id):
            try:
                return json.loads(LEARNED.read_text()).get("battle_ids", {}).get(step_id)
            except (OSError, ValueError):
                return None

        def _label(self, text, parent=None):
            ttk.Label(parent or self.mid, text=text, style="Muted.TLabel").pack(anchor="w", pady=(10, 2))

        def _text(self, lines, height, parent=None, fight=False):
            frame = ttk.Frame(parent or self.mid)
            frame.pack(fill="both", expand=True)
            t = tk.Text(frame, height=height, wrap="none", undo=True, font=("Consolas", 10),
                        relief="solid", borderwidth=1, padx=6, pady=4)
            self._colour_tk(t)
            sb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=sb.set)
            t.insert("1.0", "\n".join(lines))
            t.edit_reset()
            t.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            t.tag_configure("remove", foreground=self.th["remove"])
            for kind, colour in self.th["kinds"].items():
                t.tag_configure(kind, foreground=colour)
            t.tag_configure("unchecked", underline=True)
            t.tag_raise("remove")
            def color(_e=None):
                """Each line in its type's colour as you type; "-Silk Shirt" red; lines of a
                checked type the coach can't check underlined."""
                for tag in ("remove", "unchecked", *KIND_COLOURS):
                    t.tag_remove(tag, "1.0", "end")
                typed = t.get("1.0", "end-1c").split("\n")
                read_as = expand_headers(typed)        # indented lines under "Buy" are buys
                for n, line in enumerate(typed, 1):
                    if line.strip().startswith("#"):
                        kind, info = "comment", None
                    elif line.strip().startswith("@"):
                        info = classify(line)
                        kind = "ap"
                    elif fight:
                        kind = "steal" if steal_targets([line]) else "note"
                        info = None
                    else:
                        info = classify(read_as[n - 1] or line)
                        kind = info.kind
                    t.tag_add(kind, f"{n}.0", f"{n}.end")
                    if info is not None and info.kind not in ("note", "story", "substep") and (info.problem or not info.checked):
                        t.tag_add("unchecked", f"{n}.0", f"{n}.end")
                    col = 0                          # position in the line as typed
                    for text, off in line_segments(line):
                        start = col
                        if off and line[col:col + 1] == "-":
                            col += 1                 # the dash isn't in the piece's text
                        col += len(text)
                        if off:
                            t.tag_add("remove", f"{n}.{start}", f"{n}.{col}")
            t.bind("<KeyRelease>", color, add="+")
            t.bind("<<Paste>>", lambda e: t.after(1, color), add="+")
            color()
            return t

        def _warn(self, label, lines, kinds=False):
            msgs = []
            if kinds:
                for l in expand_headers(lines):
                    info = classify(l)
                    if info.kind != "note" and info.problem:
                        msgs.append(f"Underlined \"{l[:28]}\": {info.problem}")
            long = [l for l in lines if len(l) > LIMIT and not l.strip().startswith("#")]
            if long:
                msgs.append(f"{len(long)} line(s) over {LIMIT} characters will wrap on the panel: " + long[0][:40] + "…")
            label.configure(text="\n".join(msgs[:3]))

        def _live(self, widget, apply):
            """Apply typing straight into the route; one undo point per field."""
            def on_key(_e=None):
                if not self.snap_taken:
                    self._push_undo()
                    self.snap_taken = True
                apply()
                # The list and the panel catch up once typing pauses, not on every key.
                if getattr(self, "_typing_job", None):
                    self.root.after_cancel(self._typing_job)
                self._typing_job = self.root.after(250, self._typed)
            widget.bind("<KeyRelease>", on_key)
            widget.bind("<<Paste>>", lambda e: self.root.after(1, on_key))
            widget.bind("<FocusOut>", lambda e: setattr(self, "snap_taken", False))

        def _typed(self):
            self._typing_job = None
            self.after_edit(rebuild=False)

        # -- edits ------------------------------------------------------------
        def _push_undo(self):
            self.undo_stack.append(json.dumps({"route": self.route, "sel": self.sel}))
            del self.undo_stack[:-200]
            self.redo_stack.clear()

        def change(self, fn, keep_multi=False):
            self._push_undo()
            if not keep_multi:
                self.multi = set()
            fn()
            self.after_edit(rebuild=True)

        def after_edit(self, rebuild: bool):
            if rebuild:
                self.select(self.sel)
            else:
                self.fill_list()
                self.refresh_side()

        def change_kind(self, s, kind):
            if kind == s["type"]:
                return
            def do():
                if kind == "boss" and not s.get("id"):
                    s["id"] = routes.step_id(self.route, s.get("title", ""))
                s["type"] = kind
            self.change(do)

        def add(self, kind):
            disc = self.route["steps"][self.sel].get("disc", 1) if self.route["steps"] else 1
            def do():
                if kind == "boss":
                    step = {"disc": disc, "type": "boss", "title": "New fight", "lines": ["Attack"],
                            "id": routes.step_id(self.route, "New fight")}
                else:
                    step = {"disc": disc, "type": kind, "title": "New menu" if kind == "menu" else "New step",
                            "lines": ["First instruction"]}
                at = self.sel + 1 if self.route["steps"] else 0
                self.route["steps"].insert(at, step)
                self.sel = at
            self.change(do)
            self.root.after(50, self._focus_title)

        def _focus_title(self):
            w = getattr(self, "title_entry", None)
            if w is not None and w.winfo_exists():
                w.focus_set()
                w.select_range(0, "end")

        def move(self, d):
            j = self.sel + d
            if not (0 <= j < len(self.route["steps"])):
                return
            def do():
                st = self.route["steps"]
                st[self.sel], st[j] = st[j], st[self.sel]
                self.sel = j
            self.change(do)

        def duplicate(self):
            picked = self.selected()
            if not picked:
                return
            self._insert_steps([copy.deepcopy(self.route["steps"][i]) for i in picked], after=picked[-1])
            self.status.configure(text=f"Duplicated {len(picked)} step(s)")

        def delete(self):
            picked = self.selected()
            if not picked:
                return
            def do():
                for i in reversed(picked):
                    self.route["steps"].pop(i)
                self.sel = max(0, min(picked[0], len(self.route["steps"]) - 1))
            self.change(do)
            self.status.configure(text=f"Deleted {len(picked)} step(s) · Ctrl+Z to undo")

        # -- copy and paste (works between routes, through the clipboard) ------
        CLIP_KEY = "ffix-coach-steps"

        def copy_steps(self):
            picked = self.selected()
            if not picked:
                return
            data = json.dumps({self.CLIP_KEY: [self.route["steps"][i] for i in picked]}, ensure_ascii=False)
            self.root.clipboard_clear()
            self.root.clipboard_append(data)
            self.status.configure(text=f"Copied {len(picked)} step(s) · Ctrl+V pastes after the selected step")

        def cut_steps(self):
            self.copy_steps()
            self.delete()

        def paste_steps(self):
            try:
                data = json.loads(self.root.clipboard_get())
                steps = data[self.CLIP_KEY]
                assert isinstance(steps, list)
            except Exception:
                self.status.configure(text="Nothing to paste: copy steps first (Ctrl+C in the list)")
                return
            picked = self.selected()
            self._insert_steps(copy.deepcopy(steps), after=picked[-1] if picked else -1)
            self.status.configure(text=f"Pasted {len(steps)} step(s)")

        def _insert_steps(self, steps: list, after: int):
            def do():
                taken = {s.get("id") for s in self.route["steps"] if s.get("type") == "boss"}
                for s in steps:
                    if s.get("type") == "boss" and (not s.get("id") or s["id"] in taken):
                        s["id"] = routes.step_id(self.route, s.get("title", ""), taken)
                    taken.add(s.get("id"))
                at = after + 1
                self.route["steps"][at:at] = steps
                self.sel = at
                self.multi = set(range(at, at + len(steps))) if len(steps) > 1 else set()
            self.change(do, keep_multi=True)

        def move_selected(self, d):
            picked = self.selected()
            if len(picked) <= 1:
                self.move(d)
                return
            lo, hi = picked[0], picked[-1]
            if picked != list(range(lo, hi + 1)) or not (0 <= lo + d and hi + d < len(self.route["steps"])):
                return                                   # only a block of steps next to each other
            def do():
                st = self.route["steps"]
                block = st[lo:hi + 1]
                del st[lo:hi + 1]
                st[lo + d:lo + d] = block
                self.sel += d
                self.multi = {i + d for i in picked}
            self.change(do, keep_multi=True)

        def _key(self, fn):
            """A list/editor shortcut: left alone while typing in a box."""
            def handler(_e):
                if self._in_field():
                    return None
                fn()
                return "break"
            return handler

        def undo(self):
            if not self.undo_stack:
                return
            self.redo_stack.append(json.dumps({"route": self.route, "sel": self.sel}))
            st = json.loads(self.undo_stack.pop())
            self.route, self.sel = st["route"], st["sel"]
            self.select(self.sel)

        def redo(self):
            if not self.redo_stack:
                return
            self.undo_stack.append(json.dumps({"route": self.route, "sel": self.sel}))
            st = json.loads(self.redo_stack.pop())
            self.route, self.sel = st["route"], st["sel"]
            self.select(self.sel)

        def _in_field(self):
            w = self.root.focus_get()
            return isinstance(w, (tk.Text, tk.Entry, ttk.Entry))

        def _ctrl_z(self, _e):
            if self._in_field():
                return None            # the box's own undo
            self.undo()
            return "break"

        def _ctrl_y(self, _e):
            if self._in_field():
                return None
            self.redo()
            return "break"

        # -- side panel -------------------------------------------------------
        def refresh_side(self):
            p = self.preview
            p.configure(state="normal")
            p.delete("1.0", "end")
            if self.route["steps"]:
                s = self.route["steps"][self.sel]
                boss = s["type"] == "boss"
                nums = step_numbers(self.route["steps"])
                total = sum(1 for k, t in enumerate(self.route["steps"])
                            if not t.get("alt") and not (t.get("inside") and k > 0))
                p.insert("end", f"Disc {s.get('disc')} · {nums[self.sel]}/{total}\n", "head")
                title = s.get("title") or ("Fight" if boss else "Menu" if s["type"] == "menu" else "Next")
                rep = getattr(self, "rep", None) or []
                learn = self.sel < len(rep) and not rep[self.sel]["ok"]
                if learn:
                    title = "\u2691 " + title          # as on the coach panel: it still has to learn this step
                p.insert("end", f"{title}\n", ("title", s["type"]))
                from coach import line_segments
                first = range(0)                       # no highlight on the panel any more
                for i, l in enumerate(s.get("lines", [])):
                    sub = l[:1] in (" ", "\t") and bool(l.strip())
                    l = l.strip()
                    if l.startswith("#"):
                        p.insert("end", l.lstrip("#").strip(), "comment")
                        p.insert("end", "\n")
                        continue
                    if l.startswith("@"):
                        is_lv = re.match(r"^@\s*(lv|level)\b", l.strip(), re.I)
                        p.insert("end", ("    " if sub else "\u25c6 ") + l[1:].strip()
                                 + ("  (level)\n" if is_lv else "  (AP)\n"), "ap")
                        continue
                    if not l:
                        p.insert("end", "\n", "gap")
                    elif i in first:
                        p.insert("end", "    " if sub else "▶ ", "hl")
                        for text, off in line_segments(l):
                            p.insert("end", text, ("hl", "remove_hl") if off else "hl")
                        p.insert("end", "\n")
                    elif substep_call(l):
                        p.insert("end", ("    " if sub else "• ") + "\u21b3 " + substep_call(l) + "\n", "substep")
                    elif story_parts(l):
                        p.insert("end", "    " if sub else "• ")
                        for k, part in enumerate(story_parts(l)):
                            if k:
                                p.insert("end", " \u203a ", "head")
                            p.insert("end", part, "story")
                        p.insert("end", "\n")
                    else:
                        p.insert("end", "    " if sub else "• ")
                        for text, off in line_segments(l):
                            p.insert("end", text, "remove" if off else ())
                        p.insert("end", "\n")
                if boss and s.get("groups"):          # kinds of fight: how many of each
                    for g in s["groups"]:
                        p.insert("end", f"{g.get('name') or 'Fight'} 0/{g.get('count') or 1}\n", "head")
                if not boss:
                    for j in range(self.sel + 1, len(self.route["steps"])):
                        nxt = self.route["steps"][j]
                        if not nxt.get("inside"):
                            break
                        called = {(substep_call(x) or "").lower() for x in s.get("lines", [])}
                        if (nxt.get("title") or "").strip().lower() in called:
                            continue                       # shown where the step calls it
                        p.insert("end", f"On the way: {nxt.get('title') or 'sub-step'}", "comment")
                        p.insert("end", "\n")
                if s.get("inside") and self.sel > 0:
                    p.insert("end", "Sub-step: goes back to the step above when done\n", "note")
                if s.get("backup"):
                    p.insert("end", "\n")
                    for bl in s["backup"].split("\n"):
                        p.insert("end", ("    " + bl.strip() if bl[:1] in (" ", "\t") else bl) + "\n", "note")
                self._preview_next(p)
            p.configure(state="disabled")
            self.problems = find_problems(self.route)
            try:
                import report
                self.problems += report.run_problems(dict(self.route, name=self.name),
                                                     getattr(self, "learned", None))
            except Exception:
                pass
            self.checks.delete(0, "end")
            self.checks_label.configure(text=f"Checks ({len(self.problems)})" if self.problems else "Checks")
            if not self.problems:
                self.checks.insert("end", "✓ No problems found")
            for _, _, msg in self.problems[:200]:
                self.checks.insert("end", msg)
            self.update_status()
            if getattr(self, "cmp_win", None) is not None and self.cmp_win.winfo_exists() \
                    and not getattr(self, "_cmp_due", False):
                self._cmp_due = True                   # keep the compare window up to date
                def again():
                    self._cmp_due = False
                    self._cmp_fill()
                self.root.after(400, again)

        def _preview_next(self, p):
            """The next step under the preview, as on the panel when "Show the next step
            underneath" is ticked in the launcher (config.json: overlay.show_next)."""
            try:
                from paths import CONFIG
                if not json.loads(CONFIG.read_text(encoding="utf-8")).get("overlay", {}).get("show_next"):
                    return
            except (OSError, ValueError):
                return
            steps = self.route["steps"]
            s = steps[self.sel]
            if s.get("inside") and self.sel > 0:
                j = self.sel - 1
                while j > 0 and steps[j].get("inside"):
                    j -= 1
            else:
                from coach import route_branches
                per = route_branches(steps)[0]
                def other(t):                            # a step of the other way this step's choice goes
                    k = steps.index(t)
                    return bool(per[self.sel] and per[k] and per[k][0] == per[self.sel][0]
                                and per[k][1] != per[self.sel][1])
                j = self.sel + 1
                while j < len(steps) and ((s.get("type") != "boss" and steps[j].get("inside"))
                                          or other(steps[j])):
                    j += 1
            if j >= len(steps) or j == self.sel:
                return
            n = steps[j]
            rep = getattr(self, "rep", None) or []
            title = n.get("title") or ("Fight" if n["type"] == "boss" else "Menu" if n["type"] == "menu" else "Next")
            if j < len(rep) and not rep[j]["ok"]:
                title = "\u2691 " + title
            p.insert("end", "\n" + "─" * 30 + "\n", "head")
            p.insert("end", f"Next: {title}\n", "head")
            for l in n.get("lines", []):
                if l.strip().startswith("@"):
                    continue
                sub = l[:1] in (" ", "\t") and bool(l.strip())
                t = l.strip()
                if not t:
                    p.insert("end", "\n", "gap")
                elif t.startswith("#"):
                    p.insert("end", "      " + t.lstrip("#").strip() + "\n", "head")
                else:
                    p.insert("end", ("      " if sub else "• ") + t + "\n", "head")

        def _on_check(self, _e):
            sel = self.checks.curselection()
            if sel and self.problems and self.problems[sel[0]][0] is not None:
                self.search.set("")
                self.disc_filter.set("All")
                self.select(self.problems[sel[0]][0])

        # -- files --------------------------------------------------------------
        def dirty(self) -> bool:
            return json.dumps(self.route, sort_keys=True) != self.saved

        def update_status(self):
            d = self.dirty()
            self.status.configure(text="Unsaved changes" if d else "All changes saved",
                                  foreground=self.th["warn"] if d else self.th["good"])
            self.b_save.state(["!disabled"] if d else ["disabled"])
            self.b_undo.state(["!disabled"] if self.undo_stack else ["disabled"])
            self.b_redo.state(["!disabled"] if self.redo_stack else ["disabled"])
            self.root.title(("● " if d else "") + f"Route Editor · {self.name}")

        def save(self):
            if not self.dirty():
                return
            problems = check_route(self.route)
            if problems:
                messagebox.showerror("Not saved", "\n".join(problems[:15]), parent=self.root)
                return
            try:
                when = save_route(self.route, self.file())
            except OSError as e:
                messagebox.showerror("Not saved", str(e), parent=self.root)
                return
            self.saved = json.dumps(self.route, sort_keys=True)
            self.mtime = self.file().stat().st_mtime
            self.update_status()
            self.status.configure(text=f"Saved {when} · the coach picks it up in a few seconds")

        def reload(self, ask=True):
            if ask and self.dirty() and not messagebox.askyesno(
                    "Reload", "Throw away your unsaved changes and load the saved route again?", parent=self.root):
                return
            self.route = routes.inline_cards(json.loads(self.file().read_text(encoding="utf-8")))
            self.saved = json.dumps(self.route, sort_keys=True)
            self.mtime = self.file().stat().st_mtime
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.here = self._here_key()
            self.select(min(self.sel, len(self.route["steps"]) - 1))

        def _watch_file(self):
            """route.json changed outside the editor (e.g. by the coach's author): offer to load it."""
            try:
                m = self.file().stat().st_mtime
                if m != self.mtime:
                    self.mtime = m
                    if not self.dirty():
                        self.reload(ask=False)
                        self.status.configure(text="The route changed on disk: loaded it")
                    elif messagebox.askyesno("Route changed",
                                             "The route file was changed outside the editor.\n"
                                             "Load it and lose your unsaved changes?", parent=self.root):
                        self.reload(ask=False)
                lm = LEARNED.stat().st_mtime if LEARNED.exists() else 0
                if lm != getattr(self, "learned_mtime", lm):
                    self.fill_list()                     # what it learned shows as soon as it is
                    if not self._in_field():
                        self.build_editor()
                self.learned_mtime = lm
                h = self._here_key()
                if h != self.here:
                    self.here = h
                    self.fill_list()
            except OSError:
                pass
            self.root.after(2000, self._watch_file)

        def _here_key(self):
            try:
                return json.loads(routes.progress_path(self.name).read_text()).get("key")
            except (OSError, ValueError):
                return None

        def file(self) -> Path:
            return routes.path(self.name)

        # -- routes -----------------------------------------------------------
        def _leave_ok(self) -> bool:
            """Before switching route: save or drop unsaved changes (False = stay)."""
            if not self.dirty():
                return True
            ans = messagebox.askyesnocancel("Unsaved changes", f"Save your changes to {self.name} first?",
                                            parent=self.root)
            if ans is None:
                return False
            if ans:
                self.save()
                return not self.dirty()
            return True

        def open_route(self, name: str):
            if name == self.name:
                return
            if not self._leave_ok():
                self.route_pick.set(self.name)
                return
            self.name = name
            routes.set_active(name)                      # the coach switches to it too
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.route = routes.inline_cards(json.loads(self.file().read_text(encoding="utf-8")))
            self.saved = json.dumps(self.route, sort_keys=True)
            self.mtime = self.file().stat().st_mtime
            self.here = self._here_key()
            self.route_pick.configure(values=routes.names())
            self.route_pick.set(name)
            i = next((k for k, s in enumerate(self.route["steps"]) if step_key(s) == self.here), 0)
            self.select(i)
            self.status.configure(text=f"Opened {name} · the coach now runs this route")

        def new_route(self):
            n = simpledialog.askstring("New route", "Name for the new route:", parent=self.root)
            if n and n.strip() and self._leave_ok():
                name = routes.create(n)
                self.saved = json.dumps(self.route, sort_keys=True)   # already saved or dropped
                self.name = None
                self.open_route(name)

        def copy_route(self):
            n = simpledialog.askstring("Copy route", f"Name for the copy of {self.name}:",
                                       initialvalue=f"{self.name} copy", parent=self.root)
            if n and n.strip() and self._leave_ok():
                name = routes.create(n, copy_from=self.name)
                self.saved = json.dumps(self.route, sort_keys=True)   # already saved or dropped
                self.name = None
                self.open_route(name)

        def rename_route(self):
            n = simpledialog.askstring("Rename route", "New name:", initialvalue=self.name, parent=self.root)
            if not n or not n.strip() or not self._leave_ok():
                return
            try:
                new = routes.rename(self.name, n)
            except (ValueError, OSError) as err:
                messagebox.showerror("Not renamed", str(err), parent=self.root)
                return
            self.name = new
            self.route = routes.inline_cards(json.loads(self.file().read_text(encoding="utf-8")))
            self.saved = json.dumps(self.route, sort_keys=True)
            self.mtime = self.file().stat().st_mtime
            self.route_pick.configure(values=routes.names())
            self.route_pick.set(new)
            self.update_status()

        def delete_route(self):
            if len(routes.names()) < 2:
                messagebox.showinfo("Delete route", "This is the only route. Make another one first.",
                                    parent=self.root)
                return
            if not messagebox.askyesno("Delete route", f"Delete {self.name}?\n\nA copy goes to the backups folder.",
                                       parent=self.root):
                return
            gone = self.name
            routes.delete(gone)
            self.saved = json.dumps(self.route, sort_keys=True)   # nothing to save
            self.name = None
            self.open_route(routes.names()[0])
            self.status.configure(text=f"Deleted {gone} (copy in backups)")

        # -- comparing with another route -----------------------------------------
        def open_compare(self):
            others = [n for n in routes.names() if n != self.name]
            if not others:
                messagebox.showinfo("Compare", "There's no other route to compare with yet.", parent=self.root)
                return
            if getattr(self, "cmp_win", None) is not None and self.cmp_win.winfo_exists():
                self.cmp_win.lift()
                return
            th = self.th
            w = self.cmp_win = tk.Toplevel(self.root)
            w.title(f"Compare routes")
            w.geometry("1250x780")
            w.configure(bg=th["bg"])
            top = ttk.Frame(w, padding=8)
            top.pack(fill="x")
            ttk.Label(top, text="This route:", style="Muted.TLabel").pack(side="left")
            self.cmp_this = ttk.Label(top, text=self.name, font=("Segoe UI", 10, "bold"))
            self.cmp_this.pack(side="left", padx=(4, 16))
            ttk.Label(top, text="compared with", style="Muted.TLabel").pack(side="left")
            self.cmp_pick = ttk.Combobox(top, values=others, state="readonly", width=38)
            self.cmp_pick.set(others[0])
            self.cmp_pick.pack(side="left", padx=6)
            self.cmp_pick.bind("<<ComboboxSelected>>", lambda e: self._cmp_fill())
            self.cmp_only = tk.BooleanVar(value=True)
            ttk.Checkbutton(top, text="Only differences", variable=self.cmp_only,
                            command=self._cmp_fill).pack(side="left", padx=10)
            self.cmp_sum = ttk.Label(top, text="", style="Muted.TLabel")
            self.cmp_sum.pack(side="left", padx=10)

            pane = ttk.PanedWindow(w, orient="vertical")
            pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            tf = ttk.Frame(pane)
            pane.add(tf, weight=3)
            cols = ("a", "this", "b", "other", "diff")
            t = self.cmp_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
            for c, label, width in (("a", "#", 50), ("this", "This route", 330), ("b", "#", 50),
                                    ("other", "Other route", 330), ("diff", "What's different", 360)):
                t.heading(c, text=label)
                t.column(c, width=width, stretch=c in ("this", "other", "diff"), anchor="w")
            sb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=sb.set)
            t.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            t.tag_configure("changed", foreground=th["warn"])
            t.tag_configure("only_a", foreground=th["remove"])
            t.tag_configure("only_b", foreground=th["good"])
            t.tag_configure("same", foreground=th["muted"])
            t.bind("<<TreeviewSelect>>", lambda e: self._cmp_show())
            t.bind("<Double-1>", lambda e: self._cmp_goto())

            bf = ttk.Frame(pane, padding=(0, 6, 0, 0))
            pane.add(bf, weight=2)
            btns = ttk.Frame(bf)
            btns.pack(fill="x", pady=(0, 4))
            self.cmp_take = ttk.Button(btns, text="Use their step in this route", command=self._cmp_take)
            self.cmp_take.pack(side="left")
            ttk.Button(btns, text="Go to this step", command=self._cmp_goto).pack(side="left", padx=6)
            ttk.Label(btns, text="Red: only in this route · Green: only in the other · Orange: in both, "
                      "but different. Double-click a row to open the step.",
                      style="Muted.TLabel").pack(side="left", padx=10)
            texts = ttk.Frame(bf)
            texts.pack(fill="both", expand=True)
            self.cmp_texts = []
            for side in ("This route", "Other route"):
                col = ttk.Frame(texts)
                col.pack(side="left", fill="both", expand=True, padx=(0, 6))
                head = ttk.Label(col, text=side, font=("Segoe UI", 10, "bold"))
                head.pack(anchor="w")
                tx = tk.Text(col, height=12, wrap="word", font=("Consolas", 10), relief="solid", borderwidth=1,
                             padx=6, pady=4)
                self._colour_tk(tx)
                tx.tag_configure("del", background="#5c2626" if self.dark.get() else "#fde2e2")
                tx.tag_configure("add", background="#1f4d2b" if self.dark.get() else "#dcf5e3")
                tx.tag_configure("head", foreground=th["muted"])
                tx.pack(fill="both", expand=True)
                self.cmp_texts.append((head, tx))
            self._cmp_fill()

        def _cmp_other(self) -> dict | None:
            try:
                return routes.inline_cards(json.loads(routes.path(self.cmp_pick.get()).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return None

        def _cmp_fill(self):
            if getattr(self, "cmp_win", None) is None or not self.cmp_win.winfo_exists():
                return
            self.cmp_this.configure(text=self.name)
            self.cmp_pick.configure(values=[n for n in routes.names() if n != self.name])
            other = self.cmp_other_route = self._cmp_other()
            t = self.cmp_tree
            keep = t.selection()
            t.delete(*t.get_children())
            if other is None:
                self.cmp_sum.configure(text="Couldn't read that route.")
                return
            mine = self.route
            self.cmp_rows = compare_routes(mine, other)
            from collections import Counter
            n = Counter(r["status"] for r in self.cmp_rows)
            self.cmp_sum.configure(text=f"{n['same']} same · {n['changed']} different · "
                                        f"{n['only_a']} only here · {n['only_b']} only there")
            for k, r in enumerate(self.cmp_rows):
                if self.cmp_only.get() and r["status"] == "same":
                    continue
                a, b = r["a"], r["b"]
                la = step_label(mine, mine["steps"][a]) if a is not None else ""
                lb = step_label(other, other["steps"][b]) if b is not None else ""
                ka = f"{KIND_NAME.get(mine['steps'][a]['type'], '')}: " if a is not None else ""
                kb = f"{KIND_NAME.get(other['steps'][b]['type'], '')}: " if b is not None else ""
                what = {"same": "", "only_a": "only in this route", "only_b": "only in the other route"}.get(
                    r["status"], ", ".join(r["diff"]))
                t.insert("", "end", iid=str(k), tags=(r["status"],),
                         values=(a + 1 if a is not None else "", ka + la, b + 1 if b is not None else "",
                                 kb + lb, what))
            if keep and t.exists(keep[0]):
                t.selection_set(keep[0])
                t.see(keep[0])
            self._cmp_show()

        def _cmp_row(self) -> dict | None:
            sel = self.cmp_tree.selection()
            return self.cmp_rows[int(sel[0])] if sel else None

        @staticmethod
        def _cmp_step_text(s: dict | None) -> list[str]:
            if s is None:
                return []
            out = [f"{KIND_NAME.get(s.get('type'), '')} · Disc {s.get('disc', 1)} · {s.get('title') or ''}"]
            out += [""] + list(s.get("lines") or [])
            if s.get("backup"):
                out += ["", "Backup note:"] + s["backup"].split("\n")
            if s.get("hp"):
                out += ["", "HP: " + format_hp(s.get("hp", []), s.get("hp_names"))]
            if s.get("fights"):
                out += [f"Fights: {s['fights']}"]
            return out

        def _cmp_show(self):
            r = self._cmp_row()
            other = self.cmp_other_route
            sa = self.route["steps"][r["a"]] if r and r["a"] is not None else None
            sb = other["steps"][r["b"]] if r and other and r["b"] is not None else None
            la, lb = self._cmp_step_text(sa), self._cmp_step_text(sb)
            import difflib
            mark_a, mark_b = set(), set()
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [l.rstrip() for l in la],
                                                               [l.rstrip() for l in lb]).get_opcodes():
                if op != "equal":
                    mark_a.update(range(i1, i2))
                    mark_b.update(range(j1, j2))
            for (head, tx), lines, marks in ((self.cmp_texts[0], la, mark_a), (self.cmp_texts[1], lb, mark_b)):
                tx.configure(state="normal")
                tx.delete("1.0", "end")
                for n, l in enumerate(lines):
                    tags = ["head"] if n == 0 else []
                    if n in marks and l.strip():
                        tags.append("del" if tx is self.cmp_texts[0][1] else "add")
                    tx.insert("end", l + "\n", tuple(tags))
                if not lines:
                    tx.insert("end", "(not in this route)" if tx is self.cmp_texts[0][1] else "(not in that route)",
                              ("head",))
                tx.configure(state="disabled")
            self.cmp_texts[1][0].configure(text=self.cmp_pick.get())
            self.cmp_texts[0][0].configure(text=self.name)
            ok = bool(r and r["b"] is not None and r["status"] != "same")
            self.cmp_take.state(["!disabled"] if ok else ["disabled"])

        def _cmp_take(self):
            r = self._cmp_row()
            if not r or r["b"] is None or self.cmp_other_route is None:
                return
            where = [0]
            other = self.cmp_other_route
            self.change(lambda: where.__setitem__(0, take_step(self.route, other, r)))
            self.select(where[0])
            self.status.configure(text=f"Step {where[0] + 1} taken from {self.cmp_pick.get()} (Ctrl+Z undoes it)")
            self._cmp_fill()

        def _cmp_goto(self):
            r = self._cmp_row()
            if r and r["a"] is not None:
                self.search.set("")
                self.disc_filter.set("All")
                self.select(r["a"])
                self.root.lift()

        def close(self):
            if self.dirty():
                ans = messagebox.askyesnocancel("Unsaved changes", "Save your changes before closing?",
                                                parent=self.root)
                if ans is None:
                    return
                if ans:
                    self.save()
                    if self.dirty():
                        return
            self.root.destroy()

    root = tk.Tk()
    root.editor = Editor(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
