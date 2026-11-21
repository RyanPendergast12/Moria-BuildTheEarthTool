# Facade references (v0.6, agent A)

Retrieved and checked on **2026-10-29**. This file says what each source gives the facade tool, what it does **not** give, and how guide rules become generator rules.

Every statement in the package evidence files carries one of four labels:
- **reference-matched**: seen in a dated, located reference image;
- **measured**: from LiDAR, OSM or ortho geometry;
- **inferred**: derived from the above by a stated rule;
- **unresolved**: not covered; the compiler produces no ops for it.

Footprints, OSM and LiDAR never reveal windows, signs or entrances. Those come only from dated imagery.

## 1. Primary sources

| source | what it gives us | what it does NOT give us | licence / access (2026-10-29) |
|---|---|---|---|
| **HueBlocks** — https://1280px.github.io/hueblocks/ , https://github.com/1280px/hueblocks | An online gradient and palette aid. You pick colours or blocks, and it fills N blocks between them. Its features: a Java Edition version selector (1.8.9, 1.13.2 and "latest" 26.1), RGB or OkLAB colour space, a filter by texture facing, and palette JSON import/export. Useful to agent C as a **manual** check of colour-before-texture choices (Detail p.30). | Building identification, facade layout or any per-building data. Its block sets are not 1.18.2: "latest" is 26.1, and it omits textures that have transparent pixels, such as trapdoors. | **No licence file** (GitHub API `license: null`, 404 on /license), so treat it as all rights reserved. Do **not** vendor its code or block-set data into `prep/facade_assets`; cite it only as a manual colour reference. Repo pushed 2026-06-26. |
| **Mapillary** — https://www.mapillary.com/app/ ; API doc https://www.mapillary.com/developer/api-documentation (the help-centre article returned HTTP 403 to our fetcher) | Street-level imagery through the Graph API: `GET https://graph.mapillary.com/images?access_token=…&fields=id,captured_at,compass_angle,geometry,creator,thumb_1024_url&bbox=minLon,minLat,maxLon,maxLat`. `captured_at` is epoch ms; the bbox must be under 0.01 deg²; at most 2000 results. Rate limits: 10k/min for search, 60k/min for entity calls. | Anything without a token. Coverage at Red Rock is **unknown**, because no query was possible. | **Capability missing:** `MAPILLARY_TOKEN` and `MAPILLARY_ACCESS_TOKEN` are unset, and `C:\BTE-Migration\facade\secrets\` does not exist. To enable it, the user creates a client token at https://mapillary.com/dashboard/developers and sets `MAPILLARY_ACCESS_TOKEN` in the user environment. It is **never** committed or written into a package. Imagery is CC BY-SA 4.0 per Mapillary's terms; the API doc itself names only "© Mapillary", so record per-image attribution (`creator`). |
| **OSM Simple 3D Buildings** — https://wiki.openstreetmap.org/wiki/Simple_3D_Buildings | Semantics our geometry must honour (section 1a below). | Facade content. In practice our ways carry almost none of these tags. **None** of the Red Rock ways has `building:part`, `height`, `building:levels` or `roof:*`, so heights come from LiDAR (agent B). | Wiki text CC BY-SA 2.0; OSM data ODbL 1.0. |
| **FastAsyncWorldEdit** — https://modrinth.com/plugin/fastasyncworldedit ; docs https://intellectualsites.gitbook.io/fastasyncworldedit/ ; source https://github.com/IntellectualSites/FastAsyncWorldEdit | Modrinth API (`/v2/project/fastasyncworldedit/version`, 56 versions): **builds 2.4.6 (2022-10-02) to 2.9.2 (2024-04-04)** list game version 1.18.2 (loaders paper/spigot; 2.4.x also purpur). Newer builds drop it; the newest, 2.16.1 (2026-10-09), targets 26.x. | Any measured benefit for us. The docs pages state no Java floor. Paper 1.18.2 itself needs Java 17, so a 1.18.2-era FAWE build would run on our JDK 17, but that is **not verified** from FAWE docs. If it is ever evaluated, check the jar's class-file version. | GPL-3.0-only. **Evaluation-only. No recommendation:** no bottleneck has been measured. Facade writes go through the protected writer (ARCHITECTURE §6), which FAWE does not replace. |
| **YouTube** "How to Texture Your Builds \| Build The Earth" — https://www.youtube.com/watch?v=HKJAn_cWxQU | **Verified** via oEmbed and the watch page: title "How to Texture Your Builds \| Build The Earth"; channel **Build The Earth** (@BuildTheEarth); published **2021-05-02**; length 347 s. The description credits: voice Shy Guy, editing London, footage Fedora Man / London / d1m4, script Fedora Man / London. | **No transcript obtained.** The page lists one caption track, English auto-generated (ASR). The signed `timedtext` URL, and `timedtext?v=…&lang=en` / `type=list`, all returned HTTP 200 with an **empty body**. I did not watch the video and cite **no timestamps**. Its texturing advice is covered in writing by Detail pp.24 and 30. | Standard YouTube licence; reference only. |

