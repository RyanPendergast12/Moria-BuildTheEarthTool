# Facade tool (v0.6): commands

`/facadetool` places compiled facade plans on the building you look at. Permission: `summerlin.shelltool.use` (allowed players), or `summerlin.shelltool.admin` (ops and console). No item or projectile is involved. Contract: `ARCHITECTURE.md` sections 2, 4, 6 and 7.

**Target:** the block you look at, ray-traced up to 160 blocks. Console and tests use `sim … @ x y z`.

**Selection:** per player and in memory only. It is cleared on login and on restart. The default is **`auto`** (automatic facades, `AUTO.md`), unless `facade-auto-enabled: false`; a package or style is an optional override.

**Automatic facades (`auto`):** `preview` runs prep `auto <bid>` off the server thread (same worker, queue, timeout and cancel as `plan`). Chat shows each stage from `buildings/<bid>/auto-progress.json` (resolving, fetching evidence, associating, analyzing, materials, compiling), polled off the main thread every 0.5 s. Then a summary: mode, retrieval/association/analysis/compile timings, cost and provider status, and one entry per face with its evidence counts. Labels:
- `observed`: image-derived ops. Only these are called observed.
- `inferred (not observed)`: extended from an observed rhythm (e.g. bays across an occluded span), or observed with low confidence.
- `STYLE-GENERATED (not observed)`: the fallback style (`facade-fallback-style`) on faces with no accepted imagery. With `facade-fallback: none` those faces get no ops.
- `measured`: LiDAR/shell.

The mode is `evidence` (every resolved face observed), `procedural` (none observed) or `mixed`. Reports add `label`, `auto_mode`, `auto_faces`, `auto_providers`, `auto_cost` and `auto_ops_by_evidence`; `by_evidence` counts each class separately. Then the normal preview/apply flow applies unchanged.

## Commands
| command | what it does |
|---|---|
| `help` | the command list |
| `inspect` | identifies the targeted building (prep `identify`, cached). Shows: building id, members, parts, packages and styles available, evidence summary, facade cells already recorded |
| `select auto` / `select <package-id>` / `select style:<id>` / `select none` | chooses what `preview`/`apply` compile. `auto` (also `none`) returns to the default automatic facade. A style is labelled `STYLE-GENERATED (not observed)` in chat and in reports |
| `preview [chunk\|whole] [refresh]` | compiles (prep `auto` or `plan`, async; `refresh` re-fetches evidence and re-analyses, auto only), clips to the scope and checks every cell against the live world. **Writes nothing.** `whole` prints the chunk and op counts and arms `apply whole confirm` for 10 minutes |
| `apply [chunk]` | places the plan inside the targeted block's chunk |
| `apply whole confirm` | places it in every chunk of the building. Needs a `preview whole` of the **same plan key** by the same player in the last 10 minutes. Bounded by `facade-max-ops-whole`, or by the package's written `limits.exception` (`limits.max_ops`, at most 400,000) |
| `adopt` (dry run) | runs prep `adopt <bid>`, which writes `adopt-candidates.json` (the recorded shell: pilot `shells.txt` cells and accepted detail-job after-states of the building's members). Then it checks every candidate against the live world and **writes nothing**. A cell is adopted only if it is listed in the recorded shell (candidate source) **and** the live block exactly equals the recorded block state **and** it has no ledger owner **and** it is not a tracked player edit, a block entity, an accepted pilot road, in the Strip, or in an unloaded chunk. Ownership is never inferred from material alone. Chat shows: how many cells would be adopted, counts by block type (top 6) and by source, refused counts by reason, the bounding box, 3 example coordinates and the CSV path. `plugins/SummerlinShellTool/facade-reports/<job>-adopt-cells.csv` lists every candidate: `x,y,z,block,face_or_part,source,verdict` (`block` = the recorded state; `verdict` = `adopt`, `already_owned` or `refused:<reason>`; `face_or_part` is empty until the candidate file supplies it; `source` is the candidate file's sources unless a cell names its own) |
| `adopt confirm` | records the dry run's cells as owned (`content=facade-adopt`, `role=adopt`, before == after). **Nothing changes in the world.** Needs your latest `adopt` dry run, on the same building and the same candidate file, in the last 10 minutes. It adopts **only** cells that dry run listed and that still pass every check now. A dry-run cell that no longer passes is refused with its reason; a cell adoptable now but not in the dry run is refused as `not in your dry run`. Chat reports `ADOPTED n of the m cell(s) your dry run listed` and how many differ; the CSV has the per-cell verdicts |
| `adopt show` | outlines your latest dry run's would-adopt cells with particles that only you see, for 20 s (a flash every 0.5 s, at most 1000 cells per flash, sampled in rotation when there are more; loaded chunks only). Writes nothing |
| `status` | your selection, running jobs, prep queue, Facade locks |
| `cancel` | cancels your running jobs and preparations (admins: all). Written cells stay in the ledger and stay undoable |
| `undo [job] [confirm]` | dry run first. Restores cells still at their after-state; changed cells are kept. Refused while a later job depends on the job's cells (undo newest first) or while its chunks are locked. Survives restarts. Runs as a time-budgeted Facade job that holds its chunks locked while writing, with a report. A cell that carries attached player work or an entity (the same checks as apply) is kept and reported as `kept (attached work)`. An undo that keeps such a cell, cannot load a chunk without generation, or is cancelled is **PARTIAL**: no `undo-done` is written and the job stays live. Running the undo again after the work is moved finishes it |
| `sim <uuid> <subcommand…> @ <x> <y> <z>` | admin/console: runs any subcommand as that player with that target |
| `sim <uuid> simfail <n>` | admin test hook: the next component fails its n-th write re-check (one shot), to show the atomic rollback |

