"""Logic tests with fake RAM. Run: python test_coach.py  (or python -m pytest test_coach.py)"""

import json
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # the app folder
from coach import Coach, load_route
from memory import Addresses, FakeRam, read_state
from main import Simulator

HERE = Path(__file__).resolve().parent
ROUTE = load_route(HERE / "test_route.json")   # a fixed copy, so editing your routes never breaks the tests
FIELD_CODE, BATTLE_CODE = b"\x11" * 64, b"\x22" * 64

import copy as _copy
ROUTE_CHECKS = _copy.deepcopy(ROUTE)
ROUTE_CHECKS["bosses"]["gizamaluke"]["needs"] = ["quina_1hp"]
ROUTE_CHECKS["bosses"]["lani"]["needs"] = ["zidane_hp7"]


def step_of(boss):
    return next(i for i, s in enumerate(ROUTE["steps"]) if s.get("boss") == boss)


class Game:
    """A fake FFIX: walk between screens and fight battles, 10 reads a second."""

    FLAG = 0x1F0100

    def __init__(self, learned=None, field_id=True, code_check=True, flags=True):
        self.ram, self.a = FakeRam(), Addresses()
        if flags and code_check:
            self.a.battle_flags = [(self.FLAG + i, 0x10, 0x20) for i in range(3)]
        if field_id:
            self.a.field_id = 0x1F0010
        if not code_check:
            self.a.battle_sig_len = 0
        self.coach = Coach(ROUTE, learned_path=learned)
        self.coach._enc_flag_seen.add(True)               # the random-battle flag works here
        self.now = 0.0
        self.tick_n = 0
        self.code(False)
        self.ram.write(self.a.encounter_field, 1, 1)
        self.ram.write(self.a.party_hp_addrs[0], 500)    # Zidane is alive

    def code(self, battle):
        b = self.a.battle_module
        self.ram.mem[b:b + 64] = BATTLE_CODE if battle else FIELD_CODE
        for i in range(3):
            self.ram.mem[self.FLAG + i] = 0x20 if battle else 0x10

    def read(self, n=1):
        v = None
        for _ in range(n):
            v = self.coach.update(read_state(self.ram, self.a), self.now)
            self.now += 0.1
        return v

    def screen(self, field_id, seconds=1.0, encounters=True):
        self.ram.write(self.a.encounter_field, 1 if encounters else 0, 1)
        if self.a.field_id is not None:
            self.ram.write(self.a.field_id, field_id)
        return self.read(int(seconds * 10))

    def fight(self, hps, seconds, end_hp=None, code_flip=True, act_every=10):
        """Bars fill for `seconds`; enemies end at end_hp (default all 0)."""
        if code_flip:
            self.code(True)
        for i, h in enumerate(hps):
            self.ram.write(self.a.enemy_hp[i], h)
        v = None
        for k in range(int(seconds * 10)):
            self.tick_n += 1
            self.ram.write(self.a.enemy_atb[0], 100 + (self.tick_n * 41) % 4000)
            self.ram.write(self.a.party_atb[0], 0 if k % act_every == act_every - 1 else 200 + (k % act_every) * 300)
            v = self.read()
        ends = end_hp if end_hp is not None else [0] * len(hps)
        for i, h in enumerate(ends):
            self.ram.write(self.a.enemy_hp[i], h)
        self.read(3)
        if code_flip:
            self.code(False)
        self.read(int((6.5 if not code_flip else 1) * 10))
        return v


def tmp_learned():
    return Path(tempfile.mkdtemp()) / "learned.json"


# ----------------------------------------------------------------------
def test_route_loads_and_every_boss_has_turns():
    for bid, b in ROUTE["bosses"].items():
        assert b["turns"], bid
    assert len(ROUTE["steps"]) > 80


def test_battle_code_check_learns_and_survives_menu_pauses():
    g = Game(flags=False)
    g.coach.goto(step_of("plant_brain") - 1)
    g.screen(10)
    g.fight([150], 2)  # random fight teaches the battle bytes
    assert g.coach.learned.battle_fp == BATTLE_CODE
    # Boss fight, then sit in a menu for 20 s (ATB Wait freezes bars): still in battle
    g.coach.goto(step_of("ark"))
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 20002)
    g.ram.write(g.a.enemy_atb[0], 10)
    g.read()
    g.ram.write(g.a.enemy_atb[0], 60)
    g.read()
    assert g.coach.in_battle
    g.read(200)
    assert g.coach.in_battle
    g.code(False)
    g.read(2)
    assert not g.coach.in_battle
    assert g.coach.step == step_of("ark") + 1


def test_boss_hp_recognised_from_earlier_step_with_turn_tracking():
    g = Game()
    g.coach.goto(step_of("black_waltz_1") - 3)
    g.screen(20)
    v = g.fight([472, 300], 3.5)
    assert g.coach.step == step_of("black_waltz_1") + 1
    assert g.coach.last_end[1] == step_of("black_waltz_1")


def test_fight_steps_have_no_highlight():
    g = Game()
    g.coach.goto(step_of("black_waltz_1"))
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 472)
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.ram.write(g.a.party_atb[0], 0 if k in (10, 20) else 200 + (k % 10) * 300)
        v = g.read()
    assert v.highlight is None and g.coach.turn == 2       # turns are still counted


def test_scripted_boss_ending_moves_on_by_itself():
    g = Game()
    g.coach.goto(step_of("beatrix1"))
    g.screen(30)
    g.fight([3630], 15, end_hp=[2500])  # Beatrix never hits 0 HP
    assert g.coach.step == step_of("beatrix1") + 1


def test_random_encounter_does_not_move_the_route():
    g = Game()
    start = next(k for k, st in enumerate(ROUTE["steps"]) if st["type"] == "move")
    g.coach.goto(start)
    g.screen(40)
    v = None
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 150)
    for k in range(10):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
        v = g.read()
    assert v.kind == "move" and v.lines == ROUTE["steps"][start]["lines"]   # step stays up, no prompt
    assert not any("Flee" in l for l in v.lines)
    g.code(False)
    g.read(5)
    assert g.coach.step == start


def test_random_fight_before_unknown_boss_is_ignored_then_boss_learned_for_next_run():
    learned = tmp_learned()
    g = Game(learned)
    target = step_of("dracozombie")
    g.coach.goto(target)
    g.screen(50)
    g.fight([600, 600], 20)              # a random fight, even a long one won: not the boss
    assert g.coach.step == target
    assert len(g.coach.encounters) == 1
    g.fight([13500], 20)                 # the real one carries the hidden 10,000
    assert g.coach.step == target + 1
    assert g.coach.learned.formation("dracozombie") == [13500]

    # Next run: recognised straight away, even from a few steps earlier
    g2 = Game(learned)
    g2.coach.goto(target - 2)
    g2.screen(50)
    g2.fight([13500], 20)
    assert g2.coach.step == target + 1


def test_scripted_fight_on_no_encounter_screen_jumps_to_next_unknown_boss():
    g = Game()
    target = step_of("zombies")
    g.coach.goto(target - 1)  # 'Iifa: Ask Eiko' move step
    g.screen(60, encounters=False)
    g.fight([900], 15)
    assert g.coach.step == target + 1


def test_back_key_after_wrong_advance_forgets_what_it_learned():
    learned = tmp_learned()
    g = Game(learned)
    target = step_of("dracozombie")
    g.coach.goto(target)
    g.screen(70, encounters=False)
    g.fight([700], 14)                    # a long fight mistaken for the boss
    assert g.coach.step == target + 1
    g.coach.prev_step()
    assert g.coach.step == target
    assert g.coach.learned.formation("dracozombie") is None


def test_manual_correction_in_battle_teaches_formation():
    learned = tmp_learned()
    g = Game(learned)
    target = step_of("dracozombie")       # no HP typed for it
    g.coach.goto(target - 2)
    g.screen(80)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 2179)
    for k in range(5):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
        g.read()
    g.coach.goto(target)                  # F8 to the right fight
    assert g.coach.battle_is_boss
    assert g.coach.learned.formation("dracozombie") == [2179]


def test_stray_f8_in_a_fight_that_isnt_the_typed_hp_teaches_nothing():
    learned = tmp_learned()
    g = Game(learned)
    gar = step_of("garland")              # typed HP 40728
    g.coach.goto(gar - 2)
    g.screen(80)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 9999)
    for k in range(5):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
        g.read()
    g.coach.goto(gar)                     # F8 in the wrong fight
    assert g.coach.battle_is_boss         # goes along with it this time
    assert g.coach.learned.formation("garland") is None
    assert any("not learned" in e for e in g.coach.events)


def test_typed_hp_wins_over_wrong_learning():
    learned = tmp_learned()
    g = Game(learned)
    b = step_of("black_waltz_2")          # typed HP 1030
    g.coach.learned.set_formation("black_waltz_2", [523, 523])   # taught by a stray F8 once
    g.coach.goto(b - 1)
    g.screen(30)
    g.fight([523, 523], 20)               # those soldiers again: a random battle now
    assert g.coach.step == b - 1
    g.fight([1030], 20)                   # the real one
    assert g.coach.step == b + 1


def test_screens_learned_on_first_run_drive_the_second():
    learned = tmp_learned()
    g = Game(learned)
    first = step_of("black_waltz_1") + 1   # 'Dali: dodge the kids'
    g.coach.goto(first)
    g.screen(300)
    g.screen(301)
    g.coach.next_step()                    # MENU 1 Dali, pressed on screen 301
    g.screen(301, seconds=4)
    assert g.coach.learned.field(Coach.step_key(ROUTE["steps"][first + 1]), g.coach.rname) == 301

    g2 = Game(learned)
    g2.coach.goto(first)
    g2.screen(300)
    assert g2.coach.step == first
    g2.screen(301)
    assert g2.coach.step == first + 1


def test_several_f8s_in_a_row_teach_no_screens_and_loads_teach_none():
    learned = tmp_learned()
    g = Game(learned)
    first = step_of("black_waltz_1") + 1
    g.coach.goto(first)
    g.screen(300, seconds=4)
    for _ in range(4):                     # F8 F8 F8 F8 while standing on one screen
        g.coach.next_step()
        g.screen(300, seconds=0.5)
    key = lambda i: Coach.step_key(ROUTE["steps"][i])
    for i in range(first + 1, first + 4):
        assert g.coach.learned.field(key(i), g.coach.rname) is None, i
    g.screen(300, seconds=4)
    assert g.coach.learned.field(key(first + 4), g.coach.rname) == 300   # the one you stopped on
    g.coach.goto(first + 6)
    g.coach.loaded_at = g.now              # a save loaded: you're somewhere else entirely
    g.screen(999, seconds=4)
    assert g.coach.learned.field(key(first + 6), g.coach.rname) is None


def test_fallback_without_code_check_and_resumed_fight():
    g = Game(code_check=False)
    target = step_of("beatrix3")
    g.coach.goto(target)
    g.screen(90)
    g.fight([5709], 10, end_hp=[3000], code_flip=False)   # 6.5 s idle in a menu ends it early
    assert g.coach.step == target + 1
    v = g.fight([3000], 5, code_flip=False)                # same fight carries on
    assert v.kind == "boss" and "Beatrix 3" in v.title     # back on its card, not "random"
    assert g.coach.step == target + 1                      # and ended again -> next step
    assert g.coach.last_end[1] == target


def test_unreliable_code_check_falls_back():
    g = Game(flags=False)
    g.coach.learned.set_battle_fp(b"\x99" * 64)  # wrong bytes
    g.coach.goto(step_of("plant_brain") - 1)
    g.screen(95)
    g.fight([200], 6, code_flip=False)
    assert g.coach.learned.data["fp_unusable"]


def start_fight(g, hps):
    g.code(True)
    for i, h in enumerate(hps):
        g.ram.write(g.a.enemy_hp[i], h)
    for k in range(20):                    # long enough to identify the fight
        g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
        g.ram.write(g.a.party_atb[0], 100 + k * 40)
        g.read()
    assert g.coach.in_battle and g.coach.identified


def test_limit_glove_badge_reads_battle_hp_any_slot():
    g = Game()
    g.coach = Coach(ROUTE_CHECKS)
    g.coach.goto(step_of("gizamaluke"))
    hp = g.a.party_hp_addrs
    g.ram.write(hp[0], 400)   # Zidane
    g.ram.write(hp[2], 0)     # Quina KO'd, slot 3
    v = g.read()
    assert any(t == "Quina needs exactly 1 HP" for t, _ in v.badges)  # not in a fight: reminder only
    start_fight(g, [3175])
    v = g.read()
    assert any(t == "Nobody on 1 HP yet" for t, _ in v.badges)
    g.ram.write(hp[2], 1)     # Phoenix Down landed on 1
    v = g.read()
    assert any(t.startswith("LIMIT GLOVE READY") for t, _ in v.badges)
    assert not any(t.startswith("Party HP") for t, _ in v.badges)  # party HP isn't shown


def test_lucky_seven_badge_uses_zidane_slot():
    g = Game()
    g.coach = Coach(ROUTE_CHECKS)
    g.coach.goto(step_of("lani"))
    start_fight(g, [5708])
    g.ram.write(g.a.party_hp_addrs[0], 612)
    v = g.read()
    assert any("needs to end in 7" in t for t, _ in v.badges)
    g.ram.write(g.a.party_hp_addrs[0], 617)
    v = g.read()
    assert any(t == "LUCKY SEVEN READY" for t, _ in v.badges)


def test_quina_slot_from_config():
    g = Game()
    g.coach = Coach(ROUTE_CHECKS)
    g.coach.party_slots["quina"] = 1
    g.coach.goto(step_of("gizamaluke"))
    start_fight(g, [3175])
    g.ram.write(g.a.party_hp_addrs[1], 7)
    g.ram.write(g.a.party_hp_addrs[3], 1)   # someone else on 1 HP doesn't count
    v = g.read()
    assert any(t == "Quina HP 7: need 1" for t, _ in v.badges)


def test_leftover_values_in_empty_slots_are_ignored():
    g = Game(code_check=False)
    e_hp, e_atb = g.a.enemy_hp, g.a.enemy_atb
    p_hp = g.a.party_hp_addrs
    # Leftovers from an earlier fight in enemy slots 3-4 and party slot 4
    g.ram.write(e_hp[2], 999)
    g.ram.write(e_hp[3], 1234)
    g.ram.write(e_atb[2], 777)
    g.ram.write(p_hp[3], 1)            # a stale "1 HP" that must not trigger Limit Glove
    g.coach.goto(step_of("black_waltz_1"))
    g.screen(5)
    g.ram.write(e_hp[0], 472)
    g.ram.write(e_hp[1], 229)
    g.ram.write(p_hp[0], 98)
    v = None
    for k in range(15):
        g.ram.write(e_atb[0], 100 + k * 40)
        g.ram.write(e_atb[1], 200 + k * 30)
        g.ram.write(g.a.party_atb[0], 100 + k * 50)
        v = g.read()
    assert g.coach.active_enemy == {0, 1}
    assert ("Enemy HP 472 / 229", "info") in v.badges
    # Both real enemies down: fight is over even though the leftovers still read 999 / 1234
    g.ram.write(e_hp[0], 0)
    g.ram.write(e_hp[1], 0)
    g.read(3)
    assert g.coach.step == step_of("black_waltz_1") + 1


def test_leftover_one_hp_does_not_fake_limit_glove():
    g = Game()
    g.coach = Coach(ROUTE_CHECKS)
    g.ram.write(g.a.party_hp_addrs[3], 1)   # leftover, never changes this fight
    g.coach.goto(step_of("gizamaluke"))
    start_fight(g, [3175])
    g.ram.write(g.a.party_hp_addrs[0], 300)
    v = g.read()
    assert any(t == "Nobody on 1 HP yet" for t, _ in v.badges)


def test_unknown_boss_learns_only_real_enemies():
    learned = tmp_learned()
    g = Game(learned, code_check=False)
    g.ram.write(g.a.enemy_hp[3], 4321)       # leftover
    g.coach.goto(step_of("dracozombie"))
    g.screen(5, encounters=False)
    g.fight([3500], 20)
    assert g.coach.learned.formation("dracozombie") == [3500]


