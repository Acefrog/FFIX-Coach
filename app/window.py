"""FFIX Coach: the one window. Coach (start and stop it, pick the route), the route editor,
Memory setup and help are pages of it; the coach runs inside it, and its panel over the game
is the only other window.

    double-click "FFIX Coach.pyw"   (or: python app\\window.py)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import routes  # noqa: E402
from paths import CONFIG, log, write_atomic  # noqa: E402

PAGES = [("coach", "Coach"), ("editor", "Route editor"), ("setup", "Memory setup"), ("help", "Help")]
LOG_LINES = 400                         # the Coach page keeps the last this many lines


class _Out:
    """Where print() goes: logs/coach_output.txt, and the Coach page's messages."""

    def __init__(self):
        try:
            self.file = open(log("coach_output.txt"), "a", encoding="utf-8", buffering=1)
        except OSError:
            self.file = None
        self.pending: list[str] = []

    def write(self, s: str) -> int:
        if self.file:
            try:
                self.file.write(s)
            except (OSError, ValueError):
                pass
        self.pending.append(s)
        return len(s)

    def flush(self) -> None:
        pass

    def take(self) -> str:
        out, self.pending = "".join(self.pending), []
        return out


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _set_config(change) -> None:
    cfg = _config()
    change(cfg)
    write_atomic(CONFIG, json.dumps(cfg, indent=2))


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        import editor
        from main import CoachSession
        self.tk, self.ttk = tk, ttk
        self.root = root
        root.title("FFIX Coach")
        root.minsize(1200, 640)                   # (room for every column of the route editor)
        w = _config().get("window", {})
        root.geometry(w.get("geometry") or "1320x820")
        if w.get("zoomed"):
            try:
                root.state("zoomed")
            except tk.TclError:
                pass
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.side = ttk.Frame(root, padding=(10, 12, 0, 12), style="Side.TFrame")
        self.side.pack(side="left", fill="y")
        self.pages_box = ttk.Frame(root)
        self.pages_box.pack(side="left", fill="both", expand=True)
        self.pages: dict[str, ttk.Frame] = {name: ttk.Frame(self.pages_box) for name, _ in PAGES}
        self.page = tk.StringVar(value="coach")

        # The editor first: its theme (light or dark) is the whole app's.
        if not routes.names():
            routes.create("My route")
        self.editor = editor.run(host=self.pages["editor"])
        self.editor.on_title = self._editor_title
        real_apply = self.editor.apply_theme
        def apply_theme(rebuild=True):
            real_apply(rebuild)
            self._colours()
        self.editor.apply_theme = apply_theme

        ttk.Label(self.side, text="FFIX Coach", style="Brand.TLabel").pack(anchor="w", padx=(4, 14), pady=(0, 14))
        self.nav = {}
        for name, label in PAGES:
            b = ttk.Radiobutton(self.side, text=label, value=name, variable=self.page, style="Nav.TRadiobutton",
                                command=self.show)
            b.pack(fill="x", pady=1)
            self.nav[name] = b
        self.coach_state = ttk.Label(self.side, text="", style="SideMuted.TLabel", wraplength=150, justify="left")
        self.coach_state.pack(side="bottom", anchor="w", padx=4)

        self.session = CoachSession(root, on_stop=self._coach_stopped)
        self._build_coach_page()
        import setup_finder
        self.setup_runner = setup_finder.run(host=self.pages["setup"])
        self._build_help_page()
        self._colours()
        self.show()
        root.after(300, self._tick)

    # -- pages ---------------------------------------------------------------
    def show(self, name: str | None = None) -> None:
        if name:
            self.page.set(name)
        for n, f in self.pages.items():
            if n == self.page.get():
                f.pack(fill="both", expand=True)
            else:
                f.pack_forget()                        # (hidden pages aren't mapped: their keys sleep)

    def _editor_title(self, text: str) -> None:
        self.nav["editor"].configure(text="Route editor  ●" if text.startswith("●") else "Route editor")

    def _colours(self) -> None:
        """The editor's colours (light or dark) on the app's own parts too."""
        th = self.editor.th
        st = self.editor.style
        st.configure("Side.TFrame", background=th["button"])
        st.configure("Brand.TLabel", background=th["button"], foreground=th["fg"], font=("Segoe UI", 13, "bold"))
        st.configure("SideMuted.TLabel", background=th["button"], foreground=th["muted"], font=("Segoe UI", 8))
        st.configure("Nav.TRadiobutton", background=th["button"], foreground=th["fg"], font=("Segoe UI", 11),
                     padding=(10, 8), indicatoron=0, indicatorsize=0, indicatormargin=0, width=16)
        st.map("Nav.TRadiobutton", background=[("selected", th["bg"]), ("active", th["button_hi"])],
               foreground=[("selected", th["fg"])])
        st.layout("Nav.TRadiobutton", [("Radiobutton.padding", {"sticky": "nswe", "children": [
            ("Radiobutton.label", {"sticky": "w"})]})])
        st.configure("Page.TLabel", font=("Segoe UI", 15, "bold"))
        st.configure("Big.TButton", font=("Segoe UI", 11), padding=(14, 6))
        for t in (getattr(self, "log", None), getattr(self, "help", None)):
            if t is not None:
                t.configure(background=th["field"], foreground=th["fg"], insertbackground=th["cursor"])
        if getattr(self, "help", None) is not None:
            self.help.tag_configure("muted", foreground=th["muted"])
            self.help.tag_configure("code", background=th["bg"])

    # -- the Coach page -------------------------------------------------------
    def _build_coach_page(self) -> None:
        tk, ttk = self.tk, self.ttk
        p = self.pages["coach"]
        head = ttk.Frame(p, padding=(18, 14, 18, 6))
        head.pack(fill="x")
        ttk.Label(head, text="Coach", style="Page.TLabel").pack(side="left")
        row = ttk.Frame(p, padding=(18, 4))
        row.pack(fill="x")
        ttk.Label(row, text="Route").pack(side="left")
        self.route_pick = ttk.Combobox(row, values=routes.names(), state="readonly", width=34)
        self.route_pick.set(routes.active())
        self.route_pick.pack(side="left", padx=8)
        self.route_pick.bind("<<ComboboxSelected>>", self._pick_route)
        self.start_btn = ttk.Button(row, text="Start coach", style="Big.TButton", command=self.toggle_coach)
        self.start_btn.pack(side="left", padx=(12, 0))
        self.status = ttk.Label(row, text="Start the game in DuckStation, then Start coach.", style="Muted.TLabel")
        self.status.pack(side="left", padx=12)

        opts = ttk.Frame(p, padding=(18, 6))
        opts.pack(fill="x")
        cfg = _config()
        self.show_next = tk.BooleanVar(value=bool(cfg.get("overlay", {}).get("show_next", False)))
        self.speedrun = tk.BooleanVar(value=bool(cfg.get("speedrun", False)))
        ttk.Checkbutton(opts, text="Show the next step underneath", variable=self.show_next,
                        command=self._set_show_next).pack(side="left")
        ttk.Checkbutton(opts, text="Speedrun mode (just the notes)", variable=self.speedrun,
                        command=self._set_speedrun).pack(side="left", padx=16)

        keys = cfg.get("keys", {})
        k = {n: keys.get(n, d) for n, d in (("prev_step", "F7"), ("next_step", "F8"), ("learn", "F6"),
                                            ("learn_next", "Shift+F6"), ("next_main_step", "Shift+F8"),
                                            ("prev_main_step", "Shift+F7"), ("toggle_overlay", "F9"),
                                            ("quit", "F10"))}
        ttk.Label(p, padding=(18, 2), style="Muted.TLabel", wraplength=1000, justify="left",
                  text=f"It runs on its own; keys only to correct it, in the game:  {k['prev_step']} / {k['next_step']} "
                       f"previous / next step  ·  {k['prev_main_step']} / {k['next_main_step']} previous / next main "
                       f"step  ·  {k['learn']} learn this step / fight  ·  {k['learn_next']} learn where the next "
                       f"step starts  ·  {k['toggle_overlay']} hide / show the panel  ·  {k['quit']} stop").pack(fill="x")

        ttk.Label(p, text="What it's doing", padding=(18, 12, 18, 2), font=("Segoe UI", 10, "bold")).pack(anchor="w")
        box = ttk.Frame(p, padding=(18, 0, 18, 16))
        box.pack(fill="both", expand=True)
        self.log = tk.Text(box, wrap="word", font=("Consolas", 10), state="disabled", relief="flat",
                           padx=8, pady=6, height=12)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def toggle_coach(self) -> None:
        from main import CoachError
        if self.session.running:
            self.session.stop()
            return
        print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} started ---")
        try:
            self.session.start()
        except CoachError as e:
            print(e)
            self.status.configure(text=str(e), style="Warn.TLabel")
            return
        self.start_btn.configure(text="Stop coach")
        self.status.configure(text="Running: the panel is over the game.", style="Good.TLabel")

    def _coach_stopped(self) -> None:
        self.start_btn.configure(text="Start coach")
        self.status.configure(text="Stopped.", style="Muted.TLabel")

    def _pick_route(self, _e=None) -> None:
        name = self.route_pick.get()
        self.editor.open_route(name)                   # (asks to save unsaved changes first)
        if self.editor.name != name:
            self.route_pick.set(self.editor.name)       # cancelled
            return
        self.status.configure(text=f"Route: {name}." + (" The coach switches to it." if self.session.running else ""),
                              style="Muted.TLabel")

    def _set_show_next(self) -> None:
        _set_config(lambda c: c.setdefault("overlay", {}).__setitem__("show_next", bool(self.show_next.get())))

    def _set_speedrun(self) -> None:
        _set_config(lambda c: c.__setitem__("speedrun", bool(self.speedrun.get())))

    # -- the Help page --------------------------------------------------------
    def _build_help_page(self) -> None:
        tk, ttk = self.tk, self.ttk
        p = self.pages["help"]
        box = ttk.Frame(p, padding=(18, 14, 18, 16))
        box.pack(fill="both", expand=True)
        self.help = tk.Text(box, wrap="word", font=("Segoe UI", 10), relief="flat", padx=14, pady=10,
                            spacing1=2, spacing3=4)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.help.yview)
        self.help.configure(yscrollcommand=sb.set)
        self.help.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        h = self.help
        h.tag_configure("h1", font=("Segoe UI", 16, "bold"), spacing1=8, spacing3=8)
        h.tag_configure("h2", font=("Segoe UI", 13, "bold"), spacing1=12, spacing3=4)
        h.tag_configure("b", font=("Segoe UI", 10, "bold"))
        h.tag_configure("i", font=("Segoe UI", 10, "italic"))
        h.tag_configure("code", font=("Consolas", 10))
        try:
            text = (HERE.parent / "README.md").read_text(encoding="utf-8-sig")
        except OSError:
            text = "README.md not found."
        for line in text.splitlines():
            if line.startswith("# "):
                h.insert("end", line[2:] + "\n", "h1")
                continue
            if line.startswith("## "):
                h.insert("end", line[3:] + "\n", "h2")
                continue
            if re.fullmatch(r"\|?\s*-+\s*(\|\s*-+\s*)*\|?", line.strip()):
                continue                               # a table's ---|--- line
            if line.startswith("|"):
                line = "   ".join(c.strip() for c in line.strip().strip("|").split("|"))
            for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)", line):
                if part.startswith("**") and part.endswith("**") and len(part) > 4:
                    h.insert("end", part[2:-2], "b")
                elif part.startswith("`") and part.endswith("`") and len(part) > 2:
                    h.insert("end", part[1:-1], "code")
                elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                    h.insert("end", part[1:-1], "i")
                else:
                    h.insert("end", part)
            h.insert("end", "\n")
        h.configure(state="disabled")

    # -- every moment -----------------------------------------------------------
    def _tick(self) -> None:
        out = sys.stdout.take() if isinstance(sys.stdout, _Out) else ""
        if out:
            lg = self.log
            lg.configure(state="normal")
            lg.insert("end", out)
            extra = int(lg.index("end-1c").split(".")[0]) - LOG_LINES
            if extra > 0:
                lg.delete("1.0", f"{extra + 1}.0")
            lg.see("end")
            lg.configure(state="disabled")
        names, cur = routes.names(), routes.active()
        if list(self.route_pick.cget("values")) != names:
            self.route_pick.configure(values=names)
        if self.route_pick.get() != cur:
            self.route_pick.set(cur)
        c = self.session.coach
        if c is not None:
            from coach import step_numbers
            s = c.current()
            title = s.get("title") or c.bosses.get(s.get("boss"), {}).get("name", "")
            self.coach_state.configure(text=f"Coach running\n{step_numbers(c.steps)[c.step]} {title}")
        else:
            self.coach_state.configure(text="Coach stopped")
        self.root.after(500, self._tick)

    def close(self) -> None:
        if not self.editor.close():                    # unsaved route changes: save, or cancel
            return
        self.session.stop()
        try:
            self.setup_runner.stop()
        except Exception:
            pass
        try:
            zoomed = self.root.state() == "zoomed"
            def keep(c):
                w = c.setdefault("window", {})
                w["zoomed"] = zoomed
                if not zoomed:
                    w["geometry"] = self.root.geometry()
            _set_config(keep)
        except Exception:
            pass
        self.root.destroy()


def run() -> int:
    import tkinter as tk
    from overlay import make_dpi_aware
    if not isinstance(sys.stdout, _Out):
        sys.stdout = sys.stderr = _Out()               # (no command window: print() goes to the app)
    make_dpi_aware()
    root = tk.Tk()
    root.app = App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
