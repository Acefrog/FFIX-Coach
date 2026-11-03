"""learned.json: what the coach picks up from your runs (screens, fights), shared with
the editor, which can make it forget things while the coach runs."""

from __future__ import annotations

import json
import re

from pathlib import Path
from fights_data import known_formations, known_screens
from tuning import BATTLE_SCREENS_KEPT, FORMATION_VERSION


class Learned:
    """Things the coach picks up from your runs, saved between sessions."""

    SECTIONS = ("fields", "formations", "battle_screens")

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime = None
        # What this copy changed since it last wrote the file: when the editor wrote it in
        # between (Forget), these go on top of the editor's version instead of replacing it.
        self._changed: dict[tuple[str, str], object] = {}     # (section, key) -> value (None: removed)
        self._changed_top: set[str] = set()
        self.data = {"battle_fp": None, "fp_unusable": False, "formations": {}, "fields": {}}
        if path and path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
                self._mtime = path.stat().st_mtime
            except (ValueError, OSError):
                pass
        self._merge_numbered()
        if self.data.get("formation_version") != FORMATION_VERSION:
            # Older versions could learn from fights that weren't real (or from leftover
            # HP in empty slots), and the screens saved alongside them. Start clean.
            self.data.update({"formations": {}, "fields": {}, "battle_fp": None,
                              "fp_unusable": False, "formation_version": FORMATION_VERSION})

    def _merge_numbered(self) -> None:
        """Rooms used to be kept per fight in order (Mistodons, Mistodons#2...): they're one
        list now, so a route with more or fewer of those fights finds them all."""
        screens = self.data.get("battle_screens") or {}
        for k in [k for k in screens if re.search(r"#\d+$", k)]:
            base = re.sub(r"#\d+$", "", k)
            have = list(screens.get(base) or [])
            screens[base] = (have + [f for f in screens.pop(k) or [] if f not in have])[-BATTLE_SCREENS_KEPT:]

    def _put(self, section: str, key: str, value) -> None:
        """Set (or, with None, remove) one learned thing, remembering it was this copy's change."""
        d = self.data.setdefault(section, {})
        if value is None:
            d.pop(key, None)
        else:
            d[key] = value
        self._changed[(section, key)] = value

    def set_flag(self, key: str, value) -> None:
        self.data[key] = value
        self._changed_top.add(key)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        from paths import write_atomic
        try:
            m = self.path.stat().st_mtime if self.path.exists() else None
            if m is not None and m != self._mtime:
                # Someone else (the editor) wrote it since: start from theirs, add ours.
                try:
                    disk = json.loads(self.path.read_text())
                except ValueError:
                    disk = None
                if isinstance(disk, dict) and disk.get("formation_version") == FORMATION_VERSION:
                    merged = dict(disk)
                    for k in self._changed_top:
                        merged[k] = self.data.get(k)
                    for s in self.SECTIONS:
                        merged[s] = dict(disk.get(s) or {})
                    for (s, k), v in self._changed.items():
                        if v is None:
                            merged[s].pop(k, None)
                        else:
                            merged[s][k] = v
                    self.data = merged
            write_atomic(self.path, json.dumps(self.data, indent=1))
            self._mtime = self.path.stat().st_mtime
            self._changed.clear()
            self._changed_top.clear()
        except OSError:
            pass

    def refresh(self) -> bool:
        """Load learned.json again if something else changed it (the editor forgetting
        what was learned for a step). True if it was reloaded."""
        if not self.path:
            return False
        try:
            m = self.path.stat().st_mtime
            if m == self._mtime:
                return False
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return False
        self._mtime = m
        self.data.update(data)
        for k in self.SECTIONS:
            self.data[k] = data.get(k, {})
        return True

    def forget_step(self, key: str, route: str = "", fight_keys: list[str] | None = None) -> None:
        """Forget everything learned for one step (its screen; a fight's HP and enemies)."""
        for k in ([f"{route}|{key}"] if route else []) + [key]:
            self._put("fields", k, None)
            if k.startswith("move:"):
                self._put("fields", k.replace("move:", "menu:", 1), None)
        for k in fight_keys or []:
            for part in ("formations", "battle_screens"):
                for kk in [k] + [x for x in self.data.get(part) or {} if re.fullmatch(re.escape(k) + r"#\d+", x)]:
                    self._put(part, kk, None)
        self.save()

    # battle code fingerprint
    @property
    def battle_fp(self) -> bytes | None:
        fp = self.data.get("battle_fp")
        return bytes.fromhex(fp) if fp and not self.data.get("fp_unusable") else None

    def set_battle_fp(self, sig: bytes) -> None:
        self.set_flag("battle_fp", sig.hex())

    def mark_fp_unusable(self) -> None:
        self.set_flag("fp_unusable", True)

    # formations and fields
    def formation(self, boss_id: str) -> list[int] | None:
        return self.data["formations"].get(boss_id)

    def formations(self, boss_id: str) -> list[list[int]]:
        """Every set of enemy HP learned for a fight (one with several fights: each kind of
        line-up seen, Zombies then Dracozombie), then any the fight list has built in."""
        f = self.data["formations"]
        keys = [boss_id] + sorted((k for k in f if re.fullmatch(re.escape(boss_id) + r"#\d+", k)),
                                  key=lambda k: int(k.rsplit("#", 1)[1]))
        own = [list(f[k]) for k in keys if f.get(k)]
        return own + [x for x in known_formations(boss_id) if x not in own]

    def add_formation(self, boss_id: str, formation: list[int]) -> None:
        """Learn one more line-up for a fight with several fights (kept if already known)."""
        if list(formation) in self.formations(boss_id):
            return
        f, key, n = self.data["formations"], boss_id, 2
        while f.get(key):
            key, n = f"{boss_id}#{n}", n + 1
        self.set_formation(key, formation)

    def set_formation(self, boss_id: str, formation: list[int]) -> None:
        self._put("formations", boss_id, list(formation))
        self.save()

    def battle_screens(self, boss_id: str) -> list[int]:
        """Where a fight starts from: learned on your runs, then the fight list's own."""
        own = self._own_screens(boss_id)
        return own + [f for f in known_screens(boss_id) if f not in own]

    def _own_screens(self, boss_id: str) -> list[int]:
        return list(self.data.get("battle_screens", {}).get(boss_id) or [])

    def add_battle_screen(self, boss_id: str, field_id: int) -> bool:
        """Remember a screen this fight started from (its last few). True if it was new."""
        if field_id in self.battle_screens(boss_id):
            return False
        have = self._own_screens(boss_id)
        self._put("battle_screens", boss_id, (have + [int(field_id)])[-BATTLE_SCREENS_KEPT:])
        self.save()
        return True

    def field(self, key: str, route: str = "") -> int | None:
        """The screen a step starts on. Learned per route; older learning (shared by all
        routes) is used until this route has its own."""
        fields = self.data["fields"]
        for k in ([f"{route}|{key}"] if route else []) + [key]:
            got = fields.get(k)
            if got is None and "move:" in k:
                got = fields.get(k.replace("move:", "menu:", 1))   # learned when it was a menu step
            if got is not None:
                return got
        return None

    def set_field(self, key: str, field_id: int, route: str = "") -> None:
        self._put("fields", f"{route}|{key}" if route else key, field_id)
        self.save()
