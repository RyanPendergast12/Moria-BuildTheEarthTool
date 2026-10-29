# Egg (v0.5) vegetation specification

- **Code:** `prep/egg_vegetation.py`, which provides `plan(ctx)` (ARCHITECTURE §9) and a `__main__` self-check.
- **Catalog:** `prep/egg_assets/vegetation_catalog.json`, built by `prep/egg_catalog_build.py`.
- **Attribution:** ASSETS.md. **Tools:** DEPENDENCIES.md.

Principle: plant only what evidence supports. Las Vegas is Mojave Desert: creosote-bursage scrub on undeveloped land, and irrigated planted trees and palms in the city. **No dense oak forests.** Missing data never means "no vegetation".

## 1. Evidence tiers (`features[].tier`, `confidence`)
| tier | evidence | what it may place | confidence |
|---|---|---|---|
| `mapped` | OSM `natural=tree` (point) and `natural=tree_row` (line, trees interpolated every 6 m from 3 m) from the landcover index (`egg_common.LANDCOVER_INDEX`, kinds `tree` / `tree_row`) | a whole tree at the mapped position. The model comes from `species` / `genus` / `taxon` / `leaf_type` (§3). Size comes from the `height` tag, else from the lidar canopy height within 2 blocks of the trunk, else a deterministic choice. | `mapped` |
| `measured` | USGS 3DEP lidar canopy: class-1 points with `number_of_returns > 1`, height above class-2 ground, clustered into crowns (position, height, radius). **Species unknown** (lidar never establishes species). | a tree or shrub sized from the crown. The model is chosen by shape and habitat only: under 3 m is a desert shrub (desert habitat only); 7 m or more with crown radius ≤ 2 is palm-shaped; otherwise a broadleaf from the habitat pool. It is skipped within 3 blocks of a mapped tree. | `measured` |
| `broad` | ESA WorldCover 2021 class (and OSM `natural=scrub`) | **only** sparse desert ground cover and small desert shrubs on undeveloped, eligible desert columns. **Never trees.** | `inferred` |
| `inferred` | trees inside OSM `natural=wood` / `landuse=forest` | trees on a 9-block jittered grid. **Off by default** (`ctx['vegetation_policy']={'inferred': True}`). | `inferred` |

**Lidar details** (`lidar_canopy`):
- **Datasets:** the same list as Snowball (`NV_ClarkCo_2_B22` 2022 first). Fetches go through `ept_cached.fetch` into `<snow_cache>/ept`.
- **Fetch box:** EPSG:3857, covering the tile + 32-block ring + 16-block margin. The margin lets ring trees be measured on full data, so neighbouring tiles agree.
- **Grid:** points map into block space with a local affine fitted to exact TPM projection corners. Ground is the mean of class-2 points per block, holes filled from the nearest cell.
- **Vegetation cell:** CHM ≥ 1 m, at least 2 multi-return class-1 points, and at least 40 % of the cell's class-1 points multi-return (roof edges are mostly single-return).
- **Crowns:** local maxima of the CHM over 5×5 cells, at ≥ 1.5 m. Radius is the largest square ring that is ≥ 50 % canopy.
- **Failure:** no lidar or too little ground gives a reason, the measured tier is skipped, and mapped trees use default sizes.

**WorldCover details** (`worldcover`):
- **Product:** v200 2021, 10 m, CC BY 4.0. Tile name from lon/lat (3° tiles, e.g. `N36W117`).
- **Reading:** IFD0 by one 64 KB range request, then only the needed 1024-px DEFLATE tiles by range request (about 50–80 KB each, at most 16 per call).
- **Cache:** `<cache>/worldcover/` with `provenance.json`, a 300 MB cap warning, and year/resolution recorded in `sources.worldcover`.
- **Broad densities** (plants per eligible column):
  - shrubland (20) or OSM scrub: 0.006 (creosote 50 %, white bursage 35 %, Mojave yucca 5 %);
  - bare/sparse (60): 0.003 (bursage, dead bush, creosote);
  - grassland (30): 0.004 (grass where the ground is dirt-like, else dead bush);
  - every other class: nothing.

## 2. Catalog schema (`veg-1`)
Top level:
- `catalog_version`, `minecraft: "1.18.2"`;
- `classes`: palm, broadleaf, conifer, shrub, desert-shrub, groundcover, crop-tree;
- `habitats`: desert, irrigated-urban, riparian, mountain;
- `sources`, and `entries[]`.