## Statuses (chat line 2 and `status` in the report)
| status | meaning |
|---|---|
| `ready` | placed (or would place) |
| `nothing_to_do` | no ops, or no adoption candidates |
| `unresolved` | not enough evidence; nothing placed |
| `no_geometry` | no footprint or heights for this block |
| `no_package` | no package with that id for this building |
| `invalid_package` | package failed validation |
| `refused_limits` | more ops in scope than the limit |
| `protected` | the target chunk is in the Strip |
| `provider_failure` | prep failed, timed out (`facade-prep-timeout-seconds`), printed no status line, the plan was unreadable, or an internal error ended the job (locks released, report written) |
| `needs_preview` | `apply whole confirm` / `adopt confirm` without the matching dry run |
| `busy` | a scope chunk is held by a Snowball, Egg or Facade job |

## The writer (per cell, re-checked immediately before each write in the same tick)
A cell is written only if it is one of:
- (a) air or cave_air;
- (b) **eligible owned:**
  - its latest live ledger placement has content `shells`, `facade` or `facade-adopt`;
  - its `feature` is this building or a member;
  - the current block equals the recorded after-state. The comparison is tick-equivalent. For Facade panes, iron bars, fences, walls and stairs it also ignores the neighbour-driven `north`/`east`/`south`/`west`/`up` and stairs `shape` (`waterlogged` still counts). The same rule applies to retirement and undo;
  - it is not a tracked edit.

  Replacing an owned cell records `upgrade_of` = the owner job.

