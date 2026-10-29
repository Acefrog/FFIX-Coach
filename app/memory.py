"""Read Final Fantasy IX's PS1 RAM out of a running DuckStation process (Windows).

DuckStation exports a symbol called ``RAM`` (a pointer to the emulated 2 MB of
PS1 main RAM) so external tools such as autosplitters can find it. We read the
export table of the DuckStation module, follow that pointer, and then read
game values as offsets into PS1 RAM.

If your DuckStation build is too old to export ``RAM``, set ``ram_base`` in
config.json by hand (see README) and the export lookup is skipped.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
import sys
from dataclasses import dataclass, field

PS1_RAM_SIZE = 0x200000  # 2 MB
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010


class MemoryError_(Exception):
    """Raised when DuckStation or its RAM can't be found."""


# --------------------------------------------------------------------------
# Low-level Win32 process access (no third-party packages needed)
# --------------------------------------------------------------------------

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("th32ModuleID", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("GlblcntUsage", wt.DWORD),
            ("ProccntUsage", wt.DWORD),
            ("modBaseAddr", ctypes.c_void_p),
            ("modBaseSize", wt.DWORD),
            ("hModule", wt.HMODULE),
            ("szModule", wt.WCHAR * 256),
            ("szExePath", wt.WCHAR * 260),
        ]

    _k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    _k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _k32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    _k32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.ReadProcessMemory.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    INVALID_HANDLE = ctypes.c_void_p(-1).value


def _find_process(name_prefix: str) -> tuple[int, str]:
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE:
        raise MemoryError_("Couldn't list processes.")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            exe = entry.szExeFile
            if exe.lower().startswith(name_prefix.lower()):
                return entry.th32ProcessID, exe
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    raise MemoryError_(
        f"No running process starting with '{name_prefix}'. Is DuckStation open?"
    )


def _find_module(pid: int, exe_name: str) -> tuple[int, int]:
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == INVALID_HANDLE:
        raise MemoryError_("Couldn't list DuckStation's modules (try running as admin).")
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == exe_name.lower():
                return entry.modBaseAddr, entry.modBaseSize
            ok = _k32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    raise MemoryError_("Found DuckStation but not its main module.")


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

class RamSource:
    """Anything that can return bytes at a PS1 RAM offset."""

    def read(self, offset: int, size: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def u8(self, off: int) -> int:
        return self.read(off, 1)[0]

    def u16(self, off: int) -> int:
        return struct.unpack("<H", self.read(off, 2))[0]

    def u32(self, off: int) -> int:
        return struct.unpack("<I", self.read(off, 4))[0]

    def value(self, off: int, width: int) -> int:
        return {1: self.u8, 2: self.u16, 4: self.u32}[width](off)

    def snapshot(self) -> bytes:
        return self.read(0, PS1_RAM_SIZE)


class DuckStationRam(RamSource):
    """Live PS1 RAM inside a DuckStation process."""

    def __init__(self, process_prefix: str = "duckstation", ram_base: int | None = None):
        if sys.platform != "win32":
            raise MemoryError_("Live memory reading only works on Windows.")
        self.pid, self.exe = _find_process(process_prefix)
        self.handle = _k32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, self.pid
        )
        if not self.handle:
            raise MemoryError_("Couldn't open DuckStation (try running as admin).")
        if ram_base:
            self.ram_base = ram_base
        else:
            try:
                self.ram_base = self._ram_from_export()
            except MemoryError_:
                self.close()                           # tried again a second later: don't pile up handles
                raise

    # -- process memory ---------------------------------------------------
    def _raw(self, addr: int, size: int) -> bytes:
        buf = (ctypes.c_char * size)()
        got = ctypes.c_size_t(0)
        if not _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size,
                                      ctypes.byref(got)) or got.value != size:
            raise MemoryError_(f"Read failed at 0x{addr:X} (game closed or reset?)")
        return bytes(buf)

    def _ram_from_export(self) -> int:
        """Walk the PE export table of DuckStation for the 'RAM' symbol."""
        base, _size = _find_module(self.pid, self.exe)
        e_lfanew = struct.unpack("<I", self._raw(base + 0x3C, 4))[0]
        magic = struct.unpack("<H", self._raw(base + e_lfanew + 0x18, 2))[0]
        if magic != 0x20B:
            raise MemoryError_("Expected a 64-bit DuckStation build.")
        export_rva = struct.unpack("<I", self._raw(base + e_lfanew + 0x18 + 0x70, 4))[0]
        if not export_rva:
            raise MemoryError_(
                "This DuckStation build has no export table. Update DuckStation or "
                "set ram_base in config.json."
            )
        exp = self._raw(base + export_rva, 40)
        n_names = struct.unpack_from("<I", exp, 24)[0]
        funcs_rva, names_rva, ords_rva = struct.unpack_from("<III", exp, 28)
        names = self._raw(base + names_rva, 4 * n_names)
        ords = self._raw(base + ords_rva, 2 * n_names)
        for i in range(n_names):
            name_rva = struct.unpack_from("<I", names, 4 * i)[0]
            name = self._raw(base + name_rva, 16).split(b"\0", 1)[0]
            if name == b"RAM":
                ordinal = struct.unpack_from("<H", ords, 2 * i)[0]
                func_rva = struct.unpack("<I", self._raw(base + funcs_rva + 4 * ordinal, 4))[0]
                ptr = struct.unpack("<Q", self._raw(base + func_rva, 8))[0]
                if not ptr:
                    raise MemoryError_("DuckStation's RAM pointer is empty. Start the game first.")
                return ptr
        raise MemoryError_(
            "DuckStation doesn't export 'RAM'. Update DuckStation or set ram_base in config.json."
        )

    def read(self, offset: int, size: int) -> bytes:
        return self._raw(self.ram_base + (offset & (PS1_RAM_SIZE - 1)), size)

    def close(self) -> None:
        if getattr(self, "handle", None):
            _k32.CloseHandle(self.handle)
            self.handle = None


