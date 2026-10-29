"""Watch the game picture to see whether a steal worked.

When Zidane steals, FF9 shows a message box at the top of the battle screen:
"Stole <item>!" on success, or a "couldn't steal" style message if not. This
module captures that part of DuckStation's window, reads the text with the
text recognition built into Windows 10/11 (through the `winsdk` package), and
reports what was stolen.

    pip install -r requirements-steal.txt

Nothing here reads game memory, and nothing is sent anywhere: the screenshot
is read on your computer and thrown away.
"""

from __future__ import annotations

import ctypes
import difflib
import queue
import re
import sys
import threading
import time

from layout import picture_rect

# Every item name in FFIX (inventory order), for matching what the screen says.
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


# Key items the route picks up (not in the item list above).
# Key items aren't in the bag: they're seen on screen ("Received ...!"). The game's list.
KEY_ITEMS = ["Moogle's Flute", "Beautiful Potion", "Unusual Potion", "Strange Potion", "Gysahl Greens", "Bell",
             "Ticket", "Silver Pendant", "Continental Map", "Blank's Medicine", "World Map", "Gate Pass",
             "Gizamaluke Bell", "Kupo Nut", "Holy Bell", "Protection Bell", "Supersoft", "Blue Stone",
             "Red Stone", "Yellow Stone", "Green Stone", "Memory Earring", "Desert Star", "Falcon Claw",
             "Gulug Stone", "Fire Mirror", "Water Mirror", "Wind Mirror", "Earth Mirror",
             "King of Jump Rope", "Autograph", "Moogle Suit", "Master Hunter", "Moccha Coffee",
             "Kirman Coffee", "Athlete Queen", "Mayor's Key", "Mini-Brahne", "Burman Coffee",
             "Mini-Prima Vista", "Mini-Cid", "Mini-Burmecia", "Doga's Artifact", "Une's Mirror", "Rat Tail",
             "Griffin's Heart", "Magical Fingertip", "Superslick", "Rank S Medal", "Stellazzio Coin",
             "Chocograph", "Strategy Guide"]
_LOWER = {i.lower(): i for i in ITEMS + KEY_ITEMS}

REPEAT_SECONDS = 2.5         # the same message read again this soon is the same message
MESSAGE_TOP_SHARE = 0.30     # the message box sits in the top part of the picture


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


_GOT_WORDS = ("received", "obtained", "found")


def _received(text: str) -> str | None:
    """The item in "Received <item>!" (also "Found"/"Obtained"), or None. Strict, so that
    dialogue ("we haven't received the minister's authorization") doesn't count: the
    item has to come straight after the word and run up to the "!"."""
    raw = text.replace("’", "'")
    for m in re.finditer(r"[A-Za-z0-9']+", raw):
        w = m.group(0).lower()
        if max(difflib.SequenceMatcher(None, w, v).ratio() for v in _GOT_WORDS) < 0.85:
            continue
        end = raw.find("!", m.end())
        if end < 0 or end - m.end() > 40:
            continue
        seg = _norm(raw[m.end():end])
        seg = re.sub(r"^(x ?\d+|\d+ ?x)\s+", "", seg)
        if re.fullmatch(r"[\d ]*gil", seg):                  # "2,700 Gil" reads as "2 700 gil"
            return "Gil"
        if seg in _LOWER:
            return _LOWER[seg]
        hit = difflib.get_close_matches(seg, _LOWER.keys(), n=1, cutoff=0.85)
        if hit:
            return _LOWER[hit[0]]
    return None


def parse_message(text: str) -> tuple[str, str | None] | None:
    """('stole', item) for "Stole <item>!", ('failed', None) for a failed steal,
    ('got', item) for "Received <item>!" (chests, hidden items, rewards; gil is 'Gil'), else None."""
    t = _norm(text)
    if not t or "seen on screen" in t or "traceback" in t:
        return None                     # the coach's own console, if it's ever read
    if re.search(r"\bsav\b|date modified|save states|file name", t):
        return None                     # a file window (save-state names), not the game
    got = _received(text)
    if got:
        return ("got", got)
    words = t.split()
    for i, w in enumerate(words):
        # OCR can misread letters ("stoie", "st0le"), so accept near misses.
        if difflib.SequenceMatcher(None, w, "stole").ratio() >= 0.8:
            item = match_item(" ".join(words[i + 1:]))
            if item:
                return ("stole", item)
    if re.search(r"couldn.?t steal|could not steal|doesn.?t have anything|nothing to steal", t):
        return ("failed", None)
    return None


