"""Always-on-top, click-through panel drawn with tkinter.

On Windows it sits inside DuckStation's black bar: the game picture is drawn
at its aspect ratio (4:3 by default) in the middle of the window, and the
panel takes the empty strip to the side of it (or below it, if the window is
taller than the picture). If there's no usable bar, it falls back to a small
panel in the window's top-right corner. It ignores the mouse, so clicks go
straight through to the game.
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont

from coach import View
from layout import MIN_BAR_HEIGHT, MIN_BAR_WIDTH, black_bar  # noqa: F401

COLORS = {
    "bg": "#000000",          # matches the black bar
    "text": "#e8edf2",
    "muted": "#8a97a5",
    "accent": "#f5c542",      # current action
    "good": "#4ade80",
    "warn": "#fb923c",
    "info": "#7dd3fc",
    "boss": "#f87171",
    "menu": "#c4b5fd",
    "remove": "#f87171",      # equipment / abilities to take off
    "remove_hl": "#b91c1c",   # the same on the yellow highlight
    "comment": "#b8c4d0",     # "# ..." lines: small notes under the line above (plain text)
    "story": "#f0a6ca",       # story lines: "Ant Eater > Green Shirt > Mary"
    "sub": "#ffa657",         # a sub-step called in its step: "[Baku]"
    "comment_bg": "#1f3a12",
}

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def make_dpi_aware() -> None:
    """So window positions from Windows and tkinter use the same pixels on scaled displays."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _client_rect_for_pid(pid: int) -> tuple[int, int, int, int] | None:
    """Screen rectangle of the inside (client area) of the process's biggest visible window."""
    if sys.platform != "win32" or not pid:
        return None
    user32 = ctypes.windll.user32
    best: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        hwnd = ctypes.c_void_p(hwnd)
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            r = (ctypes.c_long * 4)()
            user32.GetClientRect(hwnd, r)
            area = r[2] * r[3]
            if area > 0:
                best.append((area, hwnd.value))
        return True

    user32.EnumWindows(cb, 0)
    if not best:
        return None
    hwnd = ctypes.c_void_p(max(best)[1])
    r = (ctypes.c_long * 4)()
    user32.GetClientRect(hwnd, r)
    pt = (ctypes.c_long * 2)(0, 0)
    user32.ClientToScreen(hwnd, pt)
    return pt[0], pt[1], pt[0] + r[2], pt[1] + r[3]


