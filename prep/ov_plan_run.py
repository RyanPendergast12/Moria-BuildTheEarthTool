"""ov_plan_run.py <bridge_way_id> <cache_dir> <out.json>: plan one crossing from the local OSM index (no world access)."""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shapely import wkb
import ept_cached, overpass
from geo import Geo
IDX = r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite'
EPT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'
DATASETS = ['NV_ClarkCo_2_B22', 'NV_ClarkCo_1_B22', 'NV_ClarkCounty_B1_2018', 'NV_LasVegasValley_2010']
wid, cache, out = sys.argv[1:4]
con = sqlite3.connect(f'file:{IDX}?mode=ro', uri=True)
b = wkb.loads(con.execute('select wkb from feat where osm=?', (wid,)).fetchone()[0]).bounds
rows = con.execute('select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=?', (b[0] - .002, b[2] + .002, b[1] - .002, b[3] + .002)).fetchall()
feats = [dict(id=o, kind=k, tags=json.loads(t), geom=wkb.loads(g)) for o, k, t, g in rows]
def lidar(box):
    for n in DATASETS:
        r = ept_cached.fetch(EPT + n, box, os.path.join(cache, 'ept'), max_nodes=600)
        if r is not None and (r['cls'] == 17).sum() > 200: r['dataset'] = n; return r
    return None
geo = Geo(); geo.fwd([(b[0], b[1])])
cr = overpass.crossing_of(feats, wid); print('crossing', cr)
p = overpass.plan(feats, cr, geo, lidar, cache); geo.close()
json.dump(p, open(out, 'w'), indent=1)
print({k: p[k] for k in ('status', 'reasons', 'deck', 'clearance', 'piers', 'cells_planned')})
for a in p['approaches']: print(a)
for l in p['lower']: print({k: l.get(k) for k in ('id', 'status', 'surface_m', 'world_block_y', 'interpolated_span_m', 'fit_mismatch_m', 'reason')})
