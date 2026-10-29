# Changelog

## v0.4.3 (2026-10-29, prerelease): automatic overpasses
- **Automatic mode** (`overpass-mode: auto`, the default once `overpass-enabled: true`): `roads` and `both` throws discover every carriageway bridge nearby, measure it and check eligibility. Eligible ones are built with their approaches; the rest are skipped with a specific reason in the report.
  - The previous behaviour remains as `overpass-mode: allowlist`.
  - `overpass-exclude` blocks individual crossings.
  - `overpass-min-clearance` (default 4) sets the clearance rule.
- **Eligibility checks** (`prep/crossings.py`): data quality, geometry (one connected deck, separation 4–15 m, deck vs OSM length, width), clearance, feasible approaches, overlap with neighbouring crossings or stacked bridges, and the Strip.
- **Whole-crossing plans:** every block of a crossing is planned once and cached (`crossings/<key>.cells.json`), then clipped to the chunk.
  - The road model and per-chunk generation moved to `prep/roadgen.py`.
  - The Charleston regression is block-identical to v0.4.2: 45,728 blocks.
- **Whole-crossing preflight** in the plugin: all of a crossing's blocks, in all its chunks, are checked against the protection rule before any is placed. One protected build anywhere skips the whole crossing, with the block named. The per-chunk check stays as the final guard.
- **Fixed-road rule narrowed** to what the baseline requires: the crossing's lower roads plus other motorway/trunk mainlines stay fixed. A freeway that is itself the upper road may ramp.
- **Fixed:**
  - multi-structure decks no longer crash the plan (they are skipped with a reason);
  - many-way structures get short stable keys (Windows path limit).
- **Pipeline** `shelltool_prep v4.3`; the overpass mode is recorded per chunk.
- **Approach junction fix:** the approach is also seeded at the **measured** deck end, and the bridge way itself joins the approach network. An OSM bridge longer than the measured deck no longer leaves a gap between deck and approach. The OSM end-node seed still drives the outward ramps, so the Charleston changes are confined to the deck-end junctions.
- **The lower road is never raised or filled,** column by column. The whole-crossing planner refuses any plan that would cover a lower road ("approach would cover the lower road").
- **Re-planning an already-built crossing:**
  - Fill and wall columns may replace this tool's own unchanged blocks.
  - Blocks of an earlier plan that the current plan no longer contains are **retired**: restored to their exact recorded before-state, ledgered as an upgrade (undoable), never a changed block or a tracked edit.
  - Fixed before release: a retirement was itself being retired on the next throw.
- **Known limitation:** a 2-block drop where the Desert Inn raised approach meets the existing road (-12160205, -5448042). The terrain model and the world surface differ by one block there. See `docs/TESTS-v0.4.3.md`.
- **Tests:** 54 checks recorded, 48 pass. The 6 first-run failures are superseded by passing reruns, mapped in `docs/TESTS-v0.4.3.md`.

## v0.4.2 (2026-10-28): current version
Raised approaches onto enabled overpasses ("Option C").
- **New `prep/approaches.py`:** the required road height spreads from each deck end along the connected road network and drops at most `overpass-approach-grade` per block (tested at 0.06).
  - Intersections become raised level areas; frontage roads, ramps and driveways ramp down from them.
  - Freeway mainlines and the crossing's lower roads are fixed: a leg that would need them raised is reported as a conflict.
  - Computed once per crossing and grade, and cached with the crossing plan, so every chunk reads identical heights.
- **Per-chunk construction:**
  - fill on top of the existing road (buried, not removed, so undo is exact);
  - retaining-wall faces and barriers on raised edges;
  - enclosed medians and islands filled;
  - the road surface, markings, crosswalks, kerbs and sidewalks redrawn at the raised height.
- **Raised surfaces** may resurface generator terrain exactly at their target height. Unknown non-terrain blocks still make the chunk UNRESOLVED (nothing placed there).
- **New roles:** `approach-fill` and `approach-wall` are built top-down to the real ground; `approach-surface` and `approach-barrier` go into air.
- **Config:** `overpass-approach-grade` (0 = off; allowed 0.04–0.10).
- **Cache:** the preparation pipeline is now `shelltool_prep v4.2`, and the approach grade is recorded per chunk, so changing either re-prepares affected chunks. The accepted deck geometry is reused untouched.
- **Tests:** a local acceptance harness (not published: it contains server-specific paths and a player UUID). All 19 checks passed on a disposable server.
  - Covered: profile steps ≤ 1 block, clearance ≥ 5 blocks, exact undo/re-apply, chunk-limited writes, no exceptions.
  - Approved in-game.

## v0.4.1 (2026-10-28)
One snowball workflow, plus road details.
- **Content:**
  - `roads` and `both` include enabled crossings automatically.
  - `overpass` is a labelled specialist mode.
  - Mode and content are shown on every change and in `/shelltool status`.
  - Activation is OFF after login and restart.
- **Independent pipelines:** shells, roads and overpass preparation fail separately and never suppress each other.
- **Road style (roadstyle-v2), one model for ground roads and decks:**
  - Bellagio-frontage palette and BTE Roads Guidebook methods.
  - One-way roads get white right and yellow left edge lines; turn-lane separation; bike lanes.
  - Mapped (including `bridge=yes`) sidewalks.
  - Dash phase continues across chained ways.
  - Each surface is placed at its own explicit height.
- **Controlled upgrades:** only blocks that are tool-owned and still exactly as recorded are replaced. Edits, containers, tracked edits and entities are preserved.
  - Each upgrade records `upgrade_of`.
  - Undoing the original job is refused while a later upgrade exists.
- **Concise chat:** per-content counts; full detail in the saved report.
- **Cache:** derived plans from another pipeline are re-prepared.
- **Fixed:**
  - the misleading `not_enabled` label for enabled crossings with no blocks in a chunk;
  - a recursion bug in the "already present" counter, found and fixed during testing.

## v0.4.0-pilot (2026-10-28)
Grade-separated crossings (pilot: W Charleston Blvd over CC-215).
- **Deck height:** world lower-road Y plus the LiDAR-measured separation (class 17 deck minus class 2 road). The world's freeway and terrain are never changed.
- **Inferred, provisional details:** deck thickness, parapets, abutments. Piers are optional and off.
- **Placement:** all-or-nothing per chunk, with explicit UNRESOLVED results.
- **Planning:** whole-crossing plans clipped to the chunk, so results don't depend on throw order.
- **Config:** off by default; allowlisted crossings only.

## v0.3.0
Initial public import: building shells and roads from OSM, USGS 3DEP LiDAR and TerraPlusMinus terrain; local OSM index; undo ledger.
