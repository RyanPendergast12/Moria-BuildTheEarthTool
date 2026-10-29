# Egg v0.5: independent review

**Reviewer:** independent, read-only. I wrote none of this code. 2026-10-29.

**Scope:**
- `plugin/.../EggTool.java`, plus the Egg hooks in `ShellToolPlugin.java`: `ownerOf`, `logPlace`, `sameModuloTicks`, `undo`, `regionLocks`, `submit`, the Snowball writers and `track`.
- `prep/egg_prep.py`, `egg_common.py`, `egg_provenance.py`, `egg_terrain.py`, `egg_vegetation.py` and `egg_agriculture.py`.
- `docs/egg/*.md`.
- `C:\BTE-Migration\scripts\egg_test.py` (with `egg_srv.py`).

**Method:** I read the code statically. I did not run anything, start a server or touch a world.

**Severity:**
- **BLOCKER:** violates a hard requirement, can damage builds, or can lose undo.
- **MAJOR:** a real defect with a plausible trigger.
- **MINOR:** limited impact.

---

## BLOCKERS

### B1. The column above `base_y` is never checked, so untracked builds count as "baseline"
**Where:** `EggTool.java:490-504` (`column0`) and `EggTool.java:469-474` (`'b'` ops).

**What happens.** The write-time check proves only two things:
- the block at `base_y` still equals `base_block`;
- no tracked edit, ledger placement, block entity or entity is in the window.

`tracked` only records `BlockPlace/Break/Bucket` events (`ShellToolPlugin.java:308-317`). **WorldEdit/FAWE edits, which are how BTE builders build, are never tracked**, and neither are pistons or `/setblock`. Nothing compares the cells above `base_y` with the baseline. They should be air, or the recorded `base_cover`. The plugin never even reads `base_cover`.

**Failure scenario.**
1. A builder WorldEdits a sandstone or stone-brick wall, a patio, a shed or a pool onto untouched sand. This can be in a pilot chunk or in a captured chunk. It sits more than 3 blocks from an OSM building, so `protect` = 0.
2. The surface at `base_y` is unchanged, so the column passes.
3. A terrain `cut` at `base_y` removes the sand under the wall. The wall is left floating.
4. Or vegetation and agriculture `air` ops place leaves or crops into the yard and into enclosed air cells of the build.

This is exactly "matching sand does not prove untouched terrain". The proof here rests on one block.

**Minimal fix.** Pass `base_cover` to the plugin. In `column0`, require every cell in `(base_y, base_y+40]` to be one of:
- `air` / `cave_air`;
- the recorded `base_cover`;
- owned by a live Egg job.

Otherwise return "column above baseline changed". Add a harness case: an untracked `setblock ... stone_bricks` at `base_y+1` in a planned cut column must leave that column untouched. Today it would be cut.

### B2. Undo and retire undermine or bury later work stacked on Egg cells
**Where:**
- Undo: `ShellToolPlugin.java:822-829`.
- Retire: `EggTool.java:445-452`.
- Snowball `airCell` / `roadCell`: `ShellToolPlugin.java:655-692`. These record `upgrade_of` only when they replace a *non-air* tool-owned block.

**What happens.** Undo restores every cell still equal to its after-state, and retire is per cell. Later work that rests *on* or *under* an Egg cell, rather than replacing it, creates no dependency.

**Failure scenarios.**
- **(a) A road built on cut ground.** Egg cuts 2 blocks, so the cells become air. A Snowball road later re-surfaces the new floor at `base_y-2`, which is not an Egg cell, so no `upgrade_of` is recorded. Then either `/eggtool undo` or a later re-plan retire refills `base_y-1..base_y` with sand. **The road is buried.**
- **(b) A build on filled ground.** A player plants a flower or torch, or builds a wall, on top of Egg fill sand at `base_y+1`. Undo or retire sets the fill back to air without physics, which leaves the player's block floating. On the next block update a flower or torch pops off, destroying the player edit.
- **(c) A shell on Egg cells.** A Snowball shell ground-finder (`ShellToolPlugin.java:713-717`) places walls on top of an Egg plant or fill without `upgrade_of`. Egg undo removes the support.

