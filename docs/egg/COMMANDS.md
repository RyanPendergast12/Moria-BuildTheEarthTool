# Egg tool (v0.5): commands, permissions, config, messages, reports

The Egg tool lives in the same plugin as Snowball (`SummerlinShellTool` 0.5.0). The contract is in `ARCHITECTURE.md`.

## How it works
- `/eggtool on` (or `preview`), then throw an egg at a **block**. The hit selects tile `tx = floorDiv(x>>4, 4)`, `tz = floorDiv(z>>4, 4)`, which is blocks `64tx..64tx+63` × `64tz..64tz+63`.
- An entity hit only sends one message.
- While Egg mode is ON or PREVIEW, your eggs never hatch (`setHatching(false)`, 0 hatches). When it is OFF, eggs are vanilla.
- Egg mode is separate from Snowball mode. It is OFF after every login and every restart. Your content choice is kept until a restart (default `all`).

## Commands (`/eggtool`, permission `summerlin.shelltool.use`)
| command | effect |
|---|---|
| `on` | ON: the next egg block hit applies the plans to that tile |
| `preview` | PREVIEW: same checks and counts, but writes nothing and records nothing in the ledger |
| `off` | OFF: eggs are vanilla |
| `content terrain\|vegetation\|agriculture\|all` | Selects what a throw generates |
| `status` | Your mode and content, active Egg jobs, the prep queue, and the tile under you with its block bounds, Strip protection and cache state |
| `prepare` | Queues background preparation of the tile under you (lower priority than throws). Nothing is placed. |
| `cancel` | Cancels your Egg jobs (all jobs for admins) between ticks, plus your queued or running preparations. Blocks already written stay in the ledger and can be undone. |
| `undo [job] [confirm]` | Without `confirm`, a dry run. Egg jobs only (`/shelltool undo` handles Snowball jobs only). With no job given, it picks your latest Egg job. See "Undo" below. |

## Admin and console test hooks (`summerlin.shelltool.admin`)
Each hook runs the same code as the real interaction.

| command | effect |
|---|---|
| `simset <uuid> <off\|preview\|on> <content>` | Sets a player's Egg mode and content |
| `simthrow <uuid> <x> <y> <z>` | A block hit by that player's current mode and content. It follows the same path as a real hit, including cooldown, refusals and locks. |
| `simentityhit <uuid>` | The entity-hit path (one message, nothing generated) |
| `simhatch <uuid>` | Reports what the hatching decision would be for that player's mode (the same function the event handler uses) |
| `simprepare <tx> <tz> <content>` | Queues background-priority preparation of a tile |

Admins can also undo and cancel other players' jobs.

## Refusals (nothing fetched or written)
| situation | message |
|---|---|
| Egg tool disabled | `The Egg tool is disabled (egg-enabled: false).` |
| Plugin data not loaded yet (Strip unknown) | `Data not loaded yet ... refused.` |
| Wrong world | `Denied: only world '<world>' is allowed.` |
| Any of the 16 chunks in the Strip | `PROTECTED: tile tx,tz (...) touches the Strip exclusion.` |
| Throwing too fast | `Cooldown - wait a moment.` (`egg-cooldown-ms`) |
| An Egg job already on this tile | `Job <id> is already running for tile tx,tz (<stage>).` |
| A Snowball job in any of its chunks | `Tile tx,tz is busy: a ShellTool job is running in chunk cx,cz.` |
| Too many jobs | `Queue full (N active Egg jobs).` (`egg-max-jobs`) |

Snowball refuses in the other direction too: while an Egg job holds a tile, `/shelltool` throws and both undos refuse its chunks (`Egg job <id> is working on this tile`).

## Job flow
1. **Lock:** the job locks all 16 chunks.
   - Before any preparation (throws and `prepare`), the plugin loads all 16 tile chunks **one at a time** (`getChunkAtAsync`).
     - Missing chunks are generated only if `egg-generate-missing` is on.
     - Nothing is loaded or generated once the plugin is stopping, and pending loads stop with the job.
     - Anchor chunks of partial features are only read if they are already generated.
   - It then waits until the baseline captures of any newly generated chunks are on disk.
   - The prep process gets `MORIA_EGG_CAPTURED=<dataFolder>/egg-baseline`, and `MORIA_BASELINE_WORLD` if `egg-baseline-world` is set.
