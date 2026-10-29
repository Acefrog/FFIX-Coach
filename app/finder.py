"""Find where things are in FFIX's memory and save them to config.json.

config.json already holds them for the US (NTSC-U) release; these are for other
releases, or if something doesn't read right. Run from the FFIX Coach folder with the
game open in DuckStation, e.g.  python app\finder.py check

    fights          when you're in a fight (Enter on the field, in fights, in the menu)
    battle          where HP lives (in a fight: type your party's HP, take a hit, again)
    check           show the HP the coach reads, live
    field           the current screen's number (walk between screens as it asks)
    stats zidane    a character's EXP and level (Status screen); after two characters
                    it works out the rest. Equipment, abilities and AP follow from these
    menu            when the menu is open
    equip zidane    where equipment is kept (Equip screen; type what's on)
    row freya       where the battle row is kept (Order screen: swap her row when asked)
    items           where the item bag is kept (in a shop: buy something, type its name)
    gil             where your gil is kept (type it, spend or get some, type it again)
    config          FF9's Config menu settings your route uses (Config menu open)
    zidane          which party position Zidane is in, if he isn't on top

Rarely needed (these are worked out from the character records already):
    ap zidane Flee      point one ability's AP at an address by hand
    ability zidane      find the support ability switches by hand
    battleid            FF9's battle number (fights are told apart by their enemies anyway)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from memory import DuckStationRam, MemoryError_, PS1_RAM_SIZE

from paths import CONFIG, write_atomic

import threading

STOP = threading.Event()        # set by Memory setup's Stop button


class Stopped(Exception):
    """Stop pressed in Memory setup."""


def _sleep(seconds: float) -> None:
    """time.sleep that Memory setup's Stop button can cut short."""
    if STOP.wait(seconds):
        raise Stopped()
MAX_FIELD_ID = 4096


def u16s(snapshot: bytes):
    return memoryview(snapshot).cast("H")


def scan(ram_bytes: bytes, value: int, width: int) -> list[int]:
    if width == 2:
        return [i * 2 for i, v in enumerate(u16s(ram_bytes)) if v == value]
    return [i for i, v in enumerate(ram_bytes) if v == value]


def narrow(ram_bytes: bytes, cands: list[int], value: int, width: int) -> list[int]:
    return [a for a in cands if int.from_bytes(ram_bytes[a:a + width], "little") == value]


def ask_int(prompt: str) -> int | None:
    while True:
        raw = input(prompt).strip()
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        if raw.isdigit():
            return int(raw)
        print("  Type a number (or q to quit).")


def wait(prompt: str) -> bool:
    return input(prompt + "  [Enter, or q to quit] ").strip().lower() not in {"q", "quit"}


def open_ram(cfg: dict) -> DuckStationRam:
    base = cfg.get("ram_base") or None
    return DuckStationRam(cfg.get("process_name", "duckstation"),
                          int(base, 16) if isinstance(base, str) else base)


# ----------------------------------------------------------------------
# Field ID
# ----------------------------------------------------------------------
def field_candidates(screens: list[list[bytes]]) -> list[int]:
    """screens[i] = snapshots taken on screen i (repeat visits share an index).

    Keeps 2-byte values that: hold steady on every visit to the same screen,
    are different for every pair of screens, and look like a small ID.
    """
    views = [[u16s(s) for s in shots] for shots in screens]
    n = len(views[0][0])
    out = []
    for i in range(n):
        vals = []
        ok = True
        for shots in views:
            v = shots[0][i]
            if v == 0 or v >= MAX_FIELD_ID or any(s[i] != v for s in shots[1:]):
                ok = False
                break
            vals.append(v)
        if ok and len(set(vals)) == len(vals):
            out.append(i * 2)
    return out


def find_field(ram: DuckStationRam) -> int | None:
    print("Finding the screen ID. Use a place with at least three connected screens")
    print("(a town is ideal). Stand still whenever you press Enter.\n")
    screens: list[list[bytes]] = []

    def snap_twice() -> list[bytes]:
        a = ram.snapshot()
        _sleep(0.5)
        return [a, ram.snapshot()]

    if not wait("1) Stand on a screen (call it A)."):
        return None
    screens.append(snap_twice())
    if not wait("2) Walk to the next screen (B)."):
        return None
    screens.append(snap_twice())
    if not wait("3) Walk back to screen A."):
        return None
    screens[0] += snap_twice()
    if not wait("4) Walk to screen B again."):
        return None
    screens[1] += snap_twice()
    if not wait("5) Walk to a third screen (C)."):
        return None
    screens.append(snap_twice())

    cands = field_candidates(screens)
    print(f"  {len(cands)} candidates")
    letter = ord("D")
    while len(cands) > 3:
        if not wait(f"Walk to another new screen ({chr(letter)})."):
            return None
        screens.append(snap_twice())
        cands = field_candidates(screens)
        print(f"  {len(cands)} candidates")
        letter += 1
    if not cands:
        print("Nothing consistent found. Try again in a different town, standing still at each Enter.")
        return None
    print("  " + ", ".join(f"0x{a:06X}" for a in cands))
    return cands[0]


# ----------------------------------------------------------------------
# Battle table (HP and ATB for all 8 slots)
# ----------------------------------------------------------------------
ATB_OFF = 4
MAX_ATB_STEP = 1500


MIN_STRIDE, MAX_STRIDE = 0x10, 0x800


def u16_at(snap: bytes, addr: int) -> int | None:
    if 0 <= addr and addr + 2 <= len(snap):
        return int.from_bytes(snap[addr:addr + 2], "little")
    return None


