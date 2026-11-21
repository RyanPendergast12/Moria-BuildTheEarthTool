# Facade asset provenance and licences (v0.6), owner: agent C

| file | what | how | licence |
|---|---|---|---|
| `prep/facade_assets/blocks-1.18.2.json` | 898 block ids → `{property: [values]}` plus `stateful` categories | `java -DbundlerMainClass=net.minecraft.data.Main -jar mojang_1.18.2.jar --reports`, run on a **copy** of `C:\BTE-Migration\12-egg-test\cache\mojang_1.18.2.jar` in the scratch dir `C:\BTE-Migration\facade\scratch\reports`, then `facade_palette_tools.py blocks`. The `stateful` lists are name rules (see the script). | Factual game data (ids and properties) derived from Mojang's own data generator. No Mojang code or assets are copied. |
| `prep/facade_assets/colours-1.18.2.json` | 895 blocks → mean sRGB, CIELAB (D65), alpha coverage, tinted flag | Our own computation (`facade_palette_tools.py colours`): the alpha-weighted mean of the first frame of each block's side-facing texture, found through blockstate → model chain. Source jar: the official 1.18.2 **client** jar, downloaded once to `C:\BTE-Migration\facade\scratch\client\1.18.2.jar` from Mojang piston-meta (sha1 `2e9a3e3107cca00d6bc9c97bf7d149cae163ef21` verified, 2026-10-29). | Only derived numbers are committed. **No textures are in the repo.** Textures © Mojang Studios. |
| palettes, components, styles | authored | Original work by agent C, from the BTE Detailing Guidebook rules (cited by page) and the accepted Harlo job. | `original (Moria)` |
| `sign-text` glyphs | 3x5 font | Original pixel design, labelled "simplified lettering". | `original (Moria)` |

**HueBlocks** (https://github.com/1280px/hueblocks), checked on 2026-10-29:
- The repo has **no licence file**; the GitHub API reports `license: null`.
- The README says its texture data is extracted live from Mojang jars, and that the rights belong to Mojang.
- With no licence, **nothing was copied from HueBlocks**: no data, colours or code. Only the idea of colour-nearest block matching was used, and the method above is our own.

**Review sheets** (`C:\BTE-Migration\facade\review\palettes\*.png`) render Mojang textures from the local jar. They stay **outside the repo** for review only.