# ----------------------------------------------------------------------
# Windows: window position, screen capture, text recognition
# ----------------------------------------------------------------------

def main_window(pid: int) -> int | None:
    """Handle of the process's biggest visible window (DuckStation's game window)."""
    if sys.platform != "win32" or not pid:
        return None
    user32 = ctypes.windll.user32
    best: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        hwnd = ctypes.c_void_p(hwnd)
        if not user32.IsWindowVisible(hwnd):
            return True
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            r = (ctypes.c_long * 4)()
            user32.GetClientRect(hwnd, r)
            area = r[2] * r[3]
            if area > 0:
                best.append((area, hwnd.value))
        return True

    user32.EnumWindows(cb, 0)
    return max(best)[1] if best else None


def client_rect_for_pid(pid: int) -> tuple[int, int, int, int] | None:
    """Screen rectangle of the inside of the process's biggest visible window."""
    h = main_window(pid)
    if h is None:
        return None
    user32 = ctypes.windll.user32
    hwnd = ctypes.c_void_p(h)
    r = (ctypes.c_long * 4)()
    user32.GetClientRect(hwnd, r)
    pt = (ctypes.c_long * 2)(0, 0)
    user32.ClientToScreen(hwnd, pt)
    return pt[0], pt[1], pt[0] + r[2], pt[1] + r[3]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


def capture(x: int, y: int, w: int, h: int) -> bytes | None:
    """BGRA pixels (top row first) of a screen rectangle."""
    if sys.platform != "win32" or w <= 0 or h <= 0:
        return None
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    user32.GetDC.restype = ctypes.c_void_p
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(ctypes.c_void_p(screen))
    bmp = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(screen), w, h)
    try:
        gdi32.SelectObject(ctypes.c_void_p(mem), ctypes.c_void_p(bmp))
        SRCCOPY = 0x00CC0020
        if not gdi32.BitBlt(ctypes.c_void_p(mem), 0, 0, w, h, ctypes.c_void_p(screen), x, y, SRCCOPY):
            return None
        info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(ctypes.c_void_p(mem), ctypes.c_void_p(bmp), 0, h, buf, ctypes.byref(info), 0):
            return None
        return buf.raw
    finally:
        gdi32.DeleteObject(ctypes.c_void_p(bmp))
        gdi32.DeleteDC(ctypes.c_void_p(mem))
        user32.ReleaseDC(None, ctypes.c_void_p(screen))


