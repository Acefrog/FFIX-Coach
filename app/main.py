"""FFIX Coach: watches DuckStation and shows the next action from your route.

    python main.py              run the overlay against DuckStation
    python main.py --debug      also print live memory values to the console
    python main.py --simulate   no game needed: replays a fake Black Waltz 1 fight
    python main.py --console    print what the overlay would show (no window)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from coach import Coach, View, load_route
from memory import Addresses, DuckStationRam, FakeRam, MemoryError_, read_state

import routes
from paths import CONFIG, LEARNED, STOP_FILE, log


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))


# ----------------------------------------------------------------------
# Simulation: a scripted fake fight so you can see the overlay work
# ----------------------------------------------------------------------
class Simulator:
    """Drives FakeRam round and round: walking -> a random battle -> walking -> the next
    fight on the route (with that fight's typed HP) -> victory."""

    FIELD_CODE = b"\x11" * 64
    BATTLE_CODE = b"\x22" * 64
    CYCLE = 45                               # seconds per round
    BOSS_SCREEN = 8888                       # the route's fights start from here (simulate mode)

    def __init__(self, ram: FakeRam, a: Addresses, next_fight=None):
        self.ram, self.a, self.t = ram, a, 0
        self.next_fight = next_fight or (lambda: [472, 229])
        self.boss_hp: list[int] = []
        if a.field_id is None:
            a.field_id = 0x1F0010
        ram.write(a.party_hp_addrs[0], 105)   # Zidane, top of the party
        ram.write(a.party_hp_addrs[1], 1)     # Quina on 1 HP
        ram.write(a.encounter_field, 1, 1)
        self._code(False)

    def _code(self, battle: bool) -> None:
        """Into or out of a fight, the ways the coach can tell: the battle code and, when
        config.json has them, the battle flag bytes."""
        self.ram.mem[self.a.battle_module:self.a.battle_module + 64] = \
            self.BATTLE_CODE if battle else self.FIELD_CODE
        for addr, fv, bv in self.a.battle_flags:
            self.ram.write(addr, bv if battle else fv, 1)

    def _fight(self, t: int) -> None:
        r, a = self.ram, self.a
        r.write(a.enemy_atb[0], 100 + (t * 41) % 4000)
        # Zidane's bar climbs for 10 ticks, then empties (he acted)
        r.write(a.party_atb[0], 0 if t % 10 == 0 else 200 + (t % 10) * 300)

    def _enemies(self, hps: list[int]) -> None:
        for k, addr in enumerate(self.a.enemy_hp):
            self.ram.write(addr, hps[k] if k < len(hps) else 0)

    def tick(self) -> None:
        r, a, t = self.ram, self.a, self.t
        self.t += 1
        r.write(a.igt, 60 * 60 * 35 + t * 6, 4)
        phase = (t // 10) % self.CYCLE       # one phase = 1 second at 100 ms polling
        screen = 100 + 3 * (t // (10 * self.CYCLE))
        if phase < 5:                        # walking
            r.write(a.field_id, screen + 1)
        elif phase < 12:                     # short random fight, fled
            self._code(True)
            self._enemies([120])
            self._fight(t)
        elif phase < 18:                     # back on the field, next screen (then the boss's)
            self._code(False)
            self._enemies([])
            r.write(a.field_id, screen + 2 if phase < 17 else self.BOSS_SCREEN)
            self.boss_hp = []
        elif phase < 40:                     # the route's next fight
            if not self.boss_hp:
                self.boss_hp = list(self.next_fight() or [999])[:len(a.enemy_hp)]
            self._code(True)
            left = 1 - (phase - 18) / 20     # HP goes down, all gone for the last 2 seconds
            self._enemies([max(0, int(h * left)) if phase < 38 else 0 for h in self.boss_hp])
            if phase < 38:
                self._fight(t)
            r.write(a.party_hp_addrs[0], 107 if phase > 30 else 98)
        else:                                # victory, back to the field
            self._code(False)
            self._enemies([])
            r.write(a.field_id, screen + 3)


# ----------------------------------------------------------------------
def console_print(view: View) -> None:
    print("\n" + "=" * 60)
    print(view.header)
    print(view.title)
    for text, tone in view.badges:
        print(f"  [{tone}] {text}")
    for i, line in enumerate(view.lines):
        mark = "  >> " if view.highlight == i else "  ✓  " if i in view.done else "   - "
        print(mark + line)
    for line in view.stats:
        print("  " + line)
    if view.footer:
        print("  " + view.footer)
    if view.next_title:
        print("  --- Next: " + view.next_title)
        for line in view.next_lines:
            print("     " + line)


def key_actions(coach: Coach, cfg: dict):
    """The hotkeys from config.json: ({key: what it does}, hide/show key, quit key)."""
    keys = cfg.get("keys", {})
    actions = {
        keys.get("prev_step", "F7"): coach.prev_step,
        keys.get("next_step", "F8"): coach.next_step,
        keys.get("learn", "F6"): coach.learn,
        keys.get("learn_next", "Shift+F6"): coach.learn_next,
        keys.get("next_main_step", "Shift+F8"): coach.next_main_step,
        keys.get("prev_main_step", "Shift+F7"): coach.prev_main_step,
    }
    return actions, keys.get("toggle_overlay", "F9"), keys.get("quit", "F10")


class Runner:
    """One poll of the game: the Stop button and keys, edits to the
    route, the coach itself, the logs and the panel.

    Everything it uses is handed in (the game's memory, the hotkeys, where the stop
    file and logs go), so it can be run in tests without DuckStation, a window or the
    real files. main() builds one and calls tick() every poll."""

    def __init__(self, coach: Coach, ram, addrs: Addresses, cfg: dict, route_name: str, *,
                 sim: Simulator | None = None, hotkeys=None, debug: bool = False,
                 simulate: bool = False, logs=log, stop_file: Path = STOP_FILE, config_path: Path = CONFIG,
                 watch_files: bool | None = None):
        self.coach, self.ram, self.addrs, self.cfg = coach, ram, addrs, cfg
        self.sim, self.hotkeys = sim, hotkeys
        self.debug, self.simulate = debug, simulate
        self.logs, self.stop_file, self.config_path = logs, stop_file, config_path
        self.watch_files = sim is None if watch_files is None else watch_files   # route and config edits
        self.overlay = None                      # the panel, once main() has made it
        self.stopped = False
        self.actions, self.toggle_key, self.quit_key = key_actions(coach, cfg)

        self.route_name = route_name
        self.route_path = routes.path(route_name)
        self.route_mtime = self.route_path.stat().st_mtime if self.route_path.exists() else 0.0
        self.cfg_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
        self.last_route_check = time.monotonic()

        self.lost_since = 0.0                    # when the game went away (0: it's there)
        self.last_try = 0.0                      # last time it was looked for again
        self.last_view: View | None = None
        self.last_debug = 0.0
        self.last_msg = ""
        self.last_log = 0.0
        self.last_resets = coach.resets
        self.run_started = False
        self.last_bag = None
        self.last_eq = None

    # -- the poll ---------------------------------------------------------------
    def tick(self, render) -> bool:
        """Read the game once and update the panel. False when it's time to quit."""
        coach = self.coach
        if self.stop_file.exists():              # "Stop coach" in the launcher (game there or not)
            try:
                self.stop_file.unlink()
            except OSError:
                pass
            print("Stopped from the launcher.")
            self.stopped = True
            return False
        try:
            if self.sim:
                self.sim.tick()
            state = read_state(self.ram, self.addrs)
            self.lost_since = 0.0
        except MemoryError_ as e:
            if self.sim:
                print(f"Lost the game: {e}")
                return False
            if not self.lost_since:
                self.lost_since = time.monotonic()
                print(f"Lost the game ({e}); waiting for it to come back...")
            if not self.handle_keys():
                return False
            self.reconnect()
            return True                          # keep waiting (F10 quits)
        if not self.handle_keys():
            return False
        if self.watch_files:
            self.check_route_file()
        view = coach.update(state)
        if coach.events:
            self.log_run(coach.events)
            coach.events.clear()
        if coach.resets != self.last_resets:
            self.last_resets = coach.resets
            print("Game reset seen: back to the start of the route, encounters cleared.")
        self.log_bag(state)
        self.log_equips(state)
        if coach.in_battle and time.monotonic() - self.last_log >= 1.0:
            self.last_log = time.monotonic()
            self.log_fight(state)
        if self.debug and coach.message != self.last_msg:
            self.last_msg = coach.message
            if coach.message:
                print("coach: " + coach.message)
        if view != self.last_view:
            render(view)
            self.last_view = view
        if self.debug and time.monotonic() - self.last_debug > 1:
            self.last_debug = time.monotonic()
            fp = coach.learned.battle_fp
            check = ("off" if coach.learned.data.get("fp_unusable") else
                     "learning" if fp is None else
                     "match" if state.battle_sig == fp else "no match")
            print(f"enemyHP={state.enemy_hp} enemyATB={state.enemy_atb} "
                  f"partyATB={state.party_atb} partyHP={state.party_hp_slots} igt={state.igt} "
                  f"screen={state.field_id} encounters={'yes' if state.encounters_possible else 'no'} "
                  f"battle-code={check} in_battle={coach.in_battle}")
            if coach.in_battle:
                print(f"  enemies (slot:first>now=shown [A=in fight, S=hidden 10k]): {coach.enemy_debug(state)}")
        return True

    def handle_keys(self) -> bool:
        """The hotkeys pressed since the last read. False when it's time to quit."""
        if not self.hotkeys:
            return True
        for k in self.hotkeys.poll():
            if k == self.quit_key:
                print(f"{self.quit_key} pressed: quitting.")
                self.stopped = True
                return False
            if k == self.toggle_key and self.overlay is not None:
                self.overlay.toggle()
            elif k in self.actions:
                self.coach.events.append(f"key {k}")
                self.actions[k]()
        return True

    def reconnect(self) -> bool:
        """DuckStation moves the game's memory on a reset or reboot: find it again."""
        if time.monotonic() - self.last_try < 1.0:
            return False
        self.last_try = time.monotonic()
        base = self.cfg.get("ram_base")
        try:
            new = DuckStationRam(self.cfg.get("process_name", "duckstation"),
                                 int(base, 16) if isinstance(base, str) else base)
        except MemoryError_:
            return False
        try:
            read_state(new, self.addrs)
        except MemoryError_:
            new.close()                          # found DuckStation, but not the game yet
            return False
        old, self.ram = self.ram, new
        if hasattr(old, "close"):
            old.close()                          # the old process handle, no longer used
        if self.overlay is not None:
            self.overlay.pid = new.pid
        print(f"Found the game again (pid {new.pid}).")
        return True

    def check_route_file(self) -> None:
        """Pick up edits to the route (from the editor) while running, and a different
        route being picked in the editor or launcher."""
        if time.monotonic() - self.last_route_check < 1.5:
            return
        self.last_route_check = time.monotonic()
        coach = self.coach
        try:
            m = self.config_path.stat().st_mtime
            if m != self.cfg_mtime:              # e.g. "Show the next step" ticked in the launcher
                self.cfg_mtime = m
                cfg = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
                coach.show_next = bool(cfg.get("overlay", {}).get("show_next", False))
                coach.speedrun = bool(cfg.get("speedrun", False))
                self.last_view = None            # draw again
        except (OSError, ValueError):
            pass
        if coach.learned.refresh():
            self.log_run(["learned.json changed (the editor forgot something): reloaded it"])
            coach.recheck_screen()
        name = routes.active()
        if name and name != self.route_name and routes.path(name).exists():
            try:
                new = load_route(routes.path(name))
            except Exception as e:
                print(f"Route {name} has a problem, staying on {self.route_name}: {e}")
                return
            self.route_name, self.route_path = name, routes.path(name)
            self.route_mtime = self.route_path.stat().st_mtime
            if not self.simulate:
                coach.switch_route(new, routes.progress_path(name))
            print(f"Switched to route: {name}")
            self.log_run([f"switched to route {name}"])
            return
        try:
            m = self.route_path.stat().st_mtime
        except OSError:
            return
        if m == self.route_mtime:
            return
        self.route_mtime = m
        try:
            coach.reload_route(load_route(self.route_path))
            print("Route updated")
        except Exception as e:                   # half-saved or broken file: keep the old route
            print(f"The route file has a problem, still using the old one: {e}")

    # -- logs, for looking at a run afterwards ------------------------------------
    def log_fight(self, state) -> None:
        """A line a second during fights, so problems can be looked at afterwards."""
        coach, path = self.coach, self.logs("fight_log.txt")
        try:
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(self.logs("fight_log.old.txt"))
            cur = coach.current()
            name = cur.get("title") or coach.bosses.get(cur.get("boss"), {}).get("name", cur.get("boss"))
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} step {coach.step + 1} {name} | "
                        f"boss={coach.battle_is_boss} random={coach.battle_random_possible} "
                        f"enc={len(coach.encounters)} igt={state.igt} | {coach.enemy_debug(state)}\n")
        except OSError:
            pass

    def log_run(self, lines) -> None:
        """Each step change and why, each fight and what it was taken for (logs/run_log.txt).
        The editor reads this back to show how each step was reached last run."""
        if not lines:
            return
        path = self.logs("run_log.txt")
        try:
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(self.logs("run_log.old.txt"))
            with path.open("a", encoding="utf-8") as f:
                if not self.run_started:
                    self.run_started = True
                    f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} route: {self.route_name} ===\n")
                for line in lines:
                    f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass

    def log_bag(self, state) -> None:
        """What's in the bag each time it changes, so shop checks can be looked at afterwards."""
        if state.items is None or state.items == self.last_bag:
            return
        addrs = self.addrs
        try:
            from items import ITEMS
            with self.logs("bag_log.txt").open("a", encoding="utf-8") as f:
                if self.last_bag is None:
                    raw = self.ram.read(max(0, addrs.inventory - 0x20), 0x240)
                    f.write(f"{time.strftime('%H:%M:%S')} bag at 0x{addrs.inventory:06X}, raw from -0x20: {raw.hex()}\n")
                names = ", ".join(f"{ITEMS[i] if i < len(ITEMS) else i} {n}" for i, n in sorted(state.items.items()))
                f.write(f"{time.strftime('%H:%M:%S')} step {self.coach.step + 1}: {names}\n")
            self.last_bag = dict(state.items)
        except OSError:
            pass

    def log_equips(self, state) -> None:
        """Everyone's equipment each time it changes (and, the first time, where each
        character's record is), so equip checks can be looked at afterwards."""
        if not state.equips or state.equips == self.last_eq:
            return
        addrs = self.addrs
        try:
            from items import ITEMS
            def names(e):
                return " / ".join(ITEMS[i] if 0 < i < len(ITEMS) else ("-" if i in (0, 255) else str(i)) for i in e)
            with self.logs("equip_log.txt").open("a", encoding="utf-8") as f:
                t = time.strftime('%H:%M:%S')
                if self.last_eq is None:
                    for n, r in addrs.chars.items():
                        f.write(f"{t} {n} record at 0x{r['exp']:06X}: exp {state.exp.get(n)} lv {state.levels.get(n)}"
                                f" hp {state.char_hp.get(n)}\n")
                    z = addrs.chars.get("zidane", {}).get("exp")
                    v = addrs.chars.get("vivi", {}).get("exp")
                    if z is not None and v is not None:
                        for k in range(12):                   # every record-sized block from Zidane's on
                            raw = self.ram.read(z + k * (v - z) + addrs.equip_off, 5)
                            f.write(f"{t} block {k} at 0x{z + k * (v - z):06X}: {names(raw)}\n")
                for n, e in state.equips.items():
                    if self.last_eq is None or self.last_eq.get(n) != e:
                        f.write(f"{t} step {self.coach.step + 1} {n}: {names(e)}\n")
            self.last_eq = {n: list(e) for n, e in state.equips.items()}
        except (OSError, KeyError, TypeError):
            pass


