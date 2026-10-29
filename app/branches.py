"""Branches: alternative steps (14 / 14a), steps shown only when something holds, and
the conditions they're decided by ("Broadsword < 2", "Zidane: Flee learned")."""

from __future__ import annotations

import re

from memory import GameState
from syntax import find_item
from tuning import IGT_MAX_STEP


_OLD_WHEN = re.compile(r"(?i)^\s*(at least|fewer than)\s+(\d+)\s*x?\s+(.+?)\s*$")
_WHEN = re.compile(r"^\s*(.+?)\s*(<=|>=|=>|=<|==|!=|<|>|=)\s*(\d+)\s*$")
_LEARNED = re.compile(r"^\s*(.+?:.+?)\s+(learned|not learned)\s*$", re.I)
_AP_SUBJECT = re.compile(r"^(?:AP\s+)?([a-z]+)\s*:\s*(.+)$", re.I)
OPS = ["<", "<=", "=", ">=", ">"]
AP_OPS = ["learned", "not learned"]
_NOT = {"<": ">=", "<=": ">", ">=": "<", ">": "<=", "=": "!=", "!=": "=",
        "learned": "not learned", "not learned": "learned"}


def ap_subject(text: str) -> tuple[str, str] | None:
    """"Zidane: Flee" -> ("zidane", "Flee") (the ability as the game names it), or None.
    (Older routes wrote "AP Zidane: Flee".)"""
    m = _AP_SUBJECT.match(text.strip())
    if not m:
        return None
    from ability_data import ABILITY_LISTS
    who = "garnet" if m.group(1).lower() == "dagger" else m.group(1).lower()
    want = re.sub(r"[^a-z0-9+]", "", m.group(2).lower())
    for entry in ABILITY_LISTS.get(who, []):
        if entry and re.sub(r"[^a-z0-9+]", "", entry[0].lower()) == want:
            return who, entry[0]
    return None


def ap_subjects() -> list[str]:
    """Every "Zidane: Flee" a condition can ask about."""
    from ability_data import ABILITY_LISTS
    return [f"{who.capitalize()}: {e[0]}" for who, lst in ABILITY_LISTS.items()
            if who != "guest" for e in lst if e]


def parse_when(v: str) -> tuple[str, str, str, int] | None:
    """A step's "when": ("cmp", item, op, n) for "Broadsword < 2" (op one of OPS), or
    ("pick", "", "", 0) for one only Shift+F5 chooses. Older "fewer than 2 Broadsword"
    and "at least 2 Broadsword" are read too."""
    v = (v or "").strip()
    if v.lower() == "pick":
        return "pick", "", "", 0
    m = _OLD_WHEN.match(v)
    if m:
        return "cmp", find_item(m.group(3)) or m.group(3), (">=" if m.group(1).lower() == "at least" else "<"), \
            int(m.group(2))
    m = _LEARNED.match(v)
    if m:
        subj = ap_subject(m.group(1))
        name = f"{subj[0].capitalize()}: {subj[1]}" if subj else m.group(1)
        return "cmp", name, m.group(2).lower(), 0
    m = _WHEN.match(v)
    if not m:
        return None
    op = {"=>": ">=", "=<": "<=", "==": "="}.get(m.group(2), m.group(2))
    subj = ap_subject(m.group(1))
    if subj:
        return "cmp", f"{subj[0].capitalize()}: {subj[1]}", op, int(m.group(3))
    return "cmp", find_item(m.group(1)) or m.group(1), op, int(m.group(3))


def parse_conds(v: str) -> list[tuple[str, int, str]] | None:
    """All the item checks of a "when" ("Broadsword < 1 and Leather Wrist >= 1"), as
    [(item, op, n)]; None if it isn't item checks (empty, or "pick")."""
    parts = [parse_when(p) for p in re.split(r"\s+and\s+", (v or "").strip()) if p.strip()]
    if not parts or any(p is None or p[0] != "cmp" for p in parts):
        return None
    return [p[1:] for p in parts]


def when_text(item: str, op: str, n: int) -> str:
    return f"{item} {op}" if op in AP_OPS else f"{item} {op} {n}"


def when_label(v: str) -> str:
    w = parse_when(v)
    if not w:
        return v
    if w[0] == "pick":
        return "picked with Shift+F5"
    return " and ".join(when_text(*c) for c in (parse_conds(v) or [w[1:]]))