**Minimal fix.** Before restoring an Egg cell in undo or retire, run the same column test as §5 check 3 on that column, covering the window from `base_y-maxCut-1` up to `+40`. Refuse, and keep the cell, if the column has any of these:
- a tracked edit;
- a live non-Egg placement;
- a non-air cell that differs from what the Egg job left.

Alternatively, have Snowball record `upgrade_of` when the cell below or above its write is a live Egg placement.

### B3. Tick-equivalence plus undo ignoring `tracked` lets undo delete a player's own replacement block
**Where:**
- Undo: `ShellToolPlugin.java:825`. It does not check `tracked`.
- `ownerOf`: `ShellToolPlugin.java:199`.
- `sameModuloTicks`: `ShellToolPlugin.java:790-797`.

**Failure scenario.**
1. Egg places `wheat[age=7]`. A player harvests it and replants `wheat[age=0]`. The edit is tracked.
2. `sameModuloTicks` ignores `age`, so undo treats the cell as unchanged and restores `air`. **The player's crop is deleted.**

The same happens when:
- a player breaks Egg `oak_leaves[persistent=true]` and places their own oak leaves (also `persistent=true`; `distance` is ignored);
- a player replaces a sapling of the same type (`stage` is ignored).

Retire checks `tracked` (`EggTool.java:449`). Undo does not.

Replacing leaves with a *different* leaves type is correctly preserved, because the material differs.

**Minimal fix.** In `undo`, treat `tracked.contains(pos)` as "kept (modified)". For Egg jobs at least, this should hold for every job.

### B4. A missing or out-of-coverage landcover index makes terrain treat the land as having no water, washes, landscaping or development
**Where:**
- `egg_prep.py:47-57` and `:82`. `lc` is `None` when the file is missing. When the tile is outside the index coverage, `q()` returns `None`, and `lc() or []` turns that into an empty list.
- `egg_prep.py:59-80`.
- `egg_terrain.py:61` (`_window_protect` returns no landcover bits) and `:146-178`. `plan()` never refuses.

**Failure scenario.** The index is absent, or the tile lies outside its coverage box. Terrain plans cuts and fills across mapped washes (`waterway=*`), mapped water, parks and residential land. Only lidar class 9 remains, and dry washes are not class 9. This breaks "no hydrology changes" and "missing data never means empty land".

Vegetation and agriculture correctly return `no_coverage` (`egg_vegetation.py:278-282`, `egg_agriculture.py:202-206`). Terrain does not.

The harness proves the bug instead of catching it. `s3` starts the server with `MORIA_LANDCOVER_INDEX=missing.sqlite` on tile N, then relies on terrain placing blocks so that it has a partial job to cancel (`egg_test.py:218-226`).

**Minimal fix.**
- In `egg_terrain.plan`, return `NO_COVERAGE` when `ctx['landcover'] is None` or the query returns `None`.
- In `egg_prep`, OR `P_UNKNOWN` (128) into `protect` when the landcover query fails.
- Make `s3` assert that terrain is `no_coverage` there.

---

## MAJOR

### M1. Undo checks the lock and dependents at command time, then restores later
**Where:** `ShellToolPlugin.java:816-820` versus `:838`.

**What happens.** The lock and dependent checks run before the async chunk loads. The restore runs in a later task without checking again. In that window, an Egg or Snowball job can take the lock, or write an `upgrade_of` dependent. Undo then marks the job `undone` while a live dependent exists, and later undoing the dependent brings back blocks of an undone job.

This contradicts §6: "Snowball submit and both undos refuse a chunk that is locked".

**Fix:** repeat both checks at the top of `run`, or put the undo's chunks into `regionLocks` during the load.

### M2. Prep timeout and cancel do not kill the real Python process on Windows
**Where:** `EggTool.java:213` and `:245` (Snowball has the same pattern).

**What happens.** `venv\Scripts\python.exe` on Windows is a launcher that spawns the real interpreter as a child. `Process.destroyForcibly()` kills only the launcher. After a "timeout" or `/eggtool cancel`:
- the orphan keeps fetching and writing `tiles/<tx>_<tz>/*.json`;
- `preps.remove` has already run, so a new prep of the same tile can start alongside it.

Two writers then share one tile directory, and `write_json` uses the same `.tmp` name for both.

**Fix:** call `p.descendants().forEach(ProcessHandle::destroyForcibly)` before `p.destroyForcibly()`.

