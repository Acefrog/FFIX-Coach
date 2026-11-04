"""Logic tests with fake RAM. Run: python test_coach.py  (or python -m pytest test_coach.py)"""

import json
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # the app folder
from coach import Coach, load_route
from memory import Addresses, FakeRam, read_state
from main import Simulator

import fights_data
BUILT_IN_STARTS, BUILT_IN_SEEN = fights_data.STARTS, fights_data.SEEN_HP
fights_data.STARTS, fights_data.SEEN_HP = {}, {}   # tests learn everything themselves (one test turns these on)

HERE = Path(__file__).resolve().parent
ROUTE = load_route(HERE / "test_route.json")   # a fixed copy, so editing your routes never breaks the tests
FIELD_CODE, BATTLE_CODE = b"\x11" * 64, b"\x22" * 64

import copy as _copy
# Fights are known by their HP and the screen they start from. The test route's fights all
# start from FIGHT_SCREEN (as if picked in the editor), and Game.fight() starts fights there.
FIGHT_SCREEN = 990


def with_fight_screens(route):
    r = _copy.deepcopy(route)
    for st in r["steps"]:
        if st["type"] == "boss":
            st.setdefault("screen", FIGHT_SCREEN)
    return r


ROUTE_S = with_fight_screens(ROUTE)
ROUTE_CHECKS = _copy.deepcopy(ROUTE_S)
ROUTE_CHECKS["bosses"]["gizamaluke"]["needs"] = ["quina_1hp"]
ROUTE_CHECKS["bosses"]["lani"]["needs"] = ["zidane_hp7"]


def step_of(boss):
    return next(i for i, s in enumerate(ROUTE["steps"]) if s.get("boss") == boss)


class Game:
    """A fake FFIX: walk between screens and fight battles, 10 reads a second."""

    FLAG = 0x1F0100

    def __init__(self, learned=None, field_id=True, code_check=True, flags=True, screens=True):
        self.ram, self.a = FakeRam(), Addresses()
        if flags and code_check:
            self.a.battle_flags = [(self.FLAG + i, 0x10, 0x20) for i in range(3)]
        if field_id:
            self.a.field_id = 0x1F0010
        if not code_check:
            self.a.battle_sig_len = 0
        self.coach = Coach(ROUTE_S if screens else ROUTE, learned_path=learned)
        self.fight_screen = FIGHT_SCREEN if screens else None   # where fights start from
        self.coach._enc_flag_seen.add(True)               # the random-battle flag works here
        self.now = 0.0
        self.tick_n = 0
        self.code(False)
        self.ram.write(self.a.encounter_field, 1, 1)
        self.ram.write(self.a.party_hp_addrs[0], 500)    # Zidane is alive

    def onto(self, screen="default"):
        """Onto the screen a fight starts from (the test route's, unless one is given)."""
        screen = self.fight_screen if screen == "default" else screen
        if screen is not None and self.a.field_id is not None and self.ram.value(self.a.field_id, 2) != screen:
            self.ram.write(self.a.field_id, screen)
            self.read()

    def code(self, battle, screen="default"):
        """Into or out of a fight. Into one: first onto the screen it starts from (the
        test route's fight screen, unless a screen is given; None: stay where you are)."""
        if battle:
            self.onto(screen)
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

    def fight(self, hps, seconds, end_hp=None, code_flip=True, act_every=10, screen=FIGHT_SCREEN):
        """Bars fill for `seconds`; enemies end at end_hp (default all 0). It starts from
        `screen` (None: the screen you're on)."""
        if code_flip:
            self.code(True, screen)
        else:
            self.onto(screen)
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
    assert v.highlight is None


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


def test_back_key_after_a_win_keeps_what_learn_taught():
    learned = tmp_learned()
    g = Game(learned, screens=False)
    target = step_of("dracozombie")
    g.coach.goto(target)
    g.screen(70, encounters=False)
    start_fight(g, [700], screen=None)    # not known: a random battle
    assert not g.coach.battle_is_boss
    g.coach.learn()                       # Learn during it: this is the fight
    assert g.coach.step == target and g.coach.battle_is_boss
    k = g.coach._lkey()
    assert g.coach.learned.formation(k) == [700] and g.coach.learned.battle_screens(k) == [70]
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(15)
    g.code(False)
    g.read(10)
    assert g.coach.step == target + 1     # won: on to the next step
    g.coach.prev_step()                   # F7 straight after: back to the fight
    assert g.coach.step == target
    assert g.coach.learned.formation(k) == [700] and g.coach.learned.battle_screens(k) == [70]  # (Forget in the editor)


def test_learn_key_in_a_fight_teaches_it_and_the_route_carries_on():
    learned = tmp_learned()
    g = Game(learned, screens=False)
    target = step_of("dracozombie")       # no HP typed for it
    g.coach.goto(target)
    g.screen(80)
    start_fight(g, [2179], screen=None)
    assert not g.coach.battle_is_boss     # not known yet
    g.coach.learn()                       # Learn: this is the fight
    assert g.coach.battle_is_boss and g.coach.step == target
    assert g.coach.learned.formation("dracozombie") == [2179]
    assert g.coach.learned.battle_screens("dracozombie") == [80]
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(15)
    assert g.coach.step == target + 1     # won: on as usual


def test_learn_in_a_fight_that_isnt_the_typed_hp_learns_no_hp():
    learned = tmp_learned()
    g = Game(learned, screens=False)
    gar = step_of("garland")              # typed HP 40728
    g.coach.goto(gar)
    g.screen(80)
    start_fight(g, [9999], screen=None)
    g.coach.learn()                       # Learn in the wrong fight
    assert g.coach.battle_is_boss         # goes along with it this time
    assert g.coach.learned.formation("garland") is None
    assert any("not learned" in e for e in g.coach.events)


def test_screens_learned_on_first_run_drive_the_second():
    learned = tmp_learned()
    g = Game(learned)
    first = step_of("black_waltz_1") + 1   # 'Dali: dodge the kids'
    g.coach.goto(first)
    g.screen(300)
    g.screen(301)
    g.coach.learn_next()                   # Shift+F6 on screen 301: the next step starts here
    g.screen(301, seconds=4)
    assert g.coach.learned.field(Coach.step_key(ROUTE["steps"][first + 1]), g.coach.rname) == 301

    g2 = Game(learned)
    g2.coach.goto(first)
    g2.screen(300)
    assert g2.coach.step == first
    g2.screen(301)
    assert g2.coach.step == first + 1


def test_nothing_is_learned_by_itself_only_with_the_learn_key():
    learned = tmp_learned()
    g = Game(learned)
    first = step_of("black_waltz_1") + 1
    g.coach.goto(first)
    g.screen(300, seconds=4)
    for _ in range(4):                     # F8 F8 F8 F8 while standing on one screen
        g.coach.next_step()
        g.screen(300, seconds=0.5)
    g.screen(300, seconds=4)
    key = lambda i: Coach.step_key(ROUTE["steps"][i])
    for i in range(first + 1, first + 5):
        assert g.coach.learned.field(key(i), g.coach.rname) is None, i      # by hand: nothing
    g.coach._goto(first + 5, manual=False)  # a step that comes up by itself learns nothing either
    g.screen(300, seconds=4)
    assert g.coach.learned.field(key(first + 5), g.coach.rname) is None
    g.coach._goto(first + 6, manual=False)
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


def start_fight(g, hps, screen=FIGHT_SCREEN):
    g.code(True, screen)
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
    g.onto()                           # the fight starts from its screen
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
    g.code(False)
    v = g.read(3)
    assert not any("HP" in t for t, _ in v.badges)  # gone once the fight ends


def test_route_can_ask_for_party_hp():
    import copy
    route = copy.deepcopy(ROUTE_S)
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
    route = _copy.deepcopy(ROUTE)
    for st in route["steps"]:
        if st["type"] == "boss":
            st["screen"] = Simulator.BOSS_SCREEN
    coach = Coach(route)
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


def test_each_steal_on_a_card_is_found():
    from coach import steal_targets
    found = {b: steal_targets(c["turns"]) for b, c in ROUTE["bosses"].items()}
    assert [it for _, it in found["masked_man"]] == ["Mage Masher"]
    assert [it for _, it in found["beatrix1"]] == ["Chain Plate"]
    assert [it for _, it in found["cage2"]] == ["Broadsword"]
    assert [it for _, it in found["black_waltz_3a"]] == [None]  # any of three items
    assert [it for _, it in found["taharka"]] == [None]         # optional: any item


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
    new = _copy.deepcopy(ROUTE_S)
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
    g = Game()
    wreck = next(k for k, st in enumerate(ROUTE["steps"]) if st.get("title") == "Prima Vista wreck")
    g.coach.goto(wreck - 1)                            # still on the fight before it
    g.screen(5)
    g.coach.pickup_event("Ether")                      # only the step on screen counts
    assert g.coach.step == wreck - 1
    g.coach.goto(wreck)
    g.read()
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
    import items
    g = Game()
    base = 0x1E1000
    g.a.chars = {n: {"exp": base + i * 0x90} for i, n in enumerate(
        ["zidane", "vivi", "garnet", "steiner", "freya", "quina", "eiko", "amarant"])}
    g.a.equip_off = 0x30
    g.a.ability_bits = {"Bird Killer": (0x40, 1), "Bug Killer": (0x40, 2)}
    g.a.menu_flags = [(0x1F0300 + i, 0, 1) for i in range(3)]
    def equip(who, slot, item):
        g.ram.write(g.a.chars[who]["exp"] + 0x30 + slot, items.ITEMS.index(item), 1)
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
    route = _copy.deepcopy(ROUTE_S)
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
    import finder
    snap = bytearray(0x400)
    snap[0x150:0x155] = bytes([2, 0x73, 0x5A, 0x99, 0xFF])
    assert finder.pattern_hits(bytes(snap), 0x100, 0x200, [2, 0x73, None, 0x99, None]) == [0x150]
    a, b = bytearray(16), bytearray(16)
    b[3] = 0b00100000
    b[7] = 0b11                                           # two bits at once (stones count): ignored
    assert finder.changed_bits(bytes(a), bytes(b), 0x1000) == [(0x1003, 5)]


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
    from items import ITEMS
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


def test_taking_gear_off_first_doesnt_tick_the_optimize_after_it():
    g, equip, ability, menu = menu_game()
    i = g.coach.step
    g.coach.steps[i] = {"disc": 3, "type": "menu", "title": "Terra",
                        "lines": ["Zidane: -Power Belt", "Zidane: Optimize", "Zidane: Gaia Gear"]}
    g.coach._entry_step = None
    equip("zidane", 4, "Power Belt")
    menu(True)
    v = g.read(2)
    g.ram.write(g.a.chars["zidane"]["exp"] + 0x30 + 4, 255, 1)     # the Power Belt off
    v = g.read(2)
    assert 0 in v.done and 1 not in v.done             # that isn't Optimize
    equip("zidane", 0, "Mythril Dagger")               # Optimize changes his gear after it
    v = g.read(2)
    assert 1 in v.done


def test_optimize_with_its_gear_written_under_it_ticks_on_that_gear():
    g, equip, ability, menu = menu_game()
    i = g.coach.step
    g.coach.steps[i] = {"disc": 3, "type": "menu", "title": "Memoria",
                        "lines": ["Zidane: -Power Belt", "Zidane: Optimize",
                                  "# Mythril Dagger / Bandana / Leather Wrist / Leather Shirt / -", "Zidane: Gaia Gear"]}
    g.coach._entry_step = None
    equip("zidane", 4, "Power Belt")
    equip("zidane", 2, "Leather Wrist")
    equip("zidane", 3, "Leather Shirt")
    menu(True)
    g.read(2)
    g.ram.write(g.a.chars["zidane"]["exp"] + 0x30 + 4, 255, 1)
    equip("zidane", 0, "Mythril Dagger")               # changed, but not all of it yet
    v = g.read(2)
    assert 1 not in v.done
    equip("zidane", 1, "Bandana")                      # now it's what the note says
    v = g.read(2)
    assert 1 in v.done