def find_hp_tables(snap: bytes, hps: list[int],
                   strides=range(MIN_STRIDE, MAX_STRIDE + 1, 2)) -> list[tuple[int, int]]:
    """Every (address of position 1's HP, stride) where the party's HPs sit in order
    at a constant spacing.

    hps = the HP of each party member, top of the list first (2 or more).
    The search starts from the highest HP, which is the rarest value in memory.
    """
    anchor = max(range(len(hps)), key=lambda k: hps[k])
    out = []
    for hit in scan(snap, hps[anchor], 2):
        for st in strides:
            base = hit - anchor * st
            if base < 0:
                continue
            if all(u16_at(snap, base + k * st) == hp for k, hp in enumerate(hps)):
                out.append((base, st))
    return out


def still_matching(snap: bytes, cands: list[tuple[int, int]], hps: list[int]) -> list[tuple[int, int]]:
    return [(a, st) for a, st in cands
            if all(u16_at(snap, a + k * st) == hp for k, hp in enumerate(hps))]


def atb_offset(snaps: list[bytes], base: int, stride: int, slots: int = 8) -> tuple[int | None, int]:
    """Offset (from each slot's HP) of the ATB bar.

    A real ATB bar only fills for characters who are in the fight and alive.
    Timers and animation counters tick in every slot, empty or not, so any
    offset that moves in a slot with 0 HP is rejected. The bar must also move
    in at least one party slot and one enemy slot. Offset 4 (where the bar sits
    in every known version) wins whenever it qualifies.
    Returns (offset, slots seen filling); offset is None when nothing qualifies.
    """
    last = snaps[-1]
    occupied = {k for k in range(slots) if (u16_at(last, base + k * stride) or 0) > 0}
    best, best_score = None, 0
    for off in range(-stride + 2, stride, 2):
        if off == 0:
            continue
        moved = set()
        for a, b in zip(snaps, snaps[1:]):
            for k in range(slots):
                x = base + k * stride + off
                va, vb = u16_at(a, x), u16_at(b, x)
                if va is not None and vb is not None and 0 < vb - va <= MAX_ATB_STEP:
                    moved.add(k)
        if moved - occupied:
            continue                       # ticks in an empty slot: a timer, not a bar
        if not (any(k < 4 for k in moved) and any(k >= 4 for k in moved)):
            continue
        if off == ATB_OFF:
            return off, len(moved)
        if len(moved) > best_score:
            best, best_score = off, len(moved)
    return best, best_score


def ask_party_hp(prompt: str) -> list[int] | None:
    print(prompt)
    hps = []
    for k in range(4):
        raw = input(f"  Position {k + 1} HP (Enter if nobody / done): ").strip()
        if raw.lower() in {"q", "quit"}:
            return None
        if not raw:
            break
        if not raw.isdigit():
            print("  Numbers only, please.")
            return ask_party_hp(prompt)
        hps.append(int(raw))
    return hps


def find_battle(ram: DuckStationRam, cfg: dict) -> dict | None:
    print("Finding where your copy keeps HP in battle.")
    print("Be in a fight with at least 2 party members. Different HPs are best (e.g. not all full).\n")
    hps = ask_party_hp("Type each party member's CURRENT HP as shown, top of the list first:")
    if hps is None:
        return None
    if len(hps) < 2:
        print("Need at least 2 party members. Try again in a fight with a bigger party.")
        return None
    if max(hps) < 10:
        print("Those HPs are too small to search for. Try a fight where someone has more HP.")
        return None

    snap = ram.snapshot()
    cands = find_hp_tables(snap, hps)
    print(f"  {len(cands)} place(s) match.")
    if not cands:
        print("Nothing matched. Check you typed the HP exactly as the battle screen shows, then try again.")
        return None

    tries = 0
    count = len(hps)
    while len(cands) > 1 and tries < 4:
        tries += 1
        again = ask_party_hp("Now change someone's HP (take a hit or heal), then type everyone's HP again:")
        if again is None:
            return None
        if len(again) != count:
            print(f"  That's {len(again)} HP values; type all {count} again.")
            continue
        before = len(cands)
        cands = still_matching(ram.snapshot(), cands, again)
        print(f"  {len(cands)} place(s) still match.")
        if 1 < len(cands) == before:
            break                 # several copies all hold live HP; pick between them below
    if not cands:
        print("Nothing kept matching. Run this again; type the HP after the numbers stop moving.")
        return None
    # Do the enemies share this table (slots 5-8)? Watch for bars filling there.
    print("\nNow let the fight run with no menu open for 3 seconds...")
    if not wait("Press Enter, then keep your hands off for 3 seconds."):
        return None
    snaps = []
    for _ in range(30):
        snaps.append(ram.snapshot())
        _sleep(0.1)

    # Prefer a copy where the enemies and ATB bars are confirmed (the real battle table).
    for hp_addr, stride in cands:
        off, score = atb_offset(snaps, hp_addr, stride)
        if off is not None:
            enemies = [u16_at(snaps[-1], hp_addr + k * stride) for k in range(4, 8)]
            print(f"  Party HP found at 0x{hp_addr:06X}, slots 0x{stride:X} apart.")
            print(f"  Enemies share the table: enemy HP reads {enemies} (0 = empty slot); "
                  f"ATB bars at +{off} ({score} of 8 seen filling).")
            return {"battle_base": f"0x{hp_addr:06X}", "slot_stride": f"0x{stride:X}",
                    "hp_off": 0, "atb_off": off, "party_hp_base": None, "party_stride": None}

    hp_addr, stride = cands[0]
    print(f"  Party HP found at 0x{hp_addr:06X}, slots 0x{stride:X} apart.")
    print("  Couldn't confirm the enemies in the same table, so only party HP is changed;")
    print("  enemy HP and battle detection keep their current settings.")
    return {"party_hp_base": f"0x{hp_addr:06X}", "party_stride": f"0x{stride:X}"}


