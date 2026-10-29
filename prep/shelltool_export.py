"""shelltool_export.py <out_file>
Exports the accepted Summerlin shell design (pilot/plan_v1: building roles only, never roads) plus the feature status
table and the pilot / Strip(+500) polygons into the plain-text data file read by the SummerlinShellTool plugin.
The design is the complete per-building shell (computed from whole footprints by summerlin_plan.py), so a chunk's
portion is just the subset of those blocks inside that chunk: no seams, no boundary walls."""
import collections, hashlib, json, os, sys
from shapely.geometry import Polygon

B = r'C:\BTE-Migration\summerlin\pilot'
ROLES = {'wall', 'roof', 'roof-edge', 'part-outline', 'unresolved-outline'}
P = json.load(open(B + r'\polygons.json'))
pilot = Polygon(P['pilot_mc']); strip = Polygon(P['strip_mc']).buffer(500)          # same buffer as summerlin_plan.py G2
rows = [r for r in map(json.loads, open(B + r'\plan_v1\plan.jsonl')) if r['role'] in ROLES]
feats = {f['feature']: f for f in json.load(open(B + r'\plan_v1\features.json')) if f['kind'] == 'building'}
bbox = json.load(open(B + r'\feature_bboxes.json'))
total = collections.Counter(r['feature'] for r in rows)
missing = [k for k in feats if k not in bbox]                   # skipped/edge buildings: bbox from their projected footprint
if missing:
    import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import proj
    from shapely.geometry import shape; from shapely.ops import transform; from pyproj import Transformer
    ll = Transformer.from_crs(32611, 4326, always_xy=True).transform
    meas = {m['osm_id']: m for m in json.load(open(B + '/buildings_measured.json'))}
    for k in missing:
        if k not in meas: continue
        xz = proj.fwd(list(transform(ll, shape(meas[k]['footprint_utm'])).exterior.coords))
        bbox[k] = [int(min(p[0] for p in xz)), int(min(p[1] for p in xz)), int(max(p[0] for p in xz)), int(max(p[1] for p in xz))]
pal = sorted({r['new'] for r in rows}); fids = sorted(feats)
with open(sys.argv[1], 'w', encoding='utf-8', newline='\n') as f:
    f.write('# SummerlinShellTool data v1 - source plan_v1 sha256 %s\n' % hashlib.sha256(open(B + r'\plan_v1\plan.jsonl', 'rb').read()).hexdigest())
    for name, poly in (('pilot', pilot), ('strip', strip)):
        f.write('POLY %s %s\n' % (name, ' '.join('%.3f %.3f' % c for c in poly.exterior.coords)))
    for i, b in enumerate(pal): f.write('P %d %s\n' % (i, b))
    for i, k in enumerate(fids):
        ft = feats[k]; bb = bbox.get(k, [0, 0, -1, -1])
        f.write('F %d %s\t%s\t%s\t%d\t%d %d %d %d\t%s\n' % (i, k, ft['status'].replace('\t', ' '), ft.get('height_source', ''), total[k],
                                                          bb[0], bb[1], bb[2], bb[3], (ft.get('name') or '').replace('\t', ' ')))
    pi = {b: i for i, b in enumerate(pal)}; fi = {k: i for i, k in enumerate(fids)}
    for r in sorted(rows, key=lambda r: (r['x'] >> 4, r['z'] >> 4, r['y'], r['z'], r['x'])):
        f.write('B %d %d %d %d %d\n' % (r['x'], r['y'], r['z'], pi[r['new']], fi[r['feature']]))
    # accepted pilot ROAD cells (plan_v1): protected from the road writer
    nroad = 0
    for r in map(json.loads, open(B + r'\plan_v1\plan.jsonl')):
        if r['role'] in ('carriageway', 'sidewalk', 'crossing'): f.write('R %d %d %d\n' % (r['x'], r['y'], r['z'])); nroad += 1
    # local OSM index coverage box in block space (for /shelltool status)
    import sqlite3; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from geo import Geo
    C = json.loads(sqlite3.connect(r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite').execute("select v from meta where k='coverage_lonlat_box'").fetchone()[0])
    edge = [(C[0] + (C[2] - C[0]) * t / 20, C[1]) for t in range(21)] + [(C[2], C[1] + (C[3] - C[1]) * t / 20) for t in range(21)] + \
           [(C[2] - (C[2] - C[0]) * t / 20, C[3]) for t in range(21)] + [(C[0], C[3] - (C[3] - C[1]) * t / 20) for t in range(21)]
    g = Geo(); f.write('POLY index_coverage %s\n' % ' '.join('%.1f %.1f' % c for c in g.fwd(edge))); g.close()
print(nroad, 'protected pilot road cells;',len(rows), 'shell blocks,', len(fids), 'buildings,', len(pal), 'block states,',
      len({(r['x'] >> 4, r['z'] >> 4) for r in rows}), 'chunks ->', sys.argv[1])
