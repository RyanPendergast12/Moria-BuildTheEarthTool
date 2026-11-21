# Facade geometry (v0.6): rules, formulas, limits

Owner: agent B. Code: `prep/facade_geometry.py` (`build`, CLI, self-check) and `prep/facade_identify.py` (`identify`). Contract: `facade-geometry-1`, ARCHITECTURE.md §8a. Everything here is deterministic: the same inputs give a byte-identical `geometry.json` and the same `geometry_sha`.

## 1. API
```
identify(x, z, packages_dir=<repo>/packages, geo=None)
  -> {bid, members, tags, package_id|None, parts, hit, distance_m[, reason]}
build(bid, members=None, package_parts=None, cache=..., outward=1, use_accepted=True, geo=None, max_nodes=300, grids=None)
  -> facade-geometry-1 dict
write(cache, d) -> <cache>/facade/buildings/<bid>/geometry.json
CLI:  python prep/facade_geometry.py <cache> <bid> [--member w..] [--no-accepted]
      python prep/facade_geometry.py          (self-check)
      python prep/facade_identify.py          (self-check)
```
- `package_parts`: `[{id, polygon_mc:[[x,z],...], member?, osm?, top_y?, base_y?, tags?}]`, taken from a package's `building.parts`.
- `grids`: an optional dict that receives the internal rasters, for measurement scripts only.
- `identify` memoisation (`index/identify-<x>_<z>.json`) is left to `facade_prep.py` (D1).

## 2. Identify
- The block centre `(x+0.5, z+0.5)` is inverse-projected (TPM). The local OSM index is queried in a ±0.0002° box for `kind='building'`.
- **Hit:** the building that covers the point, smallest area first if nested. Otherwise, the nearest building whose UTM 11N distance is ≤ **3 m**.
- **Package claim:** `packages/*/package.json` (or `.yaml`, read with `yaml.safe_load`). Only `building.osm` is read.
  - Unreadable or invalid files and a missing directory are skipped silently. They are never fatal.
  - A claimed id gives `bid` = the first id in `building.osm`, and `members` = that list.
- **Parts:** `building:part` ways (`kind='building_part'`) whose representative point lies in a member footprint.
- **`bid: null`** means no mapped building within 3 m. It is **not** a statement that the land is empty.

## 3. Coordinates and conventions
- Block space: `x` east, `z` south, **north = −Z**. 1 block = 1 m. Footprints are projected with `geo.py` (TPM, exact). No local affine approximation is used.
- **Faces run so that `u` grows left→right as seen from outside.** Each ring is oriented clockwise in the (x, z) plane, which is counter-clockwise in map view with north up.
  - On a north face, `u` therefore grows westward.
  - The outward normal of segment a→b is `(−Δz, Δx)/|ab|`.
  - Holes (courtyards) use the opposite orientation, so their normals point into the void.
  - Harlo's older `JOB.md` used a different, display-only `u`. The contract `u` is the one defined here.
