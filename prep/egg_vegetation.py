"""egg_vegetation.py - Egg (v0.5) vegetation generator: plan(ctx) -> egg_common.plan(...) (ARCHITECTURE.md sections 4, 5, 9).
Spec: docs/egg/VEGETATION-SPEC.md. Evidence tiers (features[].tier):
  mapped   OSM natural=tree points / natural=tree_row lines (position mapped; species from tags, else a Las Vegas urban mix)
  measured USGS 3DEP lidar canopy: class-1 multi-return points above class-2 ground, clustered into crowns (species unknown)
  broad    ESA WorldCover 2021 class -> ONLY sparse desert ground cover / small desert shrubs on undeveloped eligible columns
  inferred trees inside OSM wood/forest polygons (off unless ctx['vegetation_policy']['inferred'])
Whole plants are planned (also those whose trunk lies in the 32-block ring), then clipped to the tile. Only the tile holding the trunk
base writes the trunk; cells of a plant anchored in another tile get role '<role>-remote' (plugin writes them only once the anchor exists).
Deterministic: every choice is egg_common.det(feature id, seed, ...)."""
import json, math, os, struct, sys, time, urllib.request, zlib
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egg_common as E

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'egg_assets', 'vegetation_catalog.json')
EPT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'
DATASETS = [('NV_ClarkCo_2_B22', 2022), ('NV_ClarkCo_1_B22', 2022), ('NV_ClarkCounty_B1_2018', 2018), ('NV_LasVegasValley_2010', 2010)]  # = shelltool_prep
WC_BASE = 'https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/'
WC_CLASSES = {10: 'tree cover', 20: 'shrubland', 30: 'grassland', 40: 'cropland', 50: 'built-up', 60: 'bare / sparse vegetation', 70: 'snow and ice',
              80: 'permanent water', 90: 'herbaceous wetland', 95: 'mangroves', 100: 'moss and lichen'}
WC_MAX_TILES, WC_CACHE_MB = 16, 300          # COG tiles (1024 px, ~90 KB) per call; total worldcover cache cap
LIDAR_MARGIN = 16                            # lidar box = tile + ring + margin, so crowns of ring trees are measured on full data
SOIL = {'sand', 'red_sand', 'dirt', 'coarse_dirt', 'grass_block', 'podzol', 'rooted_dirt', 'terracotta'}     # + *_terracotta
SUPPORT = {'dead_bush': SOIL - {'grass_block'} | {'grass_block'},          # 1.18 DeadBushBlock: sand, terracotta, dirt tag
           'grass': {'dirt', 'coarse_dirt', 'grass_block', 'podzol', 'rooted_dirt'}}   # dirt tag only: never on sand
LANDSCAPE_OK = E.P_LANDSCAPED                # the only protect bit vegetation may touch, and only for mapped trees (section 5)
MOUNTAIN_Y = 1800                            # Spring Mountains conifer zone; the valley floor is ~500-1100 m

# species pools (catalog id prefixes, weights). Sources: VEGETATION-SPEC.md "Species rules".
POOL = {
    'palm': [('lv-washingtonia', 7), ('lv-date-palm', 3)],
    'broadleaf-urban': [('lv-mesquite', 2), ('lv-palo-verde', 2), ('lv-desert-willow', 1), ('lv-olive', 1.5), ('tpm-oak', 2), ('tpm-savanna-s', 1)],
    'conifer-urban': [('lv-aleppo-pine', 4.5), ('lv-mondell-pine', 3.5), ('lv-italian-cypress', 2)],
    'broadleaf-desert': [('lv-mesquite', 3), ('lv-palo-verde', 2), ('lv-desert-willow', 1)],
    'conifer-mountain': [('tpm-spruce', 1)],
    'broadleaf-mountain': [('tpm-birch', 1)],
    'desert-shrub': [('lv-creosote', 5), ('lv-white-bursage', 3.5), ('lv-mojave-yucca', 0.5)],
    'bare': [('lv-white-bursage', 4), ('lv-dead-bush', 4), ('lv-creosote', 2)],
    'grassy': [('lv-grass', 6), ('lv-dead-bush', 4)],
}
URBAN_UNKNOWN = [('palm', 3), ('broadleaf-urban', 5), ('conifer-urban', 2)]
GENUS = {  # OSM genus / species / taxon (lower-case prefix) -> catalog prefix
    'washingtonia': 'lv-washingtonia', 'phoenix': 'lv-date-palm', 'prosopis': 'lv-mesquite', 'parkinsonia': 'lv-palo-verde', 'cercidium': 'lv-palo-verde',
    'chilopsis': 'lv-desert-willow', 'pinus': 'pine', 'cupressus': 'lv-italian-cypress', 'olea': 'lv-olive', 'yucca brevifolia': 'lv-joshua-tree',
    'yucca': 'lv-mojave-yucca', 'larrea': 'lv-creosote', 'populus tremuloides': 'tpm-birch', 'picea': 'tpm-spruce', 'abies': 'tpm-spruce',
    'citrus': 'lv-orchard-tree', 'punica': 'lv-orchard-tree', 'ficus carica': 'lv-orchard-tree'}
BROAD = {20: (0.006, 'desert-shrub'), 60: (0.003, 'bare'), 30: (0.004, 'grassy')}   # WorldCover class -> (plants per column, pool)
TREE_ROW_SPACING = 6
LANDSCAPED = {'park', 'garden', 'golf_course', 'pitch', 'playground', 'sports_centre', 'stadium', 'dog_park', 'schoolyard', 'track', 'swimming_pool'}  # not nature_reserve
INFERRED_SPACING = 9


# ---------------- catalog
_CAT = None
def catalog():
    global _CAT
    if _CAT is None:
        c = json.load(open(CATALOG)); _CAT = dict(c, by_id={e['id']: e for e in c['entries']})
    return _CAT


def entries(prefix): return [e for e in catalog()['entries'] if e['id'].startswith(prefix)]


def pick(weighted, *key):
    tot = sum(w for _, w in weighted); u = E.det_unit(*key) * tot
    for v, w in weighted:
        u -= w
        if u < 0: return v
    return weighted[-1][0]


