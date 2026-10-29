"""shelltool_prep.py <cache_dir> <cx> <cz> [--content shells|roads|both|overpass] [--overpass-auto | --overpass-allow key,key] [--overpass-exclude key,key] [--min-clearance N] [--approach-grade G] [--piers] [--recompute-accepted]
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
WANT_SHELLS, WANT_ROADS, WANT_OVERPASS = CONTENT in ('shells', 'both'), CONTENT in ('roads', 'both'), CONTENT == 'overpass'
ALLOW = set(a[a.index('--overpass-allow') + 1].split(',')) if '--overpass-allow' in a else set()   # pilot allowlist of crossing keys
AUTO = '--overpass-auto' in a                                                     # automatic crossings (eligibility-checked)
EXCLUDE = set(a[a.index('--overpass-exclude') + 1].split(',')) if '--overpass-exclude' in a else set()
MINCLR = int(a[a.index('--min-clearance') + 1]) if '--min-clearance' in a else 4                     # air blocks over the lower road
AGRADE = float(a[a.index('--approach-grade') + 1]) if '--approach-grade' in a else 0.0   # authorized raised approaches (0 = off)
PIERS = '--piers' in a                                                                             # inferred piers: off unless asked
RECOMPUTE = '--recompute-accepted' in a
for d in ('chunks', 'buildings', 'osm', 'roads', 'ept', 'overpass', 'crossings'): os.makedirs(os.path.join(CACHE, d), exist_ok=True)
MAN = os.path.join(CACHE, 'chunks', f'{CX}_{CZ}.json')
VERSION = 'shelltool_prep v4.3'          # plugin re-prepares chunks whose manifest pipeline differs (cache invalidation)
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
if old.get('pipeline') != VERSION: old = {}                                     # sections from another pipeline are not reused
def finish(**kw):
    m = dict(old); m.update({k: v for k, v in kw.items() if v is not None})
    m['approach_grade'] = AGRADE if (WANT_ROADS or WANT_OVERPASS) and (ALLOW or AUTO) else m.get('approach_grade', 0.0)
    m['overpass_mode'] = ('auto' if AUTO else 'allowlist') if (ALLOW or AUTO) else m.get('overpass_mode', 'off')
    m.update(chunk=[CX, CZ], blocks=f'x {CX * 16}..{CX * 16 + 15}, z {CZ * 16}..{CZ * 16 + 15}', prepared=time.strftime('%Y-%m-%dT%H:%M:%S'), pipeline=VERSION)
    T['total'] = round(time.time() - t00, 3); m['timings_s'] = T
    m['status'] = m.get('shells', {}).get('status') or m.get('roads', {}).get('status') or 'unknown'      # read by v0.2
    m['buildings'] = m.get('shells', {}).get('buildings', [])
    m['retry'] = any(isinstance(s, dict) and s.get('retry') for s in (m.get('shells'), m.get('roads'), m.get('overpass')))
    json.dump(m, open(MAN + '.tmp', 'w'), indent=1); os.replace(MAN + '.tmp', MAN)
    log('manifest', json.dumps({k: (m.get(k) or {}).get('status') for k in ('shells', 'roads', 'overpass')}), T); sys.exit(0)

geo = Geo()
x0, z0 = CX * 16, CZ * 16
P = json.load(open(r'C:\BTE-Migration\summerlin\pilot\polygons.json')); strip = Polygon(P['strip_mc']).buffer(500)
if strip.intersects(box(x0, z0, x0 + 16, z0 + 16)):
    msg = 'Chunk touches the protected Strip exclusion (+500 blocks). Nothing fetched or generated.'
    finish(shells=dict(status='protected', message=msg), roads=dict(status='protected', message=msg), overpass=dict(status='protected', message=msg))
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
    finish(shells=fail if WANT_SHELLS else None, roads=fail if WANT_ROADS else None, overpass=fail if WANT_OVERPASS else None, data_source=SRC)
log('data', SRC.get('source'), len(feats), 'features')
out = dict(data_source=SRC)

# ---------------- shells (rules unchanged from v2); independent section
def do_shells():
    if os.environ.get('SHELLTOOL_PREP_FAIL') == 'shells': raise RuntimeError('injected test failure (shells)')
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
            out['shells'] = dict(status='provider_failure', stage='terrain', retry=True, message=f'TPM terrain heights incomplete ({int(np.isfinite(hts).sum())}/{len(hts)}); retried next throw'); return
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

if WANT_SHELLS:
    try: do_shells()
    except Exception as ex:
        log('shells pipeline error', ex); out['shells'] = dict(status='pipeline_error', retry=True, message=f'shell preparation failed ({type(ex).__name__}: {ex}); other content is unaffected')

# ---------------- roads + overpasses (v0.4.3): roadgen.py = road model / per-chunk generation; crossings.py = automatic crossings
import roadgen, overpass, crossings
def index_query(bounds, pad, kind=None):
    """features from the local index (complete geometries), independent of which chunk asked; None outside its coverage"""
    if not os.path.exists(INDEX): return None
    con = sqlite3.connect(f'file:{INDEX}?mode=ro', uri=True); C = json.loads(con.execute("select v from meta where k='coverage_lonlat_box'").fetchone()[0])
    if not (C[0] <= bounds[0] - pad and C[1] <= bounds[1] - pad and C[2] >= bounds[2] + pad and C[3] >= bounds[3] + pad): con.close(); return None
    q = 'select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=?'
    args = [bounds[0] - pad, bounds[2] + pad, bounds[1] - pad, bounds[3] + pad]
    if kind: q += ' and f.kind=?'; args.append(kind)
    rows = con.execute(q, args).fetchall(); con.close()
    return [dict(id=o, kind=k, tags=json.loads(t), geom=swkb.loads(g)) for o, k, t, g in rows]
def index_around(bounds, pad): return index_query(bounds, pad, 'highway')
def index_all(bounds, pad): return index_query(bounds, pad)
def lidar_fetch(box3857):
    for name, year in DATASETS:
        r = ept_cached.fetch(EPT + name, box3857, os.path.join(CACHE, 'ept'), max_nodes=600)      # provider errors propagate -> plan reports retry
        if r is not None and (r['cls'] == 17).sum() > 200: r['dataset'] = f'{name} ({year})'; return r
    return None
OV = dict(deck={}, upper=set(), lower=set(), sidewalks=set(), why={}, skip_cols=set(), raised=[], keys=[])
REPS, OV_CELLS, ov_err = [], [], None
ENV = dict(geo=geo, lidar_fetch=lidar_fetch, cache=CACHE, piers=PIERS, agrade=AGRADE, min_clear=MINCLR, strip=strip, index_around=index_around)
def do_overpass():
    if os.environ.get('SHELLTOOL_PREP_FAIL') == 'overpass': raise RuntimeError('injected test failure (overpass)')
    near = chunk_ll.buffer(0.0025 if AGRADE else 0.0005)        # deck reach ~50 m; ~250 m when raised approaches spread from deck ends
    pool = index_around(chunk_ll.bounds, 0.0025) or feats
    seen = set()
    for f in sorted((b for b in crossings.bridges(pool) if b['geom'].intersects(near)), key=lambda b: b['id']):
        fx = index_around(f['geom'].bounds, 0.002)
        if fx is None:
            REPS.append(dict(key=f['id'], status='skipped', reasons=['data: outside the local OSM index (crossings need complete ways)'])); OV['why'][f['id']] = 'bridge skipped: outside the local OSM index'; continue
        cr = overpass.crossing_of(fx, f['id'])
        if not cr or cr['key'] in seen: continue
        seen.add(cr['key'])
        def skip(status, reasons):
            REPS.append(dict(key=cr['key'], status=status, reasons=reasons))
            for u in cr['upper']: OV['why'][u] = f'bridge {status}: {reasons[0]}'
        if cr['key'] in EXCLUDE: skip('excluded', ['excluded by config overpass-exclude']); continue
        if not AUTO and cr['key'] not in ALLOW: skip('not_enabled', ['allowlist mode: crossing not in overpass-crossings']); continue
        pl = overpass.plan(fx, cr, geo, lidar_fetch, CACHE, piers=PIERS, approach_grade=AGRADE)
        reasons = crossings.eligibility(cr, pl, fx, ENV)
        if reasons: skip('skipped', reasons); continue
        cc = crossings.crossing_cells(cr, pl, ENV, index_all)
        if not cc.get('tag'): skip('unresolved', [cc.get('failure', 'crossing plan failed')]); REPS[-1]['retry'] = True; continue
        here = [c for c in cc['cells'] if (c[0] >> 4) == CX and (c[1] >> 4) == CZ]
        OV_CELLS.extend(here)
        OV['upper'] |= set(cr['upper']); OV['lower'] |= set(cr['lower'])
        OV['deck'].update({(c[0], c[1]): c[2] for c in pl['cells'] if c[5] == 'deck'})
        ups = [g['geom'] for g in fx if g['id'] in cr['upper']]
        OV['sidewalks'] |= {g['id'] for g in fx if g['tags'].get('highway') == 'footway' and g['tags'].get('footway') == 'sidewalk' and g['tags'].get('bridge', 'no') != 'no'
                            and g['tags'].get('layer', '1') == str(cr['layer']) and any(g['geom'].distance(u) < 0.0003 for u in ups)}
        OV['skip_cols'] |= {(c[0], c[1]) for c in cc['cells'] if c[5] == 'approach-surface'}
        ap = pl.get('approach')
        for e in pl.get('deck_ends', []):
            e['status'] = (f"connected by a raised approach (max grade {ap['grade']:.0%})" if ap else e['status'])
        REPS.append(dict({k: v for k, v in pl.items() if k not in ('cells', 'clearance_columns', 'approach')}, status='ready', cells_here=len(here),
                         cells_file=os.path.join(CACHE, 'crossings', f"{cr['key']}.cells.json"), cells_total=cc['n'], crossing_chunks=len(cc['chunks']),
                         approach=(dict({k: v for k, v in ap.items() if k != 'samples'}, raised_samples=len(ap['samples'])) if ap else 'off (deck ends must meet their roads)'),
                         outstanding=['existing ground-level road markings under the deck are left untouched (visual/layout issue)']))
WANT_OV = WANT_OVERPASS or (WANT_ROADS and (AUTO or bool(ALLOW)))
if WANT_OV:
    with stage('overpass'):
        try: do_overpass()
        except Exception as ex:
            ov_err = f'{type(ex).__name__}: {ex}'; OV_CELLS.clear(); OV.update(deck={}, skip_cols=set(), upper=set(), lower=set(), sidewalks=set()); log('overpass pipeline error', ov_err)

def do_roads():
    if os.environ.get('SHELLTOOL_PREP_FAIL') == 'roads': raise RuntimeError('injected test failure (roads)')
    with stage('planning'):
        if os.environ.get('SHELLTOOL_PREP_FAIL') == 'roads-terrain': r = dict(failure='TPM terrain heights incomplete (injected)')
        else: r = roadgen.chunk_roads(CX, CZ, geo, feats, OV, dict(proj={}, EY={}), approach=False)
    if 'failure' in r:
        out['roads'] = dict(status='provider_failure', stage='terrain', retry=True, message=r['failure']); return
    cells, fo = r['cells'], r['feats_out']
    json.dump(dict(chunk=[CX, CZ], cells=cells, counts=r['counts'], features=fo + r['deck_feats'], unsupported=r['unsupported'], blocks_per_metre=round(r['bpm'], 4),
                   data_source=SRC, pipeline=VERSION, style=roadgen.STYLE), open(os.path.join(CACHE, 'roads', f'{CX}_{CZ}.json'), 'w'))
    out['roads'] = dict(status='ready' if (cells or fo) else 'no_roads', cells=len(cells), counts=r['counts'], features=len(fo), unsupported=r['unsupported'],
                        message=(f'{len(fo)} mapped road way(s) within 60 m; {len(cells)} surface cells planned in this chunk' if fo
                                 else 'No mapped road or sidewalk near this chunk in the ' + SRC.get('source', '?') + '.'))
if WANT_ROADS:
    try: do_roads()
    except Exception as ex:
        log('roads pipeline error', ex); out['roads'] = dict(status='pipeline_error', retry=True, message=f'roads preparation failed ({type(ex).__name__}: {ex}); other content is unaffected')
if WANT_OV:
    json.dump(dict(chunk=[CX, CZ], crossings=REPS, cells=OV_CELLS, pipeline=VERSION, style=roadgen.STYLE), open(os.path.join(CACHE, 'overpass', f'{CX}_{CZ}.json'), 'w'))
    if ov_err: st = 'pipeline_error'
    elif OV_CELLS: st = 'ready'
    elif any(r['status'] == 'ready' for r in REPS): st = 'no_cells_here'
    elif any(r['status'] == 'unresolved' for r in REPS): st = 'unresolved'
    elif any(r['status'] == 'skipped' for r in REPS): st = 'skipped'
    elif REPS: st = 'not_enabled'
    else: st = 'no_crossing'
    first = next((r for r in REPS if r['status'] in ('skipped', 'unresolved')), None)
    msg = {'ready': f'{len(OV_CELLS)} crossing cells planned in this chunk', 'no_cells_here': 'an eligible crossing is nearby but none of its blocks fall in this chunk',
           'no_crossing': 'no grade-separated crossing reaches this chunk', 'not_enabled': 'nearby crossing excluded or not in the allowlist',
           'skipped': f"nearby crossing skipped: {first['reasons'][0] if first else ''}", 'unresolved': f"nearby crossing unresolved: {first['reasons'][0] if first else ''}",
           'pipeline_error': f'overpass preparation failed ({ov_err}); other content is unaffected'}[st]
    out['overpass'] = dict(status=st, cells=len(OV_CELLS), crossings=[dict(key=r['key'], status=r['status'], reasons=r.get('reasons', [])) for r in REPS],
                           retry=st == 'pipeline_error' or any(r.get('retry') for r in REPS), message=msg)
geo.close()
finish(**out)
