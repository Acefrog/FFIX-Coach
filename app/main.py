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

from paths import CONFIG, LEARNED, STOP_FILE, log


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# Simulation: a scripted fake fight so you can see the overlay work
# ----------------------------------------------------------------------
class Simulator:
    """Drives FakeRam round and round: walking -> a random battle -> walking -> the next
    fight on the route (with that fight's typed HP) -> victory."""

    FIELD_CODE = b"\x11" * 64
    BATTLE_CODE = b"\x22" * 64
    CYCLE = 45                               # seconds per round

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
        elif phase < 18:                     # back on the field, next screen
            self._code(False)
            self._enemies([])
            r.write(a.field_id, screen + 2)
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
    if view.upcoming:
        print("  Next: " + view.upcoming[0])
    if view.next_title:
        print("  --- Next: " + view.next_title)
        for line in view.next_lines:
            print("     " + line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--step", type=int, help="start at this step number (1-based)")
    ap.add_argument("--no-ocr", action="store_true", help="don't read steal messages off the screen")
    ap.add_argument("--ocr-test", action="store_true",
                    help="print what the screen reader sees at the top of the game picture, then quit")
    args = ap.parse_args()

    cfg = load_config()
    addrs = Addresses.from_config(cfg)
    import routes
    route_name = [routes.active()]
    if not route_name[0]:
        print("No route found: open the editor and make one.")
        return 1
    route = load_route(routes.path(route_name[0]))
    print(f"Route: {route_name[0]}")
    if args.simulate:
        coach = Coach(route, party_slots=addrs.party_slots)
    else:
        # Each start of the coach starts the route from step 1 with no encounters
        # ("resume_progress": true in config.json carries on where it left off instead).
        coach = Coach(route, routes.progress_path(route_name[0]), LEARNED, addrs.party_slots,
                      resume=bool(cfg.get("resume_progress", False)))

    coach.show_next = bool(cfg.get("overlay", {}).get("show_next", False))
    cfg_mtime = [CONFIG.stat().st_mtime if CONFIG.exists() else 0.0]

    sim = None
    pid = 0
    if args.simulate:
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
            print(f"Can't read DuckStation: {e}")
            print("Try: python app\\main.py --simulate   to see the overlay without the game.")
            return 1
        pid = ram.pid
        print(f"Attached to {ram.exe} (pid {ram.pid}), PS1 RAM at 0x{ram.ram_base:X}")

    if args.step:
        coach.goto(args.step - 1)

    watcher = None
    if not args.simulate and not args.no_ocr:
        from screen import StealWatcher, parse_message
        watcher = StealWatcher(pid, cfg.get("overlay", {}))
        watcher.log_path = log("screen_log.txt")
        watcher._log("", "")
        try:
            with watcher.log_path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} coach started (reads DuckStation's own window)\n")
        except OSError:
            pass
        if args.ocr_test:
            if not watcher.ocr.available:
                print(f"Screen reading is off: {watcher.ocr.problem}")
                return 1
            from screen import save_bmp
            watcher.full = True       # the whole picture: battle messages and "Received ..." alike
            print("Reading the game picture every second. Steal in a fight, or open a chest, to test. "
                  "Ctrl+C to stop.")
            saved = False
            try:
                while True:
                    r, pixels = watcher.grab()
                    if r is None:
                        print("Can't find DuckStation's window.")
                    elif not pixels:
                        print(f"Screen capture failed at {r}.")
                    else:
                        if not saved:
                            save_bmp(log("ocr_capture.bmp"), r[2], r[3], pixels)
                            print(f"Capturing {r[2]}x{r[3]} at ({r[0]}, {r[1]}). "
                                  f"Saved what it sees to ocr_capture.bmp: open it to check.")
                            saved = True
                        try:
                            text = watcher.ocr.read(r[2], r[3], pixels)
                            print(repr(text), "->", parse_message(text))
                        except Exception as e:
                            print(f"Text reading failed: {type(e).__name__}: {e}")
                    time.sleep(1)
            except KeyboardInterrupt:
                return 0
        if watcher.ocr.available:
            print("Steal check: reading battle messages off the screen.")
        else:
            print(f"Steal check is off ({watcher.ocr.problem}).")
            coach.steal_off = f"Steal check off: {watcher.ocr.problem}"

    keys = cfg.get("keys", {})
    actions = {
        keys.get("prev_turn", "F5"): coach.prev_turn,
        keys.get("next_turn", "F6"): coach.next_turn,
        keys.get("prev_step", "F7"): coach.prev_step,
        keys.get("next_step", "F8"): coach.next_step,
        keys.get("skip_back", "Shift+F7"): lambda: coach.skip(-1),
        keys.get("skip_forward", "Shift+F8"): lambda: coach.skip(1),
        keys.get("switch_branch", "Shift+F5"): coach.switch_branch,
    }
    toggle_key = keys.get("toggle_overlay", "F9")
    quit_key = keys.get("quit", "F10")
    stop = [False]

    hotkeys = None
    if sys.platform == "win32":
        from hotkeys import Hotkeys
        hotkeys = Hotkeys(list(actions) + [toggle_key, quit_key])
        if hotkeys.failed:
            print(f"These hotkeys are taken by another app: {', '.join(hotkeys.failed)}. "
                  "Change them in config.json.")
        print(f"Runs on its own. Keys only to correct it (it learns from each correction): "
              f"{' / '.join(list(actions)[:4])} = turn-, turn+, step-, step+  ·  "
              f"{' / '.join(list(actions)[4:])} = skip back / forward (learns nothing)  ·  "
              f"{toggle_key} = hide/show  ·  {quit_key} = quit")

    route_path = [routes.path(route_name[0])]
    route_mtime = [route_path[0].stat().st_mtime if route_path[0].exists() else 0.0]
    last_route_check = [time.monotonic()]

    def check_route_file():
        """Pick up edits to the route (from the editor) while running, and a different
        route being picked in the editor or launcher."""
        if time.monotonic() - last_route_check[0] < 1.5:
            return
        last_route_check[0] = time.monotonic()
        try:
            m = CONFIG.stat().st_mtime
            if m != cfg_mtime[0]:                      # e.g. "Show the next step" ticked in the launcher
                cfg_mtime[0] = m
                coach.show_next = bool(load_config().get("overlay", {}).get("show_next", False))
                last_view[0] = None                    # draw again
        except (OSError, ValueError):
            pass
        if coach.learned.refresh():
            log_run(["learned.json changed (the editor forgot something): reloaded it"])
        name = routes.active()
        if name and name != route_name[0] and routes.path(name).exists():
            try:
                new = load_route(routes.path(name))
            except Exception as e:
                print(f"Route {name} has a problem, staying on {route_name[0]}: {e}")
                return
            route_name[0], route_path[0] = name, routes.path(name)
            route_mtime[0] = route_path[0].stat().st_mtime
            if not args.simulate:
                coach.switch_route(new, routes.progress_path(name))
            print(f"Switched to route: {name}")
            log_run([f"switched to route {name}"])
            return
        try:
            m = route_path[0].stat().st_mtime
        except OSError:
            return
        if m == route_mtime[0]:
            return
        route_mtime[0] = m
        try:
            coach.reload_route(load_route(route_path[0]))
            print("Route updated")
        except Exception as e:      # half-saved or broken file: keep the old route
            print(f"The route file has a problem, still using the old one: {e}")

    poll_ms = int(cfg.get("poll_ms", 100))
    last_view: list[View | None] = [None]
    last_debug = [0.0]
    last_msg = [""]
    last_log = [0.0]
    last_resets = [coach.resets]
    log_path = log("fight_log.txt")

    def log_fight(coach, state):
        """A line a second during fights, so problems can be looked at afterwards."""
        try:
            if log_path.exists() and log_path.stat().st_size > 1_000_000:
                log_path.replace(log("fight_log.old.txt"))
            cur = coach.current()
            name = cur.get("title") or coach.bosses.get(cur.get("boss"), {}).get("name", cur.get("boss"))
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} step {coach.step + 1} {name} | "
                        f"boss={coach.battle_is_boss} random={coach.battle_random_possible} "
                        f"enc={len(coach.encounters)} igt={state.igt} | {coach.enemy_debug(state)}\n")
        except OSError:
            pass

    run_path = log("run_log.txt")
    run_started = [False]

    def log_run(lines):
        """Each step change and why, each fight and what it was taken for (logs/run_log.txt).
        The editor reads this back to show how each step was reached last run."""
        if not lines:
            return
        try:
            if run_path.exists() and run_path.stat().st_size > 2_000_000:
                run_path.replace(log("run_log.old.txt"))
            with run_path.open("a", encoding="utf-8") as f:
                if not run_started[0]:
                    run_started[0] = True
                    f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} route: {route_name[0]} ===\n")
                for line in lines:
                    f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass

    last_bag = [None]
    bag_path = log("bag_log.txt")

    def log_bag(state):
        """What's in the bag each time it changes, so shop checks can be looked at afterwards."""
        if state.items is None or state.items == last_bag[0]:
            return
        try:
            from screen import ITEMS
            with bag_path.open("a", encoding="utf-8") as f:
                if last_bag[0] is None:
                    raw = rh[0].read(max(0, addrs.inventory - 0x20), 0x240)
                    f.write(f"{time.strftime('%H:%M:%S')} bag at 0x{addrs.inventory:06X}, raw from -0x20: {raw.hex()}\n")
                names = ", ".join(f"{ITEMS[i] if i < len(ITEMS) else i} {n}" for i, n in sorted(state.items.items()))
                f.write(f"{time.strftime('%H:%M:%S')} step {coach.step + 1}: {names}\n")
            last_bag[0] = dict(state.items)
        except OSError:
            pass

    last_eq = [None]
    eq_path = log("equip_log.txt")

    def log_equips(state):
        """Everyone's equipment each time it changes (and, the first time, where each
        character's record is), so equip checks can be looked at afterwards."""
        if not state.equips or state.equips == last_eq[0]:
            return
        try:
            from screen import ITEMS
            def names(e):
                return " / ".join(ITEMS[i] if 0 < i < len(ITEMS) else ("-" if i in (0, 255) else str(i)) for i in e)
            with eq_path.open("a", encoding="utf-8") as f:
                t = time.strftime('%H:%M:%S')
                if last_eq[0] is None:
                    for n, r in addrs.chars.items():
                        f.write(f"{t} {n} record at 0x{r['exp']:06X}: exp {state.exp.get(n)} lv {state.levels.get(n)}"
                                f" hp {state.char_hp.get(n)}\n")
                    z = addrs.chars.get("zidane", {}).get("exp")
                    v = addrs.chars.get("vivi", {}).get("exp")
                    if z is not None and v is not None:
                        for k in range(12):                   # every record-sized block from Zidane's on
                            raw = rh[0].read(z + k * (v - z) + addrs.equip_off, 5)
                            f.write(f"{t} block {k} at 0x{z + k * (v - z):06X}: {names(raw)}\n")
                for n, e in state.equips.items():
                    if last_eq[0] is None or last_eq[0].get(n) != e:
                        f.write(f"{t} step {coach.step + 1} {n}: {names(e)}\n")
            last_eq[0] = {n: list(e) for n, e in state.equips.items()}
        except (OSError, KeyError, TypeError):
            pass

    rh = [ram]                       # the game's memory; reopened after a reset
    lost = [0.0, 0.0]                # when the game went away, last reconnect try

    def reconnect() -> bool:
        """DuckStation moves the game's memory on a reset or reboot: find it again."""
        if time.monotonic() - lost[1] < 1.0:
            return False
        lost[1] = time.monotonic()
        try:
            new = DuckStationRam(cfg.get("process_name", "duckstation"),
                                 int(cfg["ram_base"], 16) if isinstance(cfg.get("ram_base"), str) else cfg.get("ram_base"))
        except MemoryError_:
            return False
        try:
            read_state(new, addrs)
        except MemoryError_:
            new.close()                                # found DuckStation, but not the game yet
            return False
        old = rh[0]
        rh[0] = new
        if hasattr(old, "close"):
            old.close()                                # the old process handle, no longer used
        if watcher is not None:
            watcher.pid = new.pid
        if overlay is not None:
            overlay.pid = new.pid
        print(f"Found the game again (pid {new.pid}).")
        return True

    def handle_keys() -> bool:
        """The hotkeys pressed since the last read. False when it's time to quit."""
        if not hotkeys:
            return True
        for k in hotkeys.poll():
            if k == quit_key:
                print(f"{quit_key} pressed: quitting.")
                stop[0] = True
                return False
            if k == toggle_key and overlay is not None:
                overlay.toggle()
            elif k in actions:
                coach.events.append(f"key {k}")
                actions[k]()
        return True

    def tick(render):
        if STOP_FILE.exists():                         # "Stop coach" in the launcher (game there or not)
            try:
                STOP_FILE.unlink()
            except OSError:
                pass
            print("Stopped from the launcher.")
            stop[0] = True
            return False
        try:
            if sim:
                sim.tick()
            state = read_state(rh[0], addrs)
            lost[0] = 0.0
        except MemoryError_ as e:
            if sim:
                print(f"Lost the game: {e}")
                return False
            if not lost[0]:
                lost[0] = time.monotonic()
                print(f"Lost the game ({e}); waiting for it to come back...")
            if not handle_keys():
                return False
            reconnect()
            return True                       # keep waiting (F10 quits)
        if not handle_keys():
            return False
        if watcher and args.debug and time.monotonic() - last_debug[0] > 1 and watcher.active.is_set():
            print(f"screen text: {watcher.last_text!r}" + (f"  error: {watcher.last_error}" if watcher.last_error else ""))
        if watcher:
            for kind, item in watcher.poll():
                if kind == "got":
                    coach.pickup_event(item)
                    print(f"Pickup seen on screen: {item}")
                    continue
                coach.steal_event(kind, item)
                if kind == "stole":
                    print(f"Steal seen on screen: {item}")
        if not sim:
            check_route_file()
        view = coach.update(state)
        if coach.events:
            log_run(coach.events)
            coach.events.clear()
        if coach.resets != last_resets[0]:
            last_resets[0] = coach.resets
            print("Game reset seen: back to the start of the route, encounters cleared.")
        log_bag(state)
        log_equips(state)
        if coach.in_battle and time.monotonic() - last_log[0] >= 1.0:
            last_log[0] = time.monotonic()
            log_fight(coach, state)
        if args.debug and coach.message != last_msg[0]:
            last_msg[0] = coach.message
            if coach.message:
                print("coach: " + coach.message)
        if watcher:
            if coach.wants_steal_check():
                watcher.set_active(True, full=False)       # battle message at the top
            elif coach.wants_pickup_check():
                watcher.set_active(True, full=True)        # "Received ..." can show anywhere
            else:
                watcher.set_active(False)
        if view != last_view[0]:
            render(view)
            last_view[0] = view
        if args.debug and time.monotonic() - last_debug[0] > 1:
            last_debug[0] = time.monotonic()
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

    try:
        STOP_FILE.unlink()                             # a stop asked for before this start
    except OSError:
        pass

    overlay = None
    if args.console:
        try:
            while tick(console_print):
                time.sleep(poll_ms / 1000)
                if sim and sim.t > 10 * Simulator.CYCLE * 2:
                    break
        except KeyboardInterrupt:
            pass
        return 0

    from overlay import Overlay, make_dpi_aware
    make_dpi_aware()
    overlay = Overlay(cfg.get("overlay", {}), pid)
    follow_every = max(1, 1000 // poll_ms)   # re-check DuckStation's window about once a second
    ticks = [0]

    errors = [0]

    def loop():
        try:
            alive = tick(overlay.render)
        except Exception:                              # one bad update mustn't freeze the panel and its keys
            import traceback
            errors[0] += 1
            if errors[0] <= 20:
                traceback.print_exc()
            alive = True
        if alive:
            ticks[0] += 1
            if ticks[0] % follow_every == 0:
                overlay.follow()
            overlay.after(poll_ms, loop)
        else:
            overlay.root.destroy()

    overlay.after(0, loop)
    overlay.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