2. **Cache:** the plugin reads `<cache>/egg/tiles/<tx>_<tz>/manifest.json`. It is reused when:
   - `pipeline` equals `egg_prep v0.5.0`;
   - every requested section exists;
   - no section is `provider_failure` or `retry: true`.
3. **Preparation:** otherwise the tile is prepared **once**: `python egg_prep.py <cache-dir> tx tz --content c --seed N --max-cut N --max-fill N --max-ops N`.
   - It runs in a dedicated queue: throws first, then `prepare`. There is one task per tile, and content merges to `all` while queued.
   - The log goes to `<cache>/egg/prep-logs/<tx>_<tz>.log`.
   - On timeout or cancel, the process tree is killed (the interpreter that the venv launcher started, then the launcher), and the result is reported as a provider/timeout failure.
   - Whatever the preparation produced is then reported. It is not retried.
4. **Plans:** for each content, the plugin reads `terrain.json`, `vegetation.json` and `agriculture.json`.
   - A status other than `ready` is reported as is (`no_coverage`, `provider_failure`, `low_confidence`, `unsupported`, `protected`, `refused_limits`, `nothing_to_do`).
   - Ops outside the tile are dropped and counted.
   - More ops than `egg-max-ops-per-tile` makes that content `refused_limits`.
5. **Apply:** on the main thread, under `egg-blocks-per-tick` and `egg-place-ms-per-tick`, with chunk tickets on the tile and on the anchor chunks of partial features.
   - Retirements run first. Then each op is re-checked immediately before it is written (ARCHITECTURE §4–§6):
     - The column check runs once per tick per column. All of these must hold:
       - not in the Strip, and `base_y` is known;
       - nothing foreign in `[base_y-maxCut-1, base_y+40]` (tracked edit, ShellTool placement, block entity, entity);
       - every cell in `[base_y+1, base_y+40]` is air, the recorded `base_cover` state for that height, or Egg-owned. Otherwise the column fails with `column changed above the ground since baseline`; this catches untracked WorldEdit, `/setblock` and piston builds;
       - the surface at `base_y` is still at baseline, or Egg-owned.
     - **Vegetation and agriculture** ops also skip a column holding any live Egg **terrain** placement (`surface changed by Egg terrain; replan`).
     - **Height ranges:** `base` ops only in `base_y-maxCut..base_y`; terrain `air` ops only in `base_y+1..base_y+maxFill`; other `air` ops only above `base_y`.
     - **Protect bits:** terrain and agriculture need 0. Vegetation allows only bits 32 and 64, and only for `mapped` features.
     - **Expect:**
       - `air` needs air or cave_air;
       - `base` needs `base_block` at `base_y`, or a NATURAL material below it, and the cell must not be changed by an earlier Egg job;
       - `egg` needs the block to still be owned by an Egg job.
     - A target already present (tick-equivalent for Egg blocks) counts as present.
     - `*-remote` ops are written only if the feature anchor already exists (`anchor_block` if the feature gives one, otherwise any log). Otherwise they count as **deferred**.
6. **Retirement (union rule):** every live Egg placement of an applied content anywhere in the tile is restored to its before-state as a ledgered `retire` (`upgrade_of` = old job). Retirements run newest first. A placement is retired when all of these hold:
   - the plan's status is `ready` or `nothing_to_do`, and the plan is not `"complete": false` (otherwise the report notes `retirement skipped: incomplete plan`);
   - the cell is outside the Strip, has no entity, and passes the protect bits;
   - the column-safety test passes (see Undo); otherwise the cell is kept as `kept: later work depends on this column`;
   - the new plan no longer holds that position with that state;
   - the block is still as placed (tick-equivalent);
   - it is not tracked and not a block entity.
