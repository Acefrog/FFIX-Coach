"""Lines that tick by themselves: steals, pickups (from the bag or the screen), Buy and
Sell, equipment, abilities, rows and Config settings. A walk or menu step moves on
once all of its lines are done."""

from __future__ import annotations

import re

from memory import GameState
from syntax import (_OPTIMIZE_LINE, config_check, instructions_after_checks, is_optional, menu_checks,
                    pickup_targets, row_check, sell_except, shop_check, steal_targets, substep_call)
from tuning import BAG_AFTER_FIGHT, EMPTY_SLOT, FIELD_LOOKAHEAD, GIL_PICKUP_MAX, NO_SCREEN, PICKUP_LOOKAHEAD
from steps import _pickup_lines, _record_of, check_lines, step_lines


class ChecksMixin:
    """Lines that tick: steals, pickups, shopping, equipment, abilities, rows, config.
    Mixed into Coach: works on its state."""

    # -- steals (from the bag) ---------------------------------------------------
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
        from items import ITEMS
        for i, d in gained.items():
            for _ in range(d):
                if i < len(ITEMS):
                    self.steal_event("stole", ITEMS[i], source="bag")

    def steal_event(self, kind: str, item: str | None, source: str = "screen") -> None:
        """A steal seen in the bag: ('stole', item)."""
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

    # -- pickups (from the bag) ------------------------------------------------
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

    def pickup_event(self, item: str, source: str = "screen", named_only: bool = False) -> None:
        """An item (or gil) turned up in the bag: a pickup for the step that names it. Key items
        aren't in the bag: they tick from the key item block (_menu_progress)."""
        if self.in_battle:
            return
        from items import ITEMS
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
        if target is None and not named_only:
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
            from items import ITEMS
            keep_ids = {ITEMS.index(k) for k in keep if k in ITEMS}
            return all(n <= 0 or i in keep_ids or i >= len(ITEMS) for i, n in state.items.items())
        c = menu_checks(line)
        if c is None:
            return self._shop_line_state(line, state)
        who, _, eq, ab = c
        who = _record_of(who)
        checks = []
        if eq and who in state.equips:
            from items import ITEMS
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
            self.tick_step, self.shop_ticked, self.optimize_base = self.step, set(), {}
        picked = set(self._pickups_done(self.step))
        pick_items = dict(pickup_targets(_pickup_lines(s)))
        pick_lines = set(pick_items)
        subs = {(self.steps[j].get("title") or "").strip().lower(): j for j in self.inside_of(self.step)}
        lines = check_lines(s)
        sub_ahead = False                              # a sub-step called above this line still to do
        for i, line in enumerate(lines):
            call = substep_call(line)
            if call:                                   # "[Treno Shop]": done once that sub-step is
                j = subs.get(call.lower())
                st = None if j is None else j in self.inside_done
                sub_ahead = sub_ahead or st is False
            elif i in pick_lines:
                st = i in picked or i in self.shop_ticked
                keys = pick_items.get(i) or []
                if (not st and keys and state.key_items is not None
                        and all(k in state.key_known for k in keys) and all(k in state.key_items for k in keys)):
                    st = True                          # key items read from memory: you have it
                    self.shop_ticked.add(i)            # (stays ticked once a bell is used on its door)
            elif i in self.shop_ticked:
                st = True
            elif _OPTIMIZE_LINE.match(line.strip()):
                st = self._optimize_state(i, lines, done, state)
                if st:
                    self.shop_ticked.add(i)            # stays ticked
            elif _OPTIMIZE_AND.match(line.strip()):
                # "Zidane: Optimize, Wrist": the Wrist on (he may wear one already) AND an Optimize
                items = self._menu_line_state(line, state)
                opt = self._optimize_state(i, lines, done, state, use_note=False)
                if opt is None:                        # his gear not read for this step yet:
                    st = None if items is None else False   # not done (he may wear a Wrist already)
                else:
                    st = opt if items is None else (items and opt)
                if st:
                    self.shop_ticked.add(i)            # stays ticked
            else:
                st = self._menu_line_state(line, state)
                walk_gear = s["type"] == "move" and bool(menu_checks(line) or row_check(line))
                if st and walk_gear and sub_ahead:
                    # A menu sub-step called above it changes gear first (Burmecia's World Map
                    # menu before "Zidane: The Ogre"): worn already isn't it yet.
                    st = False
                if st and (shop_check(line) or sell_except(line) or _OPTIMIZE_LINE.match(line.strip())):
                    self.shop_ticked.add(i)            # stays ticked
                elif st and walk_gear:
                    # On a walk step gear changes as you go ("Zidane: The Ogre", later "Zidane:
                    # Mage Masher"): once done it stays ticked. A menu step keeps checking.
                    self.shop_ticked.add(i)
            if st is None:
                continue
            n += 1
            if st:
                done.append(i)
            elif todo is None and not is_optional(line):
                todo = i                               # "(if stolen)" lines don't hold the step up
        return done, todo, n

    def _optimize_state(self, i: int, lines: list[str], done: list[int], state: GameState,
                        use_note: bool = True) -> bool | None:
        """ "Zidane: Optimize". With the gear written under it ("# The Tower / Coronet /
        ..."): done once that's what he wears. Without: once his gear changes after the
        lines before it for him are done, so taking a Power Belt off first isn't it."""
        who = (_OPTIMIZE_LINE.match(lines[i].strip()) or _OPTIMIZE_AND.match(lines[i].strip())).group(1).lower()
        if who not in state.equips or self.equip_step != self.step:
            return None
        wanted = _gear_note(lines, i) if use_note else None
        if wanted:
            from items import ITEMS
            on = set(state.equips[who])
            # Every item the note names is worn ("A or B": either); its slot doesn't matter,
            # as notes don't always list all five ("# Mage's Hat / Magic Armlet / Cloak").
            return all(any(ITEMS.index(n) in on for n in names) for names in wanted
                       if names not in (None, "-"))
        earlier = [k for k in range(i) if _for_character(lines[k], who)]
        if any(k not in done and k not in self.shop_ticked for k in earlier):
            return False                               # his lines above it first
        if i not in self.optimize_base:
            self.optimize_base[i] = list(state.equips[who]) if earlier else list(self.equip_base.get(who) or [])
        return state.equips[who] != self.optimize_base[i]

    def _enter_called_sub(self, state: GameState) -> None:
        """A walk or menu sub-step the step calls ("[Side room]"): one with a screen it starts
        on (picked under Starts on in the editor, or taught with the Learn key) comes up when
        you reach that screen; one without comes up as soon as every line written above the
        call is done. (Fight sub-steps come up with their fight.)"""
        s = self.current()
        hold = self._auto_hold
        if hold is not None:                           # a key put you here: wait for a change
            done_n = len(self._menu_progress(state)[0])
            if hold[1] is None:
                self._auto_hold = hold = (hold[0], done_n)
            if self.field_cur == hold[0] and done_n <= hold[1]:
                return
            self._auto_hold = None
        subs = {(self.steps[j].get("title") or "").strip().lower(): j for j in self._pending_inside(self.step)}
        lines = check_lines(s)
        for k, line in enumerate(step_lines(s)):
            name = substep_call(line)
            j = subs.get(name.lower()) if name else None
            if j is None:
                continue
            if self.steps[j]["type"] == "boss":
                return                                 # its fight brings it up
            start = self._start_screen(j)
            if start is not None:
                if self.field_cur == start:
                    self._goto(j, manual=False, why=f"in {self.steps[j].get('title')}")
                return                                 # otherwise reaching its screen brings it up
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
        Not with the menu open (synthesising), and nothing straight after a fight: that's
        the fight's spoils, which never count as a pickup."""
        gil_prev, self._gil_prev = self._gil_prev, state.gil
        if (gil_prev is not None and state.gil is not None and not self.in_battle
                and now - self._battle_end_at >= BAG_AFTER_FIGHT and not state.menu_vote
                and not self._has_sell_lines() and 0 < state.gil - gil_prev < GIL_PICKUP_MAX):
            self.pickup_event("Gil", source="bag")     # walk steps only: shops sell for gil
        prev, self._bag_prev = self._bag_prev, (dict(state.items) if state.items is not None else None)
        if prev is None or state.items is None or self.in_battle or state.menu_vote:
            return
        if now - self._battle_end_at < BAG_AFTER_FIGHT:
            return                      # spoils (a Hi-Potion after Sealion isn't Dali's Hi-Potion)
        from items import ITEMS
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
        parts = self._shop_parts(line, state)
        return None if parts is None else all(ok for _, ok in parts)

    def _shop_parts(self, line: str, state: GameState) -> list[tuple[str, bool]] | None:
        """Each item of a Buy/Sell line and whether it's done. None if it can't be checked."""
        c = shop_check(line)
        if c is None or state.items is None or self.shop_step != self.step:
            return None
        from items import ITEMS
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
            elif rule == "total":                      # "(Total)", "(4 if you stole one)": own this many
                worn = sum(g.count(i) for g in (state.equips or {}).values())   # (a stolen one put on counts)
                ok.append(max(peak, now + worn) >= n)
            else:
                ok.append(peak - base >= n)
        return [(name, k) for (name, _, _), k in zip(parts, ok)]

    def _track_menu(self, state: GameState) -> None:
        """Move on from a walk or menu step once every line the coach can check is done:
        with the menu closed, once it has been opened on this step, if there's equipment,
        abilities or rows on it (pickups written after the menu can finish it then too);
        straight away if it's only pickups and shopping."""
        s = self.current()
        if self.in_battle or s["type"] == "boss":
            return                                     # (a fight doesn't undo the menu used on the step)
        # Only something done while on this step moves it on: a step that was already
        # finished when you came back to it (F7, or a savestate from before) stays put.
        if self._entry_step != self.step:
            self._entry_step = self.step
            if self._back_to_parent != self.step:
                self.menu_opened_on = None             # (back from a sub-step: the menu used before counts)
            # Back from a fight in the middle of this step: what was done before it counts.
            self._entry_done = 0 if self._back_to_parent == self.step else len(self._menu_progress(state)[0])
            self._back_to_parent = None
            return
        if state.menu_vote:
            self.menu_opened_on = self.step            # (noted even with sub-steps still to come)
        if self._pending_inside(self.step):
            self._enter_called_sub(state)              # a "[Side room]" whose turn has come
            return                                     # a sub-step in the middle of it still to come
        if not any(menu_checks(l) or _OPTIMIZE_LINE.match(l.strip()) or row_check(l) or config_check(l)
                   for l in check_lines(s)):
            done, todo, n = self._menu_progress(state)
            if n and todo is None and len(done) > self._entry_done and self._has_somewhere_next() \
                    and not (s["type"] == "move" and instructions_after_checks(step_lines(s))):
                # (more to do written after the last ticked line: the next screen or F8 moves on)
                self._step_done("all lines done")
            return
        if state.menu_vote is None:
            return
        if state.menu_vote:
            self.menu_opened_on = self.step
            return
        if self.menu_opened_on == self.step:              # the menu was used here and is closed
            done, todo, n = self._menu_progress(state)
            if n and todo is None and len(done) > self._entry_done and self._has_somewhere_next() \
                    and not (s["type"] == "move" and instructions_after_checks(step_lines(s))):
                # (more to do written after the last ticked line: the next screen or F8 moves on)
                self._step_done("all lines done, menu closed")

    def _has_somewhere_next(self) -> bool:
        """A step after this one to move on to, or (for a sub-step) its step to go back to:
        a sub-step at the very end of the route still goes back to its step."""
        return self.step + 1 < len(self.steps) or (self.is_inside(self.step) and self.parent_of(self.step) is not None)

    def _step_done(self, why: str, finished_here: bool = False) -> None:
        """A walk or menu step finished by itself: on to the next step, or for a sub-step
        (a step in the middle of the one above), back to that step."""
        if self.is_inside(self.step) and self.parent_of(self.step) is not None:
            parent = self.parent_of(self.step)
            self._sub_done(self.step)
            self._goto(parent, manual=False, why=why + ", back to its step")
            self._back_to_parent = parent
            return
        nxt = self._next_index(self.step)
        start = self._start_screen(nxt) if nxt != self.step and self.steps[nxt]["type"] != "boss" else None
        if start is not None and self.field_cur != start and not finished_here:
            return                                     # done: the next step comes up on its own screen
                                                       # (not on reaching its Finishes on: it ends there)
        self._goto(nxt, manual=False, why=why)

    def _needs_f8(self, state: GameState) -> bool:
        """A walk or menu step the coach can't move on from by itself yet: nothing on it it
        can check (that it waits for), the next steps' screens not learned, and no known
        fight straight after. The Learn key there teaches it."""
        s = self.current()
        if s["type"] == "boss" or self.step + 1 >= len(self.steps) or s.get("end_screen") is not None:
            return False                               # (a finish screen picked in the editor ends it)
        pick = {i for i, _ in pickup_targets(_pickup_lines(s))}
        for i, line in enumerate(check_lines(s)):
            if is_optional(line) or (s["type"] == "move" and instructions_after_checks(step_lines(s))):
                continue
            if i in pick or substep_call(line) or self._menu_line_state(line, state) is not None:
                return False
        i = self.step
        for _ in range(FIELD_LOOKAHEAD):
            j = self._step_by(i, 1)
            if j == i:
                break
            if self._start_screen(j) not in (None,) + NO_SCREEN:
                return False
            i = j
        k = self._next_index(self.step, skip_pending=True)    # the step after it (past its sub-steps)
        nxt = self.steps[k]
        if nxt["type"] == "boss" and self._fight_known(k):
            return False
        return True


