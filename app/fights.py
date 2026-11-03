"""Fights: telling a battle has started or ended, recognising which fight on the
route it is (battle number, enemy records, HP), the hidden 10,000 HP of scripted
bosses, and moving the route on when a fight is won."""

from __future__ import annotations

from field_names import field_name
from memory import GameState
from tuning import (ANY_FIGHTS, CAPPED_HP, FP_MISMATCH_LIMIT, FP_STUCK_LIMIT, LOOKAHEAD_STEPS, MAX_ATB_STEP, PHASE_GAP,
                    QUIET_SECONDS_TO_END, RESUME_WINDOW, SCRIPT_HP)
from steps import formation_of, hp_candidates


class FightsMixin:
    """Fights: in battle or not, which fight it is, hidden HP, and moving on when won.
    Mixed into Coach: works on its state."""

    # -- steps with several fights (Fights: 3) -----------------------------
    def _fights(self, i: int) -> int:
        # How many fights fight step i has (1 unless the route says more; "any": no end by count).
        card = self.bosses.get(self.steps[i].get("boss"), {})
        if card.get("any_count"):
            return ANY_FIGHTS
        try:
            return max(1, int(card.get("fights") or 1))
        except (TypeError, ValueError):
            return 1

    def _any_count(self, i: int) -> bool:
        return bool(self.bosses.get(self.steps[i].get("boss"), {}).get("any_count"))

    def _group_count(self, i: int, k: int) -> int:
        """How many of kind k step i has (no end with "any")."""
        if self._any_count(i):
            return ANY_FIGHTS
        return max(1, int(self._groups(i)[k].get("count") or 1))

    def _part_way(self, i: int) -> bool:
        """Some of step i's fights are won (and it's the step you're on)."""
        return i == self.step and (self.sub > 0 or bool(self.groups_done))

    def _keys(self, i: int) -> list:
        # What step i's fights are learned under (routes.learn_as): one for all of them, so
        # a route with more or fewer of them shares it. With kinds of fight (soldiers,
        # mages): one key per kind, @0, @1.
        b = self.bosses.get(self.steps[i]["boss"], {}).get("learn_as") or self.steps[i]["boss"]
        groups = self._groups(i)
        if groups:
            return [f"{b}@{k}" for k in range(len(groups))]
        return [b]

    def _lkey(self) -> str:
        # What the fight being fought on the current step is learned under.
        keys = self._keys(self.step)
        if self._groups(self.step):
            g = self.cur_group
            if g is None and self.in_battle:
                g = self._group_of(self.step, self._formation())
            return keys[g if g is not None else self._next_group(self.step)]
        return keys[0]

    # -- kinds of fight on one step (Cleyra: soldiers and mages) -------------
    def _groups(self, i: int) -> list[dict]:
        if not 0 <= i < len(self.steps) or self.steps[i]["type"] != "boss":
            return []
        return self.bosses.get(self.steps[i]["boss"], {}).get("groups") or []

    def _ungroup(self) -> None:
        """A fight on a step with kinds of fight undone (F7, or it wasn't over)."""
        g = self._last_group
        if g is None or not self._groups(self.step):
            return
        if self.groups_done.get(g, 0) > 0:
            self.groups_done[g] -= 1
        self._last_group = None

    def _next_group(self, i: int) -> int:
        """The first kind of fight on step i still to do."""
        groups = self._groups(i)
        for k, g in enumerate(groups):
            if self.groups_done.get(k, 0) < self._group_count(i, k):
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
            return self.groups_done.get(k, 0) < self._group_count(i, k)
        order = sorted(range(len(groups)), key=lambda k: not todo(k))
        for need_all in (True, False):
            for k in order:
                sig = set(groups[k]["hp"])
                hits = [bool(c & sig) for c in live]
                if (all(hits) if need_all else any(hits)):
                    return k
        return None

    def _learned_form(self, i: int) -> bool:
        return any(self.learned.formations(k) for k in self._keys(i))

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

    # -- which fight it is: its HP and the screen it started from --------------
    # A fight is known by its HP (typed, or learned) and the screen it started from (where
    # you were when it began: picked in the editor, or learned each time it's won). That's
    # the only way: a fight without both is a random battle until Learn during it teaches it.
    def _fight_screens(self, i: int) -> set[int]:
        out = {int(self.steps[i]["screen"])} if self.steps[i].get("screen") is not None else set()
        for k in self._keys(i):
            out.update(self.learned.battle_screens(k))
        return out

    def _screen_says(self, i: int) -> bool | None:
        """Whether this fight started where fight step i does (None: not known yet)."""
        known = self._fight_screens(i)
        if not known or self.battle_field is None:
            return None
        return self.battle_field in known

    def _hp_all_typed(self, i: int, form: list[int]) -> bool:
        """Every one of these enemies has its HP typed for fight step i."""
        typed = set(self.bosses.get(self.steps[i]["boss"], {}).get("hp") or [])
        return bool(typed) and all(hp_candidates(h) & typed for h in form)

    def _hp_known(self, i: int, form: list[int]) -> bool:
        """These enemies have HP typed for fight step i, or this is the HP learned for it."""
        typed = set(self.bosses.get(self.steps[i]["boss"], {}).get("hp") or [])
        # Typed HP: any enemy loaded into this fight, not only those that have acted yet
        # (Steiner can sit still behind his helpers). Leftovers were already set aside.
        loaded = [h for h in self.battle_start_hp if h > 0]
        if any(hp_candidates(h) & typed for h in set(form) | set(loaded)):
            return True
        if typed and CAPPED_HP in form:
            return True                                # too big for the counter (Gizamaluke)
        return bool(form) and any(form in self.learned.formations(k) for k in self._keys(i))

    def _match_hp_screen(self, form: list[int]) -> int | None:
        """The fight step ahead with these enemies' HP that starts from this screen. On a step
        with several fights you're already part-way through, its next ones go by HP alone:
        they can start on other screens (each one's is learned as it's won)."""
        for i in self._boss_steps_ahead(self.step, LOOKAHEAD_STEPS):
            if not self._hp_known(i, form):
                if self._screen_says(i) and self._hit_before_read(i):
                    return i
                continue
            if self._screen_says(i):
                return i
            if self._fights(i) > 1 and self._part_way(i):
                return i
        return None

    def _hit_before_read(self, i: int) -> bool:
        """An enemy already below fight step i's typed HP when it was first read: hit before
        the coach saw it (Bandersnatch at 539 of 899, Prison Cage at 455 of 533). Only taken
        where random battles can't happen, so a random enemy with little HP isn't the boss."""
        if self.battle_random_possible:
            return False
        typed = self.bosses.get(self.steps[i]["boss"], {}).get("hp") or []
        loaded = [h for h in self.battle_start_hp if h > 0]
        return any(0 < c < t for h in loaded for c in hp_candidates(h) for t in typed)

    def _learn_battle_screen(self, key: str) -> None:
        f = self.battle_field
        if f is not None and self.learned.add_battle_screen(key, f):
            self._event(f"learned: {self._label(self.step)} starts from {field_name(f)}")

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
        # The last screen you were on. Not one read before a save was loaded or the game
        # reset (Masked Man "from" Hill of Despair): then it isn't known.
        self.battle_field = self.field_cur if self.field_read_at >= self.loaded_at else None
        self.battle_by_hand = False                     # put on its step with F8/F7
        self._extra_return = None
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
        self.battle_first_hp = self.battle_start_hp      # the same list: filled in as slots load
        self._script_cache = {}
        self.cleared_since = None
        self.won_early = False
        self.battle_last_party = list(state.party_hp_slots)
        self.battle_start_party = list(state.party_hp_slots)
        self._fight_sig, self._fight_sig_stable = state.battle_sig, bool(state.battle_sig)
        self.message = ""
        self.steal_note = None
        self.pre_battle_step = self.step
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

        jump = self._match_hp_screen(form)
        was = self.step
        extra = self._left_multi
        if jump is not None:
            if jump != self.step:
                self.message = f"Recognised: {self.bosses[self.steps[jump]['boss']]['name']}"
            self.step = jump
            self.battle_is_boss = True
            what = f"recognised as {self._label(jump)} (by HP and screen)"
        elif (extra is not None and extra != self.step and self._hp_known(extra, form)
              and (self._screen_says(extra) or not self.battle_random_possible)):
            # One more of the several fights just done (this route has more of them than
            # the step says): it's that fight, and the route carries on from here after.
            self._extra_return = self.step
            self.step = extra
            self.battle_is_boss = True
            self.message = f"One more {self.bosses[self.steps[extra]['boss']]['name']} fight"
            what = f"one more fight of {self._label(extra)}"
        else:
            what = "random battle" if self.battle_random_possible else "not recognised"
        if self.step != was:
            self.step_since = self._now
        room = field_name(self.battle_field) if self.battle_field is not None else "a room not seen since the load"
        self._event(f"fight started from {room}, HP {form}: {what}")
        self.script_slots = self._find_script_slots()
        if not self.battle_is_boss and self.battle_random_possible and not self.battle_counted:
            # A random battle: it counts as soon as it's recognised, not when it ends.
            self.battle_counted = True
            self._count_encounter(self._igt_last or 0)
        self._save_progress()

    def _reidentify(self) -> None:
        """An enemy that only starts moving after the fight was judged (Masked Man's own
        slot can wake up after his two invisible helpers) can still make it the boss."""
        form = self._formation()
        if form == self._ident_form:
            return
        self._ident_form = form
        jump = self._match_hp_screen(form)
        if jump is None or (jump == self.step and self.battle_is_boss):
            return
        self._event(f"fight now HP {form}: recognised as {self._label(jump)} (by HP and screen)")
        self.step = jump
        self.battle_is_boss = True
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
        if self.in_battle:
            parts.append(f"from {field_name(self.battle_field)}")
        return " ".join(parts)

    def _finish_boss(self, now: float) -> None:
        b = self._lkey()
        if not self.battle_by_hand:                    # (a fight put on its step with F8/F7 teaches nothing)
            self._learn_battle_screen(b)
        if self._extra_return is not None:
            # One more of a step's fights after it was done: back to where the route was.
            back, self._extra_return = self._extra_return, None
            self._event(f"fight won: {self._label(self.step)} (one more than it says)")
            self.step, self.step_since = back, now
            self._save_progress()
            self.message = "Fight done: back to this step"
            return
        form = self._formation()
        # Remember the HP it stopped at, so the same fight picking up again is recognised.
        real = self._real_enemies()
        self.last_end = (formation_of([self.battle_last_hp[k] for k in real
                                       if k < len(self.battle_last_hp)]), self.step, now)
        n = self._fights(self.step)
        groups = self._groups(self.step)
        if groups:
            g = self.cur_group if self.cur_group is not None else self._group_of(self.step, form)
            if g is None or (self.groups_done.get(g, 0) >= self._group_count(self.step, g)
                             and sum(self.groups_done.values()) < n):
                g = self._next_group(self.step)        # can't tell which: the next one still to do
            self.groups_done[g] = self.groups_done.get(g, 0) + 1
            self._last_group = g
            self.cur_group = None
            left = sum(max(0, self._group_count(self.step, k) - self.groups_done.get(k, 0))
                       for k in range(len(groups)))
            of = "" if self._any_count(self.step) else f"/{self._group_count(self.step, g)}"
            self._event(f"{groups[g].get('name') or 'fight'} done "
                        f"({self.groups_done[g]}{of}) on {self._label(self.step)}")
            self.sub = min(n - 1, sum(self.groups_done.values()))
            self._last_end_sub = left > 0
            if left:
                self._save_progress()
                self.message = ("Fight done: on to the next step's room when they're over"
                                if self._any_count(self.step) else f"Fight done: {left} more on this step")
                return
        else:
            self._last_end_sub = self.sub + 1 < n
        if self._last_end_sub:
            self.sub += 1                              # the step's next fight
            self._save_progress()
            self.message = ("Fight done: on to the next step's room when they're over"
                            if self._any_count(self.step) else f"Fight done: {n - self.sub} more on this step")
            return
        if self.is_inside(self.step) and self.parent_of(self.step) is not None:
            # A fight in the middle of a step: back to that step to carry on with it.
            self.inside_done.add(self.step)
            parent = self.parent_of(self.step)
            self._goto(parent, manual=False, why="fight won, back to its step")
            self._end_dest = parent
            self._back_to_parent = parent
            self.message = ""                          # (the step it's in just carries on)
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
        if was_boss:
            self._finish_boss(now)
        if self._extra_return is not None:             # (it ended without a win)
            self.step, self._extra_return = self._extra_return, None
        self.battle_start_hp = []
        self.battle_cleared = False

    def _fight_known(self, i: int) -> bool:
        """Whether fight step i can be recognised: its HP (typed or learned) and its screen
        (picked in the editor, or learned) are both known."""
        b = self.bosses.get(self.steps[i]["boss"], {})
        return bool((b.get("hp") or self._learned_form(i)) and self._fight_screens(i))
