# Facade compiler specification (v0.6), owner: D1

`prep/facade_compile.py`: `compile(geometry, selection, assets, seed) -> facade-plan-1`.
- It is a pure function. It does no I/O, reads no clock and uses no randomness except `det()` (sha256-based).
- The same inputs always give the same plan, byte for byte. `timings_s` stays `{}` in the plan; prep timings go to `manifest.json`.
- Tests: `python prep/facade_selftest.py`.

## 1. Inputs and keys
- `geometry`: `facade-geometry-1` from B. Faces with `columns [x, z, u, seg, ox, oz]`.
  - **Row convention:** wall rows run from `base_y` to `top_y` **inclusive**. The shell puts its roof and roof-edge at `top_y`. `H = top_y − base_y + 1`.
  - `envelope.y[1]` may be `top_y + 1`. B adds one row above measured tops for a parapet.
  - **Edge kinds:** `exterior`, `shared`, `party` and `unknown`. `party` (v0.6.1) is a wall coincident with a separate, non-member OSM building. It is treated exactly like `shared` everywhere: no openings, no canopies, no protrusions (validator), and no massing wall or parapet. The neighbour owns it.
  - `envelope.holes_mc` (v0.6.1) lists enclosed neighbours and courtyards. A point inside a hole is outside the envelope.
- `selection`: from `facade_schema.load_selection`. It holds the validated document, `sha256` (the package or style file) and `inputs_sha`.
  - `inputs_sha` is the sha of the map {every file used: sha256}. That covers the package or style, its palette, its components and the blocks file.
- **`plan_key`** = `sha256(inputs_sha | geometry_sha | generator | seed)[:16]`. A change to any library file or to the generator gives a new key.

## 2. Face selection
| selection | edge | result |
|---|---|---|
| package | listed in `unresolved` (or `*`) | unresolved |
| package | `unknown` | unresolved ("select a style or resolve the geometry") |
| package | `exterior`/`shared`/`party` with a layout (`face` id, or `*` for exterior only) | laid out; `shared` and `party` never get openings or canopies |
| package | no layout | unresolved |
| package | layout evidence coverage < 0.5 or confidence < 0.3 | unresolved ("insufficient evidence") |
| style | `exterior` | `defaults.exterior` |
| style | `shared` or `party` | `defaults.shared`: `{"wall_only": true}` (or nothing) → no ops; a LAYOUT there → laid out without openings |
| style | `unknown` | `defaults.unknown`: a LAYOUT, or `exterior` when absent (with a per-face note); `{"skip": true}` → unresolved |
| style | more floors than `applies_to.max_levels` | unresolved |

Unresolved faces produce no ops. The one exception is massing (§9). They are listed in `unresolved` and summarised as evidence `unresolved`.
- **Evidence per op:**
  - a style gives `style` on every op;
  - a package gives the face's `evidence.class`;
  - massing gives `measured`.

## 3. Floors and bays (Detail p.19)
- **Parapet:** its height comes off the top: `usable = H − parapet height`.
- **Floors:** they stack from `base_v`. The first uses `ground_height` (default `height`). A floor that would cross `usable` is not created.
  - The building is **never stretched**. A clipped `count` is reported.
- **Bays:** `n = (N − 2·margin) // width` and `r` = the residual.
  - **Long facades only:** with `n ≥ 6`, `2r > width` and `width − r ≤ n + 1`, one bay is added. The `width − r` bays nearest the middle lose 1 block each. This is reported in the face notes.
  - **`center`:** the residual becomes end gaps: left `r//2`, right `r − r//2`. Gaps are adjusted first.
  - **`ends`:** the first bay gets `r//2` and the last gets the rest. Widths are adjusted.
  - **`absorb`:** +1 block per bay in middle-out order (left first on ties), cycling.
  - Short facades never add or drop bays. Bays plus gaps always equal `N` (asserted). Too short a face (`n = 0`) gets no bays.

## 4. Elements
Each element is placed in face-local `(i, v, w)`. The cell is `(x + w·ox, base_y + v, z + w·oz)` of column `i`, so curved and stepped walls follow their columns.
- **Layers** (no component, so chunk-safe; each cell is an independent op):
  - wall fill, `upper.fill`, parapet, cap, bands, `upper.top`, corners, piers and roof edge.
  - A `{component}` element is stamped as a template:
    - column `i` uses template `u = (i − first) % size.u`;
    - row `v` uses `(v − first) % size.v` when `repeat_v`, otherwise `v − first`.
  - `{role|block}` elements are 1×`height` strips at `w` (default 0).
