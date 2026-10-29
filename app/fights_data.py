"""Ready-made fights for the editor's fight list: every boss and forced fight on the way
through the game, in story order, with the HP the coach recognises them by.

HP is the number the enemy really has (what you'd see with Scan); fights that end on a
script store it with 10,000 added, and the coach takes that off by itself. Values come
from routes played on PSX and from fights the coach recorded on real runs.
Fights you save from the editor go in fights_custom.json (next to FFIX Coach.pyw).
"""

from __future__ import annotations

import json
from pathlib import Path

from paths import FIGHTS_CUSTOM as CUSTOM

# name, disc, hp (enemy HP the fight is known by), hp names, extra fields
FIGHTS: list[dict] = [
    # Disc 1
    {"name": "Masked Man", "disc": 1, "hp": [188], "hp_names": {"188": "Masked Man"}},
    {"name": "King Leo", "disc": 1, "hp": [186], "hp_names": {"186": "King Leo"}},
    {"name": "Steiner 1", "disc": 1, "hp": [169], "hp_names": {"169": "Steiner"}},
    {"name": "Steiner 2", "disc": 1, "hp": [162], "hp_names": {"162": "Steiner"}},
    {"name": "Steiner 3 + Bomb", "disc": 1, "hp": [162, 38], "hp_names": {"162": "Steiner", "38": "Bomb"}},
    {"name": "Prison Cage 1", "disc": 1, "hp": [513], "hp_names": {"513": "Prison Cage"}},
    {"name": "Prison Cage 2", "disc": 1, "hp": [533], "hp_names": {"533": "Prison Cage"}},
    {"name": "Baku", "disc": 1, "hp": [202], "hp_names": {"202": "Baku"}},
    {"name": "Plant Brain", "disc": 1, "hp": [916, 510], "hp_names": {"916": "Plant Brain", "510": "Plant Brain"}},
    {"name": "Plant Spiders", "disc": 1, "hp": [33], "hp_names": {"33": "Plant Spider"}},
    {"name": "Black Waltz 1 + Sealion", "disc": 1, "hp": [229, 472],
     "hp_names": {"229": "Black Waltz 1", "472": "Sealion"}},
    {"name": "Black Waltz 2", "disc": 1, "hp": [1030], "hp_names": {"1030": "Black Waltz 2"}},
    {"name": "Black Waltz 3", "disc": 1, "hp": [1128], "hp_names": {"1128": "Black Waltz 3"}},
    {"name": "Festival of the Hunt", "disc": 1},
    {"name": "Black Mages (Gizamaluke's Grotto 1)", "disc": 1, "hp": [398], "hp_names": {"398": "Black Mage"}},
    {"name": "Black Mages (Gizamaluke's Grotto 2)", "disc": 1, "hp": [398], "hp_names": {"398": "Black Mage"}},
    {"name": "Gizamaluke", "disc": 1, "hp": [3175], "hp_names": {"3175": "Gizamaluke"}},
    {"name": "Black Mages (Burmecia)", "disc": 1, "hp": [398], "hp_names": {"398": "Black Mage"}},
    {"name": "Beatrix 1", "disc": 1, "hp": [3630], "hp_names": {"3630": "Beatrix"}},
    # Disc 2
    {"name": "Black Waltz 3 (South Gate)", "disc": 2, "hp": [1292], "hp_names": {"1292": "Black Waltz 3"}},
    {"name": "Ralvurahva", "disc": 2, "hp": [2296], "hp_names": {"2296": "Ralvurahva"}},
    {"name": "Antlion", "disc": 2, "hp": [3938], "hp_names": {"3938": "Antlion"}},
    {"name": "Alexandrian Soldiers (Cleyra)", "disc": 2, "fights": 3},
    {"name": "Beatrix 2", "disc": 2, "hp": [4736], "hp_names": {"4736": "Beatrix"}},
    {"name": "Zorn & Thorn", "disc": 2, "hp": [2984, 4896], "hp_names": {"2984": "Thorn", "4896": "Zorn"}},
    {"name": "Beatrix 3", "disc": 2, "hp": [5709], "hp_names": {"5709": "Beatrix"}},
    {"name": "Bandersnatch (first)", "disc": 2, "hp": [899], "hp_names": {"899": "Bandersnatch"}},
    {"name": "Bandersnatches & Black Mages", "disc": 2, "hp": [899], "hp_names": {"899": "Bandersnatch"},
     "fights": 3},
    {"name": "Ralvuimago", "disc": 2, "hp": [3352], "hp_names": {"3352": "Ralvuimago"}},
    {"name": "Lani", "disc": 2, "hp": [5708], "hp_names": {"5708": "Lani"}},
    {"name": "Hilgigars", "disc": 2, "hp": [8106], "hp_names": {"8106": "Hilgigars"}},
    {"name": "Zombies & Dracozombie (Iifa elevator)", "disc": 2, "fights": 2},
    {"name": "Soulcage", "disc": 2, "hp": [9738], "hp_names": {"9738": "Soulcage"},
     "lv5": ["zidane", "vivi", "garnet", "eiko"]},
    {"name": "Scarlet Hair (Amarant)", "disc": 2, "hp": [8985], "hp_names": {"8985": "Amarant"}},
    {"name": "Mistodons (Iifa Tree)", "disc": 2, "hp": [1473], "hp_names": {"1473": "Mistodon"}, "fights": 4},
    # Disc 3
    {"name": "Mistodons (Alexandria)", "disc": 3, "hp": [1473], "hp_names": {"1473": "Mistodon"}, "fights": 4},
    {"name": "Ark", "disc": 3, "hp": [20002], "hp_names": {"20002": "Ark"}},
    {"name": "Valia Pira", "disc": 3, "hp": [12120], "hp_names": {"12120": "Valia Pira"}},
    {"name": "Red Dragons", "disc": 3, "hp": [8000], "hp_names": {"8000": "Red Dragon"}},
    {"name": "Meltigemini", "disc": 3, "hp": [24348], "hp_names": {"24348": "Meltigemini"}},
    {"name": "Taharka", "disc": 3, "hp": [29816, 29186], "hp_names": {"29816": "Taharka", "29186": "Taharka"}},
    {"name": "Earth Guardian", "disc": 3, "hp": [20756], "hp_names": {"20756": "Earth Guardian"}},
    {"name": "Amdusias", "disc": 3, "hp": [5463], "hp_names": {"5463": "Amdusias"}},
    {"name": "Abadon", "disc": 3, "hp": [10926], "hp_names": {"10926": "Abadon"}},
    {"name": "Shell Dragon", "disc": 3, "hp": [10921], "hp_names": {"10921": "Shell Dragon"}},
    {"name": "Silver Dragon", "disc": 3, "hp": [24055], "hp_names": {"24055": "Silver Dragon"}},
    {"name": "Garland", "disc": 3, "hp": [47028, 40728], "hp_names": {"47028": "Garland", "40728": "Garland"}},
    {"name": "Kuja", "disc": 3, "hp": [42382], "hp_names": {"42382": "Kuja"}},
    # Disc 4
    {"name": "Nova Dragon", "disc": 4, "hp": [54941], "hp_names": {"54941": "Nova Dragon"}},
    {"name": "Maliris", "disc": 4, "hp": [59492, 59497], "hp_names": {"59492": "Maliris", "59497": "Maliris"}},
    {"name": "Tiamat", "disc": 4, "hp": [59494], "hp_names": {"59494": "Tiamat"}},
    {"name": "Kraken", "disc": 4, "hp": [55536], "hp_names": {"55536": "Kraken"}},
    {"name": "Lich", "disc": 4, "hp": [58554], "hp_names": {"58554": "Lich"}},
    {"name": "Deathguise", "disc": 4, "hp": [55535], "hp_names": {"55535": "Deathguise"},
     "lv5": ["zidane", "steiner", "freya", "amarant"]},
    {"name": "Trance Kuja", "disc": 4, "hp": [55535], "hp_names": {"55535": "Trance Kuja"}},
    {"name": "Necron", "disc": 4, "hp": [54100], "hp_names": {"54100": "Necron"}},
]

FIELDS = ("hp", "hp_names", "fights", "lv5", "groups")


def custom() -> list[dict]:
    try:
        data = json.loads(CUSTOM.read_text(encoding="utf-8"))
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


def label(f: dict) -> str:
    return f"{'Mine' if f.get('mine') else 'Disc ' + str(f.get('disc', '?'))} · {f['name']}"


def apply(step: dict, f: dict) -> None:
    """Set a fight step up from a fight in the list. Its name, actions and notes are left
    alone; only a brand-new step still called "New fight" (or nothing) takes the fight's name."""
    if not (step.get("title") or "").strip() or step.get("title") == "New fight":
        step["title"] = f["name"]
    step["preset"] = f["name"]
    for key in FIELDS:
        if f.get(key):
            step[key] = json.loads(json.dumps(f[key]))
        else:
            step.pop(key, None)
