# Facade package, style, palette and component schema (v0.6), owner: D1

Validator: `prep/facade_schema.py` (stdlib only). Contract: `ARCHITECTURE.md` sections 4, 5 and 8a.
Every error is reported as `<json path>: <message>`, for example `$.faces[0].floors.height: expected integer`.
Library errors are prefixed by the path that refers to them.

## 1. Authoring workflow
1. Write `packages/<id>/package.json`. JSON is canonical. `package.yaml` is read with `yaml.safe_load`; quote dates in YAML.
2. Validate: `python prep/facade_prep.py validate packages/<id>/package.json`. It exits 1 and prints every error path when the file is invalid.
3. Plan: `python prep/facade_prep.py <cache> plan <bid> --package <id>`. Add `--geometry <file>` to use a fixed geometry.
4. Inspect: `python prep/facade_prep.py inspect <cache>/facade/buildings/<bid>/plan-<key>.json`.
   - It prints the evidence and component counts for each face, the unresolved faces and the reasons.
   - It writes one elevation PNG per face to `inspect/`: columns along `u`, rows upward, the outermost op per cell coloured by role, and a red frame on unresolved faces.
   - This is the authoring and inspection tool. A graphical editor is deferred.
5. Iterate, then hand the package to review. Lint the library with `python prep/facade_palette_tools.py check`.