# "Zidane: Optimize, Wrist" / "Zidane: Optimize (The Ogre / ...), Coral Ring": Optimize, then more
_OPTIMIZE_AND = re.compile(r"^(zidane|vivi|garnet|steiner|freya|quina|eiko|amarant):\s*optimi[sz]e\s*"
                           r"(?:\([^)]*\)\s*)?,\s*\S", re.I)


def _gear_note(lines: list[str], i: int) -> list | None:
    """The gear written under an Optimize line ("# The Tower / Coronet / N-Kai Armlet /
    Power Vest / Rebirth Ring"): per slot, the items that count (None: not named, or
    not an item we know; "-": empty). None if there's no such note."""
    from items import ITEMS
    for line in lines[i + 1:]:
        if not line.strip():
            continue
        text = line.strip()
        if not text.startswith("#"):
            return None
        parts = [x.strip() for x in text.lstrip("#").split("/")]
        if len(parts) < 3:
            return None
        out = []
        for part in parts[:5]:
            if part in ("-", ""):
                out.append("-")
                continue
            names = [n.strip() for n in part.split(" or ")]
            known = [n for n in names if n in ITEMS]
            out.append(known or None)
        return out if any(x is not None for x in out) else None
    return None


def _for_character(line: str, who: str) -> bool:
    """A menu line about this character ("Zidane: -Power Belt", "Zidane: Optimize")."""
    c = menu_checks(line)
    if c is not None:
        return _record_of(c[0]) == who
    m = _OPTIMIZE_LINE.match(line.strip())
    return bool(m) and m.group(1).lower() == who

