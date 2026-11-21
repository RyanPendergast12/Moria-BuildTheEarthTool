"""facade_spec_view.py <cache> <bid> <face_id> <out.png> [--world <region_dir>] [--spec <autospec.json>] [--plan <plan.json>] [--scale <px per m>]
One sheet per face, to see WHERE a photo-to-build mismatch happens:
  1 rectified evidence photo (face metres, from the accepted association)  2 extracted spec (zones, glazing rectangles, entrances)
  3 compiled plan (outermost op per face cell, by block colour; red frame = observed/inferred ops, grey = style)
  4 (with --world) the blocks in the world now, same projection.
Panels share one scale: 1 face cell = 1 m. Local diagnostic; reads only the cache (and the world region files)."""
import glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', 'prep'))
import numpy as np
from PIL import Image, ImageDraw
import facade_analyze as V

IMGS = 3; S = 12                                                    # px per metre in the sheet
COL = json.load(open(os.path.join(HERE, '..', 'prep', 'facade_assets', 'colours-1.18.2.json')))['blocks']
def rgb_of(b): return tuple(COL.get((b or 'minecraft:air').split('[')[0].split(':')[-1], [255, 0, 255])[:3])


def panel(W, H, title):
    im = Image.new('RGB', (int(W * S) + 2, int(H * S) + 22), 'white'); d = ImageDraw.Draw(im); d.text((2, 2), title, fill='black'); return im, d