Everything else is **skipped and reported by reason**:
- terrain or non-owned blocks, and cells whose latest placement belongs to another building (air included);
- roads, overpass, Egg or legacy (content-less) work;
- accepted pilot road cells;
- block entities;
- `attached` (when replacing, air-ing or retiring a non-air cell; also when undo restores one): a neighbour on one of its 6 faces that physically depends on this cell, and that is not an eligible owned cell of this building or a cell of the same instance. Depends means: fixed to this face (wall torches, wall signs, wall banners, wall heads, ladders, tripwire hooks, buttons and levers on walls or ceilings, cocoa, vines and glow lichen on this face, amethyst buds); standing on it (the cell is directly below: torches, signs, banners, carpets, rails, redstone, pressure plates, snow layers, plants, doors, beds, candles, any other non-full block); hanging from it (the cell is directly above: hanging lanterns, hanging roots, spore blossoms, cave and weeping vines, pointed dripstone); or a gravity block above it (sand, gravel, concrete powder, anvils). Bells and any other non-full block with a facing count on every face. A free-standing neighbour beside the cell is **not** attached: full or solid blocks (stone, concrete, glass, slabs, stairs, fences, panes), containers and other block entities with a solid body (chests, barrels, furnaces, hoppers, lecterns) and trapdoors;
- `attached: entity …`: any entity, including item frames, glow item frames and paintings, inside the cell box inflated by 0.1;
- tracked edits;
- the Strip;
- `envelope`: the compiler's exact rule. The cell centre must lie inside `polygon_mc` or a `parts_mc` piece, not inside any `holes_mc` ring, with y within `envelope.y`;
- ungenerated chunks (Facade never generates chunks);
- `edge of the generated world`: a scope chunk is loaded and held only if every chunk within 2 of it is already generated (FULL on disk, or loaded). A held chunk gets a plugin ticket at entity-ticking level, and the server would FULL-generate any missing chunk within 2. Cells in other chunks are skipped and counted in the report notes.

