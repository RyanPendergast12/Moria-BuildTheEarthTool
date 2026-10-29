# Egg agriculture spec (v0.5)

Owner: the agriculture agent. Code: `prep/egg_agriculture.py` (generator) and `prep/egg_landcover_index.py` (landcover index builder, shared with vegetation). Contract: `docs/egg/ARCHITECTURE.md`.

## 1. Landcover index (`clark_landcover_index.sqlite`)
- **Builder:** `python prep/egg_landcover_index.py [pbf] [provenance.json] [out.sqlite] [w,s,e,n]`. The defaults are the Nevada extract and `shelltool\osm-extract\clark_landcover_index.sqlite`.
- **Schema:** the same as `osm_index_build.py`: `feat(id, osm, kind, tags, wkb)`, an R-tree `feat_idx`, and `meta`. Use it through `egg_common.query_index`.
  - `meta` records the source, extract date, sha256, download time, coverage box, counts, build seconds and licence.
- **Coverage box:** (−116.1, 35.0, −114.0, 36.9), the same box as `clark_osm_index`. It covers Clark County, including Moapa Valley, Logandale, Overton and Mesquite. It also reaches Pahrump in Nye County.
- **Build:** written to `<out>.building`, then moved into place with `os.replace` (atomic). Node locations use osmium `flex_mem`.
- **Build run (2026-10-29):** 85 s, 37 MB.

| kind | rule | count |
|---|---|---|
| `tree` | node `natural=tree` | 19,579 |
| `tree_row` | way `natural=tree_row` (line) | 491 |
| `landuse` | area with `landuse=*` | 14,804 |
| `natural` | area `natural=` wood/scrub/grassland/heath/sand/bare_rock/wetland/water/scree/beach/... | 9,583 |
| `leisure` | area `leisure=` park/garden/golf_course/pitch/playground/nature_reserve/dog_park/... | 8,441 |
| `water` | area `natural=water`, `waterway=riverbank`, `landuse=reservoir/basin` or `water=*`, plus every `waterway=*` line (intermittent washes included; check `intermittent=*` in the tags) | 10,990 |

- **Several kinds per object:** one OSM object can be stored under more than one kind. For example, `natural=water` is stored as both `natural` and `water`.
- **Absence:** inside the coverage box, a missing feature means "not mapped in OSM on 2026-10-25". It does not mean empty land.

## 2. Types and evidence rules
| type | OSM evidence | treatment | confidence |
|---|---|---|---|
| crop | `landuse=farmland` + `crop=` wheat/barley/oats/rye/cereal/grain/triticale, potato, carrot, beet/sugar_beet | rows: `farmland[moisture=0]` with the max-age crop above it; every 4th row is a `coarse_dirt` furrow | mapped |
| green | farmland + `crop=` alfalfa/hay/grass/fodder/clover/pasture/sod | `grass_block` surface, 30 % `grass` | mapped |
| generic | farmland with no crop tag, or a crop with no stable 1.18.2 block (maize, cotton and others) | tillage bands `coarse_dirt, coarse_dirt, dirt, dirt` across the field frame. No crop is claimed and the feature is reported as generic. | inferred |
| vineyard | `landuse=vineyard` or `crop=grape` | one row of `jungle_leaves[persistent=true]` every 3 blocks, 1 block high, with 15 % gaps | mapped |
| orchard | `landuse=orchard` (`trees=*` recorded only) | vegetation catalog `crop-tree` entry (`lv-orchard-tree-4`, 5 × 5 × 5, crown radius 2, persistent oak leaves) on a 5 × 5 grid in the field frame | mapped (spacing inferred) |
| meadow | `landuse=meadow` | sparse `grass` 22 %, dandelion/poppy/cornflower 3 %. Placed **only** on dirt, coarse_dirt, grass_block or podzol baseline. No surface change. | mapped |
| farmyard | `landuse=farmyard` | **never a field**: it appears in `features` and gets no ops | mapped |

**Never farmed.** These areas are excluded even where they overlap a farm polygon, with a 1-block buffer:
- `leisure=golf_course` (**never a farm**), park, garden, pitch, playground, nature_reserve, dog_park, recreation_ground, stadium, track;
- `landuse=grass`, recreation_ground, cemetery, residential, farmyard;
- anything tagged `golf=*`.

Desert (`natural=*`) is never planned by agriculture.

