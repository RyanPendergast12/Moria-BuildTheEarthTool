# Assisted zone labelling (prototype, analyzer 0.6.7)

A person draws zones on one already-aligned photo of one face. The normal facade pipeline then builds them on the measured shell, using the same protected writer, ledger, cache, cancellation and exact undo as every other job. Everything it produces is labelled **ASSISTED** (evidence class `reference`), never automatic.

## Data: `<facade-refs-dir>/<bid>/assist.json`

Each `images[]` entry already holds the face corners (`corners_px`) and the occluder rectangles (`mask_px`). Zones are added as `labels`, in image pixels:

```json
"labels": [
  {"kind": "glazing",  "poly_px": [[512,1004],[652,1004],[652,1190],[512,1191]]},
  {"kind": "material", "role": "wall", "poly_px": [[652,1003],[800,1003],[800,1189],[652,1190]]},
  {"kind": "awning",   "depth": 2, "poly_px": [[480,1040],[797,1040],[797,1056],[480,1056]]}
]
```

- `role` is one of wall, base, band, parapet or trim.
- `depth` is the number of blocks an awning projects (1–6).
- `block` (material zones, optional), e.g. `minecraft:smooth_sandstone`: the person's choice, used instead of the photo colour. It must be a valid 1.18.2 block allowed for the role; otherwise the colour match is used and a note says why. Set it in the labelling page's **block** box.
- The labels are part of the association hash, so editing them invalidates the cached spec and plan.

## How the labels are mapped

`facade_analyze.apply_labels`:
- The inverse of the corner homography converts each polygon into face metres.
- Glazing and material polygons become the 1 m cells whose centres lie inside the polygon, merged into rectangles.
- An awning keeps the polygon's extent along the face and its underside height.
- Colours are the median of the photo pixels inside the polygon, with occluders excluded. The face's white-balance correction is applied if the analyzer found a colour cast.

What each kind replaces:
- **Glazing** replaces detected openings and is placed as glass rectangles.
- **Material** becomes `patches`. Each patch is a rectangle with its own block, matched to the sampled colour.
- **Awning** becomes `canopies`, built from the `canopy-flat-dN` component.

Placement rules:
- Glass is laid out before patches, so glass wins where the two overlap.
- Shell geometry is never changed. A zone narrower than one shell column after snapping is reported and not placed.
- Labels are applied only when the face itself passed the analyzer's alignment checks.

## Shortest steps

1. **Label.** Run:
   ```
   python tools\facade_label.py C:\BTE-Migration\facade\assist w558884282 p0:f2 C:\BTE-Migration\shelltool\test-cache-v06
   ```
   - A browser page opens.
   - Choose a kind, click the corners of the zone, then press Enter.
   - Click a zone to select it, drag its points to adjust them, or press Delete to remove it.
   - Press Save. The previous `assist.json` is kept as `assist.json.bak`.
2. **Preview** (writes nothing to the world):
   - Run `python prep\facade_prep.py <cache> auto <bid>` with `MORIA_FACADE_REFS_DIR` set to the assist folder.
   - Then run `python tools\facade_spec_view.py <cache> <bid> <face> out.png --spec <autospec> --plan <plan> --scale 40`. Use the `autospec` and `plan` paths that prep prints.
   - Or, in game: `/facadetool preview whole`.
3. **Apply in game:** `/facadetool preview whole`, then `/facadetool apply whole confirm`. Remove it with `/facadetool undo <job> confirm`, which restores exactly.

## Limits of the prototype

- One photo per face.
- No sign or lettering kind.
- Placement resolution is the shell's columns (about 1 m).
- Colours sampled in shadow come out dark; choose the block for that zone instead.
- No in-browser block preview; previewing uses the spec viewer or `/facadetool preview`.