### M3. Neighbour agreement depends on the order of capture and generation, so "either order" is not proven
**Where:** `egg_terrain.py:43-52` and `:116-124`; `EggTool.loadTile` (`:194-204`) loads only the tile's 16 chunks.

**What happens.** The terrain window reads the 32-block ring from the neighbours' baselines. Suppose tile A is prepared before B's chunks were ever generated:
1. B's ring columns have no capture, so they are NaN and ineligible.
2. A feathers its edge to 0 and caches that plan for good, because the context and plan are keyed only by pipeline.
3. B is prepared later, sees A's columns as valid, and applies the full delta at its edge.

The result is a step of up to 2 blocks at the seam.

The harness "either order" check (`egg_test.py:195-214`) re-applies the cached plans, so it is identical by construction and proves nothing.

**Fix:** either
- have `loadTile` also generate and capture the ring chunks (8×8 chunks) before prep; or
- include the ring's baseline availability in the context key and re-prepare when it changes.

Make the harness delete both tiles' caches and prepare B then A.

### M4. A degraded plan is "ready" or "nothing_to_do", and the union rule then retires earlier work
**Where:**
- `egg_vegetation.py:341-358`: a failed WorldCover or lidar fetch only appends a reason, then status is `READY`/`NOTHING` (`:503`).
- `EggTool.java:409` (`usable()`).

**Failure scenario.** A pipeline bump re-prepares a vegetation tile while lidar is unavailable. The new plan lacks every measured-tier tree, and apply retires all of them that were already planted.

This is missing data treated as "nothing here". It is undoable, but it is exactly the case the union rule must not act on.

**Fix:** set a `degraded: true` flag in the plan or manifest whenever any provider tier failed. Have the plugin skip retirement, or report `low_confidence`, for degraded contents.

### M5. Old chunks reported as `isNewChunk` are captured as baseline, and the capture treats natural-material builds as ground
**Where:** `EggTool.java:165-192`.

**What happens.** Paper reports `isNewChunk` for any chunk not loaded from disk as FULL, including chunks stored at a proto status. Converter output can be like that. Such a chunk, with converted builds in it, would be captured as an approved baseline. The Python pilot reader requires `Status full`; the plugin capture checks nothing like that.

`writeCapture` takes the first `NATURAL` block from the top. Sandstone, terracotta and stone roofs or walls therefore become `base_y`.

**Fix:**
- refuse capture for chunks inside the converted purchased-world extent: keep a deny-list of chunk keys present at install, or check the region-file offset for the chunk before trusting `isNewChunk`;
- run the listener at `EventPriority.LOWEST`, so that no other plugin can edit the chunk first;
- document the TPM decoration timing (populators run before FULL in 1.18.2).

### M6. Vegetation and orchard trees float over columns that terrain cut in the same job
**Where:** `EggTool.java:501-502`. The surface check accepts "owned by a live Egg job".

**Failure scenario.**
1. With content `all`, terrain cuts `base_y` and `base_y-1` and puts the surface at `base_y-2`.
2. A vegetation or orchard trunk op at `base_y+1` (`expect air`) passes: the cell is air and the surface is Egg-owned.
3. The tree stands 2 blocks above the ground.

Generators are independent by contract, so the plugin must catch this.

**Fix:** for `air` ops of vegetation or agriculture, require the surface at `base_y` to equal `base_block` exactly and not be Egg-changed. Otherwise require the cell below the anchor to be solid.

### M7. Harness assertions that do not prove their claim
- **`snowball_blocks_in_egg_tiles_unchanged`** (`:158-164`) passes vacuously when F and C contain no Snowball placements. It records the count but never requires `> 0`. No Snowball work is placed in F or C first.
- **`undo_restores` / `cancelled_partial_job_undoable`** (`:190`, `:227`) use a substring match: `'restored 12'` matches `'restored 123'`. Neither compares the world with `snap_FC_before.json` after undo.
- **"Undo preserves later edits" is never tested.** No case covers:
  - a player edit between apply and undo;
  - a replanted crop or re-placed leaves (B3);
  - a Snowball road on a cut (B2a).

  Nor is any of these tested for retire.
