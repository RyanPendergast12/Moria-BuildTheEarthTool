# Egg terrain: specification (v0.5)

Implemented by `prep/egg_terrain.py` (`plan(ctx)`) and `prep/egg_provenance.py` (`baseline_columns(tx, tz, world_dir)`).
Contracts: `docs/egg/ARCHITECTURE.md`. Labels: **[guide p.N]** = BTE Terraforming guide (Xylotrupes, 7 pages), **[measured]**, **[source]** = data-provider documentation, **[choice]** = a decision made here (tunable).

## 1. What the terrain content does
It moves the **baseline** ground surface of eligible, undeveloped columns toward the measured bare-earth height, by at most 2 blocks, and only where the error is clearly larger than the noise. It never builds, flattens or clears.

## 2. Rules from the BTE Terraforming guide, translated
The guide was read in full (pages 1–7, text and figures). It is written for manual work with WorldEdit in the old BTE modpack. Nothing is run as a command: every rule below becomes a bounded plan rule.

| guide | what it says | Egg rule |
|---|---|---|
| p.1 | Terraforming is mostly re-adjusting terrain elevation, using a source that ties elevation to coordinates. | Target heights come only from a georeferenced measurement (3DEP lidar ground), projected with TPM's own code (`geo.py`). |
| p.2 | Check each source's contour interval and its units (metres vs feet); contours give ~10 m resolution at best and need interpretation. | Units and datum are checked per source (§3). Lidar ground points (6–21 per m² measured here) replace contours; no interpretation step. |
| p.2–3 | Hatched contours mark depressions; a both-sides-hatched line is a ridge. | Depressions and ridges are kept automatically: the target follows the measured surface, not an interpolation between lines. |
| p.3 | Water flows at the lowest point of a valley; a river is the lowest line between contours. | **Hydrology is never changed**: columns in or within 6 blocks of mapped water/waterways (OSM, `protect` bit 16) or lidar water (class 9) are not touched, so no cut can go below nearby water and no wash is filled. |
| p.4 | V-shaped contours point upstream; even without a river they show erosion channels (washes). | Washes are kept: same exclusion as above (OSM `waterway=*` lines are buffered 3 blocks by the context builder, then 6 more here). Unmapped washes are protected only by the ±2 clamp and the 2-block threshold (open issue §9). |
| p.4 | "The more the better": use every dataset, confirm with a second source. | Second source = the baseline world itself: the regional bias (§4) is measured from thousands of columns; coverage, density and gaps are checked per tile (§5). |
| p.5 | Top soil comes from satellite imagery. | No imagery is used: the **surface material is kept** (`base_block`); a cut re-exposes the same top block, a fill is capped with it. |
| p.5 | Transfer contours with marker blocks (`//curve #clipboard` of gold + air). | No marker blocks are ever placed. The measured height per column plays the role of the gold markers. |
| p.5–6 | Smooth between markers: `//gmask !gold_block`, `//sel convex` over 8 points per 4 markers, then delete the diamond line blocks. | Local, bounded interpolation only: a 5×5 mean of the measured target (never of the world terrain), feathered over 4 blocks from protected data. **No `//smooth`, no area-wide smoothing, no convex selections, no clearing.** Each change is a ledgered per-block op. |
| p.6 (figure) | The worked example shows terraced 1-block steps on a slope. | The ramp (0 next to unchanged ground, full 5 columns in) keeps changes gradual, as in the figure. |
| p.7 | Mods (commandKeybinds) and elevation databases (NSW SIX, ArcGIS, OpenDEM). | Not used: none covers Nevada better than 3DEP. |