def choose_entry(prefix, key, height=None):
    es = sorted(entries(prefix), key=lambda e: e['id'])
    if not es: return None
    if height is not None: return min(es, key=lambda e: (abs(e['dims']['h'] - height), e['id']))
    return es[E.det(*key, 'size') % len(es)]


# ---------------- rotation of a model (multiples of 90 degrees, clockwise seen from above: north -> east)
ROT_SIDE = {'north': 'east', 'east': 'south', 'south': 'west', 'west': 'north'}


def rot_state(s, r):
    if r == 0 or '[' not in s: return s
    name, props = s[:-1].split('[', 1); p = dict(kv.split('=') for kv in props.split(','))
    for _ in range(r):
        q = {}
        for k, v in p.items():
            if k in ROT_SIDE: q[ROT_SIDE[k]] = v
            elif k == 'facing' and v in ROT_SIDE: q[k] = ROT_SIDE[v]
            elif k == 'axis' and v in ('x', 'z'): q[k] = 'z' if v == 'x' else 'x'
            else: q[k] = v
        p = q
    return name + '[' + ','.join(f'{k}={p[k]}' for k in sorted(p)) + ']'


def rot_xz(dx, dz, r):
    for _ in range(r): dx, dz = -dz, dx
    return dx, dz


def role_of(entry, state):
    if entry['class'] == 'groundcover': return 'groundcover'
    if entry['class'] in ('desert-shrub', 'shrub'): return 'shrub'
    return 'canopy' if '_leaves' in state else 'trunk'


def bare(s): return s.split('[', 1)[0].removeprefix('minecraft:') if s else None


def soil_ok(base_block, entry):
    b = bare(base_block)
    if b is None: return False
    sup = entry.get('support')
    if sup: return b in SUPPORT[sup] or (sup == 'dead_bush' and b.endswith('_terracotta'))
    return b in SOIL or b.endswith('_terracotta')


# ---------------- geometry helpers
def affine(src, dst):
    """least-squares 2-D affine src -> dst (exact enough over a few hundred metres of the TPM projection)"""
    A = np.c_[np.asarray(src, float), np.ones(len(src))]
    M, *_ = np.linalg.lstsq(A, np.asarray(dst, float), rcond=None)
    return lambda x, y: (np.c_[np.asarray(x, float), np.asarray(y, float), np.ones(np.size(x))] @ M).T


# ---------------- ESA WorldCover 2021 v200 (bounded HTTP range reads of the COG, cached per 1024-px tile)
def _wc_name(lon, lat):
    la, lo = int(math.floor(lat / 3) * 3), int(math.floor(lon / 3) * 3)
    return f"ESA_WorldCover_10m_2021_v200_{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}_Map", lo, la + 3


def _range(url, a, b, timeout=30):
    last = None
    for i in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'Range': f'bytes={a}-{b}', 'User-Agent': 'Moria-egg/0.5'}), timeout=timeout).read()
        except Exception as ex: last = ex; time.sleep(2 ** i)
    raise last