def compare(have: int, op: str, n: int) -> bool:
    return {"<": have < n, "<=": have <= n, "=": have == n, ">=": have >= n, ">": have > n,
            "!=": have != n}[op]


def step_numbers(steps: list[dict]) -> list[str]:
    """What each step is called: 14; its alternatives 14a, 14b (another way); its
    sub-steps 14.1, 14.2 (done in the middle of it, then back to it)."""
    out, n, k, base, sub = [], 0, 0, "", 0
    for i, s in enumerate(steps):
        if s.get("inside") and i > 0 and base:
            sub += 1
            out.append(f"{base}.{sub}")
            continue
        if s.get("alt") and n:
            k += 1
            base = f"{n}{chr(96 + min(k, 26))}"
        else:
            n, k = n + 1, 0
            base = str(n)
        sub = 0
        out.append(base)
    return out


def route_branches(steps: list[dict]):
    """The ways a route can go.

    A step can have alternatives: the steps right after it marked "alt" (14a, 14b), taken
    instead of it. Which is decided by the first alternative's "when" (or, in older
    routes, the main step's):
    - "Broadsword < 2" (<, <=, =, >=, >): by how many you have, the bag and what your
      characters wear together. Steps anywhere asking the same (or its opposite: < 2 and
      >= 2) share the answer, decided once on reaching the first of them, so a later
      step can say "Broadsword < 2" on its own and is skipped when you have 2.
    - "pick", or no "when": the step itself, unless you switch with Shift+F5.
    Returns (per step (group, option) or None, group -> options, group -> (item, n))."""
    per: list = [None] * len(steps)
    opts: dict[str, list[str]] = {}
    checks: dict[str, tuple[str, int]] = {}
    def put(i, g, o):
        per[i] = (g, o)
        if o not in opts.setdefault(g, []):
            opts[g].append(o)
    def count_group(w):
        """The same question asked either way round shares its answer: Broadsword < 2
        is the "no" of Broadsword >= 2."""
        _, item, op, n = w
        if op in ("<", "<=", "!=", "not learned"):
            op, o = _NOT[op], "no"
        else:
            o = "yes"
        g = f"count {item} {op} {n}"
        checks[g] = (item, op, n)
        return g, o
    flip = {"yes": "no", "no": "yes"}
    def block(k):
        """Step k and its sub-steps (they go the way it goes)."""
        out = [k]
        while k + 1 < len(steps) and steps[k + 1].get("inside"):
            k += 1
            out.append(k)
        return out
    i = 0
    while i < len(steps):
        s = steps[i]
        mine = block(i)
        alts = []
        j = mine[-1] + 1
        while j < len(steps) and steps[j].get("alt") and not steps[j].get("inside"):
            alts.append(block(j))
            j = alts[-1][-1] + 1
        w_main = parse_when(s.get("when", ""))
        first_conds = parse_conds(steps[alts[0][0]].get("when", "")) if alts else None
        if len(alts) > 1 or (first_conds and len(first_conds) > 1):
            # Several other ways (10a, 10b): the first whose condition holds, else the step.
            g = f"ways {i}"
            for k in mine:
                put(k, g, "main")
            ways = []
            for n, b in enumerate(alts):
                o = f"way {n + 1}"
                for k in b:
                    put(k, g, o)
                ways.append((o, parse_conds(steps[b[0]].get("when", ""))))
            checks[g] = ("ways", ways)
        elif alts:
            # The way's condition is on its first alternative ("take this instead when");
            # older routes had it on the main step.
            w_alt = parse_when(steps[alts[0][0]].get("when", ""))
            if w_alt and w_alt[0] == "cmp":
                g, o = count_group(w_alt)
                main, alt = flip[o], o
            elif w_main and w_main[0] == "cmp":
                g, o = count_group(w_main)
                main, alt = o, flip[o]
            else:
                g, main, alt = f"pick {i}", "main", "alt"
            for k in mine:
                put(k, g, main)
            for b in alts:
                for k in b:
                    put(k, g, alt)
        elif w_main and w_main[0] == "cmp":
            conds = parse_conds(s.get("when", ""))
            if conds and len(conds) > 1:               # "only if" with several checks: all must hold
                g = f"only {i}"
                opts[g] = ["hide"]
                for k in mine:
                    put(k, g, "show")
                checks[g] = ("ways", [("show", conds)])
            else:
                g, o = count_group(w_main)
                for k in mine:
                    put(k, g, o)
        i = j
    # A sub-step with its own condition ("Before Baku" only with the Leather Wrist): that
    # decides it (it's only ever reached through its step anyway).
    for k, s in enumerate(steps):
        if not (k and s.get("inside") and s.get("when")):
            continue
        conds = parse_conds(s["when"])
        if not conds:
            continue
        if len(conds) > 1:
            g = f"only {k}"
            opts[g] = ["hide"]
            per[k] = None
            put(k, g, "show")
            checks[g] = ("ways", [("show", conds)])
        else:
            per[k] = None
            put(k, *count_group(("cmp",) + tuple(conds[0])))
    for g in [g for g, c in checks.items() if c[0] != "ways"]:
        for o in ("yes", "no"):
            if o not in opts[g]:
                opts[g].append(o)
    return per, opts, checks


