# Facade v0.6 auto: evidence sources and association

Owner: R. Code: `prep/facade_sources.py`, `prep/facade_associate.py`, `tools/mock_imagery_server.py`. Contract: AUTO.md §4 and §5.

## 1. Providers (verified 2026-10-29)

| provider | access | licence / attribution | status in the pilot area |
|---|---|---|---|
| `mapillary` | Graph API `GET https://graph.mapillary.com/images?bbox=w,s,e,n&fields=...`, header `Authorization: OAuth <token>`. A client token is required. | CC BY-SA 4.0 (some datasets NC-SA). Derived data is allowed with attribution. Only the published API may be used; no scraping. | not yet measured (no token on this machine) |
| `panoramax` | Open STAC API `GET https://api.panoramax.xyz/api/search?bbox=...&limit=100`. No credential. | Per picture: `properties.license` (CC BY-SA 4.0, etalab-2.0, ...). Attribution: the `providers[].name` and the picture id. | 1 image in the pilot area |
| `local` | `<facade-refs-dir>/<bid>/refs.json` plus image files. | Whatever `refs.json` records (`licence`, `attribution`). | test and manual evidence |
| `osm` | The local OSM index (`facade_geometry.INDEX`), read only, no network. | ODbL, attribution "© OpenStreetMap contributors". | `building:levels` on 1 of 31 buildings |
| KartaView | API unreachable on 2026-10-29. Not supported: requesting it gives `unavailable: unknown provider`. | | |
| Google Street View / Maps | **Excluded.** Its terms forbid downloading and analysing imagery outside Google Maps. Never add it. | | |

### Mapillary details
- **Token.** Read from env `MAPILLARY_ACCESS_TOKEN`, else from `cfg.secrets_file` (`facade-secrets-file`, kept outside the repo).
  - The file is either JSON `{"mapillary_access_token": "..."}` or a `MAPILLARY_ACCESS_TOKEN=...` line.
  - The token is sent only in the header. It is never written to logs, the response cache or `evidence.json` (self-tested).
  - Logs show URLs without their query string.
  - With no token, the provider is `unavailable: no credential` and no request is made.
- **Fields requested:** `id, computed_geometry, geometry, computed_compass_angle, compass_angle, captured_at, camera_type, camera_parameters, is_pano, thumb_2048_url, creator, width, height`.
  - `computed_*` is preferred over the raw value.
  - `camera_parameters[0]` is the OpenSfM focal, normalised by max(width, height).
  - `camera_type` `spherical`/`equirectangular` (or `is_pano`) is treated as equirectangular. `fisheye` is carried but always rejected by association.
- **Bbox:** the footprint bbox plus 80 m, split into tiles no larger than `mapillary_max_bbox_deg2` (0.01 deg²). Results are de-duplicated by id.
  - Only the first page of each tile is read (`limit=500`). A building plus 80 m is about 1e-5 deg², so this is not a practical limit.
- **Image:** `thumb_2048_url` (long side 2048 px). This is a signed CDN URL that expires, so download it soon after fetch.

### Panoramax details
- `geometry.coordinates` gives the camera, `properties["view:azimuth"]` the heading and `properties.datetime` the capture time.
- `properties["pers:interior_orientation"]` gives `field_of_view` (360 means equirectangular, as does EXIF `Xmp.GPano.ProjectionType`) and `sensor_array_dimensions` [w, h].
- The image is `assets.sd.href` (2048 px wide), falling back to `assets.hd.href`.

### Limits and caching
- **Timeout:** `timeout_s` per provider (default 20 s). It is a deadline over all of that provider's requests.
- **Request budget:** `max_requests` per job (default 40), shared by all providers and counted only for real network calls.
- **Response cache:** `<cache>/facade/evidence/<provider>/<sha256(url+params)>.json`, with a TTL of `ttl_days` (default 30, by file mtime).
  - A fresh cache entry is served with no network call.
  - `refresh=True` (`--refresh`) bypasses the cache.