def test_random_fight_shows_live_hp_and_keeps_the_step():
    g = Game()
    start = next(k for k, st in enumerate(ROUTE["steps"]) if st["type"] == "move")
    g.ram.write(g.a.enemy_hp[2], 999)              # leftover from an earlier fight
    g.coach.goto(start)
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 150)
    for k in range(15):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
        g.ram.write(g.a.party_atb[0], 100 + k * 40)
        g.ram.write(g.a.enemy_hp[0], 150 - k)
        v = g.read()
    assert v.title == ROUTE["steps"][start]["title"] and v.lines == ROUTE["steps"][start]["lines"]
    assert not any(t.startswith("Party HP") for t, _ in v.badges)
    assert ("Enemy HP 136", "info") in v.badges    # the leftover 999 doesn't show
    assert v.upcoming == []
    g.code(False)
    v = g.read(3)
    assert not any("HP" in t for t, _ in v.badges)  # gone once the fight ends


def test_route_can_ask_for_party_hp():
    import copy
    route = copy.deepcopy(ROUTE)
    route["bosses"]["ark"]["show"] = ["party_hp", "enemy_hp"]
    g = Game()
    g.coach = Coach(route)
    g.coach.goto(step_of("ark"))
    g.ram.write(g.a.party_hp_addrs[0], 900)
    start_fight(g, [20002])
    g.ram.write(g.a.party_hp_addrs[0], 850)
    v = g.read()
    assert ("Enemy HP 20,002", "info") in v.badges


def test_formations_from_older_versions_are_dropped():
    import json as _json
    learned = tmp_learned()
    learned.write_text(_json.dumps({"formations": {"garland": [999, 9999]}, "fields": {"x": 3}}))
    g = Game(learned)
    assert g.coach.learned.formation("garland") is None
    assert g.coach.learned.field("x") is None          # screens learned alongside them go too


def test_sealion_hp_check():
    g = Game()
    g.coach.goto(step_of("black_waltz_1"))
    v = g.read()
    assert any(t.startswith("Hit Sealion only with 37+") for t, _ in v.badges)   # reminder outside the fight
    start_fight(g, [229, 472])
    g.ram.write(g.a.party_hp_addrs[0], 30)
    v = g.read()
    assert ("Zidane HP 30: heal before hitting Sealion", "warn") in v.badges
    g.ram.write(g.a.party_hp_addrs[0], 95)
    v = g.read()
    assert ("Zidane HP 95: safe to hit Sealion", "good") in v.badges


def test_earth_guardian_card_has_corrected_eat_number():
    g = Game()
    g.coach.goto(step_of("earth_guardian"))
    start_fight(g, [20756])
    v = g.read()
    assert any("15,567" in line for line in v.lines)
    assert ("Enemy HP 20,756", "info") in v.badges     # live HP to judge the Eat


def test_quick_scripted_fight_on_no_encounter_screen_counts():
    g = Game()
    target = step_of("steiner2")
    g.coach.goto(target)
    g.screen(70, encounters=False)
    g.fight([900], 4)                     # over in 4 seconds
    assert g.coach.step == target + 1


def test_new_route_recognises_bosses_by_hp():
    g = Game()
    g.coach.goto(step_of("gizamaluke") - 4)
    g.screen(80)
    g.fight([3175], 20)
    assert g.coach.step == step_of("gizamaluke") + 1


def test_saved_place_survives_route_edits():
    import json as _json
    prog = tmp_learned().with_name("progress.json")
    c = Coach(ROUTE, progress_path=prog)
    c.goto(step_of("antlion"))
    data = _json.loads(prog.read_text())
    data["step"] = 3                      # as if steps were inserted before it
    prog.write_text(_json.dumps(data))
    assert Coach(ROUTE, progress_path=prog).step == step_of("antlion")
    prog.write_text(_json.dumps({"step": 40}))  # old-style file without a key still loads
    assert Coach(ROUTE, progress_path=prog).step == 40


class RealisticBattleRam:
    """2 MB that looks like a real fight: random data, per-slot frame counters,
    a decoy copy of the party HP (the out-of-battle party record), and the real
    battle table at an address and spacing the coach has never seen."""

    def __init__(self, base, stride, party, enemies, atb_off=4, decoy=0x8B200, decoy_stride=0x90,
                 enemies_in_table=True, seed=7):
        import random
        self.rng = random.Random(seed)
        self.mem = bytearray(self.rng.randbytes(0x200000))
        self.base, self.stride, self.atb_off = base, stride, atb_off
        self.decoy, self.decoy_stride = decoy, decoy_stride
        self.enemies_in_table = enemies_in_table
        self.atb = [100, 900, 0, 0, 300, 0, 0, 0]
        self.speed = [55, 40, 0, 0, 35, 0, 0, 0]
        self.frame = 0
        self.set_party(party, decoy_too=True)
        for k, h in enumerate(enemies):
            self._w(base + (4 + k) * stride, h if enemies_in_table else self.rng.randrange(65536))

    def _w(self, a, v):
        self.mem[a:a + 2] = (v & 0xFFFF).to_bytes(2, "little")

    def set_party(self, party, decoy_too=False):
        for k in range(4):
            hp = party[k] if k < len(party) else 0
            self._w(self.base + k * self.stride, hp)
            if decoy_too:
                self._w(self.decoy + k * self.decoy_stride, hp)

    def tick(self):
        self.frame += 6
        for k in range(8):
            slot = self.base + k * self.stride
            self._w(slot + 0x40, self.frame + k)          # animation timer in EVERY slot
            if self.enemies_in_table or k < 4:
                self.atb[k] = min(4000, self.atb[k] + self.speed[k])
                self._w(slot + self.atb_off, self.atb[k])
        for _ in range(300):                               # unrelated memory churning
            o = self.rng.randrange(0, 0x1FFFFE, 2)
            self._w(o, self.rng.randrange(65536))
        return bytes(self.mem)


def test_hp_finder_ignores_counters_and_decoy_and_any_spacing():
    from finder import find_hp_tables, still_matching, atb_offset
    pal_base, stride = 0x10A7C0, 0x1B4
    party = [617, 1, 352, 0]                               # one KO'd member, one on 1 HP
    ram = RealisticBattleRam(pal_base, stride, party, [3175, 0, 0, 0])
    snap = ram.tick()
    cands = find_hp_tables(snap, [617, 1, 352])
    assert (pal_base, stride) in cands
    assert (0x8B200, 0x90) in cands                        # the decoy matches at first
    ram.set_party([540, 1, 352, 0])                        # take a hit: only the battle copy updates
    cands = still_matching(ram.tick(), cands, [540, 1, 352])
    assert cands == [(pal_base, stride)], cands
    snaps = [ram.tick() for _ in range(30)]
    off, score = atb_offset(snaps, pal_base, stride)
    assert off == 4, (off, score)                          # the ATB bar, not the +0x40 timer


def test_hp_finder_prefers_real_table_when_decoy_also_updates():
    from finder import find_battle
    import builtins, finder
    ram = RealisticBattleRam(0x10A7C0, 0x1B4, [617, 352], [3175])
    answers = iter(["617", "352", ""] + ["540", "352", ""] * 4 + [""])
    class R:
        def snapshot(self_inner):
            return ram.tick()
    real_input, real_sleep = builtins.input, finder.time.sleep
    builtins.input = lambda *_: next(answers)
    finder.time.sleep = lambda *_: None
    orig_set = ram.set_party
    def both(party, decoy_too=False):            # the decoy keeps up with live HP too
        orig_set(party, decoy_too=True)
    ram.set_party = both
    snap_calls = {"n": 0}
    class R2(R):
        def snapshot(self_inner):
            snap_calls["n"] += 1
            if snap_calls["n"] == 2:              # after the "take a hit" prompt
                ram.set_party([540, 352])
            return ram.tick()
    try:
        found = find_battle(R2(), {})
    finally:
        builtins.input, finder.time.sleep = real_input, real_sleep
    assert found["battle_base"] == "0x10A7C0" and found["slot_stride"] == "0x1B4", found


def test_hp_finder_when_enemies_live_elsewhere():
    from finder import find_hp_tables, atb_offset
    ram = RealisticBattleRam(0x10C000, 0xD0, [480, 230], [5000], enemies_in_table=False)
    cands = find_hp_tables(ram.tick(), [480, 230])
    assert (0x10C000, 0xD0) in cands
    snaps = [ram.tick() for _ in range(30)]
    off, _ = atb_offset(snaps, 0x10C000, 0xD0)
    assert off is None                                     # enemies not confirmed here


def test_separate_party_hp_setting_is_used():
    a = Addresses.from_config({"addresses": {"party_hp_base": "0x10A7C0", "party_stride": "0x1B4"}})
    assert a.party_hp_addrs[:2] == [0x10A7C0, 0x10A7C0 + 0x1B4]
    assert a.enemy_hp[0] == 0x109264                       # enemy settings untouched


def test_hp_finder_speed():
    import time as _t
    from finder import find_hp_tables
    ram = RealisticBattleRam(0x10A7C0, 0x1B4, [617, 300, 352], [3175])
    snap = ram.tick()
    t = _t.time()
    find_hp_tables(snap, [617, 300, 352])
    assert _t.time() - t < 20


def test_menu_steps_show_one_instruction_per_line():
    g = Game()
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "After Plant Brain")
    g.coach.goto(i)
    v = g.read()
    assert v.title.replace("\u2691 ", "") == "After Plant Brain"   # may carry the "still to learn" flag
    assert len(v.lines) >= 5 and v.highlight is None
    assert "Steiner: Bird Killer, Bug Killer" in v.lines
    assert any("-Silk Shirt" in l and "-Leather Hat" in l for l in v.lines)   # red in the guide = take off


def test_boss_card_before_fight_has_no_highlight_or_live_hp():
    g = Game()
    g.coach.goto(step_of("ark"))
    v = g.read()
    assert v.title == "Ark" and v.highlight is None
    assert not any(t.startswith("Enemy HP") for t, _ in v.badges)


def test_black_bar_geometry():
    from layout import black_bar
    # 1920x1080 windowed, 60 px of menu/toolbar, 25 px status bar, 4:3 picture
    x, y, w, h, kind = black_bar((0, 0, 1920, 1080), 4 / 3, 60, 25, 1440)
    assert kind == "side" and x == 1920 - w and y == 60 and h == 1080 - 85
    assert w == (1920 - int(995 * 4 / 3)) // 2
    # Fullscreen: no menu or status bar
    x, y, w, h, kind = black_bar((0, 0, 1920, 1080), 4 / 3, 60, 25, 1080)
    assert (y, h, w) == (0, 1080, 240)
    # Left side
    assert black_bar((0, 0, 1920, 1080), 4 / 3, 0, 0, 1080, side="left")[0] == 0
    # Window taller than the picture: bar at the bottom
    x, y, w, h, kind = black_bar((100, 100, 900, 1100), 4 / 3, 0, 0, 2000)
    assert kind == "bottom" and w == 800 and h == (1000 - 600) // 2
    # Window the same shape as the picture: no bar
    assert black_bar((0, 0, 1200, 900), 4 / 3, 0, 0, 2000) is None


def test_fake_fights_while_walking_do_not_advance_or_teach():
    """Your logs: in fallback mode, field memory under the ATB addresses moved,
    so 'fights' started while walking and unknown bosses were 'beaten'."""
    learned = tmp_learned()
    g = Game(learned, code_check=False)
    target = step_of("grotto_mages")               # a fight with no known HP
    g.coach.goto(target)
    g.screen(5)
    # Garbage under the battle addresses while walking: moving 'bars', junk 'HP', for 20 s
    for k in range(200):
        g.ram.write(g.a.enemy_hp[0], 38144)
        g.ram.write(g.a.enemy_hp[1], 3585)
        g.ram.write(g.a.enemy_atb[0], 100 + (k * 37) % 1200)
        g.ram.write(g.a.party_atb[0], 200 + (k * 53) % 1200)
        g.read()
    g.read(80)                                     # it all goes quiet
    assert g.coach.step == target                  # didn't move on
    assert g.coach.learned.formation("grotto_mages") is None
    assert g.coach.learned.battle_fp is None       # didn't learn the field bytes as 'battle'


def test_calibrated_flags_ignore_field_garbage():
    g = Game()
    target = step_of("grotto_mages")
    g.coach.goto(target)
    g.screen(5)
    for k in range(100):                           # same garbage, but the flags say 'field'
        g.ram.write(g.a.enemy_hp[0], 38144)
        g.ram.write(g.a.enemy_atb[0], 100 + (k * 37) % 1200)
        g.ram.write(g.a.party_atb[0], 200 + (k * 53) % 1200)
        v = g.read()
    assert not g.coach.in_battle and g.coach.step == target


def test_unknown_boss_needs_a_clear_or_wipe_to_count():
    g = Game()
    target = step_of("grotto_mages")
    g.coach.goto(target)
    g.screen(5, encounters=False)
    g.fight([600, 600], 20, end_hp=[300, 0])       # long fight, fled with one alive
    assert g.coach.step == target
    g.fight([600, 600], 20)                        # everyone beaten
    assert g.coach.step == target + 1


def test_screen_jumps_limited_and_skip_shared_screens():
    learned = tmp_learned()
    g = Game(learned)
    base = step_of("black_waltz_1") + 1            # Dali
    g.coach.goto(base)
    lr = g.coach.learned
    lr.set_field(Coach.step_key(ROUTE["steps"][base + 1]), 500)
    lr.set_field(Coach.step_key(ROUTE["steps"][base + 2]), 500)   # two nearby steps, same screen
    lr.set_field(Coach.step_key(ROUTE["steps"][base + 6]), 600)   # too far ahead
    g.screen(400)
    g.screen(500)
    assert g.coach.step == base                    # ambiguous: stays
    g.screen(600)
    assert g.coach.step == base                    # beyond the 3-step window


def test_lv5_death_warning():
    g = Game()
    g.a.chars = {n: {"level": 0x1E0000 + i} for i, n in enumerate(["zidane", "steiner", "freya", "amarant"])}
    for i, lv in enumerate([36, 30, 33, 29]):
        g.ram.write(0x1E0000 + i, lv, 1)
    g.coach.goto(step_of("deathguise"))
    v = g.read()
    assert ("LV5 Death danger: Steiner Lv 30", "warn") in v.badges
    g.ram.write(0x1E0001, 31, 1)
    v = g.read()
    assert ("Levels safe from LV5 Death", "good") in v.badges


def test_lv5_reminder_without_levels():
    g = Game()
    g.coach.goto(step_of("soulcage"))
    v = g.read()
    assert ("LV5 Death: no level a multiple of 5", "info") in v.badges


def test_battle_flag_finder():
    from finder import battle_flag_candidates, pick_flags
    import random
    rng = random.Random(3)
    base = bytearray(rng.randbytes(0x20000))
    def snap(mode, noise):
        b = bytearray(base)
        b[0x1234] = 1 if mode == "battle" else 0           # the real mode flag
        b[0x8000] = rng.randrange(256)                       # churn
        b[0x9000] = noise                                    # differs per screen, not per mode
        return bytes(b)
    field = [snap("field", 5), snap("field", 5), snap("field", 9), snap("field", 9)]
    battle = [snap("battle", 5), snap("battle", 5), snap("battle", 7), snap("battle", 7)]
    c = battle_flag_candidates(field, battle)
    assert (0x1234, 0, 1) in c and all(a != 0x9000 for a, _, _ in c)
    assert len(pick_flags(list(range(100)))) == 16


def test_stats_finder_helpers():
    from finder import u32_scan, level_offset
    import random
    mem = bytearray(random.Random(4).randbytes(0x10000))
    mem[0x5000:0x5004] = (12345).to_bytes(4, "little")
    mem[0x5000 - 0x10] = 17
    assert 0x5000 in u32_scan(bytes(mem), 12345)
    assert -0x10 in level_offset(bytes(mem), 0x5000, 17)


