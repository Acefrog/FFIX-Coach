"""Ready-made fights for the editor's fight list: every boss and forced fight on the way
through the game, in story order, with the HP the coach recognises them by.

HP is the number the enemy really has (what you'd see with Scan); fights that end on a
script store it with 10,000 added, and the coach takes that off by itself. Values come
from routes played on PSX and from fights the coach recorded on real runs.
Fights you save from the editor go in fights_custom.json (next to FFIX Coach.pyw).
"""

from __future__ import annotations

import json

from paths import FIGHTS_CUSTOM as CUSTOM

# name, disc, hp (enemy HP the fight is known by), hp names, extra fields
FIGHTS: list[dict] = [
    # Disc 1
    {"name": "Masked Man", "disc": 1, "hp": [188], "hp_names": {"188": "Masked Man"}},
    {"name": "King Leo", "disc": 1, "hp": [186], "hp_names": {"186": "King Leo"}},
    {"name": "Steiner 1", "disc": 1, "hp": [169], "hp_names": {"169": "Steiner"}},
    {"name": "Steiner 2", "disc": 1, "hp": [167], "hp_names": {"167": "Steiner"}},
    {"name": "Steiner 3 + Bomb", "disc": 1, "hp": [162, 38], "hp_names": {"162": "Steiner", "38": "Bomb"}},
    {"name": "Prison Cage 1", "disc": 1, "hp": [513], "hp_names": {"513": "Prison Cage"}},
    {"name": "Prison Cage 2", "disc": 1, "hp": [533, 67], "hp_names": {"533": "Prison Cage", "67": "Prison Cage"}},
    {"name": "Baku", "disc": 1, "hp": [202], "hp_names": {"202": "Baku"}},
    {"name": "Plant Brain", "disc": 1, "hp": [916, 510], "hp_names": {"916": "Plant Brain", "510": "Plant Brain"}},
    {"name": "Plant Spiders", "disc": 1, "hp": [33], "hp_names": {"33": "Plant Spider"}},
    {"name": "Black Waltz 1 + Sealion", "disc": 1, "hp": [229, 472],
     "hp_names": {"229": "Black Waltz 1", "472": "Sealion"}},
    {"name": "Black Waltz 2", "disc": 1, "hp": [1030], "hp_names": {"1030": "Black Waltz 2"}},
    {"name": "Black Waltz 3", "disc": 1, "hp": [1128], "hp_names": {"1128": "Black Waltz 3"}},
    {"name": "Festival of the Hunt", "disc": 1},
    {"name": "Black Mages (Gizamaluke's Grotto)", "disc": 1, "hp": [398], "hp_names": {"398": "Black Mage"},
     "fights": 2},
    {"name": "Gizamaluke", "disc": 1, "hp": [3175], "hp_names": {"3175": "Gizamaluke"}},
    {"name": "Black Mages (Burmecia)", "disc": 1, "hp": [398], "hp_names": {"398": "Black Mage"}},
    {"name": "Beatrix 1", "disc": 1, "hp": [3630], "hp_names": {"3630": "Beatrix"}},
    # Disc 2
    {"name": "Black Waltz 3 (South Gate)", "disc": 2, "hp": [1292], "hp_names": {"1292": "Black Waltz 3"}},
    {"name": "Ralvurahva", "disc": 2, "hp": [2296], "hp_names": {"2296": "Ralvurahva"}},
    {"name": "Antlion", "disc": 2, "hp": [3938], "hp_names": {"3938": "Antlion"}},
    {"name": "Alexandrian Soldiers (Cleyra)", "disc": 2,
     "groups": [{"name": "Soldiers", "hp": [523], "count": 4}, {"name": "Type B", "hp": [526], "count": 3},
                {"name": "Soldiers & Type B", "hp": [523, 526], "count": 1}]},
    {"name": "Beatrix 2", "disc": 2, "hp": [4736], "hp_names": {"4736": "Beatrix"}},
    {"name": "Zorn & Thorn", "disc": 2, "hp": [2984, 4896], "hp_names": {"2984": "Thorn", "4896": "Zorn"}},
    {"name": "Beatrix 3", "disc": 2, "hp": [5709], "hp_names": {"5709": "Beatrix"}},
    {"name": "Bandersnatch (first)", "disc": 2, "hp": [899], "hp_names": {"899": "Bandersnatch"}},
    {"name": "Bandersnatches & Black Mages", "disc": 2,
     "groups": [{"name": "Bandersnatches", "hp": [899], "count": 4}, {"name": "Black Mages", "hp": [623], "count": 1}]},
    {"name": "Ralvuimago", "disc": 2, "hp": [3352], "hp_names": {"3352": "Ralvuimago"}},
    {"name": "Lani", "disc": 2, "hp": [5708], "hp_names": {"5708": "Lani"}},
    {"name": "Hilgigars", "disc": 2, "hp": [8106], "hp_names": {"8106": "Hilgigars"}},
    {"name": "Zombies & Dracozombie (Iifa elevator)", "disc": 2, "fights": 2},
    {"name": "Soulcage", "disc": 2, "hp": [9765], "hp_names": {"9765": "Soulcage"},
     "lv5": ["zidane", "vivi", "garnet", "eiko"]},
    {"name": "Scarlet Hair (Amarant)", "disc": 2, "hp": [8985], "hp_names": {"8985": "Amarant"}},
    {"name": "Mistodons (Iifa Tree)", "disc": 2, "hp": [1473], "hp_names": {"1473": "Mistodon"}, "fights": 4},
    # Disc 3
    {"name": "Mistodons (Alexandria)", "disc": 3, "hp": [1473], "hp_names": {"1473": "Mistodon"}, "fights": 5},
    {"name": "Ark", "disc": 3, "hp": [20002], "hp_names": {"20002": "Ark"}},
    {"name": "Valia Pira", "disc": 3, "hp": [12119], "hp_names": {"12119": "Valia Pira"}},
    {"name": "Red Dragons", "disc": 3, "hp": [8000], "hp_names": {"8000": "Red Dragon"}},
    {"name": "Meltigemini", "disc": 3, "hp": [24348], "hp_names": {"24348": "Meltigemini"}},
    {"name": "Taharka", "disc": 3, "hp": [29816, 29186], "hp_names": {"29816": "Taharka", "29186": "Taharka"}},
    {"name": "Earth Guardian", "disc": 3, "hp": [20756], "hp_names": {"20756": "Earth Guardian"}},
    {"name": "Amdusias", "disc": 3, "hp": [10926], "hp_names": {"10926": "Amdusias"}},   # (same as Abadon: told apart by screen)
    {"name": "Abadon", "disc": 3, "hp": [10926], "hp_names": {"10926": "Abadon"}},
    {"name": "Shell Dragon", "disc": 3, "hp": [10921], "hp_names": {"10921": "Shell Dragon"}},
    {"name": "Silver Dragon", "disc": 3, "hp": [24055], "hp_names": {"24055": "Silver Dragon"}},
    {"name": "Garland", "disc": 3, "hp": [47028, 40728], "hp_names": {"47028": "Garland", "40728": "Garland"}},
    {"name": "Kuja", "disc": 3, "hp": [42382], "hp_names": {"42382": "Kuja"}},
    # Disc 4
    {"name": "Nova Dragon", "disc": 4, "hp": [54940], "hp_names": {"54940": "Nova Dragon"}},
    {"name": "Maliris", "disc": 4, "hp": [59492, 59497], "hp_names": {"59492": "Maliris", "59497": "Maliris"}},
    {"name": "Tiamat", "disc": 4, "hp": [59494], "hp_names": {"59494": "Tiamat"}},
    {"name": "Kraken", "disc": 4, "hp": [55536], "hp_names": {"55536": "Kraken"}},
    {"name": "Lich", "disc": 4, "hp": [58554], "hp_names": {"58554": "Lich"}},
    {"name": "Deathguise", "disc": 4, "hp": [55535], "hp_names": {"55535": "Deathguise"},
     "lv5": ["zidane", "steiner", "freya", "amarant"]},
    {"name": "Trance Kuja", "disc": 4, "hp": [63863], "hp_names": {"63863": "Trance Kuja"}},
    {"name": "Necron", "disc": 4, "hp": [54100], "hp_names": {"54100": "Necron"}},
]

