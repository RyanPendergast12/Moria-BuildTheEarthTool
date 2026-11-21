# Facade v0.6 (revised): on-demand automatic facades

Contract for the redirected v0.6. It supersedes the "packages are required" parts of ARCHITECTURE.md. Everything there about the writer, ownership, adoption, locks, chunk edges, components, retirement, undo and restart still holds unchanged.

## 1. Player flow
1. Target a building.
2. `/facadetool preview` (no selection needed; `auto` is the default selection).
3. Off-thread prep runs and reports progress in chat: resolving → fetching evidence → analyzing → compiling → ready for preview.
4. Preview.
5. `apply` / `apply whole confirm`.

A manually authored package, when one exists for the building, is an optional override (`/facadetool select <package>`).

## 2. Pipeline (prep, Python, never on the server thread)
`facade_prep.py <cache> auto <bid> [--fallback procedural|none] [--refresh]` runs these stages. It writes progress to `<cache>/facade/buildings/<bid>/auto-progress.json`, as `{"stage","detail","t"}`, rewritten on each stage. It prints ONE JSON status line, like `plan`.

| stage | module | output |
|---|---|---|
| resolve | existing `facade_identify` + `facade_geometry` | geometry.json (faces, facade-local u/v, normals, measured heights) |
| fetch evidence | `facade_sources.py` | `evidence.json`: OSM attributes; LiDAR/shell measurements; image candidates with provider, id, capture date, camera position, heading and intrinsics, licence and attribution |
| associate | `facade_associate.py` | per face, accepted or rejected images, with reasons and scores |
| analyze | `facade_analyze.py` (backend interface) | `autospec-<n>.json` (§3); fresh analysis is versioned, never overwritten |
| materials | `facade_materials.py` | palette per face (roles → 1.18.2 blocks) by colour matching |
| compile | existing `facade_compile` via `autospec_to_package()` | `facade-plan-1` plan (unchanged contract) |

**Caching**
- Evidence is keyed by bid + geometry key + provider set, with a TTL (default 30 days).
- An autospec is keyed by evidence sha + analyzer id/version. The accepted autospec is the compile input, so the same accepted spec compiles to identical ops.
- `--refresh` re-fetches and re-analyses.

**Failure**
- A provider outage, timeout or quota error marks that provider's evidence `unavailable` and never aborts the job by itself.
- With no usable evidence, the faces are `procedural` (when the fallback is allowed) or `unresolved`.
- No world change happens before preview/apply, so a failure can never leave half-written blocks.

## 3. `facade-autospec-1` (untrusted analysis output, schema-validated)
```
{ "schema": "facade-autospec-1", "bid", "members", "geometry_key", "created",
  "analyzer": {"backend", "version", "model": null|str},
  "mode": "evidence" | "procedural" | "mixed",
  "cost": {"usd": 0.0, "requests": 0, "by_provider": {}},
  "faces": { "<face_id>": {
      "status": "observed" | "procedural" | "unresolved",
      "evidence": [{"provider", "image_id", "captured_at", "dist_m", "view_angle_deg", "assoc_score", "attribution"}],
      "confidence": 0..1,
      "zones":    [{"v0_m", "v1_m", "u0_m", "u1_m", "role": "wall|base|band|parapet|trim", "rgb": [r,g,b], "conf"}],
      "glazing":  {"fraction", "rgb", "conf"} | null,
      "floors":   {"first_m", "height_m", "count", "conf"} | null,
      "bays":     {"pitch_m", "width_m", "offset_m", "conf"} | null,
      "entrances":[{"u0_m", "u1_m", "conf"}],
      "canopies": [{"u0_m", "u1_m", "v_m", "depth", "conf"}],
      "unresolved": ["<what could not be determined>"] } } }
```
**Validation**
- Coordinates must lie within the face's measured u/v extent.
- Rgb values are 0..255, confidences 0..1, and counts are capped.
- Unknown keys are rejected.
- No field can move, stretch or resize the measured shell.

**Evidence classes on ops**
- `observed` (image-derived);
- `measured` (LiDAR/shell);
- `inferred` (from observed rhythm, e.g. bays extended across an occluded span);
- `style` (procedural fallback).

Reports and chat say "observed" only for `observed` ops.