**What is never inferred from a generic tag:** crop, irrigation (no water blocks; farmland is always `moisture=0`), growth stage (always max age) and row direction.

**Row direction comes from:**
- `vine_row_orientation=*` (degrees, OSM wiki), or
- a numeric `direction=*`, as a compass bearing. Block-space u = (sin θ, −cos θ).
- Otherwise it is **inferred** from the longest edge of the minimum rotated rectangle of the whole field. `row_direction_source` reports `mapped` or `inferred` for each feature.

**OSM wiki notes (fetched 2026-10-29):**
- **farmland:** tillage of annual crops. Meadow, orchard, vineyard and farmyard are separate tags. Farmyard covers buildings and yards and is not for fields. Useful subtags are `crop=*` and `irrigation=pivot` / `irrigated=*`.
- **orchard:** `trees=*` is preferred, then `produce=*`, then `crop=*`; `orchard=plantation|meadow_orchard`. There is no row-direction tag.
- **vineyard:** `crop=grape` is implied; `grape_variety=*`; `vine_row_orientation=*` gives row direction in degrees.
- **meadow:** hay or grazing land (`meadow=agricultural|pasture|paddock|...`). It is distinct from `landuse=grass` (mown lawns, golf, verges) and `natural=grassland`.

In this extract, **none of the Clark County farmland polygons carries `crop=*`**, so they all get the generic treatment. Only the vineyards carry `crop=grape`.

## 3. Whole-field planning (tile continuity)
- **Field frame:** for each field, the whole polygon is projected to block space (`egg_common.to_block`). Its frame is:
  - **origin:** the lexicographically smallest corner of the minimum rotated rectangle, rounded to 1e-3;
  - **u:** the row direction, canonicalised so that u.x > 0;
  - **n:** perpendicular to u.
- **Row index:** for a cell, `row = floor(((x+.5,z+.5) − origin) · n)`. The block chosen is a pure function of (field, x, z, seed), so the two tiles on either side of a seam produce the same row lines in any order. The self-check verifies this.
- **Edges:** fields follow the polygon outline, curves included, at cell centres inside `field.buffer(−1)` (1-block margin) minus the excluded areas. Edges are never redrawn per tile.
- **Orchard trees:** each tree is the catalog `crop-tree` model, unrotated. Block `[0,0,0]` is the trunk base at `base_y + 1`. The op `feature` is the tree id `<osm>/tree<x>_<z>`.
  - Trees are kept `crown_radius` blocks inside the field edge.
  - Trees whose trunk lies outside the tile still contribute their canopy cells inside the tile. Those cells have role `leaves-remote`. The plugin writes them only if the trunk exists there (ARCHITECTURE §4); otherwise they are deferred.
  - Every tree that crosses the tile edge gets a `features` entry with `partial: true`, `asset`, `anchor` = trunk base `[x, y, z]`, `anchor_block` (the catalog block at `[0,0,0]`) and `anchor_in_tile`.
  - For a remote trunk, y is estimated from the nearest in-tile column.
- **Terrain interplay (REVIEW M6):** `ctx['terrain_cols']` is the set of (x, z) columns whose surface the same tile's terrain plan changes. No agriculture op is placed on those columns.
  - Field, vineyard and meadow cells on them are skipped.
  - An orchard tree whose trunk column is listed is skipped whole.
  - Canopy cells over a listed column are dropped.
  - Skips are counted in `stats.skipped.terrain` and reported as the reason "terrain changes this column".
- **`complete` (REVIEW M4):** the plan key is `true` normally. It is `false` only when field evidence itself is degraded: the landcover index is missing or not covering (`no_coverage`), or a field polygon failed to project. A CDL failure is supporting-only and never makes a plan incomplete.
- **Terrain:** every op uses that column's `base_y`.
  - Surface replacements are at `base_y` with expect `base`.
  - Plants, crops, vines and trunks are at `base_y+1…` with expect `air`.
  - A surface op is omitted where `base_block` already equals the target.
- **Eligible columns only:** `protect == 0`, `base_y` known and `base_cover` None.
- **Op order:** per column it is surface → plant. Orchards are sorted by y, with trunks before leaves.
- **Limits:** a plan with more than `limits.max_ops` ops is reported as `refused_limits`.