# Where each fight starts from (the screen you're on when it begins), as learned on full
# runs: with its HP, that's how the coach knows it, so a new route needs no learning for
# fights from this list. Fights with kinds (Cleyra): one list per kind. Learned screens
# are added to these.
STARTS: dict[str, list] = {
    "Masked Man": [50],
    "King Leo": [62],
    "Steiner 1": [58, 57],
    "Steiner 2": [67],
    "Steiner 3 + Bomb": [60],
    "Prison Cage 1": [251],
    "Prison Cage 2": [251],
    "Baku": [202],
    "Plant Brain": [257],
    "Plant Spiders": [259],
    "Black Waltz 1 + Sealion": [308],
    "Black Waltz 2": [451],
    "Black Waltz 3": [501],
    "Festival of the Hunt": [566],
    "Black Mages (Gizamaluke's Grotto)": [703],
    "Gizamaluke": [707],
    "Black Mages (Burmecia)": [752],
    "Beatrix 1": [768],
    "Black Waltz 3 (South Gate)": [811],
    "Ralvurahva": [954],
    "Antlion": [1052],
    "Alexandrian Soldiers (Cleyra)": [[1014, 1012, 1011, 1101], [1101, 1103], [1105]],
    "Beatrix 2": [1108],
    "Zorn & Thorn": [1205],
    "Beatrix 3": [1223],
    "Bandersnatch (first)": [1225],
    "Bandersnatches & Black Mages": [[1203, 1204, 1202], [1203]],
    "Ralvuimago": [955],
    "Lani": [1424],
    "Hilgigars": [1555],
    "Zombies & Dracozombie (Iifa elevator)": [1754],
    "Soulcage": [1756],
    "Scarlet Hair (Amarant)": [1605],
    "Mistodons (Iifa Tree)": [1663, 1655],
    "Mistodons (Alexandria)": [2053, 2051, 2050],
    "Ark": [2260],
    "Valia Pira": [2222],
    "Red Dragons": [2361],
    "Meltigemini": [2363],
    "Taharka": [2510],
    "Earth Guardian": [2553],
    "Amdusias": [2706],
    "Abadon": [2707],
    "Shell Dragon": [2708],
    "Silver Dragon": [2715],
    "Garland": [2715],
    "Kuja": [2716],
    "Nova Dragon": [2756],
    "Maliris": [2904],
    "Tiamat": [2908],
    "Kraken": [2915],
    "Lich": [2919],
    "Deathguise": [2926],
    "Trance Kuja": [2927],
    "Necron": [2928],
}