def test_old_config_is_migrated_to_battle_base():
    a = Addresses.from_config({"addresses": {"enemy_hp": ["0x109264"]}})
    assert a.battle_base == 0x108F24
    assert a.enemy_atb[0] == 0x109268 and a.party_atb[1] == 0x108FF8  # TAS-listed addresses


def test_simulator_runs_end_to_end():
    ram, a = FakeRam(), Addresses()
    sim = Simulator(ram, a)
    coach = Coach(ROUTE)
    coach.goto(step_of("black_waltz_1") - 1)
    saw_prompt = saw_boss = False
    for i in range(650):
        sim.tick()
        v = coach.update(read_state(ram, a), i * 0.1)
        saw_prompt |= v.kind == "random" or any("Flee" in l for l in v.lines)
        saw_boss |= v.kind == "boss" and coach.in_battle
    assert saw_boss and not saw_prompt
    assert coach.step == step_of("black_waltz_1") + 1


def in_boss_fight(boss, hp):
    g = Game()
    g.coach.goto(step_of(boss))
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], hp)
    for k in range(15):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        v = g.read()
    assert g.coach.in_battle and g.coach.battle_is_boss
    return g, v


def test_steal_messages_are_parsed_from_screen_text():
    from screen import parse_message, match_item
    assert parse_message("Stole Mage Masher!") == ("stole", "Mage Masher")
    assert parse_message("  St0le  Chain Plate !\n") == ("stole", "Chain Plate")   # misread letter
    assert parse_message("Stoie Steepled Hat!") == ("stole", "Steepled Hat")
    assert parse_message("Stole Broadsword!") == ("stole", "Broadsword")
    assert parse_message("Couldn't steal!") == ("failed", None)
    assert parse_message("Doesn\u2019t have anything!") == ("failed", None)
    assert parse_message("Thunder") is None and parse_message("") is None
    assert match_item("Adaman Vest") == "Adaman Vest" and match_item("Adamn Vest") == "Adaman Vest"


def test_each_steal_on_a_card_is_found():
    from coach import steal_targets
    found = {b: steal_targets(c["turns"]) for b, c in ROUTE["bosses"].items()}
    assert [it for _, it in found["masked_man"]] == ["Mage Masher"]
    assert [it for _, it in found["beatrix1"]] == ["Chain Plate"]
    assert [it for _, it in found["cage2"]] == ["Broadsword"]
    assert [it for _, it in found["black_waltz_3a"]] == [None]  # any of three items
    assert [it for _, it in found["taharka"]] == [None]         # optional: any item


def test_steal_seen_on_screen_ticks_the_line():
    hp = ROUTE["bosses"]["beatrix1"]["hp"][0]
    g, v = in_boss_fight("beatrix1", hp[0] if isinstance(hp, list) else hp)
    line = next(i for i, l in enumerate(v.lines) if "Steal" in l)
    assert v.done == [] and g.coach.wants_steal_check()
    g.coach.steal_event("failed", None)
    v = g.read()
    assert not any("missed" in t for t, _ in v.badges) and v.done == []
    g.coach.steal_event("stole", "Potion")             # the wrong item: no tick
    v = g.read()
    assert v.done == []
    g.coach.steal_event("stole", "Chain Plate")
    v = g.read()
    assert v.done == [line] and not g.coach.wants_steal_check()
    assert not any("Stole" in t for t, _ in v.badges)      # just the check mark, no notes


def test_steal_check_only_runs_in_boss_fights_with_steals():
    g = Game()
    g.coach.goto(step_of("beatrix1"))
    assert not g.coach.wants_steal_check()             # not in the fight yet
    g.coach.steal_event("stole", "Chain Plate")        # ignored outside the fight
    assert g.coach.stolen == {}


def test_scripted_boss_hidden_10000_hp_is_taken_off():
    g = Game()
    g.coach.goto(step_of("masked_man"))
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10188)              # FF9 stores Masked Man's 188 HP as 10,188
    g.ram.write(g.a.enemy_hp[1], 37)                 # leftover in an unused slot
    for k in range(20):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.ram.write(g.a.enemy_atb[1], (k * 7919) % 60000)   # rewritten junk, not a filling bar
        v = g.read()
    assert g.coach.battle_is_boss and g.coach.step == step_of("masked_man")
    assert ("Enemy HP 188", "info") in v.badges
    g.ram.write(g.a.enemy_hp[0], 10100)
    v = g.read()
    assert ("Enemy HP 100", "info") in v.badges
    g.ram.write(g.a.enemy_hp[0], 9950)                # below 10,000: the game ends the fight
    g.read()
    assert g.coach.battle_cleared


def test_unknown_scripted_boss_and_big_real_hp():
    g = Game()
    g.coach.goto(step_of("king_leo"))
    g.screen(5)
    v = g.fight([10186, 10117], 2.0)
    # no HP on the card: values over 10,000 on an early boss are hidden HP
    assert ("Enemy HP 186", "info") in v.badges, v.badges
    g2, v2 = in_boss_fight("ark", 20002)              # a real 20,002 HP boss is left alone
    assert ("Enemy HP 20,002", "info") in v2.badges


def test_beaten_boss_moves_on_before_battle_screen_closes():
    g = Game()
    g.coach.goto(step_of("cage2"))                   # no HP on the card; stored as 10,533
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10533)
    for k in range(130):                              # 13 s fight
        g.ram.write(g.a.enemy_atb[0], 100 + (k * 41) % 4000)
        g.read()
    assert g.coach.step == step_of("cage2")
    g.ram.write(g.a.enemy_hp[0], 9990)                # below 10,000: the game ends it
    g.read(15)
    assert g.coach.in_battle                          # still on the victory/battle screen
    assert g.coach.step == step_of("cage2") + 1       # but the route has moved on
    g.code(False)
    g.read(20)
    assert g.coach.step == step_of("cage2") + 1       # and doesn't move twice


def test_random_fight_won_quickly_on_unknown_boss_step_does_not_move_on():
    g = Game()
    g.coach.goto(step_of("cage2"))
    g.screen(5)
    g.fight([90], 3)
    assert g.coach.step == step_of("cage2")


def test_fight_flags_need_a_majority_of_all_flags():
    from memory import _vote
    ram = FakeRam()
    flags = [(0x1F0200 + i, 0, 1) for i in range(6)]
    for i in range(6):
        ram.mem[0x1F0200 + i] = 1
    assert _vote(ram, flags) is True
    for i in range(4):
        ram.mem[0x1F0200 + i] = 7                     # unfamiliar field values
    assert _vote(ram, flags) is False


def test_route_reload_keeps_place():
    g = Game()
    i = step_of("beatrix1")
    g.coach.goto(i)
    new = _copy.deepcopy(ROUTE)
    new["steps"].insert(0, {"disc": 1, "type": "move", "title": "Added", "lines": ["New first step"]})
    g.coach.reload_route(new)
    assert g.coach.step == i + 1 and g.coach.current()["boss"] == "beatrix1"
    v = g.read()
    assert v.title == "Beatrix 1"


def test_editor_checks_catch_broken_routes():
    import editor, routes
    good = routes.inline_cards(json.loads((HERE / "test_route.json").read_text(encoding="utf-8")))
    assert editor.check_route(_copy.deepcopy(good)) == []
    bad = _copy.deepcopy(good)
    bad["steps"].append(dict(next(s for s in good["steps"] if s["type"] == "boss")))   # same fight id twice
    assert any("share the id" in p for p in editor.check_route(bad))
    bad["steps"].append({"disc": 4, "type": "boss", "id": "x", "title": "X", "lines": [], "hp": ["lots"]})
    assert any("whole numbers" in p for p in editor.check_route(bad))
    assert editor.check_route({"steps": "x"})


def test_hidden_hp_removed_for_late_enemies_and_unrecognised_early_fights():
    g = Game()
    mv = next(k for k, st in enumerate(ROUTE["steps"]) if st["type"] == "move" and st["disc"] == 1)
    g.coach.goto(mv)                                   # not on a boss step: the coach can't tell which fight
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10150)
    for k in range(15):                                # slot 0 moves from the start
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        v = g.read()
    assert ("Enemy HP 150", "info") in v.badges, v.badges
    g.ram.write(g.a.enemy_hp[1], 10117)                # a second enemy appears / starts moving later
    for k in range(10):
        g.ram.write(g.a.enemy_atb[0], 800 + k * 40)
        g.ram.write(g.a.enemy_atb[1], 100 + k * 40)
        v = g.read()
    assert ("Enemy HP 150 / 117", "info") in v.badges, v.badges
    assert "1:10117>10117=117[AS]" in g.coach.enemy_debug(read_state(g.ram, g.a))


def test_boss_shows_only_enemies_matching_its_card():
    g = Game()
    g.coach.goto(step_of("masked_man"))
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10188)
    g.ram.write(g.a.enemy_hp[1], 35)                # invisible helper slots that tick along
    g.ram.write(g.a.enemy_hp[2], 37)
    for k in range(15):
        for i in range(3):
            g.ram.write(g.a.enemy_atb[i], 100 + k * 40 + i)
        v = g.read()
    assert ("Enemy HP 188", "info") in v.badges, v.badges
    g.ram.write(g.a.enemy_hp[0], 9990)              # Masked Man down: fight counts as won
    g.read(15)
    assert g.coach.step == step_of("masked_man") + 1


def test_back_to_back_steals_both_count():
    import screen
    w = screen.StealWatcher(0, {})                   # no Windows here: nothing runs in the background
    readings = [(0.0, "Stole Potion!"), (2.0, "Stole Mage Masher!"), (2.3, "Stole Mage Masher!"),
                (2.6, ""), (2.9, "Stole Mage Masher!"),          # flicker: still the same message
                (4.0, "Couldn't steal anything."), (8.0, "Stole Mage Masher!")]
    for t, text in readings:
        w.handle(text, now=t)
    assert w.poll() == [("stole", "Potion"), ("stole", "Mage Masher"), ("failed", None),
                        ("stole", "Mage Masher")]


def test_going_back_after_a_won_fight_stays_back():
    g = Game()
    i = step_of("cage2")
    g.coach.goto(i)
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10533)
    for k in range(130):
        g.ram.write(g.a.enemy_atb[0], 100 + (k * 41) % 4000)
        g.read()
    g.ram.write(g.a.enemy_hp[0], 9990)                 # beaten: route moves on mid-fight
    g.read(15)
    assert g.coach.step == i + 1
    g.coach.prev_step()                                # F7 on the victory screen
    g.read(20)
    assert g.coach.step == i                           # stays back while the fight is still up
    g.code(False)
    g.read(20)
    assert g.coach.step == i                           # and when it closes
    g.coach.prev_step()                                # F7 out of a fight works as before
    assert g.coach.step == i - 1


def test_last_fights_hp_left_in_memory_is_not_the_new_fights():
    g = Game()
    g.coach.goto(step_of("plant_spiders"))
    g.ram.write(g.a.enemy_hp[0], 10916)               # Plant Brain's number still in memory
    g.screen(5)
    g.code(True)
    for k in range(4):                                 # the fight starts before the new HP is loaded
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.read()
    g.ram.write(g.a.enemy_hp[0], 33)                   # now the spider's HP arrives
    for k in range(4, 25):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        v = g.read()
    assert g.coach.battle_first_hp[0] == 33
    assert ("Enemy HP 33", "info") in v.badges, v.badges
    assert g.coach.current()["boss"] == "plant_spiders"   # not mistaken for Plant Brain


def igt(g, frames):
    g.ram.write(g.a.igt, frames, 4)


def test_random_battles_are_counted_and_bosses_are_not():
    g = Game()
    start = next(k for k, st in enumerate(ROUTE["steps"]) if st["type"] == "move")
    g.coach.goto(start)
    igt(g, 6000)
    g.screen(5)
    igt(g, 6100)
    g.screen(1)
    g.fight([150], 2)                                  # random battle
    igt(g, 6400)
    g.fight([90, 90], 3)                               # another, two enemies
    assert len(g.coach.encounters) == 2
    v = g.read()
    assert v.header.endswith("2 encounters")
    g.screen(70, encounters=False)
    g.fight([900], 2)                                  # a scripted fight on a no-encounter screen
    assert len(g.coach.encounters) == 2
    b = step_of("black_waltz_1")
    g.coach.goto(b)
    g.screen(9)
    g.fight([229, 472], 4)                             # a boss
    assert len(g.coach.encounters) == 2 and g.coach.step == b + 1


def test_reset_goes_back_to_the_start_and_loads_rewind_the_count():
    g = Game()
    g.coach.goto(step_of("plant_brain"))
    t = 60 * 60 * 30
    for k in range(40):                                # timer counting up normally
        t += 6
        igt(g, t)
        g.read()
    g.coach.encounters = [t - 3000, t - 100]
    igt(g, t - 1000)                                   # savestate from before the 2nd battle
    g.read()
    assert g.coach.encounters == [t - 3000]
    assert g.coach.step == step_of("plant_brain")      # loading keeps your place
    for k in range(40):
        t += 6
        igt(g, t)
        g.read()
    igt(g, 0)                                          # reset: the timer starts again
    v = g.read(3)
    assert g.coach.step == 0                           # straight back to step 1
    for f in range(6, 120, 6):                         # new game: the timer counts from zero
        igt(g, f)
        v = g.read()
    assert g.coach.step == 0 and g.coach.encounters == [] and g.coach.resets == 1
    assert v.header.endswith("0 encounters")


def test_reset_check_ignores_a_timer_that_never_counted():
    g = Game()
    g.coach.goto(10)
    igt(g, 999999)
    g.read(3)
    igt(g, 0)
    g.read(3)
    assert g.coach.step == 10                          # a wrong timer address can't reset you


def test_pickups_tick_lines_and_move_on():
    import screen
    assert screen.parse_message("Received Ether!") == ("got", "Ether")
    assert screen.parse_message("Received 2,700 Gil!") == ("got", "Gil")
    assert screen.parse_message('Officer "What!? We haven\'t even received the minister s authorization!') is None
    assert screen.parse_message('Zidane "Try to sound more casual. Like me.') is None
    g = Game()
    wreck = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Prima Vista wreck")
    g.coach.goto(wreck - 1)                            # still on the fight before it
    g.screen(5)
    g.coach.pickup_event("Ether")                      # only the step on screen counts
    assert g.coach.step == wreck - 1
    g.coach.goto(wreck)
    g.read()
    assert g.coach.wants_pickup_check()
    g.coach.pickup_event("Ether")
    g.coach.pickup_event("Potion")                     # not on this step: ignored
    g.coach.pickup_event("Ether")
    v = g.read()
    lines = ROUTE["steps"][wreck]["lines"]
    first = next(i for i, l in enumerate(lines) if "Ether" in l)
    assert first in v.done                             # "x2 Ether" ticked after two
    g.coach.pickup_event("Ether")                      # the last one on the step
    g.read()
    assert g.coach.step == wreck + 1                   # everything picked up: next step


def test_only_prefixed_lines_are_pickups():
    from coach import pickup_targets
    from syntax import classify
    assert pickup_targets(["Tracks: arrange items, Gysahl Green", "Stroper: throw a Soft",
                           "Zombie Breath on Zidane: Magic Tag", "Ether chest on the left"]) == []
    assert pickup_targets(["1x Phoenix Pinion (left)", "135x Gil (right)", "x2 Ether",
                           "2x Any item (back right, front middle)", "1x Egoist's Armlet (chest)"]) == [
        (0, ["Phoenix Pinion"]), (1, ["Gil"]), (2, ["Ether", "Ether"]), (3, ["*", "*"]), (4, ["Egoist's Armlet"])]
    assert classify("1x Tent (left)").kind == "pickup"
    assert pickup_targets(["?x Gil (chest)"]) == [(0, ["Gil"])]
    assert classify("1x Tnet (left)").problem                  # misspelt: the editor says so
    assert classify("Buy 2 Mage Masher").kind == "buy"
    assert classify("Synth 1 Exploda").kind == "synth"
    assert classify("Zidane: Coral Ring, -Leather Hat").kind == "equip"
    assert classify("Blank: Broadsword").checked is True       # guests are read from their shared record
    assert classify("Freya front row").kind == "row"
    assert classify("Talk to Marcus").kind == "note"