### 1a. OSM 3D tags the geometry must honour (B, D1)
- **`building` vs `building:part`:**
  - The outline (`building=*`) stays the 2D footprint and carries the address and name.
  - When parts exist, the outline is **not** rendered in 3D; the parts fill it and must not overlap.
- **`height`:** ground to roof top, **excluding** antennas and rooftop equipment. This matches our "never count machinery" rule (ARCHITECTURE §8).
- **`min_height`:** the bottom of the part above ground. `height` is still measured from the ground. Use it for bridges, overhangs and cantilevered slabs.
- **`building:levels` / `building:min_level`:** above-ground floors, excluding roof levels. They are a hint for `floors.count`, never a substitute for a measured `height`.
- **`roof:shape`:** flat, skillion, gabled, half-hipped, hipped, pyramidal, gambrel, mansard, dome, onion, round, saltbox.
  - **`roof:height`:** facade height = `height − roof:height`.
  - **`roof:angle`:** may imply the roof height instead.
  - **`roof:levels`**, **`roof:direction`** and **`roof:orientation`** (along/across).
- **`building:colour`, `building:material`, `roof:colour`, `roof:material`:** palette hints only. A dated image outranks them.

## 2. Local guides: guide rule → generator rule
The guides are under `C:\BTE-Migration\summerlin\detailing\guide-pages\` (rendered pages plus `text.txt`), and the digest is `summerlin\detailing\BUILDING-STANDARDS.md`.
- **Detail** = BTE Detailing Guidebook;
- **Outlines** = How to fix outlines;
- **Res** = Builder Resources.

The PDFs stay in a private local folder and are not copied.

| topic | guide rule (page) | generator rule (compiler/components/palettes) |
|---|---|---|
| Floor lines first | Every glass facade shows its floor lines, made with a glass-colour change or thin blocks (trapdoor, slab, carpet). They may project, recess or sit flush. "Never randomly mix glass." (Detail pp.2–3) | Every glazed face has a `bands` rule at each `floors.height`. One band element per face (flush, `w=+1` or `w=−1`), the same on every floor. The palette has no random glass. `texture` allows only `rows%k`, `cols%k` and seeded `hash` rules (ARCHITECTURE §8a). |
| Mullion spacing | Vertical lining every ≤2 blocks; 3–5 is rare. Materials: end rods, panes, bars, fences, alternating glass. (Detail pp.3–4) | `bays.width` defaults to 2 for curtain wall. Use 3–5 only when the reference shows it. The mullion role prefers non-connecting blocks (end_rod, alternating stained glass). Panes and bars only with compiler-computed states. |
| Glass colour | Real glass from blue-gray to black → cyan, gray or light_gray stained glass. Odd colours only with other detail; never clear glass alone. (Detail pp.4–5) | The palette `glass` role must be a stained glass. `minecraft:glass` alone is refused by C's palette lint. Bronze or reflective glass needs a stated colour reference. |
| Backing depth | Solid backing right behind the glass, at most 2 blocks; never a concrete shell deep inside. gray/light_gray concrete or cyan terracotta; no black behind gray or black glass; no ice. (Detail pp.4–5) | Components put `backing` at `w=−1` (max `w=−2`). The schema rejects `inward_backing > 2`. Palette lint: backing ≠ black_concrete when glass is gray or black stained glass. |
| Fake interiors and lights | Optional, never random: commit or omit. (Detail pp.5–8) | Not in v0.6. No light sources unless the package assigns them. |
| Window insets | A 1-block inset adds depth; avoid more than 2; inset glass reads darker, so go one shade lighter; modern flush facades are often better without one. (Detail pp.9–10) | `openings[].inset` ∈ {0,1,2}, default 0 for curtain wall and 1 for punched windows. If inset ≥1, the palette may shift `glass` one shade lighter (explicit, not automatic). |
| Frames | Thin blocks for dividers. A vertical split uses non-connecting blocks (end rod, fence gate, banner); a horizontal split uses trapdoor, carpet, slab or pressure plate. Variants: 2-way (pp.11–12), ≥3 stripes (p.13), square grid / checkerboard (p.14), quartered (p.15). (Detail pp.10–15) | Component families `window-split-v`, `window-split-h`, `window-stripes-n`, `window-grid` and `window-quarter`, built from the role list in C's components. No banners, because they are block entities and the writer skips them (ARCHITECTURE §6). |
| Thin piers | Close pairs of windows: doors, trapdoors, walls or fences; banners can read as frames. (Detail pp.16–18) | `pier` component with explicit door/trapdoor states. Multiblock doors must be complete in one component (ARCHITECTURE §4). |
| Fitting patterns to widths | Example: 5 windows on a 14-block wall. First adjust the gaps, then the window widths, then add or drop 1–2 bays on long repeated facades. (Detail p.19) | `bays.residual` ∈ `center|ends|absorb`, applied in that order of preference. `bays.count` may change by ≤2 only when `floors.count`×bays is large (long repeated faces). **Never** stretch a face or move a shell. |
| 45° and stepped walls | A pane between the two stepped blocks. (Detail p.19) | Our walls step at 11.4°, from TPM rotation. Openings stay on the voxelised `columns` (ARCHITECTURE §8a). No filler straightens the line. Panes only at true diagonal corners, with computed connections. |
| Hoods and sills | Small roofs above windows: stairs and slabs. Below the window: inset or outset with stairs and slabs. A line above or below: slabs or trapdoors. (Detail pp.20–21) | Optional component cells at `v=-1` (sill, `w=+1`) and `v=size.v` (hood). Placeholders `{facing}` and `{facing_in}` set the stair orientation. |
| Greenery | Half bushes, leaves, vines; only where the past or present street view shows it. (Detail p.20) | Not generated unless the package cites a reference. Leaves `persistent=true`. |
| Pillars / columns | Two pillars within 2 blocks: merge, re-space or combine thin blocks (pp.22–23). Texture by alternating 2 near-identical blocks, at most a light gradient (p.24). Width: 1–2 m → walls or fences plus trapdoors; 2×2 → solid, widened base; thin → end rods, fences, walls, bars, panes (pp.28–29). Classical capitals only if shown (pp.25–27). No armor stands (p.24, and our rule). | `columns` rule with `width` 1/2/thin. Texture is `rows%2` alternation only. No entities. |
| Colour before texture | Pick the block whose colour matches, even wool for brick; then the closest texture among same-colour blocks. (Detail p.30) | Palettes record `colour_refs.target_srgb` and the source photo id. Agent C ranks candidates by colour distance first (OkLAB). HueBlocks can be a manual cross-check. |
| Outlines | Angled imagery: copy and paste the corner offsets, then `//line`. Complex shapes: use roof-to-base offsets. (Outlines pp.2–7) | Investigation only. A tall-building lean in the ortho (Red Rock's tower) is **not** a reason to shift a footprint. Footprint changes are a separate, user-approved job. |
| Imagery sources | Bing, Mapillary, Apple, older Street View dates (Res pp.9–10). | Every reference carries a source and date in `sources.json`. Eras are never mixed without saying so. |
| No-update tricks | Floating and half blocks disappear on the next update. (Res pp.3–4) | Only states that survive neighbour updates. The compiler computes connection states. No gravity blocks over air. |