def test_a_sub_step_comes_up_on_its_start_screen_else_straight_away():
    for start in (None, 2925):
        g, equip, ability, menu = menu_game()
        g.coach = Coach(_copy.deepcopy(ROUTE), learned_path=tmp_learned())   # (its own copy of the route)
        i = g.coach.step
        g.coach.steps[i] = {"disc": 4, "type": "move", "title": "Crystal World",
                            "lines": ["[Menu A]", "Up, then hold Up", "Save point: menu", "[Menu B]"]}
        g.coach.steps[i + 1] = {"disc": 4, "type": "menu", "title": "Menu A", "lines": ["Zidane: Mythril Dagger"],
                                "inside": True}
        g.coach.steps[i + 2] = {"disc": 4, "type": "menu", "title": "Menu B", "lines": ["Zidane: Bandana"],
                                "inside": True}
        if start is not None:
            g.coach.steps[i + 2]["screen"] = start         # Starts on: the save point
        g.coach._entry_step = None
        g.screen(2919, seconds=1)
        assert g.coach.step == i + 1                       # no start screen: Menu A comes up at once
        menu(True); g.read(2)
        equip("zidane", 0, "Mythril Dagger"); g.read(2)
        menu(False); g.read(4)
        g.screen(2919, seconds=4)
        if start is None:
            assert g.coach.step == i + 2, g.coach.events   # no start screen: straight away
        else:
            assert g.coach.step == i, g.coach.events       # waits on Crystal World...
            g.screen(start, seconds=2)
            assert g.coach.step == i + 2, g.coach.events   # ...for the save point
        assert g.coach._start_screen(i + 1) is None        # coming up by itself taught nothing
        menu(False)                                        # (the fake game's memory is shared by the tests)


def test_optimize_with_an_item_after_it_needs_the_optimize_too():
    g, equip, ability, menu = menu_game()
    g.coach = Coach(_copy.deepcopy(ROUTE), learned_path=tmp_learned())   # (its own copy of the route)
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "menu", "title": "Plant Brain menu",
                        "lines": ["Equipment:", "  Zidane: Optimize, Wrist", "  Steiner: Optimize"]}
    g.coach._entry_step = None
    equip("zidane", 2, "Wrist")                        # he already wears a Wrist
    menu(True)
    v = g.read(3)
    assert 1 not in v.done                             # so the Wrist alone isn't it
    equip("zidane", 0, "Mythril Dagger")               # Optimize changes his gear
    v = g.read(2)
    assert 1 in v.done
    menu(False)                                        # (the fake game's memory is shared by the tests)


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
    g.coach.steal_event("stole", lines[0][1])          # only a steal seen in the bag counts
    assert not g.coach._steals_done()
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
    g.coach.obtained.append([t - 9000, "Broadsword", 1, "3.2 Prison Cage II"])   # stolen earlier this run
    g.coach.branch_choice["count Broadsword = 0"] = "no"
    g.coach.inside_done.add(b - 1)
    igt(g, 0)                                          # reset to the title screen
    g.read(10)
    igt(g, t - 5000)                                   # load a save from earlier
    g.read()
    for k in range(20):
        igt(g, t - 5000 + k * 6)
        g.read()
    assert g.coach.step == b and g.coach.resets == 0
    assert g.coach.encounters == []                    # that battle came after the save
    # what you got, the ways taken and the sub-steps done come back with your place
    assert g.coach._count("Broadsword") == 1, g.coach.obtained
    assert g.coach.branch_choice.get("count Broadsword = 0") == "no"
    assert b - 1 in g.coach.inside_done


def test_minus_items_are_taken_off_and_drawn_red():
    from coach import line_segments, menu_checks, old_off_line
    assert line_segments("Zidane: Leather Wrist, -Silk Shirt") == [("Zidane: Leather Wrist, ", False), ("Silk Shirt", True)]
    assert line_segments("Phoenix Down + Potion on Zidane") == [("Phoenix Down + Potion on Zidane", False)]
    assert menu_checks("Zidane: Leather Wrist, -Silk Shirt") == ("zidane", False, [("Leather Wrist", False), ("Silk Shirt", True)], [])
    assert old_off_line("Freya off: Cover, Level Up") == "Freya: -Cover, -Level Up"
    g, equip, ability, menu = menu_game()
    route = _copy.deepcopy(ROUTE_S)
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
    route = _copy.deepcopy(ROUTE_S)
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
    route = _copy.deepcopy(ROUTE_S)
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
    route = _copy.deepcopy(ROUTE_S)
    route["bosses"]["masked_man"]["turns"] = lines
    g = Game()
    g.coach = Coach(route)
    g.coach.goto(step_of("masked_man"))
    g.screen(5)
    start_fight(g, [10188])
    assert turn_groups(lines)[2] == (2, 5)                     # R3 and the two lines under it
    assert 3 in [i for i, _ in g.coach._steal_lines()]


def _stats(g, k, seed):
    """Fill enemy slot k's record (past HP and ATB) with that enemy kind's stats."""
    import random
    r = random.Random(seed)
    base = g.a.slot(4 + k, 0)
    for off in range(16, g.a.slot_stride):
        g.ram.write(base + off, r.randrange(1, 256), 1)


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
    route = _copy.deepcopy(ROUTE_S)
    route["bosses"]["steiner2"]["hp"] = [167]                   # Steiner's own HP (plus the hidden 10,000)
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
    route = _copy.deepcopy(ROUTE_S)
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


def _multi_game(boss, n, learned=None):
    route = _copy.deepcopy(ROUTE_S)
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