class CoachError(Exception):
    """The coach can't start (no route, DuckStation not running): the message says why."""


def prepare(simulate: bool = False, debug: bool = False, step: int | None = None):
    """Everything the coach needs, ready to run: (runner, cfg, pid). Raises CoachError."""
    cfg = load_config()
    addrs = Addresses.from_config(cfg)
    route_name = routes.active()
    if not route_name:
        raise CoachError("No route found: open the editor and make one.")
    route = load_route(routes.path(route_name))
    print(f"Route: {route_name}")
    if simulate:
        for st in route["steps"]:                   # the simulator's fights all start from one screen
            if st["type"] == "boss":
                st.setdefault("screen", Simulator.BOSS_SCREEN)
        coach = Coach(route, party_slots=addrs.party_slots)
    else:
        # Each start of the coach starts the route from step 1 with no encounters
        # ("resume_progress": true in config.json carries on where it left off instead).
        coach = Coach(route, routes.progress_path(route_name), LEARNED, addrs.party_slots,
                      resume=bool(cfg.get("resume_progress", False)))

    coach.show_next = bool(cfg.get("overlay", {}).get("show_next", False))
    coach.speedrun = bool(cfg.get("speedrun", False))

    sim = None
    pid = 0
    if simulate:
        ram = FakeRam()
        def next_fight():
            """The typed HP of the next fight step on the route (from where the coach is)."""
            for i in range(coach.step, len(coach.steps)):
                st = coach.steps[i]
                if st.get("type") == "boss" and coach.active(i):
                    hp = coach.bosses.get(st["boss"], {}).get("hp")
                    if hp:
                        return hp
            return [999]
        sim = Simulator(ram, addrs, next_fight)
    else:
        base = cfg.get("ram_base")
        try:
            ram = DuckStationRam(cfg.get("process_name", "duckstation"),
                                 int(base, 16) if isinstance(base, str) else base)
        except MemoryError_ as e:
            raise CoachError(f"Can't read DuckStation: {e} Start the game in DuckStation, then Start coach.")
        pid = ram.pid
        print(f"Attached to {ram.exe} (pid {ram.pid}), PS1 RAM at 0x{ram.ram_base:X}")

    if step:
        coach.goto(step - 1)

    if addrs.inventory is None and not simulate:
        # Steals and pickups are read from the item bag: without it they can't tick.
        coach.steal_off = "Steal check off: find the Item bag in Memory setup"
        print("The item bag isn't found yet: steals and pickups can't tick (Memory setup > Item bag).")

    actions, toggle_key, quit_key = key_actions(coach, cfg)
    hotkeys = None
    if sys.platform == "win32":
        from hotkeys import Hotkeys
        hotkeys = Hotkeys(list(actions) + [toggle_key, quit_key])
        if hotkeys.failed:
            print(f"These hotkeys are taken by another app: {', '.join(hotkeys.failed)}. "
                  "Change them in config.json.")
        k = {fn.__name__: key for key, fn in actions.items() if hasattr(fn, '__name__')}
        print(f"Runs on its own. Keys only to correct it: "
              f"{k['prev_step']} / {k['next_step']} = previous / next step  ·  "
              f"{k['prev_main_step']} / {k['next_main_step']} = previous / next main step (past sub-steps)  ·  "
              f"{k['learn']} = learn (this fight / where this step starts)  ·  "
              f"{k['learn_next']} = learn where the next step starts  ·  "
              f"{toggle_key} = hide/show  ·  {quit_key} = quit")

    runner = Runner(coach, ram, addrs, cfg, route_name, sim=sim, hotkeys=hotkeys,
                    debug=debug, simulate=simulate)
    try:
        STOP_FILE.unlink()                             # a stop asked for before this start
    except OSError:
        pass
    return runner, cfg, pid