class Overlay:
    def __init__(self, cfg: dict, pid: int = 0):
        self.cfg = cfg
        self.pid = pid
        self.fit = cfg.get("fit", "black_bar")
        self.aspect = float(cfg.get("aspect", 4 / 3))
        self.side = cfg.get("side", "right")
        self.top_ui = int(cfg.get("duckstation_top_bars", 60))
        self.bottom_ui = int(cfg.get("duckstation_bottom_bar", 25))
        self.fallback_width = int(cfg.get("width", 300))
        self.margin = int(cfg.get("margin", 10))
        self.base_size = int(cfg.get("font_size", 11))
        self.min_size = int(cfg.get("min_font_size", 7))
        self.family = cfg.get("font", "Segoe UI")
        self.visible = True
        self.width = self.fallback_width
        self.max_height = 10_000
        self.last_view: View | None = None
        self.with_next = True            # drawing the next step this time (dropped if it doesn't fit)

        self.root = tk.Tk()
        self.root.title("FFIX Coach")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(cfg.get("opacity", 0.92)))
        self.root.configure(bg=COLORS["bg"])
        self.fonts = {}
        self._set_font_size(self.base_size)

        self.frame = tk.Frame(self.root, bg=COLORS["bg"], padx=8, pady=6)
        self.frame.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self._make_click_through()

    def _set_font_size(self, size: int) -> None:
        self.size = size
        f = self.family
        self.fonts = {
            "small": tkfont.Font(family=f, size=max(6, size - 2)),
            "tiny": tkfont.Font(family=f, size=max(6, size - 3)),
            "body": tkfont.Font(family=f, size=size),
            "bold": tkfont.Font(family=f, size=size, weight="bold"),
            "title": tkfont.Font(family=f, size=size + 2, weight="bold"),
        }

    def _make_click_through(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:  # cosmetic; overlay still works without it
            pass

    # ------------------------------------------------------------------
    def toggle(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.root.deiconify()
        else:
            self.root.withdraw()

    def _target(self) -> tuple[int, int, int, int]:
        """(x, y, width, max_height) for the panel right now."""
        m = self.margin
        client = _client_rect_for_pid(self.pid)
        if client is None:
            sw = self.root.winfo_screenwidth()
            return sw - self.fallback_width - m, m, self.fallback_width, self.root.winfo_screenheight() - 2 * m
        if self.fit == "black_bar":
            bar = black_bar(client, self.aspect, self.top_ui, self.bottom_ui,
                            self.root.winfo_screenheight(), self.side)
            if bar:
                x, y, w, h, _kind = bar
                return x + m, y + m, w - 2 * m, h - 2 * m
        left, top, right, bottom = client
        return right - self.fallback_width - m, top + self.top_ui + m, self.fallback_width, bottom - top - 2 * m

    def _label(self, text, font, fg, bg=None, **kw):
        lbl = tk.Label(self.frame, text=text, font=self.fonts[font], fg=fg, bg=bg or COLORS["bg"],
                       justify="left", anchor="w", wraplength=max(60, self.width - 20), **kw)
        lbl.pack(fill="x", anchor="w")
        return lbl

    def _rich(self, prefix, segs, font, fg, bg, red, pady=0):
        """A line with some parts in red (things to take off)."""
        t = tk.Text(self.frame, font=self.fonts[font], fg=fg, bg=bg, wrap="word", height=1,
                    borderwidth=0, highlightthickness=0, padx=3 if bg != COLORS["bg"] else 0,
                    pady=0, cursor="arrow", width=1)
        t.tag_configure("red", foreground=red)
        t.insert("end", prefix)
        for text, off in segs:
            t.insert("end", text, ("red",) if off else ())
        t.pack(fill="x", anchor="w", pady=pady)
        # How many lines it wraps to, worked out from the panel width (the widget itself
        # isn't laid out yet): words placed one by one like the panel does.
        f = self.fonts[font]
        avail = max(60, self.width - 20) - 2 * int(t.cget("padx"))
        n, x = 1, 0
        space = f.measure(" ")
        for word in (prefix + "".join(text for text, _ in segs)).split(" "):
            w = f.measure(word)
            if x and x + space + w > avail:
                n, x = n + 1, w
            else:
                x = x + space + w if x else w
        t.configure(height=max(1, int(n or 1)), state="disabled")
        return t

    def _draw(self, view: View) -> None:
        for w in self.frame.winfo_children():
            w.destroy()
        self._label(view.header, "small", COLORS["muted"])
        color = {"boss": COLORS["boss"], "menu": COLORS["menu"]}.get(view.kind, COLORS["text"])
        self._label(view.title, "title", color, pady=2)

        for text, tone in view.badges:
            self._label(f" {text} ", "small", COLORS["bg"], bg=COLORS.get(tone, COLORS["info"])).pack_configure(pady=1)

        from coach import line_segments
        hl_end = view.highlight_end if view.highlight_end is not None else \
            (view.highlight + 1 if view.highlight is not None else None)
        for i, line in enumerate(view.lines):
            if not line.strip():                # an empty line in the editor: a gap
                tk.Frame(self.frame, height=max(4, self.fonts["body"].metrics("linespace") // 2),
                         bg=COLORS["bg"]).pack(fill="x")
                continue
            if line.strip().startswith("@"):    # an AP readout the step asked for
                sub = line[:1] in (" ", "\t")
                text = line.strip()[1:].strip()
                if i in view.done:
                    self._label(("      \u2713 " if sub else "\u2713 ") + text, "body", COLORS["good"])
                else:
                    self._label(("      " if sub else "\u25c6 ") + text, "body", COLORS["info"])
                continue
            if line.strip().startswith("#"):    # a comment: small plain text under the line above, like the notes
                lbl = tk.Label(self.frame, text=line.strip().lstrip("#").strip(), font=self.fonts["tiny"],
                               fg=COLORS["comment"], bg=COLORS["bg"], justify="left", anchor="w",
                               wraplength=max(60, self.width - 40), padx=0)
                lbl.pack(anchor="w", padx=(18, 0), pady=(0, 1))
                continue
            sub = line[:1] in (" ", "\t")      # indented: part of the line above, no bullet
            line = line.strip()
            from syntax import story_parts
            story = story_parts(line)
            if story and i not in view.done and not (view.highlight is not None and view.highlight <= i < hl_end):
                # Who to talk to / where to go, in order: the names in colour, the arrows dim.
                past = view.highlight is not None and i < view.highlight
                segs = []
                for k, part in enumerate(story):
                    if k:
                        segs.append((" \u203a ", True))
                    segs.append((part, False))
                self._rich("      " if sub else "• ", segs, "body",
                           COLORS["muted"] if past else COLORS["story"], COLORS["bg"], COLORS["muted"])
                continue
            segs = line_segments(line)
            red = any(off for _, off in segs)
            lit = view.highlight is not None and view.highlight <= i < hl_end
            if i in view.done:
                self._label(("      \u2713 " if sub else "\u2713 ") + "".join(t for t, _ in segs), "body",
                            COLORS["good"])
            elif lit:
                mark = "      " if sub else "▶ "
                if red:
                    self._rich(mark, segs, "bold", COLORS["bg"], COLORS["accent"], COLORS["remove_hl"])
                else:
                    self._label(f"{mark}{line}", "bold", COLORS["bg"], bg=COLORS["accent"], padx=3)
            else:
                # Past actions grey out, except a steal not yet seen on screen: that stays
                # white until it gets its check mark.
                done = view.highlight is not None and i < view.highlight and i not in view.steals
                fg = COLORS["muted"] if done else COLORS["sub"] if line.startswith("\u21b3") else COLORS["text"]
                mark = "      " if sub else "• "
                if red:
                    self._rich(mark, segs, "body", fg, COLORS["bg"], COLORS["muted"] if done else COLORS["remove"])
                else:
                    self._label(f"{mark}{line}", "body", fg)

        for line in view.stats:
            self._label(line, "small", COLORS["info"])
        if view.footer:
            # Backup note: one label per line, so gaps and indents show like the steps.
            first = True
            for fl in view.footer.split("\n"):
                if not fl.strip():
                    tk.Frame(self.frame, height=max(3, self.fonts["small"].metrics("linespace") // 2),
                             bg=COLORS["bg"]).pack(fill="x")
                    continue
                text = ("    " + fl.strip()) if fl[:1] in (" ", "\t") else fl.strip()
                lbl = self._label(text, "small", COLORS["warn"])
                if first:
                    lbl.pack_configure(pady=(4, 0))
                first = False
        if view.upcoming:
            self._label("Next: " + view.upcoming[0], "small", COLORS["muted"], pady=2)
        if view.next_title and self.with_next:
            self._draw_next(view)

    def _draw_next(self, view: View) -> None:
        """The next step, small and grey, under a thin line (overlay.show_next)."""
        tk.Frame(self.frame, height=1, bg=COLORS["muted"]).pack(fill="x", pady=(8, 3))
        self._label("Next: " + view.next_title, "small", COLORS["muted"])
        from coach import line_segments
        for line in view.next_lines:
            if not line.strip():
                tk.Frame(self.frame, height=3, bg=COLORS["bg"]).pack(fill="x")
                continue
            if line.strip().startswith("#"):
                self._label("      " + line.strip().lstrip("#").strip(), "small", COLORS["muted"])
                continue
            sub = line[:1] in (" ", "\t")
            text = "".join(t for t, _ in line_segments(line.strip()))
            self._label(("      " if sub else "• ") + text, "small", COLORS["muted"])

    def render(self, view: View) -> None:
        self.last_view = view
        x, y, self.width, self.max_height = self._target()
        # The next step (if shown) only goes in when it fits at the normal text size:
        # it never makes the step you're on smaller.
        self.with_next = bool(view.next_title)
        if self.with_next:
            if self.base_size != self.size:
                self._set_font_size(self.base_size)
            self._draw(view)
            self.root.update_idletasks()
            if self.frame.winfo_reqheight() > self.max_height:
                self.with_next = False
        # Start at the configured size and shrink until it fits the bar's height.
        size = self.base_size
        while True:
            if size != self.size:
                self._set_font_size(size)
            self._draw(view)
            self.root.update_idletasks()
            h = self.frame.winfo_reqheight()
            if h <= self.max_height or size <= self.min_size:
                break
            size -= 1
        self.root.geometry(f"{self.width}x{min(h, self.max_height)}+{x}+{y}")

    def follow(self) -> None:
        """Re-place the panel if DuckStation's window moved or resized."""
        if self.last_view is not None:
            self.render(self.last_view)

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def run(self):
        self.root.mainloop()
