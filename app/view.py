"""What the panel shows: the step's lines with their ticks, live enemy HP, readiness
warnings (Limit Glove, Lucky Seven, LV5 Death), AP and level readouts, and the
next step underneath."""

from __future__ import annotations

from dataclasses import dataclass, field
from memory import GameState
from syntax import substep_call, turn_groups
from tuning import FLAG
from steps import _record_of, step_lines
from branches import step_numbers


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


class ViewMixin:
    """What the panel shows: the step, its lines and ticks, live HP and warnings.
    Mixed into Coach: works on its state."""

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