- `base_y` = the terrain-surface Y (the shell planner's `base`). The first wall block is `base_y + 1`.
- `top_y` = the cap or roof block Y (the shell planner's `top`).
- For a face, `v = Y − base_y`. A step face over a lower part has `base_y` = that part's `top_y`.

## 4. Cells: the shell planner's voxelisation (exact)
- **Footprint cell.** The planner tests whether the block centre lies in the footprint, in UTM 11N:
  1. `inv(x+0.5, z+0.5)`;
  2. → EPSG:32611;
  3. `contains_xy` (Polygon/MultiPolygon in UTM).
  - MC-space containment is **not** equivalent: on the pilot it differs by 0–13 cells per building. The UTM rule contained every accepted shell cell on all 22 pilot buildings; its extra cells are the planner's LiDAR-dropped open cells.
- **Wall cell.** A cell of part p is a wall if some 4-neighbour (±x, ±z) is either:
  - outside the building, which gives an outer wall from the ground; or
  - in a part with a lower `top_y`, which gives a step wall from that part's top (planner `F` wall).
- **Grid.** `X0 = floor(min x) − 2`, `Z0 = floor(min z) − 2`, with 3 cells of padding beyond the max, as in the planner.

## 5. Parts and heights
Parts are resolved in this order. Each source wins over the next.
1. **Accepted shell (frozen).**
   - Harlo: `summerlin\detailing\jobs\harlo-w348127357\geometry.json` `columns`.
   - Pilot: `shells.txt` `B` lines of the building's `F` index, read only.
   - Columns are grouped by cap Y. Each 4-connected group is a part with `top_y` = that cap and `height_src = accepted shell top (...)`.
   - `base_y` = the median of (lowest block − 1) over the wall columns (Harlo: its `ground_y`).
   - Footprint cells missing from the shell stay out (noted). **Nothing is moved, stretched or raised.** The envelope adds **no** parapet row above a frozen shell.
2. **Package parts.**
   - Polygons in MC; invalid ones are repaired (`buffer(0)`, largest piece) and noted.
   - They may extend past a wrong OSM outline: the Red Rock tower is 21.5 m off.
   - A part whose member has an accepted shell is ignored, and noted.
   - Package parts define the whole building. Footprint cells outside every part are listed in `notes` and get no faces.
3. **OSM `building:part`s (Simple 3D Buildings).** `min_height`, else 3.5 × `building:min_level`, gives the base offset. The top comes from:
   1. LiDAR (if ≥ 50 % of the interior cells are raised > 2 m);
   2. else `height`;
   3. else 3.5 × `building:levels` (labelled inferred);
   4. else the part is unresolved and skipped.
4. **No parts.** This is the shell planner's LiDAR segmentation, verbatim (`snowball_parts`):
   - the 3×3 median nDSM is rounded to whole metres;
   - connected levels are formed;
   - parts under 25 cells merge into the nearest-height neighbour;
   - neighbours within 1.5 m merge;
   - cells under 2 m are open, with no wall.
   - Part polygons are vectorised from the cell regions (§6).
5. **OSM `height`** on the building, if LiDAR is unusable. Otherwise the building is unresolved, with no faces.

### Heights
- **Base:** `median(floor(TPM ground))` over the cells. This is the planner rule.
  - It is per member in Snowball-rule mode, for exact parity.
  - It is per part for package and OSM parts, because ground varies by 8 blocks across Red Rock.
- **nDSM:** identical to the planner.
  - Classes 7 and 18 are dropped.
  - DTM = mean of class-2 points per 1 m UTM cell, filled by nearest neighbour.
  - DSM = max of single-return (`number_of_returns == 1`) class-1 points.
  - nDSM = DSM − DTM, sampled at block centres.
- **Measured top** (`roof_top`), over the part interior (polygon buffered −1 m):
  1. `med` = median nDSM.
  2. **Machinery:** 8-connected cells above `med + 1 m` whose area is < min(150 m², 10 % of the part) are excluded. They are never counted as occupied height.
  3. `top = base + round(p90)` of the remaining cells.
  - `height_src` records the dataset, p90, median, n, the excluded machinery cells and the Snowball (median) top.
- A package or accepted `top_y` wins. If LiDAR p90 disagrees by > 2 m, a note is written.
- **Roof:** `roof:shape` if tagged (evidence `osm`). Otherwise flat with a parapet.
  - The evidence is `measured` when p90 − median < 1.5 m, and `inferred` otherwise.
  - The parapet itself is below LiDAR resolution and is always inferred.
- **LiDAR source:** the shell planner's dataset order (`NV_ClarkCo_2_B22` first).
  - EPT reads go through `<cache>/ept`, with read-through to `shelltool\cache\ept`.
  - New tiles are written only under `<cache>/ept`.
  - The production limit is `max_nodes` 300. Red Rock used 3000, for measurement only.

## 6. Faces
- **Ring cleanup.**
  1. Douglas–Peucker `simplify(0.25 m, preserve_topology)`.
  2. Spikes (turn > 175°), collinear vertices (< 0.1°) and duplicates are removed.
- **Arcs.**
  - **Detection:** a run of ≥ 3 segments turning the same way by 1–25° at each interior vertex.
  - **Fit:** Kasa least-squares circle. The points are centred first, because MC coordinates near 1e7 make the raw fit ill-conditioned.
  - **Acceptance:** max |radial residual| ≤ 0.25 m and r < 2000 m. The run's segments get `curve: "arc:<part>r<ring>a<k>"`.
  - A whole round ring (all vertices curvy, ≥ 6) is one arc.
  - The arc centre, radius, residual and segment count go to `notes`. The chords stay as written, with sagitta ≤ 0.25 m.
- **Face breaks:**
  - a turn > 30° between non-arc segments;
  - a change of edge kind;
  - a change of `base_y`.
  - `u` is continuous inside a face: `u0(k) = u1(k−1)`. `length_u` is the last `u1`.
- **Edge kind** (v0.6.1): every ring edge is sampled every 0.5 m, at a point 0.3 m outward. Each sample is classified in this order:
  1. inside another part with top ≥ ours → `shared` (no openings, no columns);
  2. inside a lower part → `exterior`, with `base_y` = that part's top (a step face);
  3. inside a **non-member OSM building** → **`party`** (no openings, no columns; D1 treats it like `shared`);
  4. otherwise → `exterior`.
  - **Splitting:** the edge is cut where the kind changes, so only the coincident stretch is `shared` or `party`.
    - Runs shorter than 1 m are noise and are absorbed into the neighbouring run (the previous one, or the next for the first run).
    - The pieces of a split edge keep the edge's normal and curve id. `u` stays continuous, and a kind change starts a new face.
  - **Neighbours:** non-member `kind='building'` features from the R-tree query (footprint bounds ± 0.0005°) that come within about 2 m (2e-5°) of the footprint union, projected with TPM.
- **Labels:** the cardinal nearest the length-weighted mean normal, suffixed "(display only)".
- **Vectorising raster parts** (`regions_polys`):
  1. Footprint cells in no part are lent to the nearest part, for the polygon only.
  2. The highest part goes first: its cell squares are grown 1.5 m, minus the squares of the parts not yet placed.
  3. The result is simplified at 1 m (which straightens 11.4° stairs), clipped to the footprint, then reduced by the parts already placed.
  - Internal boundaries are therefore coincident, and outer edges equal the footprint.

## 7. Columns
- **Assignment.** Every wall cell (§4) is assigned to exactly one non-shared face of its own part: the nearest segment, by distance from the cell centre to the segment.
  - Candidates are segments whose normal has a dot product > 0.3 with one of the cell's wall directions. If none qualifies, the nearest non-shared segment is used.
  - Ties go to the lower face index, then the lower segment index.
  - **A corner cell is therefore owned by exactly one face.**
  - **Party walls:** if the winning segment is `party`, the cell gets **no column** and is counted in `notes`. It still belongs to the shell, but the facade must never write it.
- **Column fields:**
  - `u = u0 + t·(u1−u0)`, with `t` the clamped projection;
  - `seg` = the segment index in the face;
  - `(ox, oz)` = the wall direction with the largest dot product against the segment normal. It is always a unit grid step.
- Columns are sorted by (`u`, x, z).
- Cells with no candidate segment are counted in `notes` (Red Rock, Snowball mode: 26 of 7,627).

**Verification** (`C:\BTE-Migration\facade\measure\shell_match.py` → `shell-match.json`):

| building | shell wall cells | party-wall cells (no column, by design) | non-party cells reproduced (accepted mode) | non-party cells reproduced (independent: footprint + 2022 LiDAR, planner rules) | unexplained missing / extra |
|---|---|---|---|---|---|
| Harlo w348127357 | 120 (104 outer + 16 step) | 0 | 100 % | 100 % | 0 / 0 |
| Lazy Dog w348127354 | 151 | 0 | 100 % | 100 % | 0 / 0 |
| Ethan Allen w1393409724 | 120 | 25 | 100 % | 100 % | 0 / 0 |
| west elm w348127358 | 261 | 24 | 100 % | 100 % | 0 / 0 |

- Accepted mode reproduces the shell by construction. The independent mode is the real test of the voxeliser.
- **Party-wall cells:** each lies within 1 m of the one adjoining non-member footprint, and 48 of the 49 face a neighbour cell directly; the remaining one is a corner cell at the end of the party wall.
  - In v0.6.0 these cells were written as `exterior`. That is review finding M3.
- Harlo's 9 footprint cells without LiDAR ≥ 2 m are absent from both the shell and the columns.

## 8. Envelope and sha
- **Envelope (v0.6.2):** the union of:
  - the member footprints;
  - **every part polygon** (a part is never a hole, even when it lies off a wrong OSM outline, like the Red Rock tower);
  - the **outward strip**: every column cell dilated by `outward` with a **square** kernel (Chebyshev distance), so the diagonal outer-corner cells are inside. `outward` defaults to 1, and a package's `envelope.outward` overrides it.
  - Then a 0.01 mitre buffer and `simplify(0.05)`.
  - `y = [min base_y, max(top_y + 1)]`; frozen-shell parts add 0.
  - **Holes are allowed only for:**
    - (a) non-member neighbour footprints (v0.6.1), unbuffered and **minus our parts**, which are never subtracted. Any overlap is noted;
    - (b) true courtyards: member footprint ring holes that no part covers, minus the courtyard walls' own strip.
    - Every other enclosed gap (for example, between strips in a concave corner) is filled.
  - Depth beyond `outward` (package `protrusions`) is D1's to add to the plan envelope. Its corners should use the same square join.
  - **Disjoint members** give several pieces. `polygon_mc` is the largest (ties by bounds), and all pieces are in `envelope.parts_mc`.
  - **Holes** (enclosed neighbours or courtyards) are in `envelope.holes_mc`.
  - Both `parts_mc` and `holes_mc` are contract extensions (see §9). A consumer that reads only `polygon_mc` must also respect `holes_mc`.
- **`geometry_sha`** = `sha256(json.dumps(d without "geometry_sha", sort_keys=True, separators=(",",":")))`. There are no timings in the file.

## 9. Limits and open contract points
- **Contract extensions** (D1 should accept them or ask the coordinator):
  - edge kind **`party`** (v0.6.1; D1 treats it like `shared`);
  - `envelope.parts_mc` for disjoint members, and `envelope.holes_mc`;
  - arc parameters live only in `notes`. A structured `arcs: {id: {center, radius, max_residual}}` would be cleaner.
- **`polygon_mc` drops holes.** Faces do include hole rings.
- **OSM outlines can be wrong.** The Red Rock tower outline is 21.5 m from the LiDAR tower (IoU 0.16).
  - Geometry never silently moves an outline. A package must supply the corrected part polygon (see `facade\measure\red-rock`).
- **Raster parts** (Snowball-rule mode) have wavy internal boundaries and many small parts: Red Rock gives 109 parts and 1,733 faces.
  - That is faithful to what a Snowball shell would build, but it is not good massing. Use package parts for landmarks.
- **Edge-kind resolution** is 0.5 m along the edge, and runs under 1 m are merged into a neighbour. A party wall therefore starts or ends within about 1 m of the true end of the coincidence.
- **Party detection depends on OSM.** A neighbour that is missing from OSM, or mapped more than 0.3 m away, is not detected. Field truth beats OSM.
- **p90 roofs over sloped parking decks** sit 2–5 m above the ramps. A flat top is a limit of `facade-geometry-1`, which has one `top_y` per part.
- **Snowball parity** is exact for x and z. `top_y` uses the p90 measured top, which can be 1 block above a Snowball shell's median top. When an accepted shell exists, it wins.
- **Canopies** (`building=roof|carport`): the planner places only corner posts. Geometry still emits ring faces. Styles should skip these buildings.
- **Data:** the OSM index (Geofabrik 2026-10-25), 3DEP 2022 (`NV_ClarkCo_2_B22`), and TPM terrain. TPM may fetch and cache its own tiles under `shelltool\tpp-cwd`, as `geo.py` always does.
