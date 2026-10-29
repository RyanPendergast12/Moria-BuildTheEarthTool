"""shelltool_prep.py <cache_dir> <cx> <cz> [--content shells|roads|both] [--recompute-accepted]
v3 on-demand data preparation for ONE impact chunk (called by the SummerlinShellTool plugin, off the server thread).
 data      : local OSM index (Las Vegas valley, scripts/osm_index_build.py) inside its coverage; bounded Overpass outside,
             with provider avoidance and reuse of a cached neighbouring query. Failure != empty land.
 shells    : USGS 3DEP LiDAR (cached EPT tiles) + SCAFFOLD-SPEC rules (unchanged from v2), per building, whole footprint.
 roads     : global way geometry projected to block space (TerraPlusMinus code), classified for the 256 columns of this chunk
             only; widths/markings per summerlin/detailing/ROAD-IMPLEMENTATION-SPEC.md (guide methods + labelled estimates).
 terrain   : TPM height dataset (expected surface per column; the writer still checks the real surface).
Writes <cache>/chunks/<cx>_<cz>.json (manifest incl. per-stage timings), buildings/<id>.json, roads/<cx>_<cz>.json."""
import glob, hashlib, json, math, os, re, sqlite3, sys, time, urllib.parse, urllib.request
from contextlib import contextmanager
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Polygon, LineString, Point, box, mapping
from shapely.ops import transform
from shapely import wkb as swkb, contains_xy
from pyproj import Transformer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import osm_geo, ept_cached
from geo import Geo

a = sys.argv; CACHE, CX, CZ = a[1], int(a[2]), int(a[3])
CONTENT = a[a.index('--content') + 1] if '--content' in a else 'shells'
WANT_SHELLS, WANT_ROADS = CONTENT in ('shells', 'both'), CONTENT in ('roads', 'both')
RECOMPUTE = '--recompute-accepted' in a
for d in ('chunks', 'buildings', 'osm', 'roads', 'ept'): os.makedirs(os.path.join(CACHE, d), exist_ok=True)
MAN = os.path.join(CACHE, 'chunks', f'{CX}_{CZ}.json')
VERSION = 'shelltool_prep v3'
INDEX = r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite'
WALL, ROOF, PARTLINE, UNRESOLVED = 'minecraft:stone', 'minecraft:light_gray_wool', 'minecraft:stone', 'minecraft:orange_wool'
DATASETS = [('NV_ClarkCo_2_B22', 2022), ('NV_ClarkCo_1_B22', 2022), ('NV_ClarkCounty_B1_2018', 2018), ('NV_LasVegasValley_2010', 2010)]
EPT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'
MAX_BUILDING_M, EPT_CACHE_GB = 400, 8
T = {}; t00 = time.time()
@contextmanager
def stage(name):
    t = time.time()
    try: yield
    finally: T[name] = round(T.get(name, 0) + time.time() - t, 3)
def log(*m): print('[prep %.1fs]' % (time.time() - t00), *m, flush=True)
old = json.load(open(MAN)) if os.path.exists(MAN) else {}
if 'shells' not in old and old.get('status'):                                  # v0.2 manifest -> shells section (migration)
    old = dict(old, shells=dict(status=old['status'], buildings=old.get('buildings', []), message=old.get('message'), retry=old.get('retry', False)))
def finish(**kw):
    m = dict(old); m.update({k: v for k, v in kw.items() if v is not None})
    m.update(chunk=[CX, CZ], blocks=f'x {CX * 16}..{CX * 16 + 15}, z {CZ * 16}..{CZ * 16 + 15}', prepared=time.strftime('%Y-%m-%dT%H:%M:%S'), pipeline=VERSION)
    T['total'] = round(time.time() - t00, 3); m['timings_s'] = T
    m['status'] = m.get('shells', {}).get('status') or m.get('roads', {}).get('status') or 'unknown'      # read by v0.2
    m['buildings'] = m.get('shells', {}).get('buildings', [])
    m['retry'] = any(isinstance(s, dict) and s.get('retry') for s in (m.get('shells'), m.get('roads')))
    json.dump(m, open(MAN + '.tmp', 'w'), indent=1); os.replace(MAN + '.tmp', MAN)
    log('manifest', json.dumps({k: (m.get(k) or {}).get('status') for k in ('shells', 'roads')}), T); sys.exit(0)

geo = Geo()
x0, z0 = CX * 16, CZ * 16
P = json.load(open(r'C:\BTE-Migration\summerlin\pilot\polygons.json')); strip = Polygon(P['strip_mc']).buffer(500)
if strip.intersects(box(x0, z0, x0 + 16, z0 + 16)):
    msg = 'Chunk touches the protected Strip exclusion (+500 blocks). Nothing fetched or generated.'
    finish(shells=dict(status='protected', message=msg), roads=dict(status='protected', message=msg))
with stage('projection'):
    corners = geo.inv([(x0, z0), (x0 + 16, z0), (x0 + 16, z0 + 16), (x0, z0 + 16)])
