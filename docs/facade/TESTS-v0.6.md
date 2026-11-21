# Facade v0.6 candidate 0.6.0-rc1: test record (2026-10-30)
**Candidate JAR:** `SummerlinShellTool-0.6.0-rc1.jar`, sha256 `d24d3efb1b0d729d102a8d34a0fe8d823d29b19cf1850386d05515f727812a1b`.

All tests ran on disposable server 13 (port 25573) and on copies of its cache. Primary 25566 (v0.5.0) and legacy 25565 were not touched.

## Unit and self tests
- `facade_analyze.py`: ok. It covers:
  - the per-face exact schema;
  - one repair retry, then `ANALYSIS FAILED`;
  - the call budget;
  - absent vs unknown;
  - num_ctx/keep_alive;
  - the stub HTTP paths.
- `facade_selftest.py`: 35/35. New tests:
  - model backend → `inferred`;
  - uncited evidence rejected;
  - declined analysis keeps its reason;
  - the cost merge.

## Reference photos (analysis quality, not format)
These used the same image and the same hand-marked association for both backends, scored against a hand annotation. The results are in ANALYSIS.md, with per-case traces kept outside the repo.

| Photo | builtin-cv | qwen2.5vl:7b |
|---|---|---|
| Harlo S | wall ✓, glazing absent ✓, doors missed | wall ✓, everything else unknown |
| Red Rock tower | glazing ✓, wall colour ✗ | repaired, then ✗ (glazing and floors "absent") |
| Harlo N, trees | refused ✓ | refused by the gate ✓ (✗ when ungated) |
| Harlo SE, truck | refused ✓ | refused by the gate ✓ (✗ when ungated) |

## Generalization (prep, un-authored buildings, `gen_tests.py`): 11/11
- synthetic-evidence face gives mixed mode; the repeat run is cached;
- missing imagery gives STYLE-GENERATED faces with the reason, and `--fallback none` places nothing;
- Mapillary without a token reports "unavailable: no credential";
- a photo of another building is rejected (distance/side/FOV/occlusion);
- a panorama is accepted by association, then reported "panorama rectification not implemented", never observed;
- an injected source outage falls back to labelled faces;
- a real Wikimedia Commons photo with only its geotag is rejected: 257 m away, looking at the podium, older than 8 years;
- the ollama backend gives `mode: inferred`, with 0 observed faces.

## Server 13
- **Regression (`facade_test.py`): 59/61.** It covers packages, adoption, ownership, protection, preview, repeat, chunk order, locks, Red Rock cancel and undo, restart persistence, exact undo, re-apply, retirement, Snowball/Egg, max tick 9 ms, legacy 03 unchanged and no facade work in the primary ledger. Both failures were harness errors and both checks have since been fixed, but the fixed versions have not been rerun:
  - the armor stand was spawned with `%d.5` at a negative x, so it landed in the neighbouring cell (the entity was not hit);
  - the "Nothing selected after restart" check was a v0.5 expectation (v0.6 default = auto).
- **Auto spec (`live_auto2.py`, w1171439648, synthetic evidence on p0:f1): 15/16 + cancel 2/2.**
  - Adopt, then apply whole:
    - 1759 placed: 117 observed, 64 inferred, 1578 STYLE-GENERATED;
    - every op lies within 3 blocks of a shell face column, and no change falls outside the plan;
    - the chest, the armor stand (12 cells skipped "attached: entity armor_stand") and the tracked edit were preserved;
    - components are complete;
    - a repeat apply placed 0;
    - after a restart, 1759/1759 blocks were exact;
    - Snowball and Egg work, max tick 34 ms, no errors.
  - Cancel kills prep in 4.5 s with no ledger lines.
  - **FAIL:** undo restored 1757 cells and kept the later manual edit (gold block), but it also kept 1 cell beside the pre-existing chest as "attached: chest on the west face". The result is reported PARTIAL and the job stays live.
- **Coverage of the building's exterior facade area (2231 face cells):**

  | evidence | area | blocks in the plan |
  |---|---|---|
  | observed | 5.78 % | 129 |
  | inferred | 2.69 % | 120 |
  | STYLE-GENERATED | 64.68 % | 1727 |
  | existing shell kept | 26.85 % | – |

## Not tested / blocked
- **Live Mapillary retrieval** is blocked: there is no token.
- **Game frame rate during inference** was not measured, because the client was in the background.
- **Real-photo apply in the world:** none of the permitted photos with a usable pose passes association, so the only applied evidence is synthetic.