class BranchesMixin:
    """Which way the run goes: alternative steps (14a), conditions and Shift+F5.
    Mixed into Coach: works on its state."""

    # -- alternative steps (14 / 14a) ------------------------------------
    # A step with alternatives, or a "when", counts only when that's how the run went;
    # other steps always do, so the route carries on the same afterwards. See route_branches.
    def branch_of(self, s: dict) -> tuple[str, str] | None:
        self._branches()
        return self._b_of.get(id(s))

    def branch_options(self, group: str) -> list[str]:
        return self._branches()[0].get(group, [])

    def _branches(self):
        if self._b_for is not self.steps:
            per, self._b_opts, self._b_checks = route_branches(self.steps)
            self._b_of = {id(st): b for st, b in zip(self.steps, per) if b}
            self._b_for = self.steps
        return self._b_opts, self._b_checks

    def _track_obtained(self, prev: GameState | None, st: GameState) -> None:
        """Items you get this run (stolen, picked up, bought), for branches: what the bag
        gains that didn't come off someone (taking equipment off isn't getting it).
        Characters joining or leaving change no bag, so they don't count either."""
        if (prev is None or prev.items is None or st.items is None or self._reset_pending is not None
                or self._now - self.loaded_at < 1.0 or not 0 <= st.igt - prev.igt <= IGT_MAX_STEP):
            return                                     # a save loaded (or the game starting): not got
        from screen import ITEMS
        def worn(g: GameState) -> dict[int, int]:
            out: dict[int, int] = {}
            for e in (g.equips or {}).values():
                for i in e:
                    out[i] = out.get(i, 0) + 1
            return out
        w0, w1 = worn(prev), worn(st)
        for i, n in st.items.items():
            gain = n - prev.items.get(i, 0) - max(0, w0.get(i, 0) - w1.get(i, 0))
            if gain > 0 and 0 <= i < len(ITEMS) and gain < 100:
                self.obtained.append([st.igt, ITEMS[i], gain, self._label(self.step)])
                if ITEMS[i] in self._watched():
                    self._event(f"got {gain}x {ITEMS[i]} (step {self._label(self.step)})")
                self._save_progress()

    def _watched(self) -> set[str]:
        """Items the route's branches ask about."""
        out = set()
        for chk in self._branches()[1].values():
            if chk[0] == "ways":
                out |= {c[0] for _, conds in chk[1] if conds for c in conds}
            else:
                out.add(chk[0])
        return out

    def _count(self, item: str) -> int | None:
        """How many of this item you've got this run: stolen, picked up or bought."""
        return sum(n for _, it, n, _ in self.obtained if it == item)

    def _holds(self, subject: str, op: str, n: int) -> bool | None:
        """Whether a check holds: an item you've got this run ("Broadsword < 1"), or a
        character's AP ("Zidane: Flee >= 20", "Zidane: Flee learned"). None: can't tell."""
        subj = ap_subject(subject) if ":" in subject else None
        if subj:
            st = self.prev
            have, need = (st.ap.get(f"{subj[0]}:{subj[1]}", (None, None)) if st else (None, None))
            if have is None:
                return None
            if op in AP_OPS:
                return (have >= need) == (op == "learned")
            return compare(have, op, n)
        return compare(self._count(subject), op, n)

    def _count_text(self, item: str) -> str:
        subj = ap_subject(item) if ":" in item else None
        if subj:
            st = self.prev
            have, need = (st.ap.get(f"{subj[0]}:{subj[1]}", (None, None)) if st else (None, None))
            return "AP unknown" if have is None else f"{have}/{need} AP"
        where = [w for _, it, _, w in self.obtained if it == item]
        return f"got {self._count(item)} this run" + (f" (at {', '.join(where)})" if where else "")

    def branch_now(self, group: str) -> str | None:
        """The way you're going: the one fixed on reaching it (or picked with Shift+F5),
        else what the item count says, else the first."""
        if group in self.branch_choice:
            return self.branch_choice[group]
        opts = self.branch_options(group)
        if not opts:
            return None
        chk = self._branches()[1].get(group)
        ans = opts[0]
        items = []
        if chk and chk[0] == "ways":
            for o, conds in chk[1]:
                if conds:
                    items += [c[0] for c in conds]
                    if all(self._holds(it, op, n) for it, op, n in conds):
                        ans = o                        # every check of this way holds
                        break
        elif chk:
            items = [chk[0]]
            h = self._holds(*chk)
            if h is not None:
                ans = "yes" if h else "no"
        seen = self._answers
        if items and seen.get(group) != ans:
            seen[group] = ans                          # for the run log: what it counted
            self._event(", ".join(f"{it}: {self._count_text(it)}" for it in dict.fromkeys(items))
                        + f", so {self._way_name(group, ans)}")
        return ans

    def active(self, i: int) -> bool:
        """Whether step i is on the branch you're on (steps without a branch always are)."""
        if not 0 <= i < len(self.steps):
            return False
        b = self.branch_of(self.steps[i])
        return b is None or self.branch_now(b[0]) == b[1]

    def _settle_branch(self) -> None:
        """On a step of a branch: that's the branch you're on now."""
        b = self.branch_of(self.current())
        if b and self.branch_choice.get(b[0]) != b[1]:
            self.branch_choice[b[0]] = b[1]
            self.branch_at[b[0]] = self._igt_last or 0
            chk = self._branches()[1].get(b[0])
            self._event(f"way: {self._way_name(*b)}" + (f", {chk[0]}: {self._count_text(chk[0])}"
                                                         if chk and chk[0] != "ways" else ""))
            self._save_progress()

    def switch_branch(self) -> None:
        """Shift+F5: the other branch, for the branch you're on or the next one coming up."""
        group, start = None, None
        b = self.branch_of(self.current())
        if b:
            group = b[0]
        else:
            for j in range(self.step + 1, min(len(self.steps), self.step + 40)):
                bj = self.branch_of(self.steps[j])
                if bj:
                    group = bj[0]
                    break
        if group is None:
            self.message = "No alternative steps coming up"
            return
        opts = self.branch_options(group)
        cur = b[1] if b else self.branch_now(group)        # on a step of a branch: that one
        new = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0]
        self.branch_choice[group] = new
        self.branch_at[group] = self._igt_last or 0
        self._event(f"switched by hand to: {self._way_name(group, new)}")
        self.message = f"Now: {self._way_name(group, new)}"
        if b:                                          # on a step of the old branch: to the new one's
            k = self.step                              # this step and its alternatives
            while k > 0 and (self.branch_of(self.steps[k - 1]) or ("",))[0] == group:
                k -= 1
            start = None
            for j in range(k, len(self.steps)):
                bj = self.branch_of(self.steps[j])
                if not bj or bj[0] != group:
                    break
                if bj[1] == new:
                    start = j
                    break
            if start is None:                          # the new way has no steps here: on past them
                start = self._step_by(self.step, 1)
            if start is not None:
                self._goto(start, manual=False, why=f"branch switched to {new}")
        self._save_progress()

    def _way_name(self, group: str, option: str) -> str:
        chk = self._branches()[1].get(group)
        if chk and chk[0] == "ways":
            conds = dict(chk[1]).get(option)
            return " and ".join(when_text(*c) for c in conds) if conds else ("main step" if option == "main" else option)
        if chk:
            item, op, n = chk
            return when_text(item, op if option == "yes" else _NOT[op], n)
        return "main step" if option == "main" else "other way"