def main(cache, bid, fid, out, world=None, spec_path=None, plan_path=None):
    bd = os.path.join(cache, 'facade', 'buildings', bid)
    g = json.load(open(os.path.join(bd, 'geometry.json'))); fg = next(f for f in g['faces'] if f['id'] == fid)
    meta = V._face_meta(fg); U, H = meta['u_extent_m'][1], meta['v_extent_m'][1]
    if not spec_path: idx = json.load(open(os.path.join(bd, 'autospec-index.json'))); spec_path = os.path.join(bd, f'autospec-{max(idx.values())}.json')
    spec = json.load(open(spec_path))
    f = spec['faces'].get(fid) or {}
    panels = []
    # 1 rectified photo
    # 1 rectified photo, 3 m above the measured top included (red line = measured top): shows whether the roofline matches
    EXT = 3.0
    acc = ((json.load(open(os.path.join(bd, 'association.json'))).get('faces') or {}).get(fid) or {}).get('accepted') or []
    for a in [a for a in acc if a.get('pose_verified') or a.get('provider') in V.VERIFIED_POSE_PROVIDERS][:IMGS]:      # images the analysis used
        p1, d1 = panel(U, H + EXT, f'1 evidence (rectified, +{EXT:.0f} m above the measured top) - face {f.get("status")}')
        path = a.get('path')
        if not path:                                          # Mapillary: the job's cached download (facade_sources.download)
            import facade_sources as R
            path = os.path.join(cache, 'facade', 'evidence', a['provider'], 'img', R._sha(a['image_id'])[:32] + '.jpg')
        if not os.path.exists(path): d1.text((4, 40), 'image not cached', fill='red'); panels.append(p1); continue
        img = np.asarray(Image.open(path).convert('RGB')).astype(np.float32) / 255
        mx = dict(meta, v_extent_m=[0.0, H + EXT])
        for pj in V.projections_of(a, fg, (img.shape[1], img.shape[0]))[0]:
            rgb, inside = V.rectify(img, pj, mx, S, 1, 2)
            q = (np.flip(np.clip(rgb, 0, 1), 0) * 255).astype(np.uint8); m = np.flip(inside, 0)
            base = np.asarray(p1)[22:22 + q.shape[0], 1:1 + q.shape[1]].copy(); base[m] = q[m]; base[~m] = (205, 205, 205)
            p1.paste(Image.fromarray(base), (1, 22))
        d1.line([0, 22 + int(EXT * S), int(U * S), 22 + int(EXT * S)], fill=(255, 0, 0), width=2)
        d1.text((4, 12), f"{a.get('provider')}:{a.get('image_id')} {a.get('captured_at', '')[:10]} dist {a.get('dist_m')} m angle {a.get('view_angle_deg')} deg", fill='black')
        panels.append(p1)
    # 2 extracted spec
    p2, d2 = panel(U, H, '2 extracted spec (zones fill, glazing = blue boxes, entrances = red)')
    y = lambda v: 22 + int((H - v) * S)
    for z in f.get('zones') or []: d2.rectangle([int(z['u0_m'] * S), y(z['v1_m']), int(z['u1_m'] * S), y(z['v0_m'])], fill=tuple(z['rgb']))
    for z in f.get('patches') or []: d2.rectangle([int(z['u0_m'] * S), y(z['v1_m']), int(z['u1_m'] * S), y(z['v0_m'])], fill=tuple(z['rgb']), outline=(255, 0, 255), width=2)
    for o in f.get('openings') or []: d2.rectangle([int(o['u0_m'] * S), y(o['v1_m']), int(o['u1_m'] * S), y(o['v0_m'])], outline=(0, 60, 255), width=3)
    for c_ in f.get('canopies') or []: d2.line([int(c_['u0_m'] * S), y(c_['v_m']), int(c_['u1_m'] * S), y(c_['v_m'])], fill=(255, 128, 0), width=5)
    for e in f.get('entrances') or []: d2.rectangle([int(e['u0_m'] * S), y(2.4), int(e['u1_m'] * S), y(0)], outline=(220, 0, 0), width=3)
    gz = f.get('glazing'); d2.text((4, 12), ('ASSISTED (person-aligned/drawn) ' if f.get('assisted') else '') + f"patches magenta, canopy orange, glazing frac {gz and gz['fraction']} bays {f.get('bays') and f['bays']['pitch_m']} floors {f.get('floors') and f['floors']['height_m']}", fill='black')
    panels.append(p2)
    # 3 compiled plan, 4 world: outermost block per (u, v) of the face columns
    plan = json.load(open(plan_path or max(glob.glob(os.path.join(bd, 'plan-*.json')), key=os.path.getmtime)))
    n = fg['segments'][0]['normal']; cols = {(int(c[0]), int(c[1])): u for u, c in enumerate(fg['columns'])}
    def ucol(x, z):
        for k in (0, 1, -1, 2, -2):
            q = (x - round(k * n[0]), z - round(k * n[1]))
            if q in cols: return cols[q], k
        return None, None
    best = {}
    for o in plan['ops']:
        u, k = ucol(o[0], o[2])
        if u is None: continue
        key = (u, o[1] - int(fg['base_y']))
        if key not in best or k > best[key][0]: best[key] = (k, o[4], o[8])
    p3, d3 = panel(U, H, '3 compiled plan (red frame = observed/reference/inferred, none = STYLE)')
    for (u, v), (_, b, ev) in best.items():
        if 0 <= v < H:
            d3.rectangle([u * S, y(v + 1), (u + 1) * S - 1, y(v) - 1], fill=rgb_of(b), outline=(220, 0, 0) if ev in ('observed', 'reference', 'inferred') else None)
    panels.append(p3)
    if world:
        sys.path.insert(0, r'C:\BTE-Migration\scripts'); from anvil_tools import World
        Wd = World(world); p4, d4 = panel(U, H, '4 world now')
        for u, c in enumerate(fg['columns']):
            for v in range(int(H)):
                for k in (1, 0, -1):
                    b = Wd.block(int(c[0] + round(k * n[0])), int(fg['base_y']) + v, int(c[1] + round(k * n[1])))
                    if b and 'air' not in b: d4.rectangle([u * S, y(v + 1), (u + 1) * S - 1, y(v) - 1], fill=rgb_of(b)); break
        panels.append(p4)
    sheet = Image.new('RGB', (max(p.width for p in panels), sum(p.height + 6 for p in panels)), 'white'); yy = 0
    for p in panels: sheet.paste(p, (0, yy)); yy += p.height + 6
    sheet.save(out); print(out)


if __name__ == '__main__':
    a = sys.argv; opt = lambda k: a[a.index(k) + 1] if k in a else None
    if opt('--scale'): S = int(opt('--scale'))
    main(a[1], a[2], a[3], a[4], opt('--world'), opt('--spec'), opt('--plan'))
