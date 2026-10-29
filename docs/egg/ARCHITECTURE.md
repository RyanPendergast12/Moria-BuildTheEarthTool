# Egg tool (v0.5): architecture and shared contracts

Snowball stays the tool for building shells, roads and overpasses. **Egg** is a separate interaction in the same plugin, for the ground itself: terrain correction, vegetation and agriculture.

- A thrown egg that hits a **block** selects one fixed **tile of 4×4 chunks** (64×64 blocks).
- Egg prepares real geographic data for that tile and previews or generates its content.
- It writes **only inside the tile**, through the plugin's existing protected writer and ledger, so every job can be undone exactly.

This note is the single contract shared by all agents. Change it only through the coordinator.

## 1. Tile math (`prep/egg_common.py`, mirrored in Java)
- **Chunk:** `cx = floor(x/16)`, `cz = floor(z/16)`. In Java: `x >> 4`.
- **Tile:** `tx = floor(cx/4)`, `tz = floor(cz/4)`. In Java: `Math.floorDiv(cx, 4)`.
- **Bounds:** chunks run from `4tx .. 4tx+3`; blocks run from `64tx .. 64tx+63` (the same applies to z). All arithmetic is floor division, including negative coordinates. `egg_common.py` self-checks this.
- **Context ring:** 32 blocks around the tile are **read** for context only: feathering, whole-feature planning, neighbour agreement. They are never written.
- **Sizing tile:** the Desert Inn test block −12160205, −5448042 lies in tile (−190004, −85126). That tile's blocks are x −12160256..−12160193 and z −5448064..−5448001.

## 2. Flow
```
egg hits block -> plugin: tile (tx,tz) -> tile lock (16 chunk keys, shared with Snowball) -> manifest cached & current?
   no  -> queue prep (dedup per tile+content, priority) -> python prep/egg_prep.py <cache> <tx> <tz> --content <c> [...]
   yes -> read plans -> world checks (main thread, time-budgeted) -> PREVIEW report | APPLY via protected writer -> ledger
       -> report  plugins/SummerlinShellTool/egg-reports/egg-<job>.json  + BlueMap event line
```
- **`egg_prep.py`** (coordinator) is the orchestrator. It builds the shared **context**, then calls `egg_terrain.plan(ctx)`, `egg_vegetation.plan(ctx)` and `egg_agriculture.plan(ctx)` according to `--content`, and writes the outputs.
- **Generator independence:** each generator is a pure function of the cached data plus the context. None of them reads the live world. The plugin's write-time checks own that job.
- **Order independence:** this purity is why adjoining tiles agree in either generation order.

## 3. Cache layout (`<cache-dir>/egg/`)
| path | written by | content |
|---|---|---|
| `tiles/<tx>_<tz>/manifest.json` | egg_prep | `pipeline` (= `EGG_PIPELINE`), per-content `{status, reasons, plan, ops, features}`, sources, timings, `prepared` |
| `tiles/<tx>_<tz>/context.json` | egg_prep | Provenance and protection per column (§5) |
| `tiles/<tx>_<tz>/terrain.json`, `vegetation.json`, `agriculture.json` | generators via egg_prep | Plans (§4) |
| `dem/`, `worldcover/`, `cdl/` | the agent that owns the source | Bounded raster caches with `provenance.json` |
| `prep-logs/<tx>_<tz>.log` | plugin | Prep stdout and stderr |

The Snowball caches (`chunks/`, `roads/`, `ept/` and the rest) are **reused read-only**. For example, the EPT LiDAR tiles in `<cache>/ept` are shared through `ept_cached.fetch`.

## 4. Plan contract (`egg_common.plan`)
```json
{"version":"egg-plan-1","content":"terrain|vegetation|agriculture","tile":[tx,tz],"bounds":{...},
 "status":"ready|no_coverage|provider_failure|low_confidence|unsupported|protected|refused_limits|nothing_to_do",
 "reasons":["..."], "sources":{...}, "stats":{...},
 "features":[{"id":"...","kind":"...","info":"...","partial":false,"anchor":[x,y,z]|null, "anchor_block":"minecraft:...|null", "evidence":"...", "confidence":"measured|mapped|inferred"}],
 "ops":[[x,y,z,"expect","minecraft:block[state]","feature","role"], ...]}
```

**`expect`** is what the cell must hold at write time. Anything else means the cell is skipped and reported, never forced.
- **`air`:** the cell must be `air` or `cave_air`. Used for placing fill, plants, trees and crops.
- **`base`:** the cell must be **baseline terrain**. That means:
  - the column passes the provenance check (§5);
  - if `y == base_y`, the block equals `base_block`;
  - if `y < base_y`, the material is in `NATURAL` (§6).
  - Used for replacing the surface (farmland, ground cover) and for cutting (`block` = `minecraft:air`).