class FakeRam(RamSource):
    """In-memory RAM used by tests and --simulate mode."""

    def __init__(self):
        self.mem = bytearray(PS1_RAM_SIZE)

    def read(self, offset: int, size: int) -> bytes:
        offset &= PS1_RAM_SIZE - 1
        return bytes(self.mem[offset:offset + size])

    def write(self, offset: int, value: int, width: int = 2) -> None:
        fmt = {1: "<B", 2: "<H", 4: "<I"}[width]
        struct.pack_into(fmt, self.mem, offset, value)


# --------------------------------------------------------------------------
# Game-level view
# --------------------------------------------------------------------------

SLOTS = 8          # battle table: 4 party slots, then 4 enemy slots
PARTY_SLOTS = 4


@dataclass
class Addresses:
    """PS1 RAM offsets.

    Battle data is one table of 8 slots (party 0-3, enemies 4-7), each 0xD0
    bytes apart, with current HP at +0 and the ATB bar at +4. The default base
    0x108F24 comes from Lil_Gecko's FFIX TAS notes for NTSC-U; other versions
    (PAL, JP) put the table elsewhere. `python finder.py battle` finds it.
    """

    battle_base: int = 0x108F24
    slot_stride: int = 0xD0
    hp_off: int = 0
    atb_off: int = 4
    hp_width: int = 2
    atb_width: int = 2
    igt: int = 0x8B02C
    encounter_field: int = 0x7954F
    # Start of the battle code overlay (Data Crystal RAM map). Its bytes differ
    # between field and battle, which gives an exact in-battle signal.
    battle_module: int = 0x1044B0
    battle_sig_len: int = 64
    # Not published for PSX; find yours with `python finder.py field`.
    field_id: int | None = None
    field_id_width: int = 2
    # Which party slot each named character is in (slot 0 = top of the party list).
    party_slots: dict[str, int] = field(default_factory=lambda: {"zidane": 0})
    # Set by `python finder.py battle` when party HP is found somewhere other
    # than the shared battle table. None = read party HP from the battle table.
    party_hp_base: int | None = None
    party_stride: int | None = None
    # Set by `python finder.py fights`: bytes that hold one value on the field and
    # another in battle. [(address, field_value, battle_value)]; majority vote.
    battle_flags: list[tuple[int, int, int]] = field(default_factory=list)
    # Set by `python finder.py stats <name>`: {name: {"exp": addr, "level": addr}}
    chars: dict[str, dict[str, int]] = field(default_factory=dict)
    # Set by `python finder.py ap <name> <ability>`: {"zidane:Flee": {"addr", "max", "width"}}
    ap: dict[str, dict[str, int]] = field(default_factory=dict)
    # Set by `python finder.py menu`: bytes that hold one value with the menu closed and
    # another with it open. [(address, closed_value, open_value)]; majority vote.
    menu_flags: list[tuple[int, int, int]] = field(default_factory=list)
    # Set by `python finder.py equip <name>`: where the 5 equipment slots (weapon, head,
    # wrist, armour, accessory; item IDs) sit, relative to each character's EXP address.
    equip_off: int | None = None
    # Battle row: one bit per character, at exp + row_off. Found by "finder.py row".
    row_off: int | None = None
    row_bit: int = 0
    row_back: int = 1                 # the bit's value when in the back row
    # Config menu: setting -> (address, bit mask, {value name: masked byte}). "finder.py config".
    config: dict[str, tuple[int, int, dict[str, int]]] = field(default_factory=dict)
    # Set by `python finder.py ability <name>`: {ability: (offset from EXP address, bit)}
    # for support abilities switched on.
    ability_bits: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Item bag: a list of 2-byte entries (item ID, count); ID 255 = empty. Found by "finder.py items".
    inventory: int | None = None
    # Which battle this is (FF9's battle scene number): "finder.py battleid".
    battle_id: int | None = None
    battle_id_width: int = 2
    gil: int | None = None
    inventory_entries: int = 256
    inventory_id_first: bool = True

    def slot(self, k: int, off: int) -> int:
        return self.battle_base + k * self.slot_stride + off

    @property
    def party_hp_addrs(self) -> list[int]:
        if self.party_hp_base is not None:
            stride = self.party_stride or self.slot_stride
            return [self.party_hp_base + k * stride for k in range(PARTY_SLOTS)]
        return [self.slot(k, self.hp_off) for k in range(PARTY_SLOTS)]

    @property
    def party_atb(self) -> list[int]:
        return [self.slot(k, self.atb_off) for k in range(PARTY_SLOTS)]

    @property
    def enemy_hp(self) -> list[int]:
        return [self.slot(k, self.hp_off) for k in range(PARTY_SLOTS, SLOTS)]

    @property
    def enemy_atb(self) -> list[int]:
        return [self.slot(k, self.atb_off) for k in range(PARTY_SLOTS, SLOTS)]

    @classmethod
    def from_config(cls, cfg: dict) -> "Addresses":
        def h(v):
            return int(v, 16) if isinstance(v, str) else int(v)

        a = cls()
        addr = cfg.get("addresses", {})
        if addr.get("battle_base"):
            a.battle_base = h(addr["battle_base"])
        elif addr.get("enemy_hp"):  # older config: enemy 1 HP sits 4 slots in
            a.battle_base = h(addr["enemy_hp"][0]) - PARTY_SLOTS * a.slot_stride
        for key in ("slot_stride", "hp_off", "atb_off", "igt", "encounter_field", "battle_module"):
            if addr.get(key) is not None:
                setattr(a, key, h(addr[key]))
        if addr.get("field_id"):
            a.field_id = h(addr["field_id"])
        if addr.get("party_hp_base"):
            a.party_hp_base = h(addr["party_hp_base"])
        if addr.get("party_stride"):
            a.party_stride = h(addr["party_stride"])
        a.battle_flags = [(h(f["addr"]), int(f["field"]), int(f["battle"]))
                          for f in addr.get("battle_flags", []) or []]
        a.chars = {name: {k: h(v) for k, v in rec.items() if v is not None}
                   for name, rec in (addr.get("chars") or {}).items()}
        add_guest(a.chars)
        a.ap = {key: {"addr": h(v["addr"]), "max": int(v["max"]), "width": int(v.get("width", 1))}
                for key, v in (addr.get("ap") or {}).items()}
        a.menu_flags = [(h(f["addr"]), int(f["closed"]), int(f["open"]))
                        for f in addr.get("menu_flags", []) or []]
        if addr.get("equip_off") is not None:
            a.equip_off = int(addr["equip_off"])
        a.config = {k: (h(v["addr"]), int(v["mask"]), {n: int(x) for n, x in v.get("values", {}).items()})
                    for k, v in (addr.get("config") or {}).items()}
        if addr.get("row") and addr["row"].get("off") is not None:
            a.row_off, a.row_bit = int(addr["row"]["off"]), int(addr["row"].get("bit", 0))
            a.row_back = int(addr["row"].get("back", 1))
        if addr.get("battle_id"):
            a.battle_id = h(addr["battle_id"])
            a.battle_id_width = int(addr.get("battle_id_width", 2))
        if addr.get("gil"):
            a.gil = h(addr["gil"])
        if addr.get("inventory"):
            a.inventory = h(addr["inventory"])
            a.inventory_entries = int(addr.get("inventory_entries", 256))
            a.inventory_id_first = bool(addr.get("inventory_id_first", True))
        a.ability_bits = {k: (int(v[0]), int(v[1])) for k, v in (addr.get("ability_bits") or {}).items()}
        a.ability_bits = fill_abilities(a.ability_bits)
        for key in ("hp_width", "atb_width", "field_id_width", "battle_sig_len"):
            if key in addr:
                setattr(a, key, int(addr[key]))
        if isinstance(addr.get("party_slots"), dict):
            a.party_slots = {k: int(v) for k, v in addr["party_slots"].items() if v is not None}
        return a