def live_check(ram: DuckStationRam, cfg: dict, seconds: int = 30) -> None:
    from memory import Addresses
    a = Addresses.from_config(cfg)
    print("Showing what the coach reads, once a second. Compare it with the game. Ctrl+C to stop.\n")
    try:
        for _ in range(seconds):
            snap = ram.snapshot()
            party = [u16_at(snap, x) for x in a.party_hp_addrs]
            enemy = [u16_at(snap, x) for x in a.enemy_hp]
            atb = [u16_at(snap, x) for x in a.party_atb]
            print(f"  Party HP {party}   Enemy HP {enemy}   Party ATB {atb}")
            _sleep(1)
    except (KeyboardInterrupt, Stopped):
        pass


# ----------------------------------------------------------------------
# Fight detection: bytes that are one value on the field, another in battle
# ----------------------------------------------------------------------
MAX_FLAGS = 16


def battle_flag_candidates(field_snaps: list[bytes], battle_snaps: list[bytes]) -> list[tuple[int, int, int]]:
    """Bytes that read the same value in every field snapshot, the same (different)
    value in every battle snapshot. Returns [(address, field_value, battle_value)]."""
    f0, b0 = field_snaps[0], battle_snaps[0]
    n = len(f0)
    out = []
    chunk = 4096
    for start in range(0, n, chunk):
        end = min(n, start + chunk)
        fs = [f[start:end] for f in field_snaps]
        bs = [b[start:end] for b in battle_snaps]
        if fs[0] == bs[0]:
            continue                                  # nothing differs here
        for i in range(end - start):
            fv, bv = fs[0][i], bs[0][i]
            if fv == bv:
                continue
            if all(f[i] == fv for f in fs[1:]) and all(b[i] == bv for b in bs[1:]):
                out.append((start + i, fv, bv))
    return out


def pick_flags(cands: list[tuple[int, int, int]], k: int = MAX_FLAGS) -> list[tuple[int, int, int]]:
    """Spread the chosen flags across memory, so one odd area can't outvote the rest."""
    if len(cands) <= k:
        return cands
    step = len(cands) / k
    return [cands[int(i * step)] for i in range(k)]


def find_fights(ram: DuckStationRam) -> list[tuple[int, int, int]] | None:
    print("Teaching the coach exactly when you're in a fight (about 2 minutes).")
    print("Press Enter at each prompt, then keep still for a second.\n")
    field_snaps, battle_snaps = [], []

    def two(lst):
        lst.append(ram.snapshot())
        _sleep(0.6)
        lst.append(ram.snapshot())

    steps = [
        ("1) Stand on the field (walking around, no menu).", field_snaps),
        ("2) Get into any fight. Wait until the ATB bars show.", battle_snaps),
        ("3) Back on the field, on a DIFFERENT screen if you can.", field_snaps),
        ("4) Get into another fight (a different kind of enemy is best).", battle_snaps),
        ("5) On the field again, with the main menu open.", field_snaps),
    ]
    for prompt, lst in steps:
        if not wait(prompt):
            return None
        two(lst)
    cands = battle_flag_candidates(field_snaps, battle_snaps)
    print(f"  {len(cands)} bytes tell field and battle apart.")
    if len(cands) < 3:
        print("Not enough to go on. Run it again, making sure the fights had started when you pressed Enter.")
        return None
    return pick_flags(cands)


# ----------------------------------------------------------------------
# Character EXP, level and ability AP
# ----------------------------------------------------------------------
CHAR_ORDER = ["zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant"]


def u32_scan(snap: bytes, value: int) -> list[int]:
    target = value.to_bytes(4, "little")
    out, i = [], snap.find(target)
    while i != -1:
        out.append(i)
        i = snap.find(target, i + 1)
    return out


def level_offset(snap: bytes, exp_addr: int, level: int, span: int = 0x100) -> list[int]:
    """Offsets (from the EXP address) of bytes holding the character's level."""
    return [off for off in range(-span, span)
            if 0 <= exp_addr + off < len(snap) and snap[exp_addr + off] == level and off not in range(0, 4)]


def find_stats(ram: DuckStationRam, who: str, cfg: dict) -> dict | None:
    print(f"Finding {who.capitalize()}'s EXP and level. Open the Status screen for {who.capitalize()}.")
    level = ask_int("Level: ")
    exp = ask_int("EXP (the total, not 'Next Lv'): ")
    if level is None or exp is None:
        return None
    if exp < 100:
        print("EXP is too small to search for yet. Try again after a few fights.")
        return None
    cands = u32_scan(ram.snapshot(), exp)
    print(f"  {len(cands)} place(s) hold that EXP.")
    while len(cands) > 1:
        if not wait("Win a fight with them in the party, open their Status screen again."):
            return None
        exp = ask_int("EXP now: ")
        if exp is None:
            return None
        level = ask_int("Level now: ") or level
        snap = ram.snapshot()
        cands = [a for a in cands if int.from_bytes(snap[a:a + 4], "little") == exp]
        print(f"  {len(cands)} place(s) still match.")
    if not cands:
        print("Nothing matched. Check the EXP number and try again.")
        return None
    exp_addr = cands[0]
    snap = ram.snapshot()
    offs = level_offset(snap, exp_addr, level)
    known = (cfg.get("addresses", {}).get("chars") or {})
    # Prefer the level offset already found for another character.
    for rec in known.values():
        if "exp" in rec and "level" in rec:
            o = int(rec["level"], 16) - int(rec["exp"], 16)
            if o in offs:
                offs = [o]
                break
    if not offs:
        print("  Found EXP but not the level byte; levels won't show for this character.")
        return {"exp": f"0x{exp_addr:06X}"}
    lvl_addr = exp_addr + min(offs, key=abs)
    print(f"  EXP at 0x{exp_addr:06X}, level at 0x{lvl_addr:06X}.")
    return {"exp": f"0x{exp_addr:06X}", "level": f"0x{lvl_addr:06X}"}