- **`egg`:** the cell must still hold exactly what an earlier Egg job placed (`ownerOf` is an Egg job). Used for in-place upgrades only.

**Other rules:**
- **Ordering:** ops are applied in file order. Generators order them bottom-up per column: cuts top-down first, then fills bottom-up, then surface, then plants. Trunks come before leaves.
- **Removals** are ops whose `block` is `minecraft:air`. Every op is recorded as a ledger placement with exact before and after states.
- **No op outside the tile.** `plan()` asserts this.
- **Partial features** crossing a tile edge: the whole feature is planned, then clipped.
  - Its `features[]` entry has `partial: true` and `anchor` = the trunk base or field-origin cell.
  - An op whose anchor lies outside this tile has role `*-remote`. The plugin writes `*-remote` ops only when the anchor block already exists in the world with the planned state (it reads outside the tile, read-only). Otherwise they are reported as **deferred**, so no canopy floats without its trunk.

## 5. Context: provenance and protection (`context.json`)
"Matching sand/dirt/stone does NOT prove a block is untouched terrain." A column is **eligible** only when there is positive evidence.

| field (each `[64][64]`, index `[z-z0][x-x0]`) | meaning |
|---|---|
| `base_y` | Top terrain block Y in an **approved baseline**. Two sources: (1) a capture the plugin writes when the chunk is first generated (`ChunkLoadEvent#isNewChunk`, `<plugin>/egg-baseline/<cx>_<cz>.json`, checked first); (2) the pilot snapshot `summerlin/pilot/baseline-world-after-generation`, restricted to the 910 chunks in `generation_chunks.json` that TerraPlusMinus generated fresh. The other regions of that folder are the converted purchased world (builds) and are **never** a baseline. `null` means no baseline: **ineligible**. |
| `base_block` | Its block state string |
| `base_cover` | Non-terrain block(s) directly above it in the baseline (generator vegetation such as `minecraft:grass`, a TPM tree log). Such columns keep their vegetation: generators never plan over them, and terrain never cuts them. |
| `protect` | Bit mask: 1 Strip(+500), 2 road buffer, 4 building buffer, 8 `area:highway`, 16 water, 32 landscaped (park, golf, lawn, garden, pitch), 64 developed (landcover), 128 unknown/no OSM coverage |

