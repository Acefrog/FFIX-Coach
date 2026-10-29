# FFIX Coach

A panel beside DuckStation that follows your Final Fantasy IX (PSX) route: it shows the step you're on, ticks lines off as you do them, and shows each fight's actions and the enemies' HP. It only reads the game; it never presses anything.

> For practice. Leaderboard runs usually only allow notes and splits.

## Start

1. Install **Python 3.10+** from python.org (tick **Add Python to PATH**).
2. Start **DuckStation** with FFIX (US release).
3. Double-click **FFIX Coach.pyw**, pick a route, press **Start coach**.

## During a run

It moves on by itself: when a step's lines are done, when you reach the next step's screen, and when a fight is won.

On the first run, a step with **⚑** needs **F8** once as you leave it. It learns from that, so later runs need no keys.

| Key | Does |
| --- | --- |
| F8 / F7 | Next / previous step |
| Shift+F8 / Shift+F7 | Skip without teaching it anything |
| F6 / F5 | Next / previous fight action |
| Shift+F5 | Switch branch (if it picked the wrong way) |
| F9 / F10 | Hide panel / quit (or **Stop** in the launcher) |

## Editing a route

Press **Edit route**. Pick a step on the left, edit it in the middle, see it on the right as the panel shows it.

Lines the coach can tick:

| Write | Ticks when |
| --- | --- |
| `1x Tent` | you get a Tent |
| `Buy 2 Wrist` · `Sell all Ether` | you buy / sell them |
| `Zidane: Coral Ring, -Leather Hat` | it's equipped / taken off |
| `Steiner: Bird Killer` | the ability is on |
| `Freya back row` | the row is right |
| `Steal Mage Masher` | you have it |
| `@Zidane: Flee` | shows his AP for Flee |
| `@Lv Zidane, Vivi` · `@Lv Zidane 15` · `@Lv` | shows their levels (ticks at 15; `@Lv` alone: everyone) |
| `# note` | never: a small note |
| `Ant Eater > Green Shirt > Mary` | never: story order, shown in pink |

Add `(if …)` to a line and the step won't wait for it.

**+ Sub-step:** something done in the middle of a step (Baku inside Crash Site); afterwards it goes back to the step. A `[Baku]` line in the step shows where it comes (added for you; it follows if you rename the sub-step) and ticks when it's done. A sub-step can have **+ Condition** too (a menu only when you stole something): when it doesn't hold, it and its `[…]` line are left out. A sub-step starts by itself: a fight when it starts; a walk or menu sub-step once the lines above its `[…]` are done, or when you reach its screen (learned like any step).

**+ Branch:** a different way, e.g. 12a for a failed steal. Set *Do 12a instead of 12 when I got Broadsword < 1*. "Got" means stolen, picked up or bought this run. **+ Item** adds another check; all of them must hold.

**Several fights on one fight step:** set *Fights* for the same fight in a row. For different kinds (Cleyra: soldiers and mages), press **+ Fight type** and give each kind its name, HP and how many: they can come in any order, the panel shows each kind's count, and it moves on when all are done.

**+ Condition:** show a step only when something holds, e.g. *Broadsword < 1*. A check can also be AP: *Zidane: Flee >= 20*, or *Zidane: Flee learned / not learned* (in branches too).

## If something's wrong

- **Not on the US release, or something never ticks:** **Memory setup** in the launcher.
- **It learned something wrong:** **Forget** on that step in the editor.
- **What happened last run:** the editor's Checks list (from `logs\run_log.txt`).
- **Start completely fresh:** delete `learned.json` and the `progress` folder.

## Credits

Routes from Quetz (Petroesjka v0.3), Muttski, Petroesjka, Mythikdawn and Metako/MrMizzow. Ability data from [Memoria](https://github.com/Albeoris/Memoria). Thanks to the FFIX community.