@dataclass
class GameState:
    enemy_hp: list[int]
    enemy_atb: list[int]
    party_atb: list[int]
    party_hp_slots: list[int]
    igt: int
    encounters_possible: bool
    battle_sig: bytes = b""
    field_id: int | None = None
    party_hp: dict[str, int] = field(default_factory=dict)  # named, from party_slots
    battle_vote: bool | None = None       # from calibrated battle flags; None = not set up
    levels: dict[str, int] = field(default_factory=dict)
    exp: dict[str, int] = field(default_factory=dict)
    char_hp: dict[str, int] = field(default_factory=dict)   # each character's HP in their record (party check)
    ap: dict[str, tuple[int, int]] = field(default_factory=dict)  # "zidane:Flee" -> (have, need)
    menu_vote: bool | None = None         # menu open? None = not set up
    equips: dict[str, list[int]] = field(default_factory=dict)        # name -> 5 item IDs
    abilities: dict[str, set] = field(default_factory=dict)          # name -> support abilities on
    ability_known: frozenset = frozenset()                           # abilities the coach can see
    items: dict[int, int] | None = None   # item ID -> how many in the bag; None = not set up
    battle_id: int | None = None          # FF9's number for this battle; None = not set up
    enemy_blocks: list[bytes] = field(default_factory=list)  # each enemy slot's whole record
    rows: dict[str, str] = field(default_factory=dict)   # name -> "front"/"back"
    gil: int | None = None
    config: dict[str, int] = field(default_factory=dict)   # setting -> masked byte now
    config_values: dict[str, dict[str, int]] = field(default_factory=dict)   # setting -> value name -> byte


