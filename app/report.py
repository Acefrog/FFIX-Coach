"""What the coach knows about each step of a route, and how the last run went.

Used by the editor: for each walk or menu step, whether the coach can move on from it
by itself (its lines can be checked, the next step's screen has been learned, or the
next fight can be recognised) or still needs the Learn key; for each fight step, how it will be
recognised; and, from logs/run_log.txt, which steps needed F8 or F7 last run.
No window needed (tested on its own).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

from paths import LEARNED, LOGS
import routes
from field_names import field_name
from syntax import classify, expand_headers, instructions_after_checks, is_optional

FIELD_LOOKAHEAD = 3            # same as the coach: how far ahead a learned screen may jump


def load_learned(path: Path = LEARNED):
    from coach import Learned
    return Learned(path if path.exists() else None)


def _expanded(route: dict) -> dict:
    return routes.expand(copy.deepcopy(route))


def _key(s: dict) -> str:
    from coach import Coach
    return Coach.step_key(s)


def _checkable(s: dict) -> tuple[int, int, bool]:
    """(lines the step waits for, "(if ...)" lines it doesn't, any menu lines)."""
    need = opt = 0
    menu = False
    for line in expand_headers(s.get("lines") or []):
        info = classify(line)
        if not info.checked or info.problem or info.kind in ("note", "comment", "ap"):
            continue
        if info.kind in ("equip", "row", "config"):
            menu = True
        if is_optional(line):
            opt += 1
        else:
            need += 1
    return need, opt, menu


def taught_wrong(route: dict, learned, i: int) -> list[int] | None:
    """The HP fight step i learned, if it doesn't fit the HP typed for it (taught in another
    fight): the coach ignores what was learned with it."""
    from coach import hp_candidates
    s = route["steps"][i]
    typed = route["bosses"].get(s.get("boss"), {}).get("hp")
    form = learned.formation(fight_keys(s, route["bosses"].get(s.get("boss"), {}))[0])
    if not typed or not form or int(s.get("fights") or 1) > 1 or 65535 in form:
        return None
    live = set().union(*(hp_candidates(h) for h in form))
    return None if live & set(typed) else form


def fight_keys(s: dict, card: dict, steps: list | None = None) -> list[str]:
    """What each of a fight step's fights is learned under (as the coach keys them). From
    the editor (a step not expanded yet), pass the route's steps."""
    import routes
    b = card.get("learn_as") or (routes.learn_as(steps, s) if steps is not None else s.get("boss") or s.get("id"))
    if card.get("groups"):
        return [f"{b}@{k}" for k in range(len(card["groups"]))]
    return [b]                                 # (all of a step's fights: one key)


def fight_screens(route: dict, learned, i: int) -> list[int]:
    """The screens fight step i starts from: picked in the editor, then learned."""
    s = route["steps"][i]
    out = [int(s["screen"])] if s.get("screen") is not None else []
    for k in fight_keys(s, route["bosses"].get(s.get("boss"), {})):
        out += [f for f in learned.battle_screens(k) if f not in out]
    return out


def fight_known(route: dict, learned, i: int) -> list[str]:
    """How fight step i is recognised: its HP and the screen it starts from (empty: the
    coach doesn't know both yet, so the Learn key during it is needed once)."""
    s = route["steps"][i]
    card = route["bosses"].get(s.get("boss"), {})
    if card.get("hp"):
        hp = "HP " + ", ".join(str(h) for h in card["hp"])
    else:
        got = next((learned.formations(k)[0] for k in fight_keys(s, card) if learned.formations(k)), None)
        hp = "HP " + ", ".join(str(h) for h in got) + " (learned)" if got else None
    screens = fight_screens(route, learned, i)
    if not hp or not screens:
        return []
    return [hp, "starting from " + " / ".join(field_name(f) for f in screens)]


def _after(steps: list[dict], i: int, n: int) -> list[int]:
    """Up to n steps that can come after step i: past the other way it can go (alternatives)."""
    from coach import route_branches
    per, out = route_branches(steps)[0], []
    b = per[i]
    for j in range(i + 1, len(steps)):
        if b and per[j] and per[j][0] == b[0] and per[j][1] != b[1]:
            continue
        out.append(j)
        if len(out) == n:
            break
    return out


def run_way(route: dict, progress: Path) -> dict[int, bool]:
    """The way the run in progress goes, from the coach's progress file: for each step that is
    one of several ways (a branch, or a version of a step with a condition), True if this run
    takes it and False if not. {} with no run (no progress file)."""
    import copy
    import json
    import tempfile
    from coach import Coach
    import routes
    try:
        data = json.loads(progress.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    c = Coach(routes.expand(copy.deepcopy(route)), learned_path=Path(tempfile.gettempdir()) / "ffix_coach_way.json")
    c.branch_choice = {str(k): str(v) for k, v in (data.get("branches") or {}).items()}
    c.obtained = [list(x) for x in data.get("obtained") or []]
    out = {}
    for i, s in enumerate(c.steps):
        versions = c._versions(i)
        if c.branch_of(s) is not None or any(c.steps[k].get("when") for k in versions if k != i):
            out[i] = c.active(i)
    return out


def step_report(route: dict, learned=None) -> list[dict]:
    """For every step: {"ok": moves on / is recognised by itself, "how": in words,
    "screen": the screen it's learned to start on (or None)}."""
    learned = learned if learned is not None else load_learned()
    r = _expanded(route)
    name = route.get("name", "")
    steps = r["steps"]
    out = []
    for i, s in enumerate(steps):
        screen = s.get("screen") if s.get("screen") is not None else learned.field(_key(s), name)
        if s["type"] == "boss":
            known = fight_known(r, learned, i)
            n = int(s.get("fights") or 1)
            if known:
                how = "Recognised by " + " and ".join(known)
                if s.get("any_count"):
                    how += " (any number of fights: ends on the next step's room)"
                elif n > 1:
                    how += f" ({n} fights)"
            else:
                how = "Not recognised yet: set its room and HP, or press F6 in the fight"
            out.append({"ok": bool(known), "how": how, "screen": screen})
            continue
        need, opt, menu = _checkable(s)
        ways = []
        after = need and s["type"] == "move" and instructions_after_checks(s.get("lines") or [])
        if after:
            need = 0                            # more to do after its last checked line: not by its lines
        if need:
            ways.append(f"{need} checked line{'s' if need > 1 else ''} done" + (", menu closed" if menu else ""))
        if s.get("end_screen") is not None:
            ways.append(f"at {field_name(int(s['end_screen']))} (its finish)")
        nxt = _after(steps, i, FIELD_LOOKAHEAD)
        for j in nxt:
            f = steps[j].get("screen") if steps[j].get("screen") is not None else learned.field(_key(steps[j]), name)
            if f is not None and f != screen:
                ways.append(f"at {field_name(f)}")
                break
        j = nxt[0] if nxt else len(steps)       # the very next step is a fight it knows
        if j < len(steps) and steps[j]["type"] == "boss" and fight_known(r, learned, j):
            ways.append("next fight starts")
        if ways:
            how = "Moves on: " + " · or ".join(ways)
            if opt and not need:
                how += f" ({opt} \"if\" line{'s' if opt > 1 else ''} skipped)"
        else:
            how = "Won't move on by itself: set the next step's Starts on, or press Shift+F6 there"
        out.append({"ok": bool(ways), "how": how, "screen": screen})
    return out


# ----------------------------------------------------------------------
# Last run, from logs/run_log.txt
# ----------------------------------------------------------------------
_STEP = re.compile(r"^(\d\d:\d\d:\d\d) step (\d+[a-z]?(?:\.\d+)?) (.*?) -> (\d+[a-z]?(?:\.\d+)?) (.*) \((.*)\)$")
_NOT_LEARNED = re.compile(r"^(\d\d:\d\d:\d\d) not learned: HP \[(.*?)\] doesn't fit (\d+[a-z]?(?:\.\d+)?) (.*)'s typed HP$")


def last_run(route_name: str, path: Path | None = None) -> list[str]:
    """The lines of the last session in run_log.txt that was on this route."""
    path = path or (LOGS / "run_log.txt")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    sessions, cur, on = [], [], False
    for line in text.splitlines():
        if line.startswith("=== "):
            if on and cur:
                sessions.append(cur)
            cur, on = [], line.rstrip().endswith(f"route: {route_name} ===")
            continue
        m = re.match(r"^\d\d:\d\d:\d\d switched to route (.*)$", line)
        if m:
            if on and cur:
                sessions.append(cur)
            cur, on = [], m.group(1) == route_name
            continue
        if on:
            cur.append(line)
    if on and cur:
        sessions.append(cur)
    return sessions[-1] if sessions else []


def _find(route: dict, number: str, title: str) -> int | None:
    """The step a run-log line means: by its number (14, 14a) and title, or the nearest
    step with that title."""
    from coach import step_numbers
    steps = route["steps"]
    nums = step_numbers(steps)
    def label(s):
        return s.get("title") or ""
    if number in nums and label(steps[nums.index(number)]) == title:
        return nums.index(number)
    at = int("".join(ch for ch in number.split(".")[0] if ch.isdigit()) or 0) - 1
    hits = [i for i, s in enumerate(steps) if label(s) == title]
    return min(hits, key=lambda i: abs(i - at)) if hits else None


_UNTICKED = re.compile(r"^\S+ (F8|Shift\+F8) on (\S+) (.*?): \"(.*)\" not ticked$")


def run_summary(route: dict, lines: list[str]) -> dict[int, list[str]]:
    """Step index -> what happened there last run, in words."""
    out: dict[int, list[str]] = {}
    for line in lines:
        m = _STEP.match(line)
        if m:
            _, a, at, b, bt, why = m.groups()
            i = _find(route, b, bt)
            if i is None:
                continue
            if why.startswith("F8"):
                src = _find(route, a, at)
                out.setdefault(src if src is not None else i, []).append(
                    "F8 pressed to move on from here")
            elif why.startswith("F7"):
                out.setdefault(i, []).append("F7: came back to this step (it moved on too soon or too far)")
            continue
        m = _UNTICKED.match(line)
        if m:
            key, n, t, what = m.groups()
            i = _find(route, n, t)
            if i is not None:
                out.setdefault(i, []).append(f"{key} pressed with \"{what}\" not ticked")
            continue
        m = _NOT_LEARNED.match(line)
        if m:
            _, hp, n, t = m.groups()
            i = _find(route, n, t)
            if i is not None:
                out.setdefault(i, []).append(f"F8 in a fight with HP [{hp}], not this fight's typed HP")
    return out


def run_problems(route: dict, learned=None, log_path: Path | None = None) -> list[tuple[int | None, None, str]]:
    """Extra entries for the editor's Checks list: steps that will need F8, and what
    went wrong at each step last run."""
    learned = learned if learned is not None else load_learned()
    rep = step_report(route, learned)
    out: list[tuple[int | None, None, str]] = []
    r = _expanded(route)
    for i, s in enumerate(r["steps"]):
        if s["type"] != "boss":
            continue
        form = taught_wrong(r, learned, i)
        if form:
            out.append((i, None, f"Step {i + 1} ({s.get('title')}): learned off a fight with HP {form}, not its "
                                 f"typed HP {r['bosses'][s['boss']]['hp']}: ignored. Press Forget on it to clear it"))
    for i, events in sorted(run_summary(route, last_run(route.get("name", ""), log_path)).items()):
        s = route["steps"][i]
        seen = []
        for e in events:
            if e not in seen:
                seen.append(e)
        for e in seen:
            n = events.count(e)
            out.append((i, None, f"Last run, step {i + 1} ({s.get('title') or 'fight'}): {e}"
                                 + (f" ×{n}" if n > 1 else "")))
    stuck = [i for i, x in enumerate(rep) if not x["ok"] and route["steps"][i]["type"] != "boss"]
    if stuck:
        out.append((stuck[0], None, f"{len(stuck)} walk/menu step(s) can't move on by themselves yet "
                                    f"(⚑ in the list): the Learn key there once teaches them"))
    return out
