"""The route language: what each line on a walk or menu step means.

Lines are plain text. A few fixed starts make a line something the coach checks;
anything else is a note it just shows.

    1x Tent (left inside the chapel)      pickup: an item, picked up       (x1 Tent works too)
    2700x Gil (behind the guard)          pickup: gil      (?x Gil when you don't know how much)
    2x Any item (back right, front)       pickup: any two items (hidden items)
    Buy 2 Mage Masher, 1 Glass Armlet     shop: bag gains these
    Buy 5 Steepled Hat (4 if you stole one)   shop: own 5 in total
    Sell all Ether, Eye Drops             shop: none left        ("extra Wrist": fewer than before)
    Synth 1 Exploda                       synth: bag gains it
    Zidane: Coral Ring, -Leather Hat      equip: put on / take off (dash = take off, shown red)
    Steiner: Bird Killer, -Man Eater      abilities: switch on / off
    Freya: Optimize                       equip: her gear changes
    Freya front row / Steiner and Freya back row      row
    Battle Speed: Fast / Cursor: Memory   config: FF9's Config menu

On fight cards, a line with "Steal <item>" gets a check mark once you have it.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

PLAYABLE = ("zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant")
GUESTS = ("blank", "marcus", "beatrix", "cinna")   # share one record in the game: whoever is with you
# "-Head" etc. take off whatever is in that slot (Weapon can't be emptied)
SLOTS = {"head": [1], "helm": [1], "hat": [1], "arm": [2], "arms": [2], "wrist": [2], "armlet": [2],
         "armor": [3], "armour": [3], "body": [3], "acc": [4], "accessory": [4], "all": [1, 2, 3, 4]}

# FF9's Config menu settings. "Battle Speed: Fast" is a config line.
CONFIG_SETTINGS = ("Sound", "Window Color", "Target Window", "Target", "Window", "Battle Mode", "Battle Speed", "Speed",
                   "Battle Camera", "Camera", "Cursor", "Field Message", "Message", "Movement",
                   "Vibration", "Skill Help", "Help", "ATB", "Sound Effects", "Music", "Title", "Controller")
CONFIG_ALIASES = {"Camera": "Battle Camera", "Speed": "Battle Speed", "Message": "Field Message",
                  "Target": "Target Window", "Help": "Skill Help"}
_CONFIG_LINE = re.compile(r"^\s*(?:config\s*:\s*)?(" + "|".join(re.escape(c) for c in
                                                       sorted(CONFIG_SETTINGS, key=len, reverse=True))
                          + r")\s*:\s*(.+?)\s*$", re.I)


_OPTIONAL = re.compile(r"\(\s*(?:only\s+|skip\s+(?:it\s+)?)?(?:if|unless|when)\b|\(\s*(?:optional|safety|maybe)\b", re.I)


@functools.lru_cache(maxsize=4096)
def is_optional(line: str) -> bool:
    """A line whose note makes it depend on the run: "(if stolen)", "(only if Lv 18)",
    "(skip if nobles)", "(optional)". It's ticked when done, but the step doesn't wait for it."""
    return bool(_OPTIONAL.search(line))


def instructions_after_checks(lines: list[str]) -> bool:
    """Whether a step has things to do written after its last checked line ("1x Ether",
    then "Talk to Vivi, exit"): ticking the checked lines doesn't finish it then.
    Comments, @ lines, gaps, "(if ...)" lines and indented detail lines don't count."""
    last, notes = None, []
    for i, line in enumerate(expand_headers(lines)):
        if not line.strip() or is_indented(line) or not_checked(line):
            continue
        info = classify(line)
        if info.kind == "note":
            notes.append(i)
        elif info.checked and not info.problem and not is_optional(line):
            last = i
    return last is not None and any(i > last for i in notes)


def is_comment(line: str) -> bool:
    """A line starting with # is a small comment under the line above (gear after an
    Optimize, reminders): shown, never checked."""
    return line.strip().startswith("#")


def comment_text(line: str) -> str:
    return line.strip().lstrip("#").strip()


def is_ap_line(line: str) -> bool:
    """A line starting with @ asks for an AP readout: "@Zidane: Protect Girls 17"."""
    return line.strip().startswith("@")


