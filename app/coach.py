"""Decides what to show next, from live game state plus the route.

Pure Python with no Windows calls, so it can be tested anywhere.

How it moves through the route by itself:

1. In battle or not: from bytes the game changes when a battle starts (learned the
   first time ATB bars move), falling back to watching the ATB bars.
2. Which fight: the enemies' records (learned the first time a fight step is won),
   then their starting HP against the HP on the route's fight steps. A fight that
   matches a step ahead jumps there; anything else is a random encounter.
3. Fight won: on to the next step (or back to the step it's in the middle of, or on
   to the step's next fight when it has several).
4. Walk and menu steps: each line that can be checked (pickups, shopping, equipment,
   abilities, rows, settings) ticks from memory; the step moves on once all are done,
   or when you walk onto a screen a later step is known to start on.
5. Learning: screens and fights are remembered in learned.json, so later runs need
   no key presses.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from memory import GameState
from field_names import field_name
from syntax import (instructions_after_checks, is_optional, config_check, expand_headers, sell_except, turn_groups, _OPTIMIZE_LINE, _MENU_LINE, _SHOP_LINE, line_segments, menu_checks,  # noqa: F401
                    old_off_line, pickup_targets, row_check, shop_check, steal_targets, find_item)

QUIET_SECONDS_TO_END = 6.0     # fallback end-of-battle when the code check isn't available
LOOKAHEAD_STEPS = 8            # how far ahead a recognised boss may jump the route
SCRIPTED_LOOKAHEAD = 3         # how far ahead a fight on a no-encounter screen may jump
FIELD_LOOKAHEAD = 3            # how far ahead a learned screen may jump
PICKUP_LOOKAHEAD = 2           # how many walk steps ahead a named pickup may jump
FIELD_SETTLE = 0.5             # seconds a screen must hold before it counts
FLAG = "\u2691 "                # on the panel title: the coach still has to learn this step
NO_SCREEN = (0, 0xFFFF)                # screen numbers read during FMVs and loading: not places
MAX_SCREEN = 9000               # from here up: the world map (many steps cross it: not a step's
                               # place) and numbers read before the game is loaded
STEP_SETTLE = 3.0              # seconds a step must be up before its screen is learned
LOAD_SETTLE = 5.0              # no screens learned this soon after a save was loaded
FP_CONFIRM = 1.0               # seconds of steady battle bytes before trusting them
FP_MISMATCH_LIMIT = 4.0        # obvious battle but bytes don't match for this long = unreliable
FP_STUCK_LIMIT = 60.0          # "in battle" with frozen bars this long = bytes match outside battle
RESUME_WINDOW = 30.0
MIN_BOSS_SECONDS = 12.0        # an unrecognised fight shorter than this was a random one (fled)
UNDO_WINDOW = 30.0
IGT_TRUST = 180                # in-game timer frames seen counting up before resets are trusted
IGT_NEW_GAME = 300             # timer back under 5 s (from well above) = reset / new game
IGT_RESET_CONFIRM = 60         # after a reset, the timer counting to 1 s confirms a new game
IGT_BACK = 120                 # timer back by more than 2 s = a save or savestate was loaded
IGT_MAX_STEP = 1200            # bigger single jumps forward are loads, not time passing
STALE_SECONDS = 0.8            # an unchanged leftover HP is believed after this long
IDENTIFY_WITHIN = 1.5          # seconds to see which enemy slots are really in the fight
FORMATION_VERSION = 5          # learned data from older, less careful versions is dropped
ATB_DROP = 0.5                 # a bar falling below half its last value = that member acted
MAX_ATB_STEP = 1500            # biggest plausible ATB rise between two reads
CLEARED_SECONDS = 1.0          # every enemy down this long = boss beaten
GIL_PICKUP_MAX = 100000        # a bigger jump than this is a load, not a pickup
BAG_AFTER_FIGHT = 8.0          # seconds after a fight when new items are spoils, not pickups
FP_MATCH = 0.9                 # how alike two fights' enemy records must be to be the same fight
CAPPED_HP = 65535              # a boss whose HP doesn't fit the counter starts on this (Gizamaluke)
PHASE_GAP = 1000               # early on, nothing hits for this much: a bigger drop is a new phase
SCRIPT_HP = 10000              # FF9 adds this to bosses whose fight ends on a script


@dataclass
class View:
    """Everything the overlay needs to draw."""

    header: str
    title: str
    lines: list[str]
    highlight: int | None = None
    highlight_end: int | None = None   # the highlight covers lines highlight..end-1 (indented lines)
    badges: list[tuple[str, str]] = field(default_factory=list)  # (text, "good"|"warn"|"info")
    footer: str = ""
    upcoming: list[str] = field(default_factory=list)
    next_title: str = ""                 # the next step, shown underneath (if show_next is on)
    next_kind: str = ""
    next_lines: list[str] = field(default_factory=list)
    kind: str = "move"
    stats: list[str] = field(default_factory=list)   # levels and AP progress
    done: list[int] = field(default_factory=list)    # lines ticked off (steals confirmed on screen)
    steals: list[int] = field(default_factory=list)  # lines that are steals


class Learned:
    """Things the coach picks up from your runs, saved between sessions."""

    SECTIONS = ("fields", "formations", "fingerprints", "battle_ids", "random_here")

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime = None
        # What this copy changed since it last wrote the file: when the editor wrote it in
        # between (Forget), these go on top of the editor's version instead of replacing it.
        self._changed: dict[tuple[str, str], object] = {}     # (section, key) -> value (None: removed)
        self._changed_top: set[str] = set()
        self.data = {"battle_fp": None, "fp_unusable": False, "formations": {}, "fields": {}}
        if path and path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
                self._mtime = path.stat().st_mtime
            except (ValueError, OSError):
                pass
        if self.data.get("formation_version") != FORMATION_VERSION:
            # Older versions could learn from fights that weren't real (or from leftover
            # HP in empty slots), and the screens saved alongside them. Start clean.
            self.data.update({"formations": {}, "fields": {}, "battle_fp": None,
                              "fp_unusable": False, "formation_version": FORMATION_VERSION})

    def _put(self, section: str, key: str, value) -> None:
        """Set (or, with None, remove) one learned thing, remembering it was this copy's change."""
        d = self.data.setdefault(section, {})
        if value is None:
            d.pop(key, None)
        else:
            d[key] = value
        self._changed[(section, key)] = value

    def set_flag(self, key: str, value) -> None:
        self.data[key] = value
        self._changed_top.add(key)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        from paths import write_atomic
        try:
            m = self.path.stat().st_mtime if self.path.exists() else None
            if m is not None and m != self._mtime:
                # Someone else (the editor) wrote it since: start from theirs, add ours.
                try:
                    disk = json.loads(self.path.read_text())
                except ValueError:
                    disk = None
                if isinstance(disk, dict) and disk.get("formation_version") == FORMATION_VERSION:
                    merged = dict(disk)
                    for k in self._changed_top:
                        merged[k] = self.data.get(k)
                    for s in self.SECTIONS:
                        merged[s] = dict(disk.get(s) or {})
                    for (s, k), v in self._changed.items():
                        if v is None:
                            merged[s].pop(k, None)
                        else:
                            merged[s][k] = v
                    self.data = merged
            write_atomic(self.path, json.dumps(self.data, indent=1))
            self._mtime = self.path.stat().st_mtime
            self._changed.clear()
            self._changed_top.clear()
        except OSError:
            pass

    def refresh(self) -> bool:
        """Load learned.json again if something else changed it (the editor forgetting
        what was learned for a step). True if it was reloaded."""
        if not self.path:
            return False
        try:
            m = self.path.stat().st_mtime
            if m == self._mtime:
                return False
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return False
        self._mtime = m
        self.data.update(data)
        for k in self.SECTIONS:
            self.data[k] = data.get(k, {})
        return True

    def forget_step(self, key: str, route: str = "", fight_keys: list[str] | None = None) -> None:
        """Forget everything learned for one step (its screen; a fight's HP and enemies)."""
        for k in ([f"{route}|{key}"] if route else []) + [key]:
            self._put("fields", k, None)
            if k.startswith("move:"):
                self._put("fields", k.replace("move:", "menu:", 1), None)
        for k in fight_keys or []:
            for part in ("formations", "fingerprints", "battle_ids"):
                self._put(part, k, None)
        self.save()

    # battle code fingerprint
    @property
    def battle_fp(self) -> bytes | None:
        fp = self.data.get("battle_fp")
        return bytes.fromhex(fp) if fp and not self.data.get("fp_unusable") else None

    def set_battle_fp(self, sig: bytes) -> None:
        self.set_flag("battle_fp", sig.hex())

    def mark_fp_unusable(self) -> None:
        self.set_flag("fp_unusable", True)

    # formations and fields
    def formation(self, boss_id: str) -> list[int] | None:
        return self.data["formations"].get(boss_id)

    def set_formation(self, boss_id: str, formation: list[int], random_here: bool | None = None) -> None:
        self._put("formations", boss_id, list(formation))
        if random_here is not None:
            self._put("random_here", boss_id, bool(random_here))
        self.save()

    def random_here(self, boss_id: str) -> bool | None:
        """Whether random battles could happen where this fight was learned (None: not known)."""
        return self.data.get("random_here", {}).get(boss_id)

    def battle_id(self, boss_id: str) -> int | None:
        return self.data.setdefault("battle_ids", {}).get(boss_id)

    def set_battle_id(self, boss_id: str, value: int | None) -> None:
        self._put("battle_ids", boss_id, None if value is None else int(value))
        self.save()

    def fingerprint(self, boss_id: str) -> dict | None:
        return self.data.setdefault("fingerprints", {}).get(boss_id)

    def set_fingerprint(self, boss_id: str, fp: dict | None) -> None:
        self._put("fingerprints", boss_id, fp)
        self.save()

    def field(self, key: str, route: str = "") -> int | None:
        """The screen a step starts on. Learned per route; older learning (shared by all
        routes) is used until this route has its own."""
        fields = self.data["fields"]
        for k in ([f"{route}|{key}"] if route else []) + [key]:
            got = fields.get(k)
            if got is None and "move:" in k:
                got = fields.get(k.replace("move:", "menu:", 1))   # learned when it was a menu step
            if got is not None:
                return got
        return None

    def set_field(self, key: str, field_id: int, route: str = "") -> None:
        self._put("fields", f"{route}|{key}" if route else key, field_id)
        self.save()

    def forget_field(self, key: str, route: str = "") -> None:
        for k in ([f"{route}|{key}"] if route else []) + [key]:
            self._put("fields", k, None)
        self.save()


def formation_of(hps: list[int]) -> list[int]:
    return sorted(h for h in hps if h > 0)


def hp_candidates(h: int) -> set[int]:
    """What a raw enemy HP value could mean: itself, or a scripted boss's HP plus 10,000."""
    return {h, h - SCRIPT_HP} if h >= SCRIPT_HP else {h}


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