# Candidate 0.6.0-rc2 (2026-10-30, experimental)
**JAR:** `SummerlinShellTool-0.6.0-rc2.jar`, sha256 `9915b559ba71386c61ef92c1ed426b2fa37002223afba07f2ff3855daaed99cd`.

Every failed or corrected check was rerun on this build. Synthetic evidence is reported separately below. No real imagery has been applied yet (Mapillary is blocked on the token).

**Product changes since rc1**
- **Undo and retire:** they use a physical dependency rule. A free-standing chest beside a cell no longer blocks restoring it. Attached blocks (wall torches, signs, ladders, buttons, things standing on the cell, gravity blocks) and entities are still protected.
- **Adoption:**
  - the dry run writes every candidate cell with its verdict to `facade-reports/<job>-adopt-cells.csv` and prints a summary in chat;
  - `adopt show` outlines the cells with particles;
  - confirm adopts exactly the dry-run set;
  - ownership is never inferred from material alone.
- **Analysis:** an alignment check (roofline, wall continuing above the top, door scale), plus measured glazing rectangles that the compiler places as-is (`rects`).

**Results**
- **Self tests:** facade_analyze ok, facade_selftest 36/36. The Java dependency rule passes its self-check (about 45 cases, offline harness, not in the repo).
- **Server 13 regression (`facade_test.py`): 60/61.**
  - Both corrected checks now pass on this build:
    - `protected_components_skipped_whole`: 16 cells skipped for the armor stand;
    - `selection_cleared_after_restart`.
  - **FAIL `bounded_tick_time`: 58.6 ms.** This was a 57 ms GC pause during the 200k-op Red Rock whole-resort preview/apply. The Facade code's own work peaked at 7.7 ms. The test heap was reduced to 1–6 GB to protect the game client's RAM.
- **Server 13 auto spec (`live_auto2.py`, SYNTHETIC true-scale storefront on w1171439648 p0:f1): 17/17, plus cancel 2/2.**
  - Adoption: the dry-run CSV listed 5138 cells (5136 would be adopted; 2 refused: the chest and a tracked edit). Confirm adopted exactly those 5136.
  - Apply: 1734 placed, of which 156 observed (continuous glass) and 1578 STYLE-GENERATED.
  - Protection: chest, armor stand and tracked edit preserved.
  - Integrity: no change outside the plan, every op aligned to the shell, components complete, a repeat apply placed 0.
  - Restart: 1734/1734 blocks exact.
  - Undo: 1733 restored exactly, and the later manual edit kept.
  - Regression and performance: Snowball and Egg work, max tick 13.4 ms, no errors.
- **Coverage (exterior facade area, 2231 cells):**

  | evidence | area | blocks |
  |---|---|---|
  | observed | 8.47 % | 189 |
  | STYLE-GENERATED | 64.68 % | 1727 |
  | existing shell kept | 26.85 % | – |

**Known mismatches, from the photo-to-build sheet**
- The storefront glass is continuous now, but its left edge sits about 1 m left of the photo's. This is suspected to come from mapping metres to face columns.
- The glass block is grey where the photo's glass is navy, and the wall block is paler than the photo's tan: a material-matching issue.

# Candidate 0.6.0-rc2 + prep tree 2fe2ef83 (2026-10-30, experimental)
**Composition:** JAR `9915b559ba71386c61ef92c1ed426b2fa37002223afba07f2ff3855daaed99cd` (unchanged since rc2), plus prep tree sha256 `2fe2ef832341370f242f981258cb4d9bed21f4dcd11d8bdb58430c6c12b459ae`. The tree hash covers the sorted sha256 list of `prep/**/*.py|json`.

## Correction to the rc1 record (kept on purpose)
rc1's report said its synthetic fixture "squeezed a 16 m frontage into a 6 m face". **That was wrong.**
- **What it was:** the fixture showed only the bottom 6 m of the synthetic design, stretched about 1.2× horizontally. Its generator (`synth()` with `v_claim` < the design height) wrapped negative indices, so the content was garbled.
- **What caused the storefront-to-windows mismatch:** the compiler turned the glazing fraction into per-bay windows. It now places measured glazing rectangles as seen.
- **Replacement:** the fixture is now `fixture_true.py`, a true-scale design warped through the association quad. It is SYNTHETIC and is reported separately from real imagery.