Each entry:
```
id, label, species, class, habitats[], dims{w,d,h}, crown_radius, anchor [0,0,0], format "moria-blocks-1",
provenance "tpm-asset"|"authored", source, licence, attribution, [source_key, source_origin_offset, tpm_spread], [support], [mapped_only],
blocks: [[dx, dy, dz, "minecraft:block[state]"], ...]
```
- **Anchor:** `[0,0,0]` is the trunk base, the first block above the ground, placed at `base_y + 1`. The builder asserts it exists and that nothing is below it.
- **Leaves** are always `[distance=1,persistent=true]`. 1.18.2 leaves have no `waterlogged`. The plugin compares tick-equivalently, ignoring `distance`.
- **Logs and wood** carry `axis`. Fences, walls and gates carry full connection properties, computed inside the model.
- **Roles** at plan time:
  - `trunk`: non-leaf blocks of trees;
  - `canopy`: leaves;
  - `shrub`: desert shrubs;
  - `groundcover`.
- **Contents:** 45 entries, 16 from TPM and 29 authored. See ASSETS.md.

## 3. Species and habitat rules
**Habitat:**
- `mountain` if the ground y is above 1800 m (Spring Mountains conifer zone; the valley floor is about 500–1100 m);
- else `desert` if WorldCover is 20, 30 or 60 and the column lies outside developed, landscaped or water landcover;
- else `irrigated-urban`.

**Mapped trees, in order:**
1. **A `species` / `genus` / `taxon` prefix:**
   - `Washingtonia` → fan palm; `Phoenix` → date palm;
   - `Prosopis` → mesquite; `Parkinsonia` / `Cercidium` → palo verde; `Chilopsis` → desert willow;
   - `Pinus` → Aleppo, or Mondell if *eldarica*; `Cupressus` → Italian cypress; `Olea` → olive;
   - `Yucca brevifolia` → Joshua tree; `Yucca` → Mojave yucca; `Larrea` → creosote;
   - `Citrus` / `Punica` / `Ficus carica` → orchard tree; `Picea` / `Abies` → spruce; `Populus tremuloides` → birch-shaped (aspen).
2. **`leaf_type`:**
   - `palm` → fan 70 % / date 30 %;
   - `needleleaved` → Aleppo 45 / Mondell 35 / cypress 20 (spruce in the mountains);
   - `broadleaved` / `leafless` → urban broadleaf pool: mesquite, palo verde, desert willow, olive, TPM oak, TPM savanna-s.
3. **Untagged** (about 90 % of Clark County OSM trees): the Las Vegas urban mix is broadleaf 50 % / palm 30 % / conifer 20 %. The feature label says "species not tagged" or "unknown". It never invents a species claim.

**Joshua tree** only where OSM says *Yucca brevifolia*. It is never planted from habitat alone.