def not_checked(line: str) -> bool:
    """Comments and AP readouts: shown, never ticked off."""
    return is_comment(line) or is_ap_line(line)


_AP_CHARS = ("zidane", "vivi", "garnet", "dagger", "steiner", "freya", "quina", "eiko", "amarant")


_LEVEL_LINE = re.compile(r"^@\s*(?:lv|level)\b\s*:?\s*(.*)$", re.I)
_LEVEL_NAMES = _AP_CHARS + ("blank", "marcus", "cinna", "beatrix")


def level_requests(line: str) -> list[tuple[str, int | None]] | None:
    """What an @Lv line asks for: [(character, level wanted or None)]; [] for everyone.

        @Lv                    everyone you have
        @Lv Zidane, Vivi       their levels
        @Lv Zidane 15          Zidane's level, ticked at 15

    None if it isn't an @Lv line; a name it doesn't know is left out (the editor says)."""
    m = _LEVEL_LINE.match(line.strip())
    if not m:
        return None
    out = []
    for part in re.split(r",|&|\band\b", m.group(1)):
        mm = re.match(r"^\s*([a-z]+)\s*(\d+)?\s*$", part, re.I)
        if mm and mm.group(1).lower() in _LEVEL_NAMES:
            who = "garnet" if mm.group(1).lower() == "dagger" else mm.group(1).lower()
            out.append((who, int(mm.group(2)) if mm.group(2) else None))
    return out


def level_problem(line: str) -> str | None:
    m = _LEVEL_LINE.match(line.strip())
    if not m:
        return None
    bad = [p.strip() for p in re.split(r",|&|\band\b", m.group(1))
           if p.strip() and not re.match(r"^\s*(" + "|".join(_LEVEL_NAMES) + r")\s*(\d+)?\s*$", p, re.I)]
    return ("not a character: " + ", ".join(bad)) if bad else None


def ap_requests(line: str) -> list[tuple[str, str, int | None]] | None:
    """What an @ line asks for: [(character, ability, target AP or None)].

        @Zidane: Protect Girls 17      Zidane's Protect Girls, wanted at 17 AP
        @Zidane, Freya: Man Eater      both of theirs, until learned
        @Protect Girls                 everyone who can learn it

    Abilities a character can't learn are left out (the editor flags them)."""
    if not is_ap_line(line) or _LEVEL_LINE.match(line.strip()):
        return None
    from ability_data import ABILITY_LISTS
    body = line.strip()[1:].strip()
    body = re.sub(r"^ap\b\s*:?\s*", "", body, flags=re.I)
    who: list[str] = []
    m = re.match(r"^([a-z ,&]+?)\s*:\s*(.+)$", body, re.I)
    if m and all(w.strip().lower() in _AP_CHARS for w in re.split(r",|&|\band\b", m.group(1)) if w.strip()):
        who = ["garnet" if w.strip().lower() == "dagger" else w.strip().lower()
               for w in re.split(r",|&|\band\b", m.group(1)) if w.strip()]
        body = m.group(2)
    elif not m:
        # "@Amarant Bird Killer" (colon left out): a character's name first still counts
        m2 = re.match(r"^(" + "|".join(_AP_CHARS) + r")\s+(.+)$", body, re.I)
        if m2:
            who = ["garnet" if m2.group(1).lower() == "dagger" else m2.group(1).lower()]
            body = m2.group(2)
    def key(t):
        return re.sub(r"[^a-z0-9+%]", "", t.lower().replace("'s", ""))
    out = []
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        t = re.match(r"^(.*?)\s*(?:(\d+)\s*(?:ap)?)?$", part, re.I)
        name, target = t.group(1).strip(), int(t.group(2)) if t.group(2) else None
        for char in (who or list(ABILITY_LISTS)):
            entry = next((e for e in ABILITY_LISTS.get(char, []) if e and key(e[0]) == key(name)), None)
            if entry:
                out.append((char, entry[0], target))
    return out


