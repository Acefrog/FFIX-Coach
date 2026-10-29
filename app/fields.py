"""Screens: learning which screen each step starts on, and moving the route on when
you walk onto a screen a later step starts on (never past a fight not won yet)."""

from __future__ import annotations

from field_names import field_name
from memory import GameState
from tuning import FIELD_LOOKAHEAD, FIELD_SETTLE, LOAD_SETTLE, MAX_SCREEN, NO_SCREEN, STEP_SETTLE


class FieldsMixin:
    """Screens: learning where steps start, and moving on when you reach a later one.
    Mixed into Coach: works on its state."""

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
        # A walk/menu sub-step's own screen (a shop): into it, even with lines above it still
        # to do (they're still there when it's done: it comes back to its step).
        if self._start_screen(self.step) != f:
            for j in self._pending_inside(self.step):
                if self.steps[j]["type"] != "boss" and self._start_screen(j) == f:
                    self._goto(j, manual=False, why=f"reached {field_name(f)}")
                    self._pending_field_learn = None
                    self.field_handled = f
                    return
        if self.current()["type"] != "boss" and self._menu_progress(state)[1] is not None:
            return                                     # something on this step still to do: stay
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
        for i in range(self.step + 1, min(len(self.steps), last + 4)):
            b = self.branch_of(self.steps[i])
            if (not b or b[0] in self.branch_choice or self.active(i)
                    or self._start_screen(i) != f):
                continue
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
        return any(self._start_screen(j) == f
                   for j in range(lo, hi) if j != target and self.active(j))