def test_fights_on_a_several_fight_step_can_be_taught_with_learn():
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
    g.coach.learn()                                    # Learn: "this is one of them"
    assert g.coach.battle_is_boss and g.coach.step == b and not g.coach.encounters
    g.ram.write(g.a.enemy_hp[0], 0)
    g.ram.write(g.a.enemy_hp[1], 0)
    g.read(3)
    g.code(False)
    g.read(20)
    assert g.coach.step == b and g.coach.sub == 1
    assert g.coach.learned.formation(g.coach._keys(b)[0]) == [300, 310]
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
    g.coach = Coach(with_fight_screens(_inside_route()), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    g.coach.goto(1)
    g.screen(30)
    v = g.read()
    assert not any("On the way" in l for l in v.lines)     # (it comes up when it starts)
    g.fight([202], 20)
    assert g.coach.step == 1 and 2 in g.coach.inside_done      # back on Crash Site
    v = g.read()
    assert not any("Baku done" in l for l in v.lines)
    g.fight([510], 20)                                         # Plant Brain: recognised from Crash Site
    assert g.coach.step == 4


def test_shift_f8_goes_past_every_sub_step_to_the_next_main_step():
    import main
    c = Coach(_inside_route(), learned_path=tmp_learned())
    c.goto(1)
    c.next_main_step()
    assert c.step == 3                                         # past Baku, to Plant Brain
    c.goto(2)
    c.next_main_step()
    assert c.step == 3                                         # from inside Crash Site too
    c.goto(4)
    c.next_main_step()
    assert c.step == 4                                         # the end: stays
    actions, _, _ = main.key_actions(c, {})
    assert actions["Shift+F8"] == c.next_main_step and actions["F8"] == c.next_step


def test_shift_f7_goes_back_past_sub_steps_to_a_main_step():
    import main
    c = Coach(_inside_route(), learned_path=tmp_learned())
    c.goto(3)
    c.prev_main_step()
    assert c.step == 1                                         # past Baku, back to Crash Site
    c.goto(2)
    c.prev_main_step()
    assert c.step == 1                                         # from Baku: the step it's in
    c.prev_main_step()
    assert c.step == 0
    c.prev_main_step()
    assert c.step == 0                                         # the start: stays
    actions, _, _ = main.key_actions(c, {})
    assert actions["Shift+F7"] == c.prev_main_step and actions["F7"] == c.prev_step


def test_f6_learns_where_the_step_youre_on_starts():
    import routes
    learned = tmp_learned()
    c = Coach(routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Dali", "lines": ["Walk", "[Dali Shop]"]},
        {"disc": 1, "type": "menu", "title": "Dali Shop", "lines": ["1x Wrist"], "inside": True},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
    g = Game(learned)
    g.coach = c
    c.goto(1)
    g.screen(354)                                      # in the shop
    c.learn()                                          # F6: Dali Shop starts here
    assert c.step == 1 and c._start_screen(1) == 354 and c._start_screen(2) is None
    c.learn_next()                                     # Shift+F6: the step after it starts here
    assert c._start_screen(2) == 354


def test_learn_on_a_sub_step_teaches_the_next_step_not_one_past_a_fight():
    import routes
    learned = tmp_learned()
    c = Coach(routes.expand({"name": "t", "steps": [
        {"disc": 4, "type": "move", "title": "Memoria", "lines": ["Walk", "[Menu A]", "[Maliris]", "[Menu B]"]},
        {"disc": 4, "type": "menu", "title": "Menu A", "lines": ["Zidane: Optimize"], "inside": True},
        {"disc": 4, "type": "boss", "title": "Maliris", "id": "m", "lines": ["Hit"], "hp": [59492], "inside": True},
        {"disc": 4, "type": "menu", "title": "Menu B", "lines": ["Freya: Optimize"], "inside": True},
        {"disc": 4, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
    g = Game(learned)
    g.coach = c
    c.goto(1)
    g.screen(2904)
    c.learn_next()                                     # the next step is the fight: not Menu B
    assert c.step == 1 and "fight" in c.message
    assert c._start_screen(3) is None


def test_starting_the_coach_mid_game_doesnt_teach_step_1():
    learned = tmp_learned()
    g = Game(learned)                                  # a fresh coach, on a save far into the game
    g.screen(2919, seconds=6)
    assert g.coach.step == 0 and g.coach._start_screen(0) is None


def test_f8_goes_back_to_the_step_for_its_notes_between_sub_steps():
    import routes
    c = Coach(routes.expand({"name": "t", "steps": [
        {"disc": 4, "type": "move", "title": "Memoria",
         "lines": ["[Menu A]", "[Maliris]", "Walk to the bridge", "1x Rune Claws", "[Tiamat menu]"]},
        {"disc": 4, "type": "menu", "title": "Menu A", "lines": ["Zidane: Optimize"], "inside": True},
        {"disc": 4, "type": "boss", "title": "Maliris", "id": "m", "lines": ["Hit"], "hp": [59492], "inside": True},
        {"disc": 4, "type": "menu", "title": "Tiamat menu", "lines": ["Freya: Optimize"], "inside": True},
        {"disc": 4, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=tmp_learned())
    path = [c.step]
    for _ in range(5):
        c.next_step()
        path.append(c.step)
    assert path == [0, 1, 2, 0, 3, 4], path            # Menu A > Maliris > back for the walk > Tiamat menu


def test_f8_goes_through_sub_steps_in_order_like_f7_back():
    for speedrun in (False, True):
        g = Game(tmp_learned())
        g.coach = Coach(_inside_route(), learned_path=tmp_learned())
        g.coach.speedrun = speedrun
        g.coach.goto(1)
        g.coach.next_step()
        assert g.coach.step == 2                               # into Baku, inside Crash Site
        v = g.read()
        assert v.title_note == "Crash Site" and "Crash Site" not in v.title
        g.coach.next_step()
        assert g.coach.step == 3 and 2 in g.coach.inside_done  # then Plant Brain
        g.coach.prev_step()
        g.coach.prev_step()
        assert g.coach.step == 1                               # F7 back: Baku, then Crash Site
        g.coach.goto(4)
        g.coach.next_step()
        assert g.coach.step == 4                               # the end: stays


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
        from coach import Learned
        L = Learned(tmp_learned())
        step = {"disc": 2, "type": "boss", "id": "x", "title": "New fight", "lines": ["Attack"], "fights": 3,
                "screen": 77}
        fights_data.apply(step, next(f for f in fights_data.FIGHTS if f["name"] == "Mistodons (Iifa Tree)"), L)
        assert step["title"] == "New fight" and step["hp"] == [1473] and step["fights"] == 3
        assert "screen" not in step                           # (not learned yet: the old one goes)
        L.add_battle_screen("fight:Mistodons (Alexandria)#2", 940)    # taught on another route
        festival = next(f for f in fights_data.FIGHTS if f["name"] == "Festival of the Hunt")
        L.set_formation("fight:Festival of the Hunt", [201])
        assert fights_data.label(festival) == "Festival of the Hunt"
        fights_data.apply(step, next(f for f in fights_data.FIGHTS if f["name"] == "Mistodons (Alexandria)"), L)
        assert step["preset"] == "Mistodons (Alexandria)" and step["screen"] == 940
        assert step["lines"] == ["Attack"] and step["fights"] == 3   # only HP and screen change
        fights_data.apply(step, festival, L)
        assert step["hp"] == [201] and "screen" not in step
        fights_data.save_custom({"name": "Ogre (Hilda Garde)", "disc": 3, "hp": [3300]})
        mine = fights_data.all_fights()[-1]
        assert mine["mine"] and fights_data.label(mine) == "Ogre (Hilda Garde)"
        import routes
        r = routes.expand({"steps": [step]})
        assert r["bosses"]["x"]["hp"] == [201] and r["bosses"]["x"]["learn_as"] == "fight:Festival of the Hunt"
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
        {"disc": 1, "type": "boss", "title": "Boss D", "id": "boss_d", "lines": ["Attack"], "hp": [4999]},
        {"disc": 1, "type": "move", "title": "Walk E", "lines": ["Up"]}]}
    path = tmp_learned()
    L = Learned(path)
    L.add_battle_screen("boss_d", 500)                       # (where it started when it was won)
    rep = report.step_report(route, L)
    assert not rep[0]["ok"] and "Shift+F6" in rep[0]["how"]   # nothing to check, nothing learned
    assert rep[1]["ok"] and "1 checked line" in rep[1]["how"]
    assert rep[2]["ok"] and "fight starts" in rep[2]["how"]  # the next step's fight has typed HP
    assert rep[3]["ok"] and "4999" in rep[3]["how"]
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
    g = Game(tmp_learned(), screens=False)
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
    g.coach.goto(step_of("black_waltz_2"))                # typed HP, but where it starts isn't known
    assert g.read().title.startswith(FLAG)
    g.coach.learned.add_battle_screen("fight:Black Waltz 2", 30)  # (as after winning it once)
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


def test_each_sub_step_finishes_by_itself_not_only_the_last():
    g, bag, put = bag_game({"Potion": 4})
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "Dali", "lines": ["Walk", "[Dali Shop]", "[Underground]"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "Dali Shop", "lines": ["1x Elixir"], "inside": True}
    g.coach.steps[i + 2] = {"disc": 1, "type": "move", "title": "Underground", "lines": ["1x Ether"], "inside": True}
    g.coach.steps[i + 3] = {"disc": 1, "type": "move", "title": "After", "lines": ["Go"]}
    g.coach._entry_step = None
    g.read(3)
    assert g.coach.step == i + 1                       # the first sub-step comes up
    g.now += 10
    bag["Elixir"] = 1; put()
    g.read(4)
    assert g.coach.step == i + 2, g.coach.events       # done by itself: on to the next one
    bag["Ether"] = 1; put()
    g.read(4)
    assert g.coach.step == i + 3, g.coach.events       # and that one too, then the next step


def test_fight_spoils_never_count_as_pickups():
    # Sealion's spoils include a Hi-Potion: that isn't Dali's "1x Hi-Potion", nor this
    # step's "any item"; a pickup once the spoils are in counts as usual.
    g, bag, put = bag_game({"Potion": 4})
    g.coach.steps = _copy.deepcopy(g.coach.steps)      # (its own copy of the route)
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "move", "title": "Ice Cavern",
                        "lines": ["1x Any item (hidden)", "Walk on"]}
    g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "Dali", "lines": ["1x Hi-Potion (Bottom)"]}
    g.coach._entry_step = None
    g.read(3)
    g.coach._battle_end_at = g.now                     # Sealion just won
    bag["Hi-Potion"] = 1; bag["Ether"] = 1; put()      # its spoils
    g.read(2)
    assert g.coach.step == i and not g.coach.picked.get(i) and not g.coach.picked.get(i + 1), g.coach.picked
    g.now += 10                                        # later: a hidden item
    bag["Echo Screen"] = 1; put()
    g.read(2)
    assert g.coach.picked.get(i) == ["Echo Screen"], g.coach.picked


def test_a_done_step_waits_for_the_next_steps_start_screen_if_it_has_one():
    for start in (None, 350):
        g, bag, put = bag_game({"Potion": 4})
        g.coach.steps = _copy.deepcopy(g.coach.steps)  # (its own copy of the route)
        i = g.coach.step
        g.coach.steps[i] = {"disc": 1, "type": "move", "title": "Ice Cavern", "lines": ["1x Tent (on the ledge)"]}
        g.coach.steps[i + 1] = {"disc": 1, "type": "move", "title": "Dali", "lines": ["Walk", "1x Potion"]}
        if start is not None:
            g.coach.steps[i + 1]["screen"] = start         # Starts on: Dali
        g.coach._entry_step = None
        g.screen(308, seconds=1)
        g.now += 10
        bag["Tent"] = 1; put()
        g.read(3)
        if start is None:
            assert g.coach.step == i + 1                   # no start screen: straight on
            continue
        assert g.coach.step == i                           # Ice Cavern done, but Dali waits...
        g.screen(start, seconds=2)
        assert g.coach.step == i + 1, g.coach.events       # ...for its own screen


def test_one_version_of_a_sub_step_done_means_they_all_are():
    import routes
    g, bag, put = bag_game({"Potion": 4})
    g.coach = Coach(routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Dali", "lines": ["Walk", "[Dali Shop]", "[Dali Underground]"]},
        {"disc": 1, "type": "menu", "title": "Dali Shop", "lines": ["1x Leather Wrist"], "inside": True},
        {"disc": 1, "type": "menu", "title": "Dali Shop", "lines": ["1x Wrist"], "inside": True,
         "when": "Leather Wrist = 1"},
        {"disc": 1, "type": "move", "title": "Dali Underground", "lines": ["1x Feather Hat"], "inside": True},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=tmp_learned())
    g.coach._entry_step = None
    g.read(3)
    assert g.coach.step == 1                           # the plain Dali Shop
    g.now += 10
    bag["Leather Wrist"] = 1; put()                    # buying it makes "Leather Wrist = 1" true...
    g.read(6)
    assert g.coach.step == 3, g.coach.events           # ...but that Dali Shop is done too: Underground next


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
    assert not any("Side room" in l for l in v.lines)      # (no line about it once it's done)
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
    from items import ITEMS
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
    from items import ITEMS
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
        c.step = c._step_by(c.step, -1)                # back one, on the way the run goes
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


def test_speedrun_mode_takes_the_steps_without_a_condition_whatever_you_got():
    from coach import step_numbers
    for got in ([], ["Broadsword", "Broadsword"]):
        c = Coach(_branch_route(), learned_path=tmp_learned())
        c.speedrun = True
        _got(c, *got)
        ways = [c.steps[i]["title"] for i in range(len(c.steps)) if c.active(i)]
        assert ways == ["Start", "Swap Blank", "Middle", "Won A", "End"], (got, ways)
        c.goto(1)
        c.next_step()
        assert c.steps[c.step]["title"] == "Middle"   # never the alternative


def test_speedrun_mode_drops_conditional_sub_steps_and_their_lines():
    import routes
    c = Coach(routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Dali", "lines": ["Walk", "[Dali Shop]", "[Baku Menu]"]},
        {"disc": 1, "type": "menu", "title": "Dali Shop", "lines": ["Buy 2x Potion"], "inside": True},
        {"disc": 1, "type": "menu", "title": "Baku Menu", "lines": ["Zidane: Leather Wrist"], "inside": True,
         "when": "Leather Wrist = 1"},
        {"disc": 1, "type": "move", "title": "End", "lines": ["Walk"]}]}), learned_path=tmp_learned())
    c.speedrun = True
    _got(c, "Leather Wrist")                           # got it, but the prime way doesn't use it
    assert [c.active(i) for i in range(4)] == [True, True, False, True]
    g = Game(tmp_learned())
    g.coach = c
    v = g.read()
    assert not any("Baku" in l for l in v.lines) and any("Dali Shop" in l for l in v.lines)


def test_speedrun_mode_shows_just_the_notes():
    g = Game(tmp_learned())
    g.coach.speedrun = True
    b = step_of("black_waltz_2")
    g.coach.goto(b)
    g.coach.steps[b]["lines"] = g.coach.bosses[g.coach.steps[b]["boss"]]["turns"] = ["Attack", "@Zidane: Flee"]
    start_fight(g, [1030])
    v = g.read()
    assert v.badges == [] and v.stats == [] and v.done == [] and v.steals == []
    assert v.lines == ["Attack"]                       # no AP readout
    assert "encounter" not in v.header and v.title == g.coach.bosses[g.coach.steps[b]["boss"]]["name"]
    g.coach.speedrun = False
    assert g.read().badges                             # practice mode: enemy HP and so on


def test_speedrun_mode_doesnt_take_a_branch_by_reaching_its_room():
    c = Coach(_branch_route(), learned_path=tmp_learned())
    c.speedrun = True
    c.goto(3)
    c.learned.set_field(c.step_key(c.steps[5]), 500, "t")         # Lost A starts on screen 500
    g = Game(tmp_learned())
    g.coach = c
    g.coach._enc_flag_seen.add(True)
    g.screen(400, seconds=4)
    g.screen(500, seconds=4)
    assert c.step != 5


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


def test_save_loaded_undoes_branches_and_sub_steps_done_after_it():
    c = Coach(_inside_route(), learned_path=tmp_learned())
    c.goto(1)
    c._igt_last = 5000
    c._sub_done(2)                                             # Baku done, at timer 5000
    assert 2 in c.inside_done
    c._dropped_to(1000, 5000)                                  # a save from before it
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
        c.step = c._step_by(c.step, -1)                # back one, on the way the run goes
        assert c.step == want, (got, c.step)
    from coach import when_label
    assert when_label("Broadsword >= 1 and Leather Wrist >= 1") == "Broadsword >= 1 and Leather Wrist >= 1"


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
    from items import ITEMS
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
    assert c2._match_hp_screen([65535]) is None


def test_a_step_with_kinds_of_fight_counts_each_kind():
    import routes
    r = routes.expand({"name": "t", "steps": [
        {"disc": 1, "type": "move", "title": "Cleyra", "lines": ["x"]},
        {"disc": 1, "type": "boss", "title": "Cleyra Siege", "lines": ["Fight"],
         "groups": [{"name": "Soldiers", "hp": [523], "count": 2}, {"name": "Mages", "hp": [526], "count": 1}]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["x"]}]})
    assert r["bosses"]["cleyra_siege"]["fights"] == 3 and r["bosses"]["cleyra_siege"]["hp"] == [523, 526]
    g = Game(tmp_learned())
    g.coach = Coach(with_fight_screens(r), learned_path=tmp_learned())
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


def _runner(ram=None):
    """A Runner on the fake game, with its stop file and logs in a temporary folder."""
    import main
    d = Path(tempfile.mkdtemp())
    g = Game()
    r = main.Runner(g.coach, ram or g.ram, g.a, {}, "Test", logs=lambda name: d / name,
                    stop_file=d / "stop_coach", config_path=d / "config.json", watch_files=False)
    r.hotkeys, r.overlay = _FakeKeys(), _FakePanel()
    return g, r, d


def _game_gone():
    """Game memory that can't be read (DuckStation closed), and no DuckStation to find again."""
    import main
    from memory import MemoryError_, RamSource

    class Gone(RamSource):
        def read(self, offset, size):
            raise MemoryError_("closed")

    def nowhere(*a, **k):
        raise MemoryError_("not running")
    return Gone(), nowhere


class _FakePanel:
    def __init__(self):
        self.toggles = 0

    def toggle(self):
        self.toggles += 1


class _FakeKeys:
    """Hotkeys pressed, handed out on the next poll like the real ones."""
    def __init__(self):
        self.pressed = []

    def poll(self):
        out, self.pressed = self.pressed, []
        return out


def test_runner_draws_the_panel_and_logs_key_presses():
    g, r, d = _runner()
    drawn = []
    assert r.tick(drawn.append) and len(drawn) == 1
    assert r.tick(drawn.append) and len(drawn) == 1        # nothing changed: not drawn again
    before = g.coach.step
    r.hotkeys.pressed = ["F8"]
    assert r.tick(drawn.append) and g.coach.step > before and len(drawn) == 2
    assert "key F8" in (d / "run_log.txt").read_text(encoding="utf-8")
    r.hotkeys.pressed = ["F10"]
    assert not r.tick(drawn.append) and r.stopped


def test_runner_stop_button_and_keys_work_while_the_game_is_gone():
    import main
    gone, nowhere = _game_gone()
    real = main.DuckStationRam
    main.DuckStationRam = nowhere
    try:
        g, r, d = _runner(gone)
        before = g.coach.step
        r.hotkeys.pressed = ["F9", "F8"]
        assert r.tick(lambda v: None)                        # still waiting for the game
        assert r.overlay.toggles == 1 and g.coach.step > before    # F9 and F8 weren't thrown away
        (d / "stop_coach").write_text("stop")
        assert not r.tick(lambda v: None) and r.stopped        # the launcher's Stop
        assert not (d / "stop_coach").exists()
        g, r, d = _runner(gone)
        r.hotkeys.pressed = ["F10"]
        assert not r.tick(lambda v: None) and r.stopped
    finally:
        main.DuckStationRam = real


def test_story_lines_after_a_pickup_keep_the_step_up():
    from syntax import instructions_after_checks
    assert instructions_after_checks(["1x Mythril Dagger (chest left of Tot's door)", "Tot > Ladders down"])
    assert not instructions_after_checks(["Tot > Ladders down", "1x Mythril Dagger (chest)"])


def test_capped_hp_read_while_the_fight_loads_still_counts_as_capped():
    g = Game()
    b = step_of("gizamaluke")
    g.coach.goto(b - 1 if ROUTE["steps"][b - 1]["type"] != "boss" else b)
    g.screen(5, encounters=False)
    g.code(True)
    g.ram.write(g.a.enemy_hp[0], 62707)               # read a moment before the cap (Zorn & Thorn)
    g.ram.write(g.a.enemy_atb[0], 100)
    g.read()
    g.ram.write(g.a.enemy_hp[0], 65535)               # then it sits on the cap
    for k in range(25):
        g.ram.write(g.a.enemy_atb[0], 140 + k * 40)
        v = g.read()
    assert g.coach.step == b and g.coach.battle_is_boss
    assert ("Enemy HP 3,175", "info") in v.badges, v.badges


def _kind_route():
    import routes
    return routes.expand({"name": "k", "steps": [
        {"disc": 2, "type": "move", "title": "Walk", "lines": ["Walk"]},
        {"disc": 2, "type": "boss", "title": "Bandersnatches", "id": "bander", "lines": ["Attack"],
         "groups": [{"name": "Bandersnatches", "hp": [899], "count": 2}]},
        {"disc": 2, "type": "move", "title": "After", "lines": ["Walk on"]}]})


def test_pickups_after_the_menu_finish_a_menu_step():
    import routes
    from items import ITEMS
    g, equip, ability, menu = menu_game()
    g.a.inventory = 0x1E0000
    def bag(items):
        for k in range(256):
            g.ram.write(g.a.inventory + 2 * k, 255, 1)
            g.ram.write(g.a.inventory + 2 * k + 1, 0, 1)
        for k, (name, n) in enumerate(items.items()):
            g.ram.write(g.a.inventory + 2 * k, ITEMS.index(name), 1)
            g.ram.write(g.a.inventory + 2 * k + 1, n, 1)
    bag({"Potion": 3})
    g.coach = Coach(routes.expand({"name": "m", "steps": [
        {"disc": 3, "type": "menu", "title": "Synth & pickups",
         "lines": ["Vivi: Silk Shirt", "1x Phoenix Pinion (wheelbarrow)"]},
        {"disc": 3, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=tmp_learned())
    g.read(3)
    menu(True); g.read()
    equip("vivi", 3, "Silk Shirt"); g.read()
    menu(False); g.read(3)
    assert g.coach.step == 0                            # menu done, the pickup still to come
    bag({"Potion": 3, "Phoenix Pinion": 1})
    g.read(3)
    assert g.coach.step == 1                            # picked up with the menu closed: on


def test_a_shop_sub_step_comes_up_on_its_screen():
    import routes
    from items import ITEMS
    g = Game(tmp_learned())
    g.a.inventory = 0x1E0000
    def bag(items):
        for k in range(256):
            g.ram.write(g.a.inventory + 2 * k, 255, 1)
            g.ram.write(g.a.inventory + 2 * k + 1, 0, 1)
        for k, (name, n) in enumerate(items.items()):
            g.ram.write(g.a.inventory + 2 * k, ITEMS.index(name), 1)
            g.ram.write(g.a.inventory + 2 * k + 1, n, 1)
    bag({"Ether": 1})
    g.coach = Coach(routes.expand({"name": "s", "steps": [
        {"disc": 2, "type": "move", "title": "Town", "lines": ["Up the street", "1x Tent (chest)", "[Shop]"]},
        {"disc": 2, "type": "menu", "title": "Shop", "lines": ["Buy 1 Potion"], "inside": True, "screen": 500},
        {"disc": 2, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=tmp_learned())
    g.screen(400, seconds=4)
    bag({"Ether": 1, "Tent": 1})
    g.screen(400, seconds=10)
    assert g.coach.step == 0                          # lines above done, but not in the shop yet
    g.coach.picked.clear()                            # (as if the Tent had been missed)
    g.screen(500, seconds=2)
    assert g.coach.step == 1                          # walked into the shop: its sub-step
    bag({"Ether": 1, "Tent": 1, "Potion": 1})
    g.read(3)
    assert g.coach.step == 0 and 1 in g.coach.inside_done     # bought: back to the town
    assert 2 in g.read().done                                  # its "[Shop]" line is ticked
    bag({"Ether": 1, "Tent": 2})                               # (the Tent picked up after all)
    g.read(3)
    assert g.coach.step == 2                                   # everything done: on


def _one_fight_route(screen=None):
    import routes
    boss = {"disc": 1, "type": "boss", "title": "Boss", "id": "boss", "lines": ["Attack"], "hp": [1030]}
    if screen is not None:
        boss["screen"] = screen                       # picked in the editor
    return routes.expand({"name": "f", "steps": [
        {"disc": 1, "type": "move", "title": "Walk", "lines": ["Walk"]}, boss,
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]})


def test_a_ready_made_fight_learned_on_one_route_is_known_on_every_route():
    import routes
    from coach import Learned
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(_one_fight_route(), learned_path=learned)
    g.screen(30)
    start_fight(g, [1030], screen=None)
    g.coach.learn()                                   # taught on the learning route
    other = routes.expand({"name": "other", "steps": [
        {"disc": 1, "type": "move", "title": "Go", "lines": ["Go"]},
        {"disc": 1, "type": "boss", "title": "BW2", "id": "bw2", "lines": ["Hit"], "preset": "Black Waltz 2"},
        {"disc": 1, "type": "boss", "title": "Mine", "id": "mine", "lines": ["Hit"], "hp": [4999]},
        {"disc": 1, "type": "boss", "title": "Cage A", "id": "cage_a", "lines": ["Hit"], "hp": [513]},
        {"disc": 1, "type": "boss", "title": "Cage B", "id": "cage_b", "lines": ["Hit"], "hp": [513]}]})
    keys = {i: c["learn_as"] for i, c in other["bosses"].items()}
    assert keys == {"bw2": "fight:Black Waltz 2", "mine": "mine", "cage_a": "cage_a", "cage_b": "cage_b"}
    c2 = Coach(other, learned_path=learned)
    assert c2._fight_known(1)                         # nothing to learn on this route
    assert not c2._fight_known(2)


def test_learn_passes_over_a_fight_you_skipped_by_its_hp():
    import routes
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(routes.expand({"name": "f", "steps": [
        {"disc": 1, "type": "boss", "title": "BW2", "id": "bw2", "lines": ["Hit"], "hp": [1030]},
        {"disc": 1, "type": "boss", "title": "BW3", "id": "bw3", "lines": ["Hit"], "hp": [1128]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
    g.screen(30)
    start_fight(g, [1128], screen=None)               # BW2 isn't in this run: BW3 comes first
    g.coach.learn()
    assert g.coach.step == 1 and g.coach.learned.formation("fight:Black Waltz 3") == [1128]
    assert g.coach.learned.formation("fight:Black Waltz 2") is None


def test_learn_teaches_the_fight_youre_on_when_two_have_the_same_hp():
    import routes
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(routes.expand({"name": "f", "steps": [
        {"disc": 3, "type": "boss", "title": "Amdusias", "id": "amd", "lines": ["Hit"], "preset": "Amdusias", "hp": [10926]},
        {"disc": 3, "type": "boss", "title": "Abadon", "id": "aba", "lines": ["Hit"], "preset": "Abadon", "hp": [10926]},
        {"disc": 3, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
    g.screen(2706)
    start_fight(g, [20926], screen=None)
    g.coach.learn()
    assert g.coach.step == 0 and g.coach.learned.battle_screens("fight:Amdusias") == [2706]
    assert g.coach.learned.battle_screens("fight:Abadon") == []


def test_hp_still_loading_after_a_save_load_is_taken_once_it_settles():
    import routes
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(routes.expand({"name": "f", "steps": [
        {"disc": 3, "type": "boss", "title": "Shell Dragon", "id": "sd", "lines": ["Hit"], "preset": "Shell Dragon",
         "hp": [10921]},
        {"disc": 3, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
    g.screen(2708)
    g.code(True, None)
    g.ram.write(g.a.enemy_hp[0], 257)                 # last fight's number for a moment
    for k in range(5):
        g.ram.write(g.a.enemy_atb[0], 100 + k * 50)
        g.read()
    g.ram.write(g.a.enemy_hp[0], 20921)               # then Shell Dragon's
    for k in range(15):
        g.ram.write(g.a.enemy_atb[0], 400 + k * 50)
        g.read()
    g.coach.learn()
    assert g.coach.step == 0 and g.coach.learned.formation("fight:Shell Dragon") == [20921]


def test_f8_and_f7_learn_and_forget_nothing():
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(_one_fight_route(), learned_path=learned)
    g.screen(30)
    start_fight(g, [1030], screen=None)               # not known yet
    g.coach.next_step()                               # F8: it's this fight
    assert g.coach.step == 1 and g.coach.battle_is_boss
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(15)
    g.code(False)
    g.read(10)
    assert g.coach.step == 2                          # won: on, but nothing learned
    k = "fight:Black Waltz 2"
    assert g.coach.learned.battle_screens(k) == [] and g.coach.learned.formation(k) is None
    g.coach.learned.add_battle_screen(k, 30)          # (as taught by Learn on a run)
    g.coach.goto(1)
    g.screen(30)
    g.fight([1030], 20, screen=None)                  # recognised and won
    assert g.coach.step == 2
    g.coach.prev_step()                               # F7 straight after: undoes the win only
    assert g.coach.step == 1 and g.coach.learned.battle_screens(k) == [30]


def test_learn_in_steiner_1_stays_on_steiner_1():
    import routes
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(routes.expand({"name": "f", "steps": [
        {"disc": 1, "type": "boss", "title": "Steiner 1", "id": "s1", "lines": ["Hit"], "preset": "Steiner 1", "hp": [169]},
        {"disc": 1, "type": "boss", "title": "Plant Spiders", "id": "ps", "lines": ["Hit"], "preset": "Plant Spiders",
         "hp": [33]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
    g.screen(57)
    g.code(True, None)
    for slot, h in enumerate([65526, 33, 29, 40]):    # Steiner's slot still loading
        g.ram.write(g.a.enemy_hp[slot], h)
    for k in range(5):
        for slot in range(4):
            g.ram.write(g.a.enemy_atb[slot], 100 + k * 50)
        g.read()
    g.ram.write(g.a.enemy_hp[0], 10169)               # then his real HP
    for k in range(15):
        for slot in range(4):
            g.ram.write(g.a.enemy_atb[slot], 400 + k * 50)
        g.read()
    g.coach.learn()
    assert g.coach.step == 0 and g.coach.learned.battle_screens("fight:Steiner 1") == [57]
    assert 10169 in g.coach.learned.formation("fight:Steiner 1")
    assert g.coach.learned.battle_screens("fight:Plant Spiders") == []


def test_plant_spiders_are_known_with_two_to_four_spiders():
    import routes
    for n in (2, 3, 4):
        learned = tmp_learned()
        from coach import Learned
        L = Learned(learned)
        L.set_formation("fight:Plant Spiders", [33, 33])  # learned from a fight of two
        L.add_battle_screen("fight:Plant Spiders", 259)
        g = Game(learned)
        g.coach = Coach(routes.expand({"name": "f", "steps": [
            {"disc": 1, "type": "move", "title": "Walk", "lines": ["Walk"]},
            {"disc": 1, "type": "boss", "title": "Plant Spiders", "id": "ps", "lines": ["Hit"], "preset": "Plant Spiders",
             "hp": [33]},
            {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
        g.screen(259)
        start_fight(g, [33] * n, screen=None)
        assert g.coach.battle_is_boss and g.coach.step == 1, n


def test_a_room_read_before_a_save_load_isnt_where_the_fight_started():
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(_one_fight_route(), learned_path=learned)
    g.screen(2928)                                    # Hill of Despair...
    g.coach.loaded_at = g.now + 0.05                  # ...then a save loaded straight into a fight
    start_fight(g, [1030], screen=None)
    assert g.coach.battle_field is None
    g.coach.learn()
    k = "fight:Black Waltz 2"
    assert g.coach.learned.formation(k) == [1030] and g.coach.learned.battle_screens(k) == []
    assert "Learn in it again" in g.coach.message


def test_a_boss_hit_before_its_hp_is_read_is_known_where_no_random_battles_happen():
    import routes
    for randoms, known in ((False, True), (True, False)):
        learned = tmp_learned()
        from coach import Learned
        Learned(learned).add_battle_screen("fight:Bandersnatch (first)", 1225)
        g = Game(learned)
        g.coach = Coach(routes.expand({"name": "f", "steps": [
            {"disc": 2, "type": "move", "title": "Walk", "lines": ["Walk"]},
            {"disc": 2, "type": "boss", "title": "Bandersnatch", "id": "b", "lines": ["Hit"],
             "preset": "Bandersnatch (first)", "hp": [899]},
            {"disc": 2, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
        g.coach._enc_flag_seen.add(True)
        g.screen(1225, encounters=randoms)
        start_fight(g, [539], screen=None)            # already hit when it was first read
        assert g.coach.battle_is_boss == known, randoms


def _mistodon_route(**boss):
    import routes
    b = {"disc": 3, "type": "boss", "title": "Mistodons", "id": "m", "lines": ["Hit"], "hp": [1473],
         "screen": FIGHT_SCREEN}
    b.update(boss)
    return routes.expand({"name": "f", "steps": [
        {"disc": 3, "type": "move", "title": "Walk", "lines": ["Walk"]}, b,
        {"disc": 3, "type": "move", "title": "After", "lines": ["Walk on"], "screen": 777},
        {"disc": 3, "type": "move", "title": "Later", "lines": ["Walk more"]}]})


def test_a_step_with_any_number_of_fights_ends_on_the_next_steps_room():
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(_mistodon_route(fights=2, any_count=True), learned_path=learned)
    g.coach._enc_flag_seen.add(True)
    for n in range(3):                                # more than the 2 it says: still that step
        g.fight([1473], 20)
        assert g.coach.step == 1 and g.coach.sub == n + 1, n
    assert not g.coach.encounters
    assert "fight 4" in g.read().title
    g.screen(777)                                     # the next step's room: it's over
    assert g.coach.step == 2


def test_one_more_fight_than_a_step_says_isnt_a_random_battle():
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(_mistodon_route(fights=2), learned_path=learned)
    g.coach._enc_flag_seen.add(True)
    g.fight([1473], 20)
    g.fight([1473], 20)
    assert g.coach.step == 2                          # its 2 done: on to the next step
    g.fight([1473], 20)                               # but this route has a third
    assert g.coach.step == 2 and not g.coach.encounters
    assert any("one more than it says" in e for e in g.coach.events)
    g.fight([100], 20)                                # something else: a random battle
    assert len(g.coach.encounters) == 1


def test_rooms_learned_per_fight_in_order_become_one_list():
    from coach import Learned
    path = tmp_learned()
    import json as _j
    path.write_text(_j.dumps({"formation_version": __import__("tuning").FORMATION_VERSION,
                              "battle_screens": {"fight:M": [1], "fight:M#2": [2, 1], "fight:M#3": [3]},
                              "formations": {"fight:Z": [5, 5], "fight:Z#2": [9]}, "fields": {}}))
    L = Learned(path)
    assert L.battle_screens("fight:M") == [1, 2, 3] and L.battle_screens("fight:M#2") == []
    assert L.formations("fight:Z") == [[5, 5], [9]]
    L.add_formation("fight:Z", [9])
    L.add_formation("fight:Z", [7])
    assert L.formations("fight:Z") == [[5, 5], [9], [7]]


def test_a_new_route_knows_the_fight_lists_fights_with_nothing_learned():
    import routes
    fights_data.STARTS, fights_data.SEEN_HP = BUILT_IN_STARTS, BUILT_IN_SEEN
    try:
        learned = tmp_learned()                       # a fresh install: nothing learned
        g = Game(learned)
        g.coach = Coach(routes.expand({"name": "new", "steps": [
            {"disc": 1, "type": "move", "title": "Walk", "lines": ["Walk"]},
            {"disc": 1, "type": "boss", "title": "My BW2", "id": "x", "lines": ["Hit"], "preset": "Black Waltz 2",
             "hp": [1030]},
            {"disc": 2, "type": "boss", "title": "Zombies", "id": "z", "lines": ["Hit"],
             "preset": "Zombies & Dracozombie (Iifa elevator)", "fights": 2},
            {"disc": 1, "type": "boss", "title": "My own", "id": "mine", "lines": ["Hit"], "hp": [4999]},
            {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
        assert g.coach._fight_known(1) and g.coach._fight_known(2)
        assert not g.coach._fight_known(3)            # a fight of your own: the Learn key, once
        g.screen(451)                                 # Dali/Field, where Black Waltz 2 starts
        start_fight(g, [11030], screen=None)
        assert g.coach.battle_is_boss and g.coach.step == 1
        L = g.coach.learned
        assert L.battle_screens("fight:Black Waltz 2") == [451] and not L.data.get("battle_screens")
    finally:
        fights_data.STARTS, fights_data.SEEN_HP = {}, {}


def test_deleting_a_route_keeps_no_copy():
    import routes, tempfile
    tmp = Path(tempfile.mkdtemp())
    old = routes.ROUTES, routes.PROGRESS
    routes.ROUTES, routes.PROGRESS = tmp / "routes", tmp / "progress"
    try:
        routes.ROUTES.mkdir(); routes.PROGRESS.mkdir()
        name = routes.create("Gone")
        routes.progress_path(name).write_text("{}")
        routes.delete(name)
        assert not routes.path(name).exists() and not routes.progress_path(name).exists()
        assert sorted(p.name for p in tmp.iterdir()) == ["progress", "routes"]   # no copy anywhere
    finally:
        routes.ROUTES, routes.PROGRESS = old


def test_fights_are_known_by_their_hp_and_the_screen_they_start_from():
    learned = tmp_learned()
    g = Game(learned)
    g.coach = Coach(_one_fight_route(), learned_path=learned)
    g.screen(30)
    start_fight(g, [1030], screen=None)               # its HP, but where it starts isn't known yet
    assert not g.coach.battle_is_boss
    g.coach.learn()                                   # Learn during it teaches it
    assert g.coach.step == 1 and g.coach.battle_is_boss
    k = "fight:Black Waltz 2"                         # (HP 1030: learned for every route)
    assert g.coach.learned.formation(k) == [1030] and g.coach.learned.battle_screens(k) == [30]
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(15)
    g.code(False)
    g.read(10)
    assert g.coach.step == 2

    g2 = Game(learned)                                # next run
    g2.coach = Coach(_one_fight_route(), learned_path=learned)
    g2.screen(44)
    g2.fight([1030], 20, screen=None)                 # its HP, from another screen: a random battle
    assert g2.coach.step == 0
    g2.screen(30)
    g2.fight([1030], 20, screen=None)                 # its HP, from its screen: it
    assert g2.coach.step == 2
    assert any("by HP and screen" in e for e in g2.coach.events), g2.coach.events

    g3 = Game(tmp_learned())                          # a screen picked in the editor: known at once
    g3.coach = Coach(_one_fight_route(screen=50), learned_path=tmp_learned())
    g3.screen(50)
    g3.fight([1030], 20, screen=None)
    assert g3.coach.step == 2


def test_a_step_with_several_fights_takes_them_from_new_screens_too():
    g = Game(tmp_learned())
    g.coach = Coach(with_fight_screens(_kind_route()), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    g.screen(400, seconds=4, encounters=False)
    g.fight([899, 899], 15)                            # from the step's screen
    assert g.coach.groups_done.get(0) == 1
    g.fight([899], 15, screen=401)                     # part-way through: the next from somewhere else
    assert g.coach.current()["title"] == "After", g.coach.events
    assert g.coach.learned.battle_screens("bander@0") == []    # winning teaches nothing


def test_a_step_finishes_on_its_finish_screen():
    import routes
    g = Game(tmp_learned())
    g.coach = Coach(routes.expand({"name": "e", "steps": [
        {"disc": 2, "type": "move", "title": "Town", "lines": ["1x Tent (chest)", "[Shop]", "Leave"],
         "end_screen": 610},
        {"disc": 2, "type": "menu", "title": "Shop", "lines": ["Buy 1 Potion"], "inside": True, "end_screen": 601},
        {"disc": 2, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=tmp_learned())
    g.screen(600, seconds=4)
    g.coach._goto(1, manual=False)                    # in the shop
    g.read(5)
    g.screen(601, seconds=2)                          # walked out: the sub-step is done
    assert g.coach.step == 0 and 1 in g.coach.inside_done
    g.screen(605, seconds=2)
    assert g.coach.step == 0                          # the Tent still to do: stays
    g.screen(610, seconds=2)                          # its finish screen: done anyway
    assert g.coach.step == 2


def test_learn_in_a_shop_teaches_the_shop_sub_step_and_it_comes_up():
    import routes
    g = Game(tmp_learned())
    g.coach = Coach(routes.expand({"name": "l", "steps": [
        {"disc": 2, "type": "move", "title": "Town", "lines": ["1x Tent (chest)", "[Shop]", "Leave"]},
        {"disc": 2, "type": "menu", "title": "Shop", "lines": ["Buy 1 Potion"], "inside": True},
        {"disc": 2, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=tmp_learned())
    g.screen(400, seconds=4)
    g.screen(500, seconds=2)                          # in the shop (Tent not found yet): screen not known
    assert g.coach.step == 0
    g.coach.learn_next()
    g.read(5)
    assert g.coach.step == 1                          # learned, and the shop came up by itself
    assert g.coach.learned.field(Coach.step_key(g.coach.steps[1]), g.coach.rname) == 500


def routes_expand(route):
    import copy
    import routes
    return routes.expand(copy.deepcopy(route))


def test_a_menu_starting_in_a_fight_room_waits_for_the_fight():
    # "[Deathguise]" then "[After menu]", the menu starting in the fight's room: walking
    # in for the fight mustn't open the menu first.
    import route_test
    route = {"name": "t", "steps": [
        {"disc": 4, "type": "move", "title": "Crystal", "screen": 700,
         "lines": ["Walk in", "[Deathguise]", "[After menu]", "Walk on"]},
        {"disc": 4, "type": "boss", "title": "Deathguise", "inside": True, "screen": 701, "hp": [55000]},
        {"disc": 4, "type": "menu", "title": "After menu", "inside": True, "screen": 701,
         "lines": ["Zidane: Optimize"]},
        {"disc": 4, "type": "move", "title": "End", "screen": 702, "lines": ["The end"]}]}
    p = route_test.Play(routes_expand(route))
    menu_at = []                                      # what was won when the menu came up
    real = p.c._goto

    def goto(i, manual, why=""):
        if i == 2:
            menu_at.append(set(p.c.inside_done))
        real(i, manual, why)
    p.c._goto = goto
    found = p.run()
    assert found == [], found
    assert menu_at and all(1 in done for done in menu_at), menu_at


def test_every_shipped_route_plays_to_its_end():
    import json
    import route_test
    import routes
    for name in routes.names():
        raw = json.loads(routes.path(name).read_text(encoding="utf-8-sig"))
        p = route_test.Play(routes_expand(raw))
        found = p.run()
        assert p.trace[-1] == "(end of the route)", (name, p.trace[-3:])
        # On a new install the only problems allowed are start screens nobody has taught
        assert all("isn't known" in why for _, why in found), (name, found)


def test_random_states_and_keys_never_raise():
    import json
    import random
    import route_test
    import routes
    from items import ITEMS
    for name in routes.names():
        rnd = random.Random(7)
        raw = json.loads(routes.path(name).read_text(encoding="utf-8-sig"))
        p = route_test.Play(routes_expand(raw))
        c, g = p.c, p.g
        screens = sorted({s["screen"] for s in c.steps if s.get("screen") is not None} | {50, 300, 9001})
        keys = [c.learn, c.learn_next, c.next_step, c.prev_step, c.next_main_step, c.prev_main_step]
        fight = False
        for _ in range(1500):
            r = rnd.random()
            if r < 0.1:
                g.ram.write(g.a.field_id, rnd.choice(screens))
            elif r < 0.15:
                fight = not fight
                g.code(fight)
                for s in range(4):
                    g.ram.write(g.a.enemy_hp[s], rnd.choice([0, 33, 500, 10916]) if fight else 0)
            elif r < 0.22:
                it = rnd.choice(ITEMS)
                p.bag[it] = max(0, p.bag.get(it, 0) + rnd.choice([-1, 1, 2]))
                p.write_all()
            elif r < 0.26:
                p.equips[rnd.choice(route_test.CHARS)][rnd.randrange(5)] = rnd.choice([255, rnd.randrange(236)])
                p.write_all()
            elif r < 0.3:
                for i in range(3):
                    g.ram.mem[route_test.MENU + i] = rnd.choice([0, 1])
            elif r < 0.36:
                rnd.choice(keys)()
            elif r < 0.37:
                c.reload_route(routes_expand(raw))
            g.read(rnd.randint(1, 4))


def test_a_version_whose_condition_holds_replaces_the_plain_one():
    # "Plant Brain menu" and "Plant Brain menu" when Broadsword = 0: with no Broadsword
    # got this run, the plain one steps aside and the = 0 one comes up; with one, the plain.
    from coach import Coach
    route = {"name": "v", "steps": [
        {"disc": 1, "type": "move", "title": "Forest", "lines": ["[Fight]", "[Menu]", "Walk on"]},
        {"disc": 1, "type": "boss", "title": "Fight", "inside": True, "hp": [916], "screen": 257},
        {"disc": 1, "type": "menu", "title": "Menu", "inside": True, "lines": ["Zidane: Optimize"]},
        {"disc": 1, "type": "menu", "title": "Menu", "inside": True, "when": "Broadsword = 0",
         "lines": ["Steiner: Optimize"]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk"]}]}
    c = Coach(routes_expand(route), learned_path=tmp_learned())
    assert not c.active(2) and c.active(3)
    c._goto(1, manual=True)
    c.next_step()
    assert c.step == 3, c.step                        # F8 from the fight: the = 0 version
    c = Coach(routes_expand(route), learned_path=tmp_learned())
    c.obtained.append((1, "Broadsword", 1, "1.1 Fight"))
    assert c.active(2) and not c.active(3)
    c._goto(1, manual=True)
    c.next_step()
    assert c.step == 2, c.step


def test_two_steps_starting_on_one_screen_move_on_when_you_come_back_to_it():
    # Evil Forest I and II both start at the Crash Site: out into the forest and back to
    # the ship for Evil Forest I, then out at the Crash Site again is Evil Forest II.
    from coach import Coach
    route = {"name": "s", "steps": [
        {"disc": 1, "type": "move", "title": "Forest I", "screen": 206,
         "lines": ["1x Ether (chest)", "", "Vivi > Exit"]},
        {"disc": 1, "type": "move", "title": "Forest II", "screen": 206, "lines": ["Run on"]},
        {"disc": 1, "type": "move", "title": "Cavern", "screen": 300, "lines": ["Walk"]}]}
    g = Game(tmp_learned())
    g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
    g.screen(206, seconds=3)
    assert g.coach.step == 0                          # first time here: Forest I, not past it
    g.screen(250, seconds=2)
    g.coach.pickup_event("Ether", source="screen")
    g.read(5)
    assert g.coach.step == 0                          # done: waits for Forest II's screen
    g.screen(251, seconds=2)
    g.screen(206, seconds=3)                          # back at the Crash Site
    assert g.coach.step == 1, g.coach.step


def test_a_screen_doesnt_take_a_branch_the_bag_says_no_to_or_one_already_passed():
    # Baku Menu (when Leather Wrist = 1) starts in the Meeting Room. With no Leather Wrist
    # got this run, walking in there (before or after Baku) mustn't bring it up.
    from coach import Coach
    route = {"name": "b", "steps": [
        {"disc": 1, "type": "move", "title": "Forest", "screen": 206,
         "lines": ["[Baku Menu]", "[Baku]", "", "Vivi > Exit"]},
        {"disc": 1, "type": "menu", "title": "Baku Menu", "inside": True, "when": "Leather Wrist = 1",
         "screen": 203, "lines": ["Zidane: Optimize"]},
        {"disc": 1, "type": "boss", "title": "Baku", "inside": True, "hp": [202], "screen": 202},
        {"disc": 1, "type": "move", "title": "Forest II", "screen": 250, "lines": ["Run"]}]}
    for baku_won in (False, True):
        g = Game(tmp_learned())
        g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
        g.screen(206, seconds=3)
        assert g.coach.step == 0
        if baku_won:
            g.coach.inside_done.add(2)
        g.screen(205, seconds=2)
        g.screen(203, seconds=3)                      # the Meeting Room
        assert g.coach.step == 0, (baku_won, g.coach.step)
        assert "count Leather Wrist = 1" not in g.coach.branch_choice


def test_optimize_with_an_item_isnt_ticked_before_the_gear_is_read_for_the_step():
    # Straight out of the Plant Brain fight the menu step comes up before the coach has
    # taken everyone's gear for it: Zidane's Wrist alone mustn't tick (and stay ticked).
    from memory import read_state
    g, equip, ability, menu = menu_game()
    g.coach = Coach(_copy.deepcopy(ROUTE), learned_path=tmp_learned())
    i = g.coach.step
    g.coach.steps[i] = {"disc": 1, "type": "menu", "title": "Plant Brain menu",
                        "lines": ["Equipment:", "  Zidane: Optimize, Wrist", "  Steiner: Optimize"]}
    g.coach._entry_step = None
    equip("zidane", 2, "Wrist")                        # he already wears a Wrist
    g.coach.equip_step = None                          # gear not taken for this step yet
    done = g.coach._menu_progress(read_state(g.ram, g.a))[0]
    assert 1 not in done and 1 not in g.coach.shop_ticked
    menu(True)
    v = g.read(3)
    assert 1 not in v.done
    equip("zidane", 0, "Mythril Dagger")               # Optimize changes his gear
    v = g.read(2)
    assert 1 in v.done
    menu(False)                                        # (the fake game's memory is shared by the tests)


def test_a_boss_from_another_screen_is_known_where_no_random_battles_happen():
    # Black Waltz 3 is set to start on 501 but starts from the Bridge (503), where there
    # are no random battles: its HP there means it's that fight. Where randoms can happen,
    # the same HP from another screen is still a random battle.
    import routes
    for randoms, known in ((False, True), (True, False)):
        learned = tmp_learned()
        g = Game(learned)
        g.coach = Coach(routes.expand({"name": "w", "steps": [
            {"disc": 1, "type": "move", "title": "Ship", "lines": ["Walk"]},
            {"disc": 1, "type": "boss", "title": "Black Waltz 3", "id": "bw3", "lines": ["Attack"],
             "hp": [1128], "screen": 501},
            {"disc": 1, "type": "move", "title": "After", "lines": ["Walk on"]}]}), learned_path=learned)
        g.coach._enc_flag_seen.add(True)
        g.screen(503, encounters=randoms)
        start_fight(g, [11128, 32896], screen=None)
        assert g.coach.battle_is_boss == known, randoms
        assert (g.coach.step == 1) == known, (randoms, g.coach.step)
        assert g.coach.learned.battle_screens("fight:bw3") == [] or not known   # nothing learned by itself


def test_a_sell_line_shows_tabs_as_one_space_and_ticks_when_all_are_sold():
    # "Sell: All Ethers,<tab>Phoenix Pinions,<tab>Eye Drops": on the panel the tabs are one
    # space, nothing is added to the line, and it ticks once all three are sold.
    g, bag, put = bag_game({"Ether": 5, "Phoenix Pinion": 1, "Eye Drops": 2, "Potion": 8})
    g.coach.steps[g.coach.step] = {"disc": 2, "type": "menu", "title": "Lindblum Weapon Shop",
                                   "lines": ["Sell:", "  All Ethers,	Phoenix Pinions,	Eye Drops",
                                             "", "Buy:", "  2x Mage Masher"]}
    v = g.read(2)
    assert v.lines[1] == "  All Ethers, Phoenix Pinions, Eye Drops", repr(v.lines[1])
    del bag["Ether"]; del bag["Phoenix Pinion"]; put()
    v = g.read(2)
    assert 1 not in v.done and v.lines[1] == "  All Ethers, Phoenix Pinions, Eye Drops", repr(v.lines[1])
    del bag["Eye Drops"]; put()
    v = g.read(2)
    assert 1 in v.done

def test_a_total_buy_counts_what_you_already_own():
    # "6x Steepled Hats (Total)" with one stolen from Black Waltz 3: buying 5 is enough.
    from syntax import shop_check
    assert shop_check("Buy 6x Steepled Hats (Total)") == ("buy", [("Steepled Hat", 6, "total")])
    assert shop_check("Buy 6x Steepled Hats") == ("buy", [("Steepled Hat", 6, "add")])
    g, bag, put = bag_game({"Steepled Hat": 1, "Potion": 8})
    g.coach.steps[g.coach.step] = {"disc": 1, "type": "menu", "title": "Lindblum Weapon Shop",
                                   "lines": ["Buy:", "  6x Steepled Hats (Total)", "  1x Headgear"]}
    g.read(2)
    bag["Steepled Hat"] = 5; put()                     # four bought: five in all
    assert 1 not in g.read(2).done
    bag["Steepled Hat"] = 6; put()                     # five bought: six
    assert 1 in g.read(2).done


def test_the_last_sub_step_of_a_route_goes_back_to_its_step():
    # A shop sub-step at the very end of the route: done, it goes back to its step.
    from coach import Coach
    g, bag, put = bag_game({"Cotton Robe": 3, "Potion": 2})
    g.coach = Coach(routes_expand({"name": "e", "steps": [
        {"disc": 1, "type": "move", "title": "Lindblum", "lines": ["Exit", "[Gate shop]"]},
        {"disc": 1, "type": "move", "title": "Gate shop", "inside": True,
         "lines": ["Sell:", "  All Cotton Robes", "Buy:", "  21x Potions"]}]}), learned_path=tmp_learned())
    g.read(5)
    assert g.coach.step == 1, g.coach.step
    del bag["Cotton Robe"]; bag["Potion"] = 23; put()
    g.read(8)
    assert g.coach.step == 0 and 1 in g.coach.inside_done, (g.coach.step, g.coach.inside_done)


def test_the_world_map_is_one_place_a_step_can_start_or_finish_on():
    # Every world map screen (9000-9099) is "World map" (9000): picked as Finishes on, out
    # onto it ends the step; as Starts on, reaching it brings the step up. Higher numbers,
    # read before the game is loaded, are still no place.
    from coach import Coach
    from field_names import WORLD_MAP, field_name, place
    assert place(9004) == WORLD_MAP and place(9099) == WORLD_MAP and place(602) == 602
    assert place(9500) is None and place(0xFFFF) is None and place(0) is None
    assert field_name(WORLD_MAP) == "World map (9000)"
    route = {"name": "wm", "steps": [
        {"disc": 1, "type": "move", "title": "Lindblum", "screen": 602, "end_screen": WORLD_MAP,
         "lines": ["Exit > Chocobo Forest"]},
        {"disc": 1, "type": "move", "title": "Chocobo Forest", "screen": 700, "lines": ["Find Mene"]},
        {"disc": 1, "type": "move", "title": "Out again", "screen": WORLD_MAP, "lines": ["Run to the Grotto"]},
        {"disc": 1, "type": "move", "title": "Grotto", "screen": 800, "lines": ["Walk"]}]}
    g = Game(tmp_learned())
    g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
    g.screen(602, seconds=3)
    g.screen(9500, seconds=3)                         # (no place: nothing happens)
    assert g.coach.step == 0
    g.screen(9004, seconds=3)                         # out onto the world map: Lindblum finishes
    assert g.coach.step == 1, g.coach.step
    assert any("on World map (9000)" in e for e in g.coach.events)
    g.screen(700, seconds=3)
    g.screen(9007, seconds=3)                         # another world map screen: "Out again" starts there
    assert g.coach.step == 2, g.coach.step


def test_a_sub_step_left_with_a_key_doesnt_open_again_until_you_move_on():
    # Mountain/Base starts on the screen you're standing on: F7 out of it to Dali must
    # stay on Dali (it opened again at once before, three times in a row); walking to
    # another screen and back opens it again.
    from coach import Coach
    route = {"name": "h", "steps": [
        {"disc": 1, "type": "move", "title": "Dali", "screen": 400,
         "lines": ["Walk", "[Mountain/Base]", "", "Up the mountain"]},
        {"disc": 1, "type": "menu", "title": "Mountain/Base", "inside": True, "screen": 403,
         "lines": ["Zidane: Optimize"]},
        {"disc": 1, "type": "move", "title": "Lindblum", "screen": 550, "lines": ["Walk"]}]}
    g = Game(tmp_learned())
    g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
    g.screen(400, seconds=3)
    g.screen(403, seconds=3)
    assert g.coach.step == 1                          # on its screen: it comes up
    g.coach.prev_step()                               # F7: not yet
    g.read(30)
    assert g.coach.step == 0, g.coach.step            # stays on Dali while you're still here
    g.screen(402, seconds=2)
    g.screen(403, seconds=3)                          # left and came back: it comes up again
    assert g.coach.step == 1, g.coach.step


def test_f8_writes_the_line_it_skipped_in_the_run_log():
    from coach import Coach
    route = {"name": "u", "steps": [
        {"disc": 1, "type": "move", "title": "Grotto", "lines": ["[Entrance menu]", "Walk"]},
        {"disc": 1, "type": "menu", "title": "Entrance menu", "inside": True,
         "lines": ["Equipment:", "  Zidane: Coral Ring", "Front row Freya"]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk"]}]}
    g, equip, ability, menu = menu_game()
    g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
    g.coach.goto(1)
    g.read(3)
    g.coach.next_step()
    assert any(e.endswith('F8 on 1.1 Entrance menu: "Zidane: Coral Ring" not ticked') for e in g.coach.events), \
        g.coach.events[-3:]
    import report
    logged = "04:10:41 " + next(e for e in g.coach.events if "not ticked" in e)   # (as in the run log)
    out = report.run_summary(routes_expand(route), [logged])
    assert out.get(1) == ['F8 pressed with "Zidane: Coral Ring" not ticked'], out
    menu(False)


def test_gear_lines_on_a_walk_step_stay_ticked_but_a_menu_step_keeps_checking():
    # Burmecia: "Zidane: The Ogre", later "Zidane: Mage Masher, Leather Hat". On a walk
    # step The Ogre stays ticked once the Mage Masher replaces it; on a menu step it doesn't.
    for kind, stays in (("move", True), ("menu", False)):
        g, equip, ability, menu = menu_game()
        g.coach = Coach(_copy.deepcopy(ROUTE), learned_path=tmp_learned())
        i = g.coach.step
        g.coach.steps[i] = {"disc": 1, "type": kind, "title": "Burmecia",
                            "lines": ["Zidane: The Ogre", "Walk on", "Zidane: Mage Masher, Leather Hat", "Walk"]}
        g.coach._entry_step = None
        equip("zidane", 0, "Dagger")
        equip("zidane", 1, "Feather Hat")
        menu(True)
        v = g.read(3)
        assert 0 not in v.done
        equip("zidane", 0, "The Ogre")
        v = g.read(3)
        assert 0 in v.done, kind
        equip("zidane", 0, "Mage Masher")
        equip("zidane", 1, "Leather Hat")
        v = g.read(3)
        assert 2 in v.done, kind
        assert (0 in v.done) == stays, (kind, v.done)
        menu(False)                                    # (the fake game's memory is shared by the tests)


def test_reaching_the_next_steps_screen_moves_on_with_lines_left_but_not_past_a_fight():
    # Gizamaluke's Grotto with "1x Holy Bell" never ticked (a savestate skipped it): once
    # Gizamaluke is won, reaching S. Gate/Bohden Gate (800), where Burmecia starts, moves
    # on. Before Gizamaluke is won, the same screen doesn't.
    from coach import Coach
    route = {"name": "g", "steps": [
        {"disc": 1, "type": "move", "title": "Gizamaluke's Grotto", "screen": 701,
         "lines": ["1x Gizamaluke Bell", "1x Holy Bell", "[Gizamaluke]"]},
        {"disc": 1, "type": "boss", "title": "Gizamaluke", "inside": True, "hp": [3000], "screen": 707},
        {"disc": 1, "type": "move", "title": "Burmecia", "screen": 800, "lines": ["1x Multina Racket"]}]}
    for won in (False, True):
        g = Game(tmp_learned())
        g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
        g.screen(701, seconds=3)
        g.coach.pickup_event("Gizamaluke Bell", source="screen")
        g.read(3)
        if won:
            g.coach.inside_done.add(1)
        g.screen(707, seconds=2)
        g.screen(800, seconds=3)                      # the trigger: Burmecia's start screen
        assert g.coach.step == (2 if won else 0), (won, g.coach.step)


def _grotto_route():
    return routes_expand({"name": "gz", "steps": [
        {"disc": 1, "type": "move", "title": "Grotto", "screen": 701,
         "lines": ["[Black Mages]", "[After menu]", "1x Holy Bell", "[Gizamaluke]"]},
        {"disc": 1, "type": "boss", "title": "Black Mages", "inside": True, "hp": [398], "screen": 703},
        {"disc": 1, "type": "menu", "title": "After menu", "inside": True, "lines": ["Zidane: Optimize"]},
        {"disc": 1, "type": "boss", "title": "Gizamaluke", "inside": True, "hp": [3000], "screen": 707},
        {"disc": 1, "type": "move", "title": "Burmecia", "screen": 800, "lines": ["Walk"]}]})


def test_a_save_loaded_undoes_only_the_sub_steps_done_after_it():
    # A savestate from just before Gizamaluke: Black Mages and the menu after it were done
    # before it, so they stay done; one done after the save's moment comes undone.
    from coach import Coach
    c = Coach(_grotto_route(), learned_path=tmp_learned())
    c._igt_last = 7000
    c._sub_done(1)                                    # Black Mages, before the save
    c._igt_last = 7300
    c._sub_done(2)                                    # the menu after it, before the save
    c._igt_last = 8400
    c._sub_done(3)                                    # Gizamaluke, after the save
    c._dropped_to(7707, 8412)                         # the savestate: back to 7707
    assert c.inside_done == {1, 2}, c.inside_done


def test_a_boss_too_big_to_read_is_the_fight_whose_room_its_in():
    # After the load, Black Mages counted as still to do; Gizamaluke (65535: too big to
    # read) in its own room, where no random battles happen, is Gizamaluke, not Black Mages.
    from coach import Coach
    g = Game(tmp_learned())
    g.coach = Coach(_grotto_route(), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    g.screen(701, seconds=3)
    g.screen(707, encounters=False)
    start_fight(g, [65535], screen=None)
    assert g.coach.battle_is_boss and g.coach.step == 3, g.coach.step


def test_winning_a_fight_sub_step_puts_the_ones_before_it_behind_you():
    # F8 through the Grotto's sub-steps, then a savestate from before them all: Black Mages
    # and the menus count as not done again. Winning Gizamaluke puts them behind you, so
    # reaching Burmecia's start screen (800) moves on.
    from coach import Coach
    g = Game(tmp_learned())
    g.coach = Coach(_grotto_route(), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    g.screen(701, seconds=3)
    assert not g.coach.inside_done                    # (as after the load)
    g.screen(707, encounters=False)
    g.fight([65535], 20, screen=None)                 # Gizamaluke won
    assert {1, 2, 3} <= g.coach.inside_done, g.coach.inside_done
    assert g.coach.step == 0
    g.screen(800, seconds=3)
    assert g.coach.step == 4, g.coach.step


def test_a_gear_line_on_a_walk_step_waits_for_the_menu_called_above_it():
    # Burmecia: Zidane already wears The Ogre when the step comes up, but "[World Map menu]"
    # above "Zidane: The Ogre" swaps him to a Dagger first. The line ticks only once that
    # menu is done and The Ogre is back on.
    g, equip, ability, menu = menu_game()
    g.coach = Coach(routes_expand({"name": "b", "steps": [
        {"disc": 1, "type": "move", "title": "Burmecia",
         "lines": ["1x Multina Racket", "[World Map menu]", "Zidane: The Ogre", "Walk on"]},
        {"disc": 1, "type": "menu", "title": "World Map menu", "inside": True, "lines": ["Zidane: Dagger"]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk"]}]}), learned_path=tmp_learned())
    equip("zidane", 0, "The Ogre")
    v = g.read(3)
    assert g.coach.step == 0 and 2 not in v.done, v.done   # worn already: not it yet
    g.coach.inside_done.add(1)                         # the World Map menu done...
    equip("zidane", 0, "Dagger")                       # ...with the Dagger on
    v = g.read(3)
    assert 2 not in v.done
    equip("zidane", 0, "The Ogre")
    v = g.read(3)
    assert 2 in v.done, v.done
    equip("zidane", 0, "Mage Masher")                  # later on the step: stays ticked
    assert 2 in g.read(3).done
    menu(False)


def _key_ram(block_hex="", table=True):
    # A fake US game: the key item name table after the item names, and the key item block
    # just past the bag holding these bytes.
    from memory import FakeRam
    from items import KEY_ITEM_ORDER, ff9_bytes
    ram = FakeRam()
    names = b"".join(ff9_bytes(n) + b"\xff" for n in KEY_ITEM_ORDER + ["none", "none"])
    if table:
        ram.mem[0x06ADAB:0x06ADAB + 1 + len(names)] = b"\xff" + names
    blk = bytes.fromhex(block_hex) if block_hex else b""
    ram.mem[0x08B9C4:0x08B9C4 + len(blk)] = blk
    ram.snapshot = lambda: bytes(ram.mem)
    return ram


DISC3_KEY_BLOCK = "441515000100000001000000000000000000"     # your disc 3 save, read from the game
WORLD_MAP_KEY_BLOCK = "011051000000000001000000000000000000" # the world map save


def test_key_items_are_read_from_the_block_past_the_bag():
    # Both of your saves: what the Key Items screen listed is what's read.
    from memory import Addresses, read_state
    a = Addresses.from_config({"addresses": {"inventory": "0x08B7C4"}})
    assert a.key_items_at == 0x08B9C4 and len(a.key_item_ids) == 61
    st = read_state(_key_ram(DISC3_KEY_BLOCK), a)
    assert st.key_items == {"Silver Pendant", "Memory Earring", "Falcon Claw", "Desert Star", "Ticket",
                            "Continental Map", "World Map", "Moogle's Flute", "Gate Pass", "Master Hunter"}, st.key_items
    st = read_state(_key_ram(WORLD_MAP_KEY_BLOCK), a)
    assert st.key_items == {"Silver Pendant", "Ticket", "Continental Map", "Moogle's Flute", "Kupo Nut",
                            "Master Hunter"}, st.key_items
    other = Addresses.from_config({"addresses": {"inventory": "0x08B000"}})    # another release:
    assert other.key_items_at == 0x08B200 and other.key_item_ids == {}       # not built in


def test_memory_setup_reads_every_key_item_from_the_game():
    import finder
    from items import KEY_ITEM_IDS, KEY_ITEM_ORDER, read_key_item_order
    ram = _key_ram(WORLD_MAP_KEY_BLOCK)
    assert read_key_item_order(ram.snapshot()) == KEY_ITEM_ORDER + ["none", "none"]
    said = []
    real = finder.__dict__.get("print")
    finder.print = lambda *x, **k: said.append(" ".join(map(str, x)))
    try:
        found = finder.find_key_items(ram, {"addresses": {"inventory": "0x08B7C4"}})
    finally:
        finder.__dict__.pop("print", None) if real is None else setattr(finder, "print", real)
    assert found == {"key_items_at": "0x08B9C4", "key_item_ids": KEY_ITEM_IDS}, found
    assert any("all 61 key items" in s for s in said) and any("Kupo Nut" in s for s in said), said


def test_without_the_name_table_the_key_items_screen_teaches_them():
    import finder
    ram = _key_ram("00000000000000000000000000000000", table=False)
    for bit in (24, 28, 90):                           # Gizamaluke Bell, Holy Bell, Blue Stone
        ram.mem[0x08B9C4 + (bit >> 3)] |= 1 << (bit & 7)
    answers = iter(["Blue Ston, Gizamaluke Bell",      # one missing: told so
                    "Gizamaluke Bell, Holy Bell, Blue Stone"])
    said = []
    real = {k: finder.__dict__.get(k) for k in ("input", "print")}
    finder.input, finder.print = (lambda prompt="": next(answers)), (lambda *x, **k: said.append(" ".join(map(str, x))))
    try:
        found = finder.find_key_items(ram, {"addresses": {"inventory": "0x08B7C4"}})
    finally:
        for k, v in real.items():
            finder.__dict__.pop(k, None) if v is None else setattr(finder, k, v)
    assert found == {"key_items_at": "0x08B9C4",
                     "key_item_ids": {"Gizamaluke Bell": [24], "Holy Bell": [28], "Blue Stone": [90]}}, found
    assert any("has 3 key items, you typed 2" in s for s in said), said


def test_key_items_read_from_memory_tick_their_pickup_lines():
    # "1x Holy Bell" ticks from memory: no "Received" message needed (a savestate past it,
    # or a missed read), and stays ticked once the bell is used on its door.
    from memory import read_state
    from items import KEY_ITEM_IDS
    g = Game(tmp_learned())
    g.a.key_items_at, g.a.key_item_ids = 0x0D1000, {k: list(v) for k, v in KEY_ITEM_IDS.items()}
    g.coach = Coach(routes_expand({"name": "k", "steps": [
        {"disc": 1, "type": "move", "title": "Grotto", "lines": ["1x Gizamaluke Bell", "1x Holy Bell", "Walk on"]},
        {"disc": 1, "type": "move", "title": "Burmecia", "lines": ["Walk"]}]}), learned_path=tmp_learned())
    st = read_state(g.ram, g.a)
    assert st.key_items == set() and {"Gizamaluke Bell", "Holy Bell"} <= st.key_known
    assert 1 not in g.read(3).done
    n = KEY_ITEM_IDS["Holy Bell"][0]
    g.ram.mem[0x0D1000 + (n >> 3)] |= 1 << (n & 7)      # you have it now
    v = g.read(3)
    assert 1 in v.done and 0 not in v.done, v.done
    g.ram.mem[0x0D1000 + (n >> 3)] = 0                    # used on its door: stays ticked
    assert 1 in g.read(3).done
    m = KEY_ITEM_IDS["Gizamaluke Bell"][1]                # the second Gizamaluke Bell entry counts too
    g.ram.mem[0x0D1000 + (m >> 3)] |= 1 << (m & 7)
    assert 0 in g.read(3).done
    g.a.key_items_at, g.a.key_item_ids = None, {}         # (in case the fake game's addresses are shared)


def test_changing_starts_on_to_the_room_you_are_in_moves_on_without_walking_again():
    # Dali set to start on 351, but you woke up in the inn's other room (352): nothing. Change
    # Dali's Starts on to 352 in the editor while standing there: on to Dali, no walking.
    import copy
    from coach import Coach
    route = {"name": "d", "steps": [
        {"disc": 1, "type": "move", "title": "Ice Cavern", "screen": 301,
         "lines": ["1x Tent (On the ledge)", "", "Stutter step to Dali"]},
        {"disc": 1, "type": "move", "title": "Dali", "screen": 351, "lines": ["Walk"]},
        {"disc": 1, "type": "move", "title": "After", "screen": 400, "lines": ["Walk"]}]}
    g = Game(tmp_learned())
    g.coach = Coach(routes_expand(route), learned_path=tmp_learned())
    g.screen(301, seconds=3)
    g.screen(9000, seconds=2)
    g.screen(352, seconds=3)
    assert g.coach.step == 0                          # 352 isn't Dali's 351
    edited = copy.deepcopy(route)
    edited["steps"][1]["screen"] = 352
    g.coach.reload_route(routes_expand(edited))       # saved in the editor, still in the inn
    g.read(20)
    assert g.coach.step == 1, g.coach.step


def test_the_editor_knows_which_way_the_run_in_progress_goes():
    # From the coach's progress file: no Broadsword got this run, so the "Broadsword = 0"
    # Plant Brain menu is this run's, the plain one isn't; with one stolen, the other way round.
    import json as _j
    import report
    route = {"name": "w", "steps": [
        {"disc": 1, "type": "move", "title": "Forest", "lines": ["[Menu]", "Walk"]},
        {"disc": 1, "type": "menu", "title": "Menu", "inside": True, "lines": ["Zidane: Optimize"]},
        {"disc": 1, "type": "menu", "title": "Menu", "inside": True, "when": "Broadsword = 0",
         "lines": ["Steiner: Optimize"]},
        {"disc": 1, "type": "move", "title": "After", "lines": ["Walk"]}]}
    prog = Path(tempfile.mkdtemp()) / "w.json"
    assert report.run_way(route, prog) == {}           # no run: nothing to show
    prog.write_text(_j.dumps({"step": 0, "branches": {}, "obtained": []}))
    assert report.run_way(route, prog) == {1: False, 2: True}
    prog.write_text(_j.dumps({"step": 0, "branches": {}, "obtained": [[1, "Broadsword", 1, "1 Forest"]]}))
    assert report.run_way(route, prog) == {1: True, 2: False}


def test_a_save_loaded_in_the_middle_of_a_fight_isnt_a_win():
    # In the Gizamaluke fight a savestate from before it is loaded: the fight goes away, but
    # it wasn't won. Fighting it again is recognised as Gizamaluke, and that win counts.
    from coach import Coach
    g = Game(tmp_learned())
    g.coach = Coach(_grotto_route(), learned_path=tmp_learned())
    g.coach._enc_flag_seen.add(True)
    t = 60 * 60 * 2
    for _ in range(40):                                # the game timer seen counting
        t += 6
        igt(g, t)
        g.read()
    g.screen(701, seconds=3)
    g.screen(707, encounters=False)
    start_fight(g, [65535], screen=None)
    assert g.coach.step == 3 and g.coach.battle_is_boss
    igt(g, t - 2000)                                   # the savestate: from before the fight
    g.code(False)
    g.ram.write(g.a.enemy_hp[0], 0)
    g.read(15)
    assert 3 not in g.coach.inside_done, g.coach.inside_done
    assert g.coach.step == 0, g.coach.step
    assert any("not a win" in e for e in g.coach.events), g.coach.events[-4:]
    for k in range(20):
        igt(g, t - 2000 + k * 6)
        g.read()
    g.screen(707, encounters=False)
    g.fight([65535], 20, screen=None)                  # the fight again
    assert 3 in g.coach.inside_done, g.coach.events[-4:]


def test_the_app_is_one_window_with_the_coach_inside():
    # Coach, Route editor, Memory setup and Help are pages of one window; the coach runs in it
    # (its panel a window of the app) and starts and stops again and again; the editor's
    # keys sleep while another page shows; Compare is a panel inside the editor.
    import json as _j
    import time as _t
    import tkinter as tk
    from tkinter import ttk
    import window
    root = tk.Tk()
    try:
        app = window.App(root)
        root.update()
        assert list(app.pages) == ["coach", "editor", "setup", "help"]
        def tops():
            return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_viewable()]
        assert tops() == []
        app.show("setup")
        root.update()
        assert not app.editor.shown()                  # Delete in Memory setup can't delete a step
        n = len(app.editor.route["steps"])
        root.event_generate("<Delete>")
        root.update()
        assert len(app.editor.route["steps"]) == n
        app.show("editor")
        root.update()
        assert app.editor.shown()
        app.editor.open_compare()
        root.update()
        assert app.editor.cmp_win.winfo_toplevel() is root and tops() == []
        app.editor._cmp_close()
        for _ in range(2):
            app.session.start(simulate=True)
            end = _t.time() + 1.5
            while _t.time() < end:
                root.update()
                _t.sleep(0.02)
            assert app.session.running and app.session.overlay.root.master is root
            app.session.stop()
            root.update()
            assert not app.session.running and app.session.overlay is None
        app.editor.saved = _j.dumps(app.editor.route, sort_keys=True)   # (nothing to save)
    finally:
        try:
            app.session.stop()
        except Exception:
            pass
        root.destroy()


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