@functools.lru_cache(maxsize=4096)
def config_check(line: str) -> tuple[str, str] | None:
    """("Battle Speed", "Fast") for a Config menu line, else None."""
    if not_checked(line):
        return None
    m = _CONFIG_LINE.match(line)
    if not m:
        return None
    setting = next(c for c in CONFIG_SETTINGS if c.lower() == m.group(1).lower())
    setting = CONFIG_ALIASES.get(setting, setting)     # "Speed: Max" is Battle Speed
    return setting, re.sub(r"\s*\(.*\)$", "", m.group(2)).strip()


HELP = [
    ("1x Tent (where)", "pickup", "an item to pick up; x1 Tent works too"),
    ("2700x Gil (where)", "pickup", "gil to pick up (?x Gil if unsure)"),
    ("2x Any item (where)", "pickup", "any items (hidden ones you don't know)"),
    ("Buy 2 Mage Masher, 1 Glass Armlet", "buy", "the bag gains them"),
    ("Buy 5 Steepled Hat (4 if you stole one)", "buy", "own that many in total"),
    ("Sell all Ether, Eye Drops", "sell", "none left; 'extra Wrist' = fewer"),
    ("Synth 1 Exploda", "synth", "the bag gains it"),
    ("Zidane: Coral Ring, -Leather Hat", "equip", "on / off (dash = take off, red)"),
    ("Steiner: Bird Killer, -Man Eater", "equip", "abilities on / off"),
    ("Freya: Optimize", "equip", "ticks when her gear changes"),
    ("Freya front row", "row", "also: Steiner and Freya back row"),
    ("Battle Speed: Fast", "config", "a Config menu setting"),
    ("Steal Chain Plate (on a fight card)", "steal", "ticks once you have it"),
    ("anything else", "note", "shown as it is"),
]


_STEAL_LINE = re.compile(r"\bsteal\b\s*(.*)", re.I)


def steal_targets(turns: list[str]) -> list[tuple[int, str | None]]:
    """(line index, item) for each fight-card line that says to steal. The item is
    None when the line doesn't name one ("Soul Blade, then steal")."""
    from screen import match_item
    out = []
    for i, line in enumerate(turns):
        if not_checked(line):
            continue
        m = _STEAL_LINE.search(line)
        if m:
            rest = re.split(r"[,.;(]| then | until | x\d", m.group(1), maxsplit=1)[0]
            out.append((i, match_item(rest) if rest.strip() else None))
    return out



_NOT_ITEMS = {"Garnet"}        # also a character's name
_ITEM_PATTERNS: list[tuple[str, re.Pattern]] | None = None


def _item_patterns():
    global _ITEM_PATTERNS
    if _ITEM_PATTERNS is None:
        from screen import ITEMS, KEY_ITEMS
        names = sorted(set(ITEMS + KEY_ITEMS) - _NOT_ITEMS, key=len, reverse=True)     # longest first
        pats = []
        for n in names:
            words = [re.escape(w) for w in re.sub(r"'s\b", "", n).replace("'", "").split()]
            pats.append((n, re.compile(r"\b" + r"(?:'?s)?\s+".join(words) + r"(?:'s|s|es)?\b", re.I)))
        _ITEM_PATTERNS = pats
    return _ITEM_PATTERNS




_PICKUP = re.compile(r"^\s*(?:(\d[\d,]*|\?)\s*x|x\s*(\d+))\s+(.+?)\s*$", re.I)
_GIL_PICKUP = re.compile(r"^\s*(?:\d[\d,]*|\?)\s*x?\s*gill?\b", re.I)
_ANY = re.compile(r"^(any|hidden)(\s+items?)?$", re.I)


@functools.lru_cache(maxsize=4096)
def find_item(name: str) -> str | None:
    """The item a name means ("Ethers", "Magician's Shoes", "Yellow Scarves"), or None."""
    name = re.sub(r"\s+", " ", name.strip())
    if not name:
        return None
    for tryname in (name, "The " + name):              # "Ogre" is The Ogre
        for n, pat in _item_patterns():
            if pat.fullmatch(tryname) or pat.fullmatch(re.sub(r"ves$", "f", tryname)) \
                    or pat.fullmatch(tryname.replace("'", "")):
                return n
    # A small typo ("Phoenix Pinons", "Linen Curiass"): the closest name, if it's very close.
    import difflib
    from screen import ITEMS, KEY_ITEMS
    low = {i.lower(): i for i in ITEMS + KEY_ITEMS if i not in _NOT_ITEMS}
    for cand in (name.lower(), re.sub(r"e?s$", "", name.lower())):
        hit = difflib.get_close_matches(cand, low, n=1, cutoff=0.88)
        if hit:
            return low[hit[0]]
    return None


