# Facade analysis and materials (v0.6 auto facades), owner: agent V

Covers AUTO.md §6 (analysis backends), §7 (materials) and the analysis half of §3. Code: `prep/facade_analyze.py`, `prep/facade_materials.py`.
Self-tests: `python prep/facade_analyze.py --selftest`, `python prep/facade_materials.py --selftest`.

## 1. Interfaces
```
facade_analyze.analyze(image_rgb, projection, face_meta, cfg=None) -> (face_section, cost)
facade_analyze.analyze_building(geometry, evidence, association, cfg) -> {"faces": {face_id: face_section}, "cost", "model"}   # prep stage
facade_analyze.validate_face(face, face_meta) -> [errors]
facade_analyze.parse_model_output(text, face_meta) -> (model_face | None, [errors])
facade_analyze.analyzer_id(cfg) -> {"backend", "version", "model"};  VERSION = "0.6.0"
facade_materials.palette_for_face(face_section, cfg=None) -> {"roles", "zones", "texture", "matches", "derived"}
facade_materials.palettes(spec, geometry, cfg) -> {face_id: palette}                                                           # prep stage
```
- **`image_rgb`**: an HxWx3 array or a PIL image, `None`, or a list of them (several images of one face; `projection` is then a list of the same length).
- **`projection`**: `{"corners_px": {"u0v0", "u1v0", "u1v1", "u0v1"}, "u_m": [a, b], "v_m": [a, b], "evidence": {...}}`.
  - Corners are pixel positions (x right, y down); v0 is the bottom.
  - `u_m`/`v_m` are the face coordinates the corners stand for. They default to the full extent.
  - Cells outside that rectangle are never sampled, so the homography is never extrapolated.
  - `analyze_building` builds these from `facade_associate` accepted entries:
    - `corners_norm × image size`;
    - one projection per segment quad (`u_m = [u0, u1]`, `v_m = [y0 − base_y, y1 − base_y]`, SOURCES.md 3.1);
    - images by `path`, or through `facade_sources.download()` for a `url`.
- **`face_meta`**: `{"face_id", "u_extent_m", "v_extent_m", "parts"?}`.
  - From geometry, the extents equal `facade_autospec.extent()`: u 0..length_u, v rows base_y..top_y.
  - The optional `parts` (`u0_m, u1_m, v1_m`) mask cells above a stepped roofline.
- **`face_section`**: the AUTO.md §3 face object.
  - It is always valid against `validate_face`, which also passes `facade_autospec.validate_face`.
  - If a result would be invalid, it is replaced by an `unresolved` face that gives the reason.
- **`cost`**: `{"usd", "requests", "by_provider"}`.
- **Prep config** (`facade_prep.auto_cfg`) is mapped as follows:
  - `analyzer` → backend;
  - `timeout_s`;
  - `max_images_per_face`;
  - `secrets_file`;
  - `ai_max_usd_per_job/day`.
  - **The ledger needs `cfg["cache_dir"]` (or `cache`).** Prep must pass the cache dir, or the anthropic backend reports "not configured".

## 2. builtin-cv method (default; local, free, deterministic)
1. **Rectify.**
   - A DLT homography in numpy (no OpenCV) maps the face plane (u, v metres) to image pixels.
   - Sampling goes onto a metric grid of 4 cells per metre (`ppm`), with 3×3 bilinear supersamples per cell (`ss`), so masks are computed on sub-cell samples.
   - Corners behind the camera, and a singular homography, give `bad projection`.
2. **Mask** (per supersample, then aggregated per cell):
   - **vegetation:** excess-green `(2g−r−b)/(r+g+b) > 0.10`, over a third of the cell;
   - **sky candidates:** blue (`b > r+0.06`, `b ≥ g−0.02`, `V > 0.45`) or pale bluish haze. A candidate counts as **sky** only if it is connected to the top of the grid through sky, vegetation, border or above-roofline cells. A candidate that is not connected is a **reflection** of sky in glass and counts as glazing.
   - **specular:** clipped (`max ≥ 0.985`). When more than 25 % of the face is clipped, the image is treated as overexposed and clipped pixels count as wall (noted in `unresolved`).
   - **deep shadow:** `max < 0.08`;
   - **border:** within 2 px of the image edge, outside the image, or outside the framed quad.
