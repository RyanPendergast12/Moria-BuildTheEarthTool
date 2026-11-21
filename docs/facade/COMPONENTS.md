# Facade components (v0.6), owner: agent C

Library: `prep/facade_assets/components/*.json`, schema `facade-component-1` (ARCHITECTURE.md 8a). Every file is original work (`license: original (Moria)`), is under 64 KB, and cites the Detailing Guidebook ("Detail p.N"; digest in `summerlin\detailing\BUILDING-STANDARDS.md`).
Lint: `python prep/facade_palette_tools.py check`. It resolves every component under every palette layer and checks each resulting state against `blocks-1.18.2.json`.

## Format notes for D1 (extensions to 8a, please confirm or reject)
1. **`props` on a role cell.** Example: `{"u":0,"v":0,"w":1,"role":"sill","props":{"type":"top","half":"top","facing":"{facing}"}}`. The props are merged into the palette's block state **only for the properties that block has**, so one cell works for a slab (`type`) and for a trapdoor (`half`, `facing`). Placeholders are allowed in the values. The reference implementation is `resolve_cell()` in `facade_palette_tools.py`. Without this, doors, sills, canopies and frame lines could not take their block from the palette.
2. **`repeat_v: true`** on columns and corners: the compiler stacks the 1-row pattern over the column height. A 2-row pattern, such as `corner-quoin`, repeats with period `size.v`.
3. **`sign-text` is parametric.** It has `cells: []` plus `glyph_size`, `spacing`, `role`, `w` and `glyphs` (rows top-first, `1` = a sign block). The text comes from the package or style. It is labelled **simplified lettering**: an original 3x5 font with A–Z, 0–9, `&` and space. No resource packs, sign blocks or entities.
4. **Style layout keys**, documented in PALETTES.md: `ground`, `upper`, `below`/`above` (sill and hood spanning an opening), `u_inset`, `align` and `replace`. D1 owns the final layout schema. Rename as needed and tell C.
5. **Air cells:** inset windows write `minecraft:air` at `w=0`. This is allowed only on eligible owned cells (ARCHITECTURE 4).

## Catalogue (46)
| family | ids | pages |
|---|---|---|
| windows, flush | `window-{1x2,2x2,2x3,3x3}` | Detail pp.4–5, 10 |
| windows, 1-block inset | `window-*-inset` | pp.9–10 |
| framed, 2-way split (inset) | `window-*-frame2`: horizontal frame line in the middle; vertical end rod in the lower part (odd widths only) | pp.11–12 |
| framed, ≥3 stripes (inset) | `window-*-stripe3` | p.13 |
| framed, square grid (inset) | `window-*-grid`: glass/mullion checkerboard plus a frame line | p.14 |
| ribbon (`repeat_u`) | `window-ribbon`, `window-ribbon-inset`: mullion every 2nd block | pp.3–4 |
| sill / hood (`repeat_u`) | `sill` (projecting top slab), `hood` (bottom slab, or upside-down stair facing in) | pp.20–21 |
| storefront | `storefront-bay`: mullion every 2nd column, plus a transom row of darker glass | pp.2–4 |
| curtain wall | `curtain-wall-bay`: floor-line glass row, then 3 vision rows | pp.2–4 |
| doors | `door-single`, `door-double`: both halves explicit; hinges on the outer sides | p.16; ARCH 4 |
| canopies | `canopy-flat-d1/d2/d3`, `porte-cochere-edge` | pp.20–21 |
| columns | `column-thin` (wall block), `column-1x1`, `column-2x2`, `column-2x2-base` | pp.22–29 |
| bands, roof | `floor-band`, `floor-band-proj`, `cornice`, `parapet-cap`, `roof-edge` | pp.2–3, 20–21 |
| corners | `corner-wrap`, `corner-quoin`, `corner-glass` | pp.3–5, 24 |
| louvers | `louver-panel`: top slabs in the wall plane over backing (the Harlo tall-louver) | p.13 |
| lettering | `sign-text` | BUILDING-STANDARDS 8 |