## 2. Safety rules (enforced)
- **Data only.** These keys are rejected at any depth, case-insensitively: `code`, `script`, `exec`, `eval`, `command`, `import`, and any key starting with `__`.
- **JSON values only.** These are rejected: YAML dates, sets, binary, tags, NaN and Infinity. Duplicate JSON keys are rejected too.
- **YAML is safe-loaded and bounded.** The size is checked before parsing. Anchors and aliases are rejected outright (alias bombs), and a document may have at most 200,000 nodes.
- **Strict objects.** An unknown key is an error, so typos fail.
- **Size limits.** A package may be at most 2 MB. A palette, component or style may be at most 64 KB.
- **Paths.** A `sources[].local` must be a plain relative name.
  - No drive, no leading slash, no `..`, and no symlink escape.
  - It must resolve inside the package folder or `C:\BTE-Migration\facade\refs\<package-id>\`. Override that root with `MORIA_FACADE_REFS`.
  - It needs a `sha256`, and the sha is checked when the file is present.
- **URLs are provenance only.** Nothing is ever fetched.
- **Blocks.** Names and state values are checked against `prep/facade_assets/blocks-1.18.2.json`, with a small built-in list as a fallback.
  - Placeholders are checked with sample values.
  - **Global deny-list** (`facade_schema.denied`). It applies to every block string the validator sees and again to every final op in the compiler:
    - fluids and bubble columns, fire and soul fire, TNT, spawners, command blocks, structure blocks and structure void, jigsaw, barrier, light, portals and moving pistons;
    - every block entity (`stateful.block-entity` in `blocks-1.18.2.json`, plus C's `BLOCK_ENTITY` rule): chests, furnaces, hoppers, signs, banners, beacons and so on;
    - every gravity block. They are refused outright, which is stricter than "solid below";
    - everything C's `facade_palette_tools.unsafe()` refuses (unstable, light, excluded kinds). The exceptions are `air` and `cave_air` (inset windows on eligible owned cells only) and `end_rod` (the guide's divider).
- **Role rules.** Library palettes must define all 15 plan roles. Every block must be allowed for its role, using C's `role_ok`, so the validator agrees with `facade_palette_tools.py check`.
- **Components under the palette.** Every component used by a package or style is resolved under every palette layer (`roles` and each zone) and must give valid states.
- **Ids resolve.**
  - `lib:<id>` must name `prep/facade_assets/{palettes,components}/<id>.json` (override with `MORIA_FACADE_ASSETS`).
  - Layout `component`, `below`, `above` and `replace` must name a key of `components`.
  - `role` and `fill` must name a palette role.
  - `evidence.sources` and protrusion `allowance` must name a `sources[].id`.
  - At plan time, face ids, unresolved faces and protrusion faces must exist in the geometry, and the bid must be in `building.osm`.
- **Limits.**
  - `max_ops` above 60,000 or `max_extent_m` above 400 needs `limits.exception = {reason (at least 10 characters), by, date}`.
  - The hard caps are 400,000 ops and 800 m.
  - Styles can never raise limits.

## 3. Package (`facade-package-1`)
| key | type and bounds | notes |
|---|---|---|
| `schema` | `"facade-package-1"` | |
| `generator_min` | semver | must be ≤ the compiler version (0.6.0) |
| `package` | `{id, version (semver), kind: reference\|style, title, license, attribution, notes?}` | `id` = the folder name; `[a-z0-9][a-z0-9_.-]{0,63}` |
| `building` | `{osm: [w123\|r1\|n1] (≤64), parts: [id \| {id, osm?, polygon_mc?, base_y?, top_y?, source?, notes?}], shell?: existing\|none}` | `shell: none` → massing (COMPILER-SPEC §9) |
| `crs` | `{projection: "bte-tpm-geo.py", units: "block=1m", y: "world"}` | constants |
| `sources` | list of `{id, kind: street\|aerial\|ortho\|lidar\|osm\|doc\|site-photo, date: YYYY[-MM[-DD]], url?, local?, sha256?, license, attribution, coverage: [face ids], notes?}` (≤256) | |
| `elevations` | `{measured: {..any..}, accepted: {part: {base_y, top_y (> base_y), source}}}` | y in [−4096, 4096] |
| `palette` | `"lib:<id>"` or an inline palette without `schema/id/version` | |
| `components` | `{name: {ref: "lib:<id>"} \| inline component}` | |
| `faces` | list of FACE (≤1024) | face ids are unique; `face: "*"` = every exterior face not listed |
| `unresolved` | list of `{part?, face, reason}` | `face: "*"` = every face not listed |
| `envelope` | `{outward: 0–4, inward_backing: 0–2, protrusions: [{face, u: [a, b] (b > a), depth: 1–6, allowance: source id, notes?}]}` | `u` in column-index units |
| `limits` | `{max_ops: 1–400000, max_extent_m: 1–800, exception?: null \| {reason, by, date}}` | |
| `seed` | int 0–2³¹−1 | used when `--seed` is absent |
| `roofs?` | list (≤64) of `{part: id \| "*", cover?: ROOFPART, edge?: ROOFPART, notes?}` | ROOFPART = `{role? \| block?, props?, rows?: top\|top+1 (default top), evidence?: reference\|measured\|inferred (default measured)}`. **cover** = every cell inside the part polygon that is not a wall column. **edge** = the part's wall columns, excluding party faces. Both sit at `top_y`, or `top_y+1` with `top+1`. Works with `shell: existing` and `none`. The writer still replaces only air or owned/adopted cells. |
| `extras?` | list (≤256) of `[x, y, z, block, role, evidence]` | Explicit reference-matched cells for small non-parametric details, such as a corner canopy no face reaches. Ints; a valid, non-denied block without placeholders; a plan role; reference\|measured\|inferred. Each must lie inside the envelope (otherwise dropped and reported). Each is one atomic single-cell instance. |

**FACE** = LAYOUT plus:
- `face` (the geometry face id or `*`);
- `evidence {class: reference|measured|inferred, sources: [ids], confidence: 0–1, coverage: 0–1, notes?}`.

A face with coverage < 0.5 or confidence < 0.3 is **unresolved**.

## 4. Layout (shared by package faces and style `defaults`)
Face-local units:
- `u` is the **column index** (0-based, along the face's voxelised `columns`).
- `v` is rows above the face `base_y`.
- `w` is the outward offset.

| key | shape | meaning |
|---|---|---|
| `floors` | `{base_v: 0–64, height: 2–12, ground_height?: 2–16, count: 1–200 \| "auto"}` | Floors stack from `base_v`. They are clipped to the wall below the parapet and never stretched. |
| `bays` | `{width: 1–32, residual: center\|ends\|absorb, margin?: 0–16}` | COMPILER-SPEC §3 |
| `zone?` | palette zone id | a coherent material zone for the whole face |
| `backing?` | `auto\|none` (default `auto`) | `none` drops every backing cell of this face's openings (automatic or authored) |
| `backing_exclude_u?` | `[[u0, u1], ...]` (inclusive column indexes, ≤64) | drops backing cells in those columns only, so accepted cells are left alone |
| `wall?` | `{fill: bool, role?, block?}` | fills the whole wall plane |
| `openings?` | `[OPENING]` (≤64) | applies to floors selected by `floors` (default `all`) |
| `ground?` / `upper?` | ZONE | ZONE contents apply to floor 0 / floors 1 and up |
| `bands?` | `[{component \| role \| block, at: floors\|floor_lines\|top\|v<n>\|<int>, w?: 0–1, height?: 1–3}]` | `floors`/`floor_lines` = the start row of every floor above the ground floor |
| `corners?` | `{component \| role \| block, width?: 1–3, floors?: selector}` | applied to the face end that owns the geometric corner |
| `parapet?` | `{component}` or `{role \| block, height: 0–4, cap?: block}` | The top rows of the measured wall. A component's `size.v` gives the height. |
| `canopy?` | CANOPY or `[CANOPY]` | CANOPY = `{component \| role \| block, v, depth?: 1–6, u?: [a, b], bays?: selector}` |
| `roof_edge?` | `{component \| role \| block}` | the top row(s) |
| `sign?` | SIGN | |

- **OPENING** = `{component, bays?, floors?, v?: 0–16 (default 0), u?: 0–31, u_inset?: 0–8, align?: center|left|right, below?, above?, replace?, backing?: auto|none, notes?}`. An opening's `backing` overrides the face's.
  - `below` / `above`: a component (usually `repeat_u`) placed in the row under / over the opening, across its width, inside the same atomic instance.
  - `replace`: removes the named opening's instances already made in the same floor and bay.
  - `u_inset`: shrinks the bay on both sides. A `repeat_u` opening tiles the inset span; any other opening is aligned within it.
- **ZONE** = `{openings?, canopy?, piers?: {component, at: "bay_edges"}, fill?: role, top?: {component}, sign?, notes?}`.
  - `fill` paints the rows from the end of the ground floor to the parapet.
  - `top` places a component in the top rows under the parapet.
- **SIGN** = `{component (with glyphs), text: string|null, v?, u?, note?}`. A null or missing `text` places nothing.
- **Selectors** (for `bays`, `floors` and `corners.floors`): `all`, `ground`, `first`, `upper` (1 and up), `top`, `last`, `center` (index `(n−1)//2`), `odd`, `even`, `i`, `i:j` (Python slice, negatives allowed), `every n`, `every n from k`, or a list of ints.

