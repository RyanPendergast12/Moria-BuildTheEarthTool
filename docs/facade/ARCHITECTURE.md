# Facade tool (v0.6): architecture and shared contract

This file is the binding contract between the v0.6 agents. The coordinator owns it; ask the coordinator to change it.
v0.6 adds **exterior facades**: windows, mullions, backing, sills, frames, bands, parapets, canopies, columns, roof edges and simplified signage.
They are compiled from **versioned design packages** (reference-matched landmarks) or **styles** (explicitly selected, labelled fallbacks) into deterministic block plans.
The plans are applied by the existing protected writer.

**No AI at runtime.** Gameplay never calls Claude, an LLM, an image model or any vision model. During development, agents research and author **explicit files**: packages, palettes, components and styles. Runtime uses only those files, cached geometry and deterministic code.

## 1. Terms
- **Building id (`bid`):** the OSM id of the primary way or relation (`w316302064`, `r123`). A package may span several OSM ids. Its `package.id` names it (for example `red-rock-casino`), and every member id resolves to that package.
- **Part:** a vertical volume with one base and top height, for example a tower, podium or setback. Sources: an OSM `building:part`, or a package-authored polygon checked against LiDAR.
- **Face:** one exterior wall run of a part, as an ordered polyline in MC block coordinates, possibly curved or segmented. Face-local coordinates:
  - `u` = distance along the polyline from its start, in blocks (1 block = 1 m);
  - `v` = Y minus the face's `base_y`;
  - `w` = outward normal offset (0 = wall plane, +1 = one block outside, −1 = one block inside).
  - A curved face keeps a continuous `u` across segments. Its normal is per segment, or interpolated at vertices.
  - **Never** use N/E/S/W labels in place of face geometry. Labels are display names only.
- **Edge kinds:** `exterior` gets a facade. `shared` (a party wall or internal wall between parts) never gets openings. `unknown` stays unresolved unless a style is explicitly selected.
- **Evidence class**, on every planned cell and summarised per face:
  - `reference` (matched to a dated reference);
  - `measured` (LiDAR, OSM, ortho geometry);
  - `inferred` (derived from measured or reference data by a stated rule);
  - `style` (template-generated, plausible, not observed).
  - **`unresolved`** areas produce no ops. They keep the existing shell, or nothing, and are listed in the report.

## 2. Flow
```
/facadetool inspect (player target) -> plugin: block -> bid (prep "identify", cached) -> show bid, parts, package/style availability, evidence summary
/facadetool select <package-id | style:<id>>
/facadetool preview [chunk | whole]  -> prep "plan" (async, cached by sha) -> world checks (main thread, time-budgeted) -> report only
/facadetool apply   [chunk | whole confirm] -> same plan -> locks -> protected writer -> ledger -> report + BlueMap event
```
- **Prep** is `python prep/facade_prep.py <cache> identify <x> <z>` and `python prep/facade_prep.py <cache> plan <bid> (--package <id> | --style <id>) [--seed N]`. Both are deterministic. Neither reads the live world. Output goes under `<cache>/facade/`.
- **Plans are whole-building.** The plugin clips them to the selected **scope**:
  - `chunk`: the chunk holding the targeted block (the default);
  - `whole`: every chunk of the building, bounded and allowed only after a preview, which prints the envelope, chunk count and op count, then `apply whole confirm`.
  - A chunk action never becomes an unannounced whole-resort build.
- **Components are atomic.** A component instance, such as a window with its frame, sill and backing, or a door, is written only if all its cells are in scope and pass the checks.
  - Otherwise the whole instance is **deferred** (scope) or **skipped** (blocked), with the reason.
  - There are no half doors and no orphan pane connections.
- **Order independence:** applying adjacent chunks in either order gives the same world, because compilation is pure and each component is placed whole.