## 3. Sources
| source | coverage / date | resolution | units, CRS, datum | role, limits |
|---|---|---|---|---|
| **USGS 3DEP lidar, `NV_ClarkCo_2_B22`** (EPT on AWS `usgs-lidar-public`) | Clark County; flown 2022‑07‑08…08‑01 (Merrick‑Surdex JV); QL1, 1.0 m DEM GSD; work unit 300193 | point cloud; measured class-2 density 6–21 pts/m² in pilot tiles | Source: NAD83(2011) / Nevada East ftUS (EPSG 6521), NAVD88 height ftUS (EPSG 6360), GEOID18. **EPT:** horizontal EPSG:3857 (`ept.json` `srs.horizontal = 3857`, no vertical code), z in **metres** (verified: 922–925 m at the pilot, matching TPM within ~1 m; feet would read ~3030). Reprojected here to UTM 11N (EPSG:32611) for metric radii. | Class 2 ground = target; class 9 water = exclusion; 7/18 noise ignored. Hydro-flattened. Tested NVA RMSEz 4.5 cm. Shows 2022: later grading is not in it. Public domain (USGS); attribution "USGS 3D Elevation Program". [source: USGS NV_ClarkCounty_B22 project report, 2023‑11‑30] |
| `NV_ClarkCo_1_B22`, `NV_ClarkCounty_B1_2018`, `NV_LasVegasValley_2010` | fallbacks, in this order (same list as `shelltool_prep.DATASETS`) | QL2 / older | as above (EPT 3857, metres) | Used only where the 2022 QL1 unit has no ground points. |
| **USGS 3DEP 1 m DEM** | The National Map | 1 m (and 1/3″ ≈ 10 m) | NAD83, NAVD88, metres [source: USGS FAQ] | **Not used** now: it is derived from the same lidar ("bare-earth" product, surface features removed and ground resampled on a grid [source: USGS FAQ "What is the difference between lidar data and a DEM?"]). Kept as the documented alternative where lidar is missing; such tiles report `no_coverage` today. |
| **TerraPlusMinus heights** (what generated the world) | worldwide | AWS Terrain Tiles, Terrarium PNG, **zoom 13** (≈ 15 m/px at 36° N), cubic blend (`heights.json5` inside `terraplusminus-1.3.3.jar`; no local override in `tpp-cwd/terraplusplus/config/heights`) | metres; US source is 3DEP/NED (NAVD88) in the Terrain Tiles composite | The baseline world: top block at `floor(height)` (verified 16/16 samples, `egg_provenance` self-check). Smooth at ~15 m, so it misses small relief; that difference is what Egg corrects. |
| **Approved baseline** | (1) plugin-captured new-chunk baselines `<MORIA_EGG_CAPTURED>/<cx>_<cz>.json` (`egg-baseline-1`), checked first; (2) the pilot snapshot `summerlin/pilot/baseline-world-after-generation`, **only the 910 chunks in `generation_chunks.json`** (TPM-generated for the pilot: tiles x −189999…−189991, z −85169…−85160). The same folder holds ~329 regions of the converted purchased world (Strip, east valley): **not** approved terrain, always `None`. | per block | world Y = metres (1 block = 1 m), min_y −1024, height 3056 | Provenance of untouched terrain. Per-chunk source is reported in `baseline_info.chunk_sources` (`captured` / `pilot-generation` / `none`). |
| OSM (local index + landcover index) | Geofabrik 2026‑10‑25 | vector | lon/lat | Protection (roads, buildings, water, landscaped, developed). |

## 4. Bias: world vs lidar (measured once, regional)
- **Definition:** `bias = median(base_y + 0.5 − lidar_ground_m)` over uncovered natural baseline columns (protection ignored, see below), where `base_y + 0.5` is the centre of TPM's top block (TPM puts it at `floor(h)`), and `lidar_ground_m` is the per-column ground of §5.
- **Applied once, on the lidar side only:** `target_y = floor(lidar_ground_m + BIAS_M)`. The world is never shifted, so corrected columns stay continuous with roads, buildings and every column Egg does not touch.
- **Fixed constant** (`BIAS_M` in `egg_terrain.py`), never per tile: a per-tile median would differ between neighbours and create seams.
- **Measurement (2026‑10‑29, `--measure-bias`, 69 pilot tiles, NV_ClarkCo_2_B22 class 2):**
  - all uncovered natural baseline columns of the 910 pilot chunks: **n = 145 388, median −1.024 m**, mean −0.83, std 1.92, p5 −3.74, p95 +2.35 → **`BIAS_M = −1.02`** (the constant used);
  - cross-check, only unprotected (eligible) columns: n = 4 352, median −1.87, std 1.71. Fewer, clustered columns in partly graded lots, so not used as the regional constant; it shows that those lots really differ from the TPM model by ~1–3 m (what Egg corrects).