- **Corners:** at each polyline vertex of a part, the face end column nearest the vertex owns the corner. Nearness is the Chebyshev distance of the cell centre (≤ 1.5), with ties going to face order.
  - Only that end gets the `corners` element, over the `floors` rows (default 0 to `usable`).
- **Piers:** placed at each bay start column and the last bay's end column, over the zone's floors.
- **Openings** (component instances):
  - **Placement:** the anchor is the bay start plus `u` (or the `align` offset inside `u_inset`), at row `floor start + v`.
  - **`repeat_u`:** tiles the inset span, and the last partial tile is clipped. If `widths` is given, the span must be listed there.
  - **Fit:** the opening must fit in its floor. `below`/`above` parts go in the row under / over the opening, inside the same instance.
  - **Skips:** an instance whose cells leave the face (`0 ≤ i < N`, `0 ≤ v < H`) or the envelope allowance (`−inward_backing ≤ w ≤ outward`) is skipped whole, with a reason.
- **Automatic backing (Detail pp.4–5):** a glass-role cell at `w = 0` with nothing behind it gets a palette `backing` cell at `w = −1` in the same instance.
- **Backing opt-out:** the opening's `backing` (else the face's, default `auto`) set to `none` removes every backing-role cell of the instance, whether automatic or authored. The face's `backing_exclude_u` ranges (inclusive column indexes) remove backing cells in those columns. An instance left with no cells is skipped.
- **Roofs** (packages, `roofs[]`): for each matching part, `cover` stamps every cell inside the part polygon that is not one of its wall columns. `edge` stamps the part's wall columns, party faces excluded.
  - The row is `top_y`, or `top_y + 1` with `rows: top+1`, and must be inside the envelope y range.
  - These are layers: `roof` (priority 0) and `roof-edge` (priority 1). Evidence is per element, default `measured`.
  - The massing roof (§9) never overwrites a roof element.
- **Extras** (packages, ≤256): each `[x, y, z, block, role, evidence]` becomes one single-cell atomic instance with the top instance priority. An extra outside the envelope is dropped and reported.
- **Canopies:** one instance per selected bay, or one for the `u` range.
  - Depth `≤ outward` is always allowed.
  - Deeper canopies need a package protrusion on the face that covers the columns with `depth ≥` the canopy depth.
  - Styles have no protrusions, so a style's deep canopy is dropped and reported.
- **Signs:** simplified lettering from the `glyphs` table. The text is upper-cased, the advance is `glyph_u + spacing`, and it is centred on the face (or placed at `u`) and in its zone rows (or at `v`).
  - Unknown characters or a text that does not fit skip it with a reason.
  - A null text places nothing.
- **Placeholders:** taken from the instance's anchor column segment.
  - `{facing}` = the cardinal nearest the outward normal. Ties at 45° go to the x axis.
  - `{facing_in}` = its opposite.
  - `{facing_right}` = the perpendicular cardinal along +u. Seen from outside, u grows left to right. `{facing_left}` is its opposite.
  - `{axis_u}` is `x` or `z`.
- **Palette resolution:** the zone's role first, then the palette role (`backing` falls back to `wall`), then the texture rule. Cell `props` are then merged where the block has the state (`facade_palette_tools.resolve_cell`).

## 4a. Ground-contact rows
- Face rows `v = 0` and `v = 1` (`base_y` and `base_y + 1`) can coincide with the TerraPlusMinus terrain top. LiDAR ground and TPM terrain differ by about 1 block.
  - Before this rule, the Red Rock acceptance run skipped 2,813 component cells this way, because one sand cell at Y927 blocked a 10-row frame component.
- So after conflict resolution, every cell of a **non-door** instance in those rows leaves the instance. It becomes a **plain per-cell op** (component null) with the same block, role and evidence. The instance then starts at `v = 2`, and an instance left empty disappears.
  - The plugin skips an occupied plain cell on its own. The terrain stays and everything above it is placed.
- **Door instances stay atomic.** A door's lower half must go with its upper half, so a door blocked by terrain is correctly skipped whole.
- Extras have no face row and are unaffected.
- The count is in `stats.ground_contact_cells`.
- Connection states for these plain cells follow the seam rule (§6) like any layer op.

## 5. Conflicts: priorities and reporting
Nothing is overwritten silently. Every case below is counted in `stats.conflicts`, with up to 20 samples in `stats.conflict_samples`, and summarised in `reasons`.
1. **Component instances** claim cells by priority: **extra (4) > door (3) > opening (2) > canopy and sign (1)**. Inside a priority, generation order wins: face order, then opening list order, floor and bay.
   - A door claims its whole doorway at `(face, i, v)` through every `w`.
   - A lower instance that overlaps is **clipped**: the overlapping cells are removed at compile time, and the instance stays atomic for apply.
   - It is **dropped whole** if it would lose a door cell or every cell.
