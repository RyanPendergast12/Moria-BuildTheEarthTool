# Moria BuildTheEarth Tool

A Paper server plugin for [BuildTheEarth](https://buildtheearth.net) builders. Throw a snowball at a block, and the tool generates real-world content **for that one chunk** from open data:

- **Building shells**: outer walls and roofs at true height. Footprints come from OpenStreetMap; heights come from USGS 3DEP LiDAR.
- **Roads**: surface, lane markings, curbs and sidewalks, built from OSM road geometry. They follow the BTE Roads Guidebook methods.
- Or **both** at once.

The tool lays down accurate shells and roads, and builders then do the detailing by hand. It is not meant to replace builders.

> **Status: alpha (v0.3.0).** It works and is tested on the Las Vegas server it was built for: Clark County, Nevada, Paper 1.18.2 and TerraPlusMinus.
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
| `/shelltool content shells\|roads\|both` | Choose what a throw generates (default: shells). |
| `/shelltool preview` | A throw prepares data and reports what it would build. Nothing is placed. |
| `/shelltool on` / `off` | Build mode on or off. It is always off after login or restart. |
| `/shelltool prepare [radius]` | Pre-download and plan nearby chunks (radius ≤ 3). |
| `/shelltool prefetch on\|off` | Prepare chunks in the background as you walk. |
| `/shelltool status` | Mode, jobs, queue and coverage at your location. |
| `/shelltool cancel` | Cancel your running and queued work. |
| `/shelltool undo [job] [confirm]` | Dry run, then restore. |

**Permissions:**
- `summerlin.shelltool.use` goes to the UUIDs in `config.yml` → `allowed-players`.
- `summerlin.shelltool.admin` defaults to op.

## Repository layout
| path | contents |
|---|---|
| `plugin/` | Paper plugin source (`ShellToolPlugin.java`, `plugin.yml`, default `config.yml`) |
| `prep/` | Python data preparation. `shelltool_prep.py` is called by the plugin. `osm_index_build.py` builds the local OSM index. `projcheck/GeoServer.java` bridges to TerraPlusMinus. |
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