## 4. Guidebook principles (BTE Agriculture Fields Guidebook, 5 pages, read in full, text and screenshots)
| page | principle | Egg translation |
|---|---|---|
| 1 | Step 1: re-terraform the field first; "if the server has lidar, there is no need to terraform". Otherwise heights are plotted from Google Earth and `//set 0` clears the area | Fields **follow the terrain** (`base_y` per column, from the lidar-based baseline). `//set 0` (blanket clearing) is **rejected**: Egg never clears an area. |
| 2 | Outline: tpll to every corner; "if the corners are curved then tpll to several points of the curve"; `//line` between consecutive points (the screenshot shows a non-rectangular field on sloped ground) | The OSM polygon *is* the outline. Its curves are kept vertex for vertex. A 1-block edge margin keeps ops inside the polygon. No marker blocks are placed. |
| 3 | Remove vegetation: `//replacenear 48 17,18,31,37,38,39,40,106,175,161,86 0` (logs, leaves, tall grass, flowers, mushrooms, vines, double plants, pumpkins → air) | **Not done.** Columns with baseline cover (`base_cover`) are skipped and kept. Egg removes nothing outside the protected writer's explicit ops. |
| 3 | Fill: `//sel poly` on the outline, then `//re 2,57 2,159:13,159:5` (grass + outline → grass / green terracotta / lime terracotta mix "appropriate for the field"); raise `//limit` | A bounded per-cell surface palette inside the polygon (expect `base`). The legacy IDs are 159:13 = `green_terracotta` and 159:5 = `lime_terracotta`. Egg uses dirt/coarse_dirt/farmland/grass_block instead, because this is desert irrigated farmland, not the guide's green field. The terracotta mix is kept as a palette option (§5). |
| 4 | Screenshot: textured field surface with lines crossing it on slopes | Surface texture per cell, following the terrain |
| 4 | Step 5 (optional, "if your field doesn't look like it has crops on top, no need"): `//shift 1 u`, then crops on top. Wheat `40%59:6,40%59:7,20%0`; Potatoes `142:6/7`; Corn `104:6,104:7`; Grapes `50%18:3,50%0`; Carrot `141:6/7`; Cabbage `50%18,50%0`; "select just a part of the field to make different crops" | Crops go one block above the surface (expect `air`), **only when OSM `crop=*` says so**. Max age only: `wheat[age=7]`, `potatoes[age=7]`, `carrots[age=7]`, `beetroots[age=3]`. The guide's 20 % air becomes furrows (every 4th row). Grapes = `jungle_leaves` (18:3) rows. **Corn = pumpkin_stem (104) is not used**: a stem can grow a pumpkin onto a neighbour cell if random ticks run, so corn falls back to generic. Cabbage (`oak_leaves`) is not mapped because no Clark field is tagged with it. |
| 5 | Screenshots: a field split into **bands** of different crops that follow the field and the terrain, with crisp field edges against the surroundings | Row bands anchored to a field frame, continuous across tiles. Edges follow the polygon. |

The guide's `//fast`, `//limit` and `//replacenear` are never run (AGENTS rule 3: plans, not commands).

## 5. Block palette (1.18.2 stable states)
- **Surface (`base`):**
  - `minecraft:farmland[moisture=0]`, `minecraft:coarse_dirt`, `minecraft:dirt`, `minecraft:grass_block`.
  - Optional, not used by default: `green_terracotta` and `lime_terracotta` (guide p3).
- **Above the surface (`air`):**
  - `wheat[age=7]`, `potatoes[age=7]`, `carrots[age=7]`, `beetroots[age=3]`;
  - `jungle_leaves[distance=7,persistent=true]`;
  - `oak_log[axis=y]`, `oak_leaves[distance=7,persistent=true]`;
  - `grass`, `dandelion`, `poppy`, `cornflower`.
- **Orchard trees:** the vegetation catalog entry (`prep/egg_assets/vegetation_catalog.json`, class `crop-tree`; the first by id, currently `lv-orchard-tree-4`). It uses `oak_log[axis=y]` and `oak_leaves[persistent=true]`. If the catalog is missing, a small authored tree is used and the asset is reported as `authored-fallback`.

## 6. Persistence analysis
**Gamerules** (coordinator, from level.dat): the primary world 25566 and test world 11 run `randomTickSpeed=0`, `doDaylightCycle=false`, `doWeatherCycle=false`, `doMobSpawning=false` and `mobGriefing=true`. Egg **does not change gamerules**.

Under these rules there are no random ticks, so crops do not grow, farmland does not dry or revert, and leaves do not decay. The design also stays safe if `randomTickSpeed` is raised:

| block | random-tick behaviour | why it is safe |
|---|---|---|
| crops | They grow only below max age | They are placed at max age, so they never change |
| `farmland[moisture=0]` with a crop above | Moisture can only rise near water (there is none). Dry farmland turns to dirt only when **no** crop is above it | Every farmland cell has a crop above it. Furrows and gaps are `coarse_dirt`, never bare farmland. |
| leaves | Decay only if `persistent=false` | All leaves are placed `persistent=true`. `distance` is tick state (ARCHITECTURE §6). |
| grass and flowers | No random-tick change on dirt or grass_block | Meadow plants go only on those bases |
| `pumpkin_stem` | Grows a pumpkin onto a neighbour | **Not used** |

**Remaining non-tick risks:**
- trampling by players or mobs (farmland → dirt, and the crop pops off);
- a block update after a crop's farmland was **not** written: if the surface op was skipped at write time but the crop op passed, the crop sits on dirt or sand and breaks on its next update. See open issues.
- `mobGriefing=true` lets mobs trample, but mob spawning is off.

The coordinator runs the in-world place / restart / check test on server 11.

## 7. CDL (supporting evidence only)
- **Product:** USDA NASS Cropland Data Layer **2025** (latest; CropScape answers "year must be between 1997 and 2025" on 2026-10-29). Metadata dated 2026-03-09.
- **Resolution:** NASS publishes the 2025 CDL at 10 m. **The CropScape `GetCDLFile` clip returned 30 m pixels** (GeoTIFF tag 33550 = 30, 30). The resolution is recorded per sample as `resolution_m`.
- **CRS and format:** EPSG:5070 (NAD83 / Conus Albers). Uncompressed 8-bit palette GeoTIFF that Pillow reads, so no GDAL is needed.
- **Licence:** public domain, provided "as is".
- **Accuracy caveats (NASS metadata):**
  - The 2025 national agricultural accuracy is 75.4 % (kappa 0.71); major crops are 85–95 %.
  - Non-agricultural classes come from NLCD.
  - Minor and small-acreage classes are poor.
  - The reference data is biased toward program crops.
  - Nevada state figures are only on the NASS metadata page (not fetched).
  - At 30 m, a narrow Moapa field mixes with riparian woody wetland (see the samples below).
- **Access:** `cdl_sample(geom_lonlat, fid)`.
  1. Projects the field to EPSG:5070, takes its bbox (+30 m, refused above 25 km²) and calls `GetCDLFile?year=2025&bbox=...` (60 s timeout).
  2. Downloads the returned TIFF (refused above 20 MB).
  3. Caches it as `<cache>/cdl/cdl2025_<hash>.tif` plus `.provenance.json` (url, service, year, bbox, fetched, bytes, licence).
  4. Returns the majority class among pixel centres inside the field, its share and the full class histogram.
  - Failures return `provider_failure`. They never block the plan.
- **Use:** the result is reported in `features[].cdl` and as reasons. Disagreements are reported, for example "OSM farmland, CDL majority Woody Wetlands". CDL **never** picks a crop, irrigation or rows.
- **Default cache:** `C:\BTE-Migration\shelltool\test-cache-v05\egg\cdl`, or `<ctx.cache>/cdl` in production.

**CDL 2025 samples of OSM farmland fields (majority, share):**

| field | majority | share |
|---|---|---|
| w145028142 | Alfalfa | 51 % (agrees) |
| w1136237976 | Alfalfa | 68 % |
| w145028139 | Woody Wetlands | 56 % (disagrees) |
| w144316078 | Woody Wetlands | 76 % |
| w179652091 | Shrubland | 38 % |
| w127409351 (Mesquite / Bunkerville) | Herbaceous Wetlands | 54 % |

## 8. Test sites
No agricultural polygon exists in or near Summerlin. **No agricultural field lies inside the approved baseline world**: its 338 regions cover mainly x −23728…−23713 (Strip / east valley) plus a few others. Without the baseline there is no terrain provenance, so production plans there are `ineligible`. The coordinator decides.