2. **Components beat every layer** (`component>wall`, and so on).
3. **Layers:** `column (corners, piers) 5 > parapet cap 4 > parapet 3 > band 2 > roof-edge 1 > wall/roof 0`. On equal priority the earlier layer wins. The cap has its own priority so it really replaces the parapet's top row.

## 6. Connection states and state completion
These are computed after conflict resolution, from the **full** plan.
- **Panes and iron bars** connect to panes, bars, walls and solid blocks.
- **Fences** connect to the same fence family (nether brick is separate), fence gates and solid blocks.
- **Walls** connect to walls, panes, bars, fence gates and solid blocks. A side is `low` or `none`, and `up=false` only for a straight run (two opposite sides, nothing else).
- **Seam rule (review M5).** A side is set only when the neighbour cell is:
  - (a) in the **same component instance**, or
  - (b) a planned **layer** op (no component) that is connectable or solid **and in the same chunk**.
  - Otherwise the side is false. That covers unplanned cells (no world assumption is made), other component instances, and layer ops across a chunk border.
  - So a chunk-scope apply can never create an orphan connection. Components are atomic, and every layer neighbour that a side depends on is written in the same chunk job.
  - The plugin ignores connection states and stairs `shape` for ownership (review M6). The compiler still writes them explicitly.
- **Solid** means C's coarse kind `full` or `glass`, and never air.
- **Completion:**
  - stairs get an explicit `shape=straight` (plus `waterlogged=false`) unless the plan sets them. The shape is not recomputed from neighbouring stairs; the plugin does not use `shape` for ownership (M6);
  - slabs, trapdoors and connecting blocks get `waterlogged=false`;
  - trapdoors get `open=false,powered=false`;
  - doors get `open=false,powered=false,hinge=left` when missing;
  - leaves get `persistent=true`.
- **Checks:**
  - Every final block string is checked against `blocks-1.18.2.json` **and the global deny-list** (PACKAGE-SCHEMA §2). Any failure raises, so prep reports it, and the deny-list is enforced even if a palette bypassed the validator.
  - Every door's lower half must have its upper half in the same component (asserted).
  - Gravity blocks are refused at validation.

## 7. Op list, components and envelope
- **Op order:** layer ops first, sorted by (face, x, z, y), so columns run bottom-up. Then each component's ops are contiguous, with `first`/`count`.
  - Inside a component the order is: backing, then frame (and wall/column), then glass/mullion/door, then trim/sill/other, then canopy. Ties go by column, then y.
- **Components:** each one records its `chunks` (floor division, negative coordinates included), its anchor (the wall-plane cell) and its evidence.
- **Envelope:** the union of the geometry envelope (each `parts_mc` piece), every face column's cells for `w ∈ [−inward_backing, outward]`, and the protrusion cells, **minus `holes_mc`**.
  - Every layer op and every instance cell is tested while it is generated. A layer cell outside is dropped. An instance with any cell outside (for example a sill in a courtyard hole) is skipped whole. Both are reported.
  - The plan mirrors `holes_mc` into `envelope.holes_mc`, so the plugin applies the same test.
  - The plan writes the largest piece as `polygon_mc`, and every piece as `parts_mc` when the pieces are disjoint (as B does).
  - `y` is the geometry `envelope.y`.
  - **Asserted:** every op centre lies inside, y is in range, and there are no duplicate cells.

## 8. Limits
- **Whole plan:** `max_ops` comes from the package `limits`. It is capped at 60,000 for styles, and above 60,000 only with an exception (validator).
  - `max_extent_m` is the larger side of the envelope bbox, capped at 400 for styles.
  - Exceeding either gives `refused_limits`, with empty ops and components and the counts kept in `limits`.
- **Per chunk:** chunks with more than `MORIA_FACADE_MAX_OPS_CHUNK` ops (default 8000) are listed in `limits.chunks_over`. The plan stays usable for `whole`, and the plugin refuses chunk scope there.
- **Status:** `ready` when there are ops, `unresolved` when there are no ops but some faces are unresolved, and `nothing_to_do` otherwise.
  - Prep adds `no_geometry`, `no_package`, `invalid_package` (validation, geometry mismatch, or a compile failure, which is reported as `compile failed: ...`) and `protected` (the envelope touches the Strip +500 polygon of `egg_common`).

## 9. Massing for unshelled landmarks (`building.shell: "none"`)
For packages only (the default is `existing`). Shared faces are skipped.
- **Walls:** every other face gets a plain palette `wall` layer over `base_y..top_y` at `w = 0`.
  - The evidence is the face's class where the face has a layout, and `measured` where it is unresolved or has none. The face stays listed in `unresolved` as "massing only".