def menu_game():
    import screen
    g = Game()
    base = 0x1E1000
    g.a.chars = {n: {"exp": base + i * 0x90} for i, n in enumerate(
        ["zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant"])}
    g.a.equip_off = 0x30
    g.a.ability_bits = {"Bird Killer": (0x40, 1), "Bug Killer": (0x40, 2)}
    g.a.menu_flags = [(0x1F0300 + i, 0, 1) for i in range(3)]
    def equip(who, slot, item):
        g.ram.write(g.a.chars[who]["exp"] + 0x30 + slot, screen.ITEMS.index(item), 1)
    def ability(who, name, on=True):
        off, bit = g.a.ability_bits[name]
        a = g.a.chars[who]["exp"] + off
        v = g.ram.u8(a)
        g.ram.write(a, v | (1 << bit) if on else v & ~(1 << bit), 1)
    def menu(open_):
        for i in range(3):
            g.ram.mem[0x1F0300 + i] = 1 if open_ else 0
    return g, equip, ability, menu


def test_menu_lines_tick_highlight_and_move_on():
    g, equip, ability, menu = menu_game()
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "After Plant Brain")
    g.coach.goto(i)
    lines = ROUTE["steps"][i]["lines"]
    blank = next(k for k, l in enumerate(lines) if l.startswith("Blank: Broadsword"))  # read from Amarant's record
    birds = next(k for k, l in enumerate(lines) if l.startswith("Steiner: Bird Killer"))
    zo, so = lines.index("Zidane: Optimize"), lines.index("Steiner: Optimize")
    menu(True)
    v = g.read()
    assert birds not in v.done         # Optimize: ticks when their gear changes
    equip("zidane", 1, "Leather Wrist"); equip("steiner", 0, "Broadsword")
    v = g.read()
    assert zo in v.done and so in v.done
    assert v.highlight is None
    ability("steiner", "Bird Killer")
    v = g.read()
    assert birds not in v.done                               # Bug Killer still off
    ability("steiner", "Bug Killer")
    v = g.read()
    assert birds in v.done and blank not in v.done
    for slot in (1, 3):
        g.ram.write(g.a.chars["amarant"]["exp"] + 0x30 + slot, 255, 1)
    equip("amarant", 0, "Broadsword")                        # Blank's gear, in Amarant's record
    v = g.read()
    assert blank in v.done
    assert g.coach.step == i                                 # menu still open: stay
    menu(False)
    g.read()
    assert g.coach.step == i + 1                             # closed with everything done: next step


def test_menu_equipment_and_take_off_lines():
    g, equip, ability, menu = menu_game()
    route = _copy.deepcopy(ROUTE)
    route["steps"].insert(0, {"disc": 1, "type": "menu", "title": "Test menu",
                              "lines": ["Zidane: Optimize, Coral Ring", "Zidane off: Leather Hat", "Heal Zidane"]})
    g.coach = Coach(route)
    equip("zidane", 2, "Leather Hat")
    menu(True)
    v = g.read()
    assert v.done == []
    equip("zidane", 4, "Coral Ring")
    v = g.read()
    assert v.done == [0]
    equip("zidane", 2, "Leather Wrist")                      # hat off (wrist on instead)
    v = g.read()
    assert v.done == [0, 1]
    menu(False)
    g.read()
    assert g.coach.step == 1


def test_menu_finder_helpers():
    import finder, screen
    snap = bytearray(0x400)
    snap[0x150:0x155] = bytes([2, 0x73, 0x5A, 0x99, 0xFF])
    assert finder.pattern_hits(bytes(snap), 0x100, 0x200, [2, 0x73, None, 0x99, None]) == [0x150]
    a, b = bytearray(16), bytearray(16)
    b[3] = 0b00100000
    b[7] = 0b11                                           # two bits at once (stones count): ignored
    assert finder.changed_bits(bytes(a), bytes(b), 0x1000) == [(0x1003, 5)]
    order = screen.ABILITIES
    learned = {"Bird Killer": divmod(100 + order.index("Bird Killer"), 8),
               "Counter": divmod(100 + order.index("Counter"), 8)}
    full = finder.fill_from_order(learned, order)
    assert full["Man Eater"] == divmod(100 + order.index("Man Eater"), 8)
    assert finder.fill_from_order({"Bird Killer": (5, 0), "Counter": (5, 1)}, order) is None


def test_plant_brain_second_phase_is_not_a_win():
    g = Game()
    b = step_of("plant_brain")
    g.coach.goto(b)
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10916)               # phase 1: 916 + the hidden 10,000
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        v = g.read()
    assert ("Enemy HP 916", "info") in v.badges
    g.ram.write(g.a.enemy_hp[0], 458)                 # Blank arrives: phase 2 with real HP
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 1200 + k * 40)
        v = g.read()
    assert g.coach.step == b                          # still the Plant Brain fight
    assert ("Enemy HP 458", "info") in v.badges, v.badges
    g.ram.write(g.a.enemy_hp[0], 0)                   # phase 2 beaten for real
    g.read(15)
    assert g.coach.step == b + 1


def test_scripted_boss_beaten_just_under_10000_still_counts():
    g = Game()
    i = step_of("cage2")
    g.coach.goto(i)
    g.screen(5, encounters=False)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10533)
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.read()
    g.ram.write(g.a.enemy_hp[0], 9910)                # beaten: just under 10,000
    g.read(15)
    assert g.coach.step == i + 1


def bag_game(start):
    from screen import ITEMS
    g = Game()
    g.a.inventory = 0x1E0000
    bag = dict(start)

    def put():
        for k in range(256):
            g.ram.write(g.a.inventory + 2 * k, 255, 1)
            g.ram.write(g.a.inventory + 2 * k + 1, 0, 1)
        for k, (name, n) in enumerate(x for x in bag.items() if x[1]):
            g.ram.write(g.a.inventory + 2 * k, ITEMS.index(name), 1)
            g.ram.write(g.a.inventory + 2 * k + 1, n, 1)
    put()
    return g, bag, put


def test_shop_buys_and_sells_tick_and_move_on():
    g, bag, put = bag_game({"Potion": 4, "Ether": 2, "Phoenix Pinion": 1, "Eye Drops": 1, "Wrist": 1})
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Treno shop")
    g.coach.goto(i)
    v = g.read(2)
    assert v.done == []
    bag["Mythril Sword"] = 1
    v = g.read(2)
    assert v.done == []               # Ice Staff still to buy
    bag["Ice Staff"] = 1; put()
    v = g.read(2)
    assert 0 in v.done
    bag.update({"Bone Wrist": 1, "Magus Hat": 1, "Bandana": 2}); put()
    v = g.read(2)
    assert v.done == [0, 1, 2]
    bag["Bandana"] = 0; put()                              # equipped: out of the bag, still bought
    v = g.read(2)
    assert v.done == [0, 1, 2]
    bag.update({"Chain Plate": 1, "Chain Mail": 1}); put()
    g.read(2)
    assert g.coach.step == i + 1                           # all bought: next step


def test_shop_sell_lines_and_stolen_counts():
    from coach import shop_check
    assert shop_check("Buy 5 Steepled Hats (4 if you stole one)") == ("buy", [("Steepled Hat", 5, "total")])
    assert shop_check("Sell gems except Diamond, Aquamarine") is None
    assert shop_check("Buy 4-5 Gysahl Greens") == ("buy", [("Gysahl Greens", 4, "add")])
    assert shop_check("Sell all Cotton Robes, extra Wrists") == ("sell", [("Cotton Robe", 1, "all"), ("Wrist", 1, "some")])
    g, bag, put = bag_game({"Ether": 3, "Eye Drops": 1, "Steepled Hat": 1})
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Lindblum weapon shop")
    g.coach.goto(i)
    v = g.read(2)
    assert 1 in v.done                                     # nothing stolen to sell
    assert 0 not in v.done
    bag["Ether"] = 0; bag["Eye Drops"] = 0; put()
    v = g.read(2)
    assert 0 in v.done
    bag["Steepled Hat"] = 5; put()                         # had one stolen: bought 4
    v = g.read(2)
    assert 4 in v.done


def test_file_window_text_is_not_a_steal():
    from screen import parse_message
    assert parse_message("[I Wrist+StoIe Broadsword].sav Date modified SAV File") is None


def test_synth_lines_tick_like_buying_even_for_new_items():
    from coach import shop_check
    assert shop_check("Synth 3 Cotton Robes") == ("buy", [("Cotton Robe", 3, "add")])
    g, bag, put = bag_game({"Potion": 4})
    g.a.inventory_entries = 1                              # set up when the bag held one item
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Lindblum synth")
    g.coach.goto(i)
    g.read(2)
    bag.update({"The Ogre": 1, "Cotton Robe": 3, "Yellow Scarf": 2}); put()
    g.read(3)
    assert g.coach.step == i + 1


def test_one_found_ability_places_the_rest_and_optimize_ticks():
    from memory import fill_abilities
    full = fill_abilities({"Protect Girls": (128, 6)})     # what was found on the real game
    assert full["Bird Killer"] == (125, 5) and full["Level Up"] == (129, 3)
    # A second one found where the list is off: the ones after it follow it instead.
    two = fill_abilities({"Protect Girls": (128, 6), "Alert": (129, 0)})
    assert two["Alert"] == (129, 0) and two["Insomniac"] == (129, 7) and two["Bird Killer"] == (125, 5)
    # Nothing found by hand: the game's own layout (support number n = bit n from byte 124)
    game = fill_abilities({})
    assert game["Protect Girls"] == (128, 6) and game["Alert"] == (129, 1) and game["Insomniac"] == (130, 0)
    g, equip, ability, menu = menu_game()
    g.a.ability_bits = fill_abilities({"Bird Killer": (0x40, 5)})
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Grotto entrance")
    g.coach.goto(i)
    lines = ROUTE["steps"][i]["lines"]
    opt, birds = lines.index("Freya: Optimize"), lines.index("Freya: Bird Killer, Level Up")
    menu(True)
    v = g.read(2)
    assert opt not in v.done
    equip("freya", 0, "Javelin")                          # Optimize changed her gear
    v = g.read(2)
    assert opt in v.done
    ability("freya", "Bird Killer"); ability("freya", "Level Up")
    v = g.read(2)
    assert birds in v.done


def test_row_lines_tick():
    g, equip, ability, menu = menu_game()
    g.a.row_off, g.a.row_bit, g.a.row_back = 0x20, 7, 1
    def row(who, where):
        a = g.a.chars[who]["exp"] + 0x20
        v = g.ram.mem[a]
        g.ram.mem[a] = (v | 0x80) if where == "back" else (v & 0x7F)
    for w in g.a.chars:
        row(w, "back")
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Grotto entrance")
    g.coach.goto(i)
    front = ROUTE["steps"][i]["lines"].index("Freya front row")
    menu(True)
    v = g.read(2)
    assert front not in v.done
    row("freya", "front")
    v = g.read(2)
    assert front in v.done
    from coach import row_check
    assert row_check("Steiner and Freya back row") == (["steiner", "freya"], "back")
    assert row_check("Revive and heal everyone") is None


def test_gizamaluke_capped_hp_is_recognised_and_shown_from_the_card():
    g = Game()
    b = step_of("gizamaluke")
    g.coach.goto(b - 1 if ROUTE["steps"][b - 1]["type"] != "boss" else b)
    g.screen(5, encounters=False)                       # the bell room: no random battles
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 65535)               # too big for the counter
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        v = g.read()
    assert g.coach.step == b and g.coach.battle_is_boss
    assert ("Enemy HP 3,175", "info") in v.badges, v.badges
    g.ram.write(g.a.enemy_hp[0], 64347)               # 1,188 damage
    v = g.read(3)
    assert ("Enemy HP 1,987", "info") in v.badges, v.badges


def test_encounters_count_when_the_random_battle_flag_never_works():
    g = Game()
    g.coach._enc_flag_seen.clear()
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Chocobo's Forest")
    g.coach.goto(i)
    g.screen(30, encounters=False)                     # flag stuck on 0 (wrong address on this copy)
    g.fight([150, 150], 6)
    g.screen(30, encounters=False)
    g.fight([200], 6)
    assert len(g.coach.encounters) == 2
    assert g.coach.step == i


def test_encounter_counts_at_the_start_of_the_fight():
    g = Game()
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Chocobo's Forest")
    g.coach.goto(i)
    g.screen(30)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 150)
    for k in range(30):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        v = g.read()
    assert len(g.coach.encounters) == 1 and "1 encounter" in v.header
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(3)
    g.code(False)
    g.read(20)
    assert len(g.coach.encounters) == 1                # not counted again at the end


def test_pickups_come_from_the_bag_when_it_is_set_up():
    g, bag, put = bag_game({"Potion": 2})
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Chapel Tent")
    g.coach.goto(i)
    g.read(3)
    g.coach.pickup_event("Tent")                       # the screen alone doesn't count now
    assert g.coach.step == i
    bag["Potion"] = 3; put()                           # not what this step wants
    g.read(3)
    assert g.coach.step == i
    bag["Tent"] = 1; put()
    g.read(3)
    assert g.coach.step == i + 1


def test_black_frame_does_not_switch_to_reading_the_screen():
    import screen
    w = screen.StealWatcher.__new__(screen.StealWatcher)
    w.screen_only, w._window_ok = False, True
    w.pid, w.ov, w.full = 1, None, True
    class O: max_dim = 2000
    w.ocr = O()
    old = (screen.message_rect, screen.main_window, screen.client_rect_for_pid, screen.capture_window)
    screen.message_rect = lambda *a: (0, 0, 10, 10)
    screen.main_window = lambda pid: 5
    screen.client_rect_for_pid = lambda pid: (0, 0, 10, 10)
    screen.capture_window = lambda *a: None             # black for a moment
    try:
        assert w.grab()[1] == b"" and not w.screen_only
    finally:
        screen.message_rect, screen.main_window, screen.client_rect_for_pid, screen.capture_window = old


def test_gil_pickups_come_from_memory_when_set_up():
    g = Game()
    g.a.gil = 0x1E2000
    g.ram.write(g.a.gil, 500, 4)
    i = next(k for k, st in enumerate(ROUTE["steps"])
             if st["type"] == "move" and any(" gil" in (" " + l.lower()) for l in st.get("lines", [])))
    g.coach.goto(i)
    g.read(3)
    g.coach.pickup_event("Gil")                        # the screen alone doesn't count now
    assert not g.coach.picked.get(i)
    g.ram.write(g.a.gil, 616, 4)
    g.read(3)
    assert g.coach.picked.get(i) == ["Gil"]


def test_steals_come_from_the_bag_when_it_is_set_up():
    g, bag, put = bag_game({"Potion": 2})
    b = step_of("masked_man")
    g.coach.goto(b)
    g.screen(5)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10188)
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.read()
    assert g.coach.battle_is_boss
    lines = g.coach._steal_lines()
    assert lines
    g.coach.steal_event("stole", lines[0][1])          # the screen alone doesn't count now
    assert not g.coach._steals_done()
    assert not g.coach.wants_steal_check()             # no screen reading needed
    bag[lines[0][1]] = bag.get(lines[0][1], 0) + 1; put()
    g.ram.write(g.a.enemy_atb[0], 3000)
    g.read(2)
    assert lines[0][0] in g.coach._steals_done()