def derive_others(cfg: dict, ram: DuckStationRam) -> dict | None:
    """With two characters found, guess the rest from the spacing between their records."""
    chars = cfg.get("addresses", {}).get("chars") or {}
    found = [(CHAR_ORDER.index(n), n, r) for n, r in chars.items() if n in CHAR_ORDER and "exp" in r and "level" in r]
    if len(found) < 2:
        return None
    found.sort()
    (i1, _, r1), (i2, _, r2) = found[0], found[1]
    e1, e2 = int(r1["exp"], 16), int(r2["exp"], 16)
    if (e2 - e1) % (i2 - i1):
        return None
    stride = (e2 - e1) // (i2 - i1)
    lvl_off = int(r1["level"], 16) - e1
    snap = ram.snapshot()
    guess = {}
    for i, name in enumerate(CHAR_ORDER):
        if name in chars:
            continue
        e = e1 + (i - i1) * stride
        if 0 <= e and e + lvl_off < len(snap):
            guess[name] = {"exp": f"0x{e:06X}", "level": f"0x{e + lvl_off:06X}",
                           "_show": (snap[e + lvl_off], int.from_bytes(snap[e:e + 4], "little"))}
    if not guess:
        return None
    print("  Guessed the other characters from the spacing:")
    for n, g in guess.items():
        lv, ex = g["_show"]
        print(f"    {n.capitalize():8} Lv {lv:3}  EXP {ex}")
    if input("  Do those look right (or 0 for characters you haven't met)? [y/n] ").strip().lower() != "y":
        return None
    for g in guess.values():
        g.pop("_show")
    return guess


def find_ap(ram: DuckStationRam, who: str, ability: str, cfg: dict) -> dict | None:
    print(f"Finding {who.capitalize()}'s AP for {ability}. Open the ability screen where it shows the AP.")
    need = ask_int(f"AP needed to learn {ability} (the second number): ")
    have = ask_int("AP now (the first number): ")
    if need is None or have is None:
        return None
    width = 1 if need <= 255 else 2
    rec = (cfg.get("addresses", {}).get("chars") or {}).get(who)
    snap = ram.snapshot()
    if rec and "exp" in rec:
        centre = int(rec["exp"], 16)
        region = range(max(0, centre - 0x200), min(len(snap) - width, centre + 0x400))
        cands = [a for a in region if int.from_bytes(snap[a:a + width], "little") == have]
    else:
        cands = scan(snap, have, width) if width == 2 else [i for i, v in enumerate(snap) if v == have]
    print(f"  {len(cands)} place(s) match.")
    while len(cands) > 1:
        if not wait(f"Win a fight with {who.capitalize()} (so the AP goes up), then reopen the ability screen."):
            return None
        have = ask_int("AP now: ")
        if have is None:
            return None
        snap = ram.snapshot()
        cands = [a for a in cands if int.from_bytes(snap[a:a + width], "little") == have]
        print(f"  {len(cands)} place(s) still match.")
    if not cands:
        print("Nothing matched. Check the number and try again.")
        return None
    print(f"  {ability} AP at 0x{cands[0]:06X}.")
    return {"addr": f"0x{cands[0]:06X}", "max": need, "width": width}


# ----------------------------------------------------------------------
# Menus: open or not, equipment, support abilities
# ----------------------------------------------------------------------
STRUCT_SPAN = 0x200          # how far around a character's EXP address to look


def find_menu(ram: DuckStationRam) -> list[tuple[int, int, int]] | None:
    print("Teaching the coach when the menu is open (about a minute).")
    print("Press Enter at each prompt, then keep still for a second.\n")
    closed, opened = [], []

    def two(lst):
        lst.append(ram.snapshot())
        _sleep(0.6)
        lst.append(ram.snapshot())

    steps = [
        ("1) On the field, menu closed.", closed),
        ("2) Open the main menu (the list with Items, Abilities, Equip...).", opened),
        ("3) Close it, and walk to a DIFFERENT screen if you can.", closed),
        ("4) Open the menu and go into a character's Equip screen.", opened),
    ]
    for prompt, lst in steps:
        if not wait(prompt):
            return None
        two(lst)
    cands = battle_flag_candidates(closed, opened)
    print(f"  {len(cands)} bytes tell the menu apart.")
    if len(cands) < 3:
        print("Not enough to go on. Run it again, pressing Enter only once each screen has settled.")
        return None
    return pick_flags(cands)


def pattern_hits(snap: bytes, lo: int, hi: int, want: list[int | None]) -> list[int]:
    """Addresses in [lo, hi) where bytes match `want` (None matches anything)."""
    out = []
    for a in range(max(0, lo), min(len(snap) - len(want), hi)):
        if all(w is None or snap[a + i] == w for i, w in enumerate(want)):
            out.append(a)
    return out


def ask_equipment(who: str) -> list[int | None] | None:
    from screen import ITEMS, match_item
    print(f"Type {who}'s 5 equipment slots as the Equip screen shows them, top to bottom")
    print("(weapon, head, wrist, armour, accessory). Leave a line empty for an empty slot.")
    out = []
    for slot in ("Weapon", "Head", "Wrist", "Armour", "Accessory"):
        while True:
            try:
                text = input(f"  {slot}: ").strip()
            except EOFError:
                return None
            if not text:
                out.append(None)                     # empty slot: its value isn't known
                break
            item = match_item(text, cutoff=0.7)
            if item:
                if item.lower() != text.lower():
                    print(f"    (taken as {item})")
                out.append(ITEMS.index(item))
                break
            print("    Not an item name I know; check the spelling.")
    if all(v is None for v in out):
        print("Need at least one item.")
        return None
    return out