- **Roof:** a flat part (`roof.shape` flat or absent) gets a palette `roof` cap at `top_y` on every cell inside its polygon, with evidence `measured`.
- **Parapet:** a 1-block `parapet` ring at `top_y + 1` on its non-shared wall columns, when `top_y + 1 ≤ envelope.y[1]`.
- Openings are never added by massing. Facade layers and components still take priority (§5).

## 10. Prep CLI (`prep/facade_prep.py`)
- **`<cache> plan <bid> (--package id | --style id) [--seed N] [--packages-dir D] [--geometry file] [--member w..]`:**
  1. validate, then load the geometry: the `--geometry` file, else B's cached `geometry.json` when its sidecar `geometry.key` matches, else `facade_geometry.build()`, written to `buildings/<bid>/` with a new key;
     - **Geometry cache key (M4)** = sha256 of {bid, sorted members, package parts, geometry generator version, OSM index identity (its `extract_sha256` meta plus size and mtime)}. Changing members or parts recomputes.
  2. validate the geometry and the package's match to it, then check the Strip;
  3. compile, or reuse `plan-<key>.json`;
  4. write `manifest.json`, with `timings_s` and the latest status per selection;
  5. print **one JSON line** on stdout. Logs go to stderr.
  - Fault injection: `FACADE_PREP_FAIL=geometry` gives `no_geometry`, and `FACADE_PREP_FAIL=compile` gives `invalid_package` "compile failed".
- **`<cache> identify <x> <z>`:** B's `facade_identify.identify`, memoised in `index/identify-<x>_<z>.json`, plus the available styles.
  - The memo stores `packages_sha` (the sha of {relative package file: sha256} over the packages dir) and is recomputed when that changes, so editing, adding or removing a package refreshes it (M4).
  - **An invalid package never claims ids:** if the claiming package fails validation, `package_id` becomes null (`invalid_package` names it), and `bid`/`members` fall back to the hit building.
- **`<cache> adopt <bid>`:** writes `buildings/<bid>/adopt-candidates.json` = `{cells: [[x, y, z, expected]], source, source_sha, sources, missing}`. It reads its sources and never writes them.
  - **(a) Pilot shell `B` lines** in `shells.txt` (`MORIA_SHELLS_TXT`, default the primary plugin folder): cells whose `F` feature is a member, inside the cached geometry envelope and y range. The expected block is the `P` palette line.
  - **(b) Detail jobs** under `MORIA_DETAIL_JOBS` (default `summerlin\detailing\jobs`) whose `job.json` `feature` is a member.
    - The cells come from `owned.json`.
    - The **after-state comes only from `after.json`** (`{"[x, y, z]": block}`), or else from the `new` field of non-empty `plan.jsonl` rows.
    - Job-owned cells without an exact after-state are removed from the shell candidates and reported in `missing`, so they stay ambiguous. Nothing is inferred from colour.
  - **Harlo (`harlo-w348127357`):** no file in the job folder holds the after-states.
    - `plan.jsonl` was rewritten empty by the idempotency re-plan, because `detail_lib.Job.write` drops rows where old equals new.
    - `original.json` holds the before-states.
    - In `runs/05-rollback-all-plan.jsonl`, `old` equals `new` equals the pre-job block.
    - An exact `after.json` must be exported first (the `Job.want` map from `build_harlo.py`). Until then its 1,236 owned cells are reported in `missing`.
- **`validate <path>`** and **`inspect <plan> [--geometry G] [--out DIR]`:** see PACKAGE-SCHEMA §1.

## Automatic facades (v0.6 redirect, `AUTO.md`)
- Evidence classes on ops: `observed` (image-derived), `reference`, `measured`, `inferred`, `style`. Reports and chat call only `observed` ops observed.
- `prep/facade_autospec.py` turns an accepted `facade-autospec-1` into a package-shaped selection (`kind: auto`) and calls `compile()` unchanged in its rules. Layout elements of that selection may carry `evidence` (overrides the face class for that element: wall fill, parapet, bands, corners, piers, roof edge, openings, canopies) and `zone` (a palette zone for that layer element only). Authored packages are still validated without these keys.
- Each `faces[]` entry of a plan also carries `ops_by_evidence` (per-face op counts by class). An automatic plan adds `auto` = {mode, autospec_sha, analyzer, cost, fallback_style, faces}; its first reasons say how many faces are observed and which are `STYLE-GENERATED (not observed)`.
- Determinism: the plan key covers the accepted spec sha, the per-face palettes sha, the fallback style and every library file, so the same accepted spec compiles to identical ops.