- Why "bare" and not "eligible": the offset comes from the TPM model and datums (Terrain Tiles z13 cubic vs. 2022 QL1 ground), not from land use; protection only decides where Egg may write.
- Independent earlier check: `summerlin/pilot/terrain_vs_lidar_offline.json` (49 points, TPM dataset minus lidar DTM): median −1.05, mean −0.96, std 1.70 m. Consistent.
- Re-measure with `python prep/egg_terrain.py --measure-bias` only if the baseline world or the lidar dataset changes; then update `BIAS_M`, `BIAS_PROVENANCE` and this section, and bump the prep pipeline version so cached plans are rebuilt.

## 5. Algorithm
**Data field** (window = tile + 32-block ring, 128 × 128 columns). It uses only data whose content does not depend on when chunks were generated or captured, so it is the same in every tile's window:
1. **Protection**: `egg_common.protection_mask` for each of the 9 tiles (Strip, roads, buildings, `area:highway`, unknown OSM) plus landcover bits (water, landscaped, developed: same classes as `egg_prep.landcover_bits`).
2. **Lidar ground**: block centre → lon/lat (TPM inverse) → UTM 11N; median z of class‑2 points within 1.0 m (≥ 3 points), else within 2.5 m (gap fill), else no value. The EPT box is the window padded by 7.5 m, so every column sees the same points from any tile.
3. **Water**: columns within 6 blocks of mapped water or lidar class 9 (within 2 m).
4. **Data-OK** = no protection bit, lidar value present, not water. Everything else is *protected by data*.
5. **Target** = 5×5 mean of (lidar + `BIAS_M`) over data-OK columns.
6. **Feather cap** = round(((distance to the nearest data-protected column) − 1) / 4 × max), clipped to [0, max], separately for cut and fill: 0 right next to protected data, full from 5 columns in.

**Per column, inside the tile only** (`change()`): `r = target − (base_y + 0.5)`. If `|r| < 2` (THRESH): no change. Otherwise the column moves by `floor(|r| − 2 + 1)` blocks toward the target (just enough to bring it under the threshold), capped by the feather cap. Columns whose baseline is unknown, or covered, or not NATURAL are **not written** and are counted (`skipped_no_baseline`, `skipped_covered`); they do **not** feather their neighbours.

**Order independence (review M3).** The baseline of the ring is never read. A column's change depends only on the data field (identical in both tiles' windows for columns ≥ 7 from the window edge; every tile column is ≥ 32 from it) and on that column's own `base_y`. Preparing A before or after B's chunks exist, or before or after B is captured, gives the same in-tile ops. Self-check: (1) with `egg_provenance.baseline_columns` disabled, `plan(A)` gives identical ops; (2) for three edge pairs, 16 columns on each side of the shared edge get the same change from either tile's window (56 and 76 non-zero changes on two pairs).

## 6. Ops, limits and refusals
- **Cut** (delta < 0): `[x, y, z, 'base', 'minecraft:air', feature, 'cut']` from `base_y` down to `base_y + delta + 1` (top-down); then always `[x, base_y + delta, z, 'base', base_block, feature, 'surface']`: TPM lays **one** surface block over stone (checked: all 2 156 bare columns of the cut test tile are sand over stone, stone, stone), so a cut would otherwise expose stone.
- **Fill** (delta > 0): if the surface differs from its subsurface, the old top becomes subsurface (`'base'`, role `surface`, e.g. grass_block → dirt); then `'air'` fills bottom-up, the top one = `base_block`, below it the subsurface (`sand` → sand, `grass_block` → dirt, `podzol` → dirt, `smooth_sandstone` → sandstone).
- Feature id `terrain:<tx>_<tz>:r<k>` per connected changed region; `partial: true` when the region touches the tile edge (its continuation is planned identically by the neighbour); confidence `measured`.
- **Limits:** `max_cut`, `max_fill` (default 2) clamp every column; no cave filling (fills only go into air directly above the baseline top); no excavation deeper than `max_cut`.
- **Statuses:** `no_coverage` (**landcover index missing or not covering the tile + ring** (review B4: never plan without water/wash/landscape data), no baseline in the tile, or no lidar dataset); `protected` (no writable column); `low_confidence` (lidar covers < 80 % of writable columns or ground density < 0.5 pts/m²; no ops); `nothing_to_do` (everything within 2 blocks); `refused_limits` (ops > `max_ops`, no ops returned); `ready`.
- **`complete`** (plan key): `true` normally; `false` when any source is degraded: every `no_coverage` / `low_confidence` result, OSM coverage unknown anywhere in the window (bit 128), a fallback lidar dataset instead of NV_ClarkCo_2_B22, or any lidar gap in a writable column.
- **Stats:** columns known/eligible/corrected, skipped_no_baseline, skipped_covered, cut/fill blocks, regions, max |residual| before and after, lidar cover, ground density, gaps, wet columns, bias, timing.

