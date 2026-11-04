"""Test a route without the game: play it in a simulated FFIX the way a perfect run would
(fights from their rooms, steals that work, pickups, shops and menus done in order),
pressing no keys, and report every place it didn't move on by itself or moved on too
early. The editor's Test route button runs this.

    python route_test.py "MPU Route"
"""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coach import Coach, step_numbers  # noqa: E402
from memory import Addresses, FakeRam, fill_abilities, read_state  # noqa: E402
from items import ITEMS  # noqa: E402
from steps import _record_of, check_lines  # noqa: E402
from syntax import (_OPTIMIZE_LINE, config_check, menu_checks, pickup_items, row_check,  # noqa: E402
                    sell_except, shop_check, steal_targets, substep_call)

CHARS = ["zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant", "guest"]
_OPT_AND = re.compile(r"^(zidane|vivi|garnet|steiner|freya|quina|eiko|amarant):\s*optimi[sz]e\b", re.I)
FIELD_CODE, BATTLE_CODE = b"\x11" * 64, b"\x22" * 64
FLAGS, MENU, FIELD = 0x1F0100, 0x1F0300, 0x1F0010


def slot_of(item_id: int) -> int:
    """Which equipment slot an item goes in, by its number (weapon, head, arm, body, accessory)."""
    return 0 if item_id < 88 else 2 if item_id < 112 else 1 if item_id < 148 else 3 if item_id < 192 else 4


class SimGame:
    """A simulated FFIX: screens, fights, the bag, equipment, abilities, rows and the menu."""

    def __init__(self, route: dict, learned_path: Path):
        self.ram, self.a = FakeRam(), Addresses()
        a = self.a
        a.battle_flags = [(FLAGS + i, 0x10, 0x20) for i in range(3)]
        a.field_id = FIELD
        a.chars = {n: {"exp": 0x1E1000 + i * 0x90} for i, n in enumerate(CHARS)}
        a.equip_off = 0x30
        a.ability_bits = fill_abilities({})
        a.menu_flags = [(MENU + i, 0, 1) for i in range(3)]
        a.row_off, a.row_bit, a.row_back = 0x20, 7, 1
        a.inventory = 0x1E0000
        a.gil = 0x1E0800
        from items import KEY_ITEM_IDS
        a.key_items_at, a.key_item_ids = 0x1E0200, {k: list(v) for k, v in KEY_ITEM_IDS.items()}
        self.now = 0.0
        self.coach = Coach(route, learned_path=learned_path, party_slots=a.party_slots)
        self.coach._enc_flag_seen.add(True)                # the random-battle flag works here
        self.code(False)
        self.ram.write(a.encounter_field, 1, 1)
        self.ram.write(a.party_hp_addrs[0], 500)           # Zidane is alive

    def code(self, battle: bool):
        b = self.a.battle_module
        self.ram.mem[b:b + 64] = BATTLE_CODE if battle else FIELD_CODE
        for i in range(3):
            self.ram.mem[FLAGS + i] = 0x20 if battle else 0x10

    def read(self, n=1):
        for _ in range(n):
            self.coach.update(read_state(self.ram, self.a), self.now)
            self.now += 0.1

    def screen(self, field_id: int, seconds=1.0):
        self.ram.write(self.a.field_id, field_id)
        self.read(int(seconds * 10))