- **Images:** `<cache>/facade/evidence/<provider>/img/<sha(image_id)>.jpg`, verified with PIL before they are stored.
- **Failures:** each one gives the provider `status: "unavailable: <reason>"`. `fetch()` never raises for a provider failure. The reasons are:
  - `no credential`
  - `HTTP <code>`
  - `timeout`
  - `network error: <ExceptionType>`
  - `malformed response: ...`
  - `request budget exhausted`
  - `no refs dir configured`
  - `no refs.json for this building`
  - `OSM index missing`
  - `internal error <Type>`

## 2. `facade_sources` interface
```
fetch(bid, faces_world, cfg=None) -> evidence            # faces_world: geometry.json dict (preferred) or its faces list
download(cand, cfg=None) -> (path, None) | (None, reason)
localize(cands, geo) -> cands                             # adds mc, heading_mc, blocks_per_m
CLI: python prep/facade_sources.py <cache> <bid> [--refresh]   -> buildings/<bid>/evidence.json, one JSON status line
     python prep/facade_sources.py --selftest
```

**`cfg` keys and defaults**

| key | default | notes |
|---|---|---|
| `providers` | `"local,mapillary,panoramax,osm"` | |
| `timeout_s` | 20 | |
| `max_requests` | 40 | |
| `ttl_days` | 30 | |
| `pad_m` | 80 | |
| `mapillary_base` | `https://graph.mapillary.com` | Point it at the mock in tests. |
| `panoramax_base` | `https://api.panoramax.xyz/api` | Point it at the mock in tests. |
| `mapillary_max_bbox_deg2` | 0.01 | |
| `refs_dir` | | |
| `secrets_file` | | |
| `cache` | | |
| `refresh` | False | |
| `geo` | `geo.Geo()` | Any object with `fwd`/`inv`. The default `geo.Geo()` is started lazily and closed afterwards. |
| `members` | from the geometry | |
| `osm_tags` | | Overrides the index lookup. |

`cfg_from_env()` maps these env vars:
- `MORIA_FACADE_REFS` → `refs_dir`;
- `MORIA_FACADE_SECRETS` → `secrets_file`;
- `MORIA_FACADE_PROVIDERS` → `providers`;
- `MORIA_FACADE_MAX_REQUESTS` → `max_requests`;
- `MORIA_FACADE_PROVIDER_TIMEOUT` → `timeout_s`.

**`evidence.json` (`facade-evidence-1`)**
```
{ "schema": "facade-evidence-1", "bid", "geometry_sha", "providers_requested": [..],
  "key": sha256([bid, geometry_sha, sorted providers]),        # evidence cache key (AUTO.md §2); TTL is the caller's
  "created": "YYYY-MM-DDTHH:MM:SSZ", "bbox_lonlat": [w, s, e, n],
  "providers": { "<name>": {"status": "ok" | "unavailable: <reason>", "candidates": n, "requests": n, "cache_hits": n} },
  "osm": { "<member>": {"building", "building:colour", "building:material", "building:levels", "height", "min_height", "name", "roof:*"} } | null,
  "candidates": [ {
      "provider", "image_id", "captured_at": ISO-UTC | null,
      "lon", "lat", "heading_deg",                              # compass, degrees clockwise from true north (absent for refs given in MC)
      "mc": [x, z], "heading_mc": [dx, dz],                     # projected with TPM: heading is rotated ~11.4 deg near Las Vegas
      "blocks_per_m": 1.02,                                     # local TPM scale (blocks per metre, horizontal)
      "camera": {"type": "perspective" | "equirectangular" | "fisheye" | ..., "focal": f/max(w,h) | null,
                 "hfov_deg", "width", "height"},
      "url" | "path", "licence", "attribution" } ],
  "cost": {"usd": 0.0, "requests": n, "by_provider": {..}} }
```
**`refs.json` (local provider)**
```
{"images": [{"file": "a.jpg", "lon", "lat", "heading_deg" | "x", "z", "heading_mc": [dx, dz], "blocks_per_m"?,
             "camera_type": "perspective" | "equirectangular", "focal" | "hfov_deg", "width"?, "height"?,
             "captured_at", "licence", "attribution", "image_id"?}]}
```
- `file` is reduced to its basename, so it cannot point outside the refs dir.
- A missing width or height is read from the image file.