chunk_ll = Polygon(corners); lons, lats = [c[0] for c in corners], [c[1] for c in corners]
pl, po = 150 / 111320, 150 / (111320 * math.cos(math.radians(np.mean(lats))))
s, w, n, e = min(lats) - pl, min(lons) - po, max(lats) + pl, max(lons) + po

# ---------------- data: local index first, Overpass fallback (bounded, avoidance, neighbour reuse)
SRC = {}
def from_index():
    if not os.path.exists(INDEX): return None
    con = sqlite3.connect(f'file:{INDEX}?mode=ro', uri=True); meta = {k: json.loads(v) for k, v in con.execute('select k,v from meta')}
    C = meta['coverage_lonlat_box']
    if not (C[0] <= w and C[1] <= s and C[2] >= e and C[3] >= n): con.close(); return None
    rows = con.execute('select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=?', (w, e, s, n)).fetchall()
    con.close()
    SRC.update(source='local OSM index', extract=meta['source'], extract_date=meta['extract_last_modified'], coverage=C)
    return [dict(id=o, kind=k, tags=json.loads(t), geom=swkb.loads(g)) for o, k, t, g in rows]
def from_overpass():
    osm_path = os.path.join(CACHE, 'osm', f'{CX}_{CZ}.json')
    if not os.path.exists(osm_path):
        for fp in glob.glob(os.path.join(CACHE, 'osm', '*.json')):          # reuse a cached query that covers this chunk + 100 m
            try:
                mm = re.search(r'\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)', json.load(open(fp)).get('_query', ''))
                qs, qw, qn, qe = map(float, mm.groups())
                if qs <= min(lats) - pl * 2 / 3 and qw <= min(lons) - po * 2 / 3 and qn >= max(lats) + pl * 2 / 3 and qe >= max(lons) + po * 2 / 3 and 'highway' in open(fp).read(4000):
                    osm_path = fp; break
            except Exception: pass
    if not os.path.exists(osm_path):
        pv = os.path.join(CACHE, 'providers.json'); av = json.load(open(pv)) if os.path.exists(pv) else {}
        bb = f'{s:.6f},{w:.6f},{n:.6f},{e:.6f}'
        q = f'[out:json][timeout:60];(way["building"]({bb});relation["building"]["type"="multipolygon"]({bb});way["highway"]({bb});way["area:highway"]({bb}););out body;>;out skel qt;'
        errs = []
        for attempt in range(2):
            for url in ['https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter', 'https://overpass.kumi.systems/api/interpreter']:
                if av.get(url, 0) > time.time(): errs.append(f'{url}: avoided until {time.strftime("%H:%M:%S", time.localtime(av[url]))} after a recent failure'); continue
                try:
                    d = urllib.request.urlopen(urllib.request.Request(url, data=urllib.parse.urlencode({'data': q}).encode(), headers={'User-Agent': 'BTE-local-shelltool/1.0'}), timeout=60).read()
                    j = json.loads(d)
                    if 'error' in j.get('remark', '').lower() or 'timed out' in j.get('remark', '').lower(): raise RuntimeError('incomplete result: ' + j['remark'][:120])
                    j['_source_url'] = url; j['_query'] = q; j['_fetched'] = time.strftime('%Y-%m-%dT%H:%M:%S')
                    json.dump(j, open(osm_path, 'w')); break
                except Exception as ex:
                    errs.append(f'{url}: {ex}'); av[url] = time.time() + 600; json.dump(av, open(pv, 'w'))
            else:
                if attempt == 0: time.sleep(10); continue
                return errs
            break
    j = json.load(open(osm_path))
    SRC.update(source='Overpass (outside local index coverage)', provider=j.get('_source_url'), fetched=j.get('_fetched'))
    return [dict(id=f['id'], kind='building' if 'building' in f['tags'] else 'area_highway' if 'area:highway' in f['tags'] else 'highway', tags=f['tags'], geom=f['geom'])
            for f in osm_geo.load(osm_path)]
with stage('footprint_road_retrieval'):
    feats = from_index()
    if feats is None: feats = from_overpass()
if feats and isinstance(feats[0], str):
    fail = dict(status='provider_failure', stage='footprint_road_retrieval', errors=feats, retry=True,
                message='OSM data could not be fetched (outside local index; all Overpass mirrors failed or are being avoided). NOT confirmed empty land; retried on the next throw.')
    finish(shells=fail if WANT_SHELLS else None, roads=fail if WANT_ROADS else None, data_source=SRC)
log('data', SRC.get('source'), len(feats), 'features')
out = dict(data_source=SRC)