3. **Fuse.** Several images of one face (and several quads of one image) go onto the same grid. Each valid cell is a weighted mean, with weight = `assoc_score · cos(view_angle)` (minimum 0.1). Occlusions in one image are filled by another.
4. **Coverage gate.** If valid cells are less than 35 % of the face (`min_coverage`), the face is `unresolved`. Every confidence is multiplied by `min(1, coverage/0.6)`.
5. **Wall reference.**
   - k-means (k = 4, k-means++, seed 0) in CIELAB over non-reflection cells.
   - Candidates are clusters with share ≥ 8 % and L ≥ 45.
   - The smoothest candidates win: those within 1.5 of the lowest median local L-std (3×3). Among them, the largest is chosen.
   - Wall rgb = median of that cluster's cells (L ≥ 45, ΔE < 15).
6. **Classify cells:**
   - **shadow:** darker than the wall by more than 18 L, with the same chromaticity (L1 < 0.03). It counts as opaque wall for glazing and floors, and is excluded from colours.
   - **glass:** a reflection, or L < 45, or darker by more than 18 L and bluish (b* < −3).
   - **opaque:** the rest.
7. **Zones** (k-means per horizontal band):
   - A row whose median opaque colour differs from the wall by ΔE > 12 joins a run.
   - Runs of ≥ 2 rows (0.5 m) become zones. The zone colour is the dominant cluster of 2-means within the run.
   - Role by position: touching the bottom → `base`; touching the top of the data → `parapet`; ≤ 1.5 m → `band`; otherwise → `wall`.
   - Runs of ≥ 1 m with busy texture (local L-std more than 4 above the wall) are dropped as reflections/foliage, and the drop is listed in `unresolved`.
   - Vertical runs ≤ 3 m wide touching a face end → `trim`.
8. **Glazing.**
   - fraction = glass / (glass + opaque + shadow);
   - rgb = median of non-reflection glass;
   - conf = coverage factor × (0.5 + 0.5 · clip(ΔL wall−glass / 40)).
9. **Floors.**
   - Row profile = glass fraction per row. Unbiased autocorrelation over lags of **3–6 m**, needing ≥ 2.5 periods.
   - The first local peak ≥ 0.85 × the best wins, with parabolic refinement, and needs strength ≥ 0.25.
   - If the glass profile is flat (curtain walls), the high-passed luminance row profile is tried.
   - Phase is the circular centre of the low-glass plateau of the folded profile, using only rows between the ground floor and the parapet.
   - Floor lines below 2.5 m or within 1.5 m of the top are dropped, as are lines that do not sit on a low-glass row.
   - `first_m` = the lowest remaining line; `count` = 1 + lines.
10. **Bays.**
    - Column profile of glass over the upper floors, with lags of 1.5–12 m; luminance is the fallback.
    - `width_m` = median width of glass runs above the half-range.
    - `offset_m` = u of the first complete opening.
11. **Entrances.**
    - Only when the ground is framed.
    - Columns whose bottom 0.5 m and bottom 2.2 m are both ≥ 60 % glass/dark form runs.
    - Runs of 1.5–8 m are entrances. Longer runs are listed as `ground glazing ... (storefront?)` in `unresolved`.
12. **Canopies** (low confidence, ≤ 0.35):
    - Between 2 and 6 m: a lit row distinct from the wall (ΔE > 8, not darker than wall − 10 L), with shadowed wall (≥ 30 %) or glass directly below, over ≥ 1.5 m.
    - Depth is not observable, so it is fixed at `depth: 2` and listed in `unresolved`.
13. **Status.**
    - `observed` when a wall zone passes `min_item_conf` (0.30).
    - Confidence = mean of the wall, glazing, floors and bays confidences.
    - Every item below its threshold is `null` / omitted and named in `unresolved`.