## 3. Association (`facade_associate`, AUTO.md §5)
```
associate(geometry, candidates, obstacles, cfg=None, now=None) -> association
obstacles_from_index(geometry, geo, pad_m=100) -> [{"id", "poly"}]   # non-member OSM buildings within ~100 m, MC shapely polygons
project(projection, x, y, z) -> (nx, ny) | None
CLI: python prep/facade_associate.py <cache> <bid>    -> buildings/<bid>/association.json
     python prep/facade_associate.py --selftest
```
`obstacles_from_index` raises when the index is missing or does not cover the area. A caller must not associate without knowing the occluders.

**Faces.** Only `edge == "exterior"` faces are judged. `shared` and `party` faces get `status: "skipped: <edge> wall"`.

**Rules.** A candidate is accepted only if every rule holds. All failed rules are listed in `reasons`. Horizontal MC deltas are divided by `blocks_per_m`.

| rule | test | default |
|---|---|---|
| camera model | perspective with focal, width and height, or equirectangular. Fisheye and unknown models are rejected. | |
| front | (camera − face centre) · mean normal > 0 | |
| distance | horizontal distance from the camera to the nearest point of the face polyline | 5–80 m |
| view angle | angle between the face normal and the ray from the face centre to the camera | ≤ 60° |
| FOV | share of face samples (every 0.5 m along u) inside the horizontal FOV. A panorama uses a 120° virtual crop centred on the face-centre bearing. | ≥ 40 % |
| occlusion | 2D rays from the camera to the face centre and the u = ¼ and ¾ points, trimmed 0.3 m short of the wall. A ray must not cross any neighbour footprint or our own footprint (union of parts). | |
| wrong building | (perspective only) the principal ray, out to 80 m, must not reach a neighbour footprint before our face or footprint | |
| date | `captured_at` known and not older than `max_age_years` | 8 |

**Ranking.**
- `assoc_score = fov_frac · cos(view_angle) · dist_f · age_f`, where:
  - `dist_f` is 1 for 8–40 m, 0.75 under 8 m, and falls to 0.5 at 80 m;
  - `age_f = 1 − 0.5 · age / max_age`.
- At most `max_images_per_face` (4) are accepted, best first. The rest are rejected with the reason `over per-face limit (4)`.

**`association.json` (`facade-association-1`)**
```
{ "schema": "facade-association-1", "bid", "geometry_sha", "params": {..defaults..},
  "faces": { "<face_id>": {
     "status": "observed-candidates" | "no-evidence" | "skipped: shared wall" | "skipped: party wall",
     "accepted": [ {"provider", "image_id", "captured_at", "attribution", "dist_m", "view_angle_deg", "fov_frac", "age_years",
                    "assoc_score", "url" | "path", "projection": {..below..}} ],
     "rejected": [ {"provider", "image_id", "captured_at", "attribution", <metrics computed so far>, "reasons": ["..."]} ] } } }
```
The `evidence[]` entries of the autospec (AUTO.md §3) are these accepted entries, minus `projection`, `url` and `path`.

