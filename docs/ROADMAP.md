# Roadmap: from "works in Las Vegas" to "works for any BTE team"

## Done in v0.4.x
- [x] Grade-separated crossings: a measured deck fitted to the world, raised approaches, chunk-safe planning (v0.4.0–v0.4.2).
- [x] One snowball workflow: roads/both include enabled crossings; independent preparation pipelines (v0.4.1).

## v0.5 – portable (no source edits needed)
- [ ] One config for everything: move every hard-coded path into `config.yml`, and have the plugin pass them to the prep worker. These are:
  - the OSM index;
  - the protected-zone polygons;
  - the data file;
  - the JDK;
  - the TerraPlusMinus JAR;
  - the caches.
- [ ] Optional protected zones: a list of named polygons (or none) in config, instead of a mandatory `strip`.
- [ ] LiDAR datasets chosen automatically: look up the 3DEP datasets covering a chunk from the USGS EPT boundary index, instead of a hard-coded Clark County list.
- [ ] Region-neutral naming: rename the package and permissions from `summerlin.*` to `moria.*`, keeping aliases for one version.
- [ ] Gradle build plus GitHub Actions that publish the JAR to Releases. People should download it, not compile it.
- [ ] `requirements.txt` and a one-click `setup.bat` / `setup.sh`.
- [ ] Use the permissions plugin when one is present (LuckPerms nodes), alongside `allowed-players`.

## v0.6 – easy install
- [ ] Remove the Python dependency: port preparation to Java inside the plugin. This is the biggest barrier for other server owners.
  - Shapely becomes JTS. pyproj and TerraPlusMinus are already Java.
  - LAZ decoding needs a Java reader.
- [ ] Newer Minecraft: build for the Paper version current BTE servers run (1.20+), alongside 1.18.2.
- [ ] Publish on Modrinth and Hangar as a **plugin**. Server owners install it; players need nothing.

## Later
- [ ] Heights outside the US: building heights from OSM `height` / `building:levels` tags, and other national LiDAR or DSM sources.
- [ ] More content types: trees from OSM `natural=tree`, landuse ground textures, parking lots.
- [ ] Builder workflow: claim a chunk or area, give review status, and export a progress map.

## Not planned
- **A client mod or modpack.** The server plugin reaches every builder with no install. See README "Why a server plugin".