class Coach:
    def __init__(self, route: dict, progress_path: Path | None = None,
                 learned_path: Path | None = None, party_slots: dict[str, int] | None = None,
                 resume: bool = True):
        self._init_args = (learned_path, party_slots, resume)
        self.route = route
        self.rname = route.get("name", "")
        # Party position of named characters (0 = top); Zidane leads by default.
        self.party_slots = dict(party_slots) if party_slots is not None else {"zidane": 0}
        self.steps = route["steps"]
        self.bosses = route["bosses"]
        self.progress_path = progress_path
        self.learned = Learned(learned_path)
        self.step = 0
        self.turn = 0
        self.encounters: list[int] = []        # in-game time of each random battle this run
        self.resets = 0                        # how many resets / new games were seen
        self._igt_last: int | None = None
        self._igt_counted = 0                  # frames seen counting up normally
        if resume:
            self._load_progress()

        self.prev: GameState | None = None
        self.last_activity = 0.0
        self.message = ""
        self.stolen: dict[int, list[str]] = {}       # step -> items seen stolen on screen
        self.picked: dict[int, list[str]] = {}       # step -> items seen picked up on screen
        self.got_log: list[tuple[int, str, int, str]] = []   # (game time, picked/stolen, step, item)
        self._entry_step: int | None = None          # step whose starting state is below
        self._entry_done = 0                         # lines already done when it came up
        self.menu_opened_on: int | None = None        # menu step the menu was opened on
        self.shop_step: int | None = None             # step the bag counts below were taken on
        self.shop_base: dict[int, int] = {}           # bag when that step came up
        self.shop_peak: dict[int, int] = {}           # most of each item seen since
        self.shop_ticked: set[int] = set()            # shop lines done (stay done)
        self.tick_step: int | None = None
        self.equip_step: int | None = None            # step the equipment below was taken on
        self.equip_base: dict[str, list[int]] = {}
        self.steal_note: tuple[str, str] | None = None
        self.steal_off = ""                          # why the screen can't be read, if it can't

        # battle
        self.in_battle = False
        self.battle_is_boss = False
        self.battle_start_hp: list[int] = []
        self.field_enemy_hp: list[int] = []   # what enemy memory held before the fight (leftovers)
        self.battle_last_hp: list[int] = []
        self.active_enemy: set[int] = set()   # enemy slots really in this fight
        self.cleared_since: float | None = None
        self.won_early = False                # boss beaten and route moved on mid-fight
        self.hold_route = False               # you went back mid-fight: don't move on by itself
        self.script_slots: set[int] = set()   # enemy slots carrying FF9's extra 10,000 HP
        self.battle_first_hp: list[int] = []  # first non-zero HP each enemy slot showed this fight
        self._script_cache: dict[int, bool] = {}
        self.active_party: set[int] = set()
        self.identified = False
        self.battle_cleared = False
        self.battle_tentative = False
        self.battle_random_possible = True
        self.battle_counted = False
        self._ident_form: list[int] = []
        self.battle_id_now: int | None = None           # FF9's number for the fight you're in
        self._blk_start: list[bytes] = []               # enemy records when the fight started
        self._blk_now: list[bytes] = []
        self._blk_dyn: list[set] = []                   # bytes in them that changed this fight
        self._bag_prev: dict[int, int] | None = None   # bag on the last read (pickups)
        self._battle_end_at = -1e9
        self._gil_prev: int | None = None
        self._reset_pending = None          # (timer before, place before) after a reset, until it's clear
        self._steal_prev: dict[int, int] | None = None  # bag on the last read (steals)
        self._enc_flag_field: bool | None = None       # random battles possible here (last field read)
        self._enc_flag_seen: set[bool] = set()          # values that flag has shown
        self.battle_start_time = 0.0
        self.pre_battle_step = 0
        self.last_end: tuple[list[int], int, float] | None = None  # formation, step, time
        self.sub = 0                    # fights already won on this step (steps with several fights)
        self._last_end_sub = False      # the last win only moved on to the step's next fight
        self._last_learned: str | None = None
        self.inside_done: set[int] = set()  # fights inside a step (Baku in Crash Site) already won
        self._end_dest: int | None = None   # where the last won fight sent you (F7 undoes it)
        self._back_to_parent: int | None = None
        self.learned_this_session: set[str] = set()
        self._skip_teach = False
        self.events: list[str] = []          # lines for logs/run_log.txt
        self.branch_choice: dict[str, str] = {}   # branch group -> the branch you're on
        self.branch_at: dict[str, int] = {}       # branch group -> game timer when it was decided
        self.groups_done: dict[int, int] = {}     # kinds of fight on this step -> how many won
        self.cur_group: int | None = None         # the kind of fight being fought now
        self.last_party: list[str] = []      # who was in the party in the last fight (LV5 warning)
        self.party_seen: set[str] = set()    # everyone seen in a fight this run
        self.obtained: list[list] = []       # [game timer, item, how many, step] got this run (branches)
        self.show_next = False               # show the next step under this one (config: overlay.show_next)
        self.loaded_at = -1e9                # when a save was last loaded (screens aren't learned then)
        self.step_since = 0.0                # when the current step came up
        self._now = 0.0
        self._field_sig: bytes | None = None      # battle-code bytes last seen on the field
        self._fight_sig: bytes | None = None      # ... at the start of this fight
        self._fight_sig_stable = False
        self.battle_last_party: list[int] = []
        self.battle_start_party: list[int] = []
        self._fp_mismatch_since: float | None = None
        self._fp_mismatch_last = 0.0

        # field
        self.field_cur: int | None = None
        self.field_since = 0.0
        self.field_handled: int | None = None
        self._pending_field_learn: tuple[int, bool] | None = (self.step, False)
        self._pending_screen: int | None = None

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    def _load_progress(self) -> None:
        if self.progress_path and self.progress_path.exists():
            try:
                data = json.loads(self.progress_path.read_text())
                self.step = max(0, min(int(data.get("step", 0)), len(self.steps) - 1))
                self.encounters = [int(t) for t in data.get("encounters", [])]
                self.sub = int(data.get("sub", 0))
                self.groups_done = {int(k): int(v) for k, v in (data.get("groups_done") or {}).items()}
                self.branch_choice = {str(k): str(v) for k, v in (data.get("branches") or {}).items()}
                self.obtained = [list(x) for x in data.get("obtained") or []]
                # Prefer the saved step's identity, so edits to route.json don't move you.
                key = data.get("key")
                if key and self.step_key(self.steps[self.step]) != key:
                    matches = [i for i, st in enumerate(self.steps) if self.step_key(st) == key]
                    self.step = matches[0] if matches else 0
            except (ValueError, OSError):
                self.step = 0

    def _save_progress(self) -> None:
        if self.progress_path:
            from paths import write_atomic
            try:
                write_atomic(self.progress_path, json.dumps(
                    {"step": self.step, "sub": self.sub, "key": self.step_key(self.current()),
                     "groups_done": self.groups_done,
                     "encounters": self.encounters, "branches": self.branch_choice,
                     "obtained": self.obtained}))
            except OSError:
                pass

    # -- resets and the encounter counter ---------------------------------
    def _track_igt(self, igt: int) -> None:
        """Watch FF9's in-game timer for resets and loads.

        Back to (almost) zero from well above: the game was reset or a new game
        started, so the route goes back to step 1 and the counter clears. Back by
        more than a couple of seconds: a save or savestate was loaded, so random
        battles fought after that moment come off the count. Nothing is trusted
        until the timer has been seen counting up normally (a wrong address won't)."""
        last, self._igt_last = self._igt_last, igt
        if last is None:
            return
        d = igt - last
        pending = self._reset_pending
        if pending is not None:
            # The timer went back to zero and the route went back to step 1 straight away.
            # Counting up from there is a new game: done. Jumping up instead is a save
            # loaded from the title screen: back to where you were.
            if 0 < d <= IGT_MAX_STEP and IGT_RESET_CONFIRM <= igt < IGT_NEW_GAME * 4:
                self._reset_pending = None
                return
            if igt >= IGT_NEW_GAME * 4:
                was, saved = pending
                self._reset_pending = None
                self._undo_reset(saved)
                self._dropped_to(igt, was)
            return
        if 0 < d <= IGT_MAX_STEP:
            self._igt_counted = min(self._igt_counted + d, 10 * IGT_TRUST)
            return
        if self._igt_counted < IGT_TRUST or d >= 0:
            return
        if igt < IGT_NEW_GAME and last > IGT_NEW_GAME * 2:
            saved = (self.step, list(self.encounters), dict(self.stolen), dict(self.picked))
            self._reset_pending = (last, saved)
            self.new_run()                         # reset or title screen: step 1 now
        elif -d > IGT_BACK:
            self._dropped_to(igt, last)

    def _undo_reset(self, saved) -> None:
        """It was a save being loaded after all, not a new game: back to your place."""
        self._event(f"it was a save loaded from the title screen: back to step {saved[0] + 1}")
        self.loaded_at = self._now
        self.step, self.encounters, self.stolen, self.picked = saved
        self._entry_step = None
        self.resets = max(0, self.resets - 1)
        self.turn = 0
        self.message = ""
        self._save_progress()

    def _dropped_to(self, igt: int, was: int) -> None:
        """A save or savestate from timer `igt` was loaded: battles after it come off."""
        if was - igt <= IGT_BACK:
            return
        self._event(f"save loaded (game timer {was // 60}s -> {igt // 60}s)")
        self.loaded_at = self._now
        # Pickups and steals made after that moment haven't happened in the loaded game.
        for t, kind, step, item in [x for x in self.got_log if x[0] > igt]:
            got = (self.picked if kind == "picked" else self.stolen).get(step, [])
            if item in got:
                got.remove(item)
        self.got_log = [x for x in self.got_log if x[0] <= igt]
        self.obtained = [x for x in self.obtained if x[0] <= igt]
        # Branches decided after that moment are decided again; sub-steps done after it
        # haven't been done in the loaded game.
        for g in [g for g, t in self.branch_at.items() if t > igt]:
            self.branch_choice.pop(g, None)
            self.branch_at.pop(g, None)
        self.inside_done = set()
        before = len(self.encounters)
        self.encounters = [t for t in self.encounters if t <= igt]
        if len(self.encounters) != before:
            self._save_progress()

    def new_run(self) -> None:
        """Reset seen: back to the first step with a fresh encounter count."""
        self.resets += 1
        self.encounters = []
        self.party_seen = set()
        self.obtained = []
        self.stolen = {}
        self.picked = {}
        self.last_end = None
        self.inside_done = set()
        self.branch_choice = {}
        self.branch_at = {}
        self.message = "Reset: back to the start of the route"
        self._goto(0, manual=False, why="reset / new game")

    def _count_encounter(self, igt: int) -> None:
        self.encounters.append(igt)
        self._save_progress()

    def switch_route(self, route: dict, progress_path: Path | None) -> None:
        """A different route picked in the editor or launcher: start over on it, at the
        place that route was left at (if progress is kept: config's resume_progress).
        Settings from outside the route (show the next step, why steals can't be seen) stay."""
        learned_path, party_slots, resume = self._init_args
        show_next, steal_off = self.show_next, self.steal_off
        self.__init__(route, progress_path, learned_path, party_slots, resume)
        self.show_next, self.steal_off = show_next, steal_off

    def reload_route(self, route: dict) -> None:
        """Swap in an edited route without losing your place (found by the step's identity)."""
        old_steps, old_step = self.steps, self.step
        self.route, self.steps, self.bosses = route, route["steps"], route["bosses"]
        self.rname = route.get("name", "")
        where = _step_moves(old_steps, self.steps)
        if where.get(old_step) is not None:
            self.step = where[old_step]
        else:
            self.step = max(0, min(old_step, len(self.steps) - 1))
        self.turn = min(self.turn, 99)
        self._follow_moves(where)
        self.last_end = None
        self._save_progress()

    def _follow_moves(self, where: dict[int, int | None]) -> None:
        """After an edit: everything kept by step number goes with its step to where it is
        now (or is dropped, if the step is gone). Branches are named after their step's
        number too ("ways 12")."""
        def m(i):
            return where.get(i) if isinstance(i, int) else None
        self.stolen = {m(i): v for i, v in self.stolen.items() if m(i) is not None}
        self.picked = {m(i): v for i, v in self.picked.items() if m(i) is not None}
        self.got_log = [(t, kind, m(i), item) for t, kind, i, item in self.got_log if m(i) is not None]
        self.inside_done = {m(i) for i in self.inside_done if m(i) is not None}

        def group(g):
            kind, _, n = g.rpartition(" ")
            if kind in ("ways", "pick", "only") and n.isdigit():
                return None if m(int(n)) is None else f"{kind} {m(int(n))}"
            return g                                   # "count Broadsword >= 1": not by number
        for d in (self.branch_choice, self.branch_at):
            moved = {group(g): v for g, v in d.items()}
            d.clear()
            d.update({g: v for g, v in moved.items() if g is not None})
        self.__dict__.pop("_answers", None)
        for name in ("_entry_step", "tick_step", "shop_step", "equip_step", "menu_opened_on",
                     "_end_dest", "_back_to_parent"):
            setattr(self, name, m(getattr(self, name)))
        self.pre_battle_step = m(self.pre_battle_step) if m(self.pre_battle_step) is not None else self.step
        if self._pending_field_learn is not None:
            idx = m(self._pending_field_learn[0])
            self._pending_field_learn = None if idx is None else (idx, self._pending_field_learn[1])

    # ------------------------------------------------------------------
    # Moving through the route
    # ------------------------------------------------------------------
    def _event(self, msg: str) -> None:
        """Something worth a line in logs/run_log.txt (main.py writes them out)."""
        self.events.append(msg)

    def _label(self, i: int) -> str:
        s = self.steps[i]
        return f"{step_numbers(self.steps)[i]} {s.get('title') or self.bosses.get(s.get('boss'), {}).get('name', '')}".strip()

    def _goto(self, index: int, manual: bool, why: str = "") -> None:
        back = index < self.step
        old = self.step
        self._entry_step = None                        # judge the new step from how it starts
        self.step = max(0, min(index, len(self.steps) - 1))
        self.step_since = self._now
        if self.step != old:
            self._event(f"step {self._label(old)} -> {self._label(self.step)}"
                        f" ({why or ('by hand' if manual else 'auto')})")
        self.sub = 0
        self.turn = 0
        if self.step != old:
            self.groups_done = {}
        self._fresh_subs(old)
        if not back:
            self._settle_branch()                      # (going back doesn't decide a branch)
        # Learn where this step starts. Not after going back (F7: you're past it, so the
        # screen you're on isn't its start), and not for sub-steps (they're found by their
        # fight or lines, not their screen). Set here too when the step changes during a
        # fight (a win), so the step before doesn't learn the screen after the fight.
        if not back and not (self.is_inside(self.step) and self.current()["type"] == "boss"):
            self._pending_field_learn = (self.step, manual)
        else:
            self._pending_field_learn = None
        self._pending_screen = None
        self._save_progress()
        if self.in_battle and manual and back:
            # Going back during a fight (often on the victory screen, right after the
            # coach moved on): this fight must not move the route forward again.
            self.battle_is_boss = False
            self.battle_tentative = False
            self.hold_route = True
        elif self.in_battle:
            # A correction during a fight: re-label this fight and remember it.
            self.battle_is_boss = self.current()["type"] == "boss"
            if self.battle_is_boss and manual:
                self.battle_tentative = False
                self.identified = True
                form = self._formation()
                if self._hp_fits(self.step, form) is False:
                    # F8 in a fight whose HP isn't the fight typed for this step: go along
                    # with it this time, but don't learn it (a stray F8 can't spoil the step).
                    self._event(f"not learned: HP {form} doesn't fit {self._label(self.step)}'s typed HP")
                    form = None
                    self._skip_teach = True
                if form:
                    self.learned.set_formation(self._lkey(), form, self.battle_random_possible)
                    self.learned_this_session.add(self._lkey())
                    self._event(f"learned: HP {form} is {self._label(self.step)}")
                if not self._skip_teach:
                    if self.battle_id_now and self._fights(self.step) == 1:
                        self.learned.set_battle_id(self.current()["boss"], self.battle_id_now)
                    fp = self._fingerprint()
                    if fp:
                        self.learned.set_fingerprint(self._lkey(), fp)
                self._skip_teach = False


    def goto(self, index: int, why: str = "by hand") -> None:
        """Manual jump (hotkeys). Teaches the coach."""
        self._goto(index, manual=True, why=why)

    def skip(self, delta: int) -> None:
        """Shift+F8 / Shift+F7: look through the route. Moves the step and nothing else:
        no screen, fight or HP is learned or forgotten, and a fight going on now isn't
        relabelled (it can't move the route on either)."""
        target = self._step_by(self.step, delta)
        if delta == 1 and self.is_inside(self.step) and self.parent_of(self.step) is not None:
            self.inside_done.add(self.step)            # a sub-step: back to its step, as when it's done
            target = self.parent_of(self.step)
        elif delta == 1:
            target = self._next_index(self.step)       # past sub-steps already done
        if target == self.step:
            return
        self._event(f"step {self._label(self.step)} -> {self._label(target)} (Shift skip)")
        old = self.step
        self.step, self.sub, self.turn = target, 0, 0
        self._fresh_subs(old)
        self.step_since = self._now
        self._entry_step = None
        self._pending_field_learn = None
        self.field_handled = self.field_cur        # the screen you're on doesn't jump you anywhere
        self.last_end = None                       # F7 won't undo a fight's learning after this
        if self.in_battle:
            self.battle_is_boss = False
            self.battle_tentative = False
            self.hold_route = True                 # this fight stays out of the route
        self.message = f"Skipped to step {step_numbers(self.steps)[self.step]}"
        self._save_progress()

    def next_step(self) -> None:
        if (self.in_battle and self.identified and not self.battle_is_boss and not self.hold_route
                and self.current()["type"] == "boss" and self._fights(self.step) > 1):
            # In a fight on a step with several fights that wasn't recognised: this is
            # one of them. Learn it (as the step's next fight) rather than skip the step.
            self._confirm_fight()
            return
        if not self.in_battle and self.is_inside(self.step):
            self.inside_done.add(self.step)            # F8 on a fight inside a step: back to that step
            parent = self.parent_of(self.step)
            if parent is not None:
                self.goto(parent, why="F8 (fight inside a step)")
                self._back_to_parent = parent
                return
        self.goto(self._next_index(self.step, skip_pending=True), why="F8")

    def _confirm_fight(self) -> None:
        self.battle_is_boss, self.battle_tentative = True, False
        if self.battle_counted and self.encounters:
            self.encounters.pop()                      # it was counted as a random battle
            self.battle_counted = False
        k = self._lkey()
        form = self._formation()
        if form:
            self.learned.set_formation(k, form, self.battle_random_possible)
        fp = self._fingerprint()
        if fp:
            self.learned.set_fingerprint(k, fp)
        self.learned_this_session.add(k)
        self._event(f"F8: this fight learned as fight {self.sub + 1} of {self._label(self.step)}")
        self.message = f"Fight {self.sub + 1} of {self._fights(self.step)} learned"
        self._save_progress()

    def prev_step(self) -> None:
        # Undo a wrong automatic "boss done": forget what that fight taught us.
        if (not self.in_battle and self.last_end
                and self._now - self.last_end[2] < UNDO_WINDOW
                and self.step == (self._end_dest if self._end_dest is not None else self.last_end[1] + 1)):
            self._forget(self._last_learned)
            fight = self.last_end[1]
            self.inside_done.discard(fight)
            self.last_end = None
            if self.step != fight + 1:
                self.goto(fight, why="F7: undo fight win")  # it sent you back to the step it's inside
                return
        elif (not self.in_battle and self.sub > 0 and self.last_end
                and self._now - self.last_end[2] < UNDO_WINDOW and self.step == self.last_end[1]):
            self._forget(self._last_learned)           # a step with several fights: undo the last one
            self.last_end = None
            self.sub -= 1
            self._ungroup()
            self._save_progress()
            return
        elif not self.in_battle and self.sub > 0:
            self.sub -= 1                              # back one fight on this step
            self._ungroup()
            self._save_progress()
            return
        self.goto(self._step_by(self.step, -1), why="F7")

    def _forget(self, key):
        if key and key in self.learned_this_session:
            for part in ("formations", "battle_ids", "fingerprints"):
                self.learned._put(part, key, None)
            self.learned.save()
            self.learned_this_session.discard(key)

    def next_turn(self) -> None:
        self.turn += 1

    def prev_turn(self) -> None:
        self.turn = max(0, self.turn - 1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def current(self) -> dict:
        return self.steps[self.step]

    # -- alternative steps (14 / 14a) ------------------------------------
    # A step with alternatives, or a "when", counts only when that's how the run went;
    # other steps always do, so the route carries on the same afterwards. See route_branches.
    def branch_of(self, s: dict) -> tuple[str, str] | None:
        self._branches()
        return self._b_of.get(id(s))

    def branch_options(self, group: str) -> list[str]:
        return self._branches()[0].get(group, [])

    def _branches(self):
        if getattr(self, "_b_for", None) is not self.steps:
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
        seen = self.__dict__.setdefault("_answers", {})
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

    def _fresh_subs(self, old: int) -> None:
        """Arriving at a step other than from one of its own sub-steps: its sub-steps are
        still to do (they may have been passed before, then a save loaded or F7 pressed)."""
        if self.is_inside(self.step) or self.step == old:
            return
        if self.is_inside(old) and self.parent_of(old) == self.step:
            return
        j = self.step + 1
        while self.is_inside(j):
            self.inside_done.discard(j)
            j += 1

    def _step_by(self, i: int, d: int) -> int:
        """The d-th step on your branch after (d > 0) or before (d < 0) step i."""
        n, j = abs(d), i
        while n:
            k = j + (1 if d > 0 else -1)
            while 0 <= k < len(self.steps) and not self.active(k):
                k += 1 if d > 0 else -1
            if not 0 <= k < len(self.steps):
                break
            j, n = k, n - 1
        return j

    # -- fights inside a step (Crash Site: walk on to Plant Brain, Baku in the middle) --
    def is_inside(self, i: int) -> bool:
        return 0 <= i < len(self.steps) and bool(self.steps[i].get("inside")) and i > 0

    def parent_of(self, i: int) -> int | None:
        """The step a fight is inside: the nearest step above that isn't inside one itself."""
        j = i - 1
        while j >= 0 and self.is_inside(j):
            j -= 1
        return j if j >= 0 else None

    def inside_of(self, i: int) -> list[int]:
        """The sub-steps of step i (the steps right after it marked as inside), on your branch."""
        out, j = [], i + 1
        while self.is_inside(j):
            if self.active(j):
                out.append(j)
            j += 1
        return out

    def _next_index(self, i: int, skip_pending: bool = False) -> int:
        """The step after i on your branch, past sub-steps already done (or all of them
        with skip_pending: moving on from the step they're in)."""
        j = i + 1
        while j < len(self.steps) and (not self.active(j) or
                                       (self.is_inside(j) and (skip_pending or j in self.inside_done))):
            j += 1
        if j >= len(self.steps):
            return i if self.active(i) else len(self.steps) - 1
        return j

    def _pending_inside(self, i: int) -> list[int]:
        return [j for j in self.inside_of(i) if j not in self.inside_done]

    # -- steps with several fights (Fights: 3) -----------------------------
    def _fights(self, i: int) -> int:
        # How many fights fight step i has (1 unless the route says more).
        try:
            return max(1, int(self.bosses.get(self.steps[i].get("boss"), {}).get("fights") or 1))
        except (TypeError, ValueError):
            return 1

    def _keys(self, i: int) -> list:
        # What each of step i's fights is learned under: its id, then id#2, id#3... With
        # kinds of fight (soldiers, mages): one key per kind, id@0, id@1.
        b = self.steps[i]["boss"]
        groups = self._groups(i)
        if groups:
            return [f"{b}@{k}" for k in range(len(groups))]
        return [b] + [f"{b}#{n}" for n in range(2, self._fights(i) + 1)]

    def _lkey(self) -> str:
        # What the fight being fought on the current step is learned under.
        keys = self._keys(self.step)
        if self._groups(self.step):
            g = self.cur_group
            if g is None and self.in_battle:
                g = self._group_of(self.step, self._formation())
            return keys[g if g is not None else self._next_group(self.step)]
        return keys[min(self.sub, len(keys) - 1)]

    # -- kinds of fight on one step (Cleyra: soldiers and mages) -------------
    def _groups(self, i: int) -> list[dict]:
        if not 0 <= i < len(self.steps) or self.steps[i]["type"] != "boss":
            return []
        return self.bosses.get(self.steps[i]["boss"], {}).get("groups") or []

    def _ungroup(self) -> None:
        """A fight on a step with kinds of fight undone (F7, or it wasn't over)."""
        g = getattr(self, "_last_group", None)
        if g is None or not self._groups(self.step):
            return
        if self.groups_done.get(g, 0) > 0:
            self.groups_done[g] -= 1
        self._last_group = None

    def _next_group(self, i: int) -> int:
        """The first kind of fight on step i still to do."""
        groups = self._groups(i)
        for k, g in enumerate(groups):
            if self.groups_done.get(k, 0) < max(1, int(g.get("count") or 1)):
                return k
        return max(0, len(groups) - 1)

    def _group_of(self, i: int, form: list[int]) -> int | None:
        """Which kind of fight on step i these enemies are (by HP); None if none fits.
        Kinds still to do first, so two kinds with the same enemies are counted in order."""
        groups = self._groups(i)
        live = [hp_candidates(h) for h in form if h]
        if not groups or not live:
            return None
        def todo(k):
            return self.groups_done.get(k, 0) < max(1, int(groups[k].get("count") or 1))
        order = sorted(range(len(groups)), key=lambda k: not todo(k))
        for need_all in (True, False):
            for k in order:
                sig = set(groups[k]["hp"])
                hits = [bool(c & sig) for c in live]
                if (all(hits) if need_all else any(hits)):
                    return k
        return None

    def _learned_form(self, i: int) -> bool:
        return any(self.learned.formation(k) for k in self._keys(i))

    def _hp_fits(self, i: int, form: list[int]) -> bool | None:
        """Whether these enemy HPs fit the HP typed for fight step i (None: can't tell -
        no HP typed, several fights on the step, or HP that doesn't fit the counter).
        Typed HP is never overruled by learning: a fight taught by a stray F8 (the soldiers
        before Beatrix II) can't stop the real one being recognised, or be taken for it."""
        typed = self.bosses.get(self.steps[i].get("boss"), {}).get("hp")
        if not typed or not form or self._fights(i) > 1 or CAPPED_HP in form:
            return None
        live = set().union(*(hp_candidates(h) for h in form))
        return bool(live & set(typed))

    @staticmethod
    def step_key(s: dict) -> str:
        if s["type"] == "boss":
            return f"boss:{s['boss']}"
        return f"{s['type']}:{s.get('title', '')}:{step_lines(s)[0]}"

    def _boss_steps_ahead(self, start: int, count: int):
        """Fight steps from `start` to `count` steps ahead, on your branch."""
        i = start if self.active(start) else self._step_by(start, 1)
        for _ in range(count + 1):
            if self.steps[i]["type"] == "boss" and i not in self.inside_done and self.active(i):
                yield i
            j = self._step_by(i, 1)
            if j == i:
                break
            i = j

    def _known_id(self, i: int) -> int | None:
        """The battle number fight step i is known by (typed in the route, or learned)."""
        b = self.steps[i]["boss"]
        typed = self.bosses.get(b, {}).get("battle_id")
        return int(typed) if typed is not None else self.learned.battle_id(b)

    def _match_battle_id(self, bid: int | None) -> int | None:
        """The fight step (this one or a few ahead) whose battle this is, by FF9's own number."""
        if not bid:
            return None
        for i in self._boss_steps_ahead(self.step, LOOKAHEAD_STEPS):
            if self._known_id(i) == bid:
                return i
        return None

    def _match_formation(self, form: list[int]) -> int | None:
        if not form:
            return None
        # Fights whose battle number is known were checked by number already: not by HP.
        # Fights known by battle number or by their enemies' records were checked that
        # way already: not by HP.
        # A fight with learned enemy records is left out only when those records clearly
        # belong to other enemies. If one of the learned enemies isn't here at all, the
        # records may have been learned off a leftover slot (Beatrix II scored 0 against
        # hers but had her exact HP): HP still decides.
        ahead = [i for i in self._boss_steps_ahead(self.step, LOOKAHEAD_STEPS)
                 if not (self.battle_id_now and self._known_id(i) is not None)
                 and not self._fp_rules_out(i)]
        for i in ahead:  # exact learned formation (unless it goes against typed HP)
            if self._hp_fits(i, form) is not False and \
                    any(self.learned.formation(k) == form and self._learned_ok_here(k, self.learned.fingerprint(k), form)
                        for k in self._keys(i)):
                return i
        if CAPPED_HP in form and not self.battle_random_possible:
            # A boss whose HP doesn't fit the counter: the next boss with HP on its card. Not
            # where random battles happen: a leftover 65535 in a random isn't a boss.
            nxt = next((i for i in ahead if self.bosses.get(self.steps[i]["boss"], {}).get("hp")), None)
            if nxt is not None:
                return nxt
        live = set().union(*(hp_candidates(h) for h in form))
        n = 0                          # recognisable fights passed so far
        for i in ahead:  # then a known boss HP value (raw, or with the scripted 10,000 added)
            sig = set(self.bosses.get(self.steps[i]["boss"], {}).get("hp", []))
            if not live & sig:
                n += self._fight_known(i)
                continue
            if n == 0:
                return i                               # the next fight: one enemy's HP is enough
            # Further on, skipping a fight: only if every enemy here is that fight's, and not
            # where random battles happen (a 33 HP random isn't Plant Spiders three fights on).
            if n <= 1 and all(hp_candidates(h) & sig for h in form) and not self.battle_random_possible:
                return i                               # (and at most one fight skipped)
            n += self._fight_known(i)
        return None

    def _next_boss_unknown(self) -> int | None:
        for i in self._boss_steps_ahead(self.step, SCRIPTED_LOOKAHEAD):
            b = self.steps[i]["boss"]
            known = self._learned_form(i) or self.bosses.get(b, {}).get("hp")
            return None if known else i
        return None

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------
    def update(self, state: GameState, now: float | None = None) -> View:
        now = time.monotonic() if now is None else now
        if self.prev is None and self.step_since == 0.0:
            self.step_since = now                      # a step picked before the first read
        self._now = now
        prev = self.prev
        self.prev = state
        if self.in_battle and self.battle_start_party:
            self._party_now(state)                     # who's with you (for item counts)
        self._track_igt(state.igt)
        self._track_obtained(prev, state)
        self._bag_steals(state)
        if not self.in_battle and state.battle_vote is not True:
            self._enc_flag_field = state.encounters_possible
            if state.encounters_possible and True not in self._enc_flag_seen \
                    and not self.learned.data.get("enc_flag_ok"):
                self.learned.set_flag("enc_flag_ok", True)    # the flag works on this copy
            self._enc_flag_seen.add(bool(state.encounters_possible))

        moving = False
        acted = 0
        hp_changed = False
        if prev is not None:
            hp_changed = state.enemy_hp != prev.enemy_hp
            moving = _bars_filling(prev.party_atb, state.party_atb) or \
                _bars_filling(prev.enemy_atb, state.enemy_atb)
            for old, new in zip(prev.party_atb, state.party_atb):
                if old > 0 and new < old * ATB_DROP:
                    acted += 1
        if moving or (self.in_battle and hp_changed):
            self.last_activity = now

        self._calibrate_fp(state, moving, hp_changed, now)
        battle_now = self._battle_signal(state, moving, now)

        if battle_now and not self.in_battle:
            # Wait for the bars to move so enemy HP is this fight's, not the last one's.
            if moving and any(h > 0 for h in state.enemy_hp):
                self._battle_started(state, now)
        elif not battle_now and self.in_battle:
            self._battle_ended(now)

        if self.in_battle:
            if prev is not None:
                self._mark_active(prev, state)
                self._check_phase_change(prev, state)
            self.battle_last_hp = list(state.enemy_hp)
            left = self.field_enemy_hp
            settled = now - self.battle_start_time >= STALE_SECONDS
            for k, h in enumerate(state.enemy_hp):
                if k < len(self.battle_first_hp) and self.battle_first_hp[k] <= 0 < h:
                    same_as_before = k < len(left) and h == left[k]
                    # A new value is this fight's. An unchanged one counts once its bar has been
                    # filling for a moment: same HP as before (or a fight that picked up again).
                    if not same_as_before or (settled and k in self.active_enemy):
                        self.battle_first_hp[k] = h
            self.battle_last_party = list(state.party_hp_slots)
            self._track_blocks(state)
            if state.battle_sig != self._fight_sig:
                self._fight_sig_stable = False
            if not self.identified:
                start_live = {k for k, h in enumerate(self.battle_start_hp) if h > 0}
                if (start_live and start_live <= self.active_enemy) \
                        or now - self.battle_start_time >= IDENTIFY_WITHIN:
                    self._identify(state, now)
            elif (not self.battle_is_boss or self.battle_tentative) and not self.hold_route:
                self._reidentify()
            if acted and self.battle_is_boss:
                self.turn += acted
            real = self._fight_enemies()
            if self.identified and real and all(self._shown_hp(k, state.enemy_hp[k]) == 0 for k in real):
                self.battle_cleared = True
                if self.cleared_since is None:
                    self.cleared_since = now
                # Boss beaten: move on now rather than waiting for the victory screen
                # (or a scripted ending) to hand back to the field.
                if (self.battle_is_boss and not self.hold_route
                        and now - self.cleared_since >= CLEARED_SECONDS
                        and not self._too_short_for_boss(now)):
                    self._finish_boss(now)
                    self.battle_is_boss = False
                    self.won_early = True
            else:
                self.cleared_since = None
        else:
            if state.battle_sig:
                self._field_sig = state.battle_sig
            if not battle_now:                 # truly on the field, not a fight about to start
                self.field_enemy_hp = list(state.enemy_hp)
            self._track_field(state, now)
            self._track_bag(state)
            self._bag_pickups(state, now)
            self._track_menu(state)

        return self._view(state)

    # -- battle in/out -----------------------------------------------------
    def _calibrate_fp(self, state: GameState, moving: bool, hp_changed: bool, now: float) -> None:
        """Watch the battle-code bytes for signs they've stopped being a good signal.
        They're only learned (in _battle_ended) from a fight that was clearly real."""
        if state.battle_vote is not None or self.learned.data.get("fp_unusable") or not state.battle_sig:
            return
        fp = self.learned.battle_fp
        obvious_battle = moving and any(h > 0 for h in state.enemy_hp)
        if fp is None:
            return
        # Bars clearly running but the bytes don't match: the check is wrong for this setup.
        if obvious_battle and state.battle_sig != fp:
            if self._fp_mismatch_since is None or now - self._fp_mismatch_last > 2.0:
                self._fp_mismatch_since = now
            self._fp_mismatch_last = now
            if now - self._fp_mismatch_since > FP_MISMATCH_LIMIT:
                self._fp_fail("Battle check unreliable here: using ATB bars")
        # "In battle" with no bar movement for a minute: the bytes match on the field too.
        elif self.in_battle and state.battle_sig == fp and now - self.last_activity > FP_STUCK_LIMIT:
            self._fp_fail("Battle check stuck: using ATB bars")

    def _fp_fail(self, msg: str) -> None:
        self.learned.mark_fp_unusable()
        self.message = msg
        self._fp_mismatch_since = None

    def _battle_signal(self, state: GameState, moving: bool, now: float) -> bool:
        if state.battle_vote is not None:          # calibrated with `finder.py fights`: exact
            return state.battle_vote
        fp = self.learned.battle_fp
        if fp is not None and state.battle_sig:
            return state.battle_sig == fp
        # Fallback: ATB bars
        if not self.in_battle:
            return moving and any(h > 0 for h in state.enemy_hp)
        if self.battle_cleared:
            return False
        return now - self.last_activity <= QUIET_SECONDS_TO_END

    def _mark_active(self, prev: GameState, state: GameState) -> None:
        """A slot is really in the fight once its ATB bar or HP moves."""
        # Unused slots can hold leftovers that get rewritten as a fight loads, so only
        # count a bar climbing at a believable rate, or HP going down (taking damage).
        for k in range(len(state.enemy_atb)):
            rise = state.enemy_atb[k] - prev.enemy_atb[k]
            if 0 < rise <= MAX_ATB_STEP or 0 <= state.enemy_hp[k] < prev.enemy_hp[k]:
                self.active_enemy.add(k)
        for k in range(len(state.party_atb)):
            if (state.party_atb[k] != prev.party_atb[k]
                    or state.party_hp_slots[k] != prev.party_hp_slots[k]):
                self.active_party.add(k)

    def _real_enemies(self) -> list[int]:
        """Enemy slots in this fight: moving, with HP of this fight (not leftovers).
        If none has moved yet, fall back to any with this fight's HP."""
        loaded = {k for k, h in enumerate(self.battle_start_hp) if h > 0}
        moving = self.active_enemy & loaded
        return sorted(moving) if moving else sorted(loaded)

    def _fight_enemies(self) -> list[int]:
        """The enemies you're really fighting. On a recognised boss whose card has HP,
        only slots matching the card count: scripted fights can have extra invisible
        slots whose numbers move (Masked Man showed 35 and 37 beside his own HP).
        Anything else, or if nothing matches, falls back to every slot in the fight."""
        real = self._real_enemies()
        # In a scripted fight (an enemy with the hidden 10,000), tiny non-scripted slots
        # are the game's invisible helpers (35 / 37 with Masked Man, 28 / 32 with King Leo).
        if any(self._is_script(k) for k in real):
            real = [k for k in real if self._is_script(k) or self.battle_first_hp[k] >= 100]
        cur = self.current()
        if not (self.battle_is_boss and cur["type"] == "boss"):
            return real
        card = self.bosses.get(cur["boss"], {})
        allowed = set(card.get("hp") or []) | set(card.get("also_hp") or [])
        if not allowed:
            return real
        def first(k):
            return self.battle_first_hp[k] if k < len(self.battle_first_hp) else 0
        hit = [k for k in real if hp_candidates(first(k)) & allowed or first(k) == CAPPED_HP]
        return hit or real

    def _formation(self) -> list[int]:
        return formation_of([self.battle_start_hp[k] for k in self._real_enemies()])

    def _battle_started(self, state: GameState, now: float) -> None:
        self.in_battle = True
        self.identified = False
        self.battle_cleared = False
        self.battle_start_time = now
        # Enemy memory can still hold the last fight's numbers for a moment after a new
        # fight starts (Plant Brain's 10,916 showed up as Plant Spiders and Black Waltz 1).
        # A slot still holding exactly what it held on the field isn't this fight's yet:
        # it counts from the first new value it shows.
        left = self.field_enemy_hp
        start = [0 if (k < len(left) and h == left[k]) else h for k, h in enumerate(state.enemy_hp)]
        self.battle_start_hp = start
        # Whether random battles can happen on the screen this fight started from.
        # Taken from the field just before (the flag can change once the fight loads), and
        # only trusted once it's been seen saying "yes": on some versions the address is
        # wrong (always 0), and then every fight that isn't a boss counts.
        flag = self._enc_flag_field if self._enc_flag_field is not None else state.encounters_possible
        trusted = True in self._enc_flag_seen or self.learned.data.get("enc_flag_ok")
        self.battle_random_possible = flag if trusted else True
        self.active_enemy, self.active_party = set(), set()
        self.script_slots = set()
        self.hold_route = False
        self.battle_counted = False
        self.battle_id_now = None
        self._blk_start = list(state.enemy_blocks)
        self._blk_now = list(state.enemy_blocks)
        self._blk_dyn = [set() for _ in state.enemy_blocks]
        self.battle_first_hp = self.battle_start_hp      # the same list: filled in as slots load
        self._script_cache = {}
        self.cleared_since = None
        self.won_early = False
        self.battle_last_party = list(state.party_hp_slots)
        self.battle_start_party = list(state.party_hp_slots)
        self._fight_sig, self._fight_sig_stable = state.battle_sig, bool(state.battle_sig)
        self.turn = 0
        self.message = ""
        self.steal_note = None
        self.pre_battle_step = self.step
        self.battle_tentative = False
        self.battle_is_boss = False

    def _identify(self, state: GameState, now: float) -> None:
        """Decide which fight this is, once we know which enemy slots are real."""
        self.identified = True
        form = self._formation()
        self._ident_form = form

        # Same fight picked up again (fallback mode ended it too early)
        if self.last_end:
            end_form, end_step, t = self.last_end
            if now - t < RESUME_WINDOW and form and form == end_form:
                if end_step == self.step and self._last_end_sub and self.sub > 0:
                    self.sub -= 1                      # it wasn't over: same fight, not the next
                    self._ungroup()
                self._last_end_sub = False
                self.step = end_step
                self.battle_is_boss = True
                self.message = "Same fight: back to its card"
                self.script_slots = self._find_script_slots()
                self._save_progress()
                return

        self.battle_id_now = state.battle_id
        how = "battle number"
        jump = self._match_battle_id(self.battle_id_now)
        if jump is None:
            how = "enemy records"
            jump = self._match_fingerprint()
        if jump is None:
            how = "enemy HP"
            jump = self._match_formation(form)
        was = self.step
        if jump is not None:
            if jump != self.step:
                self.message = f"Recognised: {self.bosses[self.steps[jump]['boss']]['name']}"
            self.step = jump
            self.battle_is_boss = True
            what = f"recognised as {self._label(jump)} (by {how})"
        elif self._probably_this_boss(form):
            self.battle_is_boss = True
            self.battle_tentative = True
            what = f"taken as {self._label(self.step)} (a guess: no random battles here or hidden HP)"
        elif self.current()["type"] != "boss" and not state.encounters_possible:
            what = "not recognised"
            nb = self._next_boss_unknown()
            if nb is not None:
                self.step = nb
                self.battle_is_boss = True
                self.battle_tentative = True
                self.message = "Scripted fight: assuming next boss"
                what = f"taken as {self._label(nb)} (scripted fight, next unknown fight step)"
        else:
            what = "random battle" if self.battle_random_possible else "not recognised"
        if self.step != was:
            self.step_since = self._now
        self._event(f"fight started, HP {form}: {what}")
        self.script_slots = self._find_script_slots()
        if not self.battle_is_boss and self.battle_random_possible and not self.battle_counted:
            # A random battle: it counts as soon as it's recognised, not when it ends.
            self.battle_counted = True
            self._count_encounter(self._igt_last or 0)
        self._save_progress()

    # -- fight fingerprints: the enemies' own records, compared byte by byte ----
    def _track_blocks(self, state: GameState) -> None:
        if len(self._blk_start) != len(state.enemy_blocks):
            self._blk_start = list(state.enemy_blocks)
            self._blk_dyn = [set() for _ in state.enemy_blocks]
        for k, (a, b) in enumerate(zip(self._blk_start, state.enemy_blocks)):
            if a != b:
                self._blk_dyn[k].update(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
        self._blk_now = list(state.enemy_blocks)

    def _fingerprint(self) -> dict | None:
        """This fight's enemies as they're kept in memory (stats, not HP or timers)."""
        slots = self._fight_enemies()                  # not leftovers in empty slots
        if not slots or not self._blk_now:
            return None
        fp = {"slots": {str(k): {"b": self._blk_now[k].hex(), "dyn": sorted(self._blk_dyn[k])}
                        for k in slots if k < len(self._blk_now)},
              "form": self._formation(),               # its HP then (bad teaching shows up against typed HP)
              "random": self.battle_random_possible}   # random battles possible where it was fought
        solid = sum(1 for k in slots if k < len(self._blk_now)
                    for i, x in enumerate(self._blk_now[k]) if x and i not in self._blk_dyn[k])
        return fp if solid >= 16 else None             # too little to tell fights apart by

    def _fp_score(self, fp: dict | None) -> float:
        """0..1: how much this fight's enemy records match a learned fight's."""
        if not fp or not self._blk_now:
            return 0.0
        same = total = 0
        for ks, rec in fp.get("slots", {}).items():
            k = int(ks)
            if k >= len(self._blk_now):
                return 0.0
            old, cur = bytes.fromhex(rec["b"]), self._blk_now[k]
            if not any(cur):
                return 0.0                             # that enemy isn't in this fight
            skip = set(rec.get("dyn", [])) | (self._blk_dyn[k] if k < len(self._blk_dyn) else set())
            s = t = 0
            for i, (x, y) in enumerate(zip(old, cur)):
                if i in skip or (x == 0 and y == 0):
                    continue
                t += 1
                s += x == y
            if t and s / t < FP_MATCH - 0.1:
                return 0.0                             # one enemy clearly different
            same, total = same + s, total + t
        return same / total if total else 0.0

    def _match_fingerprint(self) -> int | None:
        best, best_i = 0.0, None
        form = self._formation()
        for i in self._boss_steps_ahead(self.step, LOOKAHEAD_STEPS):
            sc = max((self._fp_score(self._fp(i, k)) for k in self._keys(i)
                      if self._learned_ok_here(k, self._fp(i, k), form)), default=0.0)
            if sc > best:
                best, best_i = sc, i
        return best_i if best >= FP_MATCH else None

    def _learned_ok_here(self, k: str, fp: dict | None, form: list[int]) -> bool:
        """A fight learned where random battles can't happen (the Iifa elevator) isn't
        recognised by what it learned on a screen where they can: random battles there can
        have the very same enemies. Older learning, without that noted: only by the exact
        HP it learned."""
        if not self.battle_random_possible:
            return True
        flag = (fp or {}).get("random")
        if flag is None:
            flag = self.learned.random_here(k)
        if flag is not None:
            return flag
        learned_form = self.learned.formation(k) or (fp or {}).get("form")
        return not learned_form or learned_form == form

    def _fp(self, i: int, k: str) -> dict | None:
        """Fight k's learned enemy records, unless they were learned off a fight whose HP
        doesn't fit the HP typed for step i (a stray F8): those are ignored."""
        fp = self.learned.fingerprint(k)
        form = (fp or {}).get("form") or self.learned.formation(k)    # older records: the HP learned with them
        if fp and form and self._hp_fits(i, form) is False:
            return None
        return fp

    def _fp_rules_out(self, i: int) -> bool:
        # Step i's learned enemies are clearly not these (for every one of its fights).
        return all(self._fp_rules_out_one(self._fp(i, k)) for k in self._keys(i))

    def _fp_rules_out_one(self, fp) -> bool:
        if not fp or not self._blk_now:
            return False
        for ks in fp.get("slots", {}):
            k = int(ks)
            if k >= len(self._blk_now) or not any(self._blk_now[k]):
                return False                           # a learned enemy that isn't here: suspect
        return self._fp_score(fp) < FP_MATCH

    def _known_fp(self, i: int) -> bool:
        return all(self._fp(i, k) is not None for k in self._keys(i))

    def _probably_this_boss(self, form: list[int]) -> bool:
        """A fight on a fight step that matches nothing the coach knows. It's taken as
        the boss when it can't be a random battle: no random battles on this screen, or
        (Discs 1-2) an enemy carries the hidden 10,000 only scripted fights have, even if
        the HP typed for the fight is off. On a screen with random battles and no such
        enemy it's a random battle, unless the fight has no HP to go on yet and lasts."""
        cur = self.current()
        if cur["type"] != "boss":
            return False
        if self.battle_id_now and self._known_id(self.step) is not None:
            return False                               # its battle number is known, and this isn't it
        if self._known_fp(self.step):
            return False                               # its enemies are known, and these aren't them
        # The hidden 10,000 only counts on an enemy that's really moving: an empty slot
        # can hold a leftover big number (64,338 / 37,660 next to Lifa Tree randoms).
        moving = [self.battle_start_hp[k] for k in self.active_enemy if k < len(self.battle_start_hp)]
        scripted = int(cur.get("disc", 1)) <= 2 and any(SCRIPT_HP <= h < CAPPED_HP for h in moving)
        if scripted:
            return True
        known = self._learned_form(self.step) or self.bosses.get(cur["boss"], {}).get("hp")
        if known:
            return False
        return not self.battle_random_possible

    def _reidentify(self) -> None:
        """An enemy that only starts moving after the fight was judged (Masked Man's own
        slot can wake up after his two invisible helpers) can still make it the boss."""
        form = self._formation()
        if form == self._ident_form:
            return
        self._ident_form = form
        if self.battle_id_now and self._known_id(self.step) is not None:
            return                                     # decided by battle number already
        jump = self._match_fingerprint()
        if jump is None:
            jump = self._match_formation(form)
        tentative = False
        if jump is None:
            if not self._probably_this_boss(form):
                if self.battle_is_boss and self.battle_tentative:
                    # Only a guess, and what the fight turned out to be doesn't back it up
                    # (a leftover big number in an empty slot looked like hidden HP): a
                    # random battle after all.
                    self.battle_is_boss = self.battle_tentative = False
                    self._event(f"fight now HP {form}: a random battle after all")
                    self.step = self.pre_battle_step
                    self.turn = 0
                    self.message = ""
                    if self.battle_random_possible and not self.battle_counted:
                        self.battle_counted = True
                        self._count_encounter(self._igt_last or 0)
                    self._save_progress()
                return
            jump, tentative = self.step, True
        elif self.battle_is_boss and self.battle_tentative and jump == self.step:
            self.battle_tentative = False              # the guess is now backed up
            return
        if jump != self.step or not self.battle_is_boss:
            self._event(f"fight now HP {form}: {'taken as' if tentative else 'recognised as'} {self._label(jump)}")
        self.step = jump
        self.battle_is_boss = True
        self.battle_tentative = tentative
        if self.battle_counted and self.encounters:
            self.encounters.pop()                      # it was counted as a random battle
            self.battle_counted = False
        self.script_slots = self._find_script_slots()
        self._save_progress()

    def _find_script_slots(self) -> set[int]:
        """Kept for callers: work the hidden-HP slots out again from scratch."""
        self._script_cache = {}
        return {k for k in range(len(self.battle_first_hp)) if self._is_script(k)}

    def _is_script(self, k: int) -> bool:
        """Whether enemy slot k's HP includes FF9's hidden extra 10,000.

        Bosses whose fight ends on a script get 10,000 HP added, and the fight ends
        when it drops below 10,000. Decided per slot from the first HP that slot
        showed this fight, so an enemy that joins or starts moving late is covered:
        - on a recognised boss: HP minus 10,000 matches the card, or the card has
          no HP / only small HP values (the cards without HP are all early bosses);
        - on any other fight in Discs 1 and 2: nothing there has 10,000 HP for real,
          so 10,000+ means a scripted fight the coach didn't recognise.
        """
        if (self.battle_first_hp[k] if k < len(self.battle_first_hp) else 0) == CAPPED_HP:
            return False
        if k in self._script_cache:
            return self._script_cache[k]
        h = self.battle_first_hp[k] if k < len(self.battle_first_hp) else 0
        if h <= 0:
            return False                      # not loaded yet: decide once it is
        cur = self.current()
        boss = self.battle_is_boss and cur["type"] == "boss"
        known = set(self.bosses.get(cur.get("boss"), {}).get("hp") or []) if boss else set()
        if h < SCRIPT_HP or h in known:
            out = False
        elif h - SCRIPT_HP in known:
            out = True
        elif boss:
            out = not known or all(v < SCRIPT_HP for v in known)
        else:
            out = int(cur.get("disc", 1)) <= 2
        self._script_cache[k] = out
        return out

    def _check_phase_change(self, prev: GameState, state: GameState) -> None:
        """A boss with the hidden 10,000 that drops far below 10,000 in one go has moved
        to a new phase, not been beaten (Plant Brain: 10,916 to about 500 when Blank
        arrives). Beaten looks different: just under 10,000, one hit's worth. From then
        on that enemy's HP is real."""
        cur = self.current()
        card_hp = []
        if self.battle_is_boss and cur["type"] == "boss":
            card_hp = [v for v in (self.bosses.get(cur["boss"], {}).get("hp") or []) if v < SCRIPT_HP]
        early = int(cur.get("disc", 1)) <= 2
        for k, h in enumerate(state.enemy_hp):
            if k >= len(prev.enemy_hp) or not self._is_script(k):
                continue
            was = prev.enemy_hp[k]
            if not (was >= SCRIPT_HP > h > 0):
                continue
            base = self.battle_first_hp[k] - SCRIPT_HP          # e.g. 916
            other = [v for v in card_hp if v != base]           # e.g. 510, the next phase
            if any(h <= v for v in other) or (early and h < SCRIPT_HP - PHASE_GAP):
                self._script_cache[k] = False
                self.battle_first_hp[k] = h
                self.cleared_since = None

    def _shown_hp(self, k: int, h: int) -> int:
        """HP as the game means it (without the hidden 10,000)."""
        first = self.battle_first_hp[k] if k < len(self.battle_first_hp) else 0
        if first == CAPPED_HP:
            card = self._card_hp()
            if card:                    # too big for the counter: card HP minus damage done
                return max(0, max(card) - (CAPPED_HP - h))
        return max(0, h - SCRIPT_HP) if self._is_script(k) else h

    def _card_hp(self) -> list[int]:
        cur = self.current()
        if not (self.battle_is_boss and cur["type"] == "boss"):
            return []
        return [v for v in (self.bosses.get(cur["boss"], {}).get("hp") or []) if v < CAPPED_HP]

    def enemy_debug(self, state: GameState) -> str:
        """One line per enemy slot for --debug: first HP / now / shown, and flags."""
        parts = []
        for k, h in enumerate(state.enemy_hp):
            first = self.battle_first_hp[k] if k < len(self.battle_first_hp) else 0
            flags = ("A" if k in self.active_enemy else "-") + ("S" if self._is_script(k) else "-")
            parts.append(f"{k}:{first}>{h}={self._shown_hp(k, h)}[{flags}]")
        if self.in_battle and self.current()["type"] == "boss" and self._known_fp(self.step):
            parts.append(f"fp={max(self._fp_score(self.learned.fingerprint(k)) for k in self._keys(self.step)):.2f}")
        return " ".join(parts)

    def _too_short_for_boss(self, now: float) -> bool:
        """An unrecognised fight over this quickly, where random battles happen, was a random one."""
        return (self.battle_tentative and self.battle_random_possible
                and now - self.battle_start_time < MIN_BOSS_SECONDS)

    def _learn_battle_id(self) -> None:
        """Remember this fight step's battle number and enemies (the first time it's
        beaten, or on F8)."""
        b = self._lkey()
        self._last_learned = b
        if self._hp_fits(self.step, self._formation()) is False:
            return                                     # not the fight typed for this step: learn nothing
        old = self.learned.fingerprint(b)
        if old is None or (not self.battle_tentative and self._fp_score(old) < FP_MATCH):
            # First win, or the records learned before don't fit the fight that was
            # recognised by its HP: learn them again from this one.
            fp = self._fingerprint()
            if fp:
                self.learned.set_fingerprint(b, fp)
                self.learned_this_session.add(b)
        if self.battle_id_now and self._fights(self.step) == 1 and self.learned.battle_id(b) is None \
                and self.bosses.get(b, {}).get("battle_id") is None:
            self.learned.set_battle_id(b, self.battle_id_now)
            self.learned_this_session.add(b)

    def _finish_boss(self, now: float) -> None:
        self._learn_battle_id()
        b = self._lkey()
        form = self._formation()
        if self.battle_tentative and form and self.learned.formation(b) is None \
                and self._hp_fits(self.step, form) is not False:
            self.learned.set_formation(b, form, self.battle_random_possible)
            self.learned_this_session.add(b)
        # Remember the HP it stopped at, so the same fight picking up again is recognised.
        real = self._real_enemies()
        self.last_end = (formation_of([self.battle_last_hp[k] for k in real
                                       if k < len(self.battle_last_hp)]), self.step, now)
        n = self._fights(self.step)
        groups = self._groups(self.step)
        if groups:
            g = self.cur_group if self.cur_group is not None else self._group_of(self.step, form)
            if g is None or self.groups_done.get(g, 0) >= max(1, int(groups[g].get("count") or 1)):
                g = self._next_group(self.step)        # can't tell which: the next one still to do
            self.groups_done[g] = self.groups_done.get(g, 0) + 1
            self._last_group = g
            self.cur_group = None
            left = sum(max(0, max(1, int(gr.get("count") or 1)) - self.groups_done.get(k, 0))
                       for k, gr in enumerate(groups))
            self._event(f"{groups[g].get('name') or 'fight'} done "
                        f"({self.groups_done[g]}/{max(1, int(groups[g].get('count') or 1))}) on {self._label(self.step)}")
            self.sub = min(n - 1, sum(self.groups_done.values()))
            self._last_end_sub = left > 0
            if left:
                self.turn = 0
                self._save_progress()
                self.message = f"Fight done: {left} more on this step"
                return
        else:
            self._last_end_sub = self.sub + 1 < n
        if self._last_end_sub:
            self.sub += 1                              # the step's next fight
            self.turn = 0
            self._save_progress()
            self.message = f"Fight done: {n - self.sub} more on this step"
            return
        if self.is_inside(self.step) and self.parent_of(self.step) is not None:
            # A fight in the middle of a step: back to that step to carry on with it.
            self.inside_done.add(self.step)
            parent = self.parent_of(self.step)
            self._goto(parent, manual=False, why="fight won, back to its step")
            self._end_dest = parent
            self._back_to_parent = parent
            self.message = f"Fight done: back to {self.steps[parent].get('title') or 'the step'}"
            return
        self._event(f"fight won: {self._label(self.step)}")
        self._goto(self._next_index(self.step), manual=False, why="fight won")
        self._end_dest = self.step
        self.message = "Fight done: next step"

    def _battle_ended(self, now: float) -> None:
        self._battle_end_at = now
        if not self.identified and self.prev is not None:
            self._identify(self.prev, now)
        was_boss = self.battle_is_boss and not self.hold_route
        form = self._formation()
        self.in_battle = False
        self.battle_is_boss = False
        # A wipe: every party member in the fight started alive (1-9999 HP) and ended on 0.
        start = self.battle_start_party
        members = [k for k in sorted(self.active_party)
                   if k < len(start) and k < len(self.battle_last_party) and 0 < start[k] <= 9999]
        wiped = bool(members) and all(self.battle_last_party[k] == 0 for k in members)
        # A scripted boss (hidden 10,000) ends its fight by script once it drops below
        # 10,000, often with other enemies still standing (Steiner II): that's a win too.
        script_beaten = any(self._is_script(k) and k < len(self.battle_last_hp)
                            and 0 < self.battle_last_hp[k] < SCRIPT_HP for k in self._real_enemies())
        decisive = self.battle_cleared or wiped or script_beaten
        if decisive and self._fight_sig_stable and self._fight_sig != self._field_sig \
                and self.learned.battle_fp is None and not self.learned.data.get("fp_unusable"):
            # A fight that clearly happened: its battle-code bytes are a safe signal to learn.
            self.learned.set_battle_fp(self._fight_sig)
        counted = False
        if was_boss:
            doubtful = self.battle_tentative and (not decisive or self._too_short_for_boss(now))
            if doubtful and self.battle_random_possible and not self.battle_counted:
                self._count_encounter(self._igt_last or 0)    # it was a random battle after all
                counted = True
            if doubtful:
                # Not clearly the boss: stay on this step. F8 confirms it if it was.
                self._event("fight ended, but not clearly that fight (no clear win, or too short): staying")
                self.step = self.pre_battle_step
                self.turn = 0
                self._save_progress()
                self.message = ""
            else:
                self._finish_boss(now)
        self.battle_start_hp = []
        self.battle_cleared = False
        self.battle_tentative = False

    # -- field tracking ----------------------------------------------------
    def _track_field(self, state: GameState, now: float) -> None:
        f = state.field_id
        if f is None or f in NO_SCREEN or f >= MAX_SCREEN:
            return                                     # FMVs, loading, title: not a place
        if f != self.field_cur:
            self.field_cur, self.field_since = f, now
            return
        if now - self.field_since < FIELD_SETTLE:
            return

        # Learn where a step starts only once it has been up a moment (several F8s in a
        # row teach nothing about the steps passed through) and not just after a save
        # was loaded (that's a jump, not walking there).
        pend = self._pending_field_learn
        if pend is not None and not (now - self.step_since >= STEP_SETTLE and now - self.loaded_at >= LOAD_SETTLE):
            if self._pending_screen is None:
                self._pending_screen = f               # the screen a step comes up on never jumps it on
                self.field_handled = f
        elif pend is not None:
            idx, overwrite = pend
            self._pending_field_learn = None
            start = self._pending_screen if self._pending_screen is not None else f
            key = self.step_key(self.steps[idx])
            # A route learns its own screens even where older shared learning had one
            # (that was often taught by other routes or by F8s in a row).
            if overwrite or self.learned.data["fields"].get(f"{self.rname}|{key}" if self.rname else key) is None:
                self.learned.set_field(key, start, self.rname)
                self._event(f"learned: {self._label(idx)} starts on {field_name(start)}")
            if self._pending_screen is None:
                self.field_handled = f

        if f == self.field_handled:
            return
        if self.current()["type"] != "boss" and self._menu_progress(state)[1] is not None:
            return                                     # something on this step still to do: stay
        self.field_handled = f
        if self.learned.field(self.step_key(self.current()), self.rname) == f:
            return                                     # still on this step's own screen
        for j in self._pending_inside(self.step):     # a walk/menu sub-step's own screen: into it
            if (self.steps[j]["type"] != "boss"
                    and self.learned.field(self.step_key(self.steps[j]), self.rname) == f):
                self._goto(j, manual=False, why=f"reached {field_name(f)}")
                self._pending_field_learn = None
                self.field_handled = f
                return
        ahead = self._jumpable()
        for i in ahead:
            if self.learned.field(self.step_key(self.steps[i]), self.rname) == f:
                if self._field_ambiguous(i, f):
                    return                             # several nearby steps share this screen
                self._goto(i, manual=False, why=f"reached {field_name(f)}")
                self._pending_field_learn = None
                self.field_handled = f
                return
        # The screen a step of the other branch starts on (one not settled yet): that's the
        # way the run went, so take that branch.
        last = max(self._jumpable(), default=self.step)
        for i in range(self.step + 1, min(len(self.steps), last + 4)):
            b = self.branch_of(self.steps[i])
            if (not b or b[0] in self.branch_choice or self.active(i)
                    or self.learned.field(self.step_key(self.steps[i]), self.rname) != f):
                continue
            if any(self.learned.field(self.step_key(self.steps[j]), self.rname) == f
                   for j in range(max(0, self.step - 3), min(len(self.steps), self.step + 16))
                   if j != i and self.branch_of(self.steps[j]) != b):
                return                                 # other steps start here too: can't tell
            self.branch_choice[b[0]] = b[1]
            self.branch_at[b[0]] = self._igt_last or 0
            self._event(f"took: {self._way_name(*b)} (reached {field_name(f)})")
            self._goto(i, manual=False, why=f"reached {field_name(f)}")
            self._pending_field_learn = None
            self.field_handled = f
            return

    def _blocks(self, i: int) -> bool:
        """A step a screen can't move you past: a fight not won yet (the step's own, or
        one of its sub-steps'). Fights happen in order, so walking on can't skip one."""
        if self.steps[i]["type"] == "boss" and not self.is_inside(i):
            return True
        return any(self.steps[k]["type"] == "boss" for k in self.inside_of(i) if k not in self.inside_done)

    def _jumpable(self) -> list[int]:
        """The steps ahead a screen you reach may move you to (up to FIELD_LOOKAHEAD, and
        no further than the next fight)."""
        out, i = [], self.step
        if self._blocks(i):
            return out
        for _ in range(FIELD_LOOKAHEAD):
            j = self._next_index(i)
            if j == i:
                break
            out.append(j)
            if self._blocks(j):
                break
            i = j
        return out

    def _field_ambiguous(self, target: int, f: int) -> bool:
        lo, hi = max(0, self.step - 3), min(len(self.steps), self.step + 9)
        return any(self.learned.field(self.step_key(self.steps[j]), self.rname) == f
                   for j in range(lo, hi) if j != target and self.active(j))

    # ------------------------------------------------------------------
    # Building what the overlay shows
    # ------------------------------------------------------------------
    def _condition_badges(self, needs: list[str], state: GameState) -> list[tuple[str, str]]:
        """Readiness checks. Battle HP is only live during a fight, so outside one
        these are reminders, not readings."""
        badges = []
        live = self.in_battle and self.battle_is_boss
        # Only party slots that have moved this fight are real; the rest can be leftovers.
        real = {k for k in self.active_party if k < len(state.party_hp_slots)}
        hp = {name: state.party_hp_slots[k] for name, k in self.party_slots.items() if k in real}
        for need in needs:
            if isinstance(need, dict):
                badges.append(self._hp_rule_badge(need, hp, live))
                continue
            if need == "quina_1hp":
                if not live:
                    badges.append(("Quina needs exactly 1 HP", "info"))
                elif "quina" in hp:
                    q = hp["quina"]
                    badges.append(("LIMIT GLOVE READY" if q == 1 else f"Quina HP {q}: need 1",
                                   "good" if q == 1 else "warn"))
                elif any(state.party_hp_slots[k] == 1 for k in real):
                    # Quina's slot isn't set; in Limit Glove fights only Quina is put on 1 HP.
                    badges.append(("LIMIT GLOVE READY (1 HP in party)", "good"))
                else:
                    badges.append(("Nobody on 1 HP yet", "warn"))
            elif need == "zidane_hp7":
                if not live or "zidane" not in hp:
                    badges.append(("Zidane HP must end in 7", "info"))  # also while his slot hasn't moved
                else:
                    z = hp["zidane"]
                    ok = z % 10 == 7
                    badges.append(("LUCKY SEVEN READY" if ok else f"Zidane HP {z}: needs to end in 7",
                                   "good" if ok else "warn"))
        return badges

    @staticmethod
    def _hp_rule_badge(rule: dict, hp: dict[str, int], live: bool) -> tuple[str, str]:
        """A route-defined HP check, e.g. {"who": "zidane", "min": 37, "ok": "...", "bad": "..."}.

        "min" / "max" bound the HP; "note" is shown as a reminder outside fights or
        when the character's slot hasn't moved yet."""
        who = rule.get("who", "zidane")
        name = who.capitalize()
        note = rule.get("note", "")
        if not live or who not in hp:
            return (note or f"Watch {name}'s HP", "info")
        v = hp[who]
        ok = True
        if "min" in rule and v < rule["min"]:
            ok = False
        if "max" in rule and v > rule["max"]:
            ok = False
        msg = rule.get("ok" if ok else "bad", "")
        text = f"{name} HP {v}" + (f": {msg}" if msg else "")
        return (text, "good" if ok else "warn")

    def _fight_readout(self, state: GameState) -> list[tuple[str, str]]:
        """Live enemy HP for the fight you're in, boss or not. Only slots that have
        moved this fight count, so leftovers from earlier fights don't show."""
        out = []
        names = self._hp_names()
        parts = []
        for k in self._fight_enemies():
            h = self._shown_hp(k, state.enemy_hp[k])
            if h <= 0:
                continue
            first = self._shown_hp(k, self.battle_first_hp[k]) if k < len(self.battle_first_hp) else 0
            parts.append((names.get(str(first)), h))
        if parts and any(n for n, _ in parts):
            # Names typed with the fight's HP ("Zorn 229, Thorn 472"): one line each.
            for n, h in parts:
                out.append((f"{n or 'Enemy'} {h:,}", "info"))
        elif parts:
            out.append(("Enemy HP " + " / ".join(f"{h:,}" for _, h in parts), "info"))
        return out

    def _hp_names(self) -> dict[str, str]:
        cur = self.current()
        if not (self.battle_is_boss and cur["type"] == "boss"):
            return {}
        return self.bosses.get(cur["boss"], {}).get("hp_names") or {}

    def _upcoming(self, count: int = 1) -> list[str]:
        out, i = [], self.step
        for _ in range(count):
            j = self._step_by(i, 1)
            if j == i:
                break
            out.append(self._step_label(self.steps[j]))
            i = j
        return out

    def _step_label(self, s: dict) -> str:
        if s["type"] == "boss":
            return self.bosses.get(s["boss"], {}).get("name", s["boss"])
        return s.get("title") or step_lines(s)[0]

    def _status_badges(self, state: GameState) -> list[tuple[str, str]]:
        """Status notes ("Fight done", "Recognised: ...") are kept for --debug only;
        the panel shows just the route."""
        return []

    def _stats(self, state: GameState, ap: bool = True) -> list[str]:
        """Extra lines under a step (none at the moment: AP shows where an @ line asks)."""
        return []

    @staticmethod
    def _ap_lines(lines: list[str], state: GameState) -> tuple[list[str], list[int], list[int]]:
        """@ lines turned into AP readouts, one per ability ("@Zidane Protect Girls  12/35 AP").
        Returns the lines, which readouts are reached (the AP asked for, or learned), and
        where each original line ended up (so ticks on other lines stay on them)."""
        from syntax import ap_requests, level_requests
        out, done, where = [], [], []
        for line in lines:
            where.append(len(out))
            lv = level_requests(line)
            if lv is not None:                         # "@Lv Zidane 15": levels
                indent = line[:len(line) - len(line.lstrip())]
                if not lv:                             # everyone you have (EXP above 0)
                    lv = [(n, None) for n in state.levels if n != "guest" and state.exp.get(n, 0) > 0]
                for who, target in lv:
                    level = state.levels.get(_record_of(who))
                    if level is None or not 0 < level < 100:
                        out.append(f"{indent}@{who.capitalize()} Lv ?")
                        continue
                    if target and level >= target:
                        done.append(len(out))
                    out.append(f"{indent}@{who.capitalize()} Lv {level}" + (f"/{target}" if target else ""))
                continue
            reqs = ap_requests(line)
            if reqs is None:
                out.append(line)
                continue
            indent = line[:len(line) - len(line.lstrip())]
            if not reqs:
                out.append(line)
                continue
            for who, ability, target in reqs:
                have, need = state.ap.get(f"{who}:{ability}", (None, None))
                if have is None:
                    out.append(f"{indent}@{who.capitalize()} {ability}  ? AP")
                    continue
                goal = target or need
                if have >= goal:
                    done.append(len(out))
                out.append(f"{indent}@{who.capitalize()} {ability}  {min(have, goal)}/{goal} AP"
                           + (" (learned)" if have >= need and not target else ""))
        return out, done, where

    def _party_now(self, state: GameState) -> list[str]:
        """Who's in the party: the characters whose HP (in their own record) matches a
        party slot's HP when the fight started. Remembered from the last fight, so the
        card before a boss knows it too. Empty if it can't tell."""
        if self.in_battle and self.battle_start_party:
            slots = [h for h in self.battle_start_party[:4] if 0 < h <= 9999]
            got = [n for n, hp in state.char_hp.items() if n != "guest" and hp in slots
                   and state.exp.get(n, 1) > 0]               # not joined yet: leftovers, not the party
            if got and len(got) <= 4:
                if got != self.last_party:
                    self._event("party in this fight: " + ", ".join(n.capitalize() for n in got))
                self.last_party = got
                self.party_seen |= set(got)
        return list(self.last_party)

    def _lv5_badges(self, boss: dict, state: GameState) -> list[tuple[str, str]]:
        """Bosses with LV5 Death kill anyone whose level is a multiple of 5: the party's
        levels (everyone's, if the party can't be told)."""
        who = boss.get("lv5")
        if not who:
            return []
        party = self._party_now(state)
        if who is True:                               # one tick in the editor: the party
            who = party or [n for n in state.levels if n != "guest"]
        elif party:
            who = [n for n in who if n in party] or party
        known = {n: state.levels[n] for n in who if 0 < state.levels.get(n, 0) < 100}
        if not known:
            return [("LV5 Death: no level a multiple of 5", "info")]
        bad = [f"{n.capitalize()} Lv {l}" for n, l in known.items() if l % 5 == 0]
        if bad:
            return [("LV5 Death danger: " + ", ".join(bad), "warn")]
        return [("Levels safe from LV5 Death", "good")]

    # -- steals (seen on screen by screen.py) -------------------------------
    def _steal_lines(self) -> list[tuple[int, str | None]]:
        s = self.current()
        if s["type"] != "boss":
            return []
        return steal_targets(self.bosses.get(s["boss"], {}).get("turns", []))

    def _steals_done(self) -> list[int]:
        """Steal lines on this card already confirmed. A named item ticks its own line;
        a line without an item ("then steal") ticks on anything stolen."""
        got = list(self.stolen.get(self.step, []))
        done = []
        targets = self._steal_lines()
        for i, item in targets:
            if item and item in got:
                got.remove(item)
                done.append(i)
        for i, item in targets:
            if not item and got:
                got.pop(0)
                done.append(i)
        return sorted(done)

    def _bag_steals(self, state: GameState) -> None:
        """Steals from the bag: an item turning up in it mid-fight was stolen (spoils only
        come once the enemies are down, and those reads are skipped)."""
        prev, self._steal_prev = self._steal_prev, (dict(state.items) if state.items is not None else None)
        if prev is None or state.items is None:
            return
        if not (self.in_battle and self.battle_is_boss) or self.battle_cleared or self.cleared_since is not None:
            return
        gained = {i: n - prev.get(i, 0) for i, n in state.items.items() if n > prev.get(i, 0)}
        if len(gained) > 2:
            return                      # the whole bag changing at once: a load, not a steal
        from screen import ITEMS
        for i, d in gained.items():
            for _ in range(d):
                if i < len(ITEMS):
                    self.steal_event("stole", ITEMS[i], source="bag")

    def wants_steal_check(self) -> bool:
        """True while a boss fight is on with a steal still to confirm."""
        if not (self.in_battle and self.battle_is_boss):
            return False
        if self._steal_prev is not None:
            return False                # read from the bag instead
        return len(self._steals_done()) < len(self._steal_lines())

    def steal_event(self, kind: str, item: str | None, source: str = "screen") -> None:
        """What screen.py read: ('stole', item) or ('failed', None), or a steal seen in the
        bag. With the bag set up, only the bag counts."""
        if source == "screen" and self._steal_prev is not None:
            return
        if not (self.in_battle and self.battle_is_boss) or not self._steal_lines():
            return
        if kind == "failed":
            return                      # a missed steal isn't shown: just steal again
        if not item:
            return
        before = len(self._steals_done())
        self.stolen.setdefault(self.step, []).append(item)
        self.got_log.append((self._igt_last or 0, "stolen", self.step, item))
        if len(self._steals_done()) > before:
            self.steal_note = (f"Stole {item} \u2713", "good")
        else:
            wanted = [it for i, it in self._steal_lines() if it and i not in self._steals_done()]
            self.steal_note = (f"Stole {item}" + (f", still need {wanted[0]}" if wanted else ""), "warn")

    # -- pickups (seen on screen by screen.py) ------------------------------
    def _pickups_done(self, i: int) -> list[int]:
        """Pickup lines on walk step i whose items have all been seen (named items
        first, in order; then lines that take any item)."""
        from collections import Counter
        s = self.steps[i]
        if s["type"] == "boss":
            return []
        got = Counter(self.picked.get(i, []))
        targets = pickup_targets(_pickup_lines(s))
        done = []
        for line, items in targets:
            need = Counter(x for x in items if x != "*")
            if need and all(got[k] >= n for k, n in need.items()):
                got -= need
                if "*" not in items:
                    done.append(line)
                else:
                    targets.append((line, ["*"] * items.count("*")))   # its "any item" part, later
        for line, items in targets:
            if line in done or any(x != "*" for x in items):
                continue
            left = sum(got.values())
            if left >= len(items):
                for k in list(got):
                    take = min(got[k], len(items))
                    got[k] -= take
                    items = items[take:]
                    if not items:
                        break
                done.append(line)
        return sorted(set(done))

    def _accepts(self, i: int, item: str) -> bool:
        """Whether walk step i is still waiting for this item (or for any item)."""
        from collections import Counter
        targets = pickup_targets(_pickup_lines(self.steps[i]))
        need = Counter(x for _, items in targets for x in items)
        got = Counter(self.picked.get(i, []))
        if need[item] > got[item]:
            return True
        extra = sum(n for k, n in got.items() if k not in need or n > need[k])
        return need["*"] > extra

    def _pickup_window(self) -> list[int]:
        """The step on screen, then the next two walk or menu steps (up to a fight step):
        picking up an item a later step names means you're there. Not from a step that
        shops (a bought Tent isn't the next step's Tent chest)."""
        s = self.current()
        out = []
        if s["type"] != "boss" and \
                len(self._pickups_done(self.step)) < len(pickup_targets(_pickup_lines(s))):
            out.append(self.step)
        if s["type"] == "boss" or any(shop_check(l) or sell_except(l) for l in check_lines(s)):
            return out
        i = self.step
        for _ in range(PICKUP_LOOKAHEAD):
            i = self._next_index(i)
            if i <= self.step or self.steps[i]["type"] == "boss":
                break
            if len(self._pickups_done(i)) < len(pickup_targets(_pickup_lines(self.steps[i]))):
                out.append(i)
        return out

    def wants_pickup_check(self) -> bool:
        """True on the field while a nearby walk step still has something to pick up."""
        if self.in_battle or not self._pickup_window():
            return False
        if self._bag_prev is not None and self._gil_prev is not None:
            # Items and gil come from memory; only key items (not in the bag) need the screen.
            from screen import ITEMS
            want = {x for _, items in pickup_targets(_pickup_lines(self.current())) for x in items}
            return any(x not in ITEMS and x not in ("Gil", "*") for x in want)
        return True

    def pickup_event(self, item: str, source: str = "screen") -> None:
        """screen.py read "Received <item>!" on the field, or the item turned up in the bag.
        With the bag set up, items are taken from it only (the screen still does gil)."""
        if self.in_battle:
            return
        from screen import ITEMS
        in_bag = item in ITEMS                 # key items (Beautiful Potion, ...) aren't in the bag
        if source == "screen" and (self._gil_prev is not None if item == "Gil"
                                   else (self._bag_prev is not None and in_bag)):
            return
        window = self._pickup_window()
        # A step that names this item first, then one that takes any item.
        from collections import Counter
        def names(i):
            return Counter(x for _, items in pickup_targets(_pickup_lines(self.steps[i])) for x in items)
        target = next((i for i in window if names(i)[item] > self.picked.get(i, []).count(item)
                       and (i == self.step or item != "Gil")), None)
        if target is None:
            # "any item" lines: only the step on screen (a later step needs the item named)
            target = next((i for i in window if i == self.step and self._accepts(i, item)), None)
        if target is None:
            return
        self.picked.setdefault(target, []).append(item)
        self.got_log.append((self._igt_last or 0, "picked", target, item))
        if target != self.step:
            self._goto(target, manual=False, why=f"picked up {item}")  # picked up something from a later step: you're there
            self._entry_step, self._entry_done = target, 0  # that pickup counts as done there
        # Moving on once everything on the step is done happens in _track_menu.

    # -- menus (equipment and abilities read from memory) --------------------
    def _menu_line_state(self, line: str, state: GameState) -> bool | None:
        """True/False: done or not. None: nothing on the line the coach can check."""
        cc = config_check(line)
        if cc is not None:
            setting, want = cc
            values = state.config_values.get(setting, {})
            key = next((k for k in values if k.lower() == want.lower()), None)
            if setting not in state.config or key is None:
                return None                            # not set up for this setting / value
            return state.config[setting] == values[key]
        rc = row_check(line)
        if rc is not None:
            who, want = rc
            who = [_record_of(w) for w in who]
            known = [w for w in who if w in state.rows]
            if not known:
                return None                            # Beatrix etc., or rows not set up
            return all(state.rows[w] == want for w in known)
        opt = _OPTIMIZE_LINE.match(line.strip())
        if opt and opt.group(1).lower() in state.equips and self.equip_step == self.step:
            who = opt.group(1).lower()                 # "Freya: Optimize": her equipment changed
            return state.equips[who] != self.equip_base.get(who)
        keep = sell_except(line)
        if keep is not None:
            # "Sell all except ...": done once the bag holds nothing else
            if state.items is None:
                return None
            from screen import ITEMS
            keep_ids = {ITEMS.index(k) for k in keep if k in ITEMS}
            return all(n <= 0 or i in keep_ids or i >= len(ITEMS) for i, n in state.items.items())
        c = menu_checks(line)
        if c is None:
            return self._shop_line_state(line, state)
        who, _, eq, ab = c
        who = _record_of(who)
        checks = []
        if eq and who in state.equips:
            from screen import ITEMS
            on = state.equips[who]
            for n, off in eq:
                if n.startswith("slot:"):              # "-Armor": nothing in that slot
                    k = int(n[5:])
                    checks.append(k < len(on) and on[k] == EMPTY_SLOT)
                else:
                    checks.append((ITEMS.index(n) in on) != off)
        ab = [(n, off) for n, off in ab if n in state.ability_known]
        if ab and who in state.abilities:
            checks += [(n in state.abilities[who]) != off for n, off in ab]
        return all(checks) if checks else None

    def _menu_progress(self, state: GameState) -> tuple[list[int], int | None, int]:
        """(lines done, first line still to do, how many lines can be checked)."""
        s = self.current()
        if s["type"] == "boss":
            return [], None, 0
        done, todo, n = [], None, 0
        if self.tick_step != self.step:
            self.tick_step, self.shop_ticked = self.step, set()
        picked = set(self._pickups_done(self.step))
        pick_lines = {i for i, _ in pickup_targets(_pickup_lines(s))}
        for i, line in enumerate(check_lines(s)):
            if i in pick_lines:
                st = i in picked
            elif i in self.shop_ticked:
                st = True
            else:
                st = self._menu_line_state(line, state)
                if st and (shop_check(line) or sell_except(line) or _OPTIMIZE_LINE.match(line.strip())):
                    self.shop_ticked.add(i)            # stays ticked
            if st is None:
                continue
            n += 1
            if st:
                done.append(i)
            elif todo is None and not is_optional(line):
                todo = i                               # "(if stolen)" lines don't hold the step up
        return done, todo, n

    def _enter_called_sub(self, state: GameState) -> None:
        """A walk or menu sub-step the step calls ("[Side room]") comes up by itself once
        every line written above the call is done. (Fight sub-steps come up with their fight.)"""
        from syntax import substep_call
        s = self.current()
        subs = {(self.steps[j].get("title") or "").strip().lower(): j for j in self._pending_inside(self.step)}
        lines = check_lines(s)
        for k, line in enumerate(step_lines(s)):
            name = substep_call(line)
            j = subs.get(name.lower()) if name else None
            if j is None:
                continue
            if self.steps[j]["type"] == "boss":
                return                                 # its fight brings it up
            done = set(self._menu_progress(state)[0])
            for i in range(k):
                if is_optional(lines[i]) or i in done:
                    continue
                if self._menu_line_state(lines[i], state) is not None or \
                        i in {x for x, _ in pickup_targets(_pickup_lines(s))}:
                    return                             # a line above it still to do
            self._goto(j, manual=False, why=f"lines before [{self.steps[j].get('title')}] done")
            return

    def _has_sell_lines(self) -> bool:
        """Selling adds gil too, so gil pickups don't count on a step that sells."""
        return any((shop_check(l) or ("",))[0] == "sell" or sell_except(l) for l in check_lines(self.current()))

    def _bag_pickups(self, state: GameState, now: float) -> None:
        """Pickups from the bag: an item this step wants turning up in it (a chest, a
        hidden item) counts, read straight from memory, so it can't be missed on screen.
        Not straight after a fight (spoils) or with the menu open (synthesising)."""
        gil_prev, self._gil_prev = self._gil_prev, state.gil
        if (gil_prev is not None and state.gil is not None and not self.in_battle
                and now - self._battle_end_at >= BAG_AFTER_FIGHT and not state.menu_vote
                and not self._has_sell_lines() and 0 < state.gil - gil_prev < GIL_PICKUP_MAX):
            self.pickup_event("Gil", source="bag")     # walk steps only: shops sell for gil
        prev, self._bag_prev = self._bag_prev, (dict(state.items) if state.items is not None else None)
        if prev is None or state.items is None or self.in_battle:
            return
        if now - self._battle_end_at < BAG_AFTER_FIGHT or state.menu_vote:
            return
        from screen import ITEMS
        gained = {i: n - prev.get(i, 0) for i, n in state.items.items() if n > prev.get(i, 0)}
        if len(gained) > 2:
            return                      # the whole bag changing at once: a load, not a pickup
        for i, d in gained.items():
            for _ in range(d):
                if i < len(ITEMS):
                    self.pickup_event(ITEMS[i], source="bag")

    def _track_bag(self, state: GameState) -> None:
        """Counts of what's in the bag since this step came up (for Buy/Sell lines),
        and everyone's equipment then (for Optimize lines)."""
        if self.in_battle:
            return
        if state.equips and self.equip_step != self.step:
            self.equip_step = self.step
            self.equip_base = {k: list(v) for k, v in state.equips.items()}
        if state.items is None:
            return
        if self.shop_step != self.step:
            self.shop_step, self.shop_base = self.step, dict(state.items)
            self.shop_peak = dict(state.items)
            return
        for i, n in state.items.items():
            if n > self.shop_peak.get(i, 0):
                self.shop_peak[i] = n

    def _shop_line_state(self, line: str, state: GameState) -> bool | None:
        """Buy/Sell lines, checked against the bag. None if the line can't be checked."""
        c = shop_check(line)
        if c is None or state.items is None or self.shop_step != self.step:
            return None
        from screen import ITEMS
        kind, parts = c
        ok = []
        for name, n, rule in parts:
            i = ITEMS.index(name)
            now, base, peak = state.items.get(i, 0), self.shop_base.get(i, 0), self.shop_peak.get(i, 0)
            if rule == "all":
                ok.append(now == 0)
            elif rule == "some":                       # "extra Wrists": fewer than before
                ok.append(now < base or base == 0)
            elif rule == "less":                       # "Sell 2 Cotton Robe": two fewer than before
                ok.append(now <= max(0, base - n))
            elif rule == "total":                      # "(4 if you stole one)": own this many
                ok.append(peak >= n)
            else:
                ok.append(peak - base >= n)
        return all(ok)

    def _track_menu(self, state: GameState) -> None:
        """Move on from a walk or menu step once every line the coach can check is done:
        after the menu is closed again if there's equipment, abilities or rows on it,
        straight away if it's only pickups and shopping."""
        s = self.current()
        if self.in_battle or s["type"] == "boss":
            self.menu_opened_on = None
            return
        # Only something done while on this step moves it on: a step that was already
        # finished when you came back to it (F7, or a savestate from before) stays put.
        if self._entry_step != self.step:
            self._entry_step = self.step
            # Back from a fight in the middle of this step: what was done before it counts.
            self._entry_done = 0 if self._back_to_parent == self.step else len(self._menu_progress(state)[0])
            self._back_to_parent = None
            return
        if self._pending_inside(self.step):
            self._enter_called_sub(state)              # a "[Side room]" whose turn has come
            return                                     # a sub-step in the middle of it still to come
        if not any(menu_checks(l) or _OPTIMIZE_LINE.match(l.strip()) or row_check(l) or config_check(l)
                   for l in check_lines(s)):
            done, todo, n = self._menu_progress(state)
            if n and todo is None and len(done) > self._entry_done and self.step + 1 < len(self.steps) \
                    and not (s["type"] == "move" and instructions_after_checks(step_lines(s))):
                # (more to do written after the last ticked line: the next screen or F8 moves on)
                self._step_done("all lines done")
            return
        if state.menu_vote is None:
            return
        if state.menu_vote:
            self.menu_opened_on = self.step
            return
        if self.menu_opened_on == self.step:
            done, todo, n = self._menu_progress(state)
            self.menu_opened_on = None
            if n and todo is None and len(done) > self._entry_done and self.step + 1 < len(self.steps) \
                    and not (s["type"] == "move" and instructions_after_checks(step_lines(s))):
                # (more to do written after the last ticked line: the next screen or F8 moves on)
                self._step_done("all lines done, menu closed")

    def _fight_known(self, i: int) -> bool:
        """Whether fight step i can be recognised: typed HP or battle number, or learned."""
        b = self.bosses.get(self.steps[i]["boss"], {})
        return bool(b.get("hp") or b.get("battle_id") is not None or self._learned_form(i)
                    or any(self.learned.fingerprint(k) or self.learned.battle_id(k) is not None
                           for k in self._keys(i)))

    def _next_preview(self) -> dict:
        """The step after this one, for under the panel (title, kind and its lines)."""
        if not self.show_next:
            return {}
        if self.is_inside(self.step) and self.parent_of(self.step) is not None:
            j = self.parent_of(self.step)                # a fight inside a step: back to that step
        else:
            j = self._next_index(self.step)
        if j == self.step or j >= len(self.steps):
            return {}
        s = self.steps[j]
        if s["type"] == "boss":
            boss = self.bosses.get(s["boss"], {})
            title = boss.get("name", s["boss"])
            lines = boss.get("turns", [])
            if not self._fight_known(j):
                title = FLAG + title
        else:
            title = s.get("title") or ("Menu" if s["type"] == "menu" else "Next")
            lines = step_lines(s)
        lines = [l for l in lines if not l.strip().startswith("@")]
        return {"next_title": title, "next_kind": s["type"], "next_lines": lines}

    def _step_done(self, why: str) -> None:
        """A walk or menu step finished by itself: on to the next step, or for a sub-step
        (a step in the middle of the one above), back to that step."""
        if self.is_inside(self.step) and self.parent_of(self.step) is not None:
            parent = self.parent_of(self.step)
            self.inside_done.add(self.step)
            self._goto(parent, manual=False, why=why + ", back to its step")
            self._back_to_parent = parent
            return
        self._goto(self._next_index(self.step), manual=False, why=why)

    def _needs_f8(self, state: GameState) -> bool:
        """A walk or menu step the coach can't move on from by itself yet: nothing on it it
        can check (that it waits for), the next steps' screens not learned, and no known
        fight straight after. F8 there is expected, and teaches it."""
        s = self.current()
        if s["type"] == "boss" or self.step + 1 >= len(self.steps):
            return False
        pick = {i for i, _ in pickup_targets(_pickup_lines(s))}
        for i, line in enumerate(check_lines(s)):
            if is_optional(line) or (s["type"] == "move" and instructions_after_checks(step_lines(s))):
                continue
            if i in pick or self._menu_line_state(line, state) is not None:
                return False
        i = self.step
        for _ in range(FIELD_LOOKAHEAD):
            j = self._step_by(i, 1)
            if j == i:
                break
            if self.learned.field(self.step_key(self.steps[j]), self.rname) not in (None,) + NO_SCREEN:
                return False
            i = j
        nxt = self.steps[self._next_index(self.step)]
        if nxt["type"] == "boss" and (self.bosses.get(nxt["boss"], {}).get("hp") or self._learned_form(self._next_index(self.step))):
            return False
        return True

    def _view(self, state: GameState) -> View:
        """What the panel shows. Random encounters get no screen of their own:
        the current step stays up until a boss fight is recognised."""
        s = self.current()
        header = f"Disc {s.get('disc', 1)}  ·  {step_numbers(self.steps)[self.step]}/{sum(1 for k, t in enumerate(self.steps) if not t.get('alt') and not self.is_inside(k))}  ·  {len(self.encounters)} encounter{'' if len(self.encounters) == 1 else 's'}"
        badges = self._status_badges(state)
        fighting = self.in_battle and self.identified
        boss_fight = fighting and self.battle_is_boss

        if s["type"] == "boss":
            boss = self.bosses.get(s["boss"], {"name": s["boss"], "turns": []})
            turns = boss.get("turns", [])
            groups = turn_groups(turns)                # an action and the indented lines under it
            hl = hl_end = None                         # fight steps stay still too: no highlight
            badges += self._condition_badges(boss.get("needs", []), state)
            badges += self._lv5_badges(boss, state)
            if self.steal_off and self._steal_prev is None and self._steal_lines():
                badges.append((self.steal_off, "info"))   # steals can't tick: neither bag nor screen
            # Steals show only as a check mark on their line (no notes).
            if fighting:
                badges += self._fight_readout(state)
            n = self._fights(self.step)
            title = boss["name"] + (f"  ·  fight {min(self.sub + 1, n)} of {n}" if n > 1 else "")
            stats = []
            for k, g in enumerate(self._groups(self.step)):        # soldiers 2/4 · mages 1/2
                c = max(1, int(g.get("count") or 1))
                d = min(c, self.groups_done.get(k, 0))
                stats.append(("\u2713 " if d >= c else "") + f"{g.get('name') or 'Fight'} {d}/{c}")
            if not self._fight_known(self.step):
                title = FLAG + title                   # can't recognise this fight yet: F8 during it teaches it
            if self.is_inside(self.step) and self.parent_of(self.step) is not None:
                title += f"  ·  in {self.steps[self.parent_of(self.step)].get('title') or 'the step above'}"
            turns, ap_done, where = self._ap_lines(turns, state)
            return View(header=header, title=title,
                        lines=turns, highlight=hl, highlight_end=hl_end, badges=badges,
                        footer=boss.get("if_wrong", "") if boss_fight else "",
                        kind="boss", stats=stats, done=[where[i] for i in self._steals_done()] + ap_done,
                        steals=[where[i] for i, _ in self._steal_lines()], **self._next_preview())

        if fighting and not self.won_early:     # a random encounter: the step stays up, with live HP
            badges += self._fight_readout(state)
        done, _, _ = self._menu_progress(state)
        hl = None                       # walk and menu steps stay still: only check marks change
        lines, ap_done, where = self._ap_lines(step_lines(s), state)
        done = [where[i] for i in done if i < len(where)] + ap_done
        from syntax import substep_call
        subs = {(self.steps[j].get("title") or "").strip().lower(): j for j in self.inside_of(self.step)}
        every, j = set(), self.step + 1                # all its sub-steps, on this run's way or not
        while self.is_inside(j):
            every.add((self.steps[j].get("title") or "").strip().lower())
            j += 1
        off = [k for k, line in enumerate(lines)
               if substep_call(line) and substep_call(line).lower() in every - set(subs)]
        if off:                                        # a sub-step not needed this run: its line goes
            keep = [k for k in range(len(lines)) if k not in off]
            remap = {old: new for new, old in enumerate(keep)}
            lines = [lines[k] for k in keep]
            done = [remap[d] for d in done if d in remap]
        called = set()
        for k, line in enumerate(lines):              # "[Baku]": the sub-step, where the step calls it
            name = substep_call(line)
            j = subs.get(name.lower()) if name else None
            if j is None:
                continue
            called.add(j)
            indent = line[:len(line) - len(line.lstrip())]
            lines[k] = f"{indent}\u21b3 {self.steps[j].get('title')}"
            if j in self.inside_done and k not in done:
                done.append(k)
        for j in self.inside_of(self.step):           # sub-steps it doesn't call: listed at the end
            if j in called:
                continue
            name = self.steps[j].get("title") or "fight"
            lines.append(f"# \u2713 {name} done" if j in self.inside_done else f"# On the way: {name}")
        title = s.get("title") or ("Menu" if s["type"] == "menu" else "Next")
        if self.is_inside(self.step) and self.parent_of(self.step) is not None:
            title += f"  ·  in {self.steps[self.parent_of(self.step)].get('title') or 'the step above'}"
        if self._needs_f8(state):
            title = FLAG + title                       # it can't move on from this step by itself yet
        return View(header=header, title=title,
                    lines=lines, highlight=hl, badges=badges, footer=s.get("backup", ""),
                    kind=s["type"], stats=self._stats(state), done=done, **self._next_preview())


def _pickup_lines(s: dict) -> list[str]:
    """Every line of a walk or menu step can be a pickup ("1x Tent (...)")."""
    return check_lines(s) if s.get("type") != "boss" else []


def check_lines(s: dict) -> list[str]:
    """A step's lines as the checks read them (indented lines under "Buy" are buys)."""
    return expand_headers(step_lines(s))


EMPTY_SLOT = 255          # item number of an empty equipment slot
GUEST_NAMES = ("blank", "marcus", "beatrix", "cinna")


# Guests live in the records of characters who haven't joined yet (seen in a live game:
# Blank's Iron Sword and Silk Shirt in Amarant's record, Marcus's Broadsword in Eiko's).
# Beatrix has the record after Amarant's.
GUEST_RECORD = {"blank": "amarant", "marcus": "eiko", "cinna": "quina", "beatrix": "guest"}


def _record_of(who: str) -> str:
    """Which record a character's equipment, abilities and row are read from."""
    return GUEST_RECORD.get(who, who)


def step_lines(s: dict) -> list[str]:
    """A step's instructions, one per line ("lines"), or its single "text"."""
    if s.get("lines"):
        return list(s["lines"])
    return [s.get("text", "")]


def _step_moves(old: list[dict], new: list[dict]) -> dict[int, int | None]:
    """Where each step of a route is after an edit (old number -> new number, None if it's
    gone), found by the step's identity. Steps that look alike go to the nearest one."""
    at: dict[str, list[int]] = {}
    for j, s in enumerate(new):
        at.setdefault(Coach.step_key(s), []).append(j)
    out: dict[int, int | None] = {}
    taken: set[int] = set()
    for i, s in enumerate(old):
        free = [j for j in at.get(Coach.step_key(s), []) if j not in taken]
        out[i] = min(free, key=lambda j: abs(j - i)) if free else None
        if out[i] is not None:
            taken.add(out[i])
    return out


def _bars_filling(old: list[int], new: list[int]) -> bool:
    """Real ATB bars climb in small steps; random memory churn doesn't."""
    return any(0 < n - o <= MAX_ATB_STEP for o, n in zip(old, new))


def _fmt_igt(frames: int) -> str:
    """FFIX's in-game timer counts frames; shown as h:mm:ss assuming 60 fps."""
    secs = frames // 60
    return f"IGT {secs // 3600}:{secs // 60 % 60:02d}:{secs % 60:02d}"


def load_route(path: Path) -> dict:
    """A route file (fight steps written like other steps, or the older separate cards)."""
    from routes import expand
    route = expand(json.loads(path.read_text(encoding="utf-8")))
    route["name"] = path.stem          # the route's identity: its file (progress, learned screens, run log)
    if not route.get("steps"):
        raise ValueError("the route has no steps: add one in the editor")
    missing = [s["boss"] for s in route["steps"]
               if s["type"] == "boss" and s["boss"] not in route["bosses"]]
    if missing:
        raise ValueError(f"route steps reference unknown fights: {missing}")
    return route