def add_guest(chars: dict) -> None:
    """The game keeps nine character records in a row: the eight party members, then one
    shared by whichever guest is with you (Blank, Marcus, Beatrix or Cinna). Its place
    follows from the spacing of the others."""
    z, v = chars.get("zidane", {}).get("exp"), chars.get("vivi", {}).get("exp")
    if z is not None and v is not None and "guest" not in chars:
        chars["guest"] = {"exp": z + 8 * (v - z)}


CHAR_RECORD = 0x84     # bytes read from each character's EXP on: equipment, AP, support bits


def read_ap(ram: RamSource, a: Addresses, recs: dict[str, bytes]) -> dict[str, tuple[int, int]]:
    """AP for every ability of every character ("zidane:Flee" -> (have, need)), from the
    AP bytes in their records. Abilities set up by hand with finder.py ap win."""
    from ability_data import ABILITY_LISTS, AP_OFFSET
    out = {}
    for n, rec in recs.items():
        for i, entry in enumerate(ABILITY_LISTS.get(n, [])):
            if entry and AP_OFFSET + i < len(rec):
                out[f"{n}:{entry[0]}"] = (rec[AP_OFFSET + i], entry[1])
    for k, v in a.ap.items():
        out[k] = (ram.value(v["addr"], v["width"]), v["max"])
    return out


