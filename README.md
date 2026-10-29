# Moria BuildTheEarth Tool

A Paper server plugin for [BuildTheEarth](https://buildtheearth.net) builders. Throw a snowball at a block, and the tool generates real-world content **for that one chunk** from open data:

- **Building shells**: outer walls and roofs at true height. Footprints come from OpenStreetMap; heights come from USGS 3DEP LiDAR.
- **Roads**: surface, lane markings, curbs and sidewalks, built from OSM road geometry. They follow the BTE Roads Guidebook methods.
- Or **both** at once.

The tool lays down accurate shells and roads, and builders then do the detailing by hand. It is not meant to replace builders.

> **Current version: v0.4.3 (alpha): automatic overpasses.** Download `SummerlinShellTool-0.4.3.jar` from [Releases](https://github.com/RyanPendergast12/Moria-BuildTheEarthTool/releases/tag/v0.4.3). It was approved in-game on 2026-10-29; see [docs/TESTS-v0.4.3.md](docs/TESTS-v0.4.3.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).
> It is tested on the Las Vegas server it was built for: Clark County, Nevada, Paper 1.18.2 and TerraPlusMinus. That includes in-game tests of the snowball workflow and the connected overpass.
> Several paths and region settings are still hard-coded for that setup. Running it for another city needs the portability work in [docs/ROADMAP.md](docs/ROADMAP.md).
> [docs/SETUP.md](docs/SETUP.md) tells you exactly what to change in the meantime.

## Why a server plugin, not a mod or modpack
- **Players need nothing installed.** BTE building happens on servers. A plugin runs only on the server, so any builder who joins with a normal client (or the BTE modpack) can use it.
- **It fits the BTE stack.** Modern BTE servers are Paper with Terra+- (TerraPlusMinus). This tool reuses TerraPlusMinus's own projection and terrain code, so generated blocks land exactly where the world's terrain is.
- **One install covers a whole team.** Permissions, undo history and data caching live in one place.
- **Singleplayer still works.** Builders who want to play alone can run a local Paper server on their own PC. The setup guide covers this.

A client mod (Forge or Fabric) would need a separate rewrite for each loader and Minecraft version, and every player would have to install it. That isn't worth it unless there's demand for offline singleplayer worlds.

## How it works
```
snowball hits block ──► plugin (Java, server thread: checks, placement, undo ledger)
                           │  chunk cx,cz
                           ▼
                      prep worker (Python, background process)
                        ├─ OSM features   : local SQLite index (fast) → Overpass API fallback
                        ├─ building height: USGS 3DEP LiDAR (EPT tiles, disk-cached)
                        ├─ projection + terrain: TerraPlusMinus (one JVM per prep)
                        └─ plan → cache/chunks/<cx>_<cz>.json
                           │
                           ▼
                      plugin places blocks (≤ 8 ms or 400 blocks per tick), records before/after
```

### Safety rules
- **Shells:** shells are placed **into air only**.
- **Roads:** roads replace only untouched generator terrain at the surface. Player-placed blocks, vegetation, block entities and entities are never touched.
- **Undo:** every block is recorded in an undo ledger. `/shelltool undo <job> confirm` restores the exact previous state and keeps anything changed since.
- **Protected zones:** these are refused before any data is fetched. In Las Vegas this is the Strip.
- **Reach:** the tool never writes outside the chunk you hit.

## Commands
| command | effect |
|---|---|
| `/shelltool content shells\|roads\|both` | **shells**: building shells. **roads**: roads, plus enabled overpasses. **both**: shells and the full road pipeline. The reply shows mode and content. |
| `/shelltool preview` | A throw prepares data and reports what it would build. Nothing is placed. |
| `/shelltool on` / `off` | Build mode on or off. **Activation is OFF after every login and restart.** The selected content is remembered until the server restarts (it then resets to shells). |
| `/shelltool prepare [radius]` | Pre-download and plan nearby chunks (radius ≤ 3). |
| `/shelltool prefetch on\|off` | Prepare chunks in the background as you walk. |
| `/shelltool status` | Mode, jobs, queue and coverage at your location. |
| `/shelltool cancel` | Cancel your running and queued work. |
| `/shelltool undo [job] [confirm]` | Dry run, then restore. |
| `/shelltool content overpass` | **Specialist/debug mode.** Builds ONLY eligible crossings; no ordinary roads or shells. For normal building use `roads` or `both`, which already include enabled crossings. |

**Permissions:**
- `summerlin.shelltool.use` goes to the UUIDs in `config.yml` → `allowed-players`.
- `summerlin.shelltool.admin` defaults to op.

## Overpasses (automatic mode; off by default)
With `overpass-enabled: true` and `overpass-mode: auto`, a `roads` or `both` snowball automatically discovers every road bridge in the map data near the chunk. Each one is measured and checked; eligible ones are built (deck plus approaches), the rest are skipped with a specific reason. There is no per-bridge list to maintain.

- `overpass-mode: allowlist` restricts generation to `overpass-crossings`.
- `overpass-exclude` blocks individual crossings in either mode.

### How a bridge is built
- **Deck:** placed **into air only**. `deck Y = world lower-road Y + LiDAR-measured separation` (deck top, USGS class 17, minus lower road surface, class 2). This is a vertical adjustment to fit the world, not an absolute elevation. The lower road and its terrain are never changed.
- **Inferred, provisional parts:** deck thickness (2 blocks), parapets and abutments. Piers stay **disabled** (`overpass-piers: false`) until their layout has evidence. No support is placed in a lower carriageway.
- **Approaches** (`overpass-approach-grade`, 0 = off; allowed 0.04–0.10; tested at 0.06):
  - The required height spreads from each deck end along the connected roads, dropping at most the grade per block until it meets the ground.
  - Intersections become raised level areas, and their legs ramp down.
  - Existing roads are buried under fill, not removed. Walls and barriers go on raised edges, and enclosed medians are filled.
- **Planned as a whole:** a crossing's every block (deck plus approaches) is planned once, cached, and clipped to the thrown chunk. Results don't depend on throw order.
- **Re-planning a built crossing:** the crossing's own unchanged blocks are upgraded in place. Blocks of an earlier plan that are no longer in the plan are retired to their exact before-state (undoable). Changed blocks and player edits are never touched.
- **Whole-crossing protection:** before any block of a crossing is placed, the plugin checks **all** of its blocks in all its chunks. Any protected build anywhere means the whole crossing is skipped and the conflicting block is named, rather than built with a hole. The per-chunk check still guards every write.

### Eligibility (each failed check is a specific skip reason in the job report)
| check | rule |
|---|---|
| data | Enough bridge-deck LiDAR; ≤ 5 % of deck columns unmeasured; the lower road is measured on both sides of the deck (agreeing within 0.5 m); inside the local OSM index |
| geometry | One connected deck. Measured upper/lower separation 4–15 m. Deck length 0.6–1.4 × the OSM bridge. Deck width ≤ 80 m per carriageway |
| clearance | ≥ `overpass-min-clearance` air blocks (default 4) over every lower carriageway, across its full width |
| approach | Approaches reach the ground without raising a fixed road: the crossing's **lower** roads (the accepted baseline) and every other motorway/trunk mainline. A freeway that is itself the upper road may ramp. With approaches off, every deck end must already meet its road. |
| overlap | No other bridge crosses this bridge (stacked), and no neighbouring crossing's footprint (deck plus raised approaches) overlaps this one |
| strip | The footprint stays outside the Strip exclusion (+500 blocks) |
| protected builds | The plugin's whole-crossing preflight (above) |

### Supported bridge types
Simple **road-over-road grade separations**: one connected deck (single or twin carriageways, mapped bridge sidewalks) over at-grade roads. Supported cases include:
- arterials over freeways;
- streets over parkways;
- a freeway over a street;
- `layer=2` bridges where nothing else is stacked beneath.

### Not supported (skipped with a reason)
- Stacked or multi-level interchanges.
- Decks that are several separate structures.
- Bridges whose lower road is hidden or unmapped: washes, rail, paths, or no ground returns on one side.
- Bridges outside LiDAR or local-index coverage.
- Neighbouring crossings whose footprints overlap (no combined planning yet).
- Tunnels.

### Limitations
- Approach steps follow whole blocks: a 6 % design grade is locally up to about 8 % where rounding puts two steps close together.
- A ramp ends where the terrain model meets the approach. Where the world surface is one block lower than that model, the road drops 2 blocks. One known case: Desert Inn at -12160205, -5448042.
- Retirement covers chunks that still contain the crossing's current plan. A leftover in a chunk the new plan no longer touches is not retired.
- Bridge-crossing driveways and frontage connections follow the network spread, not hand-designed geometry.
- The existing ground-level road paint under a deck is left as it was.
- Survey of Summerlin (16 crossings): 9 eligible, 7 skipped with reasons. See `docs/OVERPASS-SURVEY.md`.

- **Road details (v0.4.1, roadstyle-v2):** one marking and sidewalk model serves ground roads and decks. Each surface gets its own explicit elevation: freeway markings stay on the freeway, deck markings on the deck.
  - Palette: the builders' Las Vegas Blvd frontage at Bellagio, plus BTE Roads Guidebook p.63–64.
  - Lines: one-way roads get a white right edge line and a yellow left edge line. Turn lanes are separated by solid lines. Bike lanes come from `cycleway:*`.
  - Sidewalks: mapped sidewalks, including `bridge=yes` sidewalks on decks. No sidewalks are invented on freeways.
  - Dash phase continues across chained ways, so lines don't break at road-to-bridge joins.
- **Independent pipelines:** a failure in shells, roads or overpass preparation is reported in its own section and never suppresses the others.
- **Controlled detail upgrades:** a block the tool generated may be replaced only if it is still exactly as the ledger recorded it. Player edits, containers, tracked edits and entities are preserved. Each upgrade records `upgrade_of`. Undoing the original job is refused while a later upgrade of its blocks exists; undoing the upgrade restores the previous generated surface exactly.
- **Cache invalidation:** chunk plans from another preparation pipeline are re-prepared automatically. Downloaded data (OSM index, LiDAR tiles) and the accepted crossing geometry are kept.
Design notes and measurements:
- `prep/overpass.py` (deck)
- `prep/approaches.py` (raised approaches)
- `prep/crossings.py` (automatic discovery, eligibility, whole-crossing plans)
- `prep/roadgen.py` (road model and per-chunk generation)
- `prep/ov_survey_auto.py` (eligibility survey over a candidate list)
- `prep/ov_approach_plan.py` (read-only approach study)
- `prep/ov_preview.py` (preview renderer)

Deployment:
- `tools/deploy_test_server.py`: test servers only; refuses the pilot and main servers.
- `tools/deploy_pilot.py`: the pilot only. It backs up the plugin data and the world first, and refuses while the server runs.

## Repository layout
| path | contents |
|---|---|
| `plugin/` | Paper plugin source (`ShellToolPlugin.java`, `plugin.yml`, default `config.yml`) |
| `prep/` | Python data preparation. `shelltool_prep.py` is called by the plugin. `overpass.py` and `approaches.py` plan crossings. `osm_index_build.py` builds the local OSM index. `projcheck/GeoServer.java` bridges to TerraPlusMinus. |
| `tools/` | Deployment scripts: test servers, and the pilot with backups |
| `CHANGELOG.md` | Version history |
| `docs/SETUP.md` | Installation, step by step |
| `docs/ROADMAP.md` | What's needed to make it work for any region |
| `docs/SCAFFOLD-SPEC.md` | Rules for building shells |
| `docs/ROAD-IMPLEMENTATION-SPEC.md` | Road rules, mapped to the BTE Roads Guidebook |

## Data sources and attribution
- **OpenStreetMap:** © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Builds made with this data should credit OSM.
- **USGS 3DEP LiDAR:** public domain (US only).
- **TerraPlusMinus:** used at runtime for the BTE projection and terrain. Not bundled. See its own license.
- **BTE Roads Guidebook** (by Brigart): its methods are referenced by page number in the road spec. The guide is not included.

## License
No license has been chosen yet, so all rights are reserved by default. Add a `LICENSE` file (MIT or GPL-3.0 are common for Minecraft plugins) before inviting contributions.
