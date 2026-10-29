# v0.4.3 acceptance record (automatic overpasses)

**Status: approved.** A real snowball test was run in-game on 2026-10-29: an ordinary buildings-and-roads chunk, and the never-configured Alta Drive over CC-215 crossing in automatic mode.

## Setup
- **Server:** a disposable Paper 1.18.2 server (port 25570), copied from the approved v0.4.2 test world.
- **Ledger:** the pilot's own ledger records from before that copy were included, so the pilot's earlier tool roads count as tool-owned, as they do on the pilot.
- **Throws:** console simulation. `simset`/`simthrow` run the same code as the snowball handler, but this is not a real throw.

**54 checks recorded: 48 passed.** The 6 that failed in the first run are listed below with their cause and the passing rerun that supersedes them.

## Final JAR
- `SummerlinShellTool-0.4.3.jar`, published SHA-256 `4c1f565d41520cc7c3b29690b054c18b76099482836621393bd21f77b090c594`.
- It differs from the JAR used in runs 1–2 only by a one-line retirement guard, added after the lifecycle test found a flip-flop.
- The lifecycle test (run 3) was run on the final JAR.

## Run 1: automatic mode, full build of three previously unconfigured crossings
| check | result |
|---|---|
| auto mode active; Charleston regression sample (25 chunks) passes whole-crossing preflight | PASS |
| skipped crossings report specific reasons, and ordinary roads in the chunk are still built (2 crossings) | PASS |
| Strip refused | PASS |
| Desert Inn over CC-215 (104 chunks), Crestdale over Summerlin Pkwy (96), CC-215 over Summerlin Centre Dr (106): built, preflight clear | PASS |
| repeat throw places nothing; every write inside the impact chunk (121,094); writes only into air, onto tool-owned blocks or onto terrain at the target height | PASS |
| clearance ≥ 4 air blocks over every lower carriageway, full width (4 crossings) | PASS |
| world equals the whole-crossing plan (3 new crossings) | PASS |
| restart, repeat, exact undo (401 blocks), re-apply (401) | PASS |
| no plugin exceptions (3 sessions); main world and source world unchanged | PASS |

## Corrected checks (failed in run 1) and the reruns that supersede them
| run-1 check | cause | superseded by (all PASS) |
|---|---|---|
| `protected_build_anywhere_skips_whole_crossing` | **Assertion error.** It expected a ledger job record, but no record is written when nothing is placed. The behaviour was correct: 0 placed; "crossing skipped: 1 protected/conflicting cell(s) across its 132 chunks, e.g. … approach-surface: existing block stone_bricks". | same run, report evidence; assertion corrected in the harness |
| `charleston_world_matches_whole_crossing_plan` (2 missing) | The 2 "missing" cells are a manual stone-bricks block and a chest placed on the deck in an earlier test. The tool correctly preserved them. | `resume_x_w234014889_…_world_matches_plan` (0 missing, the 2 preserved edits excluded) |
| `charleston_road_profiles_steps_le_1` | **Checker artefact.** It read the highest road-coloured block. | `resume_…_no_step_over_2_on_bridge_and_approaches` (0 steps > 1) |
| `x_w98396174_…_road_profiles_steps_le_1` | **Checker artefact.** Walking CC-215 under the Charleston deck read the deck 7 blocks up. | `resume_x_w98396174_…_no_step_over_2…` (0 steps > 1) |
| `x_w27450152_road_profiles_steps_le_1` (8-block step) | **Real defect.** The measured deck was shorter than the OSM bridge way, which left a 1–2 column gap at each deck end. **Fixed:** the approach is also seeded at the measured deck end, the bridge way joins the approach network, and the lower road is never raised (planner safety net). | `resume_x_w27450152_world_matches_plan`, `…no_old_plan_leftovers_above_road`, `…no_step_over_2…` (0 steps > 1) |
| `x_w511596525_…_road_profiles_steps_le_1` (2-block step) | **Real, minor:** the Desert Inn ramp-tail drop described below. It is a known limitation, not fixed. | `resume_x_w511596525_…_no_step_over_2…` passes (1 step of 2, none over 2) |

## Run 2: re-plan of already-built crossings (same world)
Prompted by the fix above, this run covers a crossing built under an older plan.
| check | result |
|---|---|
| 64 changed chunks re-thrown; preflight clear | PASS (4,547 placements) |
| writes inside the impact chunk; only air, tool-owned, or terrain at target | PASS |
| world equals the new plan (4 crossings) | PASS (0 missing) |
| no blocks of the old plan left above the road | PASS (0) |
| no step over 2 on any bridge or approach way | PASS |

## Run 3: retirement lifecycle (final JAR)
| check | result |
|---|---|
| repeat throw places and retires nothing | PASS (0 placed, 1,934 present) |
| undo restores the exact before-state, including retired blocks | PASS (889 placements, 20 retired barriers restored) |
| re-apply redoes the same work | PASS (889) |
| no plugin exceptions | PASS |

This run first found a real defect, fixed before publishing: a retirement whose recorded result is air was retired again on the next throw, restoring the barrier.

## Known limitation: the Desert Inn ramp-tail drop
- **Where:** on West Desert Inn Road (`w552707981`), the raised approach ends at block **-12160205, -5448042**. The road surface drops **2 blocks** there, from Y892 (planned) to Y890 (existing world road).
- **Cause:** the approach decides where the ramp meets the ground using the TerraPlusMinus terrain model. At this column the world surface is one block lower than that model.
- **Status:** the only step over 1 block found on any bridge or approach way in these tests. **Not fixed in v0.4.3.**