def read_state(ram: RamSource, a: Addresses) -> GameState:
    party = [ram.value(x, a.hp_width) for x in a.party_hp_addrs]
    recs = {n: ram.read(r["exp"], CHAR_RECORD) for n, r in a.chars.items() if "exp" in r}
    return GameState(
        enemy_hp=[ram.value(x, a.hp_width) for x in a.enemy_hp],
        enemy_atb=[ram.value(x, a.atb_width) for x in a.enemy_atb],
        party_atb=[ram.value(x, a.atb_width) for x in a.party_atb],
        party_hp_slots=party,
        igt=ram.u32(a.igt),
        encounters_possible=ram.u8(a.encounter_field) != 0,
        battle_sig=ram.read(a.battle_module, a.battle_sig_len) if a.battle_sig_len else b"",
        field_id=ram.value(a.field_id, a.field_id_width) if a.field_id is not None else None,
        party_hp={name: party[k] for name, k in a.party_slots.items() if 0 <= k < PARTY_SLOTS},
        battle_vote=_vote(ram, a.battle_flags),
        levels={n: ram.u8(r["level"]) for n, r in a.chars.items() if "level" in r},
        exp={n: ram.u32(r["exp"]) for n, r in a.chars.items() if "exp" in r},
        char_hp={n: int.from_bytes(recs[n][4:6], "little") for n in recs if len(recs[n]) >= 6},
        ap=read_ap(ram, a, recs),
        menu_vote=_vote(ram, a.menu_flags),
        equips={n: list(recs[n][a.equip_off:a.equip_off + 5])
                for n in recs} if a.equip_off is not None else {},
        abilities={n: {name for name, (off, bit) in a.ability_bits.items()
                       if off < len(recs[n]) and recs[n][off] >> bit & 1}
                   for n in recs} if a.ability_bits else {},
        ability_known=frozenset(a.ability_bits),
        items=read_items(ram, a),
        battle_id=ram.value(a.battle_id, a.battle_id_width) if a.battle_id is not None else None,
        enemy_blocks=[ram.read(a.slot(k, 0), a.slot_stride) for k in range(PARTY_SLOTS, SLOTS)],
        gil=ram.u32(a.gil) if a.gil is not None else None,
        config={k: ram.u8(addr) & mask for k, (addr, mask, _) in a.config.items()},
        config_values={k: v for k, (_, _, v) in a.config.items()},
        rows={n: ("back" if (ram.u8(r["exp"] + a.row_off) >> a.row_bit & 1) == a.row_back else "front")
              for n, r in a.chars.items() if "exp" in r} if a.row_off is not None else {},
    )


def fill_abilities(known: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Support abilities are one bit each, in roughly the game's usual order. Each ability
    not found directly is placed from the nearest found one before it in that order (or
    after it, for the first few), so one gap or extra in the list only throws off the
    abilities between two found ones, and finding more fixes them."""
    from screen import ABILITIES
    from ability_data import SUPPORT_BITS_OFFSET
    anchors = sorted((ABILITIES.index(n), off * 8 + bit) for n, (off, bit) in known.items() if n in ABILITIES)
    if not anchors:
        # The game's own layout: support ability number n is bit n, from 124 bytes after EXP
        # (matches Protect Girls found by hand at byte 128 bit 6).
        anchors = [(0, SUPPORT_BITS_OFFSET * 8)]
    full = {}
    for i, n in enumerate(ABILITIES):
        before = [a for a in anchors if a[0] <= i]
        idx, g = before[-1] if before else anchors[0]
        full[n] = divmod(g + (i - idx), 8)
    full.update(known)
    return full


def parse_items(raw: bytes, id_first: bool = True) -> dict[int, int]:
    out: dict[int, int] = {}
    for k in range(0, len(raw) - 1, 2):
        i, n = (raw[k], raw[k + 1]) if id_first else (raw[k + 1], raw[k])
        if i != 255 and 0 < n <= 99:
            out[i] = out.get(i, 0) + n
    return out


def read_items(ram: RamSource, a: Addresses) -> dict[int, int] | None:
    if a.inventory is None:
        return None
    # Always the whole bag: new items (bought, synthesised, found) go after the ones you had
    # when it was set up.
    return parse_items(ram.read(a.inventory, max(a.inventory_entries, 256) * 2), a.inventory_id_first)


def _vote(ram: RamSource, flags: list[tuple[int, int, int]]) -> bool | None:
    """True if most battle flags read their battle value, False if most read their field value."""
    if not flags:
        return None
    battle = field_ = 0
    for addr, fv, bv in flags:
        v = ram.u8(addr)
        if v == bv:
            battle += 1
        elif v == fv:
            field_ += 1
    if battle == field_ == 0:
        return None
    # In a fight only when most of ALL the flags say so. Away from the area they were
    # set up in, some field values differ, and those must not count towards "fight".
    return battle * 2 > len(flags)