7. **Column-atomic:** once any op in a column (x,z) is skipped (retire refusals included), every later op of that column in the same job is skipped as `column incomplete: earlier op skipped`. A crop never lands on soil whose farmland op was refused, and fill and plant layers never float.
8. **Report:** a report is written and the lock is released.

## Baseline capture (`egg-capture-baseline`)
- On `ChunkLoadEvent` (priority LOWEST) with `isNewChunk()` in the configured world, the plugin takes a `ChunkSnapshot` on the main thread, but only for a chunk that was **not FULL before this load** (absent, or only a proto chunk on disk):
  - **Chunks Egg loads** (`loadTile`, apply): `World#isChunkGenerated` is recorded on the main thread just before each request. In Paper 1.18.2 it is true only for a FULL chunk, loaded or on disk.
  - **Chunks loaded any other way** (players): the plugin reads the chunk's `Status` from its region file. `full` / `minecraft:full`, an unreadable entry or an unknown status means no capture.
  - Converted or purchased chunks stored FULL are never captured. Proto chunks that TerraPlusMinus saved next to an earlier tile are captured when they are completed.
  - **Converted-world regions** (`egg-capture-protected-regions`, default `egg-converted-regions.txt` in the data folder): one region file name per line, `#` starts a comment.
    - The converted world also has thousands of **proto** chunks (`heightmaps`, `features`, `liquid_carvers`) that contain converted builds. So in a listed region, only chunks that were **absent** from the region file before the load are captured, never proto or FULL ones.
    - For chunks Egg requests, that absence is recorded just before the request; for other chunks, it is read at load time.
    - **Fail-safe:** if the list is configured but missing or unreadable, nothing is captured in any region file that existed at plugin start, and a warning is logged.
    - `/eggtool status` shows the number of protected regions, or FAIL-SAFE.
- One async capture thread (queue 4096; beyond that the chunk is dropped with a warning) records, per column:
  - `base_y`: the highest NATURAL block, scanning down from the top non-air block;
  - `base_block`: that block's state;
  - `base_cover`: the non-air block directly above it, or null.
- Output is `plugins/SummerlinShellTool/egg-baseline/<cx>_<cz>.json` = `{"version":"egg-baseline-1","chunk":[cx,cz],"captured","source":"new-chunk","world","base_y","base_block","base_cover"}`.
  - Arrays are `[16][16]`, indexed `[z&15][x&15]`.
  - An existing file is never overwritten (first capture wins).

## Undo
- `/eggtool undo [job]` dry-runs first. `confirm` applies it.
- It restores only cells still at their recorded after-state, and never a cell with a tracked player edit (Snowball undo too). For Egg content, tick-only changes such as farmland moisture, crop age, sapling stage or leaf distance still count as unchanged.
- A cell written twice in one job (retire, then the new op) is restored newest first.
- **Column safety (Egg undo and retire).** A cell is kept, and counted as `kept (later work depends on its column)`, if any of these is in its column window `[y-1, y+40]`:
  - a tracked edit;
  - a live ShellTool placement;
  - a non-air block that is not Egg-owned by this job or an older Egg job, not the recorded `base_cover`, and not natural ground below the cell.
- The lock and dependents checks run again on the main thread right before restoring, after the chunk loads. If they changed, the undo aborts and nothing changes.
- It refuses while a later job has upgraded the job's blocks (for example a Snowball road over Egg sand: undo that first).
- It also refuses while any of the job's chunks is locked or has a running job.

## Chat (per job)
```
[Egg] Job <id> (APPLY, all): tile tx,tz (x a..b, z c..d)                       <- on throw
[Egg] Tile tx,tz: not prepared yet - preparing (queue position 1). ...          <- only if preparation is needed
[Egg] Job <id> DONE APPLY all tile tx,tz (x a..b, z c..d)
  terrain ready: 812 placed, 4 present, 37 skipped, 3 retired | vegetation no_coverage | agriculture nothing_to_do
  Report: plugins/SummerlinShellTool/egg-reports/egg-<id>.json
```

