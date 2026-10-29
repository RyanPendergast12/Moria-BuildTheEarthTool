# Egg (v0.5) vegetation assets: attribution and licences

Catalog: `prep/egg_assets/vegetation_catalog.json` (`catalog_version: veg-1`, Minecraft 1.18.2 block states). It is built by `prep/egg_catalog_build.py`. Each entry records `provenance` (`tpm-asset` or `authored`), `source`, `licence` and `attribution`.

## TerraPlusMinus tree models (16 entries, `tpm-*`)
- **Source:** `assets/terraplusminus/data/customTrees.json` (203,742 bytes, dated 2023-06-01) inside `terraplusminus-1.3.3.jar`. It is read in place and read-only. Upstream history: "feat(trees): custom tree generation" (2023-04-13).
- **Licence:** **MIT.** Copyright (c) 2022-2026 Build the Earth Germany. https://github.com/BTE-Germany/TerraPlusMinus (`LICENSE`, verified through the GitHub licence API on 2026-10-29).
- **Required attribution** (MIT: keep the copyright and permission notice with copies or substantial portions):
  > Tree models from TerraPlusMinus, Copyright (c) 2022-2026 Build the Earth Germany, MIT License.
- **Conversion** (not a verbatim copy):
  - Bukkit `Material` names become full 1.18.2 states. Leaves get `persistent=true`, logs and wood get `axis=y`. Fence, wall and fence-gate properties are computed inside each model.
  - `*_SAPLING` (twig detail in the spruce models) becomes the matching persistent leaves, because saplings tick, grow and pop off.
  - Duplicate cells are resolved as TPM does (the last one wins). Each model is re-anchored on its single `y=0` trunk block (`source_origin_offset` records the shift).
- **Original format:** `{"trees": {species: {size s|m|l: {name: {"spread": n, "blocks": [{x,y,z,material}]}}}}}`.
  - TPM places `y=0` at ground+1 and rotates by `Random.nextInt(4) * 90°`, as seen in the `TreePopulator.generateCustomTree` bytecode.
  - It uses `setType` without block states.
- **Honest labels:** these are generic shapes (oak, birch, savanna, spruce), not Las Vegas species. The catalog labels them "generic broadleaf (TPM oak model)" and similar. Birch and spruce are limited to the `mountain` habitat.

## Moria hand-authored procedural models (29 entries, `lv-*`)
- **Source:** written for Moria in `prep/egg_catalog_build.py` as deterministic geometry functions. **Licence:** the Moria repository's licence. Attribution: Moria contributors. **Open:** the repository has no `LICENSE` file yet, so the coordinator must choose one. The TPM MIT notice above has to ship with the catalog either way.
- **Species:**
  - Mexican fan palm (*Washingtonia robusta*) and date palm (*Phoenix dactylifera*);
  - mesquite (*Prosopis*), palo verde (*Parkinsonia*) and desert willow (*Chilopsis linearis*);
  - Aleppo pine (*Pinus halepensis*), Mondell pine (*P. eldarica*) and Italian cypress (*Cupressus sempervirens*);
  - olive (*Olea europaea*) and a generic orchard tree;
  - creosote bush (*Larrea tridentata*), white bursage (*Ambrosia dumosa*, dead-bush stand-in) and Mojave yucca (*Yucca schidigera*);
  - Joshua tree (*Yucca brevifolia*, only where mapped);
  - ground cover (dead bush, grass).
- No third-party models, textures or schematics are included.

## Data sources used by the generator (not redistributed)
| source | licence | attribution |
|---|---|---|
| OpenStreetMap (Geofabrik Nevada extract, 2026-10-25) | ODbL 1.0 | © OpenStreetMap contributors |
| ESA WorldCover 10 m 2021 v200 | CC BY 4.0 | © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium |
| USGS 3DEP lidar (EPT `NV_ClarkCo_2_B22` and older Clark County sets) | Public domain (US Government) | Courtesy U.S. Geological Survey 3D Elevation Program |

Raster and point caches stay outside git: `<cache>/egg/worldcover/`, `<cache>/ept/`.