**Main-thread budget:** every main-thread step runs under `facade-place-ms-per-tick`. That covers scope building, clipping, the retirement scan, per-chunk load callbacks, placement, adoption checks, retirement, undo and ticket release. The report file and BlueMap events are written off the main thread. A component is never split across ticks for writing. One with more than 500 cells has its pre-check spread over ticks, and its writes, each re-checked, happen together in a fresh tick. Chunks load one at a time and are ticketed as they load. Report timings: `max_tick_ms` (the longest main-thread step of the job, wall clock), `max_tick_excl_gc_ms` (the same without JVM GC pauses that fell inside a step, i.e. the plugin's own work), `max_gc_pause_in_step_ms`, `max_placement_tick_ms` and `server_max_tick_interval_ms` (the longest gap between server ticks while the job ran; 50 means no lag). Before a cell is checked, its four horizontal neighbour chunks must be loaded (requested asynchronously, never generated); the job waits instead of reading an unloaded chunk synchronously. Plans are held as primitive arrays, and envelope and Strip tests are rasters built off the main thread. A step over 3× the budget is logged as `slow tick` or `slow main-thread step`.

**Components** (ops sharing a component id) are all-or-nothing:
- An instance with a cell outside the scope is **deferred**.
- An instance with a blocked cell is **skipped**.
- A cell that fails its re-check mid-instance restores the cells already written in the same tick. Only complete instances are ledgered.
- A component that crosses a chunk border is therefore placed only by `whole`.

Plain ops are per cell.

**Retirement:** on a `ready` plan, live `facade` cells of this building that the new plan no longer holds are restored inside the scope to their **pre-facade** state, ledgered as `role=retire` with `upgrade_of` = the latest facade job. The plugin walks back through the cell's consecutive facade placements, so A→B→C restores what was there before A. A retired cell holds a pre-facade state and is no longer a retirement candidate. A per-building index covers old and new chunks. Cells outside a `chunk` scope are reported as `obsolete_outside_scope`.

**Locks:** `apply` and `adopt confirm` take every scope chunk in the shared region locks (`facade:<job>`) at once, or refuse. Snowball, Egg and every undo refuse locked chunks and name the holder.

## Prep interface (what the plugin reads)
The plugin runs `prep-python facade-prep-script <cache-dir> identify <x> <z>`, `… plan <bid> --package <id>|--style <id> --seed N`, `… auto <bid> [--member <id>]… --fallback procedural|none --fallback-style <id> --seed N [--refresh]`, or `… adopt <bid>`.
- It sets the environment variable `MORIA_FACADE_PACKAGES=facade-packages-dir`, and the `auto` settings as `MORIA_FACADE_PROVIDERS`, `MORIA_FACADE_ANALYZER`, `MORIA_FACADE_OLLAMA_MODEL`, `MORIA_FACADE_SECRETS_FILE` (a path, never a secret), `MORIA_FACADE_REFS_DIR`, `MORIA_FACADE_PROVIDER_TIMEOUT`, `MORIA_FACADE_MAX_REQUESTS`, `MORIA_FACADE_AI_MAX_USD_JOB` and `MORIA_FACADE_AI_MAX_USD_DAY`.
- It uses one worker. Identical requests share one process.
- The process is killed after `facade-prep-timeout-seconds`.
- The output goes to `<cache>/facade/prep-logs/`.
- The **last stdout line that is a JSON object with `status`** is the result. Keys read:
  - `bid`/`building`, `members`, `name`, `parts`, `packages`, `styles`, `evidence`, `reasons`;
  - `plan` (absolute, relative to `<cache>`, or relative to `buildings/<bid>/`), or `plan_key`;
  - for `auto`: `mode`, `faces` (`status`, `plan_evidence`, `images`, `confidence`, `ops_by_evidence`, `why`), `providers`, `timings_s` (`retrieval_s`, `association_s`, `analysis_s`, `materials_s`, `compile_s`, `plan_cached`), `cost`, `autospec`, `autospec_cached`.
- The plan file's own `status`, `reasons` and `plan_key` win.

## Reports and events
- **Report:** `plugins/SummerlinShellTool/facade-reports/facade-<job>.json`. It holds:
  - status and reasons, selection and label;
  - scope and chunk list, envelope, faces (per-face evidence, coverage, ops);
  - placed/would-place, present, upgraded;
  - skipped and deferred by reason, components placed/skipped/deferred;
  - retired, obsolete outside scope, counts by evidence class;
  - timings (identify, plan queue and prep, chunk load, placement, max tick, total);
  - examples, the identify result and the prep status line.
- **Adoption reports add:** candidates source and sha, adopted/would adopt, already owned, refused (`ambiguous_by_reason`), `adopt_by_block`, `adopt_by_source`, `adopt_box` [minX, minY, minZ, maxX, maxY, maxZ], `dry_run_cells` (confirm), and `cells_csv`.
- **BlueMap events:** one JSONL line per status change (`queued`, `running`, `completed`, `failed`) to `facade-events-file`, in Egg's format: `{"tool":"facade","id","status","minX","minZ","maxX","maxZ","label","t"}`, with `label` = `<content> <mode>`. Content is the selection (`package:<id>` / `style:<id>`) or `adopt`; mode is `preview` or `apply`. The bounds are the scope chunks in blocks. Undo emits no events, as with Egg.
- **Chunk tickets:** Snowball, Egg and Facade share the plugin's one ticket per chunk through a reference count, so one tool releasing never drops a chunk another still holds.
- **Preparation:** identical prep requests share one process. `/facadetool cancel` stops a prep only when no other job or inspect is still waiting on it.

## Config (`facade-*`)
| key | default |
|---|---|
| `facade-enabled` | `true` |
| `facade-prep-script` | `…\prep\facade_prep.py` |
| `facade-packages-dir` | `…\packages` |
| `facade-max-ops-chunk` | 8000 |
| `facade-max-ops-whole` | 60000 |
| `facade-blocks-per-tick` | 300 |
| `facade-place-ms-per-tick` | 6 |
| `facade-prep-timeout-seconds` | 600 |
| `facade-max-jobs` | 1 |
| `facade-events-file` | `''` (off) |
| `facade-seed` | 1 |
| `facade-auto-enabled` | `true` (auto is the default selection) |
| `facade-fallback` | `procedural` (`none`: unobserved faces get no ops) |
| `facade-fallback-style` | `summerlin-stucco-retail` |
| `facade-providers` | `local,mapillary,panoramax,osm` |
| `facade-analyzer` | `builtin-cv` (`ollama` selectable once installed) |
| `facade-ollama-model` | `''` |
| `facade-secrets-file` | `C:\BTE-Migration\secrets\mapillary.token` (path outside the repo: bare token, JSON or `MAPILLARY_ACCESS_TOKEN=`; never a secret value) |
| `facade-refs-dir` | `''` (prep default) |
| `facade-provider-timeout-seconds` | 20 |
| `facade-max-requests-per-job` | 40 |
| `facade-ai-max-usd-per-job` / `facade-ai-max-usd-per-day` | 0 / 0 (never spend) |
