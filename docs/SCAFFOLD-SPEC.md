# Summerlin scaffolding — geometry specification (pilot v1)

Implemented by `C:\BTE-Migration\scripts\summerlin_plan.py`. Each rule is tagged:
**[NYC-observed]** measured in `BTE NYC V3.0.0` (see `nyc-reference\analysis`), **[MineFact-documented]** from his videos,
**[pilot choice]** a decision made here where neither source specifies (flagged so you can change it).

## 0. Frames and alignment
- World: Paper 1.18.2, TerraPlusMinus 1.3.3, BTE projection (BuildTheEarth Conformal Dymaxion, scale 7318261.522857145), offsets x/y/z = 0, world Y −1024…2031.
- Locally at the pilot: 1 m ≈ 1.017–1.022 blocks horizontally; true north is rotated **11.4°** clockwise from −Z. Vertical: 1 block = 1 m.
- NYC V3 uses the same BTE projection (One WTC footprint IoU 0.85 unshifted, Woolworth 0.83; best shifts ≤2 blocks) and 1 block = 1 m vertical with sea level ≈ Y 0 [NYC-observed].
- Geography→block: every block column centre is inverse-projected with TerraPlusMinus's own code (`column_geo.npz`) and tested against footprints in UTM 11N metres.

## 1. Footprint rasterization and corners
- A column belongs to a footprint if its **block centre** lies inside the OSM polygon (inner rings/courtyards excluded) [pilot choice; standard point-in-polygon, same idea as MineFact's `PointInsidePolygon` class — MineFact-documented class name, algorithm details not visible].
- Corner placement is therefore within ±0.5 block of the projected vertex; the 11.4° grid rotation produces stair-stepped edges (inherent to BTE).
- Buildings not entirely inside the pilot are **skipped**, never clipped [pilot choice — no half buildings].

## 2. Building shells (type A)
Observed NYC type-A shell (e.g. component 51 at X −8527378..−8527274, Z −6023623..−6023541; component 225 near NYU):
- Walls **1 block thick, hollow interior**, single gray wall material (`stone`, or `dead_brain_coral_block`) [NYC-observed].
- **Closed flat roof cap of `light_gray_wool`** per part [NYC-observed].
- **Building-part / roof-break outlines drawn on the roof in the wall material** [NYC-observed].
- **Stepped massing** where parts have different heights (up to 15 setback levels) [NYC-observed].
- No windows, glass, or markers on shells (0% glass on surface) [NYC-observed]; MineFact's 2020 shells were solid single-colour boxes [MineFact-documented]; V3 shells are hollow — conventions evolved.

Pilot implementation:
- Wall = `minecraft:stone`, roof = `minecraft:light_gray_wool`, part outlines/roof edge = `minecraft:stone` (all exist in 1.18.2).
- **Heights are measured**, not extruded from a single attribute: USGS 3DEP lidar NV_ClarkCo_2_B22 (acquired 2022-07-08…08-01), single-return non-ground points, 1 m nDSM = DSM − DTM, 3×3 median per block column.
- **Parts**: columns grouped into connected 1 m height bands; regions <25 columns merged into the closest-height neighbour; neighbouring parts within 1.5 m merged. Part top Y = world ground median of the footprint + round(median nDSM of the part).
- Columns <2 m above ground inside a footprint (courtyards, arcades, open decks) get **no shell** and are counted.
- Walls run from each column's own world ground +1 to the part top −1 on the outer boundary; on inner boundaries only above the lower neighbour's top (setbacks).
- **Roof equipment and peaks** (lidar p99 above main roof) are **not built**; values recorded per building (`roof_m.p99_peak_or_equipment`).
- Pitched roofs are not modelled separately in v1: a sloped roof becomes 1 m terraces (visible as part outlines). [limitation]

## 3. Height provenance and uncertainty
| source | used when | label |
|---|---|---|
| `lidar` | ≥20 roof returns and ≥50% of the footprint >2 m above ground | measured (2022) |
| `levels-estimate` | no usable lidar but `building:levels` tagged | levels × 4.5 m — **estimate** |
| `unresolved` | otherwise | **no shell**; 1-block ground outline in `orange_wool` [pilot choice, not an NYC convention] |
- LiDAR vs world terrain: TerraPlusMinus terrain is on average 1.0 m below the LiDAR DTM (std 1.7 m) at the pilot — so heights are applied **relative to the world ground**, not as absolute elevations.
- Changes since July 2022 are not in the LiDAR; such buildings fall to `unresolved`.

## 4. Streets
- NYC finished streets: carriageway and sidewalk at the same Y (no curb step), edges marked by material bands; tree pits/drains 4 blocks below [NYC-observed, Greenwich Village/Hudson Greenway samples]. No clearly "preliminary" road state was found in V3 [limitation].
- MineFact: roads, sidewalks, trees and lights were done manually by an "advanced team" [MineFact-documented].
- Pilot: carriageways from OSM centrelines, replacing the **ground surface block** with `gray_concrete_powder` (TerraPlusMinus's own road material). Width = OSM `width`, else `lanes × 3.6 m` **estimate** (dual carriageways are separate one-way ways). Sidewalks/crossings from OSM `footway=sidewalk|crossing` lines, 2.0 m **estimate**, `smooth_stone`. Carriageway wins over sidewalk; buildings win over both.
- Skipped and listed: bridges, tunnels, `layer≠0`, covered ways, parking aisles, drive-throughs (grade separation is preserved by not flattening it).

## 5. Safety gates (deny-by-default, every block)
G1 block column entirely inside the pilot polygon · G2 outside the Strip exclusion polygon (+500-block buffer) · G3 Y within −1024…2031 · G4 the existing block is generator terrain, air, or this spec's own scaffold material. Any G4 conflict skips the **whole** building. Roads skip individual conflicting columns. Gates are asserted again when the manifest is written.

## 6. Application, idempotency, rollback
- Plan → `plan.jsonl` (x, y, z, old, new, feature, role) → `/fill` runs in `apply_*.mcfunction`; `rollback_*.mcfunction` restores every `old` state.
- Re-planning against an already-applied world yields 0 changes (blocks whose old == new are dropped).
- Only ever applied to a separate test server copy with the server stopped for every copy/diff.
