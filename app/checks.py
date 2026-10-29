"""Lines that tick by themselves: steals, pickups (from the bag or the screen), Buy and
Sell, equipment, abilities, rows and Config settings. A walk or menu step moves on
once all of its lines are done."""

from __future__ import annotations

from memory import GameState
from syntax import (_OPTIMIZE_LINE, config_check, instructions_after_checks, is_optional, menu_checks,
                    pickup_targets, row_check, sell_except, shop_check, steal_targets, substep_call)
from tuning import BAG_AFTER_FIGHT, EMPTY_SLOT, FIELD_LOOKAHEAD, GIL_PICKUP_MAX, NO_SCREEN, PICKUP_LOOKAHEAD
from steps import _pickup_lines, _record_of, check_lines, step_lines


class ChecksMixin:
    """Lines that tick: steals, pickups, shopping, equipment, abilities, rows, config.
    Mixed into Coach: works on its state."""

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