def find_equip(ram: DuckStationRam, who: str, cfg: dict) -> int | None:
    chars = cfg.get("addresses", {}).get("chars", {})
    if who not in chars or "exp" not in chars[who]:
        print(f"Run `python app\\finder.py stats {who}` first (equipment is found next to {who}'s EXP).")
        return None
    exp = int(chars[who]["exp"], 16) if isinstance(chars[who]["exp"], str) else int(chars[who]["exp"])
    if not wait(f"Open {who.capitalize()}'s Equip screen."):
        return None
    want = ask_equipment(who.capitalize())
    if want is None:
        return None
    hits = pattern_hits(ram.snapshot(), exp - STRUCT_SPAN, exp + STRUCT_SPAN, want)
    while len(hits) > 1:
        print(f"  {len(hits)} places match. Change one piece of {who.capitalize()}'s equipment, then type all 5 again.")
        want = ask_equipment(who.capitalize())
        if want is None:
            return None
        snap = ram.snapshot()
        hits = [a for a in hits if all(w is None or snap[a + i] == w for i, w in enumerate(want))]
    if not hits:
        print("Nothing matched. Check the names are exactly as the Equip screen shows them, and try again.")
        return None
    off = hits[0] - exp
    print(f"  Equipment found ({off:+d} from {who.capitalize()}'s EXP). The same spot works for everyone.")
    return off


def changed_bits(a: bytes, b: bytes, base: int) -> list[tuple[int, int]]:
    """(address, bit) for every byte that changed by exactly one bit."""
    out = []
    for i, (x, y) in enumerate(zip(a, b)):
        d = x ^ y
        if d and d & (d - 1) == 0:
            out.append((base + i, d.bit_length() - 1))
    return out


def find_abilities(ram: DuckStationRam, who: str, cfg: dict) -> dict[str, list[int]] | None:
    """Learn where support abilities are switched on, by watching you switch them."""
    from screen import match_ability
    chars = cfg.get("addresses", {}).get("chars", {})
    if who not in chars or "exp" not in chars[who]:
        print(f"Run `python app\\finder.py stats {who}` first (abilities are found next to {who}'s EXP).")
        return None
    exp = int(chars[who]["exp"], 16) if isinstance(chars[who]["exp"], str) else int(chars[who]["exp"])
    lo = exp - STRUCT_SPAN
    learned: dict[str, tuple[int, int]] = {}
    print(f"Open {who.capitalize()}'s Ability screen, on the support abilities (the ones with stones).")
    print("Each time: switch ONE ability on or off, then type its name. Empty line to finish.")
    print("Each one found also fixes the guesses for the ones near it in the list.\n")
    while True:
        before = ram.read(lo, 2 * STRUCT_SPAN)
        try:
            text = input("  Switched (name, or Enter to finish): ").strip()
        except EOFError:
            break
        if not text:
            break
        name = match_ability(text)
        if not name:
            print("    Not a support ability I know; check the spelling.")
            continue
        after = ram.read(lo, 2 * STRUCT_SPAN)
        hits = changed_bits(before, after, lo)
        if len(hits) != 1:
            # Stones left can also change; keep only bits that flip back when switched back.
            print(f"    {len(hits)} candidates. Switch {name} back the other way, then press Enter.")
            again_before = after
            try:
                input()
            except EOFError:
                break
            again = changed_bits(again_before, ram.read(lo, 2 * STRUCT_SPAN), lo)
            hits = [h for h in hits if h in again]
        if len(hits) > 1:
            # The abilities sit together in one run of bytes: keep a candidate in that run.
            near = {off for off, _ in (cfg.get("addresses", {}).get("ability_bits") or {}).values()}
            near |= {off for off, _ in learned.values()}
            if near:
                inside = [h for h in hits if min(near) - 8 <= h[0] - exp <= max(near) + 8]
                if len(inside) == 1:
                    hits = inside
        if len(hits) != 1:
            print(f"    Couldn't pin it down ({len(hits)} places); try switching it again, or another ability.")
            continue
        learned[name] = (hits[0][0] - exp, hits[0][1])
        print(f"    {name}: found.")
        # Saved straight away, so closing the window doesn't lose it.
        bits = cfg.setdefault("addresses", {}).setdefault("ability_bits", {})
        bits[name] = list(learned[name])
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("    Switch another (the more the better, spread through the list), or Enter to finish.")
    if not learned:
        return None
    print(f"  Saved the {len(learned)} you switched; the rest are placed from them.")
    return {k: list(v) for k, v in learned.items()}


def fill_from_order(learned: dict[str, tuple[int, int]], order: list[str]) -> dict[str, tuple[int, int]] | None:
    """If every learned ability sits where the usual order puts it (one bit each, lowest
    bit first), work out all the others. None if they don't agree."""
    items = [(n, off * 8 + bit) for n, (off, bit) in learned.items() if n in order]
    if len(items) < 2:
        return None
    n0, g0 = items[0]
    start = g0 - order.index(n0)
    if any(g != start + order.index(n) for n, g in items):
        return None
    return {n: divmod(start + i, 8) for i, n in enumerate(order)}