class CoachSession:
    """The coach running inside the app: its panel over the game is a window of the app, and
    it's polled from the app's own loop. start() / stop() as often as you like."""

    def __init__(self, master, on_stop=None):
        self.master, self.on_stop = master, on_stop
        self.runner = self.overlay = None
        self._job = None

    @property
    def running(self) -> bool:
        return self.runner is not None

    def start(self, simulate: bool = False) -> None:
        """Start it. Raises CoachError (with what to do) if it can't."""
        if self.running:
            return
        from overlay import Overlay
        runner, cfg, pid = prepare(simulate=simulate)
        self.runner = runner
        self.poll_ms = int(cfg.get("poll_ms", 100))
        self.follow_every = max(1, 1000 // self.poll_ms)
        self.ticks = self.errors = 0
        self.overlay = runner.overlay = Overlay(cfg.get("overlay", {}), pid, master=self.master)
        self._job = self.master.after(0, self._loop)

    def _loop(self) -> None:
        self._job = None
        if self.runner is None:
            return
        try:
            alive = self.runner.tick(self.overlay.render)
        except Exception:                              # one bad update mustn't freeze the panel and its keys
            import traceback
            self.errors += 1
            if self.errors <= 20:
                traceback.print_exc()
            alive = True
        if not alive:                                  # F10, or Stop
            self.stop()
            return
        self.ticks += 1
        if self.ticks % self.follow_every == 0:
            self.overlay.follow()
        self._job = self.master.after(self.poll_ms, self._loop)

    def stop(self) -> None:
        if self._job is not None:
            self.master.after_cancel(self._job)
            self._job = None
        runner, self.runner = self.runner, None
        if runner is not None and runner.hotkeys is not None:
            runner.hotkeys.close()
        if self.overlay is not None:
            try:
                self.overlay.root.destroy()
            except Exception:
                pass
            self.overlay = None
        if runner is not None:
            print("Coach stopped.")
            if self.on_stop:
                self.on_stop()

    @property
    def coach(self):
        return self.runner.coach if self.runner else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--step", type=int, help="start at this step number (1-based)")
    args = ap.parse_args()
    try:
        runner, cfg, pid = prepare(args.simulate, args.debug, args.step)
    except CoachError as e:
        print(e)
        return 1
    poll_ms = int(cfg.get("poll_ms", 100))
    sim = runner.sim

    if args.console:
        try:
            while runner.tick(console_print):
                time.sleep(poll_ms / 1000)
                if sim and sim.t > 10 * Simulator.CYCLE * 2:
                    break
        except KeyboardInterrupt:
            pass
        return 0

    import tkinter as tk
    from overlay import make_dpi_aware
    make_dpi_aware()
    root = tk.Tk()
    root.withdraw()                                    # (on its own: just the panel)
    session = CoachSession(root, on_stop=root.destroy)
    session.runner = None
    try:
        from overlay import Overlay
        session.runner = runner
        session.poll_ms, session.follow_every = poll_ms, max(1, 1000 // poll_ms)
        session.ticks = session.errors = 0
        session.overlay = runner.overlay = Overlay(cfg.get("overlay", {}), pid, master=root)
        root.after(0, session._loop)
        root.mainloop()
    finally:
        session.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