## 4. Sources (verified 2026-10-29)
- **Mapillary Graph API**
  - Needs a client token. Imagery is CC BY-SA 4.0 (some datasets NC-SA). Derived data is allowed with attribution. Only the published API may be used, no scraping.
  - The token is read from env `MAPILLARY_ACCESS_TOKEN` or from `facade-secrets-file` (outside the repo). It is never logged; URLs are redacted in logs.
- **Panoramax** (open STAC API, CC BY-SA): 1 image in the pilot area. Supported for completeness.
- **KartaView:** the API was unreachable (2026-10-29), so it is not supported.
- **Local reference files:** `<facade-refs-dir>/<bid>/*.jpg` plus `refs.json` (camera pose, date, licence). Used for testing and as a manual evidence source.
- **Google Street View / Maps imagery:** not used. Its terms forbid downloading and analysing it outside Google Maps.
- **OSM tags:** `building:colour`, `building:material`, `building:levels`, `roof:*`. They are rare in the pilot area (1 of 31 buildings has levels).

**Limits:** per-provider timeout (default 20 s), max requests per job (default 40), max images analysed per face (default 4), bounded concurrent jobs (prep queue).

## 5. Association (reject when unsure)
An image is accepted for a face only if all of these hold:
- the camera is in front of the face (positive normal side);
- the distance is 5–80 m;
- the view angle to the face normal is ≤ 60°;
- the face's projected u-extent lies at least 40% inside the image FOV (perspective intrinsics, or an equirectangular crop for panoramas);
- the 2D ray from the camera to the face centre and quarter points does not cross another OSM footprint first (occlusion);
- the capture date is not older than `facade-max-image-age-years` (default 8).

Every rejection keeps its reason. An ambiguous or wrong-building image is rejected, never used.

## 6. Analysis backends (`facade_analyze.py`)
Interface: `analyze(face_crop_rgb, face_meta) -> face section of the autospec`.
- **`builtin-cv`** (default, local, free, deterministic, numpy + PIL):
  - rectifies the facade from the camera pose and face geometry;
  - masks sky (top region, blue/bright), vegetation (excess green), deep shadow and specular highlights;
  - clusters wall colours in Lab space within the facade mask only;
  - detects glass by dark or low-saturation reflective regions;
  - finds floor bands from row-profile periodicity and bays from column-profile periodicity.
- **`ollama`** (optional; only if the user installs it): a local VLM that may return only the JSON schema above. Off by default.
- **`anthropic`** (optional; needs a key): disabled unless configured with `facade-ai-max-usd-per-job` and `facade-ai-max-usd-per-day`. The estimated cost is checked before each call and the actual cost is recorded. It never switches on automatically.

Model output is parsed as data, validated, and its text is never executed.

## 7. Materials
`facade_materials.py`:
- Lab nearest match against `prep/facade_assets/colours-1.18.2.json`, restricted by role (`wall`, `glass`, `frame`, `trim`, `base`) to the allowed block lists in the palette library;
- texture-variance penalties;
- coherent zones (one block per zone, optional 2-block dither for large walls).

HueBlocks has no licence, so the lookup is original.

## 8. Modes and config
**Modes**
- **evidence:** every resolved face is observed.
- **procedural:** no observations; a style fills the faces, labelled `STYLE-GENERATED (not observed)`.
- **mixed:** observed faces plus labelled procedural or inferred ones.

**Config keys**
- `facade-auto-enabled`
- `facade-fallback` (`procedural` | `none`, default `procedural`)
- `facade-fallback-style` (default `summerlin-stucco-retail`)
- `facade-providers` (default `local,mapillary,panoramax,osm`)
- `facade-analyzer` (default `builtin-cv`)
- `facade-secrets-file`, `facade-refs-dir`
- `facade-provider-timeout-seconds`, `facade-max-requests-per-job`
- `facade-ai-max-usd-per-job`, `facade-ai-max-usd-per-day`

## 9. File ownership (redirect)
- **R (sources/association):** `prep/facade_sources.py`, `prep/facade_associate.py`, `tools/mock_imagery_server.py`, `docs/facade/SOURCES.md`.
- **V (analysis/materials):** `prep/facade_analyze.py`, `prep/facade_materials.py`, `docs/facade/ANALYSIS.md`.
- **P (spec/compiler/prep/plugin):** `prep/facade_autospec.py`, the `auto` command in `prep/facade_prep.py`, compiler changes in `prep/facade_compile.py`, `FacadeTool.java`, `config.yml`, `COMMANDS.md`.
- **Coordinator:** this file, the harness, server 13, integration.