| site | OSM | tags | rep. point lon/lat | block (x, z) | tile (tx, tz) | baseline region present? |
|---|---|---|---|---|---|---|
| **Primary: Logandale farmland (Moapa Valley)** | w145028142 | `landuse=farmland` (no crop); CDL 2025 Alfalfa 51 % | −114.48779, 36.60178 | −12074140, −5485509 (y ≈ 423 from TPM) | (−188659, −85712); self-check tiles (−188659, −85711) and (−188659, −85710) | **No** (r.−23583.−10714) |
| Gilcrease Orchard (NW Las Vegas) | w27631409 | `landuse=orchard`, `name` | −115.24764, 36.30333 | −12148690, −5465799 | (−189824, −85404) | **No** (r.−23728.−10676; only z −10620…−10631 exist for x −23728) |
| Henderson vineyard | w1415674292 | `landuse=vineyard`, `crop=grape` (tiny, about 68 × 41 blocks) | −115.09155, 36.02151 | −12140954, −5431671 | (−189703, −84870) | **No** (r.−23713.−10609) |

Block bbox of w145028142: x −12074464…−12073807, z −5485879…−5485060.

**Golf-course negative controls** (`leisure=golf_course`; agriculture must return no ops):
- **Inside the baseline:** Wynn Golf Course w27910587, tile (−189762, −85070), and Bali Hai Golf Club w134048326, tile (−189803, −84994). Both return `nothing_to_do` with 0 ops in the self-check. Las Vegas Golf Center w1201568404 (−189797, −84976) is also inside the baseline.
- **Near Summerlin (no baseline regions):** TPC Summerlin r2816630 (−189930, −85217), TPC Las Vegas r3558755 (−189959, −85205), Angel Park w1378768387 (−189918, −85188), Badlands r7126303 (−189941, −85175), Red Rock CC r3554760 (−190023, −85140), Arroyo r3554761 (−190010, −85152), Siena r1839971 (−189996, −85076), The Summit Club w1353411198 (−190032, −85081), Bear's Best r3554736 (−190029, −85119), Canyon Gate r1836755 (−189927, −85144), Highland Falls r3554853 (−189956, −85266), Eagle Crest r17330136 (−189979, −85245), Palm Valley r3554854 (−189924, −85257).

## 9. Self-check (`python prep/egg_agriculture.py`, no server, about 5 s warm)
**Stand-in context:** `base_y = floor(geo.heights)` from the TPM terrain model, because `prep/egg_provenance.py` (the baseline reader) is not used here. `base_block = dirt`, `protect = 0`.

The self-check verifies:
1. Two adjacent tiles over w145028142 are both `ready` (2,047 ops each). The neighbour is chosen along the row direction.
2. Both tiles report an identical field frame (origin and u).
3. Every op equals the field-global pattern, and the seam cells agree (26/26 same band).
4. Plans are deterministic.
5. Bali Hai and Wynn golf tiles return 0 ops.
6. A synthetic farmland + `golf=*` area and a synthetic farmyard return 0 ops.
7. A synthetic `crop=wheat` field yields exactly farmland / wheat[age=7] / coarse_dirt, with each crop directly above its farmland.
8. Synthetic orchard, vineyard and meadow fields yield only their palette.
9. The real Gilcrease Orchard tile uses the catalog tree, not the fallback.
   - Every `leaves-remote` op ties to a partial tree feature that has an `anchor`, an `anchor_block` and the catalog `asset`.
   - Every in-tile anchor equals its trunk-base op.
   - The plan has `complete: true`.
11. With `terrain_cols` set, no op lands on a listed column and the reason is reported. This is checked for both the orchard and the farmland tile.
10. `landcover=None` returns `no_coverage` with `complete: false`.

`egg_common.plan` asserts that no op lies outside the tile.

## 10. Limitations and open issues
- **Dependent ops:** a crop op (expect `air`) can pass while its farmland op (expect `base`) was skipped. The plugin should skip `crop` ops whose cell below was not written by the same job. This needs a coordinator/plugin decision.
- **Remote canopy y:** the trunk y is estimated from the nearest in-tile column. If the neighbour tile placed the trunk at a different y, the canopy is deferred. That is safe, but some canopy may be missing at seams.
- **Generic bands are a visual convention**, not observed tillage, and they are labelled `inferred`. If the coordinator prefers "unresolved" over a generic texture for untagged farmland, change `treatment()` to return no ops.
- **CDL:** each field triggers one bounded network fetch at prep time. Later runs hit the cache, and a failure is non-blocking. CDL is never used for crop choice. The 30 m service resolution is coarser than the 10 m 2025 product.
- **No agricultural site has baseline coverage.** The in-world test needs either new baseline regions or a coordinator decision.