def pickup_items(line: str):
    got = _pickup_items(line)
    return list(got) if got is not None else None


@functools.lru_cache(maxsize=4096)
def _pickup_items(line: str) -> list[str] | None:
    """What a pickup line expects: "1x Tent" -> ["Tent"], "2x Ether" -> two Ethers,
    "2700x Gil" -> ["Gil"], "2x Any item" -> ["*", "*"]. None if it isn't a pickup line
    (or names something the coach doesn't know)."""
    if not_checked(line):
        return None
    if _GIL_PICKUP.match(line):
        return ["Gil"]                                  # "2000 Gil (left of the door)"
    m = _PICKUP.match(line)
    if not m:
        return None
    num = (m.group(1) or m.group(2)).replace(",", "")
    n = 1 if num == "?" else int(num)                    # "?x Gil": an amount you don't know
    name = re.sub(r"\s*\(.*$", "", m.group(3)).strip()      # "(where it is)" is a note
    if re.fullmatch(r"gill?", name, re.I):
        return ["Gil"]
    if _ANY.match(name):
        return ["*"] * min(n, 20)
    item = find_item(name)
    if item is None:
        return None
    return [item] * min(n, 99)


def pickup_targets(lines: list[str]) -> list[tuple[int, list[str]]]:
    """(line index, items expected) for each pickup line."""
    out = []
    for i, line in enumerate(lines):
        items = pickup_items(line)
        if items:
            out.append((i, items))
    return out


_CHARS = "zidane|vivi|garnet|steiner|freya|quina|eiko|amarant|dagger|beatrix|blank|marcus|cinna"
_ROW_LINE = re.compile(rf"^((?:(?:{_CHARS})(?:\s*,\s*|\s+and\s+|\s*&\s*)?)+)\s*:?\s+(?:to\s+(?:the\s+)?)?(front|back)(?:\s+row)?\s*$", re.I)


_ROW_FIRST = re.compile(rf"^(front|back)\s+row\s*:?\s+((?:(?:{_CHARS})(?:\s*,\s*|\s+and\s+|\s*&\s*)?)+)\s*$", re.I)


@functools.lru_cache(maxsize=4096)
def row_check(line: str) -> tuple[list[str], str] | None:
    """(characters, "front"/"back") for "Freya front row", "Steiner and Freya back row",
    "Front row Zidane"."""
    if not_checked(line):
        return None
    f = _ROW_FIRST.match(line.strip())
    if f:
        who = [w.lower().replace("dagger", "garnet") for w in re.findall(_CHARS, f.group(2), re.I)]
        return who, f.group(1).lower()
    m = _ROW_LINE.match(line.strip())
    if not m:
        return None
    who = [w.lower().replace("dagger", "garnet") for w in re.findall(_CHARS, m.group(1), re.I)]
    return who, m.group(2).lower()


_OPTIMIZE_LINE = re.compile(r"^(zidane|vivi|garnet|steiner|freya|quina|eiko|amarant):\s*optimi[sz]e[\s,.;]*(?:\([^)]*\)\s*)?$", re.I)
_MENU_LINE = re.compile(r"^(zidane|vivi|garnet|steiner|freya|quina|eiko|amarant|blank|marcus|beatrix|cinna|dagger)( off)?:\s*(.+)$", re.I)


@functools.lru_cache(maxsize=1)
def _name_maps() -> tuple[dict, dict]:
    from screen import ITEMS, ABILITIES
    def key(t):
        return re.sub(r"[^a-z0-9+%]", "", t.lower().replace("'s", ""))
    return {key(i): i for i in ITEMS}, {key(a): a for a in ABILITIES}


