"""FFIX item, support ability and key item lists, the game's text (for its key item name
table in memory), and matching a typed name to an item."""

from __future__ import annotations

import difflib
import re

# Every item name in FFIX (inventory order: the bag holds these numbers).
ITEMS = [
    "Hammer", "Dagger", "Mage Masher", "Mythril Dagger", "Gladius", "Zorlin Shape", "Orichalcon",
    "Butterfly Sword", "The Ogre", "Exploda", "Rune Tooth", "Angel Bless", "Sargatanas", "Masamune",
    "The Tower", "Ultima Weapon", "Broadsword", "Iron Sword", "Mythril Sword", "Blood Sword",
    "Ice Brand", "Coral Sword", "Diamond Sword", "Flame Saber", "Rune Blade", "Defender",
    "Save the Queen", "Ultima Sword", "Excalibur", "Ragnarok", "Excalibur II", "Javelin",
    "Mythril Spear", "Partisan", "Ice Lance", "Trident", "Heavy Lance", "Obelisk", "Holy Lance",
    "Kain's Lance", "Dragon's Hair", "Cat's Claws", "Poison Knuckles", "Mythril Claws",
    "Scissor Fangs", "Dragon's Claws", "Tiger Fangs", "Avenger", "Kaiser Knuckles", "Duel Claws",
    "Rune Claws", "Air Racket", "Multina Racket", "Magic Racket", "Mythril Racket", "Priest's Racket",
    "Tiger Racket", "Rod", "Mythril Rod", "Stardust Rod", "Healing Rod", "Asura's Rod", "Wizard Rod",
    "Whale Whisker", "Golem's Flute", "Lamia's Flute", "Fairy Flute", "Hamelin", "Siren's Flute",
    "Angel Flute", "Mage Staff", "Flame Staff", "Ice Staff", "Lightning Staff", "Oak Staff",
    "Cypress Pile", "Octagon Rod", "High Mage Staff", "Mace of Zeus", "Fork", "Needle Fork",
    "Mythril Fork", "Silver Fork", "Bistro Fork", "Gastro Fork", "Pinwheel", "Rising Sun", "Wing Edge",
    "Wrist", "Leather Wrist", "Glass Armlet", "Bone Wrist", "Mythril Armlet", "Magic Armlet",
    "Chimera Armlet", "Egoist's Armlet", "N-Kai Armlet", "Jade Armlet", "Thief Gloves",
    "Dragon Wrist", "Power Wrist", "Bracer", "Bronze Gloves", "Silver Gloves", "Mythril Gloves",
    "Thunder Gloves", "Diamond Gloves", "Venetia Shield", "Defense Gloves", "Genji Gloves",
    "Aegis Gloves", "Gauntlets", "Leather Hat", "Straw Hat", "Feather Hat", "Steepled Hat",
    "Headgear", "Magus Hat", "Bandana", "Mage's Hat", "Lamia's Tiara", "Ritual Hat",
    "Twist Headband", "Mantra Band", "Dark Hat", "Green Beret", "Black Hood", "Red Hat",
    "Golden Hairpin", "Coronet", "Flash Hat", "Adaman Hat", "Thief Hat", "Holy Miter",
    "Golden Skullcap", "Circlet", "Rubber Helm", "Bronze Helm", "Iron Helm", "Barbut", "Mythril Helm",
    "Gold Helm", "Cross Helm", "Diamond Helm", "Platinum Helm", "Kaiser Helm", "Genji Helmet",
    "Grand Helm", "Aloha T-Shirt", "Leather Shirt", "Silk Shirt", "Leather Plate", "Bronze Vest",
    "Chain Plate", "Mythril Vest", "Adaman Vest", "Magician Cloak", "Survival Vest", "Brigandine",
    "Judo Uniform", "Power Vest", "Gaia Gear", "Demon's Vest", "Minerva's Plate", "Ninja Gear",
    "Dark Gear", "Rubber Suit", "Brave Suit", "Cotton Robe", "Silk Robe", "Magician Robe",
    "Glutton's Robe", "White Robe", "Black Robe", "Light Robe", "Robe of Lords", "Tin Armor",
    "Bronze Armor", "Linen Cuirass", "Chain Mail", "Mythril Armor", "Plate Mail", "Gold Armor",
    "Shield Armor", "Demon's Mail", "Diamond Armor", "Platina Armor", "Carabini Mail",
    "Dragon Mail", "Genji Armor", "Maximillian", "Grand Armor", "Desert Boots", "Magician Shoes",
    "Germinas Boots", "Sandals", "Feather Boots", "Battle Boots", "Running Shoes", "Anklet",
    "Power Belt", "Black Belt", "Glass Buckle", "Madain's Ring", "Rosetta Ring", "Reflect Ring",
    "Coral Ring", "Promist Ring", "Rebirth Ring", "Protect Ring", "Pumice Piece", "Pumice",
    "Yellow Scarf", "Gold Choker", "Fairy Earrings", "Angel Earrings", "Pearl Rouge", "Pearl Armlet",
    "Cachusha", "Barette", "Extension", "Ribbon", "Maiden Prayer", "Ancient Aroma", "Garnet",
    "Amethyst", "Aquamarine", "Diamond", "Emerald", "Moonstone", "Ruby", "Peridot", "Sapphire",
    "Opal", "Topaz", "Lapis Lazuli", "Potion", "Hi-Potion", "Ether", "Elixir", "Phoenix Down",
    "Echo Screen", "Soft", "Antidote", "Eye Drops", "Magic Tag", "Vaccine", "Remedy", "Annoyntment",
    "Phoenix Pinion", "Dark Matter", "Gysahl Greens", "Dead Pepper", "Tent", "Ore",
]
# Support abilities in the order the game lists them (a best guess at FF9's internal
# order: finder.py checks it against two you switch on before trusting it).
ABILITIES = [
    "Auto-Reflect", "Auto-Float", "Auto-Haste", "Auto-Regen", "Auto-Life", "HP+10%", "HP+20%",
    "MP+10%", "MP+20%", "Accuracy+", "Distract", "Long Reach", "MP Attack", "Bird Killer",
    "Bug Killer", "Stone Killer", "Undead Killer", "Dragon Killer", "Devil Killer", "Beast Killer",
    "Man Eater", "High Jump", "Master Thief", "Steal Gil", "Healer", "Add Status", "Gamble Defence",
    "Chemist", "Power Throw", "Power Up", "Reflect-Null", "Reflectx2", "Mag Elem Null", "Concentrate",
    "Half MP", "High Tide", "Counter", "Cover", "Protect Girls", "Eye 4 Eye", "Body Temp", "Alert",
    "Initiative", "Level Up", "Ability Up", "Millionaire", "Flee-Gil", "Guardian Mog", "Insomniac", "Antibody",
    "Bright Eyes", "Loudmouth", "Restore HP", "Jelly", "Return Magic", "Absorb MP", "Auto-Potion",
    "Locomotion", "Clear Headed", "Boost", "Odin's Sword", "Mug", "Bandit",
]