### Thresholds (`DEFAULTS`)
| key | default | meaning |
|---|---|---|
| `ppm`, `ss`, `border_px` | 4, 3, 2 | grid cells/m, supersampling, edge margin |
| `min_coverage` | 0.35 | minimum valid share of the face |
| `min_item_conf`, `canopy_min_conf` | 0.30, 0.20 | report an item only above this |
| `veg_exg`, `sky_blue`, `sky_v`, `spec_v`, `dark_v` | 0.10, 0.06, 0.45, 0.985, 0.08 | mask thresholds |
| `glass_l_max`, `glass_dl`, `shadow_chroma`, `zone_de` | 45, 18, 0.03, 12 | classification |
| `floor_m`, `bay_m`, `period_min` | (3, 6), (1.5, 12), 0.25 | periodicity priors, minimum peak strength |
| `ground_min_m`, `entrance_min_m`, `entrance_max_m` | 2.5, 1.5, 8 | floor-line and entrance limits |
| `max_images` | 4 | images used per face |

## 3. What builtin-cv cannot see (limitations)
- **Illumination.** Colours are camera colours. There is no white balance and no shade correction.
  - A cream frame in shade reads cool gray (Red Rock: (127,131,136) → `polished_andesite`).
  - An overexposed white wall reads pure white.
- **Reflective glass.**
  - Brightly reflecting glass (bronze or mirror) can read as an opaque material band.
  - Busy bands ≥ 1 m are dropped, but a smooth reflection survives as a `band` (Red Rock: one bronze band → `jungle_planks`).
- **Neutral walls.** On a gray or white wall, shadows cannot be told from dark glass by chroma, so shadows may count as glazing (flagged in `unresolved`).
- **Occluders that are not green.** Tree trunks, bare branches, vehicles, people, signs and poles are not masked. They read as glass (dark) or wall.
  - The coverage gate (35 %) and the confidences are the only defence.
  - A box truck in front of a service wall is rejected by coverage, not recognised.
  - Olive or green-painted walls would be masked as vegetation.
- **Sky.** Overcast white sky is not detected unless it is slightly bluish.
- **Resolution.** At 4 cells/m:
  - mullions, frames, sills, fins and thin bands (< 0.5 m) are not resolved;
  - `frame` is therefore **derived** (a darker tone of the glazing), not observed;
  - lettering, lights and materials (stucco vs concrete) are not recognised.
- **Floors and bays need ≥ 2.5 periods and contrast.**
  - Low-rise (1–2 storey) faces and blank walls give `floors`/`bays` unresolved.
  - Ground storeys taller than the typical floor are handled only by the low-glass check on the first line.