## Report (`plugins/SummerlinShellTool/egg-reports/egg-<job>.json`)
- **Job:** `job`, `tool` ("egg"), `mode`, `content`, `cancelled`, `impact`.
- **Tile:** `tile` [tx,tz], `bounds.chunks` and `bounds.blocks` (minX/minZ/maxX/maxZ, inclusive).
- **Manifest:** `pipeline`, `prepared`.
- **`contents.<c>`:**
  - `status`, `reasons`, `sources`;
  - `ops`, `ops_outside_tile`;
  - `placed` (PREVIEW: `would_place`), `already_present`, `skipped`, `skipped_by_reason`;
  - `deferred`, `retired` (PREVIEW: `would_retire`).
- **`features[]`:** the plan feature (id, kind, info, partial, anchor, confidence, ...) plus `content`, `cells`, `placed`/`would_place`, `already_present`, `skipped`, `deferred`.
- **`timings`:**
  - `tile_load_and_capture_ms`, `queue_wait_s`, `preparation_s`, `plan_load_ms`;
  - `placement_ms`, `max_tick_ms`, `total_s`;
  - the manifest `timings`, if present.
- **Diagnostics:** `notes`, `errors`, and `skip_examples` (up to 12).

## Ledger records (`ledger.jsonl`, shared with Snowball)
- `{"type":"job","job","owner","world","tile":[tx,tz],"content":"egg-<c>","tool":"egg"}` is written when an APPLY starts.
- `{"type":"place","job","world","x","y","z","before","after","feature","role","content":"egg-terrain|egg-vegetation|egg-agriculture","upgrade_of"?}` is written per block. Retirements have `role: "retire"`.

## BlueMap events (`egg-events-file`, JSONL; empty = off)
One line per status change: `{"tool":"egg","id":<job>,"status":"queued|running|completed|failed","minX","minZ","maxX","maxZ","label":"<content> <mode>","t":<epoch s>}`.
- `failed` means the job was cancelled, or no requested content was `ready` or `nothing_to_do`.
- IO errors only log a warning.

## Config keys (`config.yml`)
| key | default | meaning |
|---|---|---|
| `egg-enabled` | true | Master switch |
| `egg-prep-script` | `...\prep\egg_prep.py` | Orchestrator, run with `prep-python` |
| `egg-baseline-world` | '' | Passed to the prep as `MORIA_BASELINE_WORLD` (empty = egg_common default) |
| `egg-prep-workers` | 1 | 1..2 dedicated prep threads |
| `egg-max-prep-queue` | 16 | Bound for `prepare` / `simprepare` tasks |
| `egg-prep-timeout-seconds` | 600 | Prep process timeout |
| `egg-max-jobs` | 2 | Concurrent Egg jobs |
| `egg-max-cut` / `egg-max-fill` | 2 / 2 | Passed to the generators. `max-cut` also sizes the column window. |
| `egg-max-ops-per-tile` | 12000 | Larger plans are `refused_limits` |
| `egg-blocks-per-tick` / `egg-place-ms-per-tick` | 300 / 6 | Placement budget per tick |
| `egg-seed` | 1 | Deterministic generator seed |
| `egg-cooldown-ms` | 3000 | Minimum time between throws |
| `egg-events-file` | '' | BlueMap events JSONL |
| `egg-capture-baseline` | true | Capture newly generated chunks as baseline provenance (below) |
| `egg-capture-protected-regions` | `egg-converted-regions.txt` | Converted-world region list (absent chunks only are captured there); missing list = fail-safe |
| `egg-generate-missing` | true | Generate missing tile chunks, one at a time per job. **Stop the server only when `/eggtool status` shows no Egg jobs**: TerraPlusMinus generation can hang a server that is stopping. `false` = a tile with an ungenerated chunk fails. |

The plugin also reuses these Snowball keys: `world`, `prep-python`, `cache-dir` (the egg cache is `<cache-dir>/egg`) and `required-server-port`.