def test_reset_then_loading_a_save_keeps_your_place():
    g = Game()
    b = step_of("plant_brain")
    g.coach.goto(b)
    t = 60 * 60 * 30
    for k in range(40):
        t += 6
        igt(g, t)
        g.read()
    g.coach.encounters = [t - 100]
    igt(g, 0)                                          # reset to the title screen
    g.read(10)
    igt(g, t - 5000)                                   # load a save from earlier
    g.read()
    for k in range(20):
        igt(g, t - 5000 + k * 6)
        g.read()
    assert g.coach.step == b and g.coach.resets == 0
    assert g.coach.encounters == []                    # that battle came after the save


def test_minus_items_are_taken_off_and_drawn_red():
    from coach import line_segments, menu_checks, old_off_line
    assert line_segments("Zidane: Leather Wrist, -Silk Shirt") == [("Zidane: Leather Wrist, ", False), ("Silk Shirt", True)]
    assert line_segments("Phoenix Down + Potion on Zidane") == [("Phoenix Down + Potion on Zidane", False)]
    assert menu_checks("Zidane: Leather Wrist, -Silk Shirt") == ("zidane", False, [("Leather Wrist", False), ("Silk Shirt", True)], [])
    assert old_off_line("Freya off: Cover, Level Up") == "Freya: -Cover, -Level Up"
    g, equip, ability, menu = menu_game()
    route = _copy.deepcopy(ROUTE)
    route["steps"].insert(0, {"disc": 1, "type": "menu", "title": "Test menu",
                              "lines": ["Zidane: Coral Ring, -Leather Hat", "Heal Zidane"]})
    g.coach = Coach(route)
    equip("zidane", 2, "Leather Hat")
    menu(True)
    equip("zidane", 4, "Coral Ring")
    v = g.read()
    assert v.done == []                                    # hat still on
    equip("zidane", 2, "Leather Wrist")
    v = g.read()
    assert v.done == [0]


def test_config_lines_tick_from_memory():
    from syntax import config_check, classify
    assert config_check("Battle Speed: Fast") == ("Battle Speed", "Fast")
    assert config_check("Target Window: Always") == ("Target Window", "Always")
    assert config_check("Speed: Max") == ("Battle Speed", "Max")         # short names mean the same
    assert classify("Cursor: Memory").kind == "config"
    g, equip, ability, menu = menu_game()
    g.a.config = {"Cursor": (0x1E3000, 0x04, {"Memory": 0x04}), "Battle Speed": (0x1E3001, 0x03, {"Fast": 0x03})}
    route = _copy.deepcopy(ROUTE)
    route["steps"].insert(0, {"disc": 1, "type": "menu", "title": "Config",
                              "lines": ["Cursor: Memory", "Battle Speed: Fast", "Window Color: Classic"]})
    g.coach = Coach(route)
    menu(True)
    v = g.read()
    assert v.done == []
    g.ram.mem[0x1E3000] = 0x84                            # other bits in the byte don't matter
    v = g.read()
    assert v.done == [0]
    g.ram.mem[0x1E3001] = 0x03
    v = g.read()
    assert v.done == [0, 1]                               # Window Color isn't set up: left alone
    menu(False)
    g.read()
    assert g.coach.step == 1


def test_boss_slot_waking_up_late_still_makes_it_the_boss():
    g, bag, put = bag_game({"Potion": 2})
    b = step_of("masked_man")
    g.coach.goto(b)
    g.screen(5, encounters=False)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 10188)
    g.ram.write(g.a.enemy_hp[1], 35)
    g.ram.write(g.a.enemy_hp[2], 37)
    for k in range(25):                                # only the helpers' bars move at first
        g.ram.write(g.a.enemy_atb[1], 100 + k * 40)
        g.ram.write(g.a.enemy_atb[2], 120 + k * 40)
        g.read()
    for k in range(10):                                # then Masked Man's own
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.ram.write(g.a.enemy_atb[1], 1200 + k * 40)
        g.read()
    assert g.coach.battle_is_boss and g.coach.step == b
    bag["Mage Masher"] = 1; put()
    g.ram.write(g.a.enemy_atb[0], 3000)
    g.read(2)
    assert g.coach._steals_done()


def test_fight_steps_written_like_other_steps_load_the_same():
    import copy, routes
    old = json.loads((HERE / "test_route.json").read_text(encoding="utf-8"))
    new = routes.inline_cards(copy.deepcopy(old))
    assert "bosses" not in new
    mm = next(s for s in new["steps"] if s.get("id") == "masked_man")
    assert mm["title"] == "Masked Man" and mm["hp"] == [188] and any("Steal" in l for l in mm["lines"])
    assert routes.expand(copy.deepcopy(new))["bosses"] == routes.expand(copy.deepcopy(old))["bosses"]


def test_boss_hp_shows_with_names():
    import editor
    assert editor.parse_hp("Zorn 229, Thorn 472") == ([229, 472], {"229": "Zorn", "472": "Thorn"})
    assert editor.parse_hp("229, 472") == ([229, 472], {})
    assert editor.parse_hp("Black Waltz 1 229, Sealion 472") == ([229, 472], {"229": "Black Waltz 1", "472": "Sealion"})
    assert editor.parse_hp("Gizamaluke 3,175") == ([3175], {"3175": "Gizamaluke"})
    assert editor.format_hp([229, 472], {"229": "Zorn"}) == "Zorn 229, 472"
    route = _copy.deepcopy(ROUTE)
    route["bosses"]["masked_man"]["hp_names"] = {"188": "Masked Man"}
    g = Game()
    g.coach = Coach(route)
    g.coach.goto(step_of("masked_man"))
    g.screen(5)
    start_fight(g, [10188])
    g.ram.write(g.a.enemy_hp[0], 10100)
    v = g.read(3)
    assert ("Masked Man 100", "info") in v.badges, v.badges


def test_indented_lines_belong_to_the_action_above():
    import editor
    from syntax import turn_groups, classify
    lines = editor.step_text_lines("R1: Attack\nR2: Attack\nR3:\n\tSteal Broadsword\n    Steiner Attack\n\n\nKO")
    assert lines == ["R1: Attack", "R2: Attack", "R3:", "  Steal Broadsword", "  Steiner Attack", "", "KO"]
    assert turn_groups(lines) == [(0, 1), (1, 2), (2, 5), (6, 7)]
    assert classify("  Zidane: Coral Ring").kind == "equip"
    assert classify("  1x Tent (left)").kind == "pickup"
    route = _copy.deepcopy(ROUTE)
    route["bosses"]["masked_man"]["turns"] = lines
    g = Game()
    g.coach = Coach(route)
    g.coach.goto(step_of("masked_man"))
    g.screen(5)
    start_fight(g, [10188])
    assert turn_groups(lines)[2] == (2, 5)                     # R3 and the two lines under it
    assert 3 in [i for i, _ in g.coach._steal_lines()]


def test_typed_hp_off_still_finds_a_scripted_boss():
    route = _copy.deepcopy(ROUTE)
    route["bosses"]["plant_brain"]["hp"] = [510]          # Blank's phase typed, not Plant Brain's
    g = Game()
    g.coach = Coach(route)
    b = step_of("plant_brain")
    g.coach.goto(b)
    g.screen(5)
    g.fight([10916], 15, end_hp=[9950])
    assert g.coach.step == b + 1


def test_battle_id_tells_fights_apart_and_is_learned():
    learned = tmp_learned()
    g = Game(learned)
    g.a.battle_id = 0x1E5000
    b = step_of("black_waltz_2")
    g.coach.goto(b)
    g.screen(30)
    g.ram.write(g.a.battle_id, 77)
    g.fight([75, 75, 75, 75], 20)                      # a random battle: nothing known yet, not the boss
    assert g.coach.step == b and len(g.coach.encounters) == 1
    g.ram.write(g.a.battle_id, 300)
    g.fight([11030], 20, end_hp=[9990])                # the real one: learned by its number
    assert g.coach.step == b + 1
    assert g.coach.learned.battle_id("black_waltz_2") == 300

    g2 = Game(learned)                                 # next run
    g2.a.battle_id = 0x1E5000
    g2.coach.goto(b - 2)
    g2.screen(30)
    g2.ram.write(g2.a.battle_id, 301)
    g2.fight([11030], 20, end_hp=[9990])               # same HP, different battle: not it
    assert g2.coach.step == b - 2
    g2.ram.write(g2.a.battle_id, 300)
    g2.fight([500], 20)                                # its number, whatever the HP
    assert g2.coach.step == b + 1


def _stats(g, k, seed):
    """Fill enemy slot k's record (past HP and ATB) with that enemy kind's stats."""
    import random
    r = random.Random(seed)
    base = g.a.slot(4 + k, 0)
    for off in range(16, g.a.slot_stride):
        g.ram.write(base + off, r.randrange(1, 256), 1)


def test_fights_recognised_by_their_enemies_records():
    learned = tmp_learned()
    g = Game(learned)
    b = step_of("black_waltz_2")
    g.coach.goto(b)
    g.screen(30)
    for k in range(4):
        _stats(g, k, "goblin")
    g.fight([75, 75, 75, 75], 20)                      # a random battle: not the boss
    assert g.coach.step == b
    for k in range(4):
        g.ram.mem[g.a.slot(4 + k, 0):g.a.slot(4 + k, 0) + g.a.slot_stride] = bytes(g.a.slot_stride)
    _stats(g, 0, "black waltz 2")
    g.fight([11030], 20, end_hp=[9990])                # the boss (hidden HP): learned
    assert g.coach.step == b + 1
    assert g.coach.learned.fingerprint("black_waltz_2")

    g2 = Game(learned)                                 # next run
    g2.coach.goto(b - 2)
    g2.screen(30, encounters=False)
    _stats(g2, 0, "something else")
    g2.fight([11030], 20, end_hp=[9990])               # same HP, other enemy: not it
    assert g2.coach.step != b + 1
    g2.coach.goto(b - 2)
    g2.screen(30)
    _stats(g2, 0, "black waltz 2")
    g2.fight([600], 20)                                # its enemy, whatever the HP
    assert g2.coach.step == b + 1


def test_a_finished_step_you_come_back_to_stays_and_loads_undo_pickups():
    g, bag, put = bag_game({"Potion": 2})
    i = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Chapel Tent")
    g.coach.goto(i)
    g.read(3)
    igt(g, 6000)
    g.read()
    for t in range(6000, 6400, 6):
        igt(g, t)
        g.read()
    bag["Tent"] = 1; put()
    g.read(3)
    assert g.coach.step == i + 1                        # picked up: moved on
    g.coach.prev_step()
    g.read(5)
    assert g.coach.step == i                            # back on it (F7): stays, though it's done
    igt(g, 6100)                                        # a savestate from before the Tent
    g.read()
    assert not g.coach._pickups_done(i)                 # that pickup hasn't happened in this game


def test_scripted_boss_ending_with_others_standing_moves_on():
    route = _copy.deepcopy(ROUTE)
    route["bosses"]["steiner2"].pop("hp", None)                 # no HP typed for the fight
    g = Game()
    g.coach = Coach(route)
    b = step_of("steiner2")
    g.coach.goto(b)
    g.screen(5, encounters=False)
    g.fight([10033, 10167, 10038], 20, end_hp=[9941, 10167, 10038])   # Steiner beaten, the rest standing
    assert g.coach.step == b + 1




def test_buy_sell_synth_headers_with_indented_items():
    from syntax import expand_headers, shop_check
    lines = ["Sell", "  All Cotton Robes", "  Extra Wrists", "", "Buy", "  21 Potions",
             "  1 Headgear, Iron Helm, Leather Plate", "", "1x Tent (Right side)", "Synth", "  The Ogre"]
    read = expand_headers(lines)
    assert read[1] == "Sell All Cotton Robes" and read[5] == "Buy 21 Potions" and read[10] == "Synth The Ogre"
    assert read[8] == "1x Tent (Right side)" and read[0] == "" and read[4] == ""
    assert shop_check(read[1]) == ("sell", [("Cotton Robe", 1, "all")])
    assert shop_check(read[2]) == ("sell", [("Wrist", 1, "some")])
    assert shop_check(read[6])[1] == [("Headgear", 1, "add"), ("Iron Helm", 1, "add"), ("Leather Plate", 1, "add")]
    g, bag, put = bag_game({"Cotton Robe": 3, "Wrist": 4})
    route = _copy.deepcopy(ROUTE)
    route["steps"].insert(0, {"disc": 1, "type": "menu", "title": "Shop", "lines": lines[:6]})
    g.coach = Coach(route)
    v = g.read(3)
    assert v.done == []
    bag.update({"Cotton Robe": 0, "Wrist": 2}); put()
    v = g.read(2)
    assert v.done == [1, 2]
    bag["Potion"] = 21; put()
    g.read(3)
    assert g.coach.step == 1                                   # all done: next step


def test_more_ways_of_writing_lines():
    from syntax import pickup_items, row_check, find_item
    assert pickup_items("1610 Gil (Down the round stairs)") == ["Gil"]
    assert pickup_items("2000 Gill (In the back)") == ["Gil"]
    assert row_check("Front row Zidane") == (["zidane"], "front")
    assert find_item("Ogre") == "The Ogre" and find_item("Phoenix Pinons") == "Phoenix Pinion"
    assert find_item("Goblin cards") is None


def test_editor_flags_discs_out_of_order():
    import editor
    r = {"steps": [{"disc": 1, "type": "move", "title": "A", "lines": ["x"]},
                   {"disc": 2, "type": "move", "title": "B", "lines": ["x"]},
                   {"disc": 1, "type": "move", "title": "C", "lines": ["x"]}]}
    assert any("out of order" in m for _, _, m in editor.find_problems(r))


def test_leftover_big_number_in_empty_slot_is_not_hidden_boss_hp():
    """Lifa Tree: random battles on Soulcage's step had 64,338 sat in an empty slot."""
    g = Game()
    b = step_of("cage2")
    g.coach.goto(b)
    g.screen(30)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 1469)
    g.ram.write(g.a.enemy_hp[1], 64338)                # never moves: a leftover
    for k in range(20):                                # only the party's bars move at first:
        g.ram.write(g.a.party_atb[0], 100 + k * 40)    # judged before any enemy has moved
        g.read()
    for k in range(60):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 41)
        g.read()
    assert not g.coach.battle_is_boss
    assert len(g.coach.encounters) == 1
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(3)
    g.code(False)
    g.read(10)
    assert g.coach.step == b and len(g.coach.encounters) == 1


def test_records_learned_off_a_leftover_slot_dont_hide_a_boss_with_its_hp():
    """Beatrix II scored 0 against her learned records but had her exact HP."""
    learned = tmp_learned()
    g = Game(learned)
    b = step_of("black_waltz_1")
    stride = g.a.slot_stride
    g.coach.learned.set_fingerprint("black_waltz_1", {"slots": {
        "0": {"b": ("5a" * stride), "dyn": []}, "1": {"b": ("33" * stride), "dyn": []}}})
    g.coach.goto(b - 2)
    g.screen(30)
    _stats(g, 0, "black waltz 1")
    g.fight([472], 20)
    assert g.coach.step == b + 1
    fp = g.coach.learned.fingerprint("black_waltz_1")
    assert list(fp["slots"]) == ["0"]                  # learned again, from the real enemy


def _multi_game(boss, n, learned=None):
    route = _copy.deepcopy(ROUTE)
    route["bosses"][boss]["fights"] = n
    g = Game(learned)
    g.coach = Coach(route, learned_path=learned)
    g.coach._enc_flag_seen.add(True)
    return g


def test_step_with_several_fights_moves_on_after_the_last():
    g = _multi_game("black_waltz_1", 3, tmp_learned())
    b = step_of("black_waltz_1")
    g.coach.goto(b)
    g.screen(30)
    g.fight([472], 20)
    assert g.coach.step == b and g.coach.sub == 1
    v = g.read()
    assert "fight 2 of 3" in v.title, v.title
    g.fight([75, 75], 20)                              # a random battle in between
    assert g.coach.step == b and g.coach.sub == 1 and len(g.coach.encounters) == 1
    g.fight([472], 20)
    assert g.coach.step == b and g.coach.sub == 2
    g.coach.prev_step()                                # F7: back one fight, not one step
    assert g.coach.step == b and g.coach.sub == 1
    g.coach.next_step()                                # out of a fight F8 still moves the step
    assert g.coach.step == b + 1 and g.coach.sub == 0
    g.coach.goto(b)
    g.fight([472], 20)
    g.fight([472], 20)
    g.fight([472], 20)
    assert g.coach.step == b + 1 and len(g.coach.encounters) == 1