# ---------------- shells (rules unchanged from v2)
if WANT_SHELLS:
    accepted = set()
    if not RECOMPUTE:
        for l in open(r'C:\BTE-Migration\shelltool\shells.txt'):
            if l.startswith('F '):
                t = l.split(' ', 2)[2].split('\t')
                if int(t[3]) > 0: accepted.add(t[0])
    to_utm = Transformer.from_crs(4326, 32611, always_xy=True).transform
    blds = [f for f in feats if f['kind'] == 'building' and f['geom'].is_valid and f['geom'].intersects(chunk_ll)]
    results, todo, lid = [], [], None
    for f in blds:
        gh = hashlib.sha256(f['geom'].normalize().wkb).hexdigest()[:16]; bp = os.path.join(CACHE, 'buildings', f['id'] + '.json')
        if f['id'] in accepted: results.append(dict(id=f['id'], source='accepted-pilot', status='shell')); continue
        if os.path.exists(bp):
            b = json.load(open(bp))
            if not b['status'].endswith('provider_failure') and (b.get('geom_hash') == gh or 'pipeline' in b and 'v3' not in b['pipeline']):
                results.append(dict(id=f['id'], source='cache', status=b['status'])); continue     # v2 cache entries are reused as-is (migration)
        G = transform(to_utm, f['geom']); minx, miny, maxx, maxy = G.bounds
        rec = dict(id=f['id'], name=f['tags'].get('name'), building=f['tags'].get('building'), geom_hash=gh, pipeline=VERSION,
                   footprint_lonlat=mapping(f['geom']), data_source=SRC, segments=[])
        if max(maxx - minx, maxy - miny) > MAX_BUILDING_M:
            rec.update(status='deferred_too_large', message=f'footprint {max(maxx - minx, maxy - miny):.0f} m across exceeds the on-demand limit ({MAX_BUILDING_M} m)')
            json.dump(rec, open(bp, 'w')); results.append(dict(id=f['id'], source='computed', status=rec['status'])); continue
        todo.append((f, G, rec, bp))
    if todo:
        grids, allpts = [], []
        with stage('projection'):
            for f, G, rec, bp in todo:
                ring = list(f['geom'].exterior.coords) if f['geom'].geom_type == 'Polygon' else [c for g in f['geom'].geoms for c in g.exterior.coords]
                mc = geo.fwd(ring)
                X0, Z0 = int(math.floor(min(p[0] for p in mc))) - 2, int(math.floor(min(p[1] for p in mc))) - 2
                NX, NZ = int(math.ceil(max(p[0] for p in mc))) - X0 + 3, int(math.ceil(max(p[1] for p in mc))) - Z0 + 3
                grids.append((X0, Z0, NX, NZ, len(allpts))); allpts += [(X0 + i + 0.5, Z0 + j + 0.5) for j in range(NZ) for i in range(NX)]
            ll = np.array(geo.inv(allpts))
        with stage('terrain'):
            hts = geo.heights([tuple(p) for p in ll])
        if not np.isfinite(hts).all():
            finish(shells=dict(status='provider_failure', stage='terrain', retry=True, message=f'TPM terrain heights incomplete ({int(np.isfinite(hts).sum())}/{len(hts)}); retried next throw'), data_source=SRC)
        E_all, N_all = to_utm(ll[:, 0], ll[:, 1])
        U = [G.bounds for _, G, _, _ in todo]; ub = (min(b[0] for b in U) - 10, min(b[1] for b in U) - 10, max(b[2] for b in U) + 10, max(b[3] for b in U) + 10)
        u2m = Transformer.from_crs(32611, 3857, always_xy=True).transform
        (bx0, by0), (bx1, by1) = u2m(ub[0], ub[1]), u2m(ub[2], ub[3])
        lid_errs = []
        with stage('lidar'):
            for name, year in DATASETS:
                try:
                    r = ept_cached.fetch(EPT + name, (min(bx0, bx1), min(by0, by1), max(bx0, bx1), max(by0, by1)), os.path.join(CACHE, 'ept'))
                    if r is None: lid_errs.append(f'{name}: outside published bounds'); continue
                    if (r['cls'] == 2).sum() < 50: lid_errs.append(f'{name}: too few ground points'); continue
                    X, Y = Transformer.from_crs(3857, 32611, always_xy=True).transform(r['x'], r['y'])
                    lid = dict(name=name, year=year, X=X, Y=Y, Z=r['z'], C=r['cls'], nret=r['nret'], tiles=r['nodes'], fetched=r['fetched'], reused=r['reused'])
                    log('lidar', name, len(X), 'points; tiles fetched', r['fetched'], 'reused', r['reused']); break
                except ept_cached.Limit as ex: lid_errs.append(f'{name}: {ex}')
                except Exception as ex: lid_errs.append(f'{name}: provider error {ex}')
            ept_cached.prune(os.path.join(CACHE, 'ept'), EPT_CACHE_GB)
        with stage('planning'):
            if lid is not None:
                keep = (lid['C'] != 7) & (lid['C'] != 18)
                X, Y, Z, C, nr = lid['X'][keep], lid['Y'][keep], lid['Z'][keep], lid['C'][keep], lid['nret'][keep]
                gx0, gy0 = np.floor(ub[0]), np.floor(ub[1]); gnx, gny = int(ub[2] - gx0) + 2, int(ub[3] - gy0) + 2
                ok = (X >= gx0) & (Y >= gy0) & (X < gx0 + gnx) & (Y < gy0 + gny); X, Y, Z, C, nr = X[ok], Y[ok], Z[ok], C[ok], nr[ok]
                g = C == 2; gi = ((Y[g] - gy0).astype(int), (X[g] - gx0).astype(int))
                dsum = np.zeros((gny, gnx)); dcnt = np.zeros((gny, gnx)); np.add.at(dsum, gi, Z[g]); np.add.at(dcnt, gi, 1)
                dtm = np.where(dcnt > 0, dsum / np.maximum(dcnt, 1), np.nan)
                dtm = dtm[tuple(ndi.distance_transform_edt(np.isnan(dtm), return_distances=False, return_indices=True))]
                ng = (C == 1) & (nr == 1); dsm = np.full((gny, gnx), np.nan); ii = ((Y[ng] - gy0).astype(int), (X[ng] - gx0).astype(int))
                o = np.argsort(Z[ng]); dsm[ii[0][o], ii[1][o]] = Z[ng][o]; ndsm = dsm - dtm
                cy, cxg = np.mgrid[0:gny, 0:gnx]; CXU, CYU = cxg + gx0 + 0.5, cy + gy0 + 0.5
                def nd_at(Ea, Na):
                    j, i = (Ea - gx0).astype(int), (Na - gy0).astype(int); okk = (i >= 0) & (j >= 0) & (i < gny) & (j < gnx)
                    v = np.full(Ea.shape, np.nan); v[okk] = ndsm[i[okk], j[okk]]; return v
            for (f, G, rec, bp), (X0, Z0, NX, NZ, off) in zip(todo, grids):
                sl = slice(off, off + NX * NZ); E, N = E_all[sl].reshape(NZ, NX), N_all[sl].reshape(NZ, NX)
                ground = np.floor(hts[sl]).astype(int).reshape(NZ, NX); cells = contains_xy(G, E, N); t = f['tags']; segs = []
                if not cells.any():
                    rec.update(status='skipped_too_small', message='smaller than one block centre'); json.dump(rec, open(bp, 'w')); results.append(dict(id=f['id'], source='computed', status=rec['status'])); continue
                base = int(np.median(ground[cells])); rec.update(cells=int(cells.sum()), ground_median_y=base); hsrc = None
                if lid is not None:
                    inner = G.buffer(-1.0); cm = contains_xy(inner if not inner.is_empty else G, CXU, CYU)
                    hv = ndsm[cm]; hv = hv[np.isfinite(hv)]; raised = float((hv > 2.0).mean()) if hv.size else 0.0
                    rec.update(lidar_dataset=lid['name'], lidar_year=lid['year'], lidar_cells_with_returns=int(hv.size), raised_fraction=round(raised, 3))
                    if hv.size >= 20 and raised >= 0.5: hsrc = 'lidar'
                if hsrc is None and re.match(r'^\d+(\.\d+)?$', str(t.get('height', '')).strip().replace(' m', '')): hsrc = 'osm-height'
                rec['height_source'] = hsrc or 'unresolved'
                if hsrc is None and lid is None and any('provider error' in x for x in lid_errs):
                    rec.update(status='height_provider_failure', height_source='unknown', message='LiDAR could not be fetched (' + '; '.join(lid_errs) + '). Nothing generated; retried next throw.')
                elif hsrc is None:
                    ring = cells & ~ndi.binary_erosion(cells)
                    for iz, ix in zip(*np.nonzero(ring)): segs.append([X0 + int(ix), Z0 + int(iz), int(ground[iz, ix]) + 1, int(ground[iz, ix]) + 1, UNRESOLVED, 'G', 'unresolved-outline'])
                    why = (f"LiDAR unavailable ({'; '.join(lid_errs)})" if lid is None else f"only {rec.get('raised_fraction', 0):.0%} of footprint raised >2 m in {lid['name']} ({lid['year']})")
                    rec.update(status='outline_unresolved_height', message='Footprint exists but no reliable height: ' + why + '. Ground outline only (approved convention); no height invented.')
                else:
                    if hsrc == 'lidar':
                        H = nd_at(E, N); Hs = ndi.generic_filter(np.nan_to_num(H, nan=-1), np.nanmedian, size=3); h = np.where(cells, Hs, np.nan); h[h < 2.0] = np.nan
                    else: h = np.where(cells, float(str(t['height']).replace(' m', '')), np.nan)
                    q = np.round(h); parts = np.zeros(cells.shape, np.int32); nparts = 0
                    for v in np.unique(q[np.isfinite(q)]):
                        lab, nn = ndi.label(q == v)
                        for i in range(1, nn + 1): nparts += 1; parts[lab == i] = nparts
                    for _ in range(50):
                        sizes = np.bincount(parts.ravel(), minlength=nparts + 1); small = [p for p in range(1, nparts + 1) if 0 < sizes[p] < 25]
                        if not small: break
                        for p in small:
                            mk = parts == p
                            if not mk.any(): continue
                            nb = ndi.binary_dilation(mk) & ~mk & (parts > 0)
                            if not nb.any(): parts[mk] = 0; continue
                            hp = np.nanmedian(h[mk]); cand = np.unique(parts[nb]); parts[mk] = min(cand, key=lambda c: abs(np.nanmedian(h[parts == c]) - hp))
                    changed = True
                    while changed:
                        changed = False
                        for p in np.unique(parts[parts > 0]):
                            mk = parts == p
                            if not mk.any(): continue
                            for c in np.unique(parts[ndi.binary_dilation(mk) & ~mk & (parts > 0)]):
                                if abs(np.nanmedian(h[parts == c]) - np.nanmedian(h[mk])) <= 1.5: parts[parts == c] = p; changed = True
                    ids = [p for p in np.unique(parts) if p > 0]; top = {p: base + int(round(float(np.nanmedian(h[parts == p])))) for p in ids}
                    rec['parts'] = [dict(columns=int((parts == p).sum()), top_y=top[p], height_m=round(float(np.nanmedian(h[parts == p])), 1)) for p in ids]
                    rec['open_columns_inside_footprint'] = int((cells & (parts == 0)).sum())
                    tops = np.zeros(cells.shape, np.int32)
                    for p in ids: tops[parts == p] = top[p]
                    canopy = t.get('building') in ('roof', 'carport'); posts = set()
                    if canopy:
                        for ring in ([G.exterior] if G.geom_type == 'Polygon' else [g.exterior for g in G.geoms]):
                            for ve, vn in list(ring.coords)[:-1]:
                                dd = np.where(parts > 0, (E - ve) ** 2 + (N - vn) ** 2, np.inf); posts.add(np.unravel_index(np.argmin(dd), dd.shape))
                    for iz, ix in zip(*np.nonzero(parts > 0)):
                        x, z, p, ty = X0 + int(ix), Z0 + int(iz), parts[iz, ix], int(tops[iz, ix])
                        nbs = [(iz + dz, ix + dx) for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                        lower = [tops[aa, bb] if (0 <= aa < NZ and 0 <= bb < NX and parts[aa, bb] > 0) else None for aa, bb in nbs]
                        outer = any(l is None for l in lower)
                        if canopy:
                            if (iz, ix) in posts: segs.append([x, z, int(ground[iz, ix]) + 1, ty - 1, WALL, 'W', 'wall'])
                        elif outer: segs.append([x, z, int(ground[iz, ix]) + 1, ty - 1, WALL, 'W', 'wall'])
                        elif min(lower) < ty: segs.append([x, z, int(min(lower)) + 1, ty - 1, WALL, 'F', 'wall'])
                        boundary = any(l is not None and parts[aa, bb] != p for (aa, bb), l in zip(nbs, lower))
                        segs.append([x, z, ty, ty, PARTLINE if (boundary or outer) else ROOF, 'F', 'part-outline' if boundary else ('roof-edge' if outer else 'roof')])
                    rec.update(status='shell', message=('LiDAR %s (%d)' % (lid['name'], lid['year'])) if hsrc == 'lidar' else 'OSM height attribute (single flat part)')
                rec['segments'] = segs; rec['chunks'] = sorted({(sg[0] >> 4, sg[1] >> 4) for sg in segs})
                json.dump(rec, open(bp, 'w')); results.append(dict(id=f['id'], source='computed', status=rec['status']))
    out['shells'] = dict(status='ready' if blds else 'no_buildings', buildings=results,
                         message=f'{len(blds)} mapped building outline(s) intersect this chunk' if blds else 'No mapped building outline intersects this chunk in the ' + SRC.get('source', '?') + '.',
                         lidar=(dict(dataset=lid['name'], tiles=lid['tiles'], fetched=lid['fetched'], reused=lid['reused']) if lid else None))

# ---------------- roads (summerlin/detailing/ROAD-IMPLEMENTATION-SPEC.md)
CARRIAGE = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link',
            'unclassified', 'residential', 'living_street', 'service', 'road'}