Example (one face):
```json
{"face":"p0:f0","zone":"podium","floors":{"base_v":0,"height":4,"ground_height":5,"count":"auto"},
 "bays":{"width":5,"residual":"ends"},
 "ground":{"openings":[{"component":"store","bays":"all","u_inset":1},{"component":"door","bays":"center"}]},
 "upper":{"openings":[{"component":"win","bays":"all","v":1,"below":"sill"}]},
 "bands":[{"component":"band","at":"floor_lines"}],"parapet":{"component":"parapet"},
 "canopy":[{"component":"canopy","v":5,"bays":"center"}],
 "evidence":{"class":"reference","sources":["sv2024"],"confidence":0.8,"coverage":0.9}}
```

## 5. Style (`facade-style-1`, `prep/facade_assets/styles/<id>.json`)
- **Required:** `schema`, `id` (= the file name), `version`, `kind: "style"`, `label` (must contain `STYLE-GENERATED`), `applies_to {building?: [tags], max_levels?: 1–200, min_face_len?: 0–1000}`, `palette`, `components`, `defaults`.
- **Optional:** `title`, `license`, `attribution`, `notes`, `generator_min`, `evidence {class: "style"}`, `envelope {outward ≤ 1, inward_backing ≤ 2}`, `limits {max_ops? ≤ 60000, max_extent_m? ≤ 400, note?}`, `seed`.
- **`defaults`** is either a LAYOUT or `{exterior: LAYOUT, shared?: {"wall_only": true} | LAYOUT, unknown?: {"skip": true} | LAYOUT}`.
  - A missing `unknown` uses `exterior`, with a per-face note.
  - **`party`** edges (walls coincident with a separate, non-member building; geometry v0.6.1) always use the `shared` rule. The neighbour owns that wall.
- A face with more floors than `max_levels` is unresolved. A face with fewer columns than `min_face_len` gets no openings.

## 6. Palette (`facade-palette-1`)
- **Keys:**
  - required: `schema`, `id`, `version`, `roles {role: block | [blocks]}`;
  - optional: `title`, `status`, `texture {role: {alt: block, rule}}`, `zones {zone: {role: block}}`, `notes`, `colour_refs`, `hueblocks`, `source_job`, `job_palette_map`, `license`, `attribution`, `sources`, `tags`.
- **Roles** are exactly the plan roles: wall, glass, mullion, frame, backing, sill, band, parapet, canopy, column, door, roof, roof-edge, sign, trim.
- **Texture rules** come from a whitelist only: `rows%k==r` (on v), `cols%k==r` (on u) and `hash(u,v,seed)<p`.
- A list value uses its first block.
- An inline package palette may omit roles. `backing` falls back to `wall`.

## 7. Component (`facade-component-1`)
- **Keys:**
  - required: `schema`, `id` (= the file name), `version`, `size {u, v: 1–32}`, `cells` (≤4096);
  - optional: `title`, `repeat_u`, `repeat_v`, `widths`, `notes`, `license`, `attribution`, `sources`, `tags`;
  - lettering only: `glyph_size {u, v}`, `spacing`, `role`, `w`, `label`, `glyphs {char: [rows top-first of "0"/"1"]}`.
- **Cell:** `{u: 0..size.u−1, v: 0..size.v−1, w: −2..4, role | block (exactly one), props?: {state: value}, notes?}`.
  - `role` must be a plan role.
  - `props` merge into the palette-resolved block **only for the states that block has**. The reference is `facade_palette_tools.resolve_cell`.
  - Placeholders `{facing}`, `{facing_in}`, `{facing_left}`, `{facing_right}` and `{axis_u}` may appear in a block or a props value.
- **Rules:**
  - Doors are complete: a `half=lower` cell has a `half=upper` cell at `v+1`, given by block or by props.
  - Stairs blocks carry `facing` and `half`.
  - Pane, fence, wall and bar connection states (and `up`) are never written in components.
  - Cells are unique.
