# Egg v0.5.0 — test results

**Approved in-game on 2026-10-29** after the in-game test below; released as v0.5.0.

**Candidate:** `SummerlinShellTool-0.5.0.jar`, sha256 `c29977b62771e45b123e4ce657fe74d77249702d4b7887b68c86ddc0a8498a8c`.
- Its class files are byte-identical to the build the automated suite ran on, `0.5.0-dev` (sha256 `c5c5c90a…93b6`). Only the version in `plugin.yml` differs.
- A smoke test of the 0.5.0 JAR passed: the plugin enabled, `status` worked, a preview wrote nothing, and there were no exceptions.

**Environment:** a disposable test server.
- Paper 1.18.2 #388, JDK 17.0.20.1, WorldEdit 7.2.20, TerraPlusMinus 1.3.3.
- The world is a copy of the primary pilot world.
- The live primary server was not used.

**Suite:** `egg_test.py s1 s2 s3 s4 final`, run on 2026-10-29: **52 of 52 checks passed**, with no plugin exceptions.
- The runner exits 1 on any failed check.
- The harness is local only, because it holds a test player id; it is not in this repo.
- Players are simulated through console commands (`eggtool simthrow|simset|simhatch|simentityhit`). Real throws are the in-game step below.

## Matrix

| Area | Checks (all passed) |
|---|---|
| Input | Hatching is suppressed only while Egg mode is on. OFF behaves as vanilla. An entity hit generates nothing. Duplicate throws are deduplicated. Mode is OFF after restart. |
| Tile math | Floor division with negative coordinates. A second block in the same tile reuses the cached plan (prep 0.0 s). |
| Strip / no data | A Strip hit is refused. Desert Inn (no baseline) gives `no_coverage` for every content. A missing landcover index gives `no_coverage`. An injected provider failure gives `provider_failure` and places 0. |
| Preview | Writes nothing to the ledger or the world (it would place 127). |
| Protection | A chest, an armor stand, landscaping, 2 tracked edits and an untracked build were all untouched (458 placed around them). There were no writes outside the tiles or in the 8-block ring. The 3,597 accepted pilot road and shell cells and the 33 Snowball road cells did not change. |
| Terrain | Fill tile F placed 127 within the ±2 limits, and a repeat placed 0. Sloped tiles A and B (new chunks, captured baseline) placed 1,047 and 940 terrain blocks. |
| Vegetation | Remote canopy is deferred until its trunk exists (14 → 0). 232 trunks with no duplicates across the boundary. Throwing in either order, or re-preparing, gives an identical world (16,384 columns, 0 differences). |
| Agriculture | Logandale field w145028142 continues across two tiles (4,096 + 4,096). The Angel Park golf course is neither farmed nor terraformed. |
| Locks | Egg is refused while Snowball holds the tile, and Snowball is refused while Egg holds it. |
| Undo | The dry run changes nothing. Undo restores the exact before-state (126) and keeps a later edit (1). Snowball undo cannot touch Egg jobs. Re-applying redoes the same 127. |
| Retirement | A shrunk plan retires exactly the dropped cells (149), which return to their exact before-state. Restoring the plan re-applies 149. No flip-flop. |
| Cancel | Cancelling partway leaves a partial job (63 of 1,047) that undo restores exactly (63). |
| Bounds | Maximum tick time 6.6 ms over 30 jobs. Egg cache 6.9 MB. Captured baselines 1.83 MB. BlueMap events: 41 lines. |

**Timings** (30 jobs):

| | median | max |
|---|---|---|
| Preparation (s) | 0.0 (cached) | 4.6 |
| Placement (ms) | 6.4 | 36 |
| Total (s) | 1.6 | 9.7 |
| Tile load + capture (ms) | 0 | 4,449 |

## Known limitations
- **Eligible ground:** only chunks generated after install (captured), and the 910 pilot-generated chunks, are eligible for terrain. Converted purchased-world regions never are.
- **Shutdown during chunk generation:** stopping the server while Egg generates a missing chunk can hang shutdown (TerraPlusMinus `TreePopulator`). Chunks are generated one at a time, and nothing is requested while stopping. Run `/eggtool status` and stop only when it shows no jobs.
- **Street trees:** they are often skipped by road protection buffers.
- **Canopy:** canopy that crosses into a refused or protected neighbour stays deferred.
- **Fences:** a fence can change its connection state next to new blocks. Undo ignores tick-only properties.
- **Farmland:** generic bands are inferred. Crop, row direction, irrigation and growth stage are never inferred.
- **Not yet done:** review minors 2, 6, 8, 9 and 11 (see REVIEW.md). The repo has no LICENSE file.

## In-game test (server 12, `localhost:25572`)
1. **Start the server.** Run `C:\BTE-Migration\12-egg-test\start-server.bat` and join `localhost:25572`. The whitelist and op are already set.
2. **Turn Egg on and get eggs.**
   - `/eggtool status`: Egg mode should be OFF after login.
   - `/eggtool on`, `/eggtool content all`, then `/give @s egg 16`.
3. **Fresh tile A** (sloped desert; its earlier jobs were undone).
   - `/tp -12159392 1040 -5459232`.
   - Throw an egg at the ground. The chat should report the job, then DONE. Expect about 1,047 terrain blocks plus a few shrubs.
   - `/eggtool undo` gives a dry run. `/eggtool undo <job> confirm` restores the tile.
4. **Previews:** `/eggtool preview`, then throw. Nothing should change; the report goes to `plugins\SummerlinShellTool\egg-reports`.
5. **Look at finished tiles:**

   | Tile | Teleport | Expect |
   |---|---|---|
   | Pilot fill F | `/tp -12159520 920 -5450464` | Roads and shells untouched |
   | Street trees V1 | `/tp -12155104 816 -5448736` | Street trees |
   | Logandale farmland L1/L2 | `/tp -12074144 425 -5485472` | Rows continue across the tile edge |
   | Angel Park golf G | `/tp -12154720 808 -5452000` | Must be unchanged |

6. **Hatching:** with Egg on, no chickens should hatch. Run `/eggtool off` and throw: vanilla behaviour.
7. **Stop the server.** Run `/eggtool status`, then `stop` once it shows `jobs 0/`.