@functools.lru_cache(maxsize=4096)
def menu_checks(line: str) -> tuple[str, bool, list[tuple[str, bool]], list[tuple[str, bool]]] | None:
    """(character, whole line is take-off?, [(equipment, take off?)], [(ability, take off?)])
    a menu line asks for, or None if nothing in it can be checked. "-Silk Shirt" means
    take it off (so does the older "Zidane off: ..."). "Optimize" and notes in brackets
    are skipped; "Steiner, in order: ..." counts as Steiner."""
    if not_checked(line):
        return None
    from screen import ITEMS, ABILITIES
    m = _MENU_LINE.match(re.sub(r",? in order|, then", "", line.strip()))
    if not m:
        return None
    who, off, rest = m.group(1).lower(), bool(m.group(2)), re.sub(r"\([^)]*\)", "", m.group(3))
    who = "garnet" if who == "dagger" else who
    def key(t):                    # "Magician's Shoes" and "Magician Shoes" are the same item
        return re.sub(r"[^a-z0-9+%]", "", t.lower().replace("'s", ""))
    items, abil = _name_maps()
    eq, ab = [], []
    for tok in (t.strip() for t in rest.split(",")):
        minus = tok.startswith("-")
        k = key(tok.lstrip("-"))
        if minus and k in SLOTS and k not in items:
            eq += [(f"slot:{n}", True) for n in SLOTS[k]]     # that slot left empty
        elif k in items:
            eq.append((items[k], off or minus))
        elif k in abil:
            ab.append((abil[k], off or minus))
    return (who, off, eq, ab) if (eq or ab) else None


_REMOVE_TOKEN = re.compile(r"(^|[:,]\s*)-(?=[A-Za-z])([^,]+)")


def line_segments(line: str) -> list[tuple[str, bool]]:
    """A line split into (text, taken off?) pieces for drawing: "-Silk Shirt" is a piece
    to show in red (without the dash)."""
    old = re.match(r"^([A-Za-z][\w' ]*? off:\s*)(.+)$", line)
    if old:                                        # older "Zidane off: ..." lines: all of it
        return [(old.group(1), False), (old.group(2), True)]
    out, pos = [], 0
    for m in _REMOVE_TOKEN.finditer(line):
        start = m.start(2) - 1                     # the dash
        if start > pos:
            out.append((line[pos:start], False))
        out.append((m.group(2), True))
        pos = m.end(2)
    if pos < len(line):
        out.append((line[pos:], False))
    return out or [(line, False)]


def old_off_line(line: str) -> str:
    """The older "Zidane off: Silk Shirt, Leather Hat" written the new way:
    "Zidane: -Silk Shirt, -Leather Hat"."""
    m = re.match(r"^([A-Za-z][\w' ]*?) off:\s*(.+)$", line.strip())
    if not m:
        return line
    parts = [p.strip() for p in m.group(2).split(",")]
    return f"{m.group(1)}: " + ", ".join(p if p.startswith("-") else "-" + p for p in parts if p)


_SHOP_LINE = re.compile(r"^\s*(buy|sell|synth|synthesi[sz]e)\b\s*(.*)$", re.I)


@functools.lru_cache(maxsize=4096)
def shop_check(line: str) -> tuple[str, list[tuple[str, int, str]]] | None:
    """("buy"/"sell", [(item, how many, rule)]) for a Buy/Sell line, None if nothing in it
    can be checked. Rules: "add" (bag gains n), "total" (bag holds n: "4 if you stole one"),
    "all" (none left), "some" (fewer than before: "extra Wrists")."""
    if not_checked(line):
        return None
    m = _SHOP_LINE.match(line)
    if not m:
        return None
    kind, rest = m.group(1).lower(), m.group(2)
    if kind.startswith("synth"):
        kind = "buy"                             # synthesising adds to the bag just like buying
    rest = re.split(r"\bexcept\b", rest, flags=re.I)[0]     # "Sell gems except Diamond": can't check
    if re.search(r"\b(everything|else|max|all (weapons|tops|armou?r))\b", rest, re.I):
        rest = re.split(r",", rest)                  # check only the parts that name items plainly
        rest = ", ".join(p for p in rest if not re.search(r"\b(except|everything|else|max)\b", p, re.I)
                         and not re.search(r"\ball (weapons|tops|armou?r)\b", p, re.I))
    notes = " ".join(re.findall(r"\(([^)]*)\)", rest))
    rest = re.sub(r"\([^)]*\)", "", rest)
    stolen = bool(re.search(r"\bstole|\bstolen", notes, re.I)) and kind == "buy"
    parts = []
    for chunk in re.split(r",|\bor\b|\band\b", rest):
        chunk = chunk.strip()
        if not chunk:
            continue
        # "2 Mage Masher", "2x Mage Masher", "1-3 Wrist", "?x Hi-Potion" (any number: at least one)
        nm = re.match(r"(all\s+|extra\s+)?(?:(\d+|\?)(?:\s*-\s*\d+)?\s*x?\s+|an?\s+)?(.*)$", chunk, re.I)
        mod, num, name = (nm.group(1) or "").strip().lower(), nm.group(2), nm.group(3)
        if num == "?":
            num = None
        name = name.strip()
        item = find_item(name)
        if item is None:
            continue
        from screen import ITEMS
        if item not in ITEMS:
            continue
        n = int(num) if num else 1
        if kind == "sell":
            # "Sell all Ether": none left. "Sell 2 Cotton Robe": two fewer than before.
            rule = "some" if mod == "extra" else "less" if num and mod != "all" else "all"
        else:
            rule = "total" if stolen else "add"
            if stolen and kind == "buy" and re.search(r"\d", notes) and "unless" not in notes.lower():
                rule = "total"                     # "Buy 5 Steepled Hats (4 if you stole one)": own 5
        parts.append((item, n, rule))
    return (kind, parts) if parts else None




