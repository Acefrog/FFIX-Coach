# FFIX Coach

A panel beside DuckStation that follows your Final Fantasy IX (PSX) route: it shows the step you're on, ticks lines off as you do them, and shows each fight's actions and the enemies' HP. It only reads the game; it never presses anything.

> For practice. Leaderboard runs usually only allow notes and splits.

## Start

1. Install **Python 3.10+** from python.org (tick **Add Python to PATH**).
2. Start **DuckStation** with FFIX (US release).
3. Double-click **FFIX Coach.pyw**, pick a route, press **Start coach**.

It comes with **MPU Route** as an example: open it with **Edit route** to see how a route is made (walk and menu steps, fights picked from the fight list, sub-steps), then make your own with **New** or a copy of it.

## During a run

It moves on by itself: when a step's lines are done, when you reach the next step's screen, and when a fight is won.

On the first run, a step with **⚑** can't tell yet where the next step starts: on that screen, press **Learn** (**F6**) once. It learns the screen and carries on by itself, so later runs need no keys.

A fight is recognised only by its **HP** together with the **screen it starts from**. A fight with **⚑** doesn't have both yet: press **Learn** during it once (it learns its HP and screen, and the fight counts from then on), or set them in the editor.

Every fight in the editor's **Fight** list (Masked Man to Necron) already comes with its HP and where it starts from, so a new route needs no learning for them: pick the fight from the list on its step (picking fills in only its **Starting HP** and where it **starts from**). **Learn** is for fights of your own that aren't on the list, and what it learns for a list fight (another room) is used by every route.

| Key | Does |
| --- | --- |
| F6 | **Learn**: this fight, or where the next step starts (the route carries on as usual) |
| F8 / F7 | Next / previous step (learns and forgets nothing; F7 straight after a win undoes the win) |
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

**+ Sub-step:** something done in the middle of a step (Baku inside Crash Site); afterwards it goes back to the step. A `[Baku]` line in the step shows where it comes (added for you; it follows if you rename the sub-step) and ticks when it's done. A sub-step can have **+ Condition** too (a menu only when you stole something): when it doesn't hold, it and its `[…]` line are left out. A sub-step starts by itself: a fight when it starts; a walk or menu sub-step once the lines above its `[…]` are done, or when you reach its screen. A sub-step with its screen set under **Area** (a shop) comes up only when you walk in.

**Area:** where a step **starts on** (reaching it moves the route there) and **finishes on** (reaching it ends the step, whatever is left on it). For a fight, where it **starts from**: with its HP, that's how the coach knows it. Type a name to search (e.g. *Dali*). Left empty, the coach learns them itself: what it has learned shows in the box in grey, marked *(learned)*, and is used as it is; pick a screen to set it yourself.

**+ Branch:** a different way, e.g. 12a for a failed steal. Set *Do 12a instead of 12 when I got Broadsword < 1*. "Got" means stolen, picked up or bought this run. **+ Item** adds another check; all of them must hold.

**Several fights on one fight step:** set *Fights* for the same fight in a row. For different kinds (Cleyra: soldiers and mages), press **+ Fight type** and give each kind its name, HP and how many: they can come in any order, the panel shows each kind's count, and it moves on when all are done. The number is your route's own: if your route has one more than it says, that one still counts as this fight (not a random battle). When the number changes from run to run, tick **Any number**: every fight with its HP counts, and the step ends when you reach the next step's room.

**+ Condition:** show a step only when something holds, e.g. *Broadsword < 1*. A check can also be AP: *Zidane: Flee >= 20*, or *Zidane: Flee learned / not learned* (in branches too).

## If something's wrong

- **Not on the US release, or something never ticks:** **Memory setup** in the launcher.
- **It learned something wrong:** **Forget** on that step in the editor.
- **What happened last run:** the editor's Checks list (from `logs\run_log.txt`).
- **Start completely fresh:** delete `learned.json` and the `progress` folder.

## Credits

Routes from Quetz (Petroesjka v0.3), Muttski, Petroesjka, Mythikdawn and Metako/MrMizzow. Ability data from [Memoria](https://github.com/Albeoris/Memoria). Thanks to the FFIX community.
