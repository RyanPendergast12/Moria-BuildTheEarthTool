"""osm_index_build.py <extract.osm.pbf> <provenance.json> <out.sqlite> [w,s,e,n]
Builds the persistent local OSM index used by shelltool_prep.py: buildings, building:parts, highways (incl. mapped
footways/sidewalks/crossings), area:highway surfaces, inside the Las Vegas valley coverage box. SQLite + R*Tree.
Records the extract source/date/hash and the coverage box in table meta. Outside coverage -> prep falls back to Overpass."""
import json, sqlite3, sys, time, os
import osmium
from shapely import wkb as swkb
from shapely.geometry import LineString, shape

PBF, PROV, OUT = sys.argv[1:4]
COVER = tuple(map(float, sys.argv[4].split(","))) if len(sys.argv) > 4 else (-115.45, 35.93, -114.90, 36.40)   # lon/lat box (default: Las Vegas valley)
fab = osmium.geom.WKBFactory()
tmp = OUT + '.building'
if os.path.exists(tmp): os.remove(tmp)
db = sqlite3.connect(tmp)
db.executescript('''create table feat(id integer primary key, osm text, kind text, tags text, wkb blob);
create virtual table feat_idx using rtree(id, minx, maxx, miny, maxy);
create table meta(k text primary key, v text);''')
def inside(b): return not (b[2] < COVER[0] or b[0] > COVER[2] or b[3] < COVER[1] or b[1] > COVER[3])
rows = []; n = {'building': 0, 'building_part': 0, 'highway': 0, 'area_highway': 0}
def add(osm, kind, tags, g):
    b = g.bounds
    if not inside(b): return
    rows.append((osm, kind, json.dumps(tags, separators=(',', ':')), g.wkb, b)); n[kind] += 1
    if len(rows) >= 20000: flush()
def flush():
    cur = db.cursor()
    for osm, kind, tags, w, b in rows:
        cur.execute('insert into feat(osm,kind,tags,wkb) values(?,?,?,?)', (osm, kind, tags, w))
        cur.execute('insert into feat_idx values(?,?,?,?,?)', (cur.lastrowid, b[0], b[2], b[1], b[3]))
    db.commit(); rows.clear()

class H(osmium.SimpleHandler):
    def way(self, w):
        t = {k: v for k, v in w.tags}
        if 'highway' in t:
            try: add('w%d' % w.id, 'highway', t, swkb.loads(fab.create_linestring(w), hex=True))
            except Exception: pass
    def area(self, a):
        t = {k: v for k, v in a.tags}
        kind = 'building' if t.get('building', 'no') != 'no' else 'building_part' if 'building:part' in t else 'area_highway' if 'area:highway' in t else None
        if kind is None: return
        try: g = swkb.loads(fab.create_multipolygon(a), hex=True)
        except Exception: return
        osm = ('w%d' if a.from_way() else 'r%d') % a.orig_id()
        if g.geom_type == 'MultiPolygon' and len(g.geoms) == 1: g = g.geoms[0]
        add(osm, kind, t, g)
t0 = time.time()
H().apply_file(PBF, locations=True, idx='flex_mem')
flush()
prov = json.load(open(PROV))
meta = dict(source=prov['source'], extract_last_modified=prov['last_modified'], extract_sha256=prov['sha256'], downloaded=prov['downloaded'],
            coverage_lonlat_box=list(COVER), built=time.strftime('%Y-%m-%dT%H:%M:%S'), counts=n, build_seconds=round(time.time() - t0),
            note='Features whose bounds intersect the coverage box. Inside it, absence of a feature = not mapped in OSM at the extract date.')
for k, v in meta.items(): db.execute('insert into meta values(?,?)', (k, json.dumps(v)))
db.execute('create index feat_osm on feat(osm)'); db.commit(); db.close()
os.replace(tmp, OUT)
print(json.dumps(meta, indent=1))
