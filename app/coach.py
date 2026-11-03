"""Decides what to show next, from live game state plus the route.

Pure Python with no Windows calls, so it can be tested anywhere.

How it moves through the route by itself:

1. In battle or not: from bytes the game changes when a battle starts (learned the
   first time ATB bars move), falling back to watching the ATB bars.
2. Which fight: only its HP (typed in the editor, or learned) together with the screen
   it started from (picked in the editor, or learned each time it's won). A fight that
   matches a step ahead jumps there; anything else is a random encounter, until F8
   during it teaches it (its HP and its screen).
3. Fight won: on to the next step (or back to the step it's in the middle of, or on
   to the step's next fight when it has several).
4. Walk and menu steps: each line that can be checked (pickups, shopping, equipment,
   abilities, rows, settings) ticks from memory; the step moves on once all are done,
   or when you walk onto a screen a later step is known to start on.
5. Learning: screens and fights are remembered in learned.json, so later runs need
   no key presses.

Where each part lives (Coach is put together from them; they share its state):

    coach.py     the route and your place in it: steps, progress, resets, keys, update()
    branches.py  alternative steps and conditions (14 / 14a, "Broadsword < 2")
    fights.py    in battle or not, which fight it is, moving on when it's won
    fields.py    screens: learning where steps start, moving on when you reach one
    checks.py    lines that tick: steals, pickups, shopping, equipment, rows, config
    view.py      what the panel shows
    learned.py   learned.json        steps.py   small step helpers        tuning.py  the numbers
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from field_names import field_name
from memory import GameState
from tuning import (ATB_DROP, CAPPED_HP, CAPPED_SETTLE, CLEARED_SECONDS, IDENTIFY_WITHIN, IGT_BACK,
                    IGT_MAX_STEP, IGT_NEW_GAME, IGT_RESET_CONFIRM, IGT_TRUST,
                    LOADING_HP, LOOKAHEAD_STEPS, MAX_SCREEN, NO_SCREEN, SCRIPT_HP, STALE_SECONDS,
                    UNDO_WINDOW)
from steps import _bars_filling, _step_moves, step_key
from learned import Learned
from branches import BranchesMixin, step_numbers
from fights import FightsMixin
from fields import FieldsMixin
from checks import ChecksMixin
from view import View, ViewMixin

# Everything other files import from here (the editor, overlay, report, tests).
from branches import (AP_OPS, OPS, ap_subject, ap_subjects,  # noqa: F401
                      parse_when, route_branches, when_label, when_text)
from steps import hp_candidates  # noqa: F401
from syntax import (line_segments, menu_checks, old_off_line, pickup_targets, row_check,  # noqa: F401
                    shop_check, steal_targets)
from tuning import *  # noqa: F401,F403


class Coach(BranchesMixin, FightsMixin, FieldsMixin, ChecksMixin, ViewMixin):
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
        self.battle_random_possible = True
        self.battle_counted = False
        self._ident_form: list[int] = []
        self._bag_prev: dict[int, int] | None = None   # bag on the last read (pickups)
        self._battle_end_at = -1e9
        self._gil_prev: int | None = None
        self._reset_pending = None          # (timer before, place before) after a reset, until it's clear
        self._steal_prev: dict[int, int] | None = None  # bag on the last read (steals)
        self._enc_flag_field: bool | None = None       # random battles possible here (last field read)
        self._enc_flag_seen: set[bool] = set()          # values that flag has shown
        self.battle_start_time = 0.0
        self.battle_field: int | None = None      # the screen the fight you're in started from
        self.battle_by_hand = False               # the fight was put on its step with F8/F7: it teaches nothing
        self._left_multi: int | None = None       # the several-fight step the route just moved on from
        self._extra_return: int | None = None     # one more of its fights: the step to go back to after
        self.pre_battle_step = 0
        self.last_end: tuple[list[int], int, float] | None = None  # formation, step, time
        self.sub = 0                    # fights already won on this step (steps with several fights)
        self._last_end_sub = False      # the last win only moved on to the step's next fight
        self.inside_done: set[int] = set()  # fights inside a step (Baku in Crash Site) already won
        self._end_dest: int | None = None   # where the last won fight sent you (F7 undoes it)
        self._back_to_parent: int | None = None
        self.events: list[str] = []          # lines for logs/run_log.txt
        self.branch_choice: dict[str, str] = {}   # branch group -> the branch you're on
        self.branch_at: dict[str, int] = {}       # branch group -> game timer when it was decided
        self._answers: dict[str, str] = {}        # branch group -> what the counts said last (run log)
        self._b_for: list | None = None           # the steps the branch tables below were worked out for
        self._b_opts: dict = {}
        self._b_checks: dict = {}
        self._b_of: dict = {}                     # id(step) -> (group, option)
        self.groups_done: dict[int, int] = {}     # kinds of fight on this step -> how many won
        self.cur_group: int | None = None         # the kind of fight being fought now
        self._last_group: int | None = None       # the kind of fight last counted (F7 takes it back)
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
        self.field_read_at = -1e9            # when the screen was last read (it's stale after a load)
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
        self.loaded_at = self._now                     # (the screen read before it isn't where you are)
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
        self._answers = {}
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
        if self.step != old:
            self.groups_done = {}
        self._fresh_subs(old)
        if not back:
            self._settle_branch()                      # (going back doesn't decide a branch)
        # A step with several fights the route moved on from: one more of them straight
        # after is that fight, not a random battle (a route with more of them than it says).
        left_multi = (not back and not manual and self.steps[old]["type"] == "boss"
                      and (self._fights(old) > 1 or bool(self._groups(old))))
        self._left_multi = old if left_multi else (None if self.steps[self.step]["type"] == "boss" or manual
                                                   else self._left_multi)
        # Learn where this step starts when the route got here by itself. Not after a key
        # (F8, F7: moving by hand teaches nothing; the Learn key does that), and not for fight
        # sub-steps (their fight brings them up). Set here too when the step changes during
        # a fight (a win), so the step before doesn't learn the screen after the fight.
        if not back and not manual and not (self.is_inside(self.step) and self.current()["type"] == "boss"):
            self._pending_field_learn = (self.step, False)
        else:
            self._pending_field_learn = None
        self._pending_screen = None
        self._save_progress()
        if self.in_battle and manual and back:
            # Going back during a fight (often on the victory screen, right after the
            # coach moved on): this fight must not move the route forward again.
            self.battle_is_boss = False
            self.hold_route = True
        elif self.in_battle:
            # Moved during a fight: this fight goes with the step you're on now (by hand it
            # learns nothing, not even when won: the Learn key does that).
            self.battle_is_boss = self.current()["type"] == "boss"
            if self.battle_is_boss and manual:
                self.identified = True
                self.battle_by_hand = True


    # -- the Learn key: teach what it can't work out here, without moving the route --------
    def learn(self) -> None:
        """In a fight: this is the fight of the fight step you're on (or the next one ahead):
        its HP and the screen it started from are learned, and it counts from now on, so
        winning it moves on as usual. Elsewhere: the screen you're on is where the next
        step starts (or a sub-step of this one, such as a shop), and the route moves on by
        itself as it would on any screen it knows."""
        if self.in_battle:
            self._learn_this_fight()
        else:
            self._learn_this_screen()
        self._save_progress()

    def _learn_this_fight(self) -> None:
        if not self.identified and self.prev is not None:
            self._identify(self.prev, self._now)
        # The fight step you're on, unless its typed HP doesn't fit this fight and a later
        # one's has every enemy in it (a fight you skipped is passed over; one helper with
        # the same HP as Plant Spiders doesn't make Steiner 1 that fight). Two fights can have the same HP
        # (Amdusias and Abadon): then it's the one you're on.
        form = self._formation()
        ahead = list(self._boss_steps_ahead(self.step, LOOKAHEAD_STEPS))
        here = self.current()["type"] == "boss" and self.active(self.step) and self.step not in self.inside_done
        fits = next((i for i in ahead if form and self._hp_all_typed(i, form)), None)
        if here and (fits is None or self._hp_fits(self.step, form) is not False):
            target = self.step
        else:
            target = fits if fits is not None else next(iter(ahead), None)
        if target is None:
            self.message = "Learn: no fight step ahead for this fight"
            return
        if target != self.step:
            self.step, self.step_since, self.sub = target, self._now, 0
        self.battle_is_boss, self.identified, self.hold_route = True, True, False
        if self.battle_counted and self.encounters:
            self.encounters.pop()                      # it was counted as a random battle
            self.battle_counted = False
        k, form = self._lkey(), self._formation()
        if form and self._hp_fits(self.step, form) is False:
            # Not the HP typed for this fight: it's taken as the fight this time, but its HP
            # isn't learned (a stray key press can't spoil the step).
            self._event(f"not learned: HP {form} doesn't fit {self._label(self.step)}'s typed HP")
        elif form and self._fights(self.step) > 1:
            self.learned.add_formation(k, form)
        elif form:
            self.learned.set_formation(k, form)
        self._learn_battle_screen(k)
        self.script_slots = self._find_script_slots()
        self._event(f"Learn key: this fight is {self._label(self.step)} (HP {form})")
        if self.battle_field is None:
            self.message = f"Learned {self._label(self.step)}'s HP; its room wasn't seen since the load: Learn in it again"
        else:
            self.message = f"Learned: {self._label(self.step)} (from {field_name(self.battle_field)})"

    def _learn_this_screen(self) -> None:
        f = self.field_cur
        if f is None or f in NO_SCREEN or f >= MAX_SCREEN:
            self.message = "Learn: this screen can't be read"
            return
        # A walk/menu sub-step of this step still to come whose screen isn't known (a shop
        # you're standing in), else the next step.
        target = next((j for j in self._pending_inside(self.step)
                       if self.steps[j]["type"] != "boss" and self._start_screen(j) is None), None)
        if target is None:
            target = self._next_index(self.step, skip_pending=True)
        if target == self.step:
            self.message = "Learn: no step after this one"
            return
        if self.steps[target]["type"] == "boss":
            self.message = "Learn: the next step is a fight: press Learn during it"
            return
        self.learned.set_field(self.step_key(self.steps[target]), f, self.rname)
        self._event(f"Learn key: {self._label(target)} starts on {field_name(f)}")
        self.message = f"Learned: {self._label(target)} starts here"
        self.field_handled = None                      # this screen again: on as usual

    def goto(self, index: int, why: str = "by hand") -> None:
        """Manual jump (hotkeys). Teaches the coach."""
        self._goto(index, manual=True, why=why)

    def next_step(self) -> None:
        if not self.in_battle and self.is_inside(self.step):
            self.inside_done.add(self.step)            # F8 on a fight inside a step: back to that step
            parent = self.parent_of(self.step)
            if parent is not None:
                self.goto(parent, why="F8 (fight inside a step)")
                self._back_to_parent = parent
                return
        self.goto(self._next_index(self.step, skip_pending=True), why="F8")

    def prev_step(self) -> None:
        # Undo a wrong automatic "boss done" (only the route: what it learned stays; the
        # editor's Forget removes it).
        if (not self.in_battle and self.last_end
                and self._now - self.last_end[2] < UNDO_WINDOW
                and self.step == (self._end_dest if self._end_dest is not None else self.last_end[1] + 1)):
            fight = self.last_end[1]
            self.inside_done.discard(fight)
            self.last_end = None
            if self.step != fight + 1:
                self.goto(fight, why="F7: undo fight win")  # it sent you back to the step it's inside
                return
        elif (not self.in_battle and self.sub > 0 and self.last_end
                and self._now - self.last_end[2] < UNDO_WINDOW and self.step == self.last_end[1]):
            self.last_end = None                       # a step with several fights: undo the last one
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def current(self) -> dict:
        return self.steps[self.step]

    step_key = staticmethod(step_key)          # a step's identity (steps.py)

    def _start_screen(self, i: int) -> int | None:
        """The screen step i starts on: typed in the route ("screen": a shop's own screen,
        known from the first run), else what was learned."""
        typed = self.steps[i].get("screen")
        if typed is not None:
            return int(typed)
        return self.learned.field(self.step_key(self.steps[i]), self.rname)

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
                elif (k < len(self.battle_first_hp) and now - self.battle_start_time < CAPPED_SETTLE
                      and LOADING_HP <= self.battle_first_hp[k] < CAPPED_HP and 0 < h < self.battle_first_hp[k]):
                    # Near the top of the counter, then a real number: still loading (Steiner 1
                    # read 65,526, then his 10,169). No hit takes that much in the first seconds.
                    self.battle_first_hp[k] = h
                elif (k < len(self.battle_first_hp) and 0 < self.battle_first_hp[k] < h < CAPPED_HP
                      and now - self.battle_start_time < CAPPED_SETTLE):
                    # Climbing in the first seconds: the fight was still loading (after a save
                    # load it read 257, then Shell Dragon's 20,921). Damage only takes HP down.
                    self.battle_first_hp[k] = h
                elif (k < len(self.battle_first_hp) and h == CAPPED_HP
                      and SCRIPT_HP <= self.battle_first_hp[k] < CAPPED_HP
                      and now - self.battle_start_time < CAPPED_SETTLE):
                    # Too big for the counter (Zorn & Thorn): read a moment early while the
                    # fight loads (65472, 62707), then it sits on 65535. Damage only takes HP
                    # down, so climbing to the cap is the load finishing.
                    self.battle_first_hp[k] = CAPPED_HP
                    self._script_cache.pop(k, None)
            self.battle_last_party = list(state.party_hp_slots)
            if state.battle_sig != self._fight_sig:
                self._fight_sig_stable = False
            if not self.identified:
                start_live = {k for k, h in enumerate(self.battle_start_hp) if h > 0}
                if (start_live and start_live <= self.active_enemy) \
                        or now - self.battle_start_time >= IDENTIFY_WITHIN:
                    self._identify(state, now)
            elif not self.battle_is_boss and not self.hold_route:
                self._reidentify()
            real = self._fight_enemies()
            if self.identified and real and all(self._shown_hp(k, state.enemy_hp[k]) == 0 for k in real):
                self.battle_cleared = True
                if self.cleared_since is None:
                    self.cleared_since = now
                # Boss beaten: move on now rather than waiting for the victory screen
                # (or a scripted ending) to hand back to the field.
                if (self.battle_is_boss and not self.hold_route
                        and now - self.cleared_since >= CLEARED_SECONDS):
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