**Sources for the species set:**
- Southern Nevada Water Authority, *Water Smart Landscapes plant list* (copy at the University of Arizona WRRC: https://www.wrrc.arizona.edu/sites/default/files/snwa%20water%20smart%20landscapes%20program%20plant%20list.pdf).
  - It lists *Washingtonia robusta* and *Parkinsonia aculeata* as **no longer to be sold or planted**. They remain common existing plantings, so they stay in the catalog for mapped and measured trees.
- Southern Nevada Regional Plant List (City of Las Vegas, 2026: https://files.lasvegasnevada.gov/planning/Sustainability/water-smart-plant-list-2026.pdf), and the SNRPC regional list. They include *Chilopsis linearis*, *Pinus eldarica* and desert-adapted trees.
- Nevada Division of Forestry, Las Vegas State Tree Nursery inventory (2021): https://forestry.nv.gov/uploads/missions/20210831_RWS_LV_Plant_Inventory.pdf.
- Mojave desert scrub: creosote bush–white bursage (*Larrea tridentata–Ambrosia dumosa*) is the dominant valley-floor association. Shrub cover is sparse, which is why the broad tier is deliberately much sparser than reality.

## 4. Protection, eligibility and support (all are hard rules; the plugin re-checks §5 of ARCHITECTURE)
- **Base data:** `base_y` must be known (provenance), and every op has `expect: air`. The trunk base goes at `base_y + 1`, and no cell is at or below its column's `base_y`.
- **Anchor column:**
  - `protect == 0`, or `protect == 32` (landscaped) **for mapped trees only**;
  - never Strip, road or sidewalk buffer, building buffer, `area:highway`, water, developed, or unknown;
  - no `base_cover`: existing vegetation, roads and the like are preserved, and Egg never deletes a tree.
- **Footprint:** a plant whose in-tile footprint touches a baseline log or leaf column is skipped, which keeps existing TPM trees intact.
- **Each cell:**
  - dropped in protected columns, under the same rule as above;
  - dropped over `base_cover`, and below ground;
  - dropped where overlapping another planned plant (deterministic first-wins: mapped, then measured, then inferred, then broad, by id).
  - **Road clearance:** canopy is never planned over a road- or sidewalk-protected column at any height. That is stricter than the "≥ `base_y` + 6" clearance, and it is what ARCHITECTURE §5 check 1 requires.
- **Support:**
  - trees and shrubs need a soil-like `base_block` (sand, red sand, dirt, coarse dirt, grass block, podzol, rooted dirt, terracotta, `*_terracotta`); never stone;
  - `dead_bush` needs sand, terracotta or the dirt tag (1.18 `DeadBushBlock`);
  - `grass` needs the dirt tag only, so it is never placed on sand (sand is the Las Vegas baseline surface).
- **Ground level:** OSM trees with `level` ≠ 0 or `location=roof/indoor` are skipped.
- **Terrain-changed columns (M6):** `ctx['terrain_cols']` is the set of (x, z) columns whose surface this tile's terrain plan changes. No anchor (trunk, shrub or ground cover) is placed there. It is counted in `plants_skipped.terrain_changes` with the reason "terrain changes this column; replant after terrain". Other cells of a plant in those columns are dropped up to `base_y + max_fill + 1`, because the surface may rise there. The plugin also refuses vegetation on Egg-terrain-changed surfaces.
- **Spacing:** non-mapped plants keep at least 1 (ground cover), 2 (shrubs) or 3 (trees) plus the crown radius from earlier anchors. Mapped trees are always kept: they are evidence.

## 5. Whole plants, clipping and ownership
- **Candidates:** they come from the tile + 32-block ring, and each is planned whole.
- **Ownership:** the tile containing the anchor (trunk base) owns the plant. Only that tile writes the trunk; trunk cells across an edge are dropped there. A plant is skipped if its own anchor cell cannot be placed.
- **Clipping:** ops outside the tile are clipped. `features[].partial` is true when the whole plant crosses the tile edge.
- **Remote cells:** cells inside this tile from a plant anchored in a neighbour get role `<role>-remote`. The feature carries:
  - `anchor = [x, base_y+1, z]` (the trunk base in the neighbour);
  - `anchor_block` = the exact planned state at the anchor (for example `minecraft:jungle_log[axis=y]`);
  - `anchor_in_tile = false`.
  The plugin writes remote ops only when the world block at `anchor` matches `anchor_block` (tick-equivalent); otherwise they are reported deferred.
- **Ring anchor height:** taken from the neighbour tile's baseline via `egg_provenance.baseline_columns` (read-only, the same value that tile uses), or from the optional hook `ctx['ring_column'](x, z) -> (base_y, base_block, base_cover)`. A ring column without a baseline drops the plant.
- **Ring protection:** if `ctx['protect_ring']` is present (`{(x, z): bits}` for the 32-block ring), a ring plant whose anchor column has bits the same rules forbid is dropped, together with all its remote ops (`plants_skipped.ring_protected`). This stops canopy from staying deferred forever. If the key is absent, ring protection is unknown and the plugin's anchor check still guards the remote ops.
- **Every feature carries** `anchor`, `anchor_block`, `asset`, `rotation`, `habitat`, `tier`, `evidence` and `ops`.

## 6. Determinism
- Every choice is `egg_common.det(feature_id, seed, ...)`: species pool, size, rotation (0–3 × 90°), broad-tier sampling (`det('broad', x, z, seed)`) and inferred jitter.
- Feature ids are global: `osm:n123`, `osm:w45#k`, `lidar:x_z`, `broad:x_z`, `inferred:x_z`. Two tiles therefore make the same choice for the same plant.
- Ops are sorted bottom-up with trunks before leaves: key `(trunk?0:1, y, x, z)`.
- The same seed gives identical ops (self-check). The seed is `egg-seed` (default 1).

## 7. Outputs
- **Status:**
  - `ready` with ops;
  - `nothing_to_do` with reasons;
  - `no_coverage` when the landcover index is missing or does not cover the tile. That means trees are unknown, never "no trees".
  - A lidar or WorldCover failure only skips its tier and adds a reason.
- **`complete`** (M4): `false` whenever an evidence tier was skipped for provider or coverage reasons: a LiDAR fetch failure or no LiDAR coverage (when the measured tier is on), a WorldCover failure, or a missing or non-covering landcover index. `incomplete_tiers` lists them (`lidar`, `worldcover`). Otherwise it is `true`. The plugin does not retire earlier vegetation from an incomplete plan.
- **`stats`:**
  - `tiers`, `candidates`, `ops`, `remote_ops`, `partial_features`, `lidar_crowns`;
  - `cells_dropped` {terrain_changes, below_ground, protected, base_cover, no_baseline, overlap, outside_tile};
  - `plants_skipped` {terrain_changes, ring_protected, protected, base_cover, no_baseline, soil, spacing, footprint_covered, habitat, wild};
  - `worldcover_classes`, `timings_s`.
- **`sources`:** osm, lidar (dataset, year, point counts, nodes fetched/reused), worldcover (product, year, 10 m, URL, licence, COG tiles fetched), catalog version.

## 8. Test tiles (self-check: `python prep/egg_vegetation.py`, no server)
| tile | blocks (in-game x / z) | centre lon, lat | context | result (seed 1) |
|---|---|---|---|---|
| (−189924, −85137) | x −12155136..−12155073, z −5448768..−5448705 | −115.27957, 36.14464 | Summerlin streets, 17 OSM trees (untagged). **Stand-in base_y.** | ready: 17 mapped trees, 1148 ops, 2 partial |
| (−189925, −85137) | x −12155200..−12155137, same z | −115.28026, 36.14453 | west neighbour, 9 OSM trees | ready: 10 trees, 639 ops, **14 remote ops** of `osm:n8294360402` (Mondell pine, trunk at −12155135, 815, −5448705 in the east tile). Anchor and anchor_block match the owning tile. |
| (−189994, −85164) / (−189994, −85163) | x −12159616..−12159553, z −5450496..−5450369 | −115.3313, 36.152 | Summerlin pilot, **real approved baseline** (sand surface, roads as concrete-powder cover), residential | nothing_to_do: no OSM trees, all columns developed or protected, nothing invented |
| (−190052, −85154) / (−190051, −85154) | x −12163328..−12163201, z −5449856..−5449793 | −115.3696, 36.1398 | Red Rock Canyon NCA edge, WorldCover bare/sparse. **Stand-in base_y.** | ready: 11 + 12 broad plants (dead bush, white bursage, creosote), 13 + 14 ops, about 0.3 % of columns. Lidar: no usable ground in the 3DEP sets there (reported). |

**Stand-in** (self-check only):
- **Why it is needed:** the approved baseline (egg_provenance: the 910 pilot-generation chunks) contains **no** OSM trees and no undeveloped desert.
- **What it is:** `base_y = floor(TPM height)` (TerraPlusMinus puts the ground top there), `base_block = minecraft:sand`, no cover.
- **Real-baseline tests:** the pilot rows above use the real baseline.

**Self-check assertions:**
- no op outside the tile, no duplicate cell, `expect` is `air`;
- leaves persistent;
- no op at or below `base_y`, in a protected column (other than bit 32 for mapped trees), or over `base_cover`;
- `-remote` exactly when the anchor is outside the tile;
- every in-tile anchor has its trunk-base op with `anchor_block`;
- the same seed gives identical ops and features;
- across each adjacent pair, no feature is anchored in two tiles and no cell is planned twice;
- remote features match the owner's anchor, `anchor_block` and asset;
- `complete` is false exactly when a tier failed (the desert pair, where LiDAR is unavailable, gives `false` / `['lidar']`; Summerlin gives `true`);
- a `terrain_cols` column loses its tree;
- a `protect_ring`-protected ring trunk emits no remote ops.

**Timings** (cached data): about 1.5–2 s per tile, of which lidar is about 1–1.5 s. A first lidar fetch takes about 11 s, and a first WorldCover tile adds about 0.7 s.

## 9. Limitations and open issues
- **Street trees in sidewalk strips are skipped.** The road buffers (`ROAD_HALF_M`, for example 7 m for residential) cover the planting strips. In the Summerlin test tile, 29 candidates were skipped as protected (mapped and broad together). Most of the mapped ones are street trees. Allowing them needs a contract change: a narrower "sidewalk" bit, or canopy over roads at ≥ `base_y` + 6.
- **Ring plants and protection (only without `ctx['protect_ring']`):** a ring plant's protection is unknown to this tile, which knows only the neighbour's baseline. A plant the neighbour refuses (for example on its road buffer) still occupies cells here as remote ops. Those stay **deferred** forever and can also block this tile's own overlapping canopy cells. A fix would expose the protection mask for the ring (§5) in `ctx`.
- **Lidar timing:** the 2022 lidar predates the 2026 OSM. Removed trees may still show as canopy (the measured tier is limited to fully unprotected columns), and young trees are under-measured.
- **Lidar coverage:** the 3DEP sets have little or no ground at the Red Rock edge, so the measured tier is unavailable there.
- **Species accuracy:** about 90 % of OSM trees in Clark County are untagged, so their species is a labelled urban mix, not a fact.
- **WorldCover:** 10 m resolution, 2021. It is never used to place trees, and its classes near development edges are coarse.
- **Model approximations:** the TPM fence, wall and gate connections are computed only inside the model. Neighbour updates can change them, so treat them as tick-equivalent or expect "changed" in undo reports.