def test_fights_on_a_several_fight_step_can_be_taught_with_f8():
    learned = tmp_learned()
    g = _multi_game("cage2", 2, learned)               # no HP on the card
    b = step_of("cage2")
    g.coach.goto(b)
    g.screen(30)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 300)
    g.ram.write(g.a.enemy_hp[1], 310)
    for k in range(40):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 41)
        g.ram.write(g.a.enemy_atb[1], 120 + k * 41)
        g.read()
    assert not g.coach.battle_is_boss and len(g.coach.encounters) == 1
    g.coach.next_step()                                # F8: "this is one of them"
    assert g.coach.battle_is_boss and g.coach.step == b and not g.coach.encounters
    g.ram.write(g.a.enemy_hp[0], 0)
    g.ram.write(g.a.enemy_hp[1], 0)
    g.read(3)
    g.code(False)
    g.read(20)
    assert g.coach.step == b and g.coach.sub == 1
    assert g.coach.learned.formation("cage2") == [300, 310]
    g.fight([300, 310], 20)                            # the second one: same soldiers, known now
    assert g.coach.step == b + 1 and not g.coach.encounters


def test_comment_lines_are_shown_but_never_checked():
    from syntax import classify, expand_headers, menu_checks, pickup_items, turn_groups
    c = "# Zidane: The Ogre / Headgear / Glass Armlet / Coral Ring"
    assert classify(c).kind == "comment"
    assert menu_checks(c) is None and pickup_items("# 1x Ether (Inn)") is None
    assert expand_headers(["Buy", "  2x Potion", "  # cheaper in Treno", "  1x Tent"]) == \
        ["", "Buy 2x Potion", "  # cheaper in Treno", "Buy 1x Tent"]
    assert turn_groups(["Zidane: Attack", "# crit at 289", "Vivi: Fire"]) == [(0, 2), (2, 3)]
    import editor
    route = {"steps": [{"disc": 1, "type": "menu", "title": "M",
                        "lines": ["Zidane: Optimize", "# " + "x" * 80]}]}
    assert editor.find_problems(route) == []


def test_skipping_steps_learns_nothing():
    learned = tmp_learned()
    g = Game(learned)
    g.coach.goto(5)
    g.screen(40)
    before = json.dumps(g.coach.learned.data, sort_keys=True)
    g.coach.skip(1); g.coach.skip(1); g.coach.skip(1)
    assert g.coach.step == 8
    g.screen(40)                                       # same screen: no jump, nothing learned
    g.screen(41)                                       # a new screen: not learned for step 8
    g.coach.skip(-2)
    assert g.coach.step == 6
    assert json.dumps(g.coach.learned.data, sort_keys=True) == before
    b = step_of("black_waltz_1")
    g.coach.skip(b - g.coach.step - 1)
    g.code(True)                                       # a fight starts, then you skip onto the boss
    g.ram.write(g.a.enemy_hp[0], 333)
    for k in range(20):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 40)
        g.read()
    g.coach.skip(1)
    assert g.coach.step == b and not g.coach.battle_is_boss
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(3)
    g.code(False)
    g.read(70)
    assert g.coach.step == b                           # the fight didn't move the route
    assert g.coach.learned.formation("black_waltz_1") is None
    assert g.coach.learned.fingerprint("black_waltz_1") is None


def test_compare_routes_lines_up_steps_and_takes_one_across():
    import editor
    a = {"steps": [{"disc": 1, "type": "menu", "title": "Config", "lines": ["Speed: Fast"]},
                   {"disc": 1, "type": "boss", "id": "steiner_i", "title": "Steiner I", "lines": ["Attack"]},
                   {"disc": 1, "type": "move", "title": "Chapel Tent", "lines": ["1x Tent"]}]}
    b = {"steps": [{"disc": 1, "type": "menu", "title": "Config", "lines": ["Speed: Fast"]},
                   {"disc": 1, "type": "menu", "title": "Before Steiner 1", "lines": ["Zidane: Optimize"]},
                   {"disc": 1, "type": "boss", "id": "x", "title": "Steiner 1", "lines": ["Attack x2"], "hp": [169]}]}
    rows = editor.compare_routes(a, b)
    got = [(r["status"], r["a"], r["b"]) for r in rows]
    assert got == [("same", 0, 0), ("only_b", None, 1), ("changed", 1, 2), ("only_a", 2, None)], got
    assert rows[2]["diff"] == ["title", "lines", "HP"]
    at = editor.take_step(a, b, rows[1])                  # only there: goes in after Config
    assert at == 1 and a["steps"][1]["title"] == "Before Steiner 1"
    row = next(r for r in editor.compare_routes(a, b) if r["b"] == 2)
    editor.take_step(a, b, row)                           # in both: replaced, keeping our fight id
    assert a["steps"][2]["lines"] == ["Attack x2"] and a["steps"][2]["id"] == "steiner_i"


def test_backup_notes_keep_line_breaks_and_indents():
    import editor
    v = "\n".join(editor.step_text_lines("If Vivi dies:\n\tPD her\n\nElse attack\n"))
    assert v == "If Vivi dies:\n  PD her\n\nElse attack"


def _inside_route():
    import routes
    return routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Before", "lines": ["Walk"]},
        {"disc": 1, "type": "move", "title": "Crash Site", "lines": ["Vivi > Baku > Steiner > Exit"]},
        {"disc": 1, "type": "boss", "title": "Baku", "lines": ["Attack x3"], "hp": [202], "inside": True},
        {"disc": 1, "type": "boss", "title": "Plant Brain", "lines": ["Fire"], "hp": [510]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk"]}]})


def test_fight_inside_a_step_goes_back_to_it():
    g = Game(tmp_learned())
    g.coach = Coach(_inside_route(), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    g.coach.goto(1)
    g.screen(30)
    v = g.read()
    assert "# On the way: Baku" in v.lines
    g.fight([202], 20)
    assert g.coach.step == 1 and 2 in g.coach.inside_done      # back on Crash Site
    v = g.read()
    assert "# \u2713 Baku done" in v.lines
    g.fight([510], 20)                                         # Plant Brain: recognised from Crash Site
    assert g.coach.step == 4


def test_f8_moves_past_fights_inside_a_step_and_back_out_of_them():
    g = Game(tmp_learned())
    g.coach = Coach(_inside_route(), learned_path=tmp_learned())
    g.coach.goto(1)
    g.coach.next_step()
    assert g.coach.step == 3                                   # past Baku, to Plant Brain
    g.coach.goto(2)
    v = g.read()
    assert "in Crash Site" in v.title
    g.coach.next_step()
    assert g.coach.step == 1 and 2 in g.coach.inside_done


def test_every_ability_ap_is_read_from_the_character_records():
    from memory import read_state
    g = Game()
    g.a.chars = {"zidane": {"exp": 0x08B298}, "vivi": {"exp": 0x08B328}}
    g.ram.write(0x08B298 + 0x4C, 12, 1)                 # Zidane: Flee 12 AP
    g.ram.write(0x08B298 + 0x4C + 31, 35, 1)            # Protect Girls learned
    g.ram.write(0x08B328 + 0x4C, 7, 1)                  # Vivi: first entry
    st = read_state(g.ram, g.a)
    assert st.ap["zidane:Flee"] == (12, 40) and st.ap["zidane:Protect Girls"] == (35, 35)
    import ability_data
    first = ability_data.ABILITY_LISTS["vivi"][0]
    assert st.ap["vivi:" + first[0]] == (7, first[1])


def test_ap_shows_only_where_an_at_line_asks_for_it():
    g = Game()
    g.a.chars = {"zidane": {"exp": 0x1E0000, "level": 0x1E0010}, "freya": {"exp": 0x1E0200}}
    g.ram.write(0x1E0000 + 0x4C, 27, 1)                  # Flee 27/40
    g.ram.write(0x1E0000 + 0x4C + 31, 9, 1)              # Protect Girls 9/35
    import ability_data
    man = next(i for i, e in enumerate(ability_data.ABILITY_LISTS["freya"]) if e and e[0] == "Man Eater")
    g.ram.write(0x1E0200 + 0x4C + man, 20, 1)            # Freya Man Eater learned (20)
    step = {"disc": 1, "type": "menu", "title": "T",
            "lines": ["Zidane: Protect Girls, Flee", "@Zidane: Protect Girls 17", "@Flee",
                      "@Freya: Man Eater"]}
    g.coach.steps[g.coach.step] = step
    v = g.read()
    assert v.stats == []                                  # naming an ability no longer shows AP
    assert v.lines[1] == "@Zidane Protect Girls  9/17 AP" and 1 not in v.done
    g.coach.steps[g.coach.step]["lines"].insert(1, "@Zidane: Protect Girls 17, Flee")
    assert g.read().lines[1:3] == ["@Zidane Protect Girls  9/17 AP", "@Zidane Flee  27/40 AP"]
    del g.coach.steps[g.coach.step]["lines"][1]
    assert v.lines[2] == "@Zidane Flee  27/40 AP"
    assert v.lines[3] == "@Freya Man Eater  20/20 AP (learned)" and 3 in v.done
    g.ram.write(0x1E0000 + 0x4C + 31, 17, 1)             # reached the 17 asked for
    v = g.read()
    assert 1 in v.done and v.lines[1] == "@Zidane Protect Girls  17/17 AP"
    from syntax import classify, menu_checks
    assert classify("@Zidane: Nonsense").problem and menu_checks("@Zidane: Flee") is None


def test_menu_steps_stay_menu_steps():
    import routes
    r = routes.inline_cards({"steps": [{"disc": 1, "type": "menu", "title": "M", "lines": ["a"]}]})
    assert r["steps"][0]["type"] == "menu"


def test_fight_list_fills_a_fight_step_and_keeps_your_own():
    import fights_data, tempfile
    from pathlib import Path
    old = fights_data.CUSTOM
    fights_data.CUSTOM = Path(tempfile.mkdtemp()) / "fights_custom.json"
    try:
        names = [f["name"] for f in fights_data.FIGHTS]
        assert len(names) == len(set(names)) and "Soulcage" in names and "Necron" in names
        step = {"disc": 2, "type": "boss", "id": "x", "title": "New fight", "lines": ["Attack"], "fights": 9}
        fights_data.apply(step, next(f for f in fights_data.FIGHTS if f["name"] == "Mistodons (Iifa Tree)"))
        assert step["title"] == "Mistodons (Iifa Tree)" and step["hp"] == [1473] and step["fights"] == 4
        step["title"] = "Lifa Tree Fights"
        fights_data.apply(step, next(f for f in fights_data.FIGHTS if f["name"] == "Mistodons (Alexandria)"))
        assert step["title"] == "Lifa Tree Fights" and step["preset"] == "Mistodons (Alexandria)"
        assert step["lines"] == ["Attack"]                    # your actions stay
        fights_data.save_custom({"name": "Ogre (Hilda Garde)", "disc": 3, "hp": [3300]})
        mine = fights_data.all_fights()[-1]
        assert mine["mine"] and fights_data.label(mine) == "Mine · Ogre (Hilda Garde)"
        import routes
        r = routes.expand({"steps": [step]})
        assert r["bosses"]["x"]["hp"] == [1473]
    finally:
        fights_data.CUSTOM = old


def test_guests_empty_slots_and_sell_all_except_tick():
    import memory
    g, equip, ability, menu = menu_game()
    memory.add_guest(g.a.chars)
    assert g.a.chars["guest"]["exp"] == g.a.chars["zidane"]["exp"] + 8 * 0x90
    step = {"disc": 3, "type": "menu", "title": "T",
            "lines": ["Beatrix: Rubber Helm, -Armor, -Accessory", "Vivi: -All"]}
    g.coach.steps[g.coach.step] = step
    for slot in range(5):                                # everyone fully dressed
        for who in ("guest", "vivi"):
            g.ram.write(g.a.chars[who]["exp"] + 0x30 + slot, 10 + slot, 1)
    menu(True)
    v = g.read()
    assert v.done == []
    equip("guest", 1, "Rubber Helm")
    g.ram.write(g.a.chars["guest"]["exp"] + 0x30 + 3, 255, 1)
    g.ram.write(g.a.chars["guest"]["exp"] + 0x30 + 4, 255, 1)
    assert g.read().done == [0]                          # Beatrix: helm on, armour and accessory off
    for slot in (1, 2, 3, 4):
        g.ram.write(g.a.chars["vivi"]["exp"] + 0x30 + slot, 255, 1)
    assert g.read().done == [0, 1]                       # weapon stays: "-All" is the other four

    g, bag, put = bag_game({"Hi-Potion": 20, "Ether": 3, "Tent": 2, "Diamond": 1})
    step = {"disc": 3, "type": "menu", "title": "Shop", "lines": ["Sell all except", "  Hi-Potion",
                                                               "  Ether", "  Diamond", "Buy 1 Tent"]}
    g.coach.steps[g.coach.step] = step
    v = g.read(2)
    assert 0 not in v.done
    del bag["Tent"]; put()
    v = g.read(2)
    assert 0 in v.done
    bag["Holy Lance"] = 1; put()                         # buying after selling: stays ticked
    assert 0 in g.read(2).done


def test_if_lines_dont_hold_a_step_up_and_later_pickups_move_you_on():
    g, bag, put = bag_game({"Potion": 4})
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "A",
                        "lines": ["1x Ether", "1x Tent (if no Nobles)"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "B", "lines": ["1x Elixir"]}
    g.coach.steps[i + 2] = {"disc": 1, "type": "move", "title": "C", "lines": ["Talk to Vivi", "1x Phoenix Down"]}
    g.coach._entry_step = None
    g.read(2)
    g.now += 10                                        # well after any fight
    bag["Ether"] = 1; put()
    g.read(3)
    assert g.coach.step == i + 1                       # the "(if ...)" Tent doesn't hold it up
    g.read(2)
    bag["Phoenix Down"] = 1; put()                     # C's item, B's Elixir skipped
    g.read(3)
    assert g.coach.step >= i + 2                       # at C (and C's last line is done: past it)
    assert any("picked up Phoenix Down" in e for e in g.coach.events)


def test_editor_report_says_how_each_step_moves_on_and_what_last_run_needed():
    import report, json as _j
    from coach import Learned
    route = {"name": "R", "steps": [
        {"disc": 1, "type": "move", "title": "Walk A", "lines": ["Go left"]},
        {"disc": 1, "type": "move", "title": "Walk B", "lines": ["1x Ether"]},
        {"disc": 1, "type": "menu", "title": "Menu C", "lines": ["Talk to Vivi"]},
        {"disc": 1, "type": "boss", "title": "Boss D", "id": "boss_d", "lines": ["Attack"], "hp": [4736]},
        {"disc": 1, "type": "move", "title": "Walk E", "lines": ["Up"]}]}
    path = tmp_learned()
    L = Learned(path)
    rep = report.step_report(route, L)
    assert not rep[0]["ok"] and "F8" in rep[0]["how"]       # nothing to check, nothing learned
    assert rep[1]["ok"] and "1 checked line" in rep[1]["how"]
    assert rep[2]["ok"] and "fight starts" in rep[2]["how"]  # the next step's fight has typed HP
    assert rep[3]["ok"] and "4736" in rep[3]["how"]
    L.set_field("move:Walk B:1x Ether", 352, "R")
    assert "Dali/Inn (352)" in report.step_report(route, L)[0]["how"]
    L.set_formation("boss_d", [523, 523])                    # taught by a stray F8
    probs = report.run_problems(route, L, Path(tempfile.mkdtemp()) / "none.txt")
    assert any("523" in m and "Forget" in m for _, _, m in probs)
    log = Path(tempfile.mkdtemp()) / "run_log.txt"
    log.write_text("\n=== 2026-10-28 05:00:00 route: Other ===\n05:00:01 step 1 Walk A -> 2 Walk B (F8)\n"
                   "\n=== 2026-10-28 06:00:00 route: R ===\n06:00:01 step 1 Walk A -> 2 Walk B (F8)\n"
                   "06:00:02 step 3 Menu C -> 2 Walk B (F7)\n", encoding="utf-8")
    probs = report.run_problems(route, L, log)
    msgs = [m for _, _, m in probs if m.startswith("Last run")]
    assert any("step 1" in m and "F8" in m for m in msgs)
    assert any("step 2" in m and "F7" in m for m in msgs)
    L.forget_step("boss:boss_d", "R", ["boss_d"])
    assert L.formation("boss_d") is None


def test_fight_learned_where_no_randoms_isnt_taken_for_randoms_with_the_same_enemies():
    learned = tmp_learned()
    g = Game(learned)
    z = step_of("zombies")                              # no HP typed: learned by winning it
    g.coach.goto(z)
    g.screen(1754, encounters=False)                    # the elevator: no random battles
    for k in range(3):
        _stats(g, k, "zombie")
    g.fight([1066, 973, 973], 20)
    assert g.coach.step == z + 1
    g2 = Game(learned)
    g2.coach.goto(z - 1)
    g2.screen(1751, encounters=True)                    # outside: random Zombies roam
    for k in range(3):
        _stats(g2, k, "zombie")
    g2.fight([1066, 973, 973], 20)
    assert g2.coach.step == z - 1                       # a random battle, not the elevator
    assert len(g2.coach.encounters) == 1


def test_shop_lines_written_with_x_counts():
    from syntax import shop_check
    assert shop_check("Buy 3x Silver Fork") == ("buy", [("Silver Fork", 3, "add")])
    assert shop_check("Buy 1x Trident") == ("buy", [("Trident", 1, "add")])
    assert shop_check("Sell 2x Cotton Robe (keep 1!)") == ("sell", [("Cotton Robe", 2, "less")])
    g, bag, put = bag_game({"Cotton Robe": 3, "Potion": 2})
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "menu", "title": "Shop",
                        "lines": ["Sell", "  2x Cotton Robe", "Buy", "  3x Silver Fork"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "Next", "lines": ["Go"]}
    g.coach._entry_step = None
    g.read(2)
    bag["Cotton Robe"] = 1; put()
    v = g.read(2)
    assert 1 in v.done and g.coach.step == i              # sold two (one kept); forks still to buy
    bag["Silver Fork"] = 2; put()
    g.read(2)
    assert g.coach.step == i
    bag["Silver Fork"] = 3; put()
    g.read(2)
    assert g.coach.step == i + 1


def test_walk_step_with_more_written_after_its_pickup_doesnt_move_on_by_the_pickup():
    g, bag, put = bag_game({"Potion": 4})
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "A",
                        "lines": ["1x Ether (chest)", "Talk to Vivi, then exit left"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "B", "lines": ["1x Elixir", "# a note"]}
    g.coach.steps[i + 2] = {"disc": 1, "type": "move", "title": "C", "lines": ["Up"]}
    g.coach._entry_step = None
    g.read(2)
    g.now += 10
    bag["Ether"] = 1; put()
    v = g.read(3)
    assert 0 in v.done and g.coach.step == i           # ticked, but "Talk to Vivi" is still to do
    g.coach.next_step()
    g.read(2)
    bag["Elixir"] = 1; put()
    g.read(3)
    assert g.coach.step == i + 2                       # nothing after B's pickup: moves on


def test_panel_title_flags_steps_the_coach_still_has_to_learn():
    from coach import FLAG
    g = Game(tmp_learned())
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "A", "lines": ["Go left"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "B", "lines": ["Up"]}
    v = g.read()
    assert v.title == FLAG + "A"                          # nothing to check, next screen unknown
    g.coach.steps[i]["lines"] = ["1x Ether"]
    assert g.read().title == "A"                          # its pickup moves it on
    z = step_of("zombies")                                # no HP typed, nothing learned
    g.coach.goto(z)
    assert g.read().title.startswith(FLAG)
    g.coach.goto(step_of("black_waltz_2"))                # typed HP
    assert not g.read().title.startswith(FLAG)


def test_next_step_shown_underneath_only_when_asked():
    g = Game(tmp_learned())
    b = step_of("black_waltz_2")
    g.coach.goto(b - 1)
    v = g.read()
    assert v.next_title == ""                              # off by default
    g.coach.show_next = True
    v = g.read()
    assert "Black Waltz 2" in v.next_title and v.next_kind == "boss" and v.next_lines
    g.coach.goto(b)
    v = g.read()
    assert v.next_title and v.next_kind != ""


def test_memory_setup_window_runs_a_finder_and_saves():
    import json as _j, finder, setup_finder, paths
    tmp = Path(tempfile.mkdtemp()) / "config.json"
    tmp.write_text(_j.dumps({"addresses": {}}))
    old = (finder.CONFIG, finder.open_ram)
    ram = FakeRam()
    ram.snapshot = lambda: bytes(ram.mem)
    ram.write(0x1234, 98765, 4)
    finder.CONFIG, finder.open_ram = tmp, (lambda cfg: ram)
    try:
        r = setup_finder.Runner()
        r.start(["gil"])
        while True:
            kind, val = r.out.get(timeout=20)
            if kind == "ask":
                r.answer("98765")
            if kind == "done":
                break
        assert val == 0
        assert _j.loads(tmp.read_text())["addresses"]["gil"] == "0x001234"
        assert setup_finder.status_text(setup_finder.TASKS[0], {}) == "not set"
    finally:
        finder.CONFIG, finder.open_ram = old


def test_walk_sub_step_goes_back_to_its_step_and_lv5_single_tick():
    g, bag, put = bag_game({"Potion": 4})
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "Crash Site", "lines": ["1x Ether", "Talk to Vivi"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "Side room", "lines": ["1x Elixir"], "inside": True}
    g.coach.steps[i + 2] = {"disc": 1, "type": "move", "title": "After", "lines": ["Go"]}
    g.coach._entry_step = None
    g.read(2)
    g.now += 10
    bag["Elixir"] = 1; put()                           # the sub-step's item: you're in it
    g.read(3)
    v = g.read()
    assert g.coach.step == i, g.coach.events          # done: back to Crash Site
    assert any("Side room done" in l for l in v.lines)
    from coach import Coach
    boss = {"lv5": True}
    from memory import GameState
    st = GameState(enemy_hp=[], enemy_atb=[], party_atb=[], party_hp_slots=[], igt=0,
                   encounters_possible=False, battle_sig=b"", levels={"zidane": 15, "vivi": 12})
    c = Coach(ROUTE)
    assert "Zidane Lv 15" in c._lv5_badges(boss, st)[0][0]           # party unknown: everyone
    st.levels = {"zidane": 15, "eiko": 15, "vivi": 12}
    st.char_hp = {"zidane": 431, "eiko": 210, "vivi": 188}
    c.in_battle, c.battle_start_party = True, [431, 188, 0, 0]       # Zidane and Vivi in the fight
    msg = c._lv5_badges(boss, st)[0][0]
    assert "Zidane Lv 15" in msg and "Eiko" not in msg
    c.in_battle = False                                              # before the next boss: last party
    assert "Eiko" not in c._lv5_badges(boss, st)[0][0]


def _branch_route():
    import routes
    return routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Start", "lines": ["Walk"]},
        {"disc": 1, "type": "move", "title": "Swap Blank", "lines": ["Blank: Broadsword"]},
        {"disc": 1, "type": "move", "title": "No swap", "lines": ["Walk"], "alt": True,
         "when": "Broadsword < 2"},
        {"disc": 1, "type": "move", "title": "Middle", "lines": ["Walk"]},
        {"disc": 1, "type": "move", "title": "Won A", "lines": ["Walk"]},
        {"disc": 1, "type": "move", "title": "Lost A", "lines": ["Walk"], "alt": True},
        {"disc": 1, "type": "move", "title": "Lost B", "lines": ["Walk"], "alt": True},
        {"disc": 1, "type": "move", "title": "Buy Iron Sword", "lines": ["Buy 1 Iron Sword"],
         "when": "fewer than 2 Broadsword"},
        {"disc": 1, "type": "move", "title": "End", "lines": ["Walk"]}]})