def is_indented(line: str) -> bool:
    """An indented line belongs to the line above it: no bullet of its own, and in a
    fight it isn't a turn of its own."""
    return line[:1] in (" ", "\t") and bool(line.strip())


def turn_groups(lines: list[str]) -> list[tuple[int, int]]:
    """(first line, end) for each action in a fight: a line plus the indented lines under it."""
    out = []
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        if (is_indented(l) or not_checked(l)) and out:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


_HEADER = re.compile(r"^\s*(buy|sell|synth|synthesi[sz]e)\s*(\([^)]*\))?\s*:?\s*$", re.I)
_KEEP_HEADER = re.compile(r"^\s*(?:sell\s+(?:all|everything)\s+(?:except|but)|keep)\s*:?\s*$", re.I)
_KEEP_LINE = re.compile(r"^\s*sell\s+(?:all|everything)\s+(?:except|but)\s*:?\s*(.+)$", re.I)


def sell_except(line: str):
    got = _sell_except(line)
    return list(got) if got is not None else None


@functools.lru_cache(maxsize=4096)
def _sell_except(line: str) -> list[str] | None:
    """"Sell all except: Hi-Potion, Ether, Elixir": the items to keep (everything else in
    the bag should be sold). None if the line isn't one."""
    if not_checked(line):
        return None
    m = _KEEP_LINE.match(line.strip())
    if not m:
        return None
    keep = []
    for chunk in re.split(r",|/", re.sub(r"\([^)]*\)", "", m.group(1))):
        chunk = re.sub(r"^\s*(\d+\s*x?|x\s*\d+)\s*", "", chunk.strip())
        item = find_item(chunk) if chunk else None
        if item:
            keep.append(item)
    return keep or None


def expand_headers(lines: list[str]) -> list[str]:
    """Lines as the checks read them. A "Buy" / "Sell" / "Synth" line on its own heads the
    indented lines under it, so

        Buy
          2 Mage Masher
          1 Glass Armlet

    reads as "Buy 2 Mage Masher" and "Buy 1 Glass Armlet" (the header itself checks nothing)."""
    out, head, head_note = [], None, ""
    keep_at = None                             # "Sell all except" header: items go on its line
    for line in lines:
        if not_checked(line):
            out.append(line)                   # a comment doesn't end a Buy/Sell group
            continue
        if _KEEP_HEADER.match(line):
            head, keep_at = None, len(out)
            out.append("Sell all except:")
            continue
        if keep_at is not None and is_indented(line):
            out[keep_at] += (" " if out[keep_at].endswith(":") else ", ") + line.strip()
            out.append("")
            continue
        keep_at = None
        m = _HEADER.match(line)
        if m:
            head = m.group(1).capitalize()
            head_note = m.group(2) or ""       # "Buy (if nobles)": goes on each item under it
            out.append("")
            continue
        if is_indented(line) and head:
            out.append(f"{head} {line.strip()}" + (f" {head_note}" if head_note and "(" not in line else ""))
            continue
        if line.strip():
            head = None                        # a line that isn't indented ends the group
        out.append(line)
    return out