## Real imagery (Mapillary, 2026-10-30), reported apart from the synthetic tests
**Access**
- The token is a Client token, stored in `C:\BTE-Migration\secrets\mapillary.token`, outside the repo and never logged.
- The first token tried was the Client secret, which gave empty search results and error 100/33. This matches what Mapillary staff reported in the forum the same day.
- Requests now succeed (sanitized checks): search 200, image detail 6/6 at 200, sequence 200.

**Retrieval and association on 8 un-authored pilot buildings**
- Mapillary returned 1844 candidates (109–371 per building, all perspective, captured 2018–2021). Retrieval took about 1.6–1.9 s per building.
- 30 faces received geometrically associated images. Most rejections were: behind the face, too far, under 40 % of the face in the field of view, occluded, wrong building, older than 8 years.

**Pose accuracy: the blocker**
- At first one face of w558884282 (California Pizza Kitchen) came out "observed" from three real photos. The overlay showed its quad in the sky, among palm fronds:
  - the photos are pitched up about 14°, but the pose was assumed level;
  - the sky check does not recognise overcast sky.
- **Fixed:** pitch and roll now come from Mapillary `computed_rotation`. The quads then sit at ground level.
- **Still offset:** about 6° in yaw, and the OSM footprint is larger than the visible building, so the roofline projects low.
  - Pure lon/lat projection agrees with the Minecraft-space projection to within 0.02 of the image width, so the coordinate conversion is correct.
  - The error is in the inputs: GPS/SfM pose and the footprint.
- **Tried and rejected:** an edge-based registration (yaw/pitch/position search). It "verified" two poses that were plainly wrong, landing on the road and on palm trunks.
- **Gate now in place:** an image from a provider is never used until its pose is verified to facade accuracy. `local` refs, whose pose is stated by the supplier, are exempt.
- **Result: 0 observed faces from real imagery.** Every face carries its reason; none is silently filled.
- Also fixed while running real imagery:
  - one face with no clean wall pixels (NaN colour) aborted the whole building's analysis; it is now isolated per face;
  - builtin-cv reported bays narrower than its own validator allows; they are now dropped.

## GC / tick time (`bounded_tick_time`, threshold unchanged at 50 ms)
**Still FAIL at a realistic budget:** -Xms4G -Xmx8G, as on the primary, with Minecraft + Bliss and the primary running.
- Max tick 52.3 ms in the Red Rock whole apply (200k ops). The Facade work was 6.4 ms; the GC pause in that step was 50 ms.
- The GC log shows G1 young evacuation pauses of 35–45 ms, with 2.3–2.8 GB still live after collection during the job.
- **Cause:** the size of the live set (plan plus ledger index), not the per-tick work.
- **Next steps:**
  - a class histogram during the job;
  - a compact ledger index and plan arrays instead of objects;
  - testing `-XX:MaxGCPauseMillis` on server 13 only.

## Server 13 (final candidate, 4/8 GB heap): `live_auto2.py` 17/17, cancel 2/2
These results are SYNTHETIC: the true-scale storefront fixture, with real Mapillary retrieval active but unused because poses are unverified.
- Adoption: the dry-run CSV listed 5138 cells and would adopt 5136; confirm adopted exactly those 5136.
- Apply: 1734 placed (156 observed glazing, 1578 STYLE-GENERATED).
- Protection: chest, armor stand and tracked edit preserved.
- Restart: exact.
- Undo: 1733 restored exactly, and the later manual edit kept.
- Snowball and Egg work, max tick 28.8 ms.
- The full regression run on rc2 was 60/61, with `bounded_tick_time` the only failure (see above).

# Candidate 0.6.0-rc4 (2026-10-31, experimental checkpoint: bounded chunk loading)
The checkpoint (JAR, source snapshot, hashes, logs, every job report) is in `reports/facade-v0.6/rc4-checkpoint/`. Results:
- **Full regression:** 62/62.
- **Chunk window:** peak 64 loaded chunks against a cap of 64, never exceeded.
- **Red Rock whole-resort apply (197,187 blocks):**
  - worst job tick 41.2, 43.4 and 12.1 ms in the three runs; the spikes are GC pauses of 40, 36 and 0 ms;
  - server-wide tick gap 54.3, 52.1 and 55.8 ms.

Two different timing measures, kept separate:
- **Job tick time** (`max_tick_ms`) is the `bounded_tick_time` gate, threshold 50 ms unchanged. It is the longest server tick in which the job worked, and it includes any GC pause that landed in that tick.
- **Server-wide tick gap** (`server_max_tick_interval_ms`) is the longest interval between any two server ticks while the job ran, whatever the cause. It is reported only, not gated.
