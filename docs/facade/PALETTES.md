# Facade palettes and styles (v0.6), owner: agent C

## Palettes (`prep/facade_assets/palettes/*.json`, `facade-palette-1`)
**Rules for every palette:**
- It defines all 15 plan roles: wall, glass, mullion, frame, backing, sill, band, parapet, canopy, column, door, roof, roof-edge, sign and trim.
- `zones` override roles for coherent material regions.
- `texture` alternation is limited to the whitelist (`rows%k==r`, `cols%k==r`, `hash(u,v,seed)<p`). It is used only for columns or coursing, never as random noise (Detail pp.3, 24).

**Slab, stair, trapdoor and door roles** hold the block id. The component's cell `props` supply `type`, `half`, `facing` and the rest (COMPONENTS.md note 1).

| id | status | body / glass / mullion / backing | zones, texture |
|---|---|---|---|
| `summerlin-stucco-tan` | accepted (style) | smooth_sandstone / gray glass / black glass / gray concrete | column alternates sandstone, `rows%2==1` |
| `summerlin-stucco-cream` | accepted (style) | smooth_quartz, tan bands / gray / black / gray | none |
| `storefront-glass-dark` | accepted (style) | gray_concrete piers / gray / black / **light-gray** (not black behind gray) | none |
| `office-lowrise-gray` | accepted (style) | polished_andesite / cyan glass / light-gray glass / gray | column alternates andesite |
| `residential-stucco` | accepted (style) | white_terracotta, quartz trim / gray / **end rod** / gray | none; not for storefront or curtain wall |
| `red-rock-resort` | **DRAFT** | smooth_sandstone / brown (bronze) glass / black glass / gray | `stone-base` (granite), `tower`; wall coursing `rows%4==3` |
| `harlo` | accepted (job-derived) | the accepted cycle-1 job palette, mapped (`job_palette_map`) | `main-w` (CMU), `cream`, `service` |

**Glazing (Detail pp.4–5):**
- There is no clear `glass` anywhere. The role rule refuses it.
- Glass colours are gray, cyan or light gray. The exception is the bronze in the draft red-rock palette, justified by its stone detail; to be confirmed by agent A in phase 2.
- Backing is 1 block behind the glass. It is never black behind gray or black glass, and never ice. The lint checks both.

**Harlo:** the accepted materials reproduced from `summerlin\detailing\jobs\harlo-w348127357\job.json` and JOB.md section 7.
- The job's `service_door` was a **solid** light-gray concrete block, so here it is zone `service.wall`, not a door block.
- `frame`, `sill` and `door` are library defaults that the job did not use.

`colour_refs`: Harlo's are the job's measured values. Every other palette's are labelled "style estimate (not measured)".

## Role constraints (`facade_palette_tools.role_ok`, used by `nearest`)
**Glass roles:**
- `glass` accepts only glass or panes, and never clear `glass` or `glass_pane` alone (Detail pp.4–5).
- `mullion` accepts glass, panes, end rods, iron bars, fences or full blocks.

**Structural roles** (wall, backing, sign, band, parapet, roof-edge, trim, sill, canopy, column, roof) accept only solid, stable blocks, full or partial, by role:
- wall, backing and sign: full blocks;
- canopy: slab, trapdoor or full;
- column: full, wall, fence, slab, stairs, end rod or bars.

**Always refused, whatever the colour match:**
- gravity blocks, leaves, ores;
- light sources (except `end_rod` as a mullion, frame or column divider, Detail p.11);
- unstable or support-needing blocks (buttons, carpets, plates, plants, torches);
- block entities, ice and snow, unwaxed copper (it weathers), grass and dirt (they spread), tinted glass;
- redstone and utility blocks, glazed terracotta, mushroom blocks.

**Other roles:**
- `frame`: trapdoor, slab, end rod, fence gate, bars or full.
- `door`: door blocks only.

`nearest #rrggbb <role>` ranks the allowed blocks by CIE76 ΔE in Lab using `colours-1.18.2.json`. Colour comes before texture (Detail p.30). Tinted textures and see-through "full" blocks are skipped.

## Styles (`prep/facade_assets/styles/*.json`, `facade-style-1`)
Each style has:
- `kind: style`, `label: "STYLE-GENERATED (not observed)"` and `evidence.class: style`;
- `applies_to {building, max_levels, min_face_len}`;
- a palette ref, component refs, and `defaults` per edge kind.

**Edge kinds:**
- `exterior`: the full layout.
- `shared`: `{"wall_only": true}`. A plain wall; never openings.
- `unknown`: `{"skip": true}`.

**Exterior layout keys** (proposed; D1 owns the final schema):
- `floors {base_v, height, count}`;
- `bays {width, residual}`;
- `ground` and `upper`, each with `openings[{component, bays, v, align, u_inset, below, above, replace}]`, plus `canopy` and `piers`;
- `bands[{component, at}]`;
- `corners`;
- `parapet`.

Pattern fitting follows Detail p.19: gaps first, then window widths, then ±1–2 bays. A style never resizes the shell.

| style | palette | ground | upper |
|---|---|---|---|
| `summerlin-stucco-retail` | stucco-tan | storefront bays, centre double door, 2-deep canopy, piers | inset 2x2 windows with sills; flush floor bands; parapet cap |
| `storefront-under-opaque-band` | storefront-glass-dark | full storefront, double door, glazed corners | opaque band to the top; projecting band at the transom; cornice; lettering only if assigned |
| `lowrise-office` | office-lowrise-gray | ribbon windows, glazed entry bay with double door, deep canopy | ribbons between floor bands; quoined corners |
| `residential-stucco` | residential-stucco | 2x2 frame2 windows with sill and hood, single door | 1x2 frame2 windows with sills; roof edge |

## Review sheets
Command: `python prep/facade_palette_tools.py sheets C:\BTE-Migration\facade\review\palettes <client-1.18.2.jar>`. Each PNG shows:
- a texture swatch, block and mean colour for every role in every zone;
- an 18x12 mock-up strip: storefront, canopy and columns, a band, punched windows with sills and trims, and a parapet.

The sheets are unshaded. **The Bliss-shader comparison is an in-game step for the user.** The sheets live outside the repo, because they contain Mojang textures.
