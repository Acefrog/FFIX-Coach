"""Screens: learning which screen each step starts on, and moving the route on when
you walk onto a screen a later step starts on (never past a fight not won yet)."""

from __future__ import annotations

from field_names import field_name, place
from memory import GameState
from steps import step_lines
from syntax import substep_call
from tuning import FIELD_LOOKAHEAD, FIELD_SETTLE, LOAD_SETTLE, MAX_SCREEN, NO_SCREEN, STEP_SETTLE


class FieldsMixin:
    """Screens: learning where steps start, and moving on when you reach a later one.
    Mixed into Coach: works on its state."""

    # -- field tracking ----------------------------------------------------
    def _track_field(self, state: GameState, now: float) -> None:
        f = place(state.field_id)                      # (every world map screen: "World map")
        if f is None:
            return                                     # FMVs, loading, title: not a place
        self.field_read_at = now
        if f != self.field_cur:
            self.field_cur, self.field_since = f, now
            return
        if now - self.field_since < FIELD_SETTLE:
            return
        if f != self.field_logged:                     # each room you're in, for the run log
            self.field_logged = f
            self._event(f"on {field_name(f)}")

        # Learn where a step starts only once it has been up a moment (several F8s in a
        # row teach nothing about the steps passed through) and not just after a save
        # was loaded (that's a jump, not walking there).
        pend = self._pending_field_learn
        if pend is not None and not (now - self.step_since >= STEP_SETTLE and now - self.loaded_at >= LOAD_SETTLE):
            if self._pending_screen is None:
                self._pending_screen = f               # the screen a step comes up on never jumps it on
                self.field_handled = f
        elif pend is not None:
            # (Nothing is learned here: where a step starts is only ever taught with the
            # Learn key or picked in the editor.)
            self._pending_field_learn = None
            if self._pending_screen is None:
                self.field_handled = f

        own = self._start_screen(self.step)
        if own is not None and f != own:
            self._left_start = True
        if f == self.field_handled:
            return
        # Back on this step's own start screen, with no fight on it still to come, and the next
        # step starts here too (Evil Forest I and II both start at the Crash Site): on to it.
        if (own == f and self._left_start and self.current()["type"] != "boss"
                and not self._blocks(self.step)):
            nxt = self._next_index(self.step)
            if nxt != self.step and self.steps[nxt]["type"] != "boss" and self._start_screen(nxt) == f:
                self.field_handled = f
                self._goto(nxt, manual=False, why=f"reached {field_name(f)} again, step done")
                self._pending_field_learn = None
                return
        end = self.current().get("end_screen")
        if end is not None and f == int(end) and self.current()["type"] != "boss":
            self.field_handled = f                     # its finish screen (picked in the editor): done
            self._step_done(f"reached {field_name(f)}", finished_here=True)
            return
        # A walk/menu sub-step's own screen (a shop): into it, even with lines above it still
        # to do (they're still there when it's done: it comes back to its step). Not past a
        # fight called above it that's still to come: fights happen in order, and the menu
        # after a fight often starts in the fight's own room.
        if self._start_screen(self.step) != f:
            for j in self._subs_before_fight():
                if self.steps[j]["type"] != "boss" and self._start_screen(j) == f:
                    self._goto(j, manual=False, why=f"reached {field_name(f)}")
                    self._pending_field_learn = None
                    self.field_handled = f
                    return
        # (Lines on this step not ticked don't hold it: reaching a later step's start screen is
        # what moves the route on. Only a fight not won yet can't be walked past: _jumpable.)
        self.field_handled = f
        if self._start_screen(self.step) == f:
            return                                     # still on this step's own screen
        ahead = self._jumpable()
        for i in ahead:
            if self._start_screen(i) == f:
                if self._field_ambiguous(i, f):
                    return                             # several nearby steps share this screen
                self._goto(i, manual=False, why=f"reached {field_name(f)}")
                self._pending_field_learn = None
                self.field_handled = f
                return
        # The screen a step of the other branch starts on (one not settled yet): that's the
        # way the run went, so take that branch.
        last = max(self._jumpable(), default=self.step)
        for i in range(self.step + 1, min(len(self.steps), last + 4) if not self.speedrun else 0):
            b = self.branch_of(self.steps[i])
            if (not b or b[0] in self.branch_choice or self.active(i)
                    or self._start_screen(i) != f or self._answered(b[0]) or self._passed(i)):
                continue                               # (one the bag or AP answers isn't taken by a screen)
            if any(self._start_screen(j) == f
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

    def _subs_before_fight(self) -> list[int]:
        """The step's sub-steps still to do, in the order its lines call them, up to the
        first fight sub-step still to come (that one included). Ones it doesn't call by
        name come after them."""
        pending = self._pending_inside(self.step)
        out, called = [], set()
        for line in step_lines(self.current()):
            name = substep_call(line)
            if not name:
                continue
            js = [j for j in pending if (self.steps[j].get("title") or "").strip().lower() == name.lower()]
            called.update(js)
            out += [j for j in js if j not in out]
            if any(self.steps[j]["type"] == "boss" for j in js):
                return out
        return out + [j for j in pending if j not in called]

    def _blocks(self, i: int) -> bool:
        """A step a screen can't move you past: a fight not won yet (the step's own, or
        one of its sub-steps'). Fights happen in order, so walking on can't skip one."""
        if self.steps[i]["type"] == "boss" and not self.is_inside(i):
            # Except a step with any number of fights, some won: reaching the next step's
            # room is how it ends.
            return not (self._any_count(i) and self._part_way(i) and not self.in_battle)
        if self.is_inside(i) and self.parent_of(i) is not None:
            i = self.parent_of(i)                      # on a sub-step: the fights still to come in its step
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
        return any(self._start_screen(j) == f
                   for j in range(lo, hi) if j != target and self.active(j))
