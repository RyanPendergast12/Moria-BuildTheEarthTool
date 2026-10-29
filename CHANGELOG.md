# Changelog

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