def _branch_state(bag, steiner_sword=True):
    from memory import GameState
    from screen import ITEMS
    return GameState(enemy_hp=[], enemy_atb=[], party_atb=[], party_hp_slots=[], igt=0,
                     encounters_possible=False, battle_sig=b"",
                     items={ITEMS.index(k): n for k, n in bag.items()},
                     equips={"steiner": [ITEMS.index("Broadsword") if steiner_sword else 0, 0, 0, 0, 0],
                             "eiko": [ITEMS.index("Broadsword"), 0, 0, 0, 0]},     # not joined: leftovers
                     exp={"steiner": 120, "eiko": 0})


def _got(c, *items):
    c.obtained = [[0, it, 1, "test"] for it in items]


def test_alternative_steps_by_what_you_got_this_run():
    from coach import step_numbers
    c = Coach(_branch_route(), learned_path=tmp_learned())
    assert step_numbers(c.steps) == ["1", "2", "2a", "3", "4", "4a", "4b", "5", "6"]
    c.next_step()
    assert c.step == 2                                            # nothing stolen: 2a
    c.next_step()
    c.next_step()
    assert c.step == 4                                            # Won A: the main step
    c.next_step()
    assert c.step == 7                                            # Buy Iron Sword: fewer than 2
    c2 = Coach(_branch_route(), learned_path=tmp_learned())
    _got(c2, "Broadsword", "Broadsword")
    c2.next_step()
    assert c2.step == 1
    c2.obtained = []                                              # decided at step 2 already
    c2.next_step()
    c2.next_step()
    c2.next_step()
    assert c2.step == 8


def test_getting_items_counts_steals_pickups_and_buys_not_unequipping_or_joining():
    from memory import GameState
    from screen import ITEMS
    B, W = ITEMS.index("Broadsword"), ITEMS.index("Wrist")
    def st(bag, equips, igt=1000):
        return GameState(enemy_hp=[], enemy_atb=[], party_atb=[], party_hp_slots=[], igt=igt,
                         encounters_possible=False, battle_sig=b"", items=bag, equips=equips)
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c._now = 100.0
    s0 = st({W: 1}, {"steiner": [B, 0, 0, 0, 0]})
    c._track_obtained(s0, st({W: 1, B: 1}, {"steiner": [B, 0, 0, 0, 0]}))      # stolen
    assert c._count("Broadsword") == 1
    c._track_obtained(st({W: 1, B: 1}, {"steiner": [B, 0, 0, 0, 0]}),
                      st({W: 1, B: 2}, {"steiner": [0, 0, 0, 0, 0]}))          # Steiner's taken off
    assert c._count("Broadsword") == 1
    c._track_obtained(s0, st({W: 1}, {"steiner": [B, 0, 0, 0, 0], "eiko": [B, 0, 0, 0, 0]}))  # joins
    assert c._count("Broadsword") == 1
    c._track_obtained(st({}, {}), st({W: 2}, {}))                               # bought / picked up
    assert c._count("Wrist") == 2
    c._dropped_to(500, 2000)                                                   # save from before them
    assert c._count("Broadsword") == 0


def test_shift_f5_switches_to_the_alternative():
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c.goto(4)
    c.switch_branch()
    assert c.step == 5                                            # 4a: Lost A
    c.switch_branch()
    assert c.step == 6                                            # 4b: Lost B, a third way
    c.next_step()
    assert c.step in (7, 8)
    c.goto(1)
    c.switch_branch()                                             # 2 -> 2a
    assert c.step == 2
    assert "2a/" in c._view(_branch_state({})).header
    c.new_run()
    assert c.branch_choice == {}


def test_three_ways_take_the_first_that_holds():
    import routes
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Main", "lines": ["x"]},
        {"disc": 1, "type": "move", "title": "None", "lines": ["x"], "alt": True, "when": "Broadsword < 1"},
        {"disc": 1, "type": "move", "title": "Two", "lines": ["x"], "alt": True, "when": "Broadsword >= 2"},
        {"disc": 1, "type": "move", "title": "End", "lines": ["x"]}]})
    for got, want in (([], 1), (["Broadsword"], 0), (["Broadsword", "Broadsword"], 2)):
        c = Coach(r, learned_path=tmp_learned())
        _got(c, *got)
        c.goto(3)
        c.skip(-1)
        assert c.step == want, (got, c.step)


def test_branch_choice_is_kept_in_progress():
    d = Path(tempfile.mkdtemp())
    c = Coach(_branch_route(), progress_path=d / "p.json", learned_path=tmp_learned())
    c.goto(5)
    c2 = Coach(_branch_route(), progress_path=d / "p.json", learned_path=tmp_learned())
    c2._load_progress()
    assert c2.branch_choice == c.branch_choice and c2.step == 5


def test_reaching_a_screen_of_the_alternative_takes_it():
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c.goto(3)
    c.learned.set_field(c.step_key(c.steps[5]), 500, "t")         # Lost A starts on screen 500
    g = Game(tmp_learned())
    g.coach = c
    g.coach._enc_flag_seen.add(True)
    g.screen(400, seconds=4)
    g.screen(500, seconds=4)
    assert c.step == 5


def test_conditions_with_every_comparison_share_answers():
    from coach import parse_when, route_branches
    assert parse_when("Broadsword => 2") == ("cmp", "Broadsword", ">=", 2)
    assert parse_when("fewer than 2 Broadsword") == ("cmp", "Broadsword", "<", 2)
    steps = [{"title": "a", "when": "Broadsword < 2"}, {"title": "b", "when": "Broadsword >= 2"},
             {"title": "c", "when": "Broadsword = 1"}]
    per = route_branches(steps)[0]
    assert per[0][0] == per[1][0] and per[0][1] != per[1][1] and per[2][0] != per[0][0]
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c.steps[7]["when"] = "Broadsword = 1"; c.steps = list(c.steps)
    _got(c, "Broadsword")
    assert c.active(7)
    _got(c, "Broadsword", "Broadsword")
    assert not c.active(7)


def test_sub_steps_are_numbered_and_go_the_way_of_their_step():
    from coach import step_numbers, route_branches
    steps = [{"title": "A"}, {"title": "Crash Site"}, {"title": "Baku", "inside": True},
             {"title": "Side room", "inside": True}, {"title": "Other", "alt": True, "when": "Broadsword < 2"},
             {"title": "Other side", "inside": True}, {"title": "B"}]
    assert step_numbers(steps) == ["1", "2", "2.1", "2.2", "2a", "2a.1", "3"]
    per = route_branches(steps)[0]
    assert per[2] == per[3] == per[1] and per[5] == per[4] and per[1] != per[4] and per[6] is None


def test_shift_f8_goes_through_a_sub_step_and_back_like_a_run():
    c = Coach(_inside_route(), learned_path=tmp_learned())
    c.goto(1)
    c.skip(1)
    assert c.step == 2                                         # Baku
    c.skip(1)
    assert c.step == 1                                         # back to Crash Site
    c.skip(1)
    assert c.step == 3                                         # Plant Brain
    c.skip(-1)
    assert c.step == 2


def test_save_loaded_undoes_branches_and_sub_steps_done_after_it():
    c = Coach(_inside_route(), learned_path=tmp_learned())
    c.goto(1)
    c.skip(1); c.skip(1)                                       # through Baku and back
    assert 2 in c.inside_done
    c._igt_last = 5000
    c._dropped_to(1000, 5000)
    assert not c.inside_done
    c.inside_done = {2}
    c.goto(0); c.goto(1)                                       # arriving at Crash Site again
    assert not c.inside_done
    c2 = Coach(_branch_route(), learned_path=tmp_learned())
    c2._igt_last = 4000
    c2.goto(2)
    assert c2.branch_choice
    c2._dropped_to(1000, 4000)
    assert not c2.branch_choice