- **`shrunk_plan_retires...`** (`:206`) asserts only `retired >= 1`. It computes `live` but never compares it. It should assert `retired == len(live)` and check the world blocks.
- **Lock coverage is one-way.** No test that Egg is refused while a Snowball job runs, or that `/shelltool undo` is refused in a locked chunk.
- **Preview is checked only through the ledger line count.** The world is not compared after preview.
- **Results can carry over between runs.** `results.json` is loaded and never reset (`:13`). A stage run on its own, or one that crashed, leaves the previous run's PASS entries in place, and `final` counts them. **Fix:** clear `R` at the start of a full run, or store a run id per check and fail checks whose id is stale.
- **The wrong report can be read.** `throw()` returns `last_report()` by mtime, not by job id (`:36-37`), so with concurrent jobs it can read another job's report.

---

## MINOR
1. **No bounds on Egg ops in the plugin.** The plugin does not enforce `y >= base_y - maxCut` for `base` ops, or `y <= base_y + maxFill` for terrain `air` ops. A terrain `air` op below `base_y` would fill `cave_air`.
   - Location: `EggTool.java:467-476`.
   - Python bounds this correctly, but the plugin is the last line of defence.
   - Fix: a two-line range check per content.
2. **A column is not written atomically.**
   - Fill case: the `surface` op (grass→dirt at `base_y`) is written before the fill `air` ops. If a fill cell is occupied, only the surface changes.
   - Cut case: if the second cut is skipped, the column is left half-cut.
   - `brokenCols` only stops *later* ops (`EggTool.java:453`).
   - Fix: pre-check every op of a column before writing any of them.
3. **Retire skips most checks.** `R` ops skip the Strip, `protect`, entities and the column check (`EggTool.java:445-452`). Restoring sand can place a block inside an entity.
4. **Egg writes do not invalidate Snowball crossing preflights.** `track()` clears `preflights` (`ShellToolPlugin.java:310`), but Egg `write()` does not. A cached "whole crossing clear" can then lead to a partially placed crossing in another chunk.
5. **Collections shared across threads.** `EggJob.notes`, `errors`, `per`, `feats`, `fc` and `ops` are plain collections. They are mutated on async threads (`prepareOrLoad`, `loadPlans`, the `t.done.thenRun` callback) while a main-thread `cancel()` → `report()` iterates them. A `ConcurrentModificationException` would lose the report.
   - Fix: marshal the mutations to the main thread, or synchronise.
6. **Preview generates chunks.** It loads, and therefore generates, any missing tile chunks (`loadTile`, `start`), and also anchor chunks. That changes the region files and creates captures. Preview also holds the 16-chunk lock for the whole preparation, up to 600 s, which blocks Snowball.
7. **Anchor coordinates are truncated, not floored.** Agriculture field anchors are floats with `y = null` (`egg_agriculture.py:273`). `start()` takes `getAsInt() >> 4` (`EggTool.java:380`), which truncates toward zero. For negative fractional coordinates this can hold or generate the wrong chunk, and an anchor in a far-away field generates a far chunk. No crash path was found, because `remote()` is only reached by orchard features, whose anchors are integers.
8. **Cancelling a shared prep fails other players' jobs.** `/eggtool cancel` cancels a `Prep` shared with other players' jobs on the same tile, and those jobs end "Preparation cancelled".
9. **Block before ledger.** The block is set before its ledger line is written, and the ledger is flushed once per tick (`EggTool.java:482-483`). An `IOException` in `log` means an unledgered write. Writing the record first would be safer. Snowball has the same pre-existing pattern.
10. **Main-thread cost of capture.** `getChunkSnapshot` runs on the main thread for every new chunk; the world height is 3056, about 191 sections. That is a noticeable cost during pregeneration or exploration. The queue is bounded at 4096, and overflow means the chunk is not captured, which is safe.
11. **Remote canopy has no dependency on its trunk.** Remote canopy placed by tile B depends on tile A's trunk, but no dependency is recorded. Undoing A leaves B's leaves floating.

---

## Verified correct
- **Tile math.**
  - `Math.floorDiv(x >> 4, 4)` and `floorDiv(cx, 4)` are correct for negative coordinates (`EggTool.java:110`, `:148`).
  - `egg_common.tile_of_block` uses floor division, with a self-check.
  - `loadPlans` drops any op outside the tile or with an invalid `expect` (`EggTool.java:351`).
  - `E.plan` asserts the same rules.
