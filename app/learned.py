"""learned.json: what the coach picks up from your runs (screens, fights), shared with
the editor, which can make it forget things while the coach runs."""

from __future__ import annotations

import json

from pathlib import Path
from tuning import FORMATION_VERSION


class Learned:
    """Things the coach picks up from your runs, saved between sessions."""

    SECTIONS = ("fields", "formations", "fingerprints", "battle_ids", "random_here")

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
        if self.data.get("formation_version") != FORMATION_VERSION:
            # Older versions could learn from fights that weren't real (or from leftover
            # HP in empty slots), and the screens saved alongside them. Start clean.
            self.data.update({"formations": {}, "fields": {}, "battle_fp": None,
                              "fp_unusable": False, "formation_version": FORMATION_VERSION})

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
            for part in ("formations", "fingerprints", "battle_ids"):
                self._put(part, k, None)
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

    def set_formation(self, boss_id: str, formation: list[int], random_here: bool | None = None) -> None:
        self._put("formations", boss_id, list(formation))
        if random_here is not None:
            self._put("random_here", boss_id, bool(random_here))
        self.save()

    def random_here(self, boss_id: str) -> bool | None:
        """Whether random battles could happen where this fight was learned (None: not known)."""
        return self.data.get("random_here", {}).get(boss_id)

    def battle_id(self, boss_id: str) -> int | None:
        return self.data.setdefault("battle_ids", {}).get(boss_id)

    def set_battle_id(self, boss_id: str, value: int | None) -> None:
        self._put("battle_ids", boss_id, None if value is None else int(value))
        self.save()

    def fingerprint(self, boss_id: str) -> dict | None:
        return self.data.setdefault("fingerprints", {}).get(boss_id)

    def set_fingerprint(self, boss_id: str, fp: dict | None) -> None:
        self._put("fingerprints", boss_id, fp)
        self.save()

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

    def forget_field(self, key: str, route: str = "") -> None:
        for k in ([f"{route}|{key}"] if route else []) + [key]:
            self._put("fields", k, None)
        self.save()
