# Egg (v0.5) source inventory

One shared list of sources. Each spec (TERRAIN-, VEGETATION- and AGRICULTURE-SPEC) adds the details it relies on: dates, resolution, units, licence and limitations. **Never commit the PDFs, rasters, caches or worlds listed here.**

## Local
| source | location (not in git) | used for | notes |
|---|---|---|---|
| BTE Terraforming guide (PDF) | local copy (not distributed) | terrain rules | Cite page numbers. Old WorldEdit commands and numeric IDs are translated into bounded plan ops, never run. |
| BTE agriculture Fields guidebook (PDF) | local copy (not distributed) | agriculture rules | as above |
| Geofabrik Nevada extract | `shelltool\osm-extract\nevada-latest.osm.pbf` + `.provenance.json` | OSM landuse, natural, trees, water | Extract date Sun, 25 Oct 2026 01:01:43 GMT. ODbL, © OpenStreetMap contributors. |
| Local OSM index (protection) | `shelltool\osm-extract\clark_osm_index.sqlite` | roads, buildings, `area:highway` | Coverage box (−115.45, 35.93, −114.90, 36.40). Outside it, Egg reports `no_coverage`. |
| Approved baseline world | `summerlin\pilot\baseline-world-after-generation` | provenance of "untouched terrain" | The pilot world as TerraPlusMinus generated it, before any build: 338 regions. Read-only. |
| TerraPlusMinus 1.3.3 | `downloads\terraplusminus\terraplusminus-1.3.3.jar` | projection, terrain model, `customTrees.json` tree assets | Licence must be checked before redistributing any asset (VEGETATION-SPEC). |
| Vertical check | `summerlin\pilot\terrain_vs_lidar_offline.json` | datum and offset evidence | World minus lidar ground: median −1.05, mean −0.96, std 1.70 m. TPM places the ground top at floor(height). |

## Online (bounded, cached, provenance recorded)
| source | product / version | resolution | role |
|---|---|---|---|
| USGS 3DEP lidar (EPT, AWS) | NV_ClarkCo_2_B22 (2022), plus older Clark County datasets | point cloud; classes 1, 2, 7, 17, 18, 20 present | Ground (class 2) for terrain. Class 1 includes unclassified vegetation, so canopy evidence needs return analysis. |
| USGS 3DEP DEM | 1 m / 1/3″ via The National Map | 1 m / ~10 m | Bare-earth alternative where lidar is missing. See the "lidar vs DEM" FAQ in TERRAIN-SPEC. |
| ESA WorldCover | v200, 2021 | 10 m | Broad landcover context only. S3 tile `N36W117` is reachable. Licence CC BY 4.0. |
| USDA NASS Cropland Data Layer | yearly | 30 m | Supporting crop evidence only. |
| OSM wiki: farmland, orchard, vineyard, meadow | — | — | Tag semantics |
| BTETerraRenderer | client mod | — | An optional overlay for builders. **Not** a server dependency. |
| SchematicBrushReborn | server plugin | — | Placement concepts. Not installed unless DEPENDENCIES.md shows it is compatible with 1.18.2 / Java 17 / WE 7.2.20. It is never a data source. |