def match_ability(text: str, cutoff: float = 0.8) -> str | None:
    t = text.strip().lower()
    lower = {a.lower(): a for a in ABILITIES}
    if t in lower:
        return lower[t]
    hit = difflib.get_close_matches(t, lower.keys(), n=1, cutoff=cutoff)
    return lower[hit[0]] if hit else None


# FF9's key item list, in the game's order (US release: read from its name table in memory,
# just after the item names). Key item n's bit is 2n in the key item block just past the bag:
# on once you have it. Some names are in the list more than once (two Silver Pendants, six
# Chocograph Pieces): any of their bits counts.
KEY_ITEM_ORDER = [
    "Silver Pendant", "Silver Pendant", "Memory Earring", "Memory Earring", "Falcon Claw", "Desert Star",
    "Ticket", "Blank's Medicine", "Continental Map", "World Map", "Moogle's Flute", "Kupo Nut",
    "Gizamaluke Bell", "Gizamaluke Bell", "Holy Bell", "Protection Bell", "Gate Pass", "Supersoft",
    "Unusual Potion", "Beautiful Potion", "Strange Potion", "Gulug Stone", "Earth Mirror", "Water Mirror",
    "Fire Mirror", "Wind Mirror", "King of Jump Rope", "Athlete Queen", "Mayor's Key", "Moccha Coffee",
    "Kirman Coffee", "Burman Coffee", "Master Hunter", "Mini-Brahne", "Mini-Cid", "Mini-Burmecia",
    "Autograph", "Moogle Suit", "Kupo Nut", "Griffin's Heart", "Doga's Artifact", "Une's Mirror",
    "Rat Tail", "Magical Fingertip", "Red Stone", "Blue Stone", "Yellow Stone", "Green Stone",
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio", "Sagittarius",
    "Capricorn", "Aquarius", "Pisces", "Ophiuchus", "Chocograph Piece", "Chocograph Piece",
    "Chocograph Piece", "Chocograph Piece", "Chocograph Piece", "Chocograph Piece", "Rank S Medal",
    "Superslick", "Strategy Guide",
]