## 3. Reusable method notes from existing work (not redone)
- **Harlo** (`summerlin\detailing\jobs\harlo-w348127357`): the reference-matched regression example. See `packages/harlo-w348127357/EVIDENCE.md`.
  - Its method was:
    1. Ray-cast heights from the frames at a known px/m.
    2. Count mullions by `u` order, so stepped columns never double up.
    3. Mark unseen faces UNFINISHED and leave the shell.
  - This maps directly to `evidence.class` and `unresolved` in `facade-package-1`.
- **Ortho method:** `C:\BTE-Migration\scripts\ortho.py` mosaics Esri World Imagery tiles (`services.arcgisonline.com/.../World_Imagery/MapServer/tile/{z}/{y}/{x}`). Private reference only. The tiles carry **no capture date**, and the Esri metadata service (`metadata.maptiles.arcgis.com/.../World_Imagery_Metadata`) answered 404, so ortho dates remain unresolved.
- **Street View:** `C:\BTE-Migration\scripts\sv_refs.py` pulled Harlo frames from an unkeyed internal thumbnail endpoint, with pano ids read off the Maps page. It is **not** a configured API key, so I did not use it for Red Rock; the task allowed it only "with a key already configured". Whether to use it remains the user's call (open question).
- **NYC reference:** read-only skim of `nyc-reference\research\minefact-research.md`. NYC shows hand-detailed buildings next to plain shells, and no generator for windows. Nothing reusable for facade generation beyond "detail only where evidence exists".