def find_row(ram: DuckStationRam, who: str, cfg: dict) -> dict | None:
    """Where a character's row is kept: the one bit near their EXP that flips each time
    they swap rows (and back)."""
    chars = cfg.get("addresses", {}).get("chars", {})
    if who not in chars or "exp" not in chars[who]:
        print(f"Run `python app\\finder.py stats {who}` first (the row is found next to {who}'s EXP).")
        return None
    exp = int(chars[who]["exp"], 16) if isinstance(chars[who]["exp"], str) else int(chars[who]["exp"])
    lo = exp - STRUCT_SPAN
    print(f"Open the menu, go to Order, and have {who.capitalize()} ready to swap rows.")
    cands = None
    for k in range(3):
        before = ram.read(lo, 2 * STRUCT_SPAN)
        if not wait(f"  Swap {who.capitalize()}'s row ({k + 1} of 3), then press Enter."):
            return None
        hits = set(changed_bits(before, ram.read(lo, 2 * STRUCT_SPAN), lo))
        cands = hits if cands is None else cands & hits
        if not cands:
            print("  Nothing flipped each time. Make sure the row actually swapped, and try again.")
            return None
    if len(cands) > 1:
        cands = {min(cands, key=lambda c: abs(c[0] - exp))}       # the one in the character's own data
    addr, bit = cands.pop()
    back = input(f"  Is {who.capitalize()} in the BACK row now? [y/n] ").strip().lower() in {"y", "yes"}
    val = ram.u8(addr) >> bit & 1
    return {"off": addr - exp, "bit": bit, "back": val if back else 1 - val}


def find_gil(ram: DuckStationRam, cfg: dict) -> int | None:
    print("Finding your gil. Open the menu (gil shows bottom right), or stand in a shop.")
    gil = ask_int("Gil: ")
    if gil is None:
        return None
    cands = u32_scan(ram.snapshot(), gil)
    print(f"  {len(cands)} place(s) hold that.")
    while len(cands) > 1:
        if not wait("Change it: buy or sell something (or pick up gil)."):
            return None
        gil = ask_int("Gil now: ")
        if gil is None:
            return None
        snap = ram.snapshot()
        cands = [a for a in cands if int.from_bytes(snap[a:a + 4], "little") == gil]
        print(f"  {len(cands)} place(s) still match.")
        if len(cands) > 1:
            inv = cfg.get("addresses", {}).get("inventory")
            if inv:                       # the game keeps gil beside the item bag
                near = [a for a in cands if abs(a - int(inv, 16)) < 0x1000]
                if len(near) == 1:
                    cands = near
    if not cands:
        print("Nothing matched. Check the number and try again.")
        return None
    print(f"  Gil at 0x{cands[0]:06X}.")
    return cands[0]