## 3. Cache layout (`<cache-dir>/facade/`)
```
buildings/<bid>/identity.json            members, parts, tags, footprint (MC), sources and their sha256
buildings/<bid>/geometry.json            faces (polylines, normals, u length, base/top Y, edge kind), roof, envelope; geometry_sha
buildings/<bid>/plan-<plan_key>.json     compiled plan (section 4); plan_key = sha256(package_sha|style_sha + geometry_sha + generator version + seed)[:16]
buildings/<bid>/manifest.json            latest plan per selection, timings, status
index/identify-<x>_<z>.json              memo of identify results
```

## 4. Plan contract (`facade-plan-1`, produced by `prep/facade_compile.py`)
```json
{"version":"facade-plan-1","building":"w123","members":["w123"],"selection":{"kind":"package|style","id":"...","version":"...","sha256":"..."},
 "generator":"facade_compile v0.6.0","seed":1,"geometry_sha":"...",
 "status":"ready|unresolved|no_geometry|no_package|invalid_package|refused_limits|protected|nothing_to_do",
 "reasons":["..."],
 "envelope":{"polygon_mc":[[x,z],...],"y":[lo,hi],"protrusions":[{"face":"...","u":[a,b],"depth":2,"allowance":"source id"}]},
 "chunks":[[cx,cz],...],
 "faces":[{"id":"...","part":"...","edge":"exterior|shared|unknown","evidence":"reference|measured|inferred|style|unresolved","coverage":0.0,"sources":["..."],"ops":123,"notes":"..."}],
 "components":[{"id":"c17","kind":"window-2x3","face":"...","anchor":[x,y,z],"evidence":"style","chunks":[[cx,cz]],"first":0,"count":14}],
 "unresolved":[{"part":"...","face":"...","reason":"..."}],
 "limits":{"ops":N,"max_ops":M,"exception":null},
 "stats":{}, "timings_s":{},
 "ops":[[x,y,z,"replace","minecraft:block[state]","feature","role","component|null","evidence"], ...]}
```
- **`replace`** is the only expect value in v0.6. At write time the cell must be one of:
  - (a) air or cave_air;
  - (b) **eligible owned** (section 6).
  - Anything else is skipped and reported, never forced. That covers terrain, non-owned blocks, player blocks, other buildings, roads, Egg work, block entities and entities.
- **Ordering:** components follow the op list. Inside a component, the order is backing, then frame, then glass and panes, then trim, then canopy. Columns go bottom-up.
- **`feature`** = `bid`. **`role`** is one of: `wall`, `glass`, `mullion`, `frame`, `backing`, `sill`, `band`, `parapet`, `canopy`, `column`, `door`, `roof`, `roof-edge`, `sign`, `trim`.
- **Air ops** (removing an owned shell block, for example an inset) are allowed only on eligible owned cells.
- **Valid states only:** every block string must parse as a 1.18.2 `BlockData`. `facade_compile.py` checks names against `prep/facade_assets/blocks-1.18.2.json`, and the plugin re-checks with `Bukkit.createBlockData`.
- **Multiblock components** (doors, tall plants, beds) must be complete in one component. Panes, fences, walls and iron bars carry explicit connection states computed from the full plan. Leaves are `persistent=true`. No gravity blocks are allowed without a solid block below in the same plan.