## 4. Simpler style-test buildings (Downtown Summerlin pilot, accepted shells)
All are inside the accepted pilot (`summerlin\pilot\plan_v1\features.json` status `shell`). Heights are LiDAR 2022 (`pilot\buildings_measured.json`). They have **no street-level references yet**, so they are suited to `style:` tests and labelled `STYLE-GENERATED`.

| case | OSM id | name | footprint | parts (accepted shell) | evidence available | test purpose |
|---|---|---|---|---|---|---|
| flat facade | `w558879873` | H&M (`building=retail`) | 5-node rectangle, 926 m², no diagonal edges | 1 part, top Y932 (10.8 m), 957 columns, 2,096 planned blocks | OSM, LiDAR, accepted shell | Plain bays and floors, residual handling, 11.4° stepping on straight faces |
| angled corner | `w558884282` | California Pizza Kitchen (OSM name misspelt "Callifornia Pizza Kichen") | 7 nodes; a 15.2 m edge at ~45° and a 32.4 m edge at ~36° to UTM | 1 part, top Y926 (5.9 m), 788 columns | OSM, LiDAR, accepted shell | Openings on diagonal and stepped wall lines, corner ownership (ARCHITECTURE §8a) |
| parts and setbacks | `w1171439648` | Shake Shack block (`building=retail`) | 15 nodes; several diagonal edges | 11 shell parts from Y928 (5.8 m) to Y938 (16.3 m) | OSM, LiDAR, accepted shell | `shared` edges between parts, per-part floors, setback parapets |
| alternate (parts) | `w348127345` | unnamed retail (the "retail cluster" in the pilot report) | 21 nodes | 4 parts, Y930–Y940 (5.7–16 m) | same | backup multi-part case |

## 5. Rule for face `evidence.coverage` and `evidence.confidence` (all A packages)
**Coverage** is the fraction of the face's u-length (columns) visible in at least one source listed in that face's `evidence.sources` and dated in `sources[]`.
- The source's `coverage` list must name the face.
- LiDAR, OSM, ortho and job documents do not count toward coverage of a facade.
- A face with no covering dated image has coverage 0, so it is unresolved (< 0.5).
- Coverage is judged from the image against the face geometry. It is an estimate in steps of 0.1, with the exact column fraction where one was ray-cast (Harlo S 0.53).

**Confidence** = 0.35 + **S** + **R** − **O**, clamped to [0.1, 0.95]:
- **S (source count):** +0.15 for each covering dated image beyond the first, up to +0.30.
- **R (recency of the newest covering image, against 2026):** ≤ 3 years +0.25; ≤ 5 years +0.15; ≤ 10 years +0.05; older 0.
- **O (occlusion of the least-occluded covering image):** low 0; medium 0.10; high 0.20; very high 0.30.
- **Adjustments:**
  - −0.10 when the face's layout combines two eras (stated in its notes);
  - × 0.8 for `class: inferred`.

The compiler treats coverage < 0.5 or confidence < 0.3 as unresolved. Under this rule, Red Rock `w316302064-v1:f33` (one 2013 image, medium occlusion, 0.25) became massing only. The per-face arithmetic is in each package's `EVIDENCE.md`.
