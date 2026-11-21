# Evidence: Red Rock Casino Resort Spa (`red-rock-casino`)

Compiled by agent A on 2026-10-29. The resort is at 11011 W Charleston Blvd, Las Vegas NV 89135; OSM landuse `w316302054`, which is **not** in the local index.
- **Local refs:** `C:\BTE-Migration\facade\refs\red-rock-casino\`. 18 images plus sidecars, 16.5 MB, private and not redistributed. Each sidecar holds the source, url, capture date, retrieval date, position, heading, licence, attribution, sha256, faces covered, era and occlusion.
- **Hashes and provenance:** `sources.json`.

Labels:
- **[ref]** = reference-matched (dated image);
- **[meas]** = measured (OSM, LiDAR, ortho);
- **[inf]** = inferred by a stated rule;
- **[est]** = image-based estimate (ratio or count from a perspective photo; not a measurement);
- **[unres]** = unresolved.

## 0. Blocking facts for phase 2
1. **No accepted shell exists.** `summerlin\pilot\plan_v1\features.json` lists `w316302058` and `w316302055` as "skipped: footprint not entirely inside pilot". Every other resort way lies outside the pilot bbox (36.1500–36.1543 N). **Resolved in phase 2:** the package uses `building.shell: none` (massing, COMPILER-SPEC §9) on B's LiDAR parts.
2. **No street-level imagery service:**
   - **Mapillary:** no token.
   - **Google Street View:** not used. The Harlo method is an unkeyed internal endpoint, not a configured key.
   - **Consequence:** the street-level evidence is **16 Wikimedia Commons photos (2005–2024)**. They have Commons coordinates for 4 and **estimated headings** for all.
3. **OSM carries no 3D tags.** The resort ways have no `building:part`, `height`, `building:levels` or `roof:*` tags. Heights must come from LiDAR. `w316302058` is already measured: roof p50 **64.18 m**, bands 64 and 66 m [meas].

## 1. Section → OSM mapping (checked against `ortho_esri_z18_osm.png`)
| section | OSM way | basis | confidence |
|---|---|---|---|
| **Hotel tower** (curved slab, concave W face toward the pool, convex E face) | `w316302058` `building=hotel` | Curved 24-node way: long sides ≈171 m, ends ≈20–29 m [meas OSM]. LiDAR roof 64 m [meas]. Its long shadow in the ortho [meas]. 2006–2024 photos [ref]. | high |
| **Casino / low-rise podium** (casino, restaurants, meeting rooms, back of house), with the pool courtyard notch and the round "rotunda" drum | `w316302064` `building=hotel`, addr 11011 W Charleston | Largest way (32,151 m²). Flat white roof with HVAC (ortho; 2018 roof photo). Pool courtyard notch (ortho). Drum (ortho round roof; 2006/2013 photos). | high |
| **Charleston-side frontage:** restaurant row (2014), Yard House (2010) and the NW casino entrance | `w316302063` `building=yes` | Jagged N edge of small roofs along Charleston [meas ortho]. The 2018 "northwest entrance" photo is geotagged at 36.15722, −115.335, at the W end of this way [ref]. Wikipedia: the 2014 restaurant row faces W Charleston [doc]. | medium [inf] |
| **SE low-rise wing**: L-shaped, with round rooftop equipment screens and a rooftop terrace at the elbow | `w316302065` `building=yes` | Ortho only. **Function unresolved:** spa, meeting space or hotel-lobby wing are all plausible; no image of it. | low |
| **Porte-cochère / hotel drop-off** | not mapped | **Candidate:** the oval drive and canopy S of `w316302065`, toward Downtown Summerlin [meas ortho]. Wikipedia: 2014 "new parking and drop-off area across from Downtown Summerlin" [doc]. **[unres]:** no ground image. | low |
| **Connecting volume E of the tower** (square roof with an oval feature, in the tower's shadow) | not mapped | Ortho only. Possibly the 2007 "separate hotel entrance" (press, 2007) [unres]. | low |
| **Spa** | not identified | Tutor Perini: spa in Phase I, expanded in Phase II. Station Casinos 2014: 25,000 sq ft spa rebuilt. Location not documented **[unres]**. | — |
| **Central plant** (4 cooling-tower fans) | not mapped, SW of `w744324634` | Ortho [meas]. Tutor Perini lists a central plant in the low-rise [doc]. | medium [inf] |
| East / West / Team-member parking decks | `w316302059`, `w316302060`, `w316302055` (15.0 m LiDAR) | OSM `building=parking` | high; **out of v0.6 package scope** |
| Regal Red Rock (cinema, opened 2006-04-21) / Red Rock Lanes (bowling, 2007) | `w744324634` / `w744324884` | OSM name tags | high; **no references gathered** |

**Ortho caveat, corrected in phase 2:** the Esri mosaic is not a true ortho. My phase-1 reading, that the tower "leans west over `w316302064`", was **wrong about which outline is true**.
- Agent B's LiDAR shows the tower base **is** the striped curved band beside the pool. The OSM `w316302058` outline was traced on the displaced roof, about **21.5 m toward 292°** off the true position (IoU 0.16 → 0.95 after shifting) [meas].
- The package therefore uses B's LiDAR-corrected parts (`facade\measure\red-rock\measure.json` → `suggested_package_parts`, source `rr-lidar-b`).
- This is a measured correction from LiDAR. It is not an outline change made from imagery (Outlines pp.2–7).

## 2. History and eras (never mix without saying so)
- **2004-01:** tower height approved at **198 ft (60 m)**, down from a proposed 300 ft (Wikipedia).
- **2005:** construction; topped out 2005-03. Image `rr-c2005-constr`: construction only, **not** facade evidence.
- **2006-04-18:** opening. Phase I: a 17-story tower with 415 rooms and a spa. The low-rise is 720,000 sq ft, including the casino and a central plant (Tutor Perini; architect Friedmutter Group).
- **2007-01:** Phase II opens: a "20-story" tower with 427 rooms, spa expansion and 4 penthouses (Tutor Perini). The 2007 bowling and garage expansion added Red Rock Lanes and 1,000 garage spaces.
- **2010:** Yard House opens. The 2010 photos show the complete tower.
- **2014:** a $35M program:
  - restaurant row facing Charleston, with outdoor entrances and patios;
  - spa rebuilt;
  - 26 suites;
  - new parking and drop-off toward Downtown Summerlin.
- **2019–20:** guest-room remodel (interior).
- **2022:** restaurants (Lotus of Siam etc.), high-limit room, adults-only pool. Interior and pool scope only.
- **Era rule for the package:**
  - **Tower exterior:** the 2006, 2010, 2018 and 2024 photos agree on composition (glass field, cream frame, ledgestone piers, cornice), so the tower is treated as **one era (2006–2024)** [ref].
  - **Signage:** "red rock / CASINO" in purple/maroon in 2010–2018. In 2024 the S end shows red letters (unreadable). The current N-pier sign is **[unres]**.
  - **Charleston frontage:** use post-2014 evidence only (2018).

## 3. Per-face coverage
Directions are display labels only (ARCHITECTURE §1). Faces must be bound to B's geometry by part and `u`.

| part : face | coverage | best refs (date) | what is visible |
|---|---|---|---|
| tower : W (concave, pool side) | **good, full width** | rr-c2018-towerW (2018), rr-c2006-towerW (2006), rr-c2024-3184 (2024, S half) | Single glass field in a cream stucco frame. **Flush** horizontal line per floor (no fins). Bronze/rose reflective glass in sun, blue-black in shade. Recessed top level with small punched windows. Flat cantilevered cornice slab. Ledgestone piers at the N and S ends. |
| tower : E (convex) | **good, ~80%** (S tip foreshortened) | rr-c2010-hotel, rr-c2010-night (2010) | Glass with **projecting light fins at every floor**. Two narrow ledgestone piers splitting the glass about 1/3 from the S end [est]. Recessed top level, glazed, with stepped curved roof canopies and rooftop palms. N-pier sign. |
| tower : N end pier | partial (seen obliquely from W and E) | 2010, 2018 | Full-height tan ledgestone (random-length horizontal strips, 2 tones). Carries the "red rock / CASINO" sign on both long faces. **The N end face itself is [unres].** |
| tower : S end | good upper, partial lower | rr-c2024-3185 (2024) | Ledgestone shaft with a **single vertical strip of bronze glazing** in stepped per-floor bay frames. Cream stucco frame turning the corner. Roof slab overhang. Red letters high on the face (unreadable). |
| tower : base / podium junction | poor | 2006, 2010, 2024 | A podium plinth band under the glass and terraces with glass rails; heights not determinable. |
| podium : pool courtyard | medium | rr-c2013-pool (2013), rr-c2006-wide (2006) | 3 cream stucco bands with recessed terraces, red sandstone pilasters/chimneys, curved wood-soffit canopy (drum). |
| podium : roof | good | rr-c2018-towerW, ortho | White membrane, AHU boxes, dishes, cream parapet screen ≈1.5–2 m [est]. |
| podium : S and W walls | poor (2006 oblique from height, pre-2007 works) | rr-c2006-wide | Cream walls with red-stone accent panels, flat parapets. **Era caveat:** before the bowling expansion. |
| frontage : NW casino entrance (`w316302063` W end) | good (one entrance) | rr-c2018-nwentry (2018) | See §4 (entrances). |
| frontage : rest of the Charleston (N) face | **none** | — | **[unres]** |
| a second casino entrance | good image, **position unknown** | rr-c2013-entry (2013) | Same vocabulary as NW, plus a red-rock monolith fountain sign. Pre-2014. |
| `w316302065`, drop-off, unmapped E volume, all parking decks, cinema, bowling, central plant | **none** | ortho only | **[unres]** |

## 4. Facade content by section

### 4a. Tower (`w316302058`)
- **Height:** roof p50 64.18 m, bands 64/66 m [meas]. This is consistent with the 60 m (198 ft) approval plus parapet or cornice [inf].
- **Floor count:**
  - Fin bands counted on the E face: **16 ± 1** typical glazed floors, in day (2010) and night (2010) photos [est].
  - **+1** recessed top level [ref].
  - **+ podium plinth** levels, unknown [unres].
  - Official counts: "17-story" (Phase I) and "20-story" (Phase II) [doc]. Those may count podium or mechanical levels differently.
  - **Floor count and floor height are [unres] to ±1.** B should derive floor height from (LiDAR top − glass base) / 17 and check it against the fin spacing.
- **Bay rhythm:** the vertical mullion module ≈ **0.45 × floor-to-floor** [est, 2010 E crop]. At about 3.3 m that is ≈1.5 m → **2-block bays** (Detail pp.3–4) [inf].
- **Frame and piers, as ratios [est, perspective]:**
  - W face: cream frame side width ≈0.6–0.7 floor heights; top frame ≈1 floor height.
  - E face pier pair: ≈1.1 and 1.5 floor heights wide; located about 30–35% of the E-face length from the S end.
  - End piers: full height, full end width.
- **Materials and colours (photo samples, lighting-dependent; C decides the blocks):**

  | zone | observation | sample sRGB (source, light) |
  |---|---|---|
  | glass (both faces) | bronze/rose reflective in sun; blue-black in shade | #222936 (2024, shade); #4d4a63 (2018 W, reflecting mountains); #65676e (2010 E) |
  | cream stucco frame and cornice | cream/off-white | #d3b773 (2024, golden hour, warm-biased); #d0b99d (courtyard 2013, noon) |
  | ledgestone piers | tan, random-length strips, 2 tones | #d2aa62 (2024 golden hour, warm-biased); #736d5f (2010 evening) |
  | E-face fins | light gray/cream metal | #6c6a6f (2010, shade side) |

- **Top:**
  - recessed top level [ref];
  - **cantilevered flat roof slab ≈2–3 m overhang** [est]; it needs an `envelope.protrusions` allowance;
  - stepped curved canopies on the E side [ref].
  - Rooftop palms are not generated.
  - Roof shape: flat [meas/ref].
- **Signage:**
  - N pier, both long faces: "red rock" over "·CASINO·" (2010, 2018) [ref].
  - Approximate letter heights [est, scaled by fin spacing]: "red rock" ≈4–5 m (ascender to descender); "CASINO" ≈1–1.2 m.
  - S end 2024: red letters, text unreadable [unres].
  - v0.6 renders signage as a simplified band or block only (BUILDING-STANDARDS §8).

### 4b. Casino / podium (`w316302064`) and frontage (`w316302063`)
- **Materials [ref]:** cream stucco (#d0b99d) and cream ledgestone bands (#9a8976 in shade) are the main walls. Red/brown stacked sandstone (#633e3a in sun) forms piers, chimneys and accent panels. Wood-tone soffits (#4d2d20) sit under flat roof slabs. A white membrane roof (#d1d2d2).
- **Courtyard [ref 2013]:**
  - 3 horizontal stucco bands with recessed terraces;
  - red-stone pilasters at irregular spacing (≈4–6 visible across the face) [est];
  - the curved drum has a deep wood-soffit canopy.
- **Floor bands:** podium height is unmeasured [unres → B LiDAR]. From the photos: entrances are single-storey at the canopy, with 2 storeys of mass behind; the courtyard has 3 bands [est].

### 4c. Entrances and canopies
- **NW casino entrance** (2018) [ref]:
  - red sandstone piers flank a glass door bay;
  - a white box canopy over the drive edge carries silver "CASINO" letters, ≈1–1.2 m [est, against ≈2.4–3 m doors];
  - above it, an upper glazed band under a cantilevered flat roof with a wood soffit;
  - to the left, the Yard House pylon in gray stacked stone, and restaurant bays with light stone piers and dark metal awnings.
- **Second entrance (2013, unlocated):** the same composition, so it can be a reusable component (`entry-casino`). Its placement is **[unres]** until it is located.
- **Hotel porte-cochère:** **[unres]** (see §1).

## 5. Unresolved (explicit; no ops)
- **Tower:**
  - N end face;
  - the lower 2–3 levels and the podium junction on both long faces;
  - exact floor count (±1) and floor height;
  - the current N-pier sign (era);
  - S-end lettering text;
  - rooftop equipment, window-washing davits and rooftop palms.
- **Podium (`w316302064`):**
  - all W, S and E exterior walls (only a 2006 oblique view, pre-expansion);
  - back of house, loading docks and service yards;
  - all rooftop equipment;
  - parapet heights.
- **Frontage (`w316302063`):** everything except the NW entrance. Positions of the restaurant-row entrances and patios.
- **`w316302065`:** function, all faces, and the rooftop equipment screens.
- **Unmapped volumes:** the porte-cochère / drop-off canopy, the square volume E of the tower, and the central plant.
- **Spa location.**
- **Parking decks, cinema, bowling:** no references gathered.
- **Ortho capture date:** the Esri metadata service is unavailable.

## 6. Layout parameters draft (for phase 2 package authoring)
Ratios and counts only. Exact metres come from agent B (LiDAR and geometry). Each item carries its evidence class.

| part : face | parameter | draft value | class |
|---|---|---|---|
| tower : all | `floors.count` (glazed typical) | 16 (allow 15–17; confirm against B's height ÷ fin spacing) | est + doc |
| tower : all | top level | 1 recessed level (`w = −1` to `−2` glass line), then the roof slab | ref |
| tower : all | roof slab | flat cornice slab at `top_y`, projecting 2–3 blocks (`envelope.protrusions`, exception note) | est |
| tower : W | `bays.width` | 2 (module ≈0.45 floor height) | est → inf |
| tower : W | `bands` | flush floor line each floor (glass-colour change or slab at `w=0`), **no** fins | ref |
| tower : W | frame | cream stucco frame: sides ≈0.6–0.7 floor height, top ≈1 floor height | est |
| tower : E | `bands` | projecting fin at each floor (`w=+1` top slab, light metal) | ref |
| tower : E | piers | 2 ledgestone piers at ≈0.30–0.35 of `u` from the S end, widths ≈1.1 and ≈1.5 floor heights | est |
| tower : N/S ends | wall | full ledgestone (2-tone `rows%k` texture; Detail p.24) | ref |
| tower : S end | openings | 1 vertical bay-window strip, every floor, bronze glass with stepped frame | ref |
| tower : N pier | sign | band/block suggestion: 2 lines, ratio ≈4:1 between "red rock" and "CASINO" | est; text era unres |
| podium : courtyard | `floors.count` | 3 bands with recessed terraces; red-stone pilasters | ref + est |
| podium : roof | parapet | cream parapet screen ≈1.5–2 m | est |
| frontage NW | component | `entry-casino`: stone piers, glass doors, white box canopy (w=+1..+N), sign band, upper glazing, wood-soffit slab | ref |
| everything in §5 | — | `unresolved` | unres |

**Palette direction for C, non-binding:**
- glass: bronze/dark (consider `brown_stained_glass` or `gray_stained_glass`; Detail pp.4–5 warns about odd colours);
- backing: `gray_concrete`, not black behind gray glass;
- cream stucco: `smooth_sandstone` or `smooth_quartz` family;
- ledgestone: a sandstone family with a 2-tone texture;
- red stone: `granite`, `terracotta` or `red_sandstone` family;
- check every choice with colour-first matching (Detail p.30).

## 7. Package as authored (revision 3, after review pass 2; geometry v0.6.2, 2026-10-29)
`package.json` (`kind: reference`, `building.shell: none`) uses:
- 9 OSM members, with package bid `w316302064`;
- B's **22 LiDAR parts** (the tower outline corrected ~21.5 m toward 292°, measured);
- palette `lib:red-rock-resort` v2.0.0;
- 15 library components `rr-*` + `lib:storefront-bay`;
- **5 detailed faces**;
- everything else listed in `unresolved` (massing only).

**Floor model [inf]:**
- The tower face is 66 rows (Y927–992, LiDAR p90).
- It is laid out as: a 10-row plinth, 16 typical floors × **3 blocks**, a 3-row cream frame band, a 3-row top level and a 2-row cornice.
- **Why 3 blocks:** the fin count is 16 ± 1. 16 × 4 = 64 rows would leave no room for the observed plinth, top level and cornice, so 3 is the only integer floor height that fits. A real floor height of about 3.0–3.2 m rounds to 3.
- The exact floor count is **not claimed**.

**Glazing (revised after review pass 2): continuous bronze reflective curtain wall** (`rr-curtain-flush` W, `rr-curtain-fin` E, both 1 column × 3 rows, repeat).
- Bronze glass (`brown_stained_glass`) runs across the whole floor band, with **no per-window mullion grid**. The references show only faint vertical joints, which are not modelled.
- The floor line is the same bronze glass over a brown concrete spandrel: a thin same-tone darker line, one row per floor.
- Behind the vision rows: gray backing.
- E face only: a light metal fin projects 1 block at every floor (2010 refs).
- The top level is darker (black) glass under a cream sill. This is a flush simplification of the setback.

**Sign (revised): no lettering, sign unresolved.**
- The only clear sign references (2010 E face, 2018 W face) show a one-line lowercase "red rock / CASINO" logo. That cannot be rendered faithfully in 3×5 block capitals on the pier.
- The 2024 references do not show the current N-pier sign.
- So the pier stays plain ledgestone and the sign is listed as unresolved, with no era mixing. Component `rr-sign-w1` was deleted.

### Evidence numbers (rule: `docs/facade/REFERENCES.md` §5)
| face | sources (dated, occlusion) | coverage (u-length seen) | confidence |
|---|---|---|---|
| `tower:f8` (W, concave) | rr-c2018-towerw (2018, low), rr-c2006-towerw (2006, medium), rr-c2024-3184 (2024, medium; S half) | **1.0** (full width in 2018; the lowest ~10 rows are hidden, which is a height, not a u, limitation) | 0.35 + 0.30 (3 sources) + 0.25 (newest 2024) − 0 (best occlusion low) = **0.90** |
| `tower:f1` (E, convex) | rr-c2010-hotel (low), rr-c2010-night (low) | **0.8** (S tip foreshortened) | 0.35 + 0.15 + 0 (newest 2010) − 0 = **0.50** |
| `tower:f0` (SW end) | rr-c2024-3185 (2024, low) | **0.7** (upper end seen; lower corner behind trees) | 0.35 + 0 + 0.25 − 0 = **0.60** |
| `w316302064-v1:f34` (pool courtyard) | rr-c2013-pool (2013, medium), rr-c2006-wide (2006, low) | **0.6** | 0.35 + 0.15 + 0 − 0 = **0.50**. Era: both predate the 2014 works, which did not touch the courtyard (inferred). |
| `w316302063-v0:f32` (NW entrance) | rr-c2018-nwentry (2018, low) | **0.6** (the entrance part of the face) | 0.35 + 0 + 0.05 − 0 = **0.40** |

- **Dropped from detail by the rule:**
  - `w316302064-v1:f33`: only rr-c2013-pool covers it, and 0.35 − 0.1 = 0.25 < 0.3. It is now massing.
  - `w316302064-v3:f44`: a shared wall in v0.6.1+.
- **Face ids follow geometry v0.6.1/0.6.2:** tower W = `tower:f8`, entrance = `v0:f32`, courtyard = `v1:f34`.

### Compile results (`plan-21c56a7db3899e1a`, status `ready`, validation `valid`)
| scope | ops | chunks | max ops/chunk | evidence |
|---|---|---|---|---|
| massing-only variant (phase 2, v0.6.0 geometry, temporary) | 187,729 | 525 | 1,477 | measured |
| tower part (+4-block cantilever margin) | **42,593** | **42** | 2,346 | reference 34,407 / measured 8,186 |
| NW entrance (f32 u 8–20 + 6) | **691** | **4** | 292 | reference 325 / measured 366 |
| courtyard area | 3,756 | 16 | 601 | reference 2,693 |
| **whole** | **201,584** | **529** | 2,346 | reference 37,302 (19%) / measured 164,282 (81%) |

- 823 components; 0 skipped instances.
- The v0.6.1 tower-interior hole is fixed in v0.6.2: no holes remain, and the tower roof cap is present.
- **Limits:** `max_ops` 210,000; `max_extent_m` 600 (actual 518 m), with a written exception.

## 8. Scores (1–5, honest; revised)
| aspect | score | reason |
|---|---|---|
| Geometry | **4** | LiDAR-corrected curved slab (arc rms 0.43 m), measured heights, 21 podium volumes (MAE 1.9 m). Not 5: chorded arc, flat p90 terraces, parapets unobserved. |
| Palette | **3** | The bronze glass now matches the reflective bronze read of the references; ledgestone, cream stucco and the white roof are close. The samples are lighting-biased, and the red sandstone (brown_terracotta) is probably too dark. |
| Facade rhythm | **3** | Continuous curtain wall with flush same-tone floor lines (W) and fins (E), piers, frame band, top level and cornice match the composition. The 1-row floor line is heavier than the real hairline; floor count ±1 and pier widths/positions are estimates; the top level is flat. |
| Entrances | **2** | Only the NW casino entrance, at an estimated position and depth; porte-cochère and other entrances unresolved; "CASINO" letters omitted. |
| Distinctive features | **2** | The curved slab, ledgestone piers, cantilevered slab, S-end bronze strip and fins are present. The sign is now unresolved (no logo); the rooftop palms, stepped curved E canopies and courtyard drum are missing. |

**Overall:** a Red Rock-shaped tower with the right glazing character and a closed massing. 5 of ~300 exterior faces have detail, and 81% of ops are measured massing. **The tower base needs an in-game check:** the reviewer saw a possible floating band, so check the 10-row plinth against the ground and podium.

## 9. Open questions for the user or coordinator
1. Provide `MAPILLARY_ACCESS_TOKEN`, or user photos, for the unseen faces: tower NE end, podium W/S/E, the Charleston frontage, `w316302065`, the drop-off, and the current N-pier sign.
2. Is the v0.6 scope (tower + courtyard + NW entrance detail, everything else massing) still the decision?
3. If a sign is wanted, it needs either a one-line glyph set or a user-approved simplification. Neither is assumed.
