"""facade_identify.py - block (x, z) -> building identity for the Facade tool (v0.6, docs/facade/ARCHITECTURE.md 1-3).
    identify(x, z, packages_dir=..., geo=None) -> {bid, members, tags, package_id|None, parts, hit, distance_m}
The building containing the block centre wins (smallest if nested); otherwise the nearest within 3 m. A package claims a building
when its packages/<id>/package.json (or .yaml) lists the id in building.osm; then bid = that list's first id and members = the
list. Missing or invalid package files are skipped (never fatal). bid None = no mapped building (not 'empty land')."""
import glob, json, os, sys
from shapely.geometry import Point, box
from shapely.ops import transform
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egg_common, facade_geometry as fg

PACKAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'packages')
NEAR_M = 3.0


def package_claims(packages_dir=PACKAGES):
    """{osm id: (package id, [members])} from building.osm only; unreadable packages are ignored"""
    out = {}
    for p in sorted(glob.glob(os.path.join(packages_dir, '*', 'package.json')) + glob.glob(os.path.join(packages_dir, '*', 'package.yaml'))):
        try:
            if p.endswith('.json'): d = json.load(open(p, encoding='utf-8'))
            else:
                import yaml; d = yaml.safe_load(open(p, encoding='utf-8'))
            ids = [str(i) for i in d['building']['osm'] if isinstance(i, str) and i[:1] in 'wr' and i[1:].isdigit()]
            pid = str((d.get('package') or {}).get('id') or os.path.basename(os.path.dirname(p)))
        except Exception: continue
        for i in ids: out.setdefault(i, (pid, ids))
    return out


def identify(x, z, packages_dir=PACKAGES, geo=None):
    from pyproj import Transformer
    from geo import Geo
    own = geo is None; geo = geo or Geo()
    try:
        lon, lat = geo.inv([(x + 0.5, z + 0.5)])[0]
    finally:
        if own: geo.close()
    d = 0.0002                                                                 # ~20 m search box
    feats, meta = egg_common.query_index(fg.INDEX, (lon - d, lat - d, lon + d, lat + d), kinds=('building',))
    if feats is None: return dict(bid=None, members=[], tags={}, package_id=None, parts=[], hit=None, distance_m=None, reason=meta)
    to_utm = Transformer.from_crs(4326, 32611, always_xy=True).transform
    P = Point(to_utm(lon, lat)); best = None
    for f in feats:
        G = transform(to_utm, f['geom']); dist = 0.0 if G.covers(P) else G.distance(P)
        key = (dist, G.area if dist == 0 else 0, f['id'])
        if dist <= NEAR_M and (best is None or key < best[0]): best = (key, f)
    if best is None: return dict(bid=None, members=[], tags={}, package_id=None, parts=[], hit=None, distance_m=None, reason='no building within 3 m')
    f = best[1]; claim = package_claims(packages_dir).get(f['id'])
    members = claim[1] if claim else [f['id']]
    F = fg.osm_feats(members)
    parts = sorted({p['id'] for m in members if m in F for p in fg.osm_parts_within(F[m]['geom'])})
    return dict(bid=members[0], members=members, tags=f['tags'], package_id=claim[0] if claim else None, parts=parts, hit=f['id'],
                distance_m=round(best[0][0], 2))


if __name__ == '__main__':
    import tempfile
    from geo import Geo
    g = Geo()
    r = identify(-12159724, -5450625, geo=g)                                 # inside Harlo (negative coordinates)
    assert r['bid'] == 'w348127357' and r['distance_m'] == 0 and r['tags'].get('name', '').startswith('Harlo'), r
    hx, hz = g.fwd([(-115.3332927, 36.152827)])[0]                           # Harlo SW corner: outside by < 3 m -> still Harlo
    r = identify(int(hx) - 2, int(hz) + 1, geo=g); assert r['bid'] == 'w348127357' and 0 < r['distance_m'] <= 3, r
    r = identify(-12159724, -5450700, geo=g); print('open ground:', r['bid'], r.get('reason'))
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, 'harlo')); os.makedirs(os.path.join(td, 'broken'))
        json.dump({'package': {'id': 'harlo-w348127357'}, 'building': {'osm': ['w348127357', 'w348127354']}}, open(os.path.join(td, 'harlo', 'package.json'), 'w'))
        open(os.path.join(td, 'broken', 'package.json'), 'w').write('{not json')
        r = identify(-12159724, -5450625, packages_dir=td, geo=g)
        assert r['package_id'] == 'harlo-w348127357' and r['members'] == ['w348127357', 'w348127354'] and r['bid'] == 'w348127357', r
    assert identify(-12159724, -5450625, packages_dir=r'C:\nonexistent\dir', geo=g)['package_id'] is None
    g.close(); print('facade_identify self-check ok')