def capture_window(hwnd: int, x: int, y: int, w: int, h: int) -> bytes | None:
    """BGRA pixels of part of a window's inside (x, y relative to its top-left), taken
    from the window itself, so other windows on top of it don't get in the way.
    None if Windows hands back a blank picture (some graphics modes)."""
    if sys.platform != "win32" or w <= 0 or h <= 0:
        return None
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    user32.GetDC.restype = ctypes.c_void_p
    hw = ctypes.c_void_p(hwnd)
    r = (ctypes.c_long * 4)()
    user32.GetClientRect(hw, r)
    cw, ch = r[2], r[3]
    if cw <= 0 or ch <= 0:
        return None
    wdc = user32.GetDC(hw)
    mem = gdi32.CreateCompatibleDC(ctypes.c_void_p(wdc))
    bmp = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(wdc), cw, ch)
    try:
        gdi32.SelectObject(ctypes.c_void_p(mem), ctypes.c_void_p(bmp))
        PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2
        if not user32.PrintWindow(hw, ctypes.c_void_p(mem), PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), cw, -ch, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(cw * ch * 4)
        if not gdi32.GetDIBits(ctypes.c_void_p(mem), ctypes.c_void_p(bmp), 0, ch, buf, ctypes.byref(info), 0):
            return None
        raw = buf.raw
    finally:
        gdi32.DeleteObject(ctypes.c_void_p(bmp))
        gdi32.DeleteDC(ctypes.c_void_p(mem))
        user32.ReleaseDC(hw, ctypes.c_void_p(wdc))
    x, y = max(0, x), max(0, y)
    w, h = min(w, cw - x), min(h, ch - y)
    rows = [raw[(y + j) * cw * 4 + x * 4:(y + j) * cw * 4 + (x + w) * 4] for j in range(h)]
    out = b"".join(rows)
    if not any(out[i] for i in range(0, len(out), max(4, len(out) // 4096 * 4))):
        return None                                   # all black: this graphics mode can't be captured
    return out


class Ocr:
    """Windows' built-in text recognition. `available` is False if it can't be used."""

    def __init__(self):
        self.available = False
        self.problem = ""
        if sys.platform != "win32":
            self.problem = "only works on Windows"
            return
        try:      # the maintained "winrt-..." packages (work on every current Python)
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
            from winrt.windows.storage.streams import DataWriter
            import winrt.windows.foundation  # noqa: F401  (needed to wait for results)
            self._kind = "winrt"
        except ImportError:
            try:  # the older all-in-one "winsdk" package, if that's what's installed
                from winsdk.windows.media.ocr import OcrEngine
                from winsdk.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
                from winsdk.windows.storage.streams import DataWriter
                self._kind = "winsdk"
            except ImportError:
                self.problem = "run:  pip install -r app\\requirements-steal.txt"
                return
        self._sb, self._fmt, self._alpha, self._writer = SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode, DataWriter
        self._engine_cls = OcrEngine
        self._engines: dict[int, object] = {}
        self.engine = self._engine()
        if self.engine is None:
            self.problem = "Windows has no text-recognition language installed (add English in Settings > Language)"
            return
        self.max_dim = int(getattr(OcrEngine, "max_image_dimension", 2600))
        self.available = True

    def _engine(self):
        """One engine per thread: Windows' objects are safest used where they were made."""
        tid = threading.get_ident()
        if tid not in self._engines:
            try:                                 # this thread may not be set up for Windows calls yet
                if self._kind == "winrt":
                    import winrt.runtime as rt
                    rt.init_apartment(rt.MTA)
                else:
                    from winsdk import _winrt
                    _winrt.init_apartment()
            except Exception:
                pass                             # already set up: fine
            self._engines[tid] = self._engine_cls.try_create_from_user_profile_languages()
        return self._engines[tid]

    def read(self, w: int, h: int, bgra: bytes) -> str:
        import asyncio
        writer = self._writer()
        writer.write_bytes(bgra)
        # Screen captures have a blank (0) alpha byte: tell Windows to ignore it,
        # or it reads the picture as fully transparent and finds no text.
        bmp = self._sb.create_copy_from_buffer(writer.detach_buffer(), self._fmt.BGRA8, w, h,
                                               self._alpha.IGNORE)
        engine = self._engine()

        async def go():
            return await engine.recognize_async(bmp)

        return asyncio.run(go()).text or ""


def message_rect(pid: int, ov: dict, full: bool = False) -> tuple[int, int, int, int] | None:
    """Screen rectangle of the top of the game picture, where battle messages appear
    (or the whole picture, for item messages on the field, which can show anywhere)."""
    client = client_rect_for_pid(pid)
    if client is None:
        return None
    sh = ctypes.windll.user32.GetSystemMetrics(1) if sys.platform == "win32" else 10_000
    pic = picture_rect(client, float(ov.get("aspect", 4 / 3)), int(ov.get("duckstation_top_bars", 60)),
                       int(ov.get("duckstation_bottom_bar", 25)), sh)
    if pic is None:
        return None
    x, y, w, h = pic
    return (x, y, w, h) if full else (x, y, w, max(40, int(h * MESSAGE_TOP_SHARE)))


class StealWatcher:
    """Reads the message box in the background while there's a steal to check.

    `events` gets ('stole', item) or ('failed', None) once per message shown.
    """

    def __init__(self, pid: int, overlay_cfg: dict, interval: float = 0.15):
        self.pid, self.ov, self.interval = pid, overlay_cfg, interval
        self.ocr = Ocr()
        self.events: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
        self.log_path = None             # set by main.py: where screen_log.txt goes
        self.screen_only = False         # True if window capture comes back blank here
        self.active = threading.Event()
        self.last_text = ""
        self.last_error = ""
        self.full = False
        self._last_kind = None
        self._last_event: tuple[tuple, float] | None = None
        if self.ocr.available:
            threading.Thread(target=self._run, daemon=True).start()

    def set_active(self, on: bool, full: bool = False) -> None:
        """on: read the screen. full: the whole picture (field) instead of the top (battle)."""
        if full != self.full:
            self.full = full
            self._last_kind = None
        (self.active.set if on else self.active.clear)()
        if not on:
            self._last_kind = None

    def poll(self) -> list[tuple[str, str | None]]:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def grab(self) -> tuple[tuple[int, int, int, int] | None, bytes | None]:
        r = message_rect(self.pid, self.ov, self.full)
        if r is None:
            return None, None
        x, y, w, h = r
        w, h = min(w, self.ocr.max_dim), min(h, self.ocr.max_dim)
        # Take it from DuckStation's own window, so a console or other window on top of
        # the game doesn't get read; fall back to the screen if that comes back blank.
        hwnd = main_window(self.pid)
        client = client_rect_for_pid(self.pid)
        pixels = None
        if hwnd and client and not self.screen_only:
            # Kept inside the window, so the picture is exactly the size reported with it.
            cx, cy = max(0, x - client[0]), max(0, y - client[1])
            x, y = client[0] + cx, client[1] + cy
            w, h = min(w, client[2] - x), min(h, client[3] - y)
            if w <= 0 or h <= 0:
                return (x, y, max(w, 0), max(h, 0)), b""
            pixels = capture_window(hwnd, cx, cy, w, h)
            if pixels is not None:
                self._window_ok, self._blanks = True, 0
                return (x, y, w, h), pixels
            if getattr(self, "_window_ok", False):
                # The game itself is black for a moment (a fade between screens): nothing to
                # read. Don't switch to copying the screen, which reads whatever is on top.
                return (x, y, w, h), b""
            self._blanks = getattr(self, "_blanks", 0) + 1
            if self._blanks < 40:                     # about 6 seconds of nothing from the start
                return (x, y, w, h), b""
            self.screen_only = True                   # this setup never gives the window's picture
            self._log("", "DuckStation's window always comes back blank; reading the screen instead "
                          "(keep other windows off the game picture)")
        return (x, y, w, h), capture(x, y, w, h)

    def read_once(self) -> str:
        r, pixels = self.grab()
        if r is None:
            raise RuntimeError("can't find DuckStation's window")
        if pixels == b"":
            return ""                                 # black frame: nothing on screen
        if not pixels:
            raise RuntimeError("screen capture failed")
        return self.ocr.read(r[2], r[3], pixels)

    def _run(self) -> None:
        while True:
            self.active.wait()
            try:
                self.last_text = self.read_once()
                self.last_error = ""
            except Exception as e:      # keep watching, but say why it failed
                self.last_error = f"{type(e).__name__}: {e}"
                self.last_text = ""
            self.handle(self.last_text)
            self._log(self.last_text, self.last_error)
            time.sleep(self.interval)

    def _log(self, text: str, error: str) -> None:
        """Note each new thing read off the screen in screen_log.txt (to look at afterwards)."""
        line = (text or "").strip().replace("\n", " / ")
        if error:
            line = f"ERROR {error}"
        if not line or line == getattr(self, "_logged", None):
            return
        self._logged = line
        if self.log_path is None:
            return
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > 500_000:
                self.log_path.replace(self.log_path.with_suffix(".old.txt"))
            with self.log_path.open("a", encoding="utf-8") as f:
                mode = "field" if self.full else "battle"
                f.write(f"{time.strftime('%H:%M:%S')} [{mode}] {line[:300]} -> {parse_message(text)}\n")
        except OSError:
            pass

    def handle(self, text: str, now: float | None = None) -> None:
        """Turn one reading of the screen into an event, once per message shown.
        The whole message counts, so "Stole Potion" then "Stole Mage Masher" are two."""
        parsed = parse_message(text)
        now = time.monotonic() if now is None else now
        if parsed and parsed != self._last_kind:
            # A flicker in the reading can make one message look like two.
            if not (self._last_event and self._last_event[0] == parsed
                    and now - self._last_event[1] < REPEAT_SECONDS):
                self.events.put(parsed)
            self._last_event = (parsed, now)
        self._last_kind = parsed


def save_bmp(path, w: int, h: int, bgra: bytes) -> None:
    """Write top-down BGRA pixels as a .bmp you can open, to see what was captured."""
    import struct
    size = 54 + len(bgra)
    header = struct.pack("<2sIHHI", b"BM", size, 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, len(bgra), 2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(header + info + bgra)
