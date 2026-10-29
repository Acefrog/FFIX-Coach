"""Small helpers about steps and fights that need no coach: a step's lines, its
identity, where steps went after an edit, what a raw enemy HP can mean."""

from __future__ import annotations

from syntax import expand_headers
from tuning import GUEST_RECORD, MAX_ATB_STEP, SCRIPT_HP


def formation_of(hps: list[int]) -> list[int]:
    return sorted(h for h in hps if h > 0)


def hp_candidates(h: int) -> set[int]:
    """What a raw enemy HP value could mean: itself, or a scripted boss's HP plus 10,000."""
    return {h, h - SCRIPT_HP} if h >= SCRIPT_HP else {h}


def _pickup_lines(s: dict) -> list[str]:
    """Every line of a walk or menu step can be a pickup ("1x Tent (...)")."""
    return check_lines(s) if s.get("type") != "boss" else []


def check_lines(s: dict) -> list[str]:
    """A step's lines as the checks read them (indented lines under "Buy" are buys)."""
    return expand_headers(step_lines(s))


def _record_of(who: str) -> str:
    """Which record a character's equipment, abilities and row are read from."""
    return GUEST_RECORD.get(who, who)


def step_lines(s: dict) -> list[str]:
    """A step's instructions, one per line ("lines"), or its single "text"."""
    if s.get("lines"):
        return list(s["lines"])
    return [s.get("text", "")]


def _step_moves(old: list[dict], new: list[dict]) -> dict[int, int | None]:
    """Where each step of a route is after an edit (old number -> new number, None if it's
    gone), found by the step's identity. Steps that look alike go to the nearest one."""
    at: dict[str, list[int]] = {}
    for j, s in enumerate(new):
        at.setdefault(step_key(s), []).append(j)
    out: dict[int, int | None] = {}
    taken: set[int] = set()
    for i, s in enumerate(old):
        free = [j for j in at.get(step_key(s), []) if j not in taken]
        out[i] = min(free, key=lambda j: abs(j - i)) if free else None
        if out[i] is not None:
            taken.add(out[i])
    return out


def _bars_filling(old: list[int], new: list[int]) -> bool:
    """Real ATB bars climb in small steps; random memory churn doesn't."""
    return any(0 < n - o <= MAX_ATB_STEP for o, n in zip(old, new))


def _fmt_igt(frames: int) -> str:
    """FFIX's in-game timer counts frames; shown as h:mm:ss assuming 60 fps."""
    secs = frames // 60
    return f"IGT {secs // 3600}:{secs // 60 % 60:02d}:{secs % 60:02d}"


def step_key(s: dict) -> str:
    """A step's identity: what learned screens, progress and edits find it by."""
    if s["type"] == "boss":
        return f"boss:{s['boss']}"
    return f"{s['type']}:{s.get('title', '')}:{step_lines(s)[0]}"
