"""ov_survey_auto.py <candidates.json> <cache_dir> <out.json> [grade] [min_clear]: run automatic-mode discovery + eligibility
(crossings.py, exactly as the prep does) for every bridge in a candidate list; eligible crossings also get their whole-crossing
cell plan. Read-only on worlds; writes only cache files and the report."""
import json, os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shapely import wkb
from shapely.geometry import Polygon
import ept_cached, overpass, crossings
from geo import Geo
IDX = r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite'
EPT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'
DATASETS = [('NV_ClarkCo_2_B22', 2022), ('NV_ClarkCo_1_B22', 2022), ('NV_ClarkCounty_B1_2018', 2018), ('NV_LasVegasValley_2010', 2010)]
cand, CACHE, OUT = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
G = float(sys.argv[4]) if len(sys.argv) > 4 else 0.06; MC = int(sys.argv[5]) if len(sys.argv) > 5 else 4
con = sqlite3.connect(f'file:{IDX}?mode=ro', uri=True)
def index_query(b, pad, kind=None):
    q = 'select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=?'
    args = [b[0] - pad, b[2] + pad, b[1] - pad, b[3] + pad]
    if kind: q += ' and f.kind=?'; args.append(kind)
    return [dict(id=o, kind=k, tags=json.loads(t), geom=wkb.loads(g)) for o, k, t, g in con.execute(q, args).fetchall()]
def lidar_fetch(box):
    for n, y in DATASETS:
        r = ept_cached.fetch(EPT + n, box, os.path.join(CACHE, 'ept'), max_nodes=600)
        if r is not None and (r['cls'] == 17).sum() > 200: r['dataset'] = f'{n} ({y})'; return r
    return None
geo = Geo(); geo.fwd([(-115.33, 36.16)])
strip = Polygon(json.load(open(r'C:\BTE-Migration\summerlin\pilot\polygons.json'))['strip_mc']).buffer(500)
env = dict(geo=geo, lidar_fetch=lidar_fetch, cache=CACHE, piers=False, agrade=G, min_clear=MC, strip=strip, index_around=lambda b, p: index_query(b, p, 'highway'))
for d in ('crossings', 'ept'): os.makedirs(os.path.join(CACHE, d), exist_ok=True)
res, seen = [], set()
for c in cand:
    fx = index_query(wkb.loads(con.execute('select wkb from feat where osm=?', (c['id'],)).fetchone()[0]).bounds, 0.002, 'highway')
    cr = overpass.crossing_of(fx, c['id'])
    if not cr or cr['key'] in seen: continue
    seen.add(cr['key']); t0 = time.time()
    try:
        pl = overpass.plan(fx, cr, geo, lidar_fetch, CACHE, piers=False, approach_grade=G)
        reasons = crossings.eligibility(cr, pl, fx, env)
        rec = dict(key=cr['key'], name=c['name'], hw=c['hw'], crosses=c['crosses'], mid_mc=c['mid_mc'], upper=cr['upper'], lower=cr['lower'], plan_status=pl['status'],
                   eligible=not reasons, reasons=reasons, clearance=[(k['min_air_blocks'], k['measured_top_to_top_m']) for k in pl.get('clearance', [])],
                   approach_legs=len((pl.get('approach') or {}).get('legs', [])), approach_conflicts=(pl.get('approach') or {}).get('n_conflicts'))
        if not reasons:
            cc = crossings.crossing_cells(cr, pl, env, lambda b, p: index_query(b, p))
            rec.update(cells=cc.get('n'), chunks=len(cc.get('chunks', [])), failure=cc.get('failure'))
    except Exception as ex:
        rec = dict(key=cr['key'], name=c['name'], eligible=False, reasons=[f'error: {type(ex).__name__}: {ex}'])
    rec['seconds'] = round(time.time() - t0, 1); res.append(rec)
    print(('ELIGIBLE ' if rec['eligible'] else 'skipped  ') + rec['key'][:34].ljust(34), (rec.get('name') or '')[:24].ljust(24), '|', rec.get('cells', ''), '|', '; '.join(rec['reasons'])[:150], flush=True)
geo.close(); json.dump(res, open(OUT, 'w'), indent=1)
print(sum(r['eligible'] for r in res), 'eligible of', len(res))