- **Preview writes nothing.**
  - `write()` returns before `setBlockData` and `logPlace`.
  - No job record is written, nothing is added to `eggJobs`, and nothing is indexed.
- **Missing provenance is ineligible.**
  - A column whose `base_y` is null is skipped.
  - An unreadable `context.json` makes every column ineligible.
  - Chunks that are neither captured nor among the 910 pilot chunks give `None`.
  - Pilot chunks must have `Status full`.
  - An all-null tile gives `NO_COVERAGE`.
  - A missing OSM index gives `P_UNKNOWN`.
  - Vegetation and agriculture return `no_coverage` when the landcover index is missing.
- **Failure statuses never trigger retirement.** `provider_failure`, `low_confidence`, `refused_limits`, `plan_unreadable` and `protected` all skip it (`usable()`).
- **Locks are mutually exclusive at submit time.**
  - Egg checks `regionLocks` and Snowball `activeChunks` across all 16 chunks, then takes all 16 in one main-thread step.
  - Snowball `submit` checks `regionLocks`.
  - Every Snowball write path (shells, roads, overpass cells, retirements) is filtered to its own impact chunk, so a chunk lock covers it.
  - Locks are released only in `finish()`.
- **Per-cell re-check before write.** Every op re-checks, in the same tick:
  - the expected state;
  - `tracked`, block entity and entity;
  - non-Egg ownership;
  - Strip, `protect` and the column window.

  Column results are cached for one tick only. Egg never writes onto a Snowball-owned cell; it reports "ShellTool work" or "ShellTool placement in column".
- **Ledger exactness.** Each Egg write logs the actual `before` (`cur.getAsString()`) and `after`, the feature, role, content and `upgrade_of`. Cancel stops between ticks and the ledger is flushed every tick, so a partial job is undoable. Egg undo restores newest first, which handles retire-then-place on the same cell.
- **Dependencies through replacement.**
  - Egg upgrades and retires record `upgrade_of`.
  - Snowball writes that replace an Egg-owned block record `upgrade_of` through `ownerOf`.
  - Undo refuses while dependents are live, at the time of the command (but see M1).
- **The union rule covers chunks the new plan no longer touches.**
  - `tileIndex` is rebuilt from `byPos` after `loadLedger()`, which runs synchronously before `new EggTool` (`ShellToolPlugin.java:141-145`).
  - Retire needs the latest placement to be identical, the block to be unchanged modulo ticks, and the cell to be untracked and not a block entity.
  - A cell whose latest placement is a `retire` is not retired again, and the next plan's `base` op may write over a restored cell, so there is no flip-flop for a deterministic plan.
- **Tick-equivalence scope.** It applies only to Egg-owned placements, and the material must match. A different leaves type, crop or sapling is preserved. Leaves are planned `persistent=true` (vegetation self-check). No saplings or melon/pumpkin stems are planned, so no random-tick growth writes outside the tile.
- **Terrain bounds in Python.**
  - Cuts are clamped to `max_cut` and fills to `max_fill`.
  - The change is feathered to 0 next to ineligible columns, with a 2-block threshold, so there is no whole-tile flattening.
  - A 6-block water and wash margin is kept when landcover is present.
  - `refused_limits` applies above `max_ops` (in `egg_terrain` and again in `egg_prep` and the plugin).
  - There are no fills below `base_y` and cuts never go deeper than `max_cut`.
  - Road, building and `area:highway` buffers exclude the roads.
- **Baseline capture.**
  - The snapshot is taken synchronously inside the load event, before any player or tool can write.
  - Captures are never overwritten: `exists` is checked and there is no `REPLACE_EXISTING`.
  - A dropped or failed capture leaves the chunk without a baseline, which is safe.
  - `loadTile` waits for in-flight captures before preparing.
- **Threading of world and ledger state.**
  - Bukkit world access, `byPos`, `ledgerJobs`, `undone`, `regionLocks`, `eggJobs` and `tileIndex` are touched only on the main thread.
  - Capture runs off-thread on a `ChunkSnapshot`.
  - Placement is limited per tick by blocks and milliseconds, and the prep and capture queues are bounded.
- **Strip protection.**
  - At submit: a 9-point sample per chunk.
  - In Python: an exact intersection of the tile box with Strip+500, giving `PROTECTED`.
  - Per column at write time: `inStrip`, which covers every height.