## 5. Package and style files (schema `facade-package-1`; documented in `docs/facade/PACKAGE-SCHEMA.md`, validated by `prep/facade_schema.py`)
- **Location:**
  - `packages/<package-id>/package.json` (or `.yaml`, read with `yaml.safe_load`; JSON is canonical);
  - `packages/<package-id>/EVIDENCE.md`;
  - `packages/<package-id>/sources.json`.
  - **No images in the repo.** Local reference files live outside it under `C:\BTE-Migration\facade\refs\<package-id>\`. They are referenced by relative name plus sha256.
- **Required top-level keys:**
  - `schema` (= `facade-package-1`), `generator_min`;
  - `package {id, version, kind: reference|style, title, license, attribution}`;
  - `building {osm: [...], parts: [...]}`;
  - `crs {projection: "bte-tpm-geo.py", units: "block=1m", y: "world"}`;
  - `sources [{id, kind: street|aerial|ortho|lidar|osm|doc|site-photo, date, url?, local?, sha256?, license, attribution, coverage: [faces]}]`;
  - `elevations {measured: {...}, accepted: {part: {base_y, top_y, source}}}`;
  - `palette` (a ref `lib:<id>` or inline roles);
  - `components {name: {ref: "lib:<id>"} | inline}`;
  - `faces [...]`;
  - `unresolved [...]`;
  - `envelope {outward, inward_backing, protrusions[...]}`;
  - `limits {max_ops, max_extent_m, exception?}`;
  - `seed`.
- **Face layout** is parametric: `floors {base_v, height, count|auto}`, `bays {width, residual: center|ends|absorb}`, `openings [{component, bays: "all|i:j|every n", floors: "..."}]`, `bands`, `corners`, `parapet`, `canopy`, `evidence {class, sources, confidence, coverage}`.
  - Thousands of repeated window coordinates are never written out; components and repeat rules are.
- **Safety:**
  - Packages are **data only**: no code, templates, shell, URLs fetched at runtime, or `eval`.
  - The validator checks:
    - schema and types;
    - that every id it references resolves;
    - that paths stay inside the package or `C:\BTE-Migration\facade\refs`;
    - file sizes (package ≤ 2 MB, component ≤ 64 KB);
    - numeric bounds;
    - block names and states;
    - limits.
  - Invalid → `invalid_package` with the exact path of the error, and nothing is placed.
- **Library** (`prep/facade_assets/`):
  - `palettes/*.json` (`facade-palette-1`: roles → block lists, coherent zones, notes and HueBlocks or colour references);
  - `components/*.json` (`facade-component-1`: a small `u,v,w` grid with roles or blocks, an anchor, `{facing}`/`{axis}` placeholders resolved from the face normal, a `size`, and allowed widths);
  - `styles/*.json` (`facade-style-1` = a package template with `kind: style` and `applies_to` tags).
  - Every library file is versioned. Plans record each file's sha256.
- **Massing:** `building.shell: none|existing` (default `existing`).
  - With `none` (a landmark with no shell in the world, such as Red Rock), unresolved faces still get a plain massing wall and a roof cap, labelled `measured` with no openings, so the building is closed.
  - They stay listed as detail-unresolved.
  - Red Rock v0.6 detailed scope: the tower `w316302058`, the casino/podium `w316302064` and the NW entrance. Everything else is massing only.
- **Adoption sources:**
  - `facade_prep.py <cache> adopt <bid>` writes `adopt-candidates.json` from the pilot `shells.txt` `B` cells and from the after-states of accepted detail jobs.
  - Harlo's `plan.jsonl` is empty and is not a source.
- **Size exception:** the Snowball shell limit (`MAX_BUILDING_M` = 400) stays global. A package may set `limits.max_extent_m` ≤ 800 and `limits.max_ops` ≤ 400,000 only with a written `limits.exception`. Styles cannot raise limits.

## 6. Ownership, adoption, writer, undo (plugin)
- **Eligible owned cell:**
  - Its latest live ledger placement belongs to a job whose content is `shells`, `facade` or `facade-adopt`, with **`feature` = this bid** (or a member id).
  - Its current block equals that placement's recorded after-state (tick-equivalent).
  - It is not in `tracked-edits`.
  - No later job depends on it.
  - Material equality alone is **never** ownership.
- **Adoption** (`/facadetool adopt [confirm]` on the targeted building; dry run first) makes pre-ledger work eligible.
  - Sources:
    - pilot shell cells from the accepted source plan (`shells.txt` `B` lines, plan sha in its header) inside the building envelope;
    - accepted detail jobs from their recorded before/after plans (`summerlin\detailing\jobs\<job>\original.json` + `plan.jsonl`).
  - Prep lists the candidate cells with their expected blocks. The plugin adopts only cells whose current block **equals** the expected block and that are not tracked. Every other cell is reported as ambiguous.
  - Adoption writes `place` records with `before == after == current`, `content = "facade-adopt"` and `role = "adopt"`. It changes nothing in the world.
- **Replacement** of an owned cell records `upgrade_of = <owner job>`. Undo dependencies then block undoing the owner while the facade job is live. That is the existing mechanism.
- **Retirement:** on `apply`, every live `facade` placement of this bid inside the scope chunks that is absent from the new plan is restored to its before-state, provided it is still as placed and not tracked. It is ledgered as `retire` with `upgrade_of` = the old job.
  - The union covers old and new chunks: the plugin keeps a per-bid index of all chunks with live facade placements.
  - `whole` retires everywhere.
  - `chunk` scope reports any obsolete cells left outside the scope.
  - Plans with status ≠ `ready` never retire.
- **Locks:** the facade job acquires every scope chunk in the shared `regionLocks` (value `facade:<job>`) atomically, or refuses. Snowball, Egg and all undos already refuse locked chunks.
- **Protection** (on top of section 4): the Strip polygon; roads, sidewalks and bridge clearance (cells owned by `roads`/`overpass` content, or protected road cells in `shells.txt` `R`); Egg-owned cells; block entities; entities in the cell; tracked edits; cells outside `envelope`.
- **Undo** `/facadetool undo [job] [confirm]` follows the Egg rules: dry run first; restore only cells still at their after-state; keep later edits; respect dependents. A cancelled or interrupted job stays undoable.

## 7. Plugin UX (`FacadeTool.java`)
- **Commands, with help and tab completion:**
  - `/facadetool help`
  - `/facadetool inspect`
  - `/facadetool select <package|style:<id>|none>`
  - `/facadetool preview [chunk|whole]`
  - `/facadetool apply [chunk]`
  - `/facadetool apply whole confirm`
  - `/facadetool adopt [confirm]`
  - `/facadetool status`
  - `/facadetool cancel`
  - `/facadetool undo [job] [confirm]`
  - Admin and test hooks: `/facadetool sim <uuid> <subcommand...> @ <x> <y> <z>`.
- **Target:** the block the player looks at (ray trace ≤ 160 blocks), or `@ x y z` for sim and console.
  - **Selection is per player and in memory.** It is cleared on login and restart, like Snowball and Egg modes.
  - There is no default style. A style must be selected explicitly, and its report header says `STYLE-GENERATED (not observed)`.
- **No projectile handling:** v0.6 does not bind facades to snowballs, eggs or any item.
- **Chat:** 1–3 lines. Full reports go to `plugins/SummerlinShellTool/facade-reports/facade-<job>.json`. BlueMap events go to `facade-events-file` in the existing format: `{"tool":"facade","id","status","minX","minZ","maxX","maxZ","label","t"}`.
- **Config (`facade-*`):**
  - `facade-enabled`, `facade-prep-script`, `facade-packages-dir`;
  - `facade-max-ops-chunk` (8000), `facade-max-ops-whole` (60000; a package exception may raise it to its own `limits.max_ops`);
  - `facade-blocks-per-tick` (300), `facade-place-ms-per-tick` (6), `facade-prep-timeout-seconds` (600), `facade-max-jobs` (1);
  - `facade-events-file`, `facade-seed` (1).

## 8. Geometry (`prep/facade_geometry.py`)
- **Projection:** the existing projection (`geo.py`, TPM). Footprints and parts come from the local OSM indexes. Heights come from cached 3DEP EPT (`ept_cached.py`).
  - Use the measured top (a high percentile of first returns over the part, with rooftop equipment excluded by area and outlier rules) **and** the accepted shell geometry where a shell exists (`summerlin\detailing\jobs\*\geometry.json`, shell planner rules).
  - **Never** move, stretch or raise an accepted shell to fit a pattern.
- **Faces:**
  - Polylines come from the footprint rings, simplified within 0.25 m.
  - Arcs are fitted where the ring is a sampled curve, then written back as segment lists with a continuous `u`.
  - `shared` edges are detected (coincident with another member or part).
  - Walls are voxelised with the same stepping as the shell planner, so facade cells land on the shell's wall line, including the 11.4° stepped walls.
- **Roof:** a flat parapet and edge by default. Other roof shapes are used only with evidence (`roof:shape` or a package). Rooftop machinery is never counted as occupied height.

## 8a. Interchange formats (binding for B, C and D1; D1's validator enforces them)
**`geometry.json`** (`facade-geometry-1`, written by B):
```json
{"version":"facade-geometry-1","bid":"w123","members":["w123"],"geometry_sha":"...","generator":"facade_geometry v0.6.0",
 "sources":{"osm":{"index":"...","ids":[...]},"lidar":{"dataset":"...","points":N},"shell":"accepted:<path>|snowball-rules|none"},
 "parts":[{"id":"p0","osm":"w123|null","polygon_mc":[[x,z],...],"base_y":924,"top_y":932,"height_src":"lidar p95 ...","roof":{"shape":"flat","parapet":true,"evidence":"measured"}}],
 "faces":[{"id":"p0:f0","part":"p0","edge":"exterior|shared|unknown","label":"N (display only)",
           "segments":[{"a":[x,z],"b":[x,z],"u0":0.0,"u1":12.5,"normal":[nx,nz],"curve":"arc:<id>|null"}],
           "length_u":33.1,"base_y":924,"top_y":932,
           "columns":[[x,z,u,seg,ox,oz], ...]}],
 "envelope":{"polygon_mc":[[x,z],...],"y":[lo,hi]},
 "notes":["..."]}
```
- **`columns`:** the voxelised wall-plane cells, ordered by `u`. Each holds:
  - the block column `x, z`;
  - `u`, the cell centre projected on the face;
  - `seg`, the segment index;
  - `ox, oz`, the outward grid step (−1/0/1) used for `w` offsets at that column.
- The `w` offset of a column is `(x + w·ox, z + w·oz)`.
- Corner columns belong to exactly one face. The other face's `columns` omit them, and the compiler applies the face's `corners` rule.

**Component** (`facade-component-1`, `prep/facade_assets/components/<id>.json`, written by C):
```json
{"schema":"facade-component-1","id":"window-2x3-framed","version":"1.0.0","title":"...","size":{"u":2,"v":3},
 "repeat_u":false,
 "cells":[{"u":0,"v":0,"w":0,"role":"glass"},{"u":0,"v":0,"w":-1,"role":"backing"},
          {"u":0,"v":-1,"w":1,"block":"minecraft:smooth_quartz_slab[type=top]"},
          {"u":0,"v":3,"w":1,"block":"minecraft:stone_brick_stairs[facing={facing_in},half=top]"}],
 "notes":"Detail pp.10-12 ...","license":"original (Moria)"}
```
- `u` and `v` are relative to the anchor, which is the bottom-left cell on the wall plane seen from outside. `u` grows left to right as seen from outside.
- **Placeholders:**
  - `{facing}` = the cardinal nearest to the outward normal;
  - `{facing_in}` = its opposite;
  - `{facing_left}` / `{facing_right}` = the cardinals along `u`;
  - `{axis_u}` = `x|z`.
  - Pane, fence and wall connections are **not** written in components. The compiler computes them from the full plan.
- A role is resolved through the palette. An explicit `block` overrides the palette.
- **Approved extensions (phase 1):**
  - `props` on role cells merge into the resolved block only where that block has the property (`resolve_cell()` in `prep/facade_palette_tools.py`);
  - `repeat_v` mirrors `repeat_u`;
  - the parametric `sign-text` component draws on a glyph table and is labelled simplified lettering;
  - styles use the `ground`/`upper`, `below`/`above`, `u_inset` and `replace` layout keys.

**Palette** (`facade-palette-1`, `prep/facade_assets/palettes/<id>.json`, written by C):
```json
{"schema":"facade-palette-1","id":"summerlin-stucco-tan","version":"1.0.0",
 "roles":{"wall":"minecraft:smooth_sandstone","glass":"minecraft:gray_stained_glass","mullion":"minecraft:end_rod[facing=up]", "...":"..."},
 "texture":{"wall":{"alt":"minecraft:sandstone","rule":"rows%3==2"}},
 "zones":{"podium":{"wall":"..."}},
 "notes":"...","colour_refs":[{"role":"wall","target_srgb":"#cdb38b","source":"..."}]}
```
- `texture` rules are deterministic expressions over `u`, `v` and `seed` only, from the whitelist `rows%k==r`, `cols%k==r` and `hash(u,v,seed)<p`.
- Zones are coherent material regions. There is no per-block random noise.

**Style** (`facade-style-1`, `prep/facade_assets/styles/<id>.json`, written by C): the same structure as a package's `palette` + `components` + face layout rules, plus:
- `applies_to {building: [...], max_levels, min_face_len}`;
- `defaults` for floors, bays and openings, per edge kind and per ground or upper floor.
- The compiler applies a style to every `exterior` face and labels every op `style`.

## 9. Test server
- **Disposable server 13:** `C:\BTE-Migration\13-facade-test`, port **25573**.
  - Its world is cloned from the clean-stop primary backup `shelltool\deploy-backups\20261029-115151-primary-pre-0.5.0` (never from the running primary).
  - **Only the coordinator, or the one agent it names, may start it or write to it.**
- The primary server (25566), the legacy server (25565) and test servers 07–12 are read-only for everyone.

## 10. File ownership
| owner | files |
|---|---|
| Coordinator | `AGENTS.md`, `docs/facade/ARCHITECTURE.md`, `docs/facade/TESTS-v0.6.md`, `docs/ROADMAP.md`, `README.md`, `CHANGELOG.md`, `tools/*facade*`, the test harness (`C:\BTE-Migration\scripts\facade_test*.py`), server 13 |
| A. Reference | `docs/facade/REFERENCES.md`, `packages/*/EVIDENCE.md`, `packages/*/sources.json`, `packages/red-rock-casino/package.json` and `packages/harlo-w348127357/package.json` (authored in phase 2), local refs under `C:\BTE-Migration\facade\refs\` |
| B. Geometry | `prep/facade_geometry.py`, `prep/facade_identify.py`, `docs/facade/GEOMETRY-SPEC.md`, measurement outputs under `C:\BTE-Migration\facade\measure\` |
| C. Palette/components | `prep/facade_assets/**` (palettes, components, styles, `blocks-1.18.2.json`), `prep/facade_palette_tools.py`, `docs/facade/PALETTES.md`, `docs/facade/COMPONENTS.md`, `docs/facade/ASSETS.md` (licences) |
| D1. Compiler | `prep/facade_schema.py`, `prep/facade_compile.py`, `prep/facade_prep.py`, `prep/facade_common.py`, `docs/facade/PACKAGE-SCHEMA.md`, `docs/facade/COMPILER-SPEC.md` |
| D2. Plugin | `plugin/src/main/java/org/bte/summerlin/shelltool/FacadeTool.java`, minimal hooks in `ShellToolPlugin.java` and `EggTool.java` (lock messages), `plugin.yml`, the `config.yml` facade keys, `docs/facade/COMMANDS.md` |
| E. Reviewer | `docs/facade/REVIEW.md` (read-only on everything else) |