def test_screens_cant_skip_a_fight_and_going_back_teaches_nothing():
    g = Game(tmp_learned())
    g.coach = Coach(_inside_route(), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    c = g.coach
    c.learned.set_field(c.step_key(c.steps[3]), 250, "t")    # Plant Brain starts on 250
    c.learned.set_field(c.step_key(c.steps[4]), 260, "t")
    c.goto(1)                                                # Crash Site, Baku not fought
    g.screen(206, seconds=4)
    g.screen(250, seconds=4)
    assert c.step == 1                                       # Baku first: no jump past it
    c.inside_done.add(2)
    g.screen(206, seconds=2); g.screen(250, seconds=4)
    assert c.step == 3                                       # Baku won: on to Plant Brain
    g.screen(260, seconds=4)
    assert c.step == 3                                       # Plant Brain not won: stays
    before = json.dumps(c.learned.data, sort_keys=True)
    c.prev_step()                                            # F7 back to Crash Site
    g.screen(300, seconds=5)
    assert json.dumps(c.learned.data, sort_keys=True) == before
    b = Coach(_branch_route(), learned_path=tmp_learned())
    b.goto(8)
    b.goto(2)                                                # back into a branch: not decided
    assert not b.branch_choice


def test_a_branch_can_check_two_items():
    import routes
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Main", "lines": ["x"]},
        {"disc": 1, "type": "move", "title": "Both", "lines": ["x"], "alt": True,
         "when": "Broadsword >= 1 and Leather Wrist >= 1"},
        {"disc": 1, "type": "move", "title": "End", "lines": ["x"]}]})
    for got, want in ((["Broadsword"], 0), (["Broadsword", "Leather Wrist"], 1), ([], 0)):
        c = Coach(r, learned_path=tmp_learned())
        _got(c, *got)
        c.goto(2)
        c.skip(-1)
        assert c.step == want, (got, c.step)
    from coach import when_label
    assert when_label("Broadsword >= 1 and Leather Wrist >= 1") == "Broadsword >= 1 and Leather Wrist >= 1"


def test_a_fight_further_on_needs_all_its_hp_and_no_randoms():
    import routes
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Walk", "lines": ["x"]},
        {"disc": 1, "type": "boss", "title": "Steiner", "lines": ["x"], "hp": [162]},
        {"disc": 1, "type": "boss", "title": "Bomb", "lines": ["x"], "hp": [38]},
        {"disc": 1, "type": "boss", "title": "Cage", "lines": ["x"], "hp": [533]},
        {"disc": 1, "type": "boss", "title": "Spiders", "lines": ["x"], "hp": [33]}]})
    c = Coach(r, learned_path=tmp_learned())
    c.battle_random_possible = False
    assert c._match_formation([33, 10169]) is None           # Steiner's fight with a 33 HP soldier
    assert c._match_formation([33]) is None                  # three fights on: too far by HP alone
    assert c._match_formation([10162]) == 1
    c.goto(2)
    assert c._match_formation([10533]) == 3                  # one fight on, every enemy fits
    c.battle_random_possible = True
    assert c._match_formation([10533]) is None               # but not where randoms happen


def test_level_lines_show_levels_and_tick_at_a_target():
    from memory import GameState
    from syntax import level_requests, classify
    assert level_requests("@Lv Zidane, Vivi 12") == [("zidane", None), ("vivi", 12)]
    assert level_requests("@Lv") == [] and level_requests("@Zidane: Flee") is None
    assert classify("@Lv Zidane").kind == "ap" and not classify("@Lv Zidane").problem
    assert classify("@Lv Zidaen").problem
    st = GameState(enemy_hp=[], enemy_atb=[], party_atb=[], party_hp_slots=[], igt=0,
                   encounters_possible=False, battle_sig=b"",
                   levels={"zidane": 15, "vivi": 9, "amarant": 3, "eiko": 1},
                   exp={"zidane": 900, "vivi": 300, "amarant": 40, "eiko": 0})
    lines, done, _ = Coach._ap_lines(["@Lv Zidane 15, Vivi 12", "  @Lv Blank", "@Lv"], st)
    assert lines[:3] == ["@Zidane Lv 15/15", "@Vivi Lv 9/12", "  @Blank Lv 3"] and done == [0]
    assert "@Eiko Lv 1" not in lines and "@Zidane Lv 15" in lines           # @Lv: everyone you have


def test_conditions_on_ap_and_steps_shown_only_if():
    import routes
    from memory import GameState
    from coach import parse_when, when_label
    assert parse_when("zidane: flee >= 20") == ("cmp", "Zidane: Flee", ">=", 20)
    assert parse_when("AP Zidane: Flee learned") == ("cmp", "Zidane: Flee", "learned", 0)   # older way
    assert when_label("Zidane: Flee learned and Broadsword < 1") == "Zidane: Flee learned and Broadsword < 1"
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "A", "lines": ["x"]},
        {"disc": 1, "type": "move", "title": "Teach Flee", "lines": ["x"], "when": "Zidane: Flee not learned"},
        {"disc": 1, "type": "move", "title": "Both", "lines": ["x"], "when": "Broadsword >= 1 and Zidane: Flee >= 20"},
        {"disc": 1, "type": "move", "title": "End", "lines": ["x"]}]})
    def st(ap):
        return GameState(enemy_hp=[], enemy_atb=[], party_atb=[], party_hp_slots=[], igt=0,
                         encounters_possible=False, battle_sig=b"", ap={"zidane:Flee": (ap, 150)})
    c = Coach(r, learned_path=tmp_learned())
    c.prev = st(40)
    assert c.active(1) and not c.active(2)                   # Flee not learned; no Broadsword
    _got(c, "Broadsword")
    assert c.active(2)
    c2 = Coach(r, learned_path=tmp_learned())
    c2.prev = st(150)
    assert not c2.active(1)                                  # learned: the step isn't shown
    c2.next_step()
    assert c2.step == 3


def test_a_step_calls_its_sub_step_where_it_comes():
    import routes, editor
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Crash Site", "lines": ["1x Ether", "[Baku]", "Steiner > Exit"]},
        {"disc": 1, "type": "boss", "title": "Baku", "lines": ["Attack"], "hp": [202], "inside": True},
        {"disc": 1, "type": "move", "title": "After", "lines": ["x"]}]})
    c = Coach(r, learned_path=tmp_learned())
    v = c._view(_branch_state({}))
    assert v.lines[1] == "\u21b3 Baku" and not any("On the way" in l for l in v.lines)
    c.inside_done.add(1)
    assert 1 in c._view(_branch_state({})).done
    r["steps"][0]["lines"][1] = "[Bakuu]"
    assert any("isn't one of its sub-steps" in m for _, _, m in editor.find_problems(r))


def test_a_called_sub_step_comes_up_when_the_lines_above_it_are_done():
    g, bag, put = bag_game({"Potion": 4})
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "Crash Site",
                        "lines": ["1x Ether", "[Side room]", "Talk to Vivi"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "Side room", "lines": ["1x Elixir"], "inside": True}
    g.coach.steps[i + 2] = {"disc": 1, "type": "move", "title": "After", "lines": ["Go"]}
    g.coach.steps = list(g.coach.steps)
    g.coach._entry_step = None
    g.read(2)
    g.now += 10
    assert g.coach.step == i                                  # the Ether first
    bag["Ether"] = 1; put()
    g.read(3)
    assert g.coach.step == i + 1, g.coach.events              # into the side room by itself
    g.now += 10
    bag["Elixir"] = 1; put()
    g.read(3)
    assert g.coach.step == i                                  # back to Crash Site
    v = g.read()
    assert i + 1 in g.coach.inside_done and 1 in v.done        # "[Side room]" ticked where it's called


def test_a_save_loaded_isnt_items_got_and_65535_in_a_random_isnt_a_boss():
    from memory import GameState
    from screen import ITEMS
    B = ITEMS.index("Broadsword")
    def st(bag, igt):
        return GameState(enemy_hp=[], enemy_atb=[], party_atb=[], party_hp_slots=[], igt=igt,
                         encounters_possible=False, battle_sig=b"", items=bag, equips={})
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c._now = 50.0
    c._track_obtained(st({}, 600), st({B: 1}, 900000))     # a later save loaded: not stolen
    assert c._count("Broadsword") == 0
    c._track_obtained(st({}, 900000), st({B: 1}, 900060))  # one second on: got it
    assert c._count("Broadsword") == 1
    import routes
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Trunk", "lines": ["x"]},
        {"disc": 1, "type": "boss", "title": "Antlion", "lines": ["x"], "hp": [3938]}]})
    c2 = Coach(r, learned_path=tmp_learned())
    c2.battle_random_possible = True
    assert c2._match_formation([65535]) is None


def test_a_step_with_kinds_of_fight_counts_each_kind():
    import routes
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Cleyra", "lines": ["x"]},
        {"disc": 1, "type": "boss", "title": "Cleyra Siege", "lines": ["Fight"],
         "groups": [{"name": "Soldiers", "hp": [523], "count": 2}, {"name": "Mages", "hp": [526], "count": 1}]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["x"]}]})
    assert r["bosses"]["cleyra_siege"]["fights"] == 3 and r["bosses"]["cleyra_siege"]["hp"] == [523, 526]
    g = Game(tmp_learned())
    g.coach = Coach(r, learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    g.coach.goto(1)
    g.screen(30, encounters=False)
    g.fight([526, 526], 20)                                   # the mages first
    assert g.coach.step == 1 and g.coach.groups_done == {1: 1}
    v = g.read()
    assert "\u2713 Mages 1/1" in v.stats and "Soldiers 0/2" in v.stats
    g.fight([523, 523], 20)
    g.fight([523], 20)
    assert g.coach.step == 2, g.coach.events                  # every kind done: on


def test_a_sub_step_can_have_its_own_condition():
    for got, want in (([], ["1x Ether", "\u21b3 Baku"]), (["Leather Wrist"], ["1x Ether", "\u21b3 Before Baku", "\u21b3 Baku"])):
        g, bag, put = bag_game({"Potion": 4})
        i = g.coach.step
        g.coach.steps[i] = {"disc": 1, "type": "move", "title": "Prima Vista",
                            "lines": ["1x Ether", "[Before Baku]", "[Baku]"]}
        g.coach.steps[i + 1] = {"disc": 1, "type": "menu", "title": "Before Baku", "lines": ["Zidane: Optimize"],
                                "inside": True, "when": "Leather Wrist >= 1"}
        g.coach.steps[i + 2] = {"disc": 1, "type": "boss", "title": "Baku", "lines": ["Attack"], "inside": True,
                                "boss": "baku"}
        g.coach.bosses["baku"] = {"name": "Baku", "turns": ["Attack"], "hp": [202]}
        g.coach.steps = list(g.coach.steps)
        _got(g.coach, *got)
        v = g.read()
        assert v.lines[:len(want)] == want, v.lines
        g.coach._entry_step = None
        g.read(2)
        g.now += 10
        bag["Ether"] = 1; put()
        g.read(3)
        assert g.coach.step == (i + 1 if got else i), (got, g.coach.events)


def test_editor_checks_alternatives():
    import editor
    r = _branch_route()
    assert not [m for _, _, m in editor.find_problems(r) if "lternative" in m or "when" in m.lower()]
    r["steps"][2]["when"] = "Broadswrd x < 2"
    r["steps"][0]["alt"] = True
    msgs = [m for _, _, m in editor.find_problems(r)]
    assert any("isn't an item" in m for m in msgs) and any("first step" in m for m in msgs)



def test_switching_route_keeps_panel_settings_and_resume_choice():
    d = Path(tempfile.mkdtemp())
    (d / "p.json").write_text(json.dumps({"step": 5}))
    c = Coach(_branch_route(), progress_path=d / "x.json", learned_path=tmp_learned(), resume=False)
    c.show_next, c.steal_off = True, "Steal check off: no OCR"
    c.switch_route(_branch_route(), d / "p.json")
    assert c.show_next and c.steal_off == "Steal check off: no OCR"
    assert c.step == 0                                  # resume_progress off: from the start
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c.switch_route(_branch_route(), d / "p.json")
    assert c.step == 5                                  # resume_progress on: where it was left


def test_route_edit_moves_numbered_state_with_its_steps():
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c.goto(5)                                           # on "Lost A": its branch group is "ways 4"
    assert c.branch_choice.get("ways 4") == "way 1"
    c.picked = {5: ["Tent"]}
    c.stolen = {8: ["Ether"]}
    c.inside_done = {4}
    new = _branch_route()
    new["steps"].insert(0, {"disc": 1, "type": "move", "title": "Added", "lines": ["New first step"]})
    new["steps"].pop()                                  # "End" (old 8) is gone
    c.reload_route(new)
    assert c.current()["title"] == "Lost A" and c.step == 6
    assert c.branch_choice.get("ways 5") == "way 1" and "ways 4" not in c.branch_choice
    assert c.picked == {6: ["Tent"]}                    # ticks stay on their step
    assert c.stolen == {}                               # its step was removed
    assert c.inside_done == {5}
    assert c.active(6) and not c.active(5)


def test_learning_doesnt_undo_a_forget_from_the_editor():
    import os
    from coach import Learned
    path = tmp_learned()
    coach_copy = Learned(path)
    coach_copy.set_formation("boss_a", [100])
    coach_copy.set_field("move:Walk:Go", 300, "R")
    editor_copy = Learned(path)
    editor_copy.forget_step("boss:boss_a", "R", ["boss_a"])
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))   # a later write, whatever the clock
    coach_copy.set_formation("boss_b", [200])          # the coach learns before it notices the forget
    data = json.loads(path.read_text())
    assert "boss_a" not in data["formations"] and data["formations"]["boss_b"] == [200]
    assert data["fields"]["R|move:Walk:Go"] == 300
    assert coach_copy.formation("boss_a") is None


def test_panel_says_when_steals_cant_be_checked():
    from syntax import steal_targets
    g = Game()
    i = next(k for k, st in enumerate(g.coach.steps) if st["type"] == "boss"
             and steal_targets(g.coach.bosses[st["boss"]].get("turns", [])))
    g.coach.goto(i)
    g.coach.steal_off = "Steal check off: no text recognition"
    assert ("Steal check off: no text recognition", "info") in g.read().badges
    g.coach.steal_off = ""
    assert not any("Steal check" in t for t, _ in g.read().badges)


def test_a_route_without_steps_or_disc_doesnt_crash():
    d = Path(tempfile.mkdtemp())
    (d / "Empty.json").write_text(json.dumps({"name": "Empty", "steps": []}), encoding="utf-8")
    try:
        load_route(d / "Empty.json")
        assert False, "an empty route should be refused"
    except ValueError as e:
        assert "no steps" in str(e)
    (d / "NoDisc.json").write_text(json.dumps({"steps": [{"type": "move", "title": "A", "lines": ["Go"]}]}),
                                   encoding="utf-8")
    g = Game()
    g.coach.reload_route(load_route(d / "NoDisc.json"))
    assert g.read().header.startswith("Disc 1")


def test_backups_of_a_route_with_a_similar_name_are_kept():
    import editor
    d = Path(tempfile.mkdtemp())
    backups = d / "backups"
    backups.mkdir()
    other = backups / "Muttski 2026 20261029-000000.json"      # route "Muttski 2026"
    other.write_text("{}")
    for k in range(editor.KEEP_BACKUPS + 3):
        (backups / f"Muttski 20261028-{k:06d}.json").write_text("{}")
    route = {"name": "Muttski", "steps": [{"disc": 1, "type": "move", "title": "A", "lines": ["Go"]}]}
    (d / "Muttski.json").write_text(json.dumps(route), encoding="utf-8")
    editor.save_route(route, d / "Muttski.json", backups)
    mine = [f for f in backups.iterdir() if f.name != other.name]
    assert other.exists() and len(mine) == editor.KEEP_BACKUPS

if __name__ == "__main__":
    import inspect
    import sys
    import traceback
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                fails += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{fails} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
