"""Fights: telling a battle has started or ended, recognising which fight on the
route it is (battle number, enemy records, HP), the hidden 10,000 HP of scripted
bosses, and moving the route on when a fight is won."""

from __future__ import annotations

from memory import GameState
from tuning import (CAPPED_HP, FP_MATCH, FP_MISMATCH_LIMIT, FP_STUCK_LIMIT, LOOKAHEAD_STEPS, MAX_ATB_STEP,
                    MIN_BOSS_SECONDS, PHASE_GAP, QUIET_SECONDS_TO_END, RESUME_WINDOW, SCRIPTED_LOOKAHEAD,
                    SCRIPT_HP)
from steps import formation_of, hp_candidates


class FightsMixin:
    """Fights: in battle or not, which fight it is, hidden HP, and moving on when won.
    Mixed into Coach: works on its state."""

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
        # hers but had her exact HP): HP still decides. So does HP typed for a step with
        # several fights that every enemy here has, where random battles can't happen: fights
        # of one kind can differ in their records (Bandersnatches scored 0 against the first).
        ahead = [i for i in self._boss_steps_ahead(self.step, LOOKAHEAD_STEPS)
                 if not (self.battle_id_now and self._known_id(i) is not None)
                 and (not self._fp_rules_out(i) or self._all_typed_hp(i, form))]
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

    def _all_typed_hp(self, i: int, form: list[int]) -> bool:
        """On a step with several fights, every enemy here has HP typed for it and random
        battles can't happen here: that's one of its fights, whatever was learned about the
        enemies' records (they differ between fights of one kind). A single fight keeps to
        its records: the same HP with other enemies isn't it."""
        typed = set(self.bosses.get(self.steps[i]["boss"], {}).get("hp") or [])
        return (self._fights(i) > 1 and bool(typed and form) and not self.battle_random_possible
                and all(hp_candidates(h) & typed for h in form))

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

    def _fight_known(self, i: int) -> bool:
        """Whether fight step i can be recognised: typed HP or battle number, or learned."""
        b = self.bosses.get(self.steps[i]["boss"], {})
        return bool(b.get("hp") or b.get("battle_id") is not None or self._learned_form(i)
                    or any(self.learned.fingerprint(k) or self.learned.battle_id(k) is not None
                           for k in self._keys(i)))