## 7. Test tiles
| tile | block range (x, z) | in-game test spot | expected |
|---|---|---|---|
| **fill test** (−189993, −85164) | x −12159552…−12159489, z −5450496…−5450433 | `/tp -12159510 921 -5450459` (sand lot between developed parcels) | `ready`, complete: 261 eligible, 127 corrected, 127 fill blocks, mean residual 2.40 → 1.91 blocks |
| **cut test** (−189998, −85167) | x −12159872…−12159809, z −5450688…−5450625 | `/tp -12159854 929 -5450682` (south-west pilot edge) | `ready`, complete: 346 eligible, 185 corrected, 185 cut blocks + 185 surface ops (370 ops), mean residual 2.36 → 1.83 |
| seam pairs | (−189993, −85164) with (−189993, −85163) and (−189992, −85164); (−189998, −85167) with (−189997, −85167) | | 2 × 16 × 64 edge columns get the same change from either window (56 / 0 / 76 non-zero); ops identical without any ring baseline |
| no change (−189993, −85166) | x −12159552…−12159489, z −5450624…−5450561 | most unprotected columns in the pilot (907) | `nothing_to_do`: max residual 1.88 < 2 |
| Desert Inn sizing tile (−190004, −85126) | x −12160256…−12160193, z −5448064…−5448001 | block −12160205, −5448042 | `no_coverage`: region `r.-23751.-10641` is not in the approved baseline |

**No sloped undeveloped land exists inside the approved baseline.** The 910 pilot chunks are developed Summerlin (OSM `residential`, `construction`, `retail` landcover); the eligible columns of any pilot tile span at most 4 blocks of height. Sloped desert (Red Rock foothills, west Summerlin edge) needs a captured baseline first (plugin new-chunk capture), then the same tests apply.

Timings (no server; laptop): cold lidar cache 15.8 s (fill tile, ≈ 18 MB of EPT tiles fetched) / 7.8 s (cut tile, partly shared); cached 1.4–2.2 s per tile; Desert Inn 0.1 s (refused before any fetch). Baseline read: 0.03 s per tile (16 chunks). `egg_prep.py <cache> -189998 -85167 --content terrain` end to end into an empty cache: 14.1 s (lidar fetch), 370 ops, `ready`, complete.

## 8. Dependencies
None added. Uses numpy, scipy (`ndimage`, `cKDTree`), pyproj, shapely, laspy (via `ept_cached`), all already in the venv.

## 9. Open issues
1. **Baseline coverage** is the real limit: only the 910 pilot chunks (and future captured chunks) are eligible. Everything else reports `no_coverage`, including the Desert Inn sizing tile.
2. **Landcover classes are duplicated** from `egg_prep.landcover_bits` (`_window_protect` also treats `natural=wetland` as water). If they drift, a tile's own `protect` may be stricter than the window field: safe (those columns are just not written), but the feather would not ramp toward them. Suggest moving one classifier into `egg_common` (coordinator).
3. **Unmapped washes**: only OSM waterways and lidar class 9 are excluded. A dry wash that is neither is protected only by the threshold, clamp and feather.
4. **Dataset switching at coverage edges**: each window picks the first dataset with ground points. Two neighbouring windows on either side of a dataset boundary could pick different datasets (seam). Not present in the pilot (all NV_ClarkCo_2_B22).
5. **Lidar date** 2022: grading after July 2022 is not reflected; the plugin's write-time checks still protect anything built since.
6. **Fill subsurface**: fills below the cap use the surface's subsurface family (sand under sand, dirt under grass_block), not stone, so a 2-block fill of a sand column is sand on sand on (old) sand over stone. Deliberate: it keeps the visible material and avoids exposing stone on later cuts.
7. `egg_provenance.baseline_columns(tx, tz, world_dir, captured_dir=None)` takes the captured dir as a keyword (default env `MORIA_EGG_CAPTURED`); `egg_terrain` reads `ctx['captured']` if `egg_prep` provides it.
