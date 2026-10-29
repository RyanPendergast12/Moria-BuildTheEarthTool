# Road implementation spec (SummerlinShellTool 0.3, `scripts/shelltool_prep.py` roads section)

Source guide: `BTE ROADS GUIDEBOOK.pdf` (Brigart#0689, 70 pages). The main session read all 70 pages of text and inspected every page image as 18 contact sheets (`guide-pages/BTE_ROADS_GUIDEBOOK/sheet_*.png`). The digest is in `ROAD-STANDARDS.md`.

Every rule below is tagged as one of:
- **[GUIDE p.N]**: the guide's method.
- **[ENG]**: my engineering decision.
- **[US/LV]**: a US or Las Vegas convention.

The tool computes geometry in code. It never runs the guide's WorldEdit commands or numeric IDs, and it never reads the PDF at runtime.

## 1. Geometry pipeline (global first, then clipped)
1. **Measure, then build a blank before texturing** [GUIDE pp.1–6, 53 "sequence of steps"]. The guide measures width in imagery, then draws the road blank, then lines, then texture. The tool keeps that order, but its widths come from mapped attributes or labelled estimates (§2), not from per-road imagery measurement.
2. **Centreline** [GUIDE p.2, 44–45]: the guide selects points on the road's middle. The tool uses the OSM way centreline, the complete way from the local OSM index. That centreline is projected vertex-by-vertex into block space with TerraPlusMinus's own projection code.
3. **Surface = line of half-width** [GUIDE p.3 `//line <block> <half-width>`, p.12 `//curve`]. Each way is buffered by half its width with round caps and joins. That gives the straight road (p.1), the curved road (p.12) and the joins between consecutive ways (p.46–49 "connect 2 parts") in one deterministic operation.
4. **Clipping** [ENG]: the geometry above is computed for the whole way with about 60 m of context. Only the 256 columns of the impact chunk are then classified, by block-centre tests. Neighbouring chunks see identical geometry, so there are no seams or duplicate edges.
5. **Surface only, following the terrain** [GUIDE p.3 `//gmask <0` "change only blocks on a surface"; pp.5, 14, 50–51 shift up/down so the road follows the terrain].
   - The tool replaces only the existing surface block of each column, at the terrain height TerraPlusMinus generated from its elevation dataset. That height is checked against the expected TPM height, within ±1.
   - It never flattens. The guide's flatten-and-restack workflow (pp.34–40) is **not used**: the TPM terrain is already the elevation source of record.
   - It never uses roof heights.

## 2. Widths and cross-section (Las Vegas)
| item | rule | label |
|---|---|---|
| carriageway width | OSM `width` | measured attribute |
| | otherwise `lanes` × 3.6 m (12 ft US lane) | **ESTIMATE** [US/LV] |
| lanes | OSM `lanes` | mapped |
| | otherwise default per class (residential 2 two-way, primary 2 per direction, …) | **ESTIMATE** |
| bike lane | `cycleway*=lane` adds 1.5 m per side, with a solid white line | **ESTIMATE** [US/LV] |
| shoulders | motorway/trunk 3.0 m right, 1.2 m left; links 2.4/1.2 m | **ESTIMATE** (AASHTO typical) [ENG] |
| curb | 1 flush block outside the carriageway on urban classes (not motorway, trunk or service) | **ESTIMATE** |
| | Flush with the surface, as the guide does (kerb "43" at surface level, p.15, p.61–62) | [GUIDE] |
| mapped sidewalk | `footway=sidewalk` buffered by `width` or 1.5 m | position mapped; width ESTIMATE |
| | Overrides an *estimated* carriageway width, with a curb between them | [ENG] |
| inferred sidewalk | only from `sidewalk=both/left/right/yes`, 1.5 m outside the curb | **INFERRED** |
| | Suppressed when a mapped sidewalk lies within the would-be band | no double sidewalks |
| mapped road area | `area:highway` polygons | measured boundary |
| median | not generated: the space between separately mapped carriageways keeps its terrain | Overlapping *estimated* widths can close a narrow median: reported, not guessed |

The guide's example cross-sections (Russia, Estonia, Czechia, Liechtenstein, pp.57–62) are **not** used as standards [GUIDE p.1 "find the standard of road in the country"].

## 3. Markings (US MUTCD conventions; the guide supplies only the method)
- **Measuring dashes** [GUIDE p.6–7]: measure line and gap in imagery. The guide's examples are 9 on / 3 off and 3 on / 9 off.
  - The tool uses the US standard 10 ft line / 30 ft gap (3 m on / 9 m off, period 12 m) [US/LV].
  - Not yet measured per road from Vegas imagery.
- **Phase** [ENG]: the distance along the complete OSM way from its first node, so dashes continue across chunk borders.
- **Thin 1-block lines** [GUIDE §4.4 p.52, §4.6 p.54 queryRel thinning]: a cell is on a line when its centre is within 0.5·max(|ux|,|uz|) of the line offset. That gives exactly one cell per row or column, which is a digital line.
- **Rules** [US/LV]:
  - **Two-way marked roads:** yellow solid centre line. A second yellow line where `lanes:both_ways=1` (two-way left-turn lane). White dashed lines between same-direction lanes. A white solid line at bike lanes.
  - **One-way carriageways:** white dashed lane lines. On motorway/trunk, a white solid right edge and a yellow solid left edge.
  - **Unmarked:** residential/unclassified without an explicit `lanes` ≥ 2, and service roads.
- **Intersections** [GUIDE p.46–49 joins]: no lane markings within half-width + 2 m of a junction node shared by different ways, or where two carriageways overlap.
- **Crossings**: mapped `footway=crossing` ways, 3 m wide, with white bars parallel to traffic on alternate blocks [ENG continental-style]. None if `crossing=unmarked/no`.

## 4. Materials (1.18.2, deterministic; the guide's palettes translated)
| role | blocks | source |
|---|---|---|
| asphalt | `gray_concrete_powder`, with 1 in 4 `cyan_terracotta` chosen by a fixed hash of (x,z) | [GUIDE 5.2.1 p.63 "252:7,159:9"]. The hash makes it deterministic and idempotent instead of random. |
| white line / bars | `white_concrete` | [GUIDE "252:0"]; translated to non-gravity concrete [ENG] |
| yellow line | `yellow_concrete` | [US/LV] |
| curb | `smooth_stone` | [GUIDE "43" double smooth-stone slab look] |
| sidewalk | `light_gray_concrete_powder`, with 1 in 3 `light_gray_wool` | [GUIDE 5.3.2 p.64 "252:8, 35:8"] |

## 5. Grade separation and highways
- `bridge`, `tunnel`, `layer≠0` and `covered` carriageways are **skipped and reported** ("deck/tunnel elevation not resolved from evidence"). The ground road beneath, for example CC-215 under the Charleston bridges, is generated normally.
- No deck is ever draped on the ground or invented from a `layer` tag.
- Ramps (`*_link`) at ground level are generated. Separate carriageways come from their own one-way ways.
- Not generated: `service=parking_aisle`/`drive-through` (pilot rule), `construction`/`proposed`, paths, cycleways and pedestrian areas. All are reported.

## 6. Writer rules for road cells (plugin, re-checked in the writing tick)
1. **Strip exclusion:** checked per cell, at all heights. A chunk touching the Strip is refused before anything is fetched.
2. **Surface found and checked:** the surface is the first non-air block within ±2 of the expected TPM height. It must be within ±1 of that height.
3. **Generator material:** the surface must be terrain TPM generates: sand, red sand, grass, dirt, coarse dirt, gravel, stone, sandstone, terracotta, podzol, moss, TPM's road `gray_concrete_powder` or its outline `bricks`.
4. **Protected, never replaced:**
   - the accepted pilot road cells (plan_v1);
   - **tracked edits**: player place/break/bucket events recorded *from installation on*, on the surface cell or the cell above;
   - block entities;
   - cells with any non-air block above (vegetation, objects, landscaping, which are **not cleared**);
   - cells with entities on them.
5. **Never touched:** anything below the surface, and anything outside the chunk.
6. **Buildings win:** no road cell inside an OSM building footprint. Shells write only air above the surface, and roads write only the surface, so the two never overwrite each other.
7. **Ledger:** exact before/after state, feature, role and content. Undo restores the exact original terrain block. Cells changed since then are kept.

**Known limitation:** a block a player placed **before tracking existed**, which is a generator material at the generator height, cannot be told apart from untouched terrain. That is the honest boundary of this provenance check. Use preview first, or a staged copy, where historical hand edits may exist.