**Write-time column check (plugin, main thread, once per column per job, cached for that job's tick).** Every check must hold:
1. The column is not in the Strip and its `protect` bits are allowed for the op's content:
   - terrain: none;
   - vegetation: only bits within 32|64 (landscaped, developed), and only for features with confidence `mapped`;
   - agriculture: none.
2. `base_y` is known.
3. **No outside ownership in the window:** no tracked player edit, no **non-Egg** ledger placement (a Snowball road, shell, deck or approach), no `TileState`, and no entity in `y ∈ [base_y − maxCut − 1, base_y + 40]`.
4. **Surface still at baseline:** the current block at `base_y` equals `base_block`, or it is owned by a live Egg job (`ownerOf`).

Python cannot see the live world, so the generators only use `context` to plan. The plugin enforces checks 1–4 again on every cell, immediately before writing it.

## 6. Writer, ownership, undo (plugin)
- **One writer.** Egg ops go through the same `write()` record format as Snowball:
  - `type place`;
  - before and after states;
  - `feature`, `role`;
  - `content = "egg-terrain" | "egg-vegetation" | "egg-agriculture"`;
  - and `upgrade_of` when the replaced block is owned by another job.
  - Egg never writes onto Snowball-owned blocks.
  - When Snowball later replaces an Egg-owned block (for example a road over Egg-placed sand), it records `upgrade_of = <egg job>`. Undo then refuses until the dependent job is undone first. This is the existing mechanism.
- **Tick-state equivalence.** Some blocks change state through vanilla random ticks: `farmland[moisture]`, crop `age`, sapling `stage`, `leaves[distance]`. For **Egg-owned** blocks of those materials, `ownerOf`/undo compare the material plus every property except those tick properties. Leaves are always placed `persistent=true`.
- **NATURAL** (subsurface materials a `base` cut may remove below `base_y`): `sand, red_sand, sandstone, red_sandstone, smooth_sandstone, terracotta, *_terracotta, dirt, coarse_dirt, grass_block, gravel, stone, andesite, diorite, granite, tuff, clay, podzol`.
- **Retirement, the union rule.** On APPLY of `(tile, content)`, the plugin collects every live Egg placement of that content **anywhere in the tile's 16 chunks**, from a per-tile index rather than per chunk. Each one absent from the new plan, still exactly as placed (by tick-equivalence) and not tracked is restored to its before-state as a ledgered `retire` op (`upgrade_of` = the old job). Chunks the new plan no longer touches are therefore included. This fixes the Snowball plan-shrink limitation.
- **Locks.** The plugin holds one `regionLocks: Map<Long chunkKey, String jobId>`:
  - Egg acquires all 16 chunk keys atomically, or refuses ("busy");
  - Snowball `submit` and both undos refuse a chunk that is locked;
  - only one Egg prep runs per tile.
- **Cancel** stops between ticks. Everything already written is in the ledger, and is undoable like any job.
- **Undo** (`/eggtool undo [job] [confirm]`) uses the same restore rule. It restores only cells still at their recorded after-state and keeps later edits. A dry run comes first.

## 7. Plugin UX
- **Commands:** `/eggtool on | off | preview | status | cancel | prepare | content <terrain|vegetation|agriculture|all> | undo [job] [confirm]`.
- **State:** Egg mode is separate from Snowball. It is OFF after login and after restart.
- **Hatching:** while Egg mode is ON or PREVIEW, `PlayerEggThrowEvent#setHatching(false)` applies to that player. When it is OFF, eggs are vanilla.
- **Hits:** an entity hit does nothing beyond a one-line message.
- **Deduplication:** a repeated throw at the same tile while a job runs reports "already running". A repeat after completion reuses the cached plans, so ops already present count as present and nothing is re-fetched.
- **Chat:** 1–3 lines per job. The full report is a file.
- **Config keys (`egg-*`):**
  - `egg-enabled`, `egg-prep-script`, `egg-max-cut` (2), `egg-max-fill` (2);
  - `egg-max-ops-per-tile` (12000; plans over the limit are `refused_limits`);
  - `egg-blocks-per-tick` (300), `egg-place-ms-per-tick` (6);
  - `egg-prep-timeout-seconds` (600), `egg-seed` (1);
  - `egg-events-file` (BlueMap events JSONL; empty means off), `egg-baseline-world`.

## 8. Data sources (summary; details in SOURCE-INVENTORY.md and the specs)
- **OSM:** the local Nevada extract (Geofabrik, 2026-10-25).
  - The existing `clark_osm_index.sqlite` holds buildings and highways (protection).
  - A new `clark_landcover_index.sqlite` (same schema) holds landuse, natural, leisure, trees and water.
- **Terrain:** USGS 3DEP lidar class 2, ground (EPT `NV_ClarkCo_2_B22`, 2022, already cached), and/or the 3DEP 1 m DEM. The TPM projection comes from `geo.py`.
- **Vegetation:** OSM trees and tree rows; lidar (class 1, unclassified, with first-of-many returns as canopy evidence). It never determines species. ESA WorldCover 2021 v200 (10 m) gives broad context only. TPM `customTrees.json` assets go into the catalog.
- **Agriculture:** OSM polygons and crop tags first. USDA CDL (30 m, by year) is supporting evidence only.

## 9. File ownership
| owner | files |
|---|---|
| Coordinator | `AGENTS.md`, `docs/egg/ARCHITECTURE.md`, `docs/egg/SOURCE-INVENTORY.md`, `docs/egg/TESTS-v0.5.md`, `docs/ROADMAP.md`, `README.md`, `CHANGELOG.md`, `prep/egg_common.py`, `prep/egg_prep.py`, `tools/*egg*`, test harness (`C:\BTE-Migration\scripts\egg_test*.py`), server 11 |
| Terrain | `prep/egg_terrain.py`, `prep/egg_provenance.py` (baseline MCA reader → context columns), `docs/egg/TERRAIN-SPEC.md` |
| Vegetation | `prep/egg_vegetation.py`, `prep/egg_assets/vegetation_catalog.json`, `prep/egg_catalog_build.py`, `docs/egg/VEGETATION-SPEC.md`, `docs/egg/ASSETS.md` (attribution), `docs/egg/DEPENDENCIES.md` |
| Agriculture | `prep/egg_agriculture.py`, `prep/egg_landcover_index.py` (index builder, including trees for vegetation), `docs/egg/AGRICULTURE-SPEC.md` |
| Plugin | `plugin/src/main/java/org/bte/summerlin/shelltool/EggTool.java` (new), minimal hooks in `ShellToolPlugin.java` (shared lock, writer/ownership access, tick-equivalence), `plugin.yml`, `config.yml` egg keys, `docs/egg/COMMANDS.md` |
| Reviewer | `docs/egg/REVIEW.md` (read-only on everything else) |

**Generator module interface:** each of `egg_terrain`, `egg_vegetation` and `egg_agriculture` exposes `plan(ctx) -> dict`, the result of `egg_common.plan(...)`. `ctx` is a dict holding:
- `tx, tz, bounds, seed, geo` (a shared `Geo`);
- `cache` (the egg cache dir), `snow_cache` (the Snowball cache dir);
- `base_y, base_block, base_cover, protect` (numpy arrays / lists as in §5);
- `limits` = `{max_cut, max_fill, max_ops}`;
- `landcover`: a callable `(kinds) -> features in lon/lat`, or `None` when the index is missing.

Each module also has a `__main__` self-check that runs without the server.