### 3.1 `projection`: what analysis (V) needs to rectify a face
```
"projection": {
  "model": "perspective" | "equirectangular",
  "camera": {"mc": [x, z], "y": eye Y (world), "heading_mc": [dx, dz], "blocks_per_m", "pitch_deg": 0.0, "roll_deg": 0.0},
  "image": {"width", "height"},                 # of the ORIGINAL image as reported by the provider
  "focal": f / max(width, height) | null,       # perspective only (OpenSfM normalisation)
  "yaw_window_deg": [a, b],                     # equirectangular only: crop, degrees relative to the image centre column (+ = right)
  "crop_nx": [0.5 + a/360, 0.5 + b/360],        # same window as normalised columns; values outside 0..1 wrap modulo 1
  "quads": [ { "seg": k, "u0", "u1", "y0", "y1",
               "corners_world": [[x, y, z] x4],
               "corners_norm":  [[nx, ny] | null x4] } ] }   # one quad per face segment
```
**Corners.**
- Corner order is (u0, bottom), (u1, bottom), (u1, top), (u0, top): bottom-left, bottom-right, top-right, top-left as seen from outside.
- `y0 = base_y + 1` is the ground surface, the bottom of wall row v = 1. `y1 = top_y + 1` is the top of the cap block.
- A world height Y maps to block row `v = floor(Y) − base_y`.

**Normalised coordinates.**
- `corners_norm` = (column / width, row / height), with y pointing down.
- They are resolution-independent: multiply by the size of the image you actually downloaded. A Mapillary 2048 thumbnail or a Panoramax `sd` image keeps the aspect ratio.
- Values outside 0..1 are normal (corners outside the frame, e.g. a wide face seen from close up). The homography is still valid.
- `null` means the corner is behind a perspective camera.

**Rectification.**
- For each quad, the homography from the four `corners_norm` to the rectangle `[u0, u1] × [y0, y1]` rectifies that wall segment. A face with several segments (bends < 30°, arcs) has one quad per segment.
- For a panorama, either sample the equirectangular image inside `yaw_window_deg`, or render a virtual perspective view with `project()`. Panorama corners are given as equirectangular columns and rows (`nx = 0.5 + yaw/360`, `ny = 0.5 − pitch/180`), and a quad can straddle the seam (wrap nx).
- `project(projection, x, y, z)` gives the same mapping for any world point. Use it for per-row or per-column sampling instead of a homography if you prefer.

**Camera assumptions (known ceilings).**
- The camera is level (pitch = roll = 0).
- The eye is `camera_height_m` (2.5 m, a car mount) above the face's ground level.
- Mapillary's `computed_rotation` and altitude are not used yet. A tilted camera therefore shifts or skews the corners, so V should refine the corners against image edges, or treat a vertical offset of a few percent as normal.
- For panoramas, the provider heading is assumed to be the direction of the image centre column. This is assumed, not yet verified against a real pilot panorama. Check it on the first one.

## 4. Mock server and self-tests
- `python tools/mock_imagery_server.py <fixture_dir> [--port 8765] [--mode ok|outage|timeout|malformed|wrong-building]`
  - Mimics Mapillary `/images` (401 without an `OAuth` header), Panoramax `/api/search`, and `/img/<file>` downloads.
  - `GET /__mode?m=<mode>` switches the mode.
  - For in-process use: `start(fixture_dir, mode)` returns a server with `.url`, `.set_mode()`, `.hits` and `.shutdown()`.
  - `facade_sources.selftest_fixture(dir, geo)` writes a fixture. Its `wrong-building` set puts cameras in front of a neighbouring building.
- `python prep/facade_sources.py --selftest` runs these checks:
  - no token;
  - ok;
  - compass→MC heading under an 11.4° rotation and 1.02 scale;
  - cache hit with zero network calls during an outage;
  - outage (503);
  - timeout;
  - malformed response;
  - request budget;
  - bbox split and de-duplication;
  - Panoramax parsing;
  - KartaView unavailable;
  - connection refused;
  - local refs and a path escape;
  - OSM attributes;
  - image download, cache and outage;
  - the token never appears in logs, evidence or cache.
- `python prep/facade_associate.py --selftest` runs these checks:
  - rotated faces (5 angles; identical image corners);
  - a camera behind the face;
  - too far and too close;
  - a grazing view;
  - a face outside the FOV;
  - occlusion, plus a non-blocking neighbour;
  - a wrong-building camera;
  - old and undated images;
  - a fisheye camera;
  - the panorama yaw window;
  - the per-face limit;
  - a party face skipped;
  - end to end through the mock (wrong-building mode accepts nothing; ok mode accepts both).
