# Facade v0.6: independent review (agent E)

**Scope:** the uncommitted v0.6 working tree on 2026-10-29: `FacadeTool.java`; the diffs to `ShellToolPlugin.java`, `EggTool.java`, `config.yml` and `plugin.yml`; `prep/facade_{common,schema,compile,prep,geometry,identify,palette_tools,selftest}.py`; and `prep/facade_assets/`. I checked them against `ARCHITECTURE.md` and the user's hard requirements.

**Method:**
- I read the code end to end.
- I ran only offline commands, using a scratch cache at `C:\BTE-Migration\facade\review\scratch-cache` and scratch assets and packages under `C:\BTE-Migration\facade\review\`.
- No server was started, no world was written and nothing was committed. The plugin was not compiled.

**Counts:** blocker 1, major 7, minor 14.

---

## Blocker

### B1. Replacing an owned wall cell detaches player work fixed to it
**Where:**
- `FacadeTool.java:466-487` (`check`);
- `:496-522` (`unit`);
- `:527-541` (`retireCell`).

**Gap:** `check()` inspects only the target cell. It looks at the tracked set, the ledger owner, `TileState`, and entities whose bounding box intersects that one block. It never looks at the six neighbours.

**Failure scenario:**
1. A player places a wall torch, a wall sign, a button, a ladder or an item frame on a Snowball shell wall. The torch cell is tracked, but the wall cell behind it is an untracked `shells` cell of this building, still at its after-state.
2. A Facade plan puts a glass pane, a mullion (`end_rod`) or an air inset at that wall cell.
   - `setBlockData(..., false)` suppresses physics only for this write.
   - The next neighbour update (any block change nearby, or a redstone tick) pops the attached block, and it drops as an item.
   - Item frames and paintings are not caught by `getNearbyEntities(BoundingBox.of(b))` either. Their boxes lie in the adjacent cell and only touch the face; they break on their survival check.
3. Retirement does the same when it restores `before = air`.

This destroys player work, which is a "never touch" item.

**Fix:**
- Before writing or retiring a cell, refuse it (and therefore the whole component) when any of its 6 neighbours:
  - is tracked; or
  - holds a block whose support depends on this face (`Directional`/`FaceAttachable` wall blocks, ladders, torches, signs, banners, rails, carpets and doors above); or
  - has a hanging entity whose attached face is this cell (search an expanded box of 1 block and check `Hanging.getAttachedFace()`).
- Report the refusal as "supports player work".

---

## Major

### M1. Retirement restores an obsolete facade block when a cell was facade-on-facade
**Where:**
- `FacadeTool.java:410-414` (retire selection);
- `:539` (`b.setBlockData(tb)` with `tb = l.before()`);
- `:412` (`role == "retire"` excluded).

**Failure scenario:**
1. Job JA applies plan A and places block X over air at cell c.
2. Job JB applies plan B (another style or package version) and upgrades c to Y, so JB's `before` is X.
3. Plan C has no op at c. Retirement picks the latest placement (JB) and restores **X**, plan A's block.
4. The retire row (content `facade`, `role=retire`, after X) is now the latest placement, and line 412 excludes `retire` rows. X is never retired again: an obsolete facade block stays on the building permanently.

**Fix:** when retiring, walk back through the cell's consecutive live `facade`-content placements of this building's members. Restore the `before` of the oldest one, which is the last non-facade state. Alternatively, treat a retire row whose `after` came from a facade placement as still live and retirable.

Add a regression test for the sequence A → B → C.

### M2. Hazardous blocks and block entities pass the validator and compile (reproduced)
**Where:**
- `facade_schema.py:186-196` (`check_block`): name and state only;
- `:298`, `:330`, `:335-337`, `:345`: every explicit `block` is checked with `check_block` only;
- `facade_compile.py:126` and `:149`: explicit blocks bypass `role_ok`.

**Reproduction:** `adv-hazard` (from fx-flat) adds:
- a component cell `minecraft:lava`;
- a band `{"block":"minecraft:spawner"}`;
- a parapet cap `minecraft:tnt`.

`facade_prep.py validate` reports **valid**. A `plan` against `facade_fixtures/geometry/flat.json` gives `status=ready` with 60 `minecraft:spawner` ops and 1 `minecraft:lava` op.

**Why it matters:**
- COMPONENTS.md:55 says block entities are "refused by the role rules", but explicit blocks never reach those rules.
- Placing such blocks means a spawner (block entity; in 1.18 it spawns pigs by default), flowing lava or water, TNT, command blocks or chests.
- Once placed, a block entity can never be replaced or retired, because `check()` refuses `TileState`.

**Fix:** apply `facade_palette_tools.role_ok`, or one shared denylist (block-entity, fluid, unstable, redstone, gravity and unobtainable kinds), to **every** explicit block string. That covers component cells, bands, corners, wall, parapet and cap, canopies and roof edges. Also re-check the denylist in `compile()` on the final states as a last guard.

### M3. Neighbouring OSM buildings are treated as "outside", so their unbuilt cells are writable
**Where:**
- `facade_geometry.py:6` and `:234`: a wall is any cell next to a cell outside *this* building or a lower part;
- `facade_compile.py:425-441` (envelope = own wall cells ± w);
- `FacadeTool.check()` line 478: air cells need no owner.

**Failure scenario:**
1. Two separate OSM buildings share an edge, as in a typical Summerlin strip mall. The neighbour has not been shelled yet.
2. The shared edge is classed `exterior`, so it gets windows, sills (w=+1) and frames.
3. The w=+1 cells are the neighbour's future wall-line cells. They are air and inside this building's envelope, so they are written.
4. A later Snowball throw on the neighbour is air-only and skips them. The neighbour's shell now contains foreign blocks owned by this building.

**Fix:**
- In geometry, mark edges coincident with *any* other building footprint from the OSM index as `shared` (or `unknown`).
- In the compiler or the plugin, exclude cells whose centre lies inside another building's footprint. The prep can ship a "foreign footprint" mask in the plan.

### M4. The geometry cache ignores members and package parts
**Where:**
- `facade_prep.py:30-33`: the cached `geometry.json` is reused when `generator` and `bid` match;
- `:75`, `:34`: members and `package_parts` are passed only on a rebuild;
- `facade_identify.py:273-285`: package claims come from **unvalidated** package files;
- `facade_prep.py:116-119`: the identify memo is never invalidated;
- `FacadeTool.java:58`: the in-memory `identified` cache.

**Failure scenarios:**
1. **Stale member list.** A package lists `[w1, w2]` and is planned, so geometry with both is cached. The author removes w2, which was a wrong id, in v1.1. The next plan still uses the cached w1+w2 geometry: `plan_key` changes but `geometry_sha` does not. Plan `members` still contain w2, so the plugin treats w2's `shells` cells as eligible and writes on the neighbour.
2. **Stale parts.** A corrected `building.parts` polygon is likewise ignored.
3. **Invalid package still claims ids.** An invalid package keeps claiming ids in `identify`, which feeds the plugin's `members` (`FacadeTool:370`).

**Fix:**
- Key the geometry cache by `sha(bid, sorted members, package_parts)`, or store those inputs in `geometry.json` and rebuild when they differ.
- In `package_claims`, take claims only from packages that validate.
- Invalidate the identify memo when the packages directory changes (for example, key it by a sha of the package files).

### M5. Pane, bar, fence and wall connections chain across chunk borders through layer ops (reproduced)
**Where:** `facade_compile.py:402`: `o[2] == cid`.

**Gap:** when the connecting block is itself a layer op (`cid is None`), it connects to **any** layer neighbour, solid or not. COMPILER-SPEC §6 says connections are made only within the same component or to a *solid* layer op.

**Reproduction:** fx-flat with `bands: [{block: iron_bars, at: floors, w: 1}]` gives 6 layer `iron_bars` cells connected across a chunk border, for example `[-33,73,120] east=true -> [-32,73,120]`.

**Failure scenario:** a chunk-scope apply of chunk -3 writes bars pointing east at air. The connection stays orphaned permanently if the neighbour cell is skipped, for example because it is protected. This violates "no orphan pane connections" (ARCHITECTURE §2).

**Fix:** make every connector run atomic. Group contiguous connector layer cells into one synthetic component so the chunk rule defers them whole. Otherwise, connect only to solid layer ops, as the spec says.

### M6. Connection and stair states are not the settled world state, so ownership drifts and undo becomes incomplete
**Where:**
- `facade_compile.py:403`: the solid-neighbour assumption uses only `wall_line`;
- `:411`: stairs get `shape=straight` by default, while COMPONENTS.md:48-49 requires computing `shape`, and the wall and pane states, from the full plan plus world neighbours.

**Failure scenario:**
1. A pane's off-plan neighbour sits on the wall line, so it is planned `east=true`, but in the world that cell is air (a missing or unbuilt shell cell). Likewise, two trim stairs meet at a corner and are written `straight`.
2. The first neighbour update recomputes the state (`east=false`, `inner_left`).
3. `sameModuloTicks` is exact for these materials, so the cell now reads "changed since its placement". It loses ownership: it can no longer be replaced or retired, and **undo keeps it**.

This breaks "undo restores exactly". Stairs and connections are also not in `TICK_PROPS`.

**Fix:**
- Compute stair `shape` from the plan.
- In the plugin, at write time, settle connection and `up` states against the live neighbours: either recompute them as in vanilla before `setBlockData`, or refuse when they differ.
- Record the settled state as `after`.

### M7. A 455-byte YAML package hangs the validator (alias bomb)
**Where:** `facade_schema.py:401-414` (`yaml.safe_load` with no alias limit) and `:199-211` (the recursive `_data_only` walk).

**Reproduction:** `adv-bomb/package.yaml` defines 9 nested anchors, each expanding to 10 aliases of the previous one (10^9 nodes). `facade_prep.py validate` was still running after 60 s (killed by `timeout`).

**Effect:** in the plugin, the single prep worker is blocked for `facade-prep-timeout-seconds` (600 s) for every player. Deep nesting would instead raise an uncaught `RecursionError`, so the prep prints no status line. The size limit alone does not stop "limit abuse".

**Fix:**
- Reject YAML that contains anchors or aliases: scan the events or use a loader that raises on `AliasEvent`, or simply require JSON.
- Cap the node count and depth in `_data_only` (for example 200k nodes and depth 64), with a path in the error.

---

## Minor

1. **The parapet `cap` is never placed** (`facade_compile.py:265-266`, with the tie rule at `:184`).
   - The cap is stamped on row `H-1`, which the parapet rows already occupy at the same `LAYER_PRI`, and the first writer wins.
   - Reproduced: "conflict 30x: parapet>parapet", 0 cap ops.
   - Fix: give the cap a higher priority, or stamp it first.
2. **The plugin reads only `envelope.polygon_mc`** (the largest piece) and ignores `parts_mc` (`FacadeTool.java:200-203`). For buildings whose members do not touch, all ops in the other pieces are skipped as "outside envelope". This fails safe but is a functional loss. `adopt_candidates` (`facade_prep.py:136`) has the same limitation.
3. **The plugin envelope is looser than the compiler's** (`FacadeTool.java:173-174`).
   - Any cell centre within 0.75 of an outline edge passes, which adds about one block outside the outline everywhere, including diagonal cells.
   - The compiler already asserts that every op lies inside its exact envelope, so the plugin guard is weaker than necessary.
   - Fix: use `parts_mc` plus the exact compiler envelope with a small epsilon.
4. **An air cell whose latest placement is `facade` work of *another* feature is writable** (`FacadeTool.java:476-485`, where the feature check applies only to non-air). An example is a neighbour's retired cell. Fix: apply the feature check whenever `l != null`.
5. **The prep `protected()` Strip check fails open** (`facade_prep.py:40-47`): any exception gives `None`, so planning continues. The plugin's per-cell `inStrip` still protects. Fix: return `PROTECTED` or `provider_failure` on error.
6. **A job can hang after an exception in an async callback** (`FacadeTool.java:364-385`, `:430-437`).
   - An unchecked exception inside `whenComplete` or `main()` lambdas (for example `members()` on a non-string element, or `getAsDouble` on a malformed `_prep_s`) leaves the job in `jobs` without finishing it.
   - With `facade-max-jobs: 1`, Facade is then blocked, and the locks too if the exception came after `lock()`, until `/facadetool cancel`.
   - Fix: wrap each callback body in `try { ... } catch (Throwable t) { fail; finish(); }`.
7. **Cancellation affects shared preparations** (`FacadeTool.java:108`, `:291`). One player's `/facadetool cancel` kills a deduplicated prep that another player's job is waiting on: the prep's `owner` is only the first requester. Fix: keep a reference count of waiters.
8. **Retirement is outside the op limits** (`FacadeTool.java:402-414`). A whole-scope retire list is not bounded by `facade-max-ops-whole`. Fix: count `retire.size()` into `inScopeOps`.
9. **A large component is written in one tick** (`FacadeTool.java:458`). `budget -= size` is checked only after the write, so a long repeat-u storefront or sign can exceed the 6 ms tick target. This is acceptable for atomicity but should be documented and capped at compile time (maximum cells per component).
10. **Styles fill `unknown` edges silently** (`facade_compile.py:204`). A style without an `unknown` key applies the exterior layout, openings included, to `unknown` edges without a per-face note. These edges are often undetected party walls (see M3). Fix: add a note at least, or default to skip.
11. **The BlueMap event label lacks the style tag** (`FacadeTool.java:680`). It shows `style:<id>` but not `STYLE-GENERATED (not observed)`, unlike the report and chat.
12. **Adoption gaps** (`facade_prep.py:155-177`):
    - detail-job cells are not filtered by the envelope;
    - `open()` is used without `encoding=` (cp1252 on Windows): a non-ASCII `job.json` is skipped silently, and a non-ASCII `owned.json` or `after.json` crashes the prep;
    - the plugin then falls back to a stale `adopt-candidates.json` (`FacadeTool.java:549-551`). This is safe, because each cell is re-verified exactly, but should be reported prominently.
13. **Snowball's ownership checks do not distinguish tools** (`ShellToolPlugin.java:199-204`, `:672-690`, `:711-714`).
    - Overpass detail upgrades, road surfaces and `X` retirements accept any owner, so they can overwrite Facade cells. The `upgrade_of` record keeps undo consistent. `tickTolerant` now also covers `facade*` there.
    - Snowball crossings that write into neighbouring chunks never check `regionLocks` (pre-existing, but it now includes Facade-locked chunks).
    - Fix: document this, or make Snowball refuse `facade*`-owned cells.
14. **The cached plan file is reused whenever its key matches** (`facade_prep.py:93-96`). The key includes the `GENERATOR` string but not the compiler code, so compiler fixes without a version bump reuse old plans. EPT tiles are read through and may be downloaded during a first `plan` (`facade_geometry.py:76-99`), although the docstring says "no network". The download is bounded and cached, so it is not an AI call, but the doc should say so.

---

## Adversarial package tests (`C:\BTE-Migration\facade\review\make_adv.py`)
Each test was run as `facade_prep.py validate <pkg>`, with the assets set to the fixture library plus the real `blocks-1.18.2.json`.

| case | result |
|---|---|
| path traversal: `local` = `..\..\..\..\Windows\win.ini`, `C:\Windows\win.ini`, `/etc/passwd` | **rejected**, exit 1; `$.sources[0..2].local: ... escapes the package folder and C:\BTE-Migration\facade\refs` |
| `!!python/object/apply:os.system` YAML tag | **rejected**; `$: parse error: ConstructorError ...`; no side effect (marker file not created) |
| 50 MB `package.json` | **rejected**; `$: file is 52431005 bytes > limit 2097152` |
| invalid states (`oak_door[half=middle]`, `not_a_block`, `stairs[facing=up]`) | **rejected**, each with its path (`$.components.bad.cells[0].block`, `$.faces[0].bands[0].block`) |
| `max_ops` 10^9, no exception | **rejected**; `$.limits.max_ops: 1000000000 outside [1, 400000]` |
| `max_ops` 100000 with `exception: null` | **rejected**; `$.limits.exception: ... needs a written exception` |
| JSON `NaN` | **rejected**; `$: parse error: ValueError: NaN not allowed` |
| JSON `1e999` | **rejected**; `$.faces[0].evidence.confidence: non-finite number` |
| YAML `.nan` | **rejected**; `$.seed: non-finite number` |
| lava, spawner and TNT as explicit blocks | **ACCEPTED**, and compiled into a ready plan (M2) |
| 455-byte YAML alias bomb | **hangs** (> 60 s) (M7) |

## Self-tests
- `facade_selftest.py`: 16/16 pass.
- `facade_palette_tools.py check`: `assets ok`.
- `facade_geometry.py` self-check: ok.
- `facade_common`, `facade_schema` and `facade_compile` `__main__` checks: ok.

## Verified OK
- **No AI at runtime.** No LLM, vision or API client anywhere in the `facade_*` prep or in `FacadeTool`. Runtime is deterministic code over cached files; the only network access is the bounded EPT read-through.
- **Package data-only rules.** Forbidden keys (`code`, `script`, `exec`, `eval`, `command`, `import`, `__*`), non-JSON values, duplicate JSON keys, unknown keys, `lib:` ids constrained to `ID` (no traversal), a package id that must match its folder, and size limits (2 MB package, 64 KB library file) are all enforced with JSON paths.
- **Limits.** Styles cannot raise limits. An exception needs a reason of at least 10 characters. The plugin caps a package exception at 400k (`FacadeTool:179`).
- **Eligibility in `check()`.**
  - The content must be in `OWNABLE` (`shells`, `facade`, `facade-adopt`); `""` (legacy) is not eligible.
  - The feature must be a member, and the current block must equal the recorded after-state (tick-equivalent).
  - The cell must not be tracked, and `TileState` cells and entities in the cell are refused.
  - Egg, `roads` and `overpass` content, pilot `R` roads, Strip cells, cells outside world height, ungenerated chunks and cells outside the envelope are all refused.
  - Ownership is only ever decided from the ledger; material equality alone never decides it.
- **Atomic writes.**
  - Every cell is re-checked immediately before its write in the same tick, and a mid-component failure is rolled back in reverse order.
  - Only a complete instance is ledgered.
  - A component is never split across ticks.
  - A component crossing the scope edge is deferred whole.
  - Doors are complete in the validator (`_component_rules`) and asserted in `compile()`. A door's two halves share x and z, so they are always in one chunk.
- **Adoption.** Exact `BlockData.equals` to the prep-supplied expected block. A cell is ambiguous when it has any ledger entry, is a block entity, is air, has an invalid expected block, is tracked, is in the Strip or is ungenerated. It is reported by reason with examples. A dry run with the same candidate sha within 10 minutes is required before `confirm`. Adoption writes `before == after`.
- **Locks.**
  - Facade acquires all scope chunks in `regionLocks` only after checking every chunk against `regionLocks` and Snowball's `activeChunks`, and the lock and the retire list are computed in the same main-thread step.
  - Release is conditional (`remove(k, "facade:"+id)`) in `finish()`, which also runs on cancel and stop.
  - Snowball submit, Egg submit, and all three undos refuse Facade-held chunks.
  - Egg's release is conditional, so it can never free a Facade lock.
- **`ShellToolPlugin`/`EggTool` diff: no regressions found.**
  - `undo(..., String tool)`: Snowball keeps forward order and exact after-match. Egg keeps reversed order, `restoreBlocked` and egg-only tick tolerance.
  - `toolOf` now keeps Snowball's default `/shelltool undo` from picking Facade jobs, which was a latent cross-tool bug.
  - `tickTolerant` only adds `facade*` contents.
  - Lock messages only. Defaults are unchanged.
  - Facade construction happens after the ledger load, so the per-building facade index is rebuilt from the ledger on restart. `facadeJobs` is rebuilt from `job` rows.
- **Undo.**
  - `/facadetool undo` uses the Egg rules: dry run first, restore only cells at their after-state, never tracked cells, respect dependents.
  - Facade `upgrade_of` rows block undo of the shell or facade owner.
  - A cancelled job stays undoable, because the job row is flushed in `begin()` before the first write.
- **Retirement union.**
  - `whole` = plan chunks ∪ chunks of every live facade placement of every member (covers shrinkage).
  - `chunk` reports `obsolete_outside_scope`.
  - Plans that are not `ready` never retire. The retire re-check is done in the same tick as the write.
- **Determinism.** `det()` is SHA-256 based (not Python `hash`). There is no clock or random. Outputs are sorted, and `plan_key` = sha(inputs, geometry, generator, seed). The self-test `determinism` passes. Block states are precomputed, so chunk application order does not change any written state (apart from M5 and M6).
- **Style labelling.** There is no default selection: preview, apply and plan refuse with "no default style". A style's `label` must contain `STYLE-GENERATED`. Every style op is evidence `style`, and the report, the chat head and the plan reasons carry `STYLE-GENERATED (not observed)`.
- **Valid states.** Every compiled state is re-checked against `blocks-1.18.2.json` in `compile()`, and the plugin re-parses with `Bukkit.createBlockData`, skipping the whole unit on failure.
- **Coordinates.** `pos`/`px`/`pz` and `key`/`cxOf`/`czOf` round-trip negative coordinates (26-bit x and z). Prep writes are atomic (tmp + `os.replace`). A plan is parsed on the worker thread before the next prep starts, so there is no read/write race on plan files.

---

# Pass 2 (2026-10-29, after the fixes)

**Method:**
- I re-read `FacadeTool.java` (955 lines) and the `ShellToolPlugin`/`EggTool` diff (`matchesAfter`).
- I re-read the changed parts of the compiler, prep, identify and geometry, and re-ran every self-test and my adversarial packages.
- Two helper reviewers (read-only) audited the acceptance harness, the deploy tool and the two packages' evidence. I checked their key claims.
- I viewed the matched-camera sheets and the reference pairs.
- Offline only.

**Self-tests:**
- `facade_selftest.py`: 25/25 pass.
- `facade_palette_tools.py check`: assets ok.
- The geometry, common, schema and compile self-checks: ok.

## Status of pass-1 findings
| # | status | evidence |
|---|---|---|
| B1 attached player work | **Fixed for apply and retire; NOT for undo** (see N1) | `check()` → `attached()` checks the 6 neighbours (`FacadeTool.java:674-689`). `entities()` uses a 0.1-expanded box, so item frames and paintings are caught (`:664-671`). `retireCell` calls both (`:750`). `undoCell` (`:809-818`) calls neither. |
| M1 retire to an obsolete facade block | **Fixed** | `preFacade()` (`:532-543`) walks back over consecutive live facade placements of the members to the oldest `before`. Adopt and retire rows stop the walk. |
| M2 hazardous explicit blocks | **Fixed (reproduced)** | `adv-hazard` → `minecraft:lava is a hazardous block (denied)` for lava, spawner and TNT. |
| M3 neighbour buildings exterior | **Fixed (code and self-test)** | Geometry marks `party` edges inside non-member OSM buildings (`facade_geometry.py:255-262`), party cells get no columns (`:279-304`), and `holes_mc` is honoured by the plugin (`FacadeTool.java:217`). Self-test `m3_party_wall` passes. Not checked against live OSM. |
| M4 stale geometry and claims | **Fixed** | The key covers bid, members, parts, generator, outward, nodes and the index sha (`facade_prep.py:36-58`). The identify memo is keyed by `packages_sha` (`:139`). Invalid packages lose their claim (`:144-145`). |
| M5 layer connections across seams | **Fixed (reproduced)** | The `iron_bars` band variant now has 0 layer connections across a seam. The 6 remaining cross-seam connections are inside one component, which is deferred whole. |
| M6 ownership drift | **Fixed** | Stairs carry an explicit `shape` (self-test `m6`). `matchesAfter` ignores N/E/S/W/up and stair `shape` for `facade*` content only (`ShellToolPlugin` diff). |
| M7 YAML alias bomb | **Fixed (reproduced)** | `adv-bomb` → `YAML anchors/aliases are not allowed`, in 0.18 s. |
| minor 1: cap never placed | Fixed | 30 cap ops, `cap>parapet`. |
| minor 2-4: envelope pieces, tolerance, foreign-feature air | Fixed | `:215-220` (exact compiler rule, `parts_mc` and `holes_mc`); `:642` applies the feature check whenever a ledger row exists. |
| minor 5: `protected()` fails open | **Not fixed** | `facade_prep.py:61-68` |
| minor 6: callback exceptions | Fixed | `safe()` (`:463-472`) |
| minor 7: shared prep cancel | **Not fixed** | `:151`, `:337` |
| minor 8: retire outside limits | **Not fixed** | `gates()` (`:507-516`) counts only `inScopeOps`. |
| minor 9: huge component in one tick | Mitigated | The pre-check is resumable (`SPLIT`, `:614-618`), and the write is atomic in a fresh tick. Run 4 still saw a 62.4 ms tick (harness FAIL). |
| minor 10: unknown edges silently styled | Fixed (now has a note) | `facade_compile.py:230` |
| minor 11: event label | **Not fixed** | `:923` |
| minor 12: adopt `open()` encoding | **Not fixed** | `facade_prep.py:216-227` |
| minor 13: Snowball overwrites facade cells | Unchanged (documented risk) | |
| Adversarial set | All rejected with paths | traversal, `!!python` tag, 50 MB, bad states, `max_ops` 10^9 and 100k without an exception, NaN, 1e999, YAML `.nan`, hazardous blocks, alias bomb. No side effect. |

**Snowball and Egg regressions:** none found.
- `matchesAfter` is exact for Snowball contents and `sameModuloTicks` for `egg-` (the same as before).
- The connection tolerance applies only to `facade*`.
- `props()` keeps the old TICK_PROPS behaviour for Egg.
- The `undo(..., String tool)` order is unchanged: Snowball forward, Egg reversed with `restoreBlocked`.

## New findings

### N1 (major). `/facadetool undo` can still detach player work (B1 is not applied to undo)
**Where:** `FacadeTool.java:809-818`.

**Failure scenario:**
1. A Facade apply puts a canopy slab, sill or frame into **air** (before = air).
2. A player hangs a sign, lantern or torch on it, or an item frame. The player's block is tracked, but the facade cell is not.
3. `/facadetool undo confirm` sets the cell back to air with no `attached()` or `entities()` check, and the player's work pops on the next update.

**Fix:** reuse `attached(b, Set.of())` and `entities(...)` in `undoCell`. Count refusals as "kept: supports player work".

### N2 (minor). Undo is marked done even when cells were never examined
**Where:** `:831-835`.

**Failure scenario:** `undo-done` is logged and the job added to `undone` even when `notLoaded > 0` (edge chunks). Those cells silently lose their ownership; they are only counted in `kept_modified`. The case is unlikely, because placements only happen in interior chunks.

**Fix:** with `notLoaded > 0`, report a partial undo and leave the job live.

### N3 (minor). The shared plugin chunk ticket can be dropped under a Facade preview
**Where:** `:81-90`.
- Snowball, Egg, `pl.undo` and Facade all use the **same** plugin ticket. A Snowball job finishing in a chunk that an unlocked Facade **preview** holds removes the ticket. Later reads then load the chunk synchronously.
- Conversely, `releaseSome` skips removal while an Egg lock exists. Egg normally removes the shared ticket itself; if it does not, the ticket stays until restart.

**Fix:** document this, or keep per-tool refcounts in `ShellToolPlugin`.

### N4 (minor). Edge-chunk detection reads region files while the server may write them
**Where:** `:557-562`. The interior-chunk test reads region files on an async thread while the server may be writing them. A misread fails safe ("edge"), except a false FULL, which could trigger generation. Re-check with `w.isChunkGenerated` on the main thread before holding.

### N5 (minor). `part_hit` claims cells near a package part ahead of OSM
**Where:** `facade_prep.py:151-164`. Any block within 1.5 m of a valid package part polygon identifies as that package, even inside a different OSM building. This mis-identifies and mis-reports, but it is not a write leak: ops come from the plan.

`package_claims` (`facade_identify.py:16-28`) still reads unvalidated files. An invalid package that sorts first shadows a valid one claiming the same id, because the claim is nulled at `facade_prep.py:144`.

### N6 (minor). Ground-contact rows break atomicity for non-door components
**Where:** `facade_compile.py:433-442`. Rows v=0-1 of non-door components become plain ops (COMPILER-SPEC §4a). This is by design, but a frame or pane run can now be half-placed at the base. The seam rule still applies.

### N7 (major, ops). The deploy tool's rollback can fail
**Where:** `tools/deploy_facade_primary.py`.
- `:80-82`: rollback picks the newest `*-primary-pre-0.6.*` backup. After a second deploy, that backup holds 0.6.0, so rollback reinstalls 0.6.0.
- `:83-85`: rollback deletes the live JAR before checking that the backup has a JAR or config. A missing file leaves the server half rolled back.
- Neither the installed nor the restored JAR is sha-verified (`:72`).
- An existing `REL` freeze is reused without hashing (`:67`).
- A missing `latest.log` raises an uncaught error (`:52`).
- The `session.lock` append-open does not detect a lock on Windows (`:50`).
- Correct: port, approval gate and backup ordering. Only 25566 is touched; deploy is refused while JAR_SHA/PREP_SHA are None; the backup is taken before the swap.

## Acceptance harness (`C:\BTE-Migration\scripts\facade_test.py`, run4: 59 PASS / 1 FAIL)
**Sound:** either-order and undo-exact (real world snapshots, `:224-226`), the Snowball/Egg lock overlap (`:287`), and cancel (`:277`).

**Weak checks** (they pass without proving the claim):
- **Container and entity protection, `:196/198`.** Never exercised. Run 4 skip reasons are only "not owned" (900) and "tracked" (4): the chest and armour stand were skipped as *unowned*. `'CHEST_STILL_THERE' in S.text()` searches the whole log.
- **`:188`.** Contains `... or True`, a tautology.
- **`rr_preview_chunk_writes_nothing`, `:267`.** Asserts only `status=='ready'`; there is no ledger or world diff.
- **`blocks_persist_exactly_after_restart`, `:305-310`.** Never reads the world. It compares the plugin's undo dry run with its own ledger, and silently skips jobs when the regex misses.
- **Undo, adoption, retire and reapply, `:176, :280, :316, :320, :329, :331`.** Checked against the ledger's own before/after, or by counts.
  - `:320`: the fallback compares a retired cell with its own record, which always passes.
  - `:316`: a subset test (74 of 256 dropped were retired, and completeness is never asserted).
  - `same()` (`:89`) ignores properties when one side has none.
- **Counter-only checks.** `:205` ("complete" counts facade-adopt cells), and `:208` / `:322` (repeat and no-flip-flop).
- **Vacuous passes:**
  - Harlo `:242` (placed=0, changed=[]);
  - shared faces for HM and CPK `:140` (shared=[]);
  - `snowball_still_works` `:339` (cells_in_chunk 0);
  - setbacks `:235` (`placed>0`, 154 of 1950).
- **Determinism, `:136`.** Compares `plan_key`, an input hash, rather than plan contents.
- **Limits, `:152`.** No negative control, and it also passes on `no_geometry`.
- **Error scan, `facade_srv.py:34`.** An and/or precedence bug in `errors()` means facade SEVERE lines are missed.
- **`set_cfg`, `:274`.** Never reloaded, verified or restored.
- **No spec.** `docs/facade/TESTS-v0.6.md` does not exist.

## Package evidence honesty
**Both packages:**
- The validator reports both as valid.
- There are no images in the repo, and all 29 local reference sha256 values match.
- Every face cites existing source ids.

**Red Rock:**
- **Honest:**
  - massing is labelled (`shell: none`);
  - `unresolved` covers tower f2-f7 and `*`;
  - the `limits.exception` is needed (210k ops, 600 m) and written;
  - the licences are plausible (Commons CC with authors, 3DEP public domain, OSM ODbL, Esri marked private).
- **Major:**
  - the tower:f1 sign places "RED/ROCK" from the 2010 photos, while EVIDENCE.md:56/135 lists the current sign as unresolved;
  - no rule derives any face's `confidence`/`coverage`, and EVIDENCE gives only qualitative wording.
- **Minor:**
  - f33 cites `rr-c2006-wide`, whose coverage is f34 only and which predates the 2007 works;
  - the courtyard faces are classed `reference` on estimated pilaster spacing;
  - the exception is approved by "agent A / coordinator", not the user;
  - EVIDENCE.md still says "7 detailed faces".

**Harlo:**
- **Honest:**
  - p0:f0 coverage is 0.53;
  - p0:f2 is unresolved (trees);
  - Street View is labelled private reference.
- **Major:**
  - p1:f0/f1/f4 are `inferred` with `coverage: 1.0`, while no source lists f0 or f1;
  - the Street View frames were taken from an unkeyed thumbnail endpoint (REFERENCES.md:220), which is a ToS problem even though the label is honest.
- **Minor:**
  - p0:f1 and p0:f6 claim coverage 1.0 despite a pre-renovation frame and a tree-blocked frame;
  - the roof cover is `reference` on ortho evidence (should be `measured`);
  - the package attribution omits OSM.

## Visual accuracy: Red Rock (honest assessment)
- **Reads correctly at a distance:**
  - a long, banded hotel slab with a fin or floor line on every storey;
  - a stone pier splitting the E face about one third from the SW end;
  - a solid ledgestone NE pier carrying a sign;
  - a cream frame and top band on the W face.
  The overall proportions and floor count are plausible.
- **Not faithful:**
  1. **The glass.** The real E/W faces are a continuous, dark bronze mirror-glass curtain wall with thin fins. The plan renders a gridded window pattern with vertical mullions every 1–2 columns, and on the role sheets it reads as a mesh of punched windows, not a curtain wall. In-game block colour (dark brown/grey) is closer than the role sheet.
  2. **Signature features missing:** the stepped, curved roof canopies with rooftop palms; the one-line lowercase "red rock / CASINO" logo (rendered as stacked red block capitals "RED ROCK", from an older era); the "CASINO" letters and red-brick piers at the NW entrance; the pool drum and wood canopy.
  3. **The NW entrance is a coarse abstraction.** Box canopy, door and glazing are tan and brown blocks; the red brick and the white canopy edge of the photo do not come through.
  4. **The tower base may float.** The tower W/E "after" sheets show the tower's lowest rows missing over most of its width, an empty band between the ground strip and the tower. This matches the README's "lower 10 rows poorly covered" and the ground-contact rule. Unless the podium hides it in-game, the tower appears to float. **Check in-game before calling Red Rock accepted.**
- **Verdict:** a recognisable massing-plus-rhythm likeness (roughly "Red Rock-shaped banded tower"), not a reference-accurate facade. The `reference` class on tower:f1 and f7 overstates how closely the glass system and signage match.
