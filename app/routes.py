"""Routes: each one a file in the routes folder; config.json says which one is in use.

The coach, the editor and the launcher all go through here, so picking a route in
one of them is picked everywhere. Each route keeps its own place (progress) too.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from paths import BACKUPS, CONFIG, PROGRESS, ROOT, ROUTES, write_atomic


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def safe_name(name: str) -> str:
    """A route name that works as a file name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return name[:80] or "Route"


def migrate() -> None:
    """First run with routes: the old single route.json becomes the first route."""
    ROUTES.mkdir(exist_ok=True)
    PROGRESS.mkdir(exist_ok=True)
    old = ROOT / "route.json"
    if any(ROUTES.glob("*.json")) or not old.exists():
        return
    try:
        name = safe_name(json.loads(old.read_text(encoding="utf-8")).get("name") or "My route")
    except ValueError:
        name = "My route"
    shutil.copyfile(old, ROUTES / f"{name}.json")
    if (ROOT / "progress.json").exists():
        shutil.copyfile(ROOT / "progress.json", PROGRESS / f"{name}.json")
    set_active(name)
    BACKUPS.mkdir(exist_ok=True)
    old.replace(BACKUPS / "route.json (before routes folder).json")


def names() -> list[str]:
    migrate()
    return sorted((p.stem for p in ROUTES.glob("*.json")), key=str.lower)


def active() -> str:
    """The route in use (the first one if none is set or it's gone)."""
    all_ = names()
    want = _config().get("route")
    if want in all_:
        return want
    return all_[0] if all_ else ""


def path(name: str | None = None) -> Path:
    return ROUTES / f"{name or active()}.json"


def progress_path(name: str | None = None) -> Path:
    PROGRESS.mkdir(exist_ok=True)
    return PROGRESS / f"{name or active()}.json"


def set_active(name: str) -> None:
    """Remember which route is in use (the coach switches within a couple of seconds)."""
    cfg = _config()
    if cfg.get("route") == name:
        return
    cfg["route"] = name
    write_atomic(CONFIG, json.dumps(cfg, indent=2))


def blank_route(name: str) -> dict:
    return {"name": name,
            "steps": [{"disc": 1, "type": "move", "title": "Start", "lines": ["Your first step"]}]}


def create(name: str, copy_from: str | None = None) -> str:
    """A new route (empty, or a copy of another). Returns the name actually used."""
    migrate()
    base = safe_name(name)
    name, k = base, 2
    while path(name).exists():
        name, k = f"{base} ({k})", k + 1
    if copy_from and path(copy_from).exists():
        data = json.loads(path(copy_from).read_text(encoding="utf-8"))
        data["name"] = name
    else:
        data = blank_route(name)
    path(name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return name


def rename(old: str, new: str) -> str:
    new = safe_name(new)
    if new == old:
        return old
    if path(new).exists():
        raise ValueError(f"There's already a route called {new}.")
    data = json.loads(path(old).read_text(encoding="utf-8"))
    data["name"] = new
    path(new).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    path(old).unlink()
    if progress_path(old).exists():
        progress_path(old).replace(progress_path(new))
    if _config().get("route") == old:
        set_active(new)
    return new


def delete(name: str) -> None:
    """Moves the route to backups (nothing is lost for good)."""
    BACKUPS.mkdir(exist_ok=True)
    if path(name).exists():
        dest = BACKUPS / f"{name} (deleted).json"
        if dest.exists():                              # deleted before: keep that one too
            dest = BACKUPS / f"{name} (deleted {time.strftime('%Y%m%d-%H%M%S')}).json"
        path(name).replace(dest)
    if progress_path(name).exists():
        progress_path(name).unlink()


# ----------------------------------------------------------------------
# Fight steps: written like any other step (title, lines, backup note, plus HP)
# ----------------------------------------------------------------------
FIGHT_FIELDS = ("hp", "hp_names", "also_hp", "lv5", "needs", "battle_id", "fights", "groups")


def step_id(route: dict, title: str, taken: set | None = None) -> str:
    """A short id for a fight step (what the coach learns and remembers it by)."""
    base = re.sub(r"[^a-z0-9]+", "_", (title or "fight").lower()).strip("_") or "fight"
    taken = taken if taken is not None else {s.get("id") for s in route.get("steps", []) if s.get("type") == "boss"}
    sid, n = base, 2
    while sid in taken:
        sid, n = f"{base}_{n}", n + 1
    return sid


def inline_cards(route: dict) -> dict:
    """Older routes kept fights as separate cards: put each card's contents into its step."""
    if "bosses" not in route:
        return route
    cards = route.pop("bosses") or {}
    taken: set = set()
    for s in route.get("steps", []):
        if s.get("type") != "boss":
            continue
        cid = s.pop("boss", None) or "fight"
        card = cards.get(cid, {})
        sid = cid if cid not in taken else step_id(route, cid, taken)
        taken.add(sid)
        s["id"] = sid
        s["title"] = card.get("name", s.get("title") or cid)
        s["lines"] = list(card.get("turns", s.get("lines") or []))
        if card.get("if_wrong"):
            s["backup"] = card["if_wrong"]
        for f in FIGHT_FIELDS:
            if card.get(f):
                s[f] = card[f]
    return route


def expand(route: dict) -> dict:
    """The route as the coach uses it: fight steps also listed as cards by their id."""
    route = inline_cards(route)
    bosses = {}
    for s in route.get("steps", []):
        if s.get("type") != "boss":
            continue
        if not s.get("id"):
            s["id"] = step_id(route, s.get("title", ""))
        s["boss"] = s["id"]
        card = {"name": s.get("title") or s["id"], "turns": list(s.get("lines") or [])}
        if s.get("backup"):
            card["if_wrong"] = s["backup"]
        for f in FIGHT_FIELDS:
            if s.get(f):
                card[f] = s[f]
        groups = [g for g in card.get("groups") or [] if g.get("hp")]
        if groups:
            # Several kinds of fight on the step (soldiers, mages): recognised by any of
            # their HP, done once every kind has been fought as many times as it says.
            card["groups"] = groups
            card["hp"] = sorted({h for g in groups for h in g["hp"]})
            card["fights"] = sum(max(1, int(g.get("count") or 1)) for g in groups)
        bosses[s["id"]] = card
    route["bosses"] = bosses
    return route
