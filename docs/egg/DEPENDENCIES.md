# Egg (v0.5): external tools and dependencies

Checked on 2026-10-29 against official repositories and the jars already on this machine. **Nothing was installed on any server.**
Moria's fixed stack: Paper 1.18.2 #388, Java 17, WorldEdit 7.2.20 (plain, not FAWE). See AGENTS.md rule 7.

## Verdicts

| tool | role for Moria | verdict |
|---|---|---|
| SchematicBrushReborn | Placement **concepts** only | **Not installed. It never pastes.** Moria's planner reimplements the ideas inside its own ledgered writer. |
| WorldEdit 7.2.20 (Bukkit) | Already on every server. It could read `.schem` assets later. | It is not used by Egg at runtime. Egg assets are JSON block lists. |
| BTETerraRenderer | Optional client overlay for builders | It is a client mod and **not** a server dependency. |
| TerraPlusMinus 1.3.3 | Projection, terrain model, `customTrees.json` tree models | Already a dependency. Its tree data is MIT and is used in the catalog (see ASSETS.md). |

## SchematicBrushReborn (eldoriarpg)
- **Source:** https://github.com/eldoriarpg/SchematicBrushReborn. **Licence: AGPL-3.0** (GitHub licence API, `LICENSE.md`).
- **Releases and requirements** (from release notes and the build files at each tag):

| version | date | Java | server API / Minecraft | WorldEdit it was built against | fits our stack? |
|---|---|---|---|---|---|
| 2.4.3 | 2023-03-10 | — | `api-version 1.13`, `depend: WorldEdit` | 7.2.x | old, but plausible |
| 2.5.0 | 2023-04-07 | 17 | "Dropping support for versions older than 1.16.5" (uses library loading) | 7.2.x | yes (on paper) |
| **2.5.6** | 2024-01-08 | **17** (`sourceCompatibility 17`) | `api-version 1.16`; modules spigot / paper-legacy / paper | **WorldEdit 7.2.18**, FAWE 2.8.4 (both supported) | **Best match:** last release compiled against WE 7.2.x |
| 2.6.0–2.6.4 | 2024-01 to 2024-07 | 17 | `api-version 1.16` | WorldEdit **7.3.0** (compileOnly) | Probably loads, but compiled against the WE 7.3 API: **unverified** on 7.2.20 |
| 2.7.0–2.7.10 | 2024-07 to 2026-01 | **21** (toolchain 21) | 2.7.7 removes the spigot and legacy-paper modules | WE 7.3.x / FAWE 2.11+ | **No.** Java 21 class files do not load on Java 17. |
| 3.0.0–3.0.1 | 2026-10 | **25** | **Paper 1.21.11+ only** | **FAWE required**, plain WorldEdit not supported | **No** |

- **Schematic formats:** whatever WorldEdit's `ClipboardFormats` reads. On WE 7.2.20 that is Sponge `.schem` (v1/v2) and legacy MCEdit `.schematic` (see below). It picks schematics by name, directory or regex, and can save "presets" of schematic sets.
- **Concepts Moria adopts** (reimplemented in `prep/egg_vegetation.py` as plan ops, never as pastes):
  - weighted random choice from a set of models (`POOL` weights, made deterministic with `egg_common.det`);
  - rotation in 90° steps and flips (`rot_state` / `rot_xz` rotate positions **and** block-state properties);
  - an offset/anchor point (catalog `anchor` = trunk base, pasted at `base_y + 1`);
  - placement masks ("don't replace" = expect `air`, plus Moria's own protection, provenance and cover rules).
- **Why it is not installed:**
  - it pastes through WorldEdit edit sessions, which bypass Moria's protected writer and ledger (AGENTS rule 3);
  - only 2.5.x is plausibly compatible;
  - the AGPL licence would apply to any modification we distributed.

## WorldEdit 7.2.20 (Bukkit), as a possible asset reader
- **Local jar:** `07-shelltool-test-server/plugins/worldedit-bukkit-7.2.20.jar`. Its `plugin.yml` says `version "7.2.20+6667-f0f1e66"` and `api-version 1.13`, and the manifest says `WorldEdit-Version 7.2.20`, built 2024-02-29.
- **Licence:** GPL-3.0 (EngineHub/WorldEdit `LICENSE.txt`).
- **Formats** (`BuiltInClipboardFormat` in the jar):
  - `MCEDIT_SCHEMATIC` (`.schematic`, legacy numeric IDs);
  - `SPONGE_SCHEMATIC` (`.schem`, aliases `sponge`/`schem`). It is read by `SpongeSchematicReader`: NBT keys `Version`, `Palette`, `PaletteMax`, `BlockData`, `Offset`, with DataFixer upgrades from older data versions.
  - Sponge v3 is not in the 7.2 line. It was added in WorldEdit 7.3.
- **Use for Egg:** none at runtime.
  - If `.schem` tree assets are added later, the simplest safe route is offline. A small NBT reader in `egg_catalog_build.py` (Sponge v2: varint `BlockData` indexed into `Palette`) converts them into catalog JSON.
  - Placement then stays in Moria's writer.
  - WorldEdit's own `ClipboardFormats` API could also do this from the plugin, but it is not needed.

## BTETerraRenderer (tf2mandeokyi)
- **Source:** https://github.com/tf2mandeokyi/BTETerraRenderer. **Licence: Apache-2.0.**
- **Latest release:** v1.03.4 (2025-02-05). Builds for Forge 1.12.2 / 1.18.2 and Fabric 1.18.2 to 1.21.4.
- It is a **client-side** mod that renders map tiles (OSM, Bing and others) as a hologram for builders.
- It has no server component, so it is **not a server dependency**. Builders may use the 1.18.2 client build to compare Egg vegetation with imagery.

## Python (prep side)
- **No new packages.** Egg vegetation uses only what the venv already has: numpy, scipy, shapely, pyproj, laspy (via `ept_cached`), and the standard library (`urllib`, `zlib`, `struct`, `zipfile`).
- The WorldCover GeoTIFF is read with a ~40-line stdlib COG reader (HTTP range requests, DEFLATE tiles), so rasterio/GDAL is not needed.
