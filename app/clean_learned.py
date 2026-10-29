"""Tidy learned.json: drop what was learned wrong (run once after updating).

    python app\\clean_learned.py

- A fight that learned the HP of another fight (an F8 pressed in the wrong fight):
  its learned HP, enemies and battle number are dropped, so its typed HP decides again.
- Screens learned for a step that are far from the screens of the steps around it in
  your routes (taught by several F8s in a row, or after loading a save): dropped.

A copy of the old file is kept in backups/.
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from paths import BACKUPS, LEARNED, ROUTES, write_atomic  # noqa: E402
from coach import Coach, hp_candidates, load_route  # noqa: E402

SPECIAL = 2900          # world map, Chocobo Forest...: screens far from the story's numbering
FAR = 300               # a screen this far from its neighbours' is out of place


def clean(data: dict, routes: list[dict]) -> list[str]:
    said = []
    fields = data.setdefault("fields", {})
    forms = data.setdefault("formations", {})
    fps = data.setdefault("fingerprints", {})
    ids = data.setdefault("battle_ids", {})
    typed: dict[str, set[int]] = {}
    for r in routes:
        for s in r["steps"]:
            if s["type"] == "boss" and not (int(s.get("fights") or 1) > 1):
                hp = r["bosses"].get(s["boss"], {}).get("hp")
                if hp:
                    typed.setdefault(s["boss"], set()).update(hp)
    for b, hp in typed.items():
        fp = fps.get(b) or {}
        form = fp.get("form") or forms.get(b)
        if not form or 65535 in form:
            continue
        live = set().union(*(hp_candidates(h) for h in form))
        if not live & hp:
            for part in (forms, fps, ids):
                part.pop(b, None)
            said.append(f"fight {b}: learned off a fight with HP {form}, not its HP {sorted(hp)}: dropped")
    for r in routes:
        keys = [Coach.step_key(s) for s in r["steps"]]
        name = r.get("name", "")
        def got(k):
            for kk in (f"{name}|{k}", k):
                if kk in fields:
                    return kk, fields[kk]
            return None, None
        vals = [got(k) for k in keys]
        for i, (kk, f) in enumerate(vals):
            if kk is not None and f in (0, 0xFFFF):
                fields.pop(kk, None)                   # read during an FMV or loading: not a place
                said.append(f"{name} step {i + 1} ({r['steps'][i].get('title')}): screen {f} isn't a place: dropped")
                continue
            if kk is None or f >= SPECIAL:
                continue
            near = [v for j, (k2, v) in enumerate(vals)
                    if k2 is not None and j != i and abs(j - i) <= 6 and 0 < v < SPECIAL]
            if len(near) >= 3 and abs(f - statistics.median(near)) > FAR:
                fields.pop(kk, None)
                said.append(f"{name} step {i + 1} ({r['steps'][i].get('title')}): screen {f} is far from "
                            f"the steps around it ({int(statistics.median(near))}): dropped")
    for k in [k for k, v in fields.items() if v in (0, 0xFFFF)]:
        fields.pop(k)                                  # same, for steps of routes no longer here
        said.append(f"{k.split(':')[1] if ':' in k else k}: screen 0 isn't a place: dropped")
    return said


def main() -> int:
    if not LEARNED.exists():
        print("Nothing learned yet.")
        return 0
    data = json.loads(LEARNED.read_text())
    routes = []
    for p in sorted(ROUTES.glob("*.json")):
        try:
            routes.append(load_route(p))
        except Exception as e:
            print(f"Skipped {p.name}: {e}")
    said = clean(data, routes)
    if not said:
        print("learned.json looks fine: nothing dropped.")
        return 0
    BACKUPS.mkdir(exist_ok=True)
    shutil.copy(LEARNED, BACKUPS / f"learned {time.strftime('%Y%m%d-%H%M%S')}.json")
    write_atomic(LEARNED, json.dumps(data, indent=1))
    print("\n".join(said))
    print(f"\n{len(said)} things dropped (old file kept in backups).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
