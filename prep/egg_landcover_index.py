"""egg_landcover_index.py [extract.osm.pbf] [provenance.json] [out.sqlite] [w,s,e,n]
Builds the Egg landcover index (same SQLite+RTree schema as osm_index_build.py: feat / feat_idx / meta) for vegetation
and agriculture. Kinds:
  tree      node natural=tree (point)
  tree_row  way natural=tree_row (line)
  landuse   areas with landuse=*
  natural   areas natural=wood/scrub/grassland/heath/sand/bare_rock/wetland/water/...
  leisure   areas leisure=park/garden/golf_course/pitch/playground/nature_reserve/...
  water     natural=water / waterway=riverbank areas, landuse=reservoir/basin areas, all waterway=* lines (incl. intermittent washes)
One OSM object may appear under more than one kind (e.g. natural=water is both 'natural' and 'water'; landuse=reservoir is
both 'landuse' and 'water'), so callers can ask for the kind they care about without re-reading tags.
Built into <out>.building, then os.replace (atomic). Node locations use osmium's flex_mem index (bounded by the extract size)."""
import json, sqlite3, sys, time, os
import osmium
from shapely import wkb as swkb

D = r'C:\BTE-Migration\shelltool\osm-extract'
a = sys.argv[1:]
PBF = a[0] if len(a) > 0 else os.path.join(D, 'nevada-latest.osm.pbf')
PROV = a[1] if len(a) > 1 else os.path.join(D, 'nevada-latest.provenance.json')
OUT = a[2] if len(a) > 2 else os.path.join(D, 'clark_landcover_index.sqlite')
COVER = tuple(map(float, a[3].split(','))) if len(a) > 3 else (-116.1, 35.0, -114.0, 36.9)   # same box as clark_osm_index (Clark County incl. Moapa Valley)

NATURAL = {'wood', 'scrub', 'grassland', 'heath', 'sand', 'bare_rock', 'wetland', 'water', 'scree', 'shingle', 'beach', 'mud',
           'tree_row', 'fell', 'shrubbery', 'rock', 'stone', 'cliff', 'desert', 'dune', 'valley', 'spring'}
LEISURE = {'park', 'garden', 'golf_course', 'pitch', 'playground', 'nature_reserve', 'dog_park', 'common', 'recreation_ground',
           'sports_centre', 'stadium', 'track', 'miniature_golf', 'schoolyard', 'picnic_site', 'water_park', 'swimming_pool'}
WATER_LANDUSE = {'reservoir', 'basin'}

fab = osmium.geom.WKBFactory()
tmp = OUT + '.building'
if os.path.exists(tmp): os.remove(tmp)
db = sqlite3.connect(tmp)
db.executescript('''create table feat(id integer primary key, osm text, kind text, tags text, wkb blob);
create virtual table feat_idx using rtree(id, minx, maxx, miny, maxy);
create table meta(k text primary key, v text);''')
rows = []; n = dict.fromkeys(('tree', 'tree_row', 'landuse', 'natural', 'leisure', 'water'), 0)


def inside(b): return not (b[2] < COVER[0] or b[0] > COVER[2] or b[3] < COVER[1] or b[1] > COVER[3])


def add(osm, kinds, tags, g):
    b = g.bounds
    if not inside(b): return
    t = json.dumps(tags, separators=(',', ':')); w = g.wkb
    for k in kinds:
        rows.append((osm, k, t, w, b)); n[k] += 1
    if len(rows) >= 20000: flush()


def flush():
    cur = db.cursor()
    for osm, kind, tags, w, b in rows:
        cur.execute('insert into feat(osm,kind,tags,wkb) values(?,?,?,?)', (osm, kind, tags, w))
        cur.execute('insert into feat_idx values(?,?,?,?,?)', (cur.lastrowid, b[0], b[2], b[1], b[3]))
    db.commit(); rows.clear()


def area_kinds(t):
    k = []
    if 'landuse' in t: k.append('landuse')
    if t.get('natural') in NATURAL: k.append('natural')
    if t.get('leisure') in LEISURE: k.append('leisure')
    if t.get('natural') == 'water' or t.get('waterway') == 'riverbank' or t.get('landuse') in WATER_LANDUSE or t.get('water'): k.append('water')
    return k


class H(osmium.SimpleHandler):
    def node(self, o):
        if o.tags.get('natural') == 'tree':
            try: add('n%d' % o.id, ['tree'], {k: v for k, v in o.tags}, swkb.loads(fab.create_point(o), hex=True))
            except Exception: pass

    def way(self, w):
        nat, ww = w.tags.get('natural'), w.tags.get('waterway')
        kinds = (['tree_row'] if nat == 'tree_row' else []) + (['water'] if ww and ww != 'riverbank' else [])
        if not kinds: return
        try: add('w%d' % w.id, kinds, {k: v for k, v in w.tags}, swkb.loads(fab.create_linestring(w), hex=True))
        except Exception: pass

    def area(self, ar):
        t = {k: v for k, v in ar.tags}
        kinds = area_kinds(t)
        if not kinds: return
        try: g = swkb.loads(fab.create_multipolygon(ar), hex=True)
        except Exception: return
        if g.geom_type == 'MultiPolygon' and len(g.geoms) == 1: g = g.geoms[0]
        add(('w%d' if ar.from_way() else 'r%d') % ar.orig_id(), kinds, t, g)


if __name__ == '__main__':
    t0 = time.time()
    H().apply_file(PBF, locations=True, idx='flex_mem')
    flush()
    prov = json.load(open(PROV))
    meta = dict(source=prov['source'], extract_last_modified=prov['last_modified'], extract_sha256=prov['sha256'], downloaded=prov['downloaded'],
                coverage_lonlat_box=list(COVER), built=time.strftime('%Y-%m-%dT%H:%M:%S'), counts=n, build_seconds=round(time.time() - t0),
                builder='prep/egg_landcover_index.py', licence='ODbL, (c) OpenStreetMap contributors',
                note='Features whose bounds intersect the coverage box. Inside it, absence of a feature = not mapped in OSM at the extract date. '
                     'An OSM object may be stored under several kinds.')
    for k, v in meta.items(): db.execute('insert into meta values(?,?)', (k, json.dumps(v)))
    db.execute('create index feat_osm on feat(osm)'); db.execute('create index feat_kind on feat(kind)'); db.commit(); db.close()
    os.replace(tmp, OUT)
    print(json.dumps(meta, indent=1))