# The enemies' HP seen in fights that have none typed above (each line-up).
SEEN_HP: dict[str, list[list[int]]] = {
    "Festival of the Hunt": [[201]],
    "Zombies & Dracozombie (Iifa elevator)": [[973, 973, 973, 1066], [2179]],
}

FIELDS = ("hp", "hp_names", "fights", "lv5", "groups")


def _split(key: str) -> tuple[str | None, int | None]:
    # "fight:Mistodons (Iifa Tree)" -> (name, None); "fight:Alexandrian Soldiers (Cleyra)@1" -> (name, 1)
    if not key.startswith("fight:"):
        return None, None
    name, _, kind = key[len("fight:"):].partition("@")
    return name, (int(kind) if kind.isdigit() else None)


def known_screens(key: str) -> list[int]:
    """The rooms a fight from the list starts from, built in (what a fight is learned under)."""
    name, kind = _split(key)
    got = STARTS.get(name) or []
    if got and isinstance(got[0], list):                   # kinds of fight: one list each
        return list(got[kind]) if kind is not None and kind < len(got) else []
    return list(got) if kind is None else []


def known_formations(key: str) -> list[list[int]]:
    name, kind = _split(key)
    return [list(f) for f in SEEN_HP.get(name) or []] if kind is None else []


def custom() -> list[dict]:
    try:
        data = json.loads(CUSTOM.read_text(encoding="utf-8-sig"))
        return [f for f in data if isinstance(f, dict) and f.get("name")]
    except (OSError, ValueError):
        return []


def save_custom(fight: dict) -> None:
    """Add (or replace, by name) a fight in your own list."""
    mine = [f for f in custom() if f["name"].lower() != fight["name"].lower()]
    mine.append(fight)
    CUSTOM.write_text(json.dumps(mine, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def all_fights() -> list[dict]:
    """The ready-made fights, then yours (marked)."""
    return FIGHTS + [dict(f, mine=True) for f in custom()]


def learned_for(learned, name: str) -> tuple[list[int] | None, int | None]:
    """What's known about a fight from the list: its HP (learned on any route, else built
    in) and the screen it starts from (the last learned, else the list's own first)."""
    base = f"fight:{name}"
    built = STARTS.get(name) or []
    built = [f for kind in built for f in kind] if built and isinstance(built[0], list) else list(built)
    seen = known_formations(base)
    if learned is None:
        return (seen[0] if seen else None), (built[0] if built else None)
    mine = lambda k: k == base or k.startswith(base + "#") or k.startswith(base + "@")
    hp = learned.formation(base) or (seen[0] if seen else None)
    screens = [f for k, v in (learned.data.get("battle_screens") or {}).items() if mine(k) for f in v or []]
    return (list(hp) if hp else None), (screens[-1] if screens else built[0] if built else None)


def label(f: dict) -> str:
    return f["name"]                           # (the list only shows the step's disc)


def apply(step: dict, f: dict, learned=None) -> None:
    """Set a fight step to a fight from the list: only its HP (typed in the list, else
    learned on a run) and the screen it starts from (learned on a run). The step's name,
    actions, fight count and notes stay as they are; the step learns as that fight from
    now on, for every route."""
    hp, screen = learned_for(learned, f["name"])
    step["preset"] = f["name"]
    for key in ("hp", "hp_names", "groups"):
        step.pop(key, None)
    if f.get("groups"):
        step["groups"] = json.loads(json.dumps(f["groups"]))
    elif f.get("hp"):
        step["hp"] = list(f["hp"])
        if f.get("hp_names"):
            step["hp_names"] = dict(f["hp_names"])
    elif hp:
        step["hp"] = hp
    if screen is not None:
        step["screen"] = screen
    else:
        step.pop("screen", None)