DEFAULT_LANES = {'motorway': 2, 'trunk': 2, 'primary': 2, 'secondary': 2, 'tertiary': 1, 'unclassified': 1, 'residential': 1, 'living_street': 1, 'service': 1, 'road': 1}
MARKED = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link'}
CURBED = CARRIAGE - {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'service'}
LANE_M, SIDEWALK_M, BIKE_M, CURB_M, CROSS_M, DASH_ON, DASH_PERIOD = 3.6, 1.5, 1.5, 1.0, 3.0, 3.0, 12.0
SHOULDER = {'motorway': (3.0, 1.2), 'motorway_link': (2.4, 1.2), 'trunk': (3.0, 1.2), 'trunk_link': (2.4, 1.2)}   # (right, left) estimates
def num(v):
    try: return float(str(v).lower().replace('m', '').strip())
    except Exception: return None
def hsh(x, z): return ((x * 73856093) ^ (z * 19349663)) & 0xffff
if WANT_ROADS:
    with stage('planning'):
        ways, unsupported = [], []
        ctx = chunk_ll.buffer(0.0006)                                                    # ~60 m context: widths, joins, junctions
        for f in feats:
            if f['kind'] != 'highway' or f['geom'].geom_type != 'LineString' or len(f['geom'].coords) < 2 or not f['geom'].intersects(ctx): continue
            t = f['tags']; hw = t.get('highway'); fid = f['id']
            grade = [k + '=' + t[k] for k in ('bridge', 'tunnel') if t.get(k, 'no') != 'no'] + (['layer=' + t['layer']] if t.get('layer', '0') != '0' else []) + (['covered'] if t.get('covered') == 'yes' else [])
            if hw in CARRIAGE and grade:
                if f['geom'].intersects(chunk_ll): unsupported.append(dict(id=fid, highway=hw, name=t.get('name'), reason='grade-separated (' + ','.join(grade) + '): deck/tunnel elevation not resolved from evidence; skipped (ground road beneath untouched)'))
                continue
            if hw == 'service' and t.get('service') in ('parking_aisle', 'drive-through'):
                if f['geom'].intersects(chunk_ll): unsupported.append(dict(id=fid, highway=hw, reason='service=' + t['service'] + ' not generated (pilot rule)'))
                continue
            if hw in CARRIAGE and t.get('area') != 'yes': ways.append(('carriage', f))
            elif hw == 'footway' and t.get('footway') == 'sidewalk' and not grade: ways.append(('sidewalk', f))
            elif hw == 'footway' and t.get('footway') == 'crossing' and not grade: ways.append(('crossing', f))
            elif f['geom'].intersects(chunk_ll) and hw not in ('footway', 'path', 'cycleway', 'steps', 'pedestrian', 'track', 'bridleway'):
                unsupported.append(dict(id=fid, highway=hw, reason='class not generated by this spec'))
        areas = [f for f in feats if f['kind'] == 'area_highway' and f['geom'].geom_type == 'Polygon' and f['geom'].intersects(ctx)]
        blds_ll = [f['geom'] for f in feats if f['kind'] == 'building' and f['geom'].intersects(chunk_ll.buffer(0.0002))]
    with stage('projection'):
        pts = [c for _, f in ways for c in f['geom'].coords] + [c for g in areas for c in g['geom'].exterior.coords] + \
              [c for g in blds_ll for gg in (g.geoms if g.geom_type == 'MultiPolygon' else [g]) for c in gg.exterior.coords]
        mc = geo.fwd(pts) if pts else []
        cells_ll = geo.inv([(x0 + i + 0.5, z0 + j + 0.5) for j in range(16) for i in range(16)])
        uu = Transformer.from_crs(4326, 32611, always_xy=True).transform
        (ea, na), (eb, nb2) = uu(*corners[0]), uu(*corners[1]); B = 16 / math.hypot(eb - ea, nb2 - na)              # blocks per metre here
    with stage('terrain'):
        hh = geo.heights(cells_ll)
    if WANT_ROADS and not np.isfinite(hh).all():
        finish(roads=dict(status='provider_failure', stage='terrain', retry=True, message='TPM terrain heights incomplete; retried next throw'), **{k: v for k, v in out.items() if k != 'data_source'}, data_source=SRC)
    ey = np.floor(hh).astype(int)
    with stage('planning'):
        it = iter(mc)
        ways_mc = [(kind, f, LineString([next(it) for _ in f['geom'].coords])) for kind, f in ways]
        areas_mc = [(g, Polygon([next(it) for _ in g['geom'].exterior.coords])) for g in areas]
        bld_mc = []
        for g in blds_ll:
            for gg in (g.geoms if g.geom_type == 'MultiPolygon' else [g]): bld_mc.append(Polygon([next(it) for _ in gg.exterior.coords]))
        sidewalk_lines = [L for kind, f, L in ways_mc if kind == 'sidewalk']
        feats_out, carr = [], []
        for kind, f, L in ways_mc:
            if kind != 'carriage': continue
            t = f['tags']; hw = t.get('highway')
            oneway = t.get('oneway') in ('yes', '1', '-1') or hw in ('motorway', 'motorway_link') or t.get('junction') == 'roundabout'
            if t.get('oneway') == '-1': L = LineString(list(L.coords)[::-1])
            lanes = int(num(t['lanes'])) if num(t.get('lanes')) else None
            lane_src = 'osm lanes' if lanes else 'estimate (default for highway=' + hw + ')'
            if not lanes: lanes = DEFAULT_LANES.get(hw, 1) * (1 if oneway else 2)
            cyc = {k: t.get(k) for k in ('cycleway', 'cycleway:both', 'cycleway:left', 'cycleway:right')}
            nbike = 2 if cyc['cycleway:both'] == 'lane' or (cyc['cycleway'] == 'lane' and not oneway) else (1 if 'lane' in cyc.values() else 0)
            lw, wtag = LANE_M, num(t.get('width'))
            if wtag: W, wsrc = wtag, 'osm width tag (mapped attribute)'; lw = max(2.5, (W - nbike * BIKE_M) / lanes)
            else: W, wsrc = lanes * LANE_M + nbike * BIKE_M, f'ESTIMATE: {lanes} lanes x {LANE_M} m' + (f' + {nbike} x {BIKE_M} m bike lane' if nbike else '')
            sh = SHOULDER.get(hw, (0, 0)); Wtot = W + sh[0] + (sh[1] if oneway else sh[0]); half = Wtot / 2 * B
            poly = L.buffer(half, cap_style=1, join_style=1)
            marks = []                                  # (offset m, left of travel positive; colour; style)
            if hw in MARKED or (hw in ('residential', 'unclassified') and 'lanes' in t and lanes >= 2):
                edge = -Wtot / 2 + sh[0]
                if oneway:
                    if nbike: marks.append((edge + BIKE_M, 'white', 'solid')); edge += BIKE_M
                    for kk in range(1, lanes): marks.append((edge + kk * lw, 'white', 'dashed'))
                    if hw in SHOULDER: marks += [(edge, 'white', 'solid'), (edge + lanes * lw, 'yellow', 'solid')]
                else:
                    nf = int(num(t.get('lanes:forward'))) if num(t.get('lanes:forward')) else (lanes + 1) // 2
                    twl = 1 if num(t.get('lanes:both_ways')) == 1 else 0
                    nbw = max(lanes - nf - twl, 0)
                    if nbike: edge += BIKE_M; marks.append((edge, 'white', 'solid'))
                    for kk in range(1, nf): marks.append((edge + kk * lw, 'white', 'dashed'))
                    c1 = edge + nf * lw; marks.append((c1, 'yellow', 'solid'))
                    if twl: marks.append((c1 + lw, 'yellow', 'solid'))
                    c2 = c1 + twl * lw
                    for kk in range(1, nbw): marks.append((c2 + kk * lw, 'white', 'dashed'))
                    if nbike: marks.append((c2 + nbw * lw, 'white', 'solid'))
            sw = t.get('sidewalk', t.get('sidewalk:both', ''))
            sides = set({'both': ('l', 'r'), 'yes': ('l', 'r'), 'left': ('l',), 'right': ('r',)}.get(sw, ()))
            if t.get('sidewalk:left') == 'yes': sides.add('l')
            if t.get('sidewalk:right') == 'yes': sides.add('r')
            mapped_near = any(L.distance(S) <= half + (CURB_M + SIDEWALK_M + 3) * B for S in sidewalk_lines)
            inferred = []
            if sides and not mapped_near:
                inner_d = half + (CURB_M * B if hw in CURBED else 0); outer_d = inner_d + SIDEWALK_M * B
                for sd in sides:
                    sg = -1 if sd == 'l' else 1             # shapely left = +y; block z grows southward, so map-left is shapely-right
                    inferred.append(L.buffer(sg * outer_d, single_sided=True).difference(L.buffer(sg * inner_d, single_sided=True)))
            carr.append(dict(id=f['id'], hw=hw, L=L, poly=poly, half=half, marks=marks, curb=hw in CURBED, inferred=inferred, est=not wtag))
            feats_out.append(dict(id=f['id'], name=t.get('name'), highway=hw, oneway=oneway, lanes=lanes, lanes_source=lane_src, width_m=round(Wtot, 2),
                                  width_source=wsrc + (f' + shoulders {sh[0]}/{sh[1] if oneway else sh[0]} m (ESTIMATE)' if sh[0] else ''), lane_width_m=round(lw, 2),
                                  sidewalks=('mapped separately (footway=sidewalk); not inferred' if mapped_near else (f'INFERRED from sidewalk={sw or "side tags"} ({SIDEWALK_M} m ESTIMATE)' if sides else 'none tagged/mapped')),
                                  markings=[f'{c} {sty} at {o:+.1f} m' for o, c, sty in marks] or ['none (class/lanes unmarked by this spec)'],
                                  curbs='flush 1-block curb (ESTIMATE)' if hw in CURBED else 'none (highway/service class)'))
        vert = {}
        for c in carr:
            for p in c['L'].coords: vert.setdefault((round(p[0], 3), round(p[1], 3)), set()).add(c['id'])
        byid = {c['id']: c for c in carr}
        junc = [(Point(p), max(byid[i]['half'] for i in ids) + 2 * B) for p, ids in vert.items() if len(ids) > 1]
        sw_polys = [(f, L.buffer((num(f['tags'].get('width')) or SIDEWALK_M) / 2 * B, cap_style=2)) for kind, f, L in ways_mc if kind == 'sidewalk']
        cr_polys = [(f, L, L.buffer(CROSS_M / 2 * B, cap_style=2)) for kind, f, L in ways_mc if kind == 'crossing']
        carea = [(g, p) for g, p in areas_mc if g['tags'].get('area:highway') in CARRIAGE]
        sarea = [(g, p) for g, p in areas_mc if g['tags'].get('area:highway') in ('footway', 'sidewalk')]
        curbs = [(c, c['poly'].buffer(CURB_M * B)) for c in carr if c['curb']]
        cells, counts = [], {}
        def add(x, z, i, block, role, fid, label):
            cells.append([x, z, int(ey[i]), block, role, fid, label]); counts[role] = counts.get(role, 0) + 1
        for j in range(16):
            for i in range(16):
                x, z = x0 + i, z0 + j; P_ = Point(x + 0.5, z + 0.5); idx = j * 16 + i
                if any(b.contains(P_) for b in bld_mc): counts['skipped_under_building'] = counts.get('skipped_under_building', 0) + 1; continue
                inside = [c for c in carr if c['poly'].contains(P_)]
                ca = next((g for g, p in carea if p.contains(P_)), None)
                if inside and all(c['est'] for c in inside) and not any(pc.contains(P_) for _, _, pc in cr_polys):
                    # mapped sidewalk = positional evidence of the road edge: it overrides an ESTIMATED carriageway width
                    sm = next((f for f, p in sw_polys if p.contains(P_)), None)
                    if sm: add(x, z, idx, 'minecraft:light_gray_wool' if hsh(x, z) % 3 == 0 else 'minecraft:light_gray_concrete_powder', 'sidewalk', sm['id'], 'MAPPED sidewalk (overrides estimated road width)'); continue
                    if any(p.buffer(CURB_M * B).contains(P_) for f, p in sw_polys) and any(c['curb'] for c in inside):
                        add(x, z, idx, 'minecraft:smooth_stone', 'curb', inside[0]['id'], 'curb at mapped sidewalk (estimate)'); continue
                if inside:
                    c = min(inside, key=lambda c: c['L'].distance(P_)); L = c['L']
                    s_ = L.project(P_); q = L.interpolate(s_); q2 = L.interpolate(min(s_ + 0.5, L.length)); q1 = L.interpolate(max(s_ - 0.5, 0))
                    ux, uz = q2.x - q1.x, q2.y - q1.y; nrm = math.hypot(ux, uz) or 1; ux, uz = ux / nrm, uz / nrm
                    d = ((P_.x - q.x) * uz - (P_.y - q.y) * ux) / B                   # metres; left of travel direction positive
                    cr = next(((f, Lc) for f, Lc, pc in cr_polys if pc.contains(P_)), None)
                    if cr and cr[0]['tags'].get('crossing') not in ('unmarked', 'no'):
                        add(x, z, idx, 'minecraft:white_concrete' if int(cr[1].project(P_) / B) % 2 == 0 else 'minecraft:gray_concrete_powder', 'crossing', cr[0]['id'], 'mapped crossing; bar pattern = engineering choice'); continue
                    thin = 0.5 * max(abs(ux), abs(uz)) / B                              # 1-block digital line (guide 4.4 thin lines)
                    injunc = len({cc['id'] for cc in inside}) > 1 or any(jp.distance(P_) < r for jp, r in junc)
                    mk = None
                    if not injunc:
                        for o, col, sty in c['marks']:
                            if abs(d - o) < thin and (sty == 'solid' or ((s_ / B) % DASH_PERIOD) < DASH_ON): mk = (col, sty, o); break
                    if mk: add(x, z, idx, 'minecraft:white_concrete' if mk[0] == 'white' else 'minecraft:yellow_concrete', 'marking', c['id'], f'{mk[0]} {mk[1]} line at {mk[2]:+.1f} m (US MUTCD convention; phase = distance along way)')
                    else: add(x, z, idx, 'minecraft:cyan_terracotta' if hsh(x, z) % 4 == 0 else 'minecraft:gray_concrete_powder', 'pavement', c['id'], 'carriageway (width per feature report)')
                    continue
                if ca: add(x, z, idx, 'minecraft:gray_concrete_powder', 'pavement', ca['id'], 'mapped area:highway (measured boundary)'); continue
                cb = next((c for c, p in curbs if p.contains(P_)), None)
                if cb: add(x, z, idx, 'minecraft:smooth_stone', 'curb', cb['id'], 'curb (ESTIMATE)'); continue
                sm = next((f for f, p in sw_polys if p.contains(P_)), None) or next((g for g, p in sarea if p.contains(P_)), None)
                if sm: add(x, z, idx, 'minecraft:light_gray_wool' if hsh(x, z) % 3 == 0 else 'minecraft:light_gray_concrete_powder', 'sidewalk', sm['id'], 'MAPPED sidewalk (width tag or 1.5 m ESTIMATE)'); continue
                si = next((c for c in carr for bnd in c['inferred'] if bnd.contains(P_)), None)
                if si: add(x, z, idx, 'minecraft:light_gray_wool' if hsh(x, z) % 3 == 0 else 'minecraft:light_gray_concrete_powder', 'sidewalk', si['id'], 'INFERRED sidewalk (sidewalk tag; 1.5 m ESTIMATE)')
        rpath = os.path.join(CACHE, 'roads', f'{CX}_{CZ}.json')
        json.dump(dict(chunk=[CX, CZ], cells=cells, counts=counts, features=feats_out, unsupported=unsupported, blocks_per_metre=round(B, 4), data_source=SRC, pipeline=VERSION), open(rpath, 'w'))
        out['roads'] = dict(status='ready' if (cells or feats_out) else 'no_roads', cells=len(cells), counts=counts, features=len(feats_out), unsupported=unsupported,
                            message=(f'{len(feats_out)} mapped road way(s) within 60 m; {len(cells)} surface cells planned in this chunk' if feats_out
                                     else 'No mapped road or sidewalk near this chunk in the ' + SRC.get('source', '?') + '.'))
geo.close()
finish(**out)