def _wc_index(cd, name):
    """IFD0 of the COG: (tile offsets, byte counts, tiles across, tile size, pixel size); cached as JSON"""
    p = os.path.join(cd, name + '.ifd.json')
    if os.path.exists(p): return json.load(open(p))
    url = WC_BASE + name + '.tif'; h = _range(url, 0, 65535)
    assert h[:4] == b'II*\x00', 'not a little-endian classic TIFF'
    off = struct.unpack('<I', h[4:8])[0]; n = struct.unpack('<H', h[off:off + 2])[0]; tags = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack('<HHII', h[off + 2 + 12 * i: off + 14 + 12 * i]); tags[tag] = (typ, cnt, val)
    def arr(tag):
        typ, cnt, val = tags[tag]; f, sz = {3: ('H', 2), 4: ('I', 4), 12: ('d', 8)}[typ]
        if cnt * sz <= 4: return list(struct.unpack('<' + f * cnt, struct.pack('<I', val)[:cnt * sz]))
        raw = h[val:val + cnt * sz] if val + cnt * sz <= len(h) else _range(url, val, val + cnt * sz - 1)
        return list(struct.unpack('<' + f * cnt, raw))
    W, T = arr(256)[0], arr(322)[0]
    assert arr(259)[0] == 8 and arr(258)[0] == 8 and arr(277)[0] == 1, 'expected 1-band uint8 DEFLATE'
    assert arr(317)[0] == 1 if 317 in tags else True, 'predictor not supported'
    d = dict(url=url, width=W, tile=T, across=(W + T - 1) // T, offsets=arr(324), counts=arr(325), pixel_deg=arr(33550)[0], tiepoint=arr(33922)[3:5])
    E.write_json(p, d); return d


def worldcover(cache, lons, lats):
    """WorldCover class per (lon, lat); 0 = no data. Returns (classes, provenance)."""
    cd = os.path.join(cache, 'worldcover'); os.makedirs(cd, exist_ok=True)
    lons, lats = np.asarray(lons, float), np.asarray(lats, float); out = np.zeros(lons.shape, np.uint8)
    name, lo0, la1 = _wc_name(float(lons.mean()), float(lats.mean()))
    ix = _wc_index(cd, name); px = ix['pixel_deg']
    assert abs(ix['tiepoint'][0] - lo0) < 1e-9 and abs(ix['tiepoint'][1] - la1) < 1e-9, 'unexpected COG origin'
    col = np.floor((lons - lo0) / px).astype(int); row = np.floor((la1 - lats) / px).astype(int)
    ok = (col >= 0) & (row >= 0) & (col < ix['width']) & (row < ix['width'])
    keys = sorted({(r // ix['tile'], c // ix['tile']) for r, c in zip(row[ok], col[ok])})
    if len(keys) > WC_MAX_TILES: raise RuntimeError(f'WorldCover request needs {len(keys)} COG tiles > {WC_MAX_TILES}')
    fetched = 0
    for tr, tc in keys:
        p = os.path.join(cd, f'{name}_{tr}_{tc}.deflate')
        if not os.path.exists(p):
            k = tr * ix['across'] + tc; o, n = ix['offsets'][k], ix['counts'][k]
            b = _range(ix['url'], o, o + n - 1); assert len(b) == n
            open(p + '.part', 'wb').write(b); os.replace(p + '.part', p); fetched += 1
        t = np.frombuffer(zlib.decompress(open(p, 'rb').read()), np.uint8).reshape(ix['tile'], ix['tile'])
        m = ok & (row // ix['tile'] == tr) & (col // ix['tile'] == tc)
        out[m] = t[row[m] % ix['tile'], col[m] % ix['tile']]
    prov = dict(product='ESA WorldCover 10 m 2021 v200', year=2021, resolution_m=10, url=ix['url'], licence='CC BY 4.0',
                attribution='(c) ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium',
                cog_tiles=len(keys), fetched=fetched)
    pp = os.path.join(cd, 'provenance.json')
    if not os.path.exists(pp): E.write_json(pp, dict(prov, first_fetch=time.strftime('%Y-%m-%dT%H:%M:%S'), cog_tiles=None, fetched=None))
    total = sum(os.path.getsize(os.path.join(cd, f)) for f in os.listdir(cd))
    if total > WC_CACHE_MB * 1e6: prov['warning'] = f'worldcover cache {total / 1e6:.0f} MB > cap {WC_CACHE_MB} MB'
    return out, prov


# ---------------- LiDAR canopy (USGS 3DEP EPT): height above class-2 ground of class-1 multi-return points
def lidar_canopy(ctx, X0, Z0, N, to_ll):
    """CHM (metres, [N,N] block cells from X0,Z0) and crowns [(x, z, height_m, radius)]. Species is never derived."""
    import ept_cached
    from pyproj import Transformer
    from scipy import ndimage as ndi
    cx = [X0, X0 + N, X0 + N, X0, X0 + N / 2]; cz = [Z0, Z0, Z0 + N, Z0 + N, Z0 + N / 2]
    lon, lat = to_ll(cx, cz); mx, my = Transformer.from_crs(4326, 3857, always_xy=True).transform(lon, lat)
    to_blk = affine(np.c_[mx, my], np.c_[cx, cz])
    box3857 = (min(mx), min(my), max(mx), max(my)); errs = []
    for name, year in DATASETS:
        try: r = ept_cached.fetch(EPT + name, box3857, os.path.join(ctx['snow_cache'], 'ept'), max_nodes=600)
        except ept_cached.Limit as ex: errs.append(f'{name}: {ex}'); continue
        if r is None: errs.append(f'{name}: outside dataset'); continue
        if (r['cls'] == 2).sum() < 500: errs.append(f'{name}: too few ground points'); continue
        break
    else: raise RuntimeError('no lidar: ' + '; '.join(errs))
    bx, bz = to_blk(r['x'], r['y']); ix, iz = np.floor(bx - X0).astype(int), np.floor(bz - Z0).astype(int)
    ins = (ix >= 0) & (iz >= 0) & (ix < N) & (iz < N); cls, z, nret = r['cls'], r['z'], r['nret']
    g = ins & (cls == 2); s = np.zeros((N, N)); c = np.zeros((N, N))
    np.add.at(s, (iz[g], ix[g]), z[g]); np.add.at(c, (iz[g], ix[g]), 1)
    if (c > 0).sum() < N * N * 0.2: raise RuntimeError(f'{name}: ground covers only {(c > 0).mean():.0%} of the cells')
    ground = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    idx = ndi.distance_transform_edt(np.isnan(ground), return_distances=False, return_indices=True)
    ground = ground[tuple(idx)]                                            # holes (under buildings, crowns): nearest ground cell
    one = ins & (cls == 1); multi = one & (nret > 1)
    hag = z - ground[np.clip(iz, 0, N - 1), np.clip(ix, 0, N - 1)]
    v = multi & (hag > 0.5) & (hag < 35)
    chm = np.zeros((N, N)); cnt = np.zeros((N, N)); n1 = np.zeros((N, N))
    np.maximum.at(chm, (iz[v], ix[v]), hag[v]); np.add.at(cnt, (iz[v], ix[v]), 1); np.add.at(n1, (iz[one], ix[one]), 1)
    canopy = (chm >= 1.0) & (cnt >= 2) & (cnt >= 0.4 * n1)                # mostly multi-return: vegetation, not a roof
    chm = np.where(canopy, chm, 0.0)
    peak = (chm == ndi.maximum_filter(chm, size=5)) & (chm >= 1.5)
    crowns = []
    for pz, px in zip(*np.nonzero(peak)):
        rad = 0
        for rr in range(1, 7):
            ring = [(px + dx, pz + dz) for dx in range(-rr, rr + 1) for dz in range(-rr, rr + 1) if max(abs(dx), abs(dz)) == rr]
            ring = [(a, b) for a, b in ring if 0 <= a < N and 0 <= b < N]
            if ring and sum(canopy[b, a] for a, b in ring) >= 0.5 * len(ring): rad = rr
            else: break
        crowns.append((int(X0 + px), int(Z0 + pz), float(round(chm[pz, px], 1)), rad))
    src = dict(dataset=name, year=year, ept=EPT + name, points=int(ins.sum()), ground_points=int(g.sum()), multi_return_class1=int(multi.sum()),
               nodes=r['nodes'], fetched=r['fetched'], reused=r['reused'], note='class 1 includes vegetation; canopy = multi-return class-1 height above class-2 ground')
    return chm, crowns, src, errs


# ---------------- species from OSM tags
def mapped_species(tags, fid, seed, habitat):
    """(catalog prefix, species label, how) for an OSM tree"""
    for key in ('species', 'genus', 'taxon', 'species:en', 'taxon:en'):
        v = (tags.get(key) or '').strip().lower()
        if not v: continue
        for k in sorted(GENUS, key=len, reverse=True):
            if v.startswith(k):
                pre = GENUS[k]
                if pre == 'pine': pre = 'lv-mondell-pine' if 'eldarica' in v else 'lv-aleppo-pine'
                return pre, tags.get(key), f'{key} tag'
    lt = tags.get('leaf_type'); hab = 'mountain' if habitat == 'mountain' else 'urban'
    if lt == 'palm': return pick(POOL['palm'], fid, seed, 'sp'), 'palm (species not tagged)', 'leaf_type=palm'
    if lt == 'needleleaved':
        return pick(POOL['conifer-mountain' if hab == 'mountain' else 'conifer-urban'], fid, seed, 'sp'), 'conifer (species not tagged)', 'leaf_type=needleleaved'
    if lt in ('broadleaved', 'leafless'):
        return pick(POOL['broadleaf-mountain' if hab == 'mountain' else 'broadleaf-urban'], fid, seed, 'sp'), 'broadleaf (species not tagged)', f'leaf_type={lt}'
    if hab == 'mountain': return pick(POOL['conifer-mountain'], fid, seed, 'sp'), 'unknown (mountain default)', 'untagged'
    grp = pick(URBAN_UNKNOWN, fid, seed, 'grp')
    return pick(POOL[grp], fid, seed, 'sp'), 'unknown (Las Vegas urban planting mix)', 'untagged'


def _height(tags):
    try: return float(str(tags.get('height', '')).split()[0].replace('m', ''))
    except Exception: return None


# ---------------- plan
def plan(ctx):
    t0 = time.time(); T = {}
    tx, tz, seed, geo = ctx['tx'], ctx['tz'], ctx.get('seed', 1), ctx['geo']
    b = E.tile_bounds(tx, tz)['blocks']; x0, z0 = b[0], b[1]; R = E.RING
    policy = dict(dict(inferred=False, measured=True, broad=True), **(ctx.get('vegetation_policy') or {}))
    by = np.array([[(-10 ** 9 if v is None else v) for v in r] for r in ctx['base_y']], dtype=np.int64)
    prot = np.asarray(ctx['protect'], dtype=np.int64); bblock, bcover = ctx['base_block'], ctx['base_cover']
    reasons, sources, stats = [], dict(catalog=dict(version=catalog()['catalog_version'], file='prep/egg_assets/vegetation_catalog.json')), {}
    incomplete = []                              # tiers skipped for provider/coverage reasons -> plan 'complete': false (M4)
    terrain_cols = set(ctx.get('terrain_cols') or ())   # columns whose surface this tile's terrain plan changes (M6)
    max_fill = int((ctx.get('limits') or {}).get('max_fill', 2))
    protect_ring = ctx.get('protect_ring')         # optional {(x, z): bits} for the ring columns
    if ctx.get('landcover') is None:
        return E.plan('vegetation', tx, tz, E.NO_COVERAGE, reasons=['landcover index missing: mapped trees unknown (never treated as "no trees")'], sources=sources, complete=False)
    feats = ctx['landcover'](['tree', 'tree_row', 'natural', 'landuse', 'leisure', 'water'])
    if feats is None:
        return E.plan('vegetation', tx, tz, E.NO_COVERAGE, reasons=['tile outside the landcover index coverage'], sources=sources, complete=False)
    sources['osm'] = dict(index=E.LANDCOVER_INDEX, licence='ODbL, (c) OpenStreetMap contributors')

    # block <-> lon/lat (exact TPM projection at the corners, affine in between: the ring box is ~0.2 km)
    EX0, EZ0, EN = x0 - R - LIDAR_MARGIN, z0 - R - LIDAR_MARGIN, E.TILE + 2 * (R + LIDAR_MARGIN)
    cb = [(EX0, EZ0), (EX0 + EN, EZ0), (EX0 + EN, EZ0 + EN), (EX0, EZ0 + EN), (EX0 + EN / 2, EZ0 + EN / 2)]
    ll = geo.inv(cb); to_ll = affine(cb, ll)
    T['projection'] = round(time.time() - t0, 3)

    def inside(x, z): return x0 <= x < x0 + 64 and z0 <= z < z0 + 64
    def in_ext(x, z): return x0 - R <= x < x0 + 64 + R and z0 - R <= z < z0 + 64 + R

    # ---- neighbour baseline for anchors in the ring: the same base_y the owning tile will use (read-only), else TPM floor(height)
    nb_cache = {}
    def ring_column(x, z):
        if ctx.get('ring_column'): return ctx['ring_column'](x, z)        # optional hook: (base_y, base_block, base_cover) of a ring column
        k = E.tile_of_block(x, z)
        if k not in nb_cache:
            try:
                import egg_provenance
                nb_cache[k] = egg_provenance.baseline_columns(*k, ctx.get('baseline_world') or E.BASELINE_WORLD, ctx.get('captured_dir'))
            except Exception as ex:
                nb_cache[k] = dict(error=f'{type(ex).__name__}: {ex}')
        d = nb_cache[k]
        if 'error' in d: return None
        nb = E.tile_bounds(*k)['blocks']; r_, c_ = z - nb[1], x - nb[0]
        return d['base_y'][r_][c_], d['base_block'][r_][c_], d['base_cover'][r_][c_]

    # ---- landcover polygons in block space (habitat / eligibility outside the tile and for broad/inferred tiers)
    from shapely.geometry import Point, box as sbox
    from shapely import contains_xy
    from shapely.strtree import STRtree
    ext_box = sbox(x0 - R, z0 - R, x0 + 64 + R, z0 + 64 + R)
    DEV = {'residential', 'commercial', 'industrial', 'retail', 'construction', 'railway', 'military', 'garages', 'education', 'institutional',
           'religious', 'landfill', 'quarry', 'brownfield', 'grass', 'recreation_ground', 'cemetery', 'village_green', 'flowerbed', 'farmland',
           'orchard', 'vineyard', 'meadow', 'plant_nursery'}
    no_wild, woods, scrub = [], [], []
    trees, rows = [], []
    for f in feats:
        k, t = f['kind'], f['tags']
        if k == 'tree': trees.append(f); continue
        if k == 'tree_row': rows.append(f); continue
        try: g = E.to_block(geo, f['geom'])
        except Exception: continue
        if not g.intersects(ext_box): continue
        if g.geom_type in ('LineString', 'MultiLineString'): g = g.buffer(3)
        if k == 'water' or (k == 'leisure' and t.get('leisure') in LANDSCAPED) or (k == 'landuse' and t.get('landuse') in DEV) or t.get('natural') in ('water', 'wetland', 'beach'):
            no_wild.append(g)
        elif t.get('natural') == 'wood' or t.get('landuse') == 'forest': woods.append(g)
        elif t.get('natural') == 'scrub': scrub.append(g)
    nw_tree = STRtree(no_wild) if no_wild else None
    def wild_ok(x, z):        # outside water / leisure / developed / landscaped polygons
        if nw_tree is None: return True
        p = Point(x + 0.5, z + 0.5)
        return not any(no_wild[i].contains(p) for i in nw_tree.query(p))
    T['landcover'] = round(time.time() - t0 - sum(T.values()), 3)

    # ---- WorldCover (broad context only) over tile + ring
    wc = None
    if policy['broad']:
        try:
            gx, gz = np.meshgrid(np.arange(x0 - R, x0 + 64 + R) + 0.5, np.arange(z0 - R, z0 + 64 + R) + 0.5)
            lo, la = to_ll(gx.ravel(), gz.ravel())
            cls, sources['worldcover'] = worldcover(ctx['cache'], lo, la); wc = cls.reshape(gx.shape)
            stats['worldcover_classes'] = {WC_CLASSES.get(int(k), str(k)): int(v) for k, v in zip(*np.unique(wc[R:R + 64, R:R + 64], return_counts=True))}
        except Exception as ex:
            reasons.append(f'WorldCover unavailable ({type(ex).__name__}: {str(ex)[:120]}): broad ground-cover tier skipped'); sources['worldcover'] = dict(error=str(ex)[:200]); incomplete.append('worldcover')
    T['worldcover'] = round(time.time() - t0 - sum(T.values()), 3)

    # ---- LiDAR canopy
    chm = None; crowns = []
    try:
        chm, crowns, sources['lidar'], lerr = lidar_canopy(ctx, EX0, EZ0, EN, to_ll)
        if lerr: sources['lidar']['skipped'] = lerr
    except Exception as ex:
        reasons.append(f'lidar unavailable ({type(ex).__name__}: {str(ex)[:120]}): measured tier skipped, mapped trees use default sizes'); sources['lidar'] = dict(error=str(ex)[:200])
        if policy['measured']: incomplete.append('lidar')
    T['lidar'] = round(time.time() - t0 - sum(T.values()), 3)

    def chm_max(x, z, r=2):
        if chm is None: return None
        i, j = x - EX0, z - EZ0
        w = chm[max(0, j - r):j + r + 1, max(0, i - r):i + r + 1]
        return float(w.max()) if w.size and w.max() >= 2 else None

    def habitat(x, z, y):
        if y is not None and y > MOUNTAIN_Y: return 'mountain'
        if wc is not None and in_ext(x, z) and int(wc[z - (z0 - R), x - (x0 - R)]) in (20, 30, 60) and wild_ok(x, z): return 'desert'
        return 'irrigated-urban'

    # ---- candidates: (fid, tier, x, z, entry, extra)
    cand = []
    pts = [(f['geom'].x, f['geom'].y) for f in trees]
    for f, (x, z) in zip(trees, geo.fwd(pts) if pts else []):
        x, z = int(math.floor(x)), int(math.floor(z)); t = f['tags']
        if not in_ext(x, z): continue
        if str(t.get('level', '0')).strip() not in ('0', '') or t.get('location') in ('roof', 'rooftop', 'indoor'):
            stats['skipped_not_ground_level'] = stats.get('skipped_not_ground_level', 0) + 1; continue
        cand.append(('osm:' + f['id'], 'mapped', x, z, t, f"OSM natural=tree {f['id']}"))
    for f in rows:
        try: g = E.to_block(geo, f['geom'])
        except Exception: continue
        if g.is_empty or not g.intersects(ext_box.buffer(8)): continue
        L = g.length; k = 0; d = TREE_ROW_SPACING / 2
        while d <= L:
            p = g.interpolate(d); x, z = int(math.floor(p.x)), int(math.floor(p.y))
            if in_ext(x, z): cand.append((f"osm:{f['id']}#{k}", 'mapped', x, z, f['tags'], f"OSM natural=tree_row {f['id']}, tree {k} (spacing {TREE_ROW_SPACING} m interpolated)"))
            d += TREE_ROW_SPACING; k += 1
    mapped_xz = [(c[2], c[3]) for c in cand]
    if policy['measured']:
        for x, z, h, rad in crowns:
            if not in_ext(x, z) or any(abs(x - a) <= 3 and abs(z - c) <= 3 for a, c in mapped_xz): continue
            cand.append((f'lidar:{x}_{z}', 'measured', x, z, dict(h=h, r=rad), f'LiDAR canopy (species unknown): {h} m high, crown radius {rad}'))
    if policy['inferred'] and woods:
        for gw in woods:
            mnx, mnz, mxx, mxz = gw.intersection(ext_box).bounds if gw.intersects(ext_box) else (0, 0, -1, -1)
            for gx_ in range(int(mnx) // INFERRED_SPACING * INFERRED_SPACING, int(mxx) + 1, INFERRED_SPACING):
                for gz_ in range(int(mnz) // INFERRED_SPACING * INFERRED_SPACING, int(mxz) + 1, INFERRED_SPACING):
                    x = gx_ + E.det('inf', gx_, gz_, seed) % 5 - 2; z = gz_ + E.det('inf', gz_, gx_, seed) % 5 - 2
                    if in_ext(x, z) and gw.contains(Point(x + 0.5, z + 0.5)):
                        cand.append((f'inferred:{x}_{z}', 'inferred', x, z, {}, 'inferred from an OSM wood/forest polygon (policy on)'))
    if policy['broad'] and wc is not None:
        scrub_mask = np.zeros(wc.shape, bool)
        if scrub:
            gx, gz = np.meshgrid(np.arange(x0 - R, x0 + 64 + R) + 0.5, np.arange(z0 - R, z0 + 64 + R) + 0.5)
            for gs in scrub: scrub_mask |= contains_xy(gs, gx, gz)
        for j in range(wc.shape[0]):
            for i in range(wc.shape[1]):
                c = 20 if scrub_mask[j, i] else int(wc[j, i])
                if c not in BROAD: continue
                x, z = x0 - R + i, z0 - R + j; p, pool = BROAD[c]
                if E.det_unit('broad', x, z, seed) >= p: continue
                cand.append((f'broad:{x}_{z}', 'broad', x, z, dict(pool=pool, wc=c), f"WorldCover 2021 '{WC_CLASSES[c]}'" + (' / OSM natural=scrub' if scrub_mask[j, i] else '')))
    cand.sort(key=lambda c: ({'mapped': 0, 'measured': 1, 'inferred': 2, 'broad': 3}[c[1]], c[0]))
    T['candidates'] = round(time.time() - t0 - sum(T.values()), 3)

    # ---- realise candidates: eligibility, model, rotation, clip
    occupied = {}                                   # (x,y,z) -> fid ; whole plants, also outside the tile (for spacing/overlap)
    anchors = []
    ops, features = [], []
    drop = dict(terrain_changes=0, below_ground=0, protected=0, base_cover=0, no_baseline=0, overlap=0, outside_tile=0)
    skip = dict(terrain_changes=0, ring_protected=0, protected=0, base_cover=0, no_baseline=0, soil=0, spacing=0, footprint_covered=0, habitat=0, wild=0)
    tiers = dict(mapped=0, measured=0, broad=0, inferred=0)
    def col(x, z):
        """(base_y, base_block, base_cover, protect or None if unknown) of any column of tile + ring"""
        if inside(x, z):
            r_, c_ = z - z0, x - x0; v = int(by[r_, c_])
            return (None if v == -10 ** 9 else v), bblock[r_][c_], bcover[r_][c_], int(prot[r_, c_])
        rc = ring_column(x, z)
        pr_ = protect_ring.get((x, z)) if protect_ring is not None else None
        return (rc[0], rc[1], rc[2], pr_) if rc else (None, None, None, None)

    for fid, tier, x, z, info, evidence in cand:
        y0, bb, bc, pr = col(x, z)
        if y0 is None: skip['no_baseline'] += 1; continue
        if bc: skip['base_cover'] += 1; continue
        mapped = tier == 'mapped'
        if pr is not None and (pr & ~(LANDSCAPE_OK if mapped else 0)): skip['protected' if inside(x, z) else 'ring_protected'] += 1; continue
        if (x, z) in terrain_cols: skip['terrain_changes'] += 1; continue      # terrain changes this column; replant after terrain
        if pr is None and not mapped and not wild_ok(x, z): skip['wild'] += 1; continue
        hab = habitat(x, z, y0)
        # model choice
        if mapped:
            pre, species, how = mapped_species(info, fid, seed, hab)
            h = _height(info) or chm_max(x, z)
            entry = choose_entry(pre, (fid, seed), round(h) if h else None) if pre else None
            conf = 'mapped'; label = f'{species} [{how}]' + (f', height {h:.0f} m ({"tag" if _height(info) else "lidar"})' if h else '')
        elif tier == 'measured':
            h, rad = info['h'], info['r']
            if h < 3: pre = pick(POOL['desert-shrub'], fid, seed, 'sp') if hab == 'desert' else None
            elif hab == 'mountain': pre = 'tpm-spruce'
            elif h >= 7 and rad <= 2: pre = 'lv-washingtonia'                  # tall + narrow crown: palm-shaped (shape only, not species)
            else: pre = pick(POOL['broadleaf-desert' if hab == 'desert' else 'broadleaf-urban'], fid, seed, 'sp')
            if pre is None: skip['habitat'] += 1; continue
            entry = choose_entry(pre, (fid, seed), round(h)); conf = 'measured'
            label = f'LiDAR canopy (species unknown), {h} m, crown r {rad}; model {pre} chosen by shape/habitat'
        elif tier == 'inferred':
            entry = choose_entry(pick(POOL['broadleaf-desert' if hab == 'desert' else 'broadleaf-urban'], fid, seed, 'sp'), (fid, seed)); conf = 'inferred'
            label = 'inferred tree (wood/forest polygon)'
        else:
            if hab != 'desert' or (pr is None and not wild_ok(x, z)): skip['habitat'] += 1; continue
            entry = choose_entry(pick(POOL[info['pool']], fid, seed, 'sp'), (fid, seed)); conf = 'inferred'
            label = f"{entry['label'] if entry else '?'} (broad: {evidence})"
        if entry is None: skip['habitat'] += 1; continue
        if not soil_ok(bb, entry): skip['soil'] += 1; continue
        r = E.det(fid, seed, 'rot') % 4
        # spacing: plants never share an anchor neighbourhood (mapped trees always kept: they are evidence)
        sp = 1 if entry['class'] == 'groundcover' else 2 if entry['class'] in ('desert-shrub', 'shrub') else 3
        if not mapped and any(abs(x - a) < sp + ar and abs(z - c) < sp + ar for a, c, ar in anchors): skip['spacing'] += 1; continue
        ay = y0 + 1
        cells = []
        for dx, dy, dz, s in entry['blocks']:
            rx, rz = rot_xz(dx, dz, r); cells.append((x + rx, ay + dy, z + rz, rot_state(s, r), role_of(entry, s)))
        # existing vegetation inside the footprint (in-tile columns) -> keep it, skip this plant
        if any(inside(cx, cz) and bcover[cz - z0][cx - x0] and any(('_log' in v or '_leaves' in v) for v in bcover[cz - z0][cx - x0])
               for cx, _, cz, _, _ in cells):
            skip['footprint_covered'] += 1; continue
        anchors.append((x, z, entry['crown_radius'] if entry['class'] not in ('groundcover',) else 0))
        own = inside(x, z); emitted = []; partial = any(not inside(cx, cz) for cx, _, cz, _, _ in cells)
        for cx, cy, cz, s, role in cells:
            key = (cx, cy, cz)
            if key in occupied: drop['overlap'] += 1; continue
            occupied[key] = fid
            if not inside(cx, cz): drop['outside_tile'] += 1; continue
            yy, _, cov, pp = col(cx, cz)
            if yy is None: drop['no_baseline'] += 1; continue
            if cy <= yy: drop['below_ground'] += 1; continue
            if (cx, cz) in terrain_cols and cy <= yy + max_fill + 1: drop['terrain_changes'] += 1; continue   # surface may rise there
            if pp & ~(LANDSCAPE_OK if mapped else 0): drop['protected'] += 1; continue   # roads, sidewalks, buildings, water, Strip: no canopy over them
            if cov: drop['base_cover'] += 1; continue
            emitted.append([cx, cy, cz, 'air', s, fid, role if own else role + '-remote'])
        if own and not any(o[0] == x and o[1] == ay and o[2] == z for o in emitted):
            for o in emitted: occupied.pop((o[0], o[1], o[2]), None)
            continue                                                       # anchor cell itself not placeable: no plant without its base
        if not emitted: continue
        ops += emitted; tiers[tier] += 1
        features.append(dict(id=fid, kind='tree' if entry['class'] in ('palm', 'broadleaf', 'conifer', 'crop-tree') else entry['class'],
                             info=label, partial=partial, anchor=[x, ay, z], anchor_block=rot_state(next(bl[3] for bl in entry['blocks'] if bl[:3] == [0, 0, 0]), r),
                             anchor_in_tile=own, evidence=evidence, confidence=conf, tier=tier, asset=entry['id'], provenance=entry['provenance'],
                             rotation=r * 90, habitat=hab, ops=len(emitted)))
    T['realise'] = round(time.time() - t0 - sum(T.values()), 3)
    ops.sort(key=lambda o: (0 if o[6].startswith('trunk') else 1, o[1], o[0], o[2]))       # bottom-up, trunks before leaves
    stats.update(tiers=tiers, candidates=len(cand), ops=len(ops), remote_ops=sum(o[6].endswith('-remote') for o in ops),
                 partial_features=sum(f['partial'] for f in features), cells_dropped=drop, plants_skipped=skip, lidar_crowns=len(crowns), timings_s=T)
    if not trees and not rows: reasons.append('no OSM trees or tree rows mapped in tile + ring (extract date: see sources.osm)')
    status = E.READY if ops else E.NOTHING
    if not ops: reasons.append('nothing eligible to plant (see stats.plants_skipped)')
    if skip['terrain_changes']: reasons.append(f"{skip['terrain_changes']} plant(s) skipped: terrain changes this column; replant after terrain")
    return E.plan('vegetation', tx, tz, status, ops=ops, features=features, reasons=reasons, sources=sources, stats=stats, complete=not incomplete,
                  incomplete_tiers=incomplete)


# ---------------- self-check (no server): real baseline columns + protection, plan two adjacent tiles, verify the contract
def _standin(geo, tx, tz):
    """STAND-IN baseline (self-check only, for tiles outside the approved baseline world): base_y = floor(TPM height) (TPM puts the
    ground top there), base_block = minecraft:sand (the baseline surface of the Las Vegas pilot), no cover. Never used by egg_prep."""
    x0, z0 = E.tile_bounds(tx, tz)['blocks'][:2]
    h = geo.heights(geo.inv([(x0 + i + 0.5, z0 + j + 0.5) for j in range(64) for i in range(64)]))
    by = [[int(math.floor(h[j * 64 + i])) for i in range(64)] for j in range(64)]
    return dict(base_y=by, base_block=[['minecraft:sand'] * 64 for _ in range(64)], base_cover=[[None] * 64 for _ in range(64)], columns_known=4096)


def _ctx(geo, tx, tz, seed=1, cache=r'C:\BTE-Migration\shelltool\test-cache-v05\egg', snow=r'C:\BTE-Migration\shelltool\test-cache-v05', drop_strip=False, standin=False):
    """ctx as egg_prep builds it (baseline via egg_provenance, OSM protection, landcover bits). drop_strip=True clears the Strip bit
    for a MECHANICS-ONLY test (egg_prep refuses Strip tiles before any generator runs; such plans are never applied)."""
    import egg_provenance
    base = _standin(geo, tx, tz) if standin else egg_provenance.baseline_columns(tx, tz)
    mask, _ = E.protection_mask(geo, tx, tz)
    llb = E.tile_lonlat_box(geo, tx, tz)
    def lc(kinds=None): return E.query_index(E.LANDCOVER_INDEX, llb, set(kinds) if kinds else None)[0]
    # landcover protection bits: same rules as egg_prep.landcover_bits (egg_prep runs on import, so the rule is restated here for the test)
    from shapely.geometry import box as sbox
    x0, z0 = E.tile_bounds(tx, tz)['blocks'][:2]
    xs, zs = np.meshgrid(np.arange(x0, x0 + 64) + 0.5, np.arange(z0, z0 + 64) + 0.5)
    from shapely import contains_xy
    LAND = {'park', 'garden', 'golf_course', 'pitch', 'playground', 'sports_centre', 'stadium', 'dog_park', 'schoolyard', 'track'}
    for f in lc() or []:
        t, k = f['tags'], f['kind']; bit = None
        if k == 'water' or t.get('natural') == 'water' or t.get('landuse') in ('reservoir', 'basin') or 'waterway' in t: bit = E.P_WATER
        elif k == 'leisure' and t.get('leisure') in LAND: bit = E.P_LANDSCAPED
        elif k == 'landuse' and t.get('landuse') in ('grass', 'recreation_ground', 'cemetery', 'village_green', 'flowerbed'): bit = E.P_LANDSCAPED
        elif k == 'landuse' and t.get('landuse') in ('residential', 'commercial', 'industrial', 'retail', 'construction', 'railway', 'military', 'garages',
                                                   'education', 'institutional', 'religious', 'landfill', 'quarry', 'brownfield'): bit = E.P_DEVELOPED
        if bit is None: continue
        g = E.to_block(geo, f['geom'])
        if g.geom_type in ('LineString', 'MultiLineString'): g = g.buffer(3)
        if g.intersects(sbox(x0, z0, x0 + 64, z0 + 64)): mask[contains_xy(g, xs, zs)] |= bit
    if drop_strip: mask &= ~np.uint8(E.P_STRIP)
    extra = {}
    if standin:
        tiles = {}
        def ring_column(x, z):
            k = E.tile_of_block(x, z)
            if k not in tiles: tiles[k] = _standin(geo, *k)
            nb = E.tile_bounds(*k)['blocks']
            return tiles[k]['base_y'][z - nb[1]][x - nb[0]], 'minecraft:sand', None
        extra['ring_column'] = ring_column
    return dict(**extra, tx=tx, tz=tz, bounds=E.tile_bounds(tx, tz), seed=seed, geo=geo, cache=cache, snow_cache=snow, limits=dict(max_cut=2, max_fill=2, max_ops=12000),
                base_y=base['base_y'], base_block=base['base_block'], base_cover=base['base_cover'], protect=mask, landcover=lc), base['columns_known']


def _check(p, ctx):
    tx, tz = ctx['tx'], ctx['tz']
    assert all(E.in_tile(tx, tz, o[0], o[2]) for o in p['ops'])                 # also asserted by E.plan
    assert len({(o[0], o[1], o[2]) for o in p['ops']}) == len(p['ops']), 'duplicate cells'
    assert all(o[3] == 'air' for o in p['ops'])
    assert all('persistent=true' in o[4] for o in p['ops'] if '_leaves' in o[4])
    by = ctx['base_y']; x0, z0 = ctx['bounds']['blocks'][:2]
    for o in p['ops']:
        v = by[o[2] - z0][o[0] - x0]; assert v is not None and o[1] > v, ('below ground', o)
        pr = int(ctx['protect'][o[2] - z0][o[0] - x0]); assert pr & ~E.P_LANDSCAPED == 0, ('protected column', o, pr)
        assert not ctx['base_cover'][o[2] - z0][o[0] - x0], ('over existing cover', o)
    F = {f['id']: f for f in p['features']}
    for o in p['ops']:
        f = F[o[5]]
        assert o[6].endswith('-remote') == (not f['anchor_in_tile']), (o, f)
        if f['tier'] != 'mapped': assert int(ctx['protect'][o[2] - z0][o[0] - x0]) == 0, ('non-mapped plant in a protected column', o)
    for f in p['features']:
        if f['anchor_in_tile']:
            assert any(o[5] == f['id'] and [o[0], o[1], o[2]] == f['anchor'] and o[4] == f['anchor_block'] and o[6] in ('trunk', 'shrub', 'groundcover') for o in p['ops']), f
    return F


if __name__ == '__main__':
    import collections
    from geo import Geo
    a = sys.argv
    geo = Geo(); seed = 1
    DESERT = (-190052, -85154)                                       # Red Rock Canyon NCA edge, WorldCover 'bare / sparse'
    # (tile, neighbour, drop_strip, standin)
    TESTS = [((-189924, -85137), (-189925, -85137), False, True),     # Summerlin streets, 17 + 9 OSM trees, 5 within 4 blocks of the shared edge (STAND-IN base_y)
             ((-189994, -85164), (-189994, -85163), False, False),    # Summerlin pilot baseline (developed: nothing_to_do expected)
             (DESERT, (DESERT[0] + 1, DESERT[1]), False, True)]       # undeveloped desert outside the baseline: STAND-IN base_y
    if '--tiles' in a:
        v = list(map(int, a[a.index('--tiles') + 1].split(','))); TESTS = [((v[0], v[1]), (v[2], v[3]), '--drop-strip' in a, '--standin' in a)]
    for t1, t2, ds, si in TESTS:
        plans = {}
        for t in (t1, t2):
            ctx, known = _ctx(geo, *t, seed=seed, drop_strip=ds, standin=si)
            tm = time.time(); p = plan(ctx); dt = time.time() - tm
            F = _check(p, ctx); plans[t] = (p, ctx)
            print(f'tile {t} baseline cols {known} status {p["status"]} ops {len(p["ops"])} features {len(p["features"])} '
                  f'partial {p["stats"]["partial_features"]} remote {p["stats"]["remote_ops"]} {dt:.1f}s', p['stats']['tiers'], p['reasons'][:2])
            print('   timings', p['stats']['timings_s'], 'skipped', p['stats']['plants_skipped'], 'dropped', p['stats']['cells_dropped'])
            print('   assets', collections.Counter(f['asset'] for f in p['features']).most_common(8))
            # determinism: same seed -> identical ops; another seed -> (generally) different choices
            p2 = plan(ctx); assert p2['ops'] == p['ops'] and [f['id'] for f in p2['features']] == [f['id'] for f in p['features']], 'not deterministic'
        (pa, ca), (pb, cb) = plans[t1], plans[t2]
        # no trunk written by two tiles; remote canopy anchors = trunk the owning tile plans
        trunks = collections.Counter(f['id'] for p_ in (pa, pb) for f in p_['features'] if f['anchor_in_tile'])
        assert all(n == 1 for n in trunks.values()), 'feature anchored in two tiles'
        assert not ({(o[0], o[1], o[2]) for o in pa['ops']} & {(o[0], o[1], o[2]) for o in pb['ops']})
        own = {f['id']: f for p_ in (pa, pb) for f in p_['features'] if f['anchor_in_tile']}
        cross = 0
        for p_ in (pa, pb):
            for f in p_['features']:
                if f['anchor_in_tile'] or f['id'] not in own: continue
                assert f['anchor'] == own[f['id']]['anchor'] and f['anchor_block'] == own[f['id']]['anchor_block'] and f['asset'] == own[f['id']]['asset'], (f, own[f['id']])
                cross += 1
        print(f'   pair {t1}/{t2}: features crossing the shared edge with matching anchors: {cross}')
        for f in [f for p_ in (pa, pb) for f in p_['features'] if f['partial']][:3]:
            print('   partial', f['id'], f['asset'], 'anchor', f['anchor'], 'in tile' if f['anchor_in_tile'] else 'REMOTE', f['ops'], 'ops')
        assert pa['complete'] == (not pa['incomplete_tiers'])
        print(f"   complete: {pa['complete']} / {pb['complete']}", pa['incomplete_tiers'], pb['incomplete_tiers'])
        if '--tiles' in a or not (pa['ops'] and pb['stats']['remote_ops']): continue
        # M6: a column the terrain plan changes gets no plant
        f0 = next(f for f in pa['features'] if f['anchor_in_tile'])
        q = plan(dict(ca, terrain_cols={(f0['anchor'][0], f0['anchor'][2])}))
        assert f0['id'] not in {f['id'] for f in q['features']} and q['stats']['plants_skipped']['terrain_changes'] >= 1, 'terrain_cols ignored'
        # ring protection: a remote tree whose trunk column the ring mask protects emits no remote ops
        fr = next(f for f in pb['features'] if not f['anchor_in_tile'])
        q = plan(dict(cb, protect_ring={(fr['anchor'][0], fr['anchor'][2]): E.P_ROAD}))
        assert fr['id'] not in {f['id'] for f in q['features']}, 'protect_ring ignored'
        print('   terrain_cols and protect_ring checks ok')
    geo.close(); print('egg_vegetation ok')