class Play:
    def __init__(self, route: dict, learned_from: Path | None = None):
        self.learned = Path(tempfile.mkdtemp()) / "learned.json"
        if learned_from is not None and Path(learned_from).exists():
            self.learned.write_bytes(Path(learned_from).read_bytes())   # a copy: never written back
        g = self.g = SimGame(route, self.learned)
        self.gil = 500
        self.bag: dict[str, int] = {"Potion": 10}
        self.equips = {n: [255] * 5 for n in CHARS}
        for k, name in enumerate(["Dagger", "Leather Hat", "Wrist", "Leather Shirt"]):
            self.equips["zidane"][k] = ITEMS.index(name)
        self.write_all()
        self.c = g.coach
        self.n = step_numbers(self.c.steps)
        self.found: list[tuple[int, str]] = []         # (step, what went wrong there)
        self.trace: list[str] = []
        self.stuck = False
        self.field = 1
        real_goto = self.c._goto

        def goto(index, manual, why=""):
            # Leaving a walk/menu step for a later one, by a screen, with lines still to do
            c = self.c
            if (not manual and index > c.step and why.startswith("reached")
                    and c.steps[c.step]["type"] != "boss"):
                todo = c._menu_progress(read_state(g.ram, g.a))[1]
                if todo is not None and index in c.inside_of(c.step):
                    # Into one of its own sub-steps: fine once the lines above its call are done
                    want = (c.steps[index].get("title") or "").strip().lower()
                    calls = [k for k, x in enumerate(check_lines(c.current()))
                             if (substep_call(x) or "").lower() == want]
                    if calls and todo >= calls[0]:
                        todo = None
                if todo is not None:
                    self.found.append((c.step, f"moved on to {self.label(index)} early, on reaching its "
                                                f"start screen, with \"{check_lines(c.current())[todo].strip()}\" "
                                                f"still to do: is that screen also passed on the way?"))
            real_goto(index, manual, why)
        self.c._goto = goto

    # -- the simulated game ----------------------------------------------------
    def write_all(self):
        g = self.g
        for k in range(256):
            g.ram.write(g.a.inventory + 2 * k, 255, 1)
            g.ram.write(g.a.inventory + 2 * k + 1, 0, 1)
        for k, (name, n) in enumerate((x for x in self.bag.items() if x[1] > 0)):
            g.ram.write(g.a.inventory + 2 * k, ITEMS.index(name), 1)
            g.ram.write(g.a.inventory + 2 * k + 1, min(99, n), 1)
        for who, gear in self.equips.items():
            for k, v in enumerate(gear):
                g.ram.write(g.a.chars[who]["exp"] + 0x30 + k, v, 1)
        g.ram.write(g.a.gil, self.gil, 4)

    def menu(self, open_: bool):
        for i in range(3):
            self.g.ram.mem[MENU + i] = 1 if open_ else 0
        self.g.read(3)

    def screen(self, f: int, seconds=1.5):
        self.field = f
        self.g.screen(f, seconds=seconds)

    def read(self, n=5):
        self.g.read(n)

    def give(self, name: str, n: int = 1):
        if name == "Gil":
            self.gil += 100
        elif name in ITEMS:
            self.bag[name] = self.bag.get(name, 0) + n
        self.write_all()

    # -- doing a line ----------------------------------------------------------
    def do_line(self, lines: list[str], k: int) -> str | None:
        """Do line k. Returns what kind of thing it was ("shop", "pickup", "menu"), or None."""
        line = lines[k]
        sc = shop_check(line)
        if sc:
            kind, parts = sc
            for name, n, rule in parts:
                if kind in ("buy", "synth"):
                    self.give(name, max(1, n))
                elif kind == "sell":
                    have = self.bag.get(name, 0)
                    self.bag[name] = 0 if rule == "all" else max(0, have - max(1, n))
            self.write_all()
            return "shop"
        if sell_except(line) is not None:
            keep = set(sell_except(line))
            self.bag = {k2: v for k2, v in self.bag.items() if k2 in keep}
            self.write_all()
            return "shop"
        items = pickup_items(line)
        if items:
            m2 = re.match(r"\s*(\d+)\s*x", line)
            count = int(m2.group(1)) if m2 and "gil" not in line.lower() else 1
            for name in items:
                if name == "*" or name.lower() in ("any", "any item", "hidden"):
                    for _ in range(count):
                        self.give("Potion")
                        self.read(2)
                elif name in ITEMS or name == "Gil":
                    self.give(name)
                elif name in self.g.a.key_item_ids:              # a key item: its bit in the key item block
                    n = self.g.a.key_item_ids[name][0]
                    self.g.ram.mem[self.g.a.key_items_at + (n >> 3)] |= 1 << (n & 7)
            return "pickup"
        rc = row_check(line)
        if rc:
            who, want = rc
            for w in who:
                w = _record_of("garnet" if w == "dagger" else w)
                if w in self.g.a.chars:
                    a = self.g.a.chars[w]["exp"] + 0x20
                    v = self.g.ram.mem[a]
                    self.g.ram.mem[a] = (v | 0x80) if want == "back" else (v & 0x7F)
            return "menu"
        m = _OPTIMIZE_LINE.match(line.strip()) or _OPT_AND.match(line.strip())
        if m:
            who = m.group(1).lower()
            note = next((x.strip() for x in lines[k + 1:] if x.strip()), "")
            gear = list(self.equips[who])
            if note.startswith("#") and "/" in note:
                for part in note.lstrip("#").split("/"):
                    name = part.split(" or ")[0].strip()
                    if name in ITEMS:
                        gear[slot_of(ITEMS.index(name))] = ITEMS.index(name)
            else:
                gear[0] = (gear[0] + 1) % 80 if gear[0] != 255 else 2
            self.equips[who] = gear
            self.write_all()
        mc = menu_checks(line)
        if mc:
            who, off_all, eq, ab = mc
            who = _record_of({"dagger": "garnet"}.get(who, who))
            if who in self.equips:
                for name, off in eq:
                    if name.startswith("slot:"):
                        self.equips[who][int(name[5:])] = 255
                        continue
                    i = ITEMS.index(name)
                    if off or off_all:
                        self.equips[who] = [255 if x == i else x for x in self.equips[who]]
                    else:
                        self.equips[who][slot_of(i)] = i
                for name, off in ab:
                    if name in self.g.a.ability_bits:
                        o, bit = self.g.a.ability_bits[name]
                        a = self.g.a.chars[who]["exp"] + o
                        v = self.g.ram.u8(a)
                        self.g.ram.write(a, v & ~(1 << bit) if (off or off_all) else v | (1 << bit), 1)
                self.write_all()
            return "menu"
        if m or config_check(line):
            return "menu"
        return None

    # -- a fight ----------------------------------------------------------------
    def fight(self, i: int):
        c, g = self.c, self.g
        card = c.bosses.get(c.steps[i]["boss"], {})
        f = next(iter(c._fight_screens(i)), self.field)
        groups = card.get("groups") or []
        hps = list(groups[0]["hp"]) if groups else list(card.get("hp") or [])
        if not hps:
            forms = c.learned.formations(c._keys(i)[0])
            hps = forms[0] if forms else [500]
        steals = [it for _, it in steal_targets(card.get("turns") or []) if it]
        self.screen(f, seconds=1)
        g.code(True)
        for k, h in enumerate(hps[:4]):
            g.ram.write(g.a.enemy_hp[k], h)
        for k in range(30):
            g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
            g.ram.write(g.a.party_atb[0], 100 + k * 40)
            g.read()
            if k == 15:
                for it in steals:
                    self.give(it)                      # the steal works
        for k in range(len(hps[:4])):
            g.ram.write(g.a.enemy_hp[k], 0)
        g.read(15)
        g.code(False)
        g.read(15)
        g.now += 9                                     # walking on after the fight

    # -- the run ------------------------------------------------------------------
    def label(self, i=None):
        i = self.c.step if i is None else i
        return f"{self.n[i]} {self.c.steps[i].get('title') or ''}".strip()

    def stall(self, why: str):
        c = self.c
        st = read_state(self.g.ram, self.g.a)
        done = set(c._menu_progress(st)[0])
        left = [line.strip() for k, line in enumerate(check_lines(c.current()))
                if k not in done and c._menu_line_state(line, st) is not None]
        if left:
            why += f" (\"{left[0]}\" never ticked)"
        self.found.append((c.step, why))
        before = (c.step, c.sub)
        c.next_step()                                  # carry on, as you would with F8
        self.read(3)
        if (c.step, c.sub) == before:
            self.found.append((c.step, "F8 couldn't move on from here either: the test stops here"))
            self.stuck = True

    def run(self, limit=4000):
        c = self.c
        start = c._start_screen(0)
        if start is not None:
            self.screen(start)
        seen = None
        for _ in range(limit):
            if self.stuck:
                break
            i = c.step
            if seen != i:
                seen = i
                self.trace.append(self.label())
                self.read(3)                           # a moment on the new step before doing anything
            s = c.steps[i]
            if s["type"] == "boss":
                before = (c.step, c.sub, dict(c.groups_done))
                self.fight(i)
                if (c.step, c.sub, dict(c.groups_done)) == before:
                    self.stall("this fight was won, but the route didn't move on")
                continue
            top = c.parent_of(i) if c.is_inside(i) and c.parent_of(i) is not None else i
            if (top == i and not c._pending_inside(top)
                    and c._next_index(top, skip_pending=True) == top):
                self.trace.append("(end of the route)")
                break
            lines = check_lines(s)
            moved = menu_open = False
            for k, line in enumerate(lines):
                if not line.strip() or k in set(c._menu_progress(read_state(self.g.ram, self.g.a))[0]):
                    continue
                call = substep_call(line)
                if call:
                    pending = [j for j in c._pending_inside(i)
                               if (c.steps[j].get("title") or "").lower() == call.lower()]
                    if not pending:
                        continue
                    if menu_open:
                        self.menu(False)
                        menu_open = False
                    j = pending[0]
                    if c.steps[j]["type"] == "boss":
                        self.fight(j)
                    else:
                        self.read(4)
                        if c.step == i and c._start_screen(j) is not None:
                            self.screen(c._start_screen(j))
                        if c.step == i:
                            self.stall(f"[{call}] didn't come up by itself")
                    moved = True
                    break
                if menu_open and (pickup_items(line) or shop_check(line)) and not row_check(line):
                    self.menu(False)                   # walking on to pick it up: the menu's closed
                    menu_open = False
                kind = self.do_line(lines, k)
                if kind == "menu" and not menu_open:
                    self.menu(True)
                    menu_open = True
                    self.do_line(lines, k)
                if kind:
                    self.read(3)
                if c.step != i:
                    moved = True
                    break
            if menu_open:
                self.menu(False)
            if moved:
                continue
            self.read(6)
            if c.step != i:
                continue
            nxt = c._next_index(i)
            if s.get("end_screen") is not None:        # Finishes on: walk there
                self.screen(int(s["end_screen"]))
                if c.step != i:
                    continue
            if c.is_inside(i) and c.parent_of(i) is not None:
                self.stall("everything on this sub-step was done, but it didn't go back to its step")
                continue
            if nxt != i and c.steps[nxt]["type"] != "boss":
                if c._start_screen(nxt) is not None:
                    self.screen(c._start_screen(nxt))
                    if c.step != i:
                        continue
                    self.stall(f"everything was done, but reaching {self.label(nxt)}'s start screen didn't move on")
                else:
                    self.stall(f"everything was done, but it waits for {self.label(nxt)}'s start screen, which "
                               f"isn't known: set its Starts on in the editor, or press Shift+F6 there on a run")
            elif nxt != i:
                self.fight(nxt)
                if c.step == i:
                    self.stall(f"everything was done, but it didn't move on to the fight {self.label(nxt)}")
            else:
                self.stall("everything was done, but it didn't move on")
            if c.step == i:
                break
        return self.found


def test_route(route: dict, learned_from: Path | None = None) -> list[tuple[int, str]]:
    """Play `route` (as saved: not yet expanded) through; (step, what went wrong) for each
    place it didn't move on by itself or moved on early. With learned_from, what that
    learned.json taught is used (a copy); without, it's as on a new install."""
    import routes
    return Play(routes.expand(copy.deepcopy(route)), learned_from).run()


if __name__ == "__main__":
    import routes
    from paths import LEARNED
    name = sys.argv[1] if len(sys.argv) > 1 else routes.active()
    raw = json.loads(routes.path(name).read_text(encoding="utf-8-sig"))
    for title, learned in (("with what you've taught", LEARNED), ("as on a new install", None)):
        found = test_route(raw, learned)
        print(f"{name}, {title}: " + ("moves on by itself all the way" if not found else f"{len(found)} problem(s)"))
        n = step_numbers(routes.expand(copy.deepcopy(raw))["steps"])
        for i, why in found:
            print(f"   step {n[i]}: {why}")
