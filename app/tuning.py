"""The coach's numbers: how long to wait, how far ahead to look, what counts as what.

Kept in one place so they can be tuned (and read) without digging through the logic.
"""

from __future__ import annotations


QUIET_SECONDS_TO_END = 6.0     # fallback end-of-battle when the code check isn't available
LOOKAHEAD_STEPS = 8            # how far ahead a recognised boss may jump the route
SCRIPTED_LOOKAHEAD = 3         # how far ahead a fight on a no-encounter screen may jump
FIELD_LOOKAHEAD = 3            # how far ahead a learned screen may jump
PICKUP_LOOKAHEAD = 2           # how many walk steps ahead a named pickup may jump
FIELD_SETTLE = 0.5             # seconds a screen must hold before it counts
FLAG = "\u2691 "                # on the panel title: the coach still has to learn this step
NO_SCREEN = (0, 0xFFFF)                # screen numbers read during FMVs and loading: not places
MAX_SCREEN = 9000               # from here up: the world map (many steps cross it: not a step's
                               # place) and numbers read before the game is loaded
STEP_SETTLE = 3.0              # seconds a step must be up before its screen is learned
LOAD_SETTLE = 5.0              # no screens learned this soon after a save was loaded
FP_CONFIRM = 1.0               # seconds of steady battle bytes before trusting them
FP_MISMATCH_LIMIT = 4.0        # obvious battle but bytes don't match for this long = unreliable
FP_STUCK_LIMIT = 60.0          # "in battle" with frozen bars this long = bytes match outside battle
RESUME_WINDOW = 30.0
MIN_BOSS_SECONDS = 12.0        # an unrecognised fight shorter than this was a random one (fled)
UNDO_WINDOW = 30.0
IGT_TRUST = 180                # in-game timer frames seen counting up before resets are trusted
IGT_NEW_GAME = 300             # timer back under 5 s (from well above) = reset / new game
IGT_RESET_CONFIRM = 60         # after a reset, the timer counting to 1 s confirms a new game
IGT_BACK = 120                 # timer back by more than 2 s = a save or savestate was loaded
IGT_MAX_STEP = 1200            # bigger single jumps forward are loads, not time passing
STALE_SECONDS = 0.8            # an unchanged leftover HP is believed after this long
IDENTIFY_WITHIN = 1.5          # seconds to see which enemy slots are really in the fight
FORMATION_VERSION = 5          # learned data from older, less careful versions is dropped
ATB_DROP = 0.5                 # a bar falling below half its last value = that member acted
MAX_ATB_STEP = 1500            # biggest plausible ATB rise between two reads
CLEARED_SECONDS = 1.0          # every enemy down this long = boss beaten
GIL_PICKUP_MAX = 100000        # a bigger jump than this is a load, not a pickup
BAG_AFTER_FIGHT = 8.0          # seconds after a fight when new items are spoils, not pickups
FP_MATCH = 0.9                 # how alike two fights' enemy records must be to be the same fight
CAPPED_HP = 65535              # a boss whose HP doesn't fit the counter starts on this (Gizamaluke)
CAPPED_SETTLE = 3.0            # seconds into a fight a capped boss's HP may still be loading
PHASE_GAP = 1000               # early on, nothing hits for this much: a bigger drop is a new phase
SCRIPT_HP = 10000              # FF9 adds this to bosses whose fight ends on a script


EMPTY_SLOT = 255          # item number of an empty equipment slot
GUEST_NAMES = ("blank", "marcus", "beatrix", "cinna")


# Guests live in the records of characters who haven't joined yet (seen in a live game:
# Blank's Iron Sword and Silk Shirt in Amarant's record, Marcus's Broadsword in Eiko's).
# Beatrix has the record after Amarant's.
GUEST_RECORD = {"blank": "amarant", "marcus": "eiko", "cinna": "quina", "beatrix": "guest"}