- **Geometry.** A planar homography per quad: curved faces need several quads (R provides one per segment).
  - Corner error from a tilted camera (R's level-camera assumption) shifts rows by the same amount.
  - Panoramas (equirectangular) are **not** rectified yet. They are listed as unresolved, never used.
- **Canopy depth, roof and parapet height above the measured top, and doors versus windows at ground level** cannot be determined.

## 4. Optional model backends (off unless configured)
Both send ONE fused, rectified PNG of the face (long side ≤ 1024 px, top = roof). The prompt states the metric ranges and asks for the strict JSON face fields of §3 (no `status`/`evidence`).

The reply is processed as follows:
- It is parsed with `json.loads` only: NaN and Infinity are rejected, and so are outputs over 64 KB.
- It is validated by the same schema walker as packages (`facade_schema.chk`, data-only, forbidden keys).
- **Unknown keys and out-of-range values reject the whole reply.** This includes a coordinate outside the measured extent, an rgb outside 0..255, a conf outside 0..1, or a boolean where a number is expected.
- **Model text is never executed.**
- `status` is set by us: observed only if `confidence ≥ 0.30` and there are zones. The evidence comes from the association.

| backend | needs | call | cost |
|---|---|---|---|
| `ollama` | `ollama_model` (+ `ollama_url`, default `http://localhost:11434`) | `POST /api/generate` with `format: "json"`, `stream: false`, temperature 0 | $0, 1 request |
| `anthropic` | `ai_max_usd_per_job > 0`, `ai_max_usd_per_day > 0`, `cache_dir`, and a key in env `ANTHROPIC_API_KEY` or in `secrets_file` (JSON `{"ANTHROPIC_API_KEY": ...}` or `KEY=value` lines) | Messages API via `requests` (`x-api-key`, `anthropic-version: 2023-06-01`), model `claude-opus-5-5`, `max_tokens` 4096, `output_config.effort: low`, image + text | see below |

**Cost controls (anthropic).**
- **Before every call**, the estimate is checked:
  - the estimate is an upper bound: (prompt chars / 3 + w·h/750 + 100) input tokens at `anthropic_usd_per_mtok_in` (4.0), plus `max_tokens` at `anthropic_usd_per_mtok_out` (20.0);
  - it is checked against `ai_max_usd_per_job − spent(job)` and `ai_max_usd_per_day − spent(today)`;
  - over either cap, **no request is sent** and the face is `unresolved: anthropic backend failed (no fallback): anthropic cost cap ...`.
- **After the call**, actual usage is recorded from `usage.input_tokens` (+ cache tokens) and `output_tokens` × the prices.
  - The ledger is `<cache_dir>/facade/ai-ledger/<YYYY-MM-DD>.json`, holding `total_usd`, per-job totals and the last 500 calls.
  - A failed HTTP call is recorded at $0. An unparsable 200 response is charged the estimate.
  - Known gap: the ledger update is not locked, so concurrent jobs could lose an entry.
- **Secrets:**
  - the key is read only from env or the secrets file;
  - it is sent only in the request header;
  - it is never written to the ledger, the face section, errors or logs (errors are scrubbed).
- **No silent fallback.** A missing configuration, cap, HTTP error, refusal, timeout or rejected output gives an `unresolved` face whose first `unresolved` entry names the backend and the reason. builtin-cv is never substituted. Prep then applies its own procedural fallback, labelled as such.
- Neither backend is installed or keyed here. Both are tested only against a local stub HTTP server in the self-test.

## 5. Materials (`facade_materials`)
- **Matching.**
  - Lab nearest match (CIE76) against `colours-1.18.2.json`.
  - Candidates per role pass `facade_palette_tools.role_ok`. `base` uses the wall rules.
  - Structural roles (`wall`, `base`, `band`, `parapet`, `trim`, `frame`) take **full opaque blocks** (coverage ≥ 0.99, not tinted).
  - `glass` takes **stained-glass blocks** only: no panes (connection states), no clear or tinted glass.
  - Every state passes `facade_schema.check_block` (1.18.2 validity + deny-list), both at candidate time and on the final output.
- **Texture penalty** (ΔE units): the colour table has no texture variance, so this is a name heuristic.
  - +12 for noisy, patterned or utility textures: cobble, mossy, cracked, chiseled, bricks, logs, raw stones, ores and metal blocks, workstations, organics, prismarine and more.
  - +3 for mild ones: planks, wool, terracotta, end stone, quartz pillar or bricks, purpur, stone.
- **Coherent zones.**
  - One block per zone.
  - A non-wall zone within ΔE 6 of the wall reuses the wall block.
  - `roles` takes the first zone of each role. The `zones` list keeps the input order, so facade_autospec can index it.
- **Dither** (optional, `dither: true`):
  - for wall or base zones of ≥ 60 m²;
  - a pair of top-8 candidates, at most ΔE 12 apart, whose 50/50 mix beats the single block by ≥ 3 ΔE;
  - emitted as palette texture `{"alt", "rule": "rows%2==1"}` (whitelisted coursing, never random noise).
- **`frame`** is derived: the glazing (or wall) colour × 0.6, listed in `derived`.
- No HueBlocks code or data is used (it has no licence; see ASSETS.md).

## 6. Test results (2026-10-29)
**Synthetic facade** (self-test; generated with PIL in the test):
- The facade is 24 × 16 m:
  - 3.5 m floors (first line 4.5 m), 2 m windows at a 4 m pitch, offset 1 m;
  - tan wall (205,185,145), gray base, cream parapet, a 2 m double door with a fascia and its shadow;
  - a 0.8 m cast-shadow band.
- The scene adds blue sky, a tree in front, and a perspective warp.
- The face is claimed 17 m tall, so the top metre is sky.

| item | truth | recovered |
|---|---|---|
| floor height / first / count | 3.5 / 4.5 / 4 | 3.56 / 4.43 / 4 (conf 0.44) |
| bay pitch / width / offset | 4.0 / 2.0 / 1.0 | 4.0 / 2.0 / 1.0 (conf 1.0) |
| glazing fraction | 0.318 | 0.311 |
| wall colour | (205,185,145) | (204,184,144), ΔE 0.37 → `sandstone` |
| zones | base, parapet | base 0–0.5 m (121,120,113), parapet 15–16 m (238,229,212) |
| entrance | 8.5–10.5 m | 8.5–10.5 m |
| canopy | 6.5–12.5 m at 3.0 m | 7.75–12.5 m at 3.0 m (conf 0.35) |

- Sky and tree colours enter no zone: every zone is ΔE > 20 from both, and the wall block is not blue or green.
- Fusing a second, oblique and cropped image raises coverage from 0.85 to 0.93.
- **Other self-test checks:**
  - a missing image gives `unresolved`;
  - the cost cap blocks the call (0 stub hits);
  - the day cap blocks a new job;
  - the ledger records the actual usage, and the key appears in neither the ledger nor the face;
  - the model's JSON is rejected for an extra key, rgb 300, u outside the extent, `code` key, confidence 1.5, boolean count, `status` from the model, NaN or Infinity;
  - HTTP 500 is reported with "no fallback";
  - ollama is tested through the stub;
  - the `analyze_building` adapter is tested with normalised two-segment quads and a skipped panorama.

**Real reference photos** (`C:\BTE-Migration\facade\refs`, local only; corners hand-picked; extents estimated, not measured):

| photo / face | result | assessment |
|---|---|---|
| Harlo S wall (`S_face_from_SE_2022-10`) | observed, coverage 0.99. Wall (254,253,255) → `smooth_quartz`; base 0–1.5 m (207,193,175); glazing 0; floors and bays unresolved | **Right in kind:** white stucco over a CMU base, no windows. The image is overexposed, so the wall is pure white. The base colour is warm (the CMU in sun). The base height is plausible. A thin `trim` at one end (232,231,224) is spurious or a shadowed corner. |
| Red Rock tower W (`commons_Red_Rock_hotel_tower_2018`), frame mapped to v 20–63 m | observed, coverage 0.62. Wall (127,131,136) → `polished_andesite`; glazing 0.88 (76,61,58) → `gray_stained_glass`; floors unresolved; bays 4.5 m (width = pitch) | **Partly right.** Glazing dominance is right. The wall is the frame **in shade** (the true finish is cream), so the colour is wrong by illumination. One bronze reflection band (26–28 m, `jungle_planks`) is a false zone; two busy ones were dropped. Floors were not found: the floor lines at this (estimated) scale are about 2.7–2.9 m apart, below the 3 m prior. With a 2.5 m prior it found 2.7 m at conf 0.32. The bays are not the mullion module (≈1.5 m per EVIDENCE.md). |
| Harlo N (`N_facade_2025-04`) | unresolved, coverage 0.20 (vegetation 73 %) | **Correctly refused:** palo verde trees cover the facade. |
| Harlo SE service (`SE_service_2022-10`) | unresolved, coverage 0.29 (vegetation 68 %) | **Correctly refused.** The truck is not masked, though. With `min_coverage` 0.25 it was "observed" with the truck's wheels as two entrances. That is why the gate is 0.35. |

**Conclusion.**
- builtin-cv is reliable for:
  - masking greenery and sky;
  - refusing occluded faces;
  - colour on evenly lit opaque walls;
  - rhythm on regular punched-window facades.
- It is weak on:
  - shaded or reflective facades;
  - curtain walls;
  - true material colour under non-neutral light.
- **Treat its colours as camera colours and its rhythm as `inferred` unless the confidence is high.**

## Local VLM measurement (2026-10-29: Ollama 0.40.13, qwen2.5vl:7b, RTX 5060 8 GB)
The model ran on the same four local reference photos as builtin-cv. Each call took 6–15 s, or about 68 s for the first one, which includes the model load.

**Free-form JSON:** all 4 answers were rejected by the validator (for example `null` instead of a list).

**Schema-constrained output** (Ollama `format` = JSON schema):
- **Harlo S wall:** rejected; an entrance lay outside the face.
- **Red Rock tower W face:** rejected; its zones lay outside the face.
- **Harlo N face (73 % trees):** accepted as a grey wall with a 10 m entrance and a canopy, which is a hallucination that builtin-cv had refused.
- **Harlo SE service wall:** reported a blue wall at low confidence, so the face stayed unresolved.

Two safeguards follow from this:
1. **Coverage gate first.** The builtin-cv occlusion/coverage gate runs before any model call. A face it finds blocked is never sent to a model, and that costs nothing.
2. **Model output is always `inferred`.** Every model item's confidence is capped at 0.49, so the compiler labels it `inferred`, never `observed`. The model's self-reported confidence is not calibrated.

With the safeguards, both blocked photos are refused without a model call. On these photos the 7B model is not better than builtin-cv, so it remains optional and builtin-cv remains the default.

## Strict output, repair retry, unknown vs absent (analyzer 0.6.1, 2026-10-29)
**Contract changes**
- **Exact schema.** The Ollama `format` schema is built per face. It lists:
  - every key (`additionalProperties: false`);
  - enums;
  - item caps;
  - that face's u/v ranges.
- **Strict validation.** `validate_face` still checks everything, because the decoder may not enforce every bound. Invalid output is never trimmed or partly kept.
- **One bounded repair retry.** If a reply fails validation, the model is asked once more, with the validator's error list. If the second reply also fails, or the retry call itself fails, the face is `unresolved` with `ANALYSIS FAILED: ...` and nothing from either reply is used.
  - Both calls are counted in `cost.requests` and, for anthropic, in the ledger.
  - A face that was repaired on the retry is noted in `unresolved`.
- **`absent` vs unknown.** The model must return `absent: [features]`.
  - A feature listed there is a claim that it does not exist. Listing a feature as absent while also describing it is a validation error.
  - A feature that is null/[] and not in `absent` is **unknown**, and `analyze()` adds `unknown (not determined, not placed): ...`.
  - builtin-cv now reports "enough wall seen, no glass" as `absent: [glazing]`. It previously reported a fake `glazing {fraction: 0, rgb [0,0,0]}`.
  - The prep status line carries `features: {glazing: present|absent|unknown, ...}` per observed face. Neither unknown nor absent features are placed.

**Rerun on the same four photos, scored against hand annotation** (`refs_truth.json`: wall colour, base, glazing fraction/colour, floor height, door positions, and which features are really absent). "ungated" bypasses the coverage gate, as a diagnostic only.

| photo | builtin-cv | qwen2.5vl:7b (pipeline) | qwen ungated |
|---|---|---|---|
| Harlo S wall (white stucco, no glass, 2 service doors) | wall dE 9 ✓, glazing **absent ✓**; doors missed (unknown) | wall dE 1 ✓; everything else unknown (doors missed); also a "base" zone covering the whole wall in the wall colour | – |
| Red Rock tower W (cream frame, bronze glass curtain wall, ~3.3 m floors) | glazing 0.88 ✓; **wall WRONG** (grey 127,131,136, dE 31); floors missed | needed the repair retry. Then **valid but WRONG**: grey wall (dE 30) and glazing and floors claimed **absent** on a glass tower | – |
| Harlo N (73 % trees) | refused ✓ | refused by the gate, no call ✓ | **WRONG**: describes a grey wall |
| Harlo SE (truck + tree) | refused ✓ | refused by the gate, no call ✓ | **WRONG**: describes a blue wall |

**What this shows**
- Passing the schema says nothing about correctness. The Red Rock reply is valid JSON in range, yet it denies the most obvious feature in the photo.
- The 7B model never declined on its own. The gate is what refuses occluded faces.
- Where the model was right (the Harlo wall colour), it added nothing that builtin-cv lacked.
- **qwen2.5vl:7b stays optional and off by default.** Its items stay labelled `inferred`, and its `absent` claims are reported but carry no more weight than unknown.

**Timing and GPU** (RTX 5060, 8151 MiB; Minecraft not running):
- One call takes 4.8–6.7 s. A repaired face takes 12.6 s (2 calls).
- VRAM is about 1.2–2.0 GB with nothing loaded. With the model resident (5104 MiB, fully on the GPU) it is about 7.7 GB, which leaves only about 0.4 GB.
- The GPU test with the Minecraft client and Bliss shaders running is in the next section.

### GPU test with Minecraft + Bliss running (2026-10-29)
- **Setup:** the Modrinth profile `BTE-Las-Vegas-Modern-Test-1.18.2` was in a world, with Iris and Bliss v2.0.4 enabled. Same four photos, same script (`truth_run.py`), with VRAM sampled every 0.25 s.
- **VRAM:**
  - The game alone used 3369 MiB in total, about 2.1 GB over the desktop baseline.
  - Loading the model took the total to 7.8 GB of 8151 MiB. Ollama reported all 5104 MiB of the model in VRAM; it did not spill to the CPU.
  - Game + model is about 8.5 GB, more than the card has, so the driver pushed the game's allocations out to shared memory. After the model was unloaded, the total read 1819 MiB instead of 3369 MiB.
  - **The effect on the game's frame rate was not measured.** Expect stutter while the model is resident.
- **Speed:** the model ran about 3× slower with the game running.

  | call | idle | game running |
  |---|---|---|
  | Red Rock (2 calls with the repair retry) | 12.6 s | 36.7 s |
  | single call, ungated | 4.8 s | 16.1–28.3 s |
  | first call, including the cold load | – | 36.2 s |

- **Outputs:** identical to the idle run for all 10 runs (temperature 0). Correctness did not change, only speed.
- **Mitigation:** `ollama_keep_alive` now defaults to `30s` (Ollama's own default is 5 min), so the model leaves VRAM 30 s after the last call of a job.
- builtin-cv uses no GPU and takes 0–1.2 s per face either way.

## Review and hardening (2026-10-30)
**Fixes**
- **Model faces are never `observed`.**
  - With `ollama` or `anthropic`, an analysed face's plan evidence class is `inferred`. On top of that, every item's confidence is capped at 0.49.
  - The prep status line reports `mode: inferred`, chat says "auto: model-INFERRED (vision model, uncalibrated, not observed)", and the plan reasons count "model-INFERRED face(s)" separately.
- **At most two model requests per face:** the first call plus one repair retry, with a validator in between.
- **Per-job model-call budget:** `model_max_calls_per_job`, 12 by default. Once it is used up, faces fail with a clear `ANALYSIS FAILED ... budget` and no request is sent.
- **Model limits are separate from the imagery providers' 20 s timeout:**
  - `model_timeout_s` is 90 s, because in-game calls measured 16–37 s;
  - `ollama_num_ctx` is fixed at 4096;
  - `ollama_keep_alive` is 30 s.
- **Evidence references are checked before compilation.** A face's evidence must cite an image that the association accepted for that face; otherwise the face is rejected with the reason.
- **Declined analysis keeps its reason.** Before this fix, an unresolved section that still listed its image ("panorama rectification not implemented", "ANALYSIS FAILED", a coverage refusal) failed prep validation. Its reason was then replaced by "analysis output rejected".
- **Cost merge fix.** `facade_sources` reports per-provider request counts as plain integers. Prep's cost merge crashed on them as soon as Panoramax made a request, which it does in the default provider list. The whole auto job then ended `provider_failure, nothing planned`.
- **Traces for diagnosis.** Callers can pass `trace=[]` to record each attempt's raw reply, errors and seconds. Traces are never part of the spec.

**In-game runs (Minecraft + Iris/Bliss + the primary server + server 13 prep; 32 GB RAM, about 4–6 GB free)**
- **Run 1:**
  - Ollama's llama-server crashed while loading (`CUDA error: shared object initialization failed`, exit 0xc0000409).
  - On the next call it hit `std::bad_alloc` (prompt cache) and returned HTTP 500.
  - Both analysed faces failed cleanly, as "backend failed (no fallback)". Nothing was substituted and no retry was spent on a backend error.
  - nvidia-smi briefly lost contact with the driver. No display-driver reset was logged.
- **Run 2:**
  - Everything completed: 7.6–17.8 s per call, 14.7 s for a face that needed the repair retry.
  - Free RAM dropped by about 1 GB, to 3.3 GB. Total VRAM went from 1.3 to 7.7 GB of 8 GB.
  - After the 30 s keep-alive, the model unloaded and VRAM returned to 1.1 GB.
- **Limits of this measurement:**
  - The game window was in the background throughout, using about 7 % 3D load and 0.26–0.32 GB of dedicated VRAM.
  - **Frame rate during active play was not measured.** Do not read these numbers as "fits alongside Bliss".
- **Conclusion:**
  - Concurrent local inference on this 8 GB card is unreliable: in 1 of 2 runs the runner crashed.
  - Even when it works, the model's output is wrong on Red Rock and adds nothing on Harlo.
  - Keep `facade-analyzer: builtin-cv`.

## Alignment and glazing geometry (analyzer 0.6.1+, 2026-10-30)
**Alignment check (`_alignment`, builtin-cv, per image, before any analysis; reject when unsure)**
- The check rectifies a margin above the measured top, beyond a tolerance `align_tol_m` (1 m, for parapets and LiDAR).
- The image is rejected if any of these hold:
  - sky fills more than 35 % of the 1–2.5 m band below the measured top: the photographed roofline is lower than the shell;
  - the wall continues above the top with the same colour (dE < 6, less than 15 % sky): the photographed frontage is taller than the face;
  - a ground-touching opening is shorter than 1.8 m: the photo's scale does not match the face, so the door would be squeezed.
- Stepped faces (parts) skip the roofline test.
- Coverage of the face by the camera's view is handled by association (at least 40 % of the face width inside the field of view) and by the coverage gate. Cells outside the frame are unseen, never extrapolated.
- Self-test cases:
  - the lower 8 m of a 16 m frontage registered onto an 8 m face is rejected as "wall continues above";
  - a 17 m photo registered onto an 8 m face is rejected as "ground opening 0.8 m tall".

**Correction to an earlier claim.** The rc1 synthetic fixture was not a 16 m frontage squeezed into 6 m.
- It showed only the bottom 6 m of the synthetic design, but its drawing code wrapped negative indices, so its content was garbled.
- The fixture has been replaced by `fixture_true.py`, which draws the design in the face's own metres and warps it into the photo through the association quad. It is still synthetic and is reported separately from real imagery.

**Measured glazing geometry**
- builtin-cv now reports `openings`: connected glass regions as rectangles in face metres.
  - Gaps narrower than 0.5 m (storefront mullions) are bridged.
  - Irregular regions (fill below 0.6) are listed as unresolved, not placed.
- When a face has `openings`, the compiler places exactly those rectangles through a `rects` layout element (glass at the wall plane, layer priority above bands and columns). It no longer turns the glazing fraction into per-bay template windows.
- Result on the true-scale storefront fixture:
  - continuous glass extracted as u 3.0–24.0 × v 1.0–3.5 m (truth 3–24 × 0.6–3.6 m; v below 1.0 m is outside the associated quad);
  - wall colour exact;
  - the compiled plan holds one unbroken glass band.
- `tools/facade_spec_view.py <cache> <bid> <face> <out.png> [--world <region>]` shows, per face, the rectified photo (unseen cells grey), the extracted spec, the compiled plan and the world. Use it to see which stage a mismatch comes from.

**Local VLM, next step**
- qwen2.5vl:7b next to Minecraft is unsuccessful (CUDA/OOM crashes) and is no longer run in-game.
- Candidates for the next benchmark:
  - `granite3.2-vision:2b` (IBM, Apache-2.0, 2.5B Q4, about 2.4 GB);
  - qwen2.5vl:7b CPU-only (`ollama_num_gpu: 0`, no VRAM).
- Both are to be measured with active gameplay and frame times. Qwen2.5-VL-3B is excluded because its licence is for research only.

## Pretrained vision backends: tested configurations, paused (2026-10-31)
Two backends were evaluated **as configured** and failed the glazing bar on a locked holdout building. Neither is integrated.
- **Florence-2-large (MIT):** holdout glass recall 0.08. Its awning detection hit 4/4 (1 false positive).
- **Grounding DINO tiny + SAM vit-base (Apache-2.0):** holdout glass recall 0.24.

These are results for specific configurations, **not** a verdict on the model families. What was tested:
- zero-shot, one checkpoint size each, a few fixed prompts and thresholds;
- one prompt-to-region strategy;
- 5 tuning faces and 1 holdout building.

Not tested: tiling or per-bay prompts, point prompts for SAM, larger checkpoints, and fine-tuning (the assisted zone labels could become training data). Details: `reports/facade-v0.6/florence/DECISION.md`.

Model downloads and automatic-recognition tuning are paused. The current route to a recognisable facade is assisted zone labelling (`ASSISTED-ZONES.md`).