def route_config_lines() -> list[tuple[str, str]]:
    """(setting, value) for every Config line in the route in use, first one of each setting."""
    import routes
    from syntax import config_check
    try:
        route = json.loads(routes.path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for st in route.get("steps", []):
        for line in st.get("lines", []) or []:
            c = config_check(line)
            if c and c[0] not in seen:
                seen.add(c[0])
                out.append(c)
    return out


def find_config(ram: DuckStationRam, cfg: dict, only: str | None = None) -> dict | None:
    """Where each Config setting is kept: the bits that follow you switching it back and forth."""
    from syntax import CONFIG_SETTINGS
    todo = route_config_lines()
    if only:
        name = next((c for c in CONFIG_SETTINGS if c.lower() == only.lower()), None)
        if not name:
            print(f"Not a Config setting I know. These are: {', '.join(CONFIG_SETTINGS)}")
            return None
        want = input(f"Which value should {name} be set to (as the route writes it)? ").strip()
        todo = [(name, want)]
    if not todo:
        print("Your route has no Config lines (like \"Battle Speed: Fast\"). Add them in the editor first,")
        print("or name one: python app\\finder.py config \"Battle Speed\"")
        return None
    known = cfg.get("addresses", {}).get("config", {}) or {}
    chars = cfg.get("addresses", {}).get("chars", {})
    anchor = None
    if known:
        anchor = int(next(iter(known.values()))["addr"], 16)
    elif "zidane" in chars and "exp" in chars["zidane"]:
        anchor = int(chars["zidane"]["exp"], 16)
    print("Open FF9's Config menu. For each setting, switch it when asked and press Enter.\n")
    found = {}
    for setting, want in todo:
        print(f"{setting} (the route wants: {want})")
        snaps = []
        for k, ask in enumerate([f"Set {setting} to {want}", f"Change {setting} to something else",
                                 f"Back to {want}", f"Something else again (a different value if there is one)",
                                 f"Back to {want}"]):
            if not wait(f"  {ask}, then press Enter."):
                return found or None
            snaps.append(ram.snapshot())
        a1, b1, a2, b2, a3 = snaps
        cands = [i for i in range(len(a1)) if a1[i] == a2[i] == a3[i] and a1[i] != b1[i] and a1[i] != b2[i]]
        if not cands:
            print("  Nothing followed it. Skipped; try again later with:"
                  f" python finder.py config \"{setting}\"")
            continue
        if len(cands) > 1 and anchor is not None:
            cands.sort(key=lambda i: abs(i - anchor))
        addr = cands[0]
        mask = (a1[addr] ^ b1[addr]) | (a1[addr] ^ b2[addr])
        found[setting] = {"addr": f"0x{addr:06X}", "mask": mask, "values": {want: a1[addr] & mask}}
        anchor = addr
        print(f"  Found ({len(cands)} place(s) matched; using 0x{addr:06X}).")
    return found or None


# ----------------------------------------------------------------------
# Battle ID: FF9's number for the battle you're in
# ----------------------------------------------------------------------
MAX_BATTLE_ID = 4096


def battle_id_candidates(a1: bytes, a2: bytes, a3: bytes, b1: bytes, b2: bytes,
                         c: bytes | None = None) -> list[int]:
    """16-bit places that hold one number all through a fight, the same number when the
    same fight comes round again, and a different one for a different fight."""
    va1, va2, va3, vb1, vb2 = (u16s(x) for x in (a1, a2, a3, b1, b2))
    vc = u16s(c) if c is not None else None
    out = []
    for i in range(len(va1)):
        x = va1[i]
        if not (0 < x < MAX_BATTLE_ID) or x != va2[i] or x != va3[i]:
            continue
        y = vb1[i]
        if y == x or y != vb2[i] or not (0 < y < MAX_BATTLE_ID):
            continue
        if vc is not None and (vc[i] in (x, y) or not (0 < vc[i] < MAX_BATTLE_ID)):
            continue
        out.append(i * 2)
    return out


def find_battle_id(ram: DuckStationRam) -> int | None:
    print("Finding which battle you're in. You need three fights; savestates make it quick.")
    print("Press Enter once each fight has loaded (the enemies are on screen).\n")
    def two(prompt):
        if not wait(prompt):
            return None
        x = ram.snapshot()
        _sleep(2.0)
        return x, ram.snapshot()
    a = two("Fight 1: any fight.")
    if a is None:
        return None
    b = two("Fight 2: a fight with DIFFERENT enemies (on the same screen if you can).")
    if b is None:
        return None
    if not wait("Fight 3: the SAME enemies as fight 1 (load a savestate from before fight 1)."):
        return None
    a3 = ram.snapshot()
    cands = battle_id_candidates(a[0], a[1], a3, b[0], b[1])
    print(f"  {len(cands)} place(s) match.")
    for _ in range(3):
        if len(cands) <= 1:
            break
        if not wait("One more fight with different enemies again (not fight 1's or fight 2's)."):
            return None
        c = ram.snapshot()
        keep = set(battle_id_candidates(a[0], a[1], a3, b[0], b[1], c))
        cands = [x for x in cands if x in keep]
        print(f"  {len(cands)} place(s) still match.")
    if len(cands) != 1:
        print("Couldn't single it out" if cands else "Nothing matched", end="")
        print(". Not saved. That's fine: the coach already tells fights apart by their enemies'")
        print("records, which it learns by itself (no setup).")
        return None
    snap = ram.snapshot()
    print(f"  Using 0x{cands[0]:06X} (this fight: {u16_at(snap, cands[0])}).")
    return cands[0]


# ----------------------------------------------------------------------
# Item bag
# ----------------------------------------------------------------------
EMPTY_ID = 255


def _entry_ok(e: tuple[int, int]) -> bool:
    i, n = e
    return n == 0 or (i == EMPTY_ID and n in (0, EMPTY_ID)) or (i < EMPTY_ID and 1 <= n <= 99)


def bag_run(snap: bytes, start: int, id_first: bool, limit: int = 1024) -> tuple[int, int]:
    """The run of plausible bag entries around the entry at `start`: (first entry address, entries)."""
    def entry(a):
        if a < 0 or a + 1 >= len(snap):
            return None
        return (snap[a], snap[a + 1]) if id_first else (snap[a + 1], snap[a])

    seen: set[int] = set()

    def take(e):
        if e is None or not _entry_ok(e) or (e[0] != EMPTY_ID and e[0] in seen):
            return False
        if e[0] != EMPTY_ID:
            seen.add(e[0])
        return True

    if not take(entry(start)):
        return start, 0
    lo = hi = start
    while (hi - lo) // 2 + 1 < limit and take(entry(hi + 2)):
        hi += 2
    while (hi - lo) // 2 + 1 < limit and take(entry(lo - 2)):
        lo -= 2
    return lo, (hi - lo) // 2 + 1


def bag_candidates(before: bytes, after: bytes, item_id: int) -> list[tuple[int, int, bool]]:
    """(first entry address, entries, id_first) for places where `item_id`'s count moved by one."""
    out = set()
    for a in range(1, len(after) - 1):
        if abs(after[a] - before[a]) != 1:
            continue
        for id_first, start, id_at in ((True, a - 1, a - 1), (False, a, a + 1)):
            if after[id_at] == item_id:
                lo, n = bag_run(after, start, id_first)
                if n >= 4:
                    out.add((lo, n, id_first))
    return sorted(out, key=lambda c: -c[1])


def find_items(ram: DuckStationRam) -> dict | None:
    from screen import ITEMS, match_item
    from memory import parse_items
    print("Go into any shop (or open the Items menu).")
    cands = None
    for round_ in (1, 2):
        if not wait("Before you buy anything, press Enter." if round_ == 1 else
                    "Now buy (or sell) one of a DIFFERENT item. Press Enter first."):
            return None
        before = ram.snapshot()
        while True:
            name = input("Buy or sell exactly one of something, then type its name: ").strip()
            if name.lower() in {"q", "quit"}:
                return None
            item = match_item(name)
            if item and item in ITEMS:
                break
            print("  Don't know that item, try again.")
        after = ram.snapshot()
        found = bag_candidates(before, after, ITEMS.index(item))
        if cands is None:
            cands = found
        else:
            keys = {(lo, f) for lo, _, f in found}
            near = [c for c in cands if any(abs(c[0] - lo) < 0x200 and f == c[2] for lo, f in keys)]
            cands = [max((c2 for c2 in found if c2[2] == c[2] and abs(c2[0] - c[0]) < 0x200),
                         key=lambda c2: c2[1]) for c in near]
        if not cands:
            print("Couldn't find the item bag. Try again: buy just one, then type its name.")
            return None
        if len(cands) == 1:
            break
    lo, n, id_first = cands[0]
    bag = parse_items(ram.read(lo, n * 2), id_first)
    print("\nThis is what the coach would see in your bag:")
    for i, c in sorted(bag.items()):
        print(f"  {ITEMS[i] if i < len(ITEMS) else i}: {c}")
    if input("Is that right? [y/n] ").strip().lower() not in {"y", "yes"}:
        print("Not saved.")
        return None
    return {"inventory": f"0x{lo:06X}", "inventory_entries": n, "inventory_id_first": id_first}


def ask_slot(name: str) -> int | None:
    raw = input(f"Which party position is {name} in for most fights? (1 = top, Enter = skip) ").strip()
    return int(raw) - 1 if raw in {"1", "2", "3", "4"} else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 1
    what = argv[0].lower()
    args = argv[1:]
    cfg = json.loads(CONFIG.read_text())
    try:
        ram = open_ram(cfg)
    except MemoryError_ as e:
        print(f"Can't read DuckStation: {e}")
        return 1

    addrs = cfg.setdefault("addresses", {})
    if what == "check":
        live_check(ram, cfg)
        return 0
    if what == "fights":
        flags = find_fights(ram)
        if not flags:
            return 1
        addrs["battle_flags"] = [{"addr": f"0x{a:06X}", "field": fv, "battle": bv} for a, fv, bv in flags]
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print(f"Saved {len(flags)} battle flags. Restart the coach; it now knows exactly when you're in a fight.")
        return 0
    if what == "stats":
        if len(args) < 1:
            print("Usage: python app\\finder.py stats zidane")
            return 1
        who = args[0].lower()
        found = find_stats(ram, who, cfg)
        if not found:
            return 1
        addrs.setdefault("chars", {})[who] = found
        others = derive_others(cfg, ram)
        if others:
            addrs["chars"].update(others)
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. Restart the coach to see levels on the panel.")
        if len(addrs["chars"]) == 1:
            print("Tip: do one more character (e.g. python app\\finder.py stats vivi) and it can guess the rest.")
        return 0
    if what == "ap":
        if len(args) < 2:
            print("Usage: python app\\finder.py ap zidane Flee")
            return 1
        who, ability = args[0].lower(), " ".join(args[1:])
        found = find_ap(ram, who, ability, cfg)
        if not found:
            return 1
        addrs.setdefault("ap", {})[f"{who}:{ability}"] = found
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. The panel shows it until it's learned.")
        return 0
    if what == "menu":
        flags = find_menu(ram)
        if not flags:
            return 1
        addrs["menu_flags"] = [{"addr": f"0x{a:06X}", "closed": c, "open": o} for a, c, o in flags]
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print(f"Saved {len(flags)} menu flags. Restart the coach.")
        return 0
    if what == "equip":
        if not args:
            print("Usage: python app\\finder.py equip zidane")
            return 1
        off = find_equip(ram, args[0].lower(), cfg)
        if off is None:
            return 1
        addrs["equip_off"] = off
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. Restart the coach: menu steps now tick equipment as you put it on.")
        return 0
    if what == "ability":
        if not args:
            print("Usage: python app\\finder.py ability zidane")
            return 1
        found = find_abilities(ram, args[0].lower(), cfg)
        if not found:
            return 1
        bits = addrs.setdefault("ability_bits", {})
        bits.update(found)
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. Restart the coach: menu steps now tick abilities as you switch them.")
        return 0
    if what == "row":
        if not args:
            print("Usage: python app\\finder.py row freya")
            return 1
        found = find_row(ram, args[0].lower(), cfg)
        if not found:
            return 1
        addrs["row"] = found
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved for everyone. Restart the coach: \"front row\" / \"back row\" lines now tick.")
        return 0
    if what in ("battleid", "battle_id", "battle-id"):
        addr = find_battle_id(ram)
        if addr is None:
            return 1
        addrs["battle_id"] = f"0x{addr:06X}"
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. Restart the coach: each fight step learns its battle the first time.")
        return 0
    if what in ("gil", "money"):
        addr = find_gil(ram, cfg)
        if addr is None:
            return 1
        addrs["gil"] = f"0x{addr:06X}"
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. Restart the coach: gil pickups now tick from memory.")
        return 0
    if what == "config":
        found = find_config(ram, cfg, " ".join(args) if args else None)
        if not found:
            return 1
        cur = addrs.setdefault("config", {})
        for k, v in found.items():
            if k in cur and cur[k].get("addr") == v["addr"]:
                v["values"] = {**cur[k].get("values", {}), **v["values"]}
                v["mask"] = v["mask"] | int(cur[k].get("mask", 0))
            cur[k] = v
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print(f"Saved {len(found)} setting(s). Restart the coach: Config lines now tick.")
        return 0
    if what in ("items", "inventory", "bag"):
        found = find_items(ram)
        if not found:
            return 1
        addrs.update(found)
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved. Restart the coach: shop steps now tick what you buy and sell.")
        return 0
    if what == "battle":
        found = find_battle(ram, cfg)
        if found is None:
            return 1
        for k, v in found.items():
            if v is None:
                addrs.pop(k, None)
            else:
                addrs[k] = v
        for old in ("enemy_hp", "enemy_atb", "party_atb", "party_hp"):
            addrs.pop(old, None)  # replaced by the battle table
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print("Saved to config.json. Check it with: python app\\finder.py check")
        return 0
    if what == "field":
        addr = find_field(ram)
        if addr is None:
            return 1
        addrs["field_id"] = f"0x{addr:06X}"
        label = "screen ID"
    else:
        # A character name: HP comes from the battle table, so we only need their position.
        slot = ask_slot(what.capitalize())
        if slot is None:
            return 1
        addrs.setdefault("party_slots", {})[what] = slot
        write_atomic(CONFIG, json.dumps(cfg, indent=2))
        print(f"Saved: {what.capitalize()} is party position {slot + 1}.")
        return 0

    if addr >= PS1_RAM_SIZE:
        print("Unexpected address; not saved.")
        return 1
    write_atomic(CONFIG, json.dumps(cfg, indent=2))
    print(f"Saved {label} at 0x{addr:06X} to config.json. Restart the coach to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