**Rules applied in every component:**
- Backing sits 1 block behind the glass. For inset windows that is `w=-2`, which is still 1 block behind the glass, within Detail p.5.
- No clear glass.
- No connection states are written. The thin column's wall states are left to the compiler.
- Vertical window splits use `end_rod[facing=up]`, which does not connect (Detail p.11). Horizontal splits use the palette `frame` (a trapdoor or slab).
- `storefront-bay`, `curtain-wall-bay`, `*-grid` and the ribbons need a **glass** `mullion` in the palette. The lint enforces this for styles.

## Stateful-block checklist
These are the blocks the library and palettes can emit. The categories come from `blocks-1.18.2.json` → `stateful`.

| block type (used in) | valid planned state | neighbour-update risk | compiler duty | persistence check after restart |
|---|---|---|---|---|
| **doors** (`iron_door`, `spruce_door`; door-single/double) | `facing,half,hinge,open=false,powered=false`, both halves | A lone half drops. An adjacent redstone source sets `powered` and opens the door. Wooden doors can be opened by players. Doors need a sturdy block below. | Emit both halves in one atomic component, with the lower half first. Require a solid block at `v=-1` (in the world or the plan). No redstone within 1 block. | Both halves are present, with the same `facing/hinge`, and `open=false,powered=false`. A player-opened wooden door counts as a tracked edit, not a failure. |
| **slabs** (sill, canopy, louver, roof-edge, frame) | `type=top|bottom`, `waterlogged=false` | Stable. Water nearby would only waterlog a slab on placement. | Always write `type` and `waterlogged=false`. Never write `type=double` (use the full block). | Exact state equality. |
| **trapdoors** (frame lines; any `trapdoor` role) | `facing,half,open=false,powered=false,waterlogged=false` | Redstone opens them. Wooden ones can be toggled by players. They do not need support in 1.18.2. | Write every property. No redstone neighbours. | `open=false`, and `half` as planned. |
| **stairs** (when a palette puts stairs in trim/band/sill; hood, cornice and fascia props set `shape=straight`) | `facing,half,shape,waterlogged=false` | **`shape` is recomputed from neighbouring stairs.** Two stairs at a corner become `inner_*` or `outer_*`. | Compute `shape` from the full plan (the corner and adjacent stair cells), not the component. Otherwise prefer slabs. | Exact `shape` equality. Any mismatch is a `wrong_result`. |
| **walls** (`polished_blackstone_wall` in column-thin; `wall` kinds) | `up`, `north/east/south/west=none|low|tall`, `waterlogged=false` | Connections and `up` are recomputed from all 4 neighbours and the block above. | Compute the settled state from the full plan **plus** the current world neighbours outside the plan. A component never carries these states (enforced by the lint). | Exact equality. Check the neighbours outside the plan again. |
| **panes / iron bars / fences** (only if a palette chooses them as glass or mullion) | `north/east/south/west=true|false`, `waterlogged=false` | Connections are recomputed. Orphan connections pointing at an unplaced chunk change when the neighbour arrives. | The same as walls. Components are atomic, and a pane run crossing a scope edge is deferred whole (ARCH 2). | Exact equality on every pane in the run. |
| **end rod** (frame splits, residential mullion) | `facing=up` | Stable, with no support needed. Light level 14; this is accepted as the guide's divider (Detail p.11). | Write `facing`. | `facing=up`. |
| **axis blocks** (`stripped_oak_wood[axis=y]`, logs) | `axis` | Stable. | Write `axis`. | Equality. |
| **leaves** | `persistent=true` only | They decay when `persistent=false`. | They are **not allowed** by any role rule. If a package adds greenery, it must write `persistent=true,distance=…`. | `persistent=true`. |
| **gravity blocks** (sand, gravel, concrete powder, anvils, scaffolding, pointed dripstone) | none | They fall when unsupported. | **Refused by the role rules.** Never emit them. | n/a |
| **block entities** (signs, banners, chests, beds, heads…) | none | Their NBT is not ledgered. | Refused by the role rules. Lettering uses full blocks. | n/a |

**Restart test (the harness):**
1. Apply.
2. Stop the server cleanly, then start it.
3. Re-read every planned cell, compare the exact block-data strings with the plan, and list any differences as `wrong_result`.
4. Also re-read the 6 neighbours of every connecting block, so connection drift caused by unplanned world blocks is caught.
5. Re-running the plan must report 0 planned changes (the Harlo A4/A5 pattern).