def key_item_ids(order: list[str]) -> dict[str, list[int]]:
    """Each key item's bits in the key item block, from the game's list."""
    out: dict[str, list[int]] = {}
    for n, name in enumerate(order):
        if name and name.lower() != "none":
            out.setdefault(name, []).append(2 * n)
    return out


KEY_ITEM_IDS = key_item_ids(KEY_ITEM_ORDER)
KEY_ITEM_BYTES = 18                 # the block: 144 bits ("all key items" cheat: 9 x FFFF)
US_INVENTORY = 0x08B7C4             # (the list above is the release with the bag here)
KEY_ITEMS = list(KEY_ITEM_IDS)       # key items aren't in the bag: read from memory

# FF9's text: A-Z 0x10-0x29, a-z 0x30-0x49, space 0x0F, ' 0x7E, - 0x0B, names end with 0xFF.
def ff9_text(b: bytes) -> str | None:
    """A name in FF9's text, or None if it has bytes that aren't letters."""
    s = ""
    for x in b:
        if 0x10 <= x <= 0x29:
            s += chr(65 + x - 0x10)
        elif 0x30 <= x <= 0x49:
            s += chr(97 + x - 0x30)
        elif x <= 0x09:
            s += str(x)
        else:
            s += {0x0F: " ", 0x7E: "'", 0x0B: "-"}.get(x, "\0")
    return None if "\0" in s else s


def ff9_bytes(s: str) -> bytes:
    out = bytearray()
    for ch in s:
        out.append(0x10 + ord(ch) - 65 if "A" <= ch <= "Z" else 0x30 + ord(ch) - 97 if "a" <= ch <= "z"
                   else {" ": 0x0F, "'": 0x7E, "-": 0x0B}[ch])
    return bytes(out)


def read_key_item_order(snapshot: bytes, entries: int = 72) -> list[str] | None:
    """The game's key item list, read from its name table in memory (it starts with the
    Silver Pendant, after the item names). None if it isn't there."""
    at = snapshot.find(b"\xff" + ff9_bytes("Silver Pendant") + b"\xff")
    if at < 0:
        return None
    i, out = at + 1, []
    while len(out) < entries:
        j = snapshot.find(b"\xff", i, i + 40)
        if j < 0:
            return None
        name = ff9_text(snapshot[i:j])
        if name is None:
            return None
        out.append(name)
        i = j + 1
    return out


_LOWER = {i.lower(): i for i in ITEMS + KEY_ITEMS}


def _norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9' -]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_item(text: str, cutoff: float = 0.75) -> str | None:
    """The item name a piece of (possibly misread) text is closest to."""
    t = _norm(text)
    if not t:
        return None
    if t in _LOWER:
        return _LOWER[t]
    # Try the whole text and its leading words, longest first.
    words = t.split()
    for n in range(min(len(words), 4), 0, -1):
        cand = " ".join(words[:n])
        hit = difflib.get_close_matches(cand, _LOWER.keys(), n=1, cutoff=cutoff)
        if hit:
            return _LOWER[hit[0]]
    return None