@dataclass
class LineInfo:
    kind: str                      # pickup, buy, sell, synth, equip, row, config, note
    checked: bool = True           # False: the right kind, but the coach can't check it
    problem: str = ""              # why not, for the editor


_CALL = re.compile(r"^\s*\[([^\[\]]+)\]\s*$")


def substep_call(line: str) -> str | None:
    """"[Baku]": where the sub-step called Baku comes in this step (its title), or None."""
    m = _CALL.match(line)
    return m.group(1).strip() if m else None


def story_parts(line: str) -> list[str] | None:
    """A story line ("Ant Eater > Green Shirt > Mary": who to talk to or where to go, in
    order) split into its parts; None if it isn't one."""
    t = line.strip()
    if ">" not in t or t.startswith(("#", "@")):
        return None
    parts = [p.strip() for p in t.split(">")]
    if not parts[-1]:                              # "Guard > Exit >": goes on on the next line
        parts.pop()
    return parts if len(parts) >= 2 and all(parts) else None


@functools.lru_cache(maxsize=4096)
def classify(line: str) -> LineInfo:
    """What kind of line this is, for the editor's colours and warnings."""
    t = line.strip()
    if not t:
        return LineInfo("note", False)
    if is_comment(t):
        return LineInfo("comment", False)
    if substep_call(t):
        return LineInfo("substep", False)
    if story_parts(t):
        return LineInfo("story", False)
    if _LEVEL_LINE.match(t):
        problem = level_problem(t)
        return LineInfo("ap", not problem, problem)
    if is_ap_line(t):
        if ap_requests(t):
            return LineInfo("ap")
        return LineInfo("ap", False, "no character can learn that ability")
    m = _HEADER.match(t)
    if m:
        kind = m.group(1).lower()
        return LineInfo("synth" if kind.startswith("synth") else kind)    # a header: fine as it is
    if config_check(t):
        return LineInfo("config")
    if _PICKUP.match(t) or _GIL_PICKUP.match(t):
        if pickup_items(t):
            return LineInfo("pickup")
        return LineInfo("pickup", False, "item name not recognised")
    if _KEEP_HEADER.match(t) or _KEEP_LINE.match(t):
        if _KEEP_HEADER.match(t) or sell_except(t):
            return LineInfo("sell")
        return LineInfo("sell", False, "no items to keep recognised")
    m = _SHOP_LINE.match(t)
    if m:
        kind = "synth" if m.group(1).lower().startswith("synth") else m.group(1).lower()
        if shop_check(t):
            return LineInfo(kind)
        return LineInfo(kind, False)               # "Sell everything else": shown, not checked
    if row_check(t):
        who, _ = row_check(t)
        return LineInfo("row")
    m = _MENU_LINE.match(re.sub(r",? in order|, then", "", t))
    if m:
        who = m.group(1).lower()
        if _OPTIMIZE_LINE.match(t) or menu_checks(t):
            unknown = _unknown_tokens(m.group(3))
            if unknown:
                return LineInfo("equip", True, "not recognised: " + ", ".join(unknown))
            return LineInfo("equip")
        return LineInfo("note", False)               # "Garnet: Cure Steiner": just a note
    return LineInfo("note", False)


@functools.lru_cache(maxsize=1)
def _known_names() -> frozenset:
    from screen import ITEMS, ABILITIES
    def key(t):
        return re.sub(r"[^a-z0-9+%]", "", t.lower().replace("'s", ""))
    return frozenset({key(x) for x in ITEMS + ABILITIES} | {"optimize", "optimise"} | set(SLOTS))


def _unknown_tokens(rest: str) -> list[str]:
    from screen import ITEMS, ABILITIES
    def key(t):
        return re.sub(r"[^a-z0-9+%]", "", t.lower().replace("'s", ""))
    known = _known_names()
    rest = re.sub(r"\([^)]*\)", "", rest)
    return [tok.strip() for tok in rest.split(",") if tok.strip() and key(tok.strip().lstrip("-")) not in known]
