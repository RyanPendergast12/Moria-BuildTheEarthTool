"""egg_agriculture.py - Egg agriculture generator (v0.5). plan(ctx) -> egg_common.plan(...) (ARCHITECTURE.md sections 4, 9).
Spec: docs/egg/AGRICULTURE-SPEC.md. Evidence: OSM polygons + tags first; USDA CDL (CropScape clip) as supporting evidence only.

Every field is planned whole in block space with a field-global origin and row direction (vine_row_orientation / numeric
direction tag, else the longest edge of the minimum rotated rectangle -> 'inferred'), so the pattern of a cell depends only
on (field, x, z): adjoining tiles agree in any order. Ops are then clipped to the tile and to eligible columns
(protect == 0, base_y known, base_cover None), inside the field polygon minus a 1-block edge margin.
No water, no gamerules, no blanket clearing: surface ops replace baseline terrain (expect 'base'), plants go into air."""
import hashlib, json, math, os, re, time
import numpy as np
from shapely.geometry import box, Point
from shapely.ops import unary_union
from shapely import contains_xy, minimum_rotated_rectangle
import egg_common as ec

CONTENT = 'agriculture'
AG = ('farmland', 'orchard', 'vineyard', 'meadow')
NOT_FIELD = {'farmyard'}                                    # farm buildings and yards: reported, never cropped
# areas that are never farmed even if they overlap a farm polygon (golf courses are NEVER farms)
EXCLUDE_LEISURE = {'golf_course', 'park', 'garden', 'pitch', 'playground', 'nature_reserve', 'dog_park', 'recreation_ground', 'stadium', 'track'}
EXCLUDE_LANDUSE = {'grass', 'recreation_ground', 'cemetery', 'residential', 'farmyard'}
EDGE = 1.0                                                  # edge margin (blocks) inside the field outline
ROW = 4                                                     # row period (blocks) across the row direction; guidebook p5 bands
CROP_BLOCK = {                                              # max age: they never grow (stable even if randomTickSpeed is raised)
    'wheat': 'minecraft:wheat[age=7]', 'barley': 'minecraft:wheat[age=7]', 'oats': 'minecraft:wheat[age=7]', 'rye': 'minecraft:wheat[age=7]',
    'cereal': 'minecraft:wheat[age=7]', 'grain': 'minecraft:wheat[age=7]', 'triticale': 'minecraft:wheat[age=7]',
    'potato': 'minecraft:potatoes[age=7]', 'potatoes': 'minecraft:potatoes[age=7]',
    'carrot': 'minecraft:carrots[age=7]', 'carrots': 'minecraft:carrots[age=7]',
    'beet': 'minecraft:beetroots[age=3]', 'sugar_beet': 'minecraft:beetroots[age=3]', 'beetroot': 'minecraft:beetroots[age=3]'}
GREEN_CROPS = {'alfalfa', 'hay', 'grass', 'fodder', 'clover', 'pasture', 'sod'}
FARMLAND = 'minecraft:farmland[moisture=0]'                 # dry: irrigation is never inferred; with a crop above it never reverts
FURROW = 'minecraft:coarse_dirt'
GENERIC = ('minecraft:coarse_dirt', 'minecraft:coarse_dirt', 'minecraft:dirt', 'minecraft:dirt')   # tillage bands, no crop claimed
VINE = 'minecraft:jungle_leaves[distance=7,persistent=true]'  # guidebook p4 "Grapes: 18:3" = jungle leaves
ORCH_LOG, ORCH_LEAF = 'minecraft:oak_log[axis=y]', 'minecraft:oak_leaves[distance=7,persistent=true]'
MEADOW_ON = {'minecraft:grass_block', 'minecraft:dirt', 'minecraft:coarse_dirt', 'minecraft:podzol'}
MEADOW_PLANTS = (('minecraft:grass', 0.22), ('minecraft:dandelion', 0.015), ('minecraft:poppy', 0.01), ('minecraft:cornflower', 0.005))
ORCH_STEP = 5                                               # tree spacing along and across rows (blocks = metres)
CDL_CACHE = r'C:\BTE-Migration\shelltool\test-cache-v05\egg\cdl'
CDL_YEAR = 2025                                             # latest CropScape year (2026 refused by the service on 2026-10-29)
CDL_NAMES = {0: 'Background', 1: 'Corn', 2: 'Cotton', 4: 'Sorghum', 5: 'Soybeans', 12: 'Sweet Corn', 21: 'Barley', 23: 'Spring Wheat',
             24: 'Winter Wheat', 27: 'Rye', 28: 'Oats', 36: 'Alfalfa', 37: 'Other Hay/Non Alfalfa', 43: 'Potatoes', 49: 'Onions',
             58: 'Clover/Wildflowers', 59: 'Sod/Grass Seed', 61: 'Fallow/Idle Cropland', 66: 'Cherries', 67: 'Peaches', 68: 'Apples',
             69: 'Grapes', 71: 'Other Tree Crops', 72: 'Citrus', 74: 'Pecans', 75: 'Almonds', 76: 'Walnuts', 111: 'Open Water',
             121: 'Developed/Open Space', 122: 'Developed/Low Intensity', 123: 'Developed/Med Intensity', 124: 'Developed/High Intensity',
             131: 'Barren', 141: 'Deciduous Forest', 142: 'Evergreen Forest', 143: 'Mixed Forest', 152: 'Shrubland',
             176: 'Grass/Pasture', 190: 'Woody Wetlands', 195: 'Herbaceous Wetlands', 204: 'Pistachios', 205: 'Triticale',
             206: 'Carrots', 211: 'Olives', 212: 'Oranges'}
CDL_AG = {1, 2, 4, 5, 12, 21, 23, 24, 27, 28, 36, 37, 43, 49, 58, 59, 61, 66, 67, 68, 69, 71, 72, 74, 75, 76, 204, 205, 206, 211, 212} | set(range(225, 255))


# ---------- field geometry: one global frame per field ----------
def _num(v):
    try: return float(str(v).split(';')[0])
    except Exception: return None


def frame(fid, g, tags):
    """(origin, u, n, source): u = row direction (unit), n = across rows; depends only on the whole field + its tags"""
    deg = _num(tags.get('vine_row_orientation')) if tags.get('vine_row_orientation') is not None else _num(tags.get('direction'))
    r = minimum_rotated_rectangle(g)
    pts = [(round(x, 3), round(z, 3)) for x, z in list(r.exterior.coords)[:4]]
    origin = min(pts)                                          # deterministic corner
    if deg is not None:                                         # compass bearing -> block space (x east, z south)
        u = (math.sin(math.radians(deg)), -math.cos(math.radians(deg))); src = 'mapped'
    else:
        e = max(((pts[i], pts[(i + 1) % 4]) for i in range(4)), key=lambda e: (math.dist(*e), e))
        L = math.dist(*e) or 1.0; u = ((e[1][0] - e[0][0]) / L, (e[1][1] - e[0][1]) / L); src = 'inferred'
    if u[0] < -1e-9 or (abs(u[0]) <= 1e-9 and u[1] < 0): u = (-u[0], -u[1])
    u = (round(u[0], 9), round(u[1], 9))
    return origin, u, (-u[1], u[0]), src


def row_of(fr, x, z):
    """row index (across-row band) of block cell (x, z) in field frame fr"""
    (ox, oz), _, n, _ = fr
    return math.floor(((x + 0.5 - ox) * n[0] + (z + 0.5 - oz) * n[1]))


def along(fr, x, z):
    (ox, oz), u, _, _ = fr
    return math.floor(((x + 0.5 - ox) * u[0] + (z + 0.5 - oz) * u[1]))


# ---------- treatments ----------
def treatment(tags):
    """-> (kind, crop_block|None, reason, confidence)"""
    lu = tags.get('landuse')
    crop = (tags.get('crop') or tags.get('produce') or '').split(';')[0].strip().lower()
    if lu == 'vineyard' or crop in ('grape', 'grapes', 'grapevine'): return 'vineyard', None, 'landuse=vineyard / crop=grape', 'mapped'
    if lu == 'orchard': return 'orchard', None, 'landuse=orchard' + (f' trees={tags["trees"]}' if 'trees' in tags else ''), 'mapped'
    if lu == 'meadow': return 'meadow', None, 'landuse=meadow', 'mapped'
    if crop in CROP_BLOCK: return 'crop', CROP_BLOCK[crop], f'crop={crop}', 'mapped'
    if crop in GREEN_CROPS: return 'green', None, f'crop={crop}', 'mapped'
    return 'generic', None, (f'crop={crop} has no stable 1.18.2 crop block' if crop else 'landuse=farmland without crop tag'), 'inferred'


def _cell_ops(kind, crop, fr, fid, x, z, y, bb, seed):
    """ops for one eligible in-field column; pattern = f(field frame, x, z) only"""
    r = row_of(fr, x, z)
    if kind == 'crop':
        if r % ROW == ROW - 1: return [] if bb == FURROW else [(x, y, z, 'base', FURROW, 'furrow')]
        return ([] if bb == FARMLAND else [(x, y, z, 'base', FARMLAND, 'farmland')]) + [(x, y + 1, z, 'air', crop, 'crop')]
    if kind == 'generic':
        b = GENERIC[r % len(GENERIC)]
        return [] if bb == b else [(x, y, z, 'base', b, 'tillage')]
    if kind == 'green':
        o = [] if bb == 'minecraft:grass_block' else [(x, y, z, 'base', 'minecraft:grass_block', 'cover')]
        return o + ([(x, y + 1, z, 'air', 'minecraft:grass', 'cover-plant')] if ec.det_unit(fid, x, z, seed) < 0.3 else [])
    if kind == 'vineyard':                                      # one leaf row every 3 blocks, ~15% gaps (guidebook p4: 50% leaves)
        if r % 3 == 0 and ec.det_unit(fid, 'v', x, z, seed) >= 0.15: return [(x, y + 1, z, 'air', VINE, 'vine')]
        return []
    if kind == 'meadow':
        if bb.split('[')[0] not in MEADOW_ON: return []
        h = ec.det_unit(fid, 'm', x, z, seed); acc = 0
        for b, p in MEADOW_PLANTS:
            acc += p
            if h < acc: return [(x, y + 1, z, 'air', b, 'meadow-plant')]
    return []


def _trees(fr, inner, fid, margin=1.0):
    """orchard trunk cells (global grid in field frame, inside the field with canopy margin)"""
    (ox, oz), u, n, _ = fr
    minx, minz, maxx, maxz = inner.bounds; out = []
    corners = [((cx - ox) * u[0] + (cz - oz) * u[1], (cx - ox) * n[0] + (cz - oz) * n[1]) for cx, cz in ((minx, minz), (maxx, minz), (maxx, maxz), (minx, maxz))]
    a0, a1 = min(c[0] for c in corners), max(c[0] for c in corners); b0, b1 = min(c[1] for c in corners), max(c[1] for c in corners)
    core = inner.buffer(-margin)
    for i in range(math.floor(a0 / ORCH_STEP), math.ceil(a1 / ORCH_STEP) + 1):
        for j in range(math.floor(b0 / ORCH_STEP), math.ceil(b1 / ORCH_STEP) + 1):
            px, pz = ox + (i * ORCH_STEP + 2.5) * u[0] + (j * ORCH_STEP + 2.5) * n[0], oz + (i * ORCH_STEP + 2.5) * u[1] + (j * ORCH_STEP + 2.5) * n[1]
            x, z = math.floor(px), math.floor(pz)
            if core.contains(Point(x + 0.5, z + 0.5)): out.append((x, z))
    return out


def _crop_tree():
    """orchard tree: the vegetation catalog 'crop-tree' entry (prep/egg_assets/vegetation_catalog.json); blocks [dx,dy,dz,state],
    [0,0,0] = trunk base = base_y + 1. Falls back to a small authored tree if the catalog is missing."""
    try:
        c = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'egg_assets', 'vegetation_catalog.json')))
        e = sorted((x for x in c['entries'] if x.get('class') == 'crop-tree'), key=lambda x: x['id'])[0]
        return e['id'], int(e.get('crown_radius', 2)), [tuple(b) for b in e['blocks']]
    except Exception:
        blk = [(0, 0, 0, ORCH_LOG), (0, 1, 0, ORCH_LOG), (0, 2, 0, ORCH_LOG), (0, 3, 0, ORCH_LEAF)] + \
              [(dx, 2, dz, ORCH_LEAF) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dz) != (0, 0)]
        return 'authored-fallback', 1, blk


TREE_ID, TREE_R, TREE = _crop_tree()


# ---------- CDL (supporting evidence only) ----------
def cdl_sample(g_lonlat, fid, cache=CDL_CACHE, year=CDL_YEAR, timeout=60):
    """majority CDL class inside the field (CropScape GetCDLFile clip of the field bbox, cached per field+year).
    Returns dict (never raises): {status, year, resolution_m, majority, share, classes, url, fetched}"""
    import pyproj
    out = dict(year=year, product='USDA NASS Cropland Data Layer (CropScape clip)', licence='public domain', crs='EPSG:5070 (NAD83 Conus Albers)')
    try:
        tr = pyproj.Transformer.from_crs(4326, 5070, always_xy=True)
        import shapely
        g = shapely.transform(g_lonlat, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
        w, s, e, n = g.bounds; w, s, e, n = math.floor(w / 30) * 30 - 30, math.floor(s / 30) * 30 - 30, math.ceil(e / 30) * 30 + 30, math.ceil(n / 30) * 30 + 30
        if (e - w) * (n - s) > 25e6: return dict(out, status='refused_limits', reason='field bbox > 25 km2')
        os.makedirs(cache, exist_ok=True)
        key = hashlib.sha256(f'{year}|{w},{s},{e},{n}'.encode()).hexdigest()[:16]
        tif, prov = os.path.join(cache, f'cdl{year}_{key}.tif'), os.path.join(cache, f'cdl{year}_{key}.provenance.json')
        if not (os.path.exists(tif) and os.path.exists(prov)):
            import requests
            r = requests.get('https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile', params=dict(year=year, bbox=f'{w},{s},{e},{n}'), timeout=timeout)
            m = re.search(r'<returnURL>(.*?)</returnURL>', r.text)
            if r.status_code != 200 or not m: return dict(out, status='provider_failure', reason=r.text[:200])
            d = requests.get(m.group(1), timeout=timeout); d.raise_for_status()
            if len(d.content) > 20_000_000: return dict(out, status='refused_limits', reason='download > 20 MB')
            open(tif + '.tmp', 'wb').write(d.content); os.replace(tif + '.tmp', tif)
            ec.write_json(prov, dict(url=m.group(1), service='https://nassgeodata.gmu.edu/axis2/services/CDLService/GetCDLFile', year=year,
                                     bbox_5070=[w, s, e, n], fetched=time.strftime('%Y-%m-%dT%H:%M:%S'), bytes=len(d.content),
                                     licence='public domain (USDA NASS CDL metadata)', field=fid))
        P = json.load(open(prov))
        from PIL import Image
        im = Image.open(tif); a = np.array(im); sx, sy = im.tag_v2[33550][:2]; X0, Y0 = im.tag_v2[33922][3:5]
        xs = X0 + (np.arange(a.shape[1]) + 0.5) * sx; ys = Y0 - (np.arange(a.shape[0]) + 0.5) * sy
        XX, YY = np.meshgrid(xs, ys); v = a[contains_xy(g, XX, YY)]
        if v.size == 0: v = np.array([a[min(a.shape[0] - 1, int((Y0 - g.centroid.y) / sy)), min(a.shape[1] - 1, int((g.centroid.x - X0) / sx))]])
        vals, cnt = np.unique(v, return_counts=True); k = int(vals[cnt.argmax()])
        return dict(out, status='ready', resolution_m=float(sx), url=P['url'], fetched=P['fetched'], pixels=int(v.size),
                    majority=f'{k} {CDL_NAMES.get(k, "code %d" % k)}', share=round(float(cnt.max()) / v.size, 2), agricultural=k in CDL_AG,
                    classes={CDL_NAMES.get(int(c), 'code %d' % c): int(n) for c, n in zip(vals, cnt)})
    except Exception as ex:
        return dict(out, status='provider_failure', reason=repr(ex)[:200])


def _cdl_check(kind, cdl):
    if cdl.get('status') != 'ready': return None
    maj = cdl['majority']
    if not cdl['agricultural']: return f'CDL {cdl["year"]} majority "{maj}" is non-agricultural (OSM {kind} kept; CDL is supporting only)'
    if kind == 'vineyard' and not maj.startswith('69'): return f'CDL majority "{maj}" is not Grapes'
    if kind == 'orchard' and int(maj.split()[0]) not in {66, 67, 68, 71, 72, 74, 75, 76, 204, 211, 212}: return f'CDL majority "{maj}" is not a tree crop'
    return None


# ---------- plan ----------
def _val(a, i, j):
    v = a[i][j]
    if v is None: return None
    if isinstance(v, float) and math.isnan(v): return None
    return v


def plan(ctx):
    tx, tz, geo, seed = ctx['tx'], ctx['tz'], ctx['geo'], ctx.get('seed', 1)
    x0, z0, x1, z1 = ec.tile_bounds(tx, tz)['blocks']
    t0 = time.time()
    if ctx.get('landcover') is None:
        return ec.plan(CONTENT, tx, tz, ec.NO_COVERAGE, reasons=['landcover index missing or does not cover the tile (not empty land)'], complete=False)
    feats = ctx['landcover'](['landuse', 'leisure'])
    if feats is None:
        return ec.plan(CONTENT, tx, tz, ec.NO_COVERAGE, reasons=['landcover query returned no coverage'], complete=False)
    tb = box(x0, z0, x1 + 1, z1 + 1)
    fields, excl, features, reasons = [], [], [], []
    seen = set()
    for f in feats:
        t, fid = f['tags'], f['id']
        if (fid, f['kind']) in seen: continue
        seen.add((fid, f['kind']))
        lu = t.get('landuse')
        if f['kind'] == 'leisure' or t.get('leisure') in EXCLUDE_LEISURE or lu in EXCLUDE_LANDUSE or 'golf' in t:
            if t.get('leisure') in EXCLUDE_LEISURE or lu in EXCLUDE_LANDUSE or 'golf' in t:
                try: excl.append(ec.to_block(geo, f['geom']))
                except Exception: pass
            if lu in NOT_FIELD:
                features.append(dict(id=fid, kind='farmyard', info='landuse=farmyard: farm buildings/yard, not a field: no crops', partial=False,
                                     anchor=None, anchor_block=None, evidence='OSM', confidence='mapped'))
            continue
        if lu in AG and f['geom'].geom_type in ('Polygon', 'MultiPolygon'): fields.append(f)
    ops, nskip, st = [], dict(ineligible=0, terrain=0), {}
    tcols = set(map(tuple, ctx.get('terrain_cols') or ()))         # columns whose surface this tile's terrain plan changes (REVIEW M6)
    complete = True                                                   # False only if field evidence itself is degraded (CDL never counts)
    excl_u = unary_union(excl).buffer(EDGE) if excl else None
    xs, zs = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(z0, z1 + 1) + 0.5)
    for f in sorted(fields, key=lambda f: f['id']):
        t, fid = f['tags'], f['id']
        try:
            g = ec.to_block(geo, f['geom'])
            if not g.is_valid: g = g.buffer(0)
        except Exception as ex:
            complete = False; reasons.append(f'{fid}: projection failed ({ex!r:.80}): field not planned'); continue
        if not g.intersects(tb): continue
        kind, crop, why, conf = treatment(t)
        fr = frame(fid, g, t)
        inner = g.buffer(-EDGE)
        if excl_u is not None: inner = inner.difference(excl_u)
        cdl = cdl_sample(f['geom'], fid, cache=os.path.join(ctx['cache'], 'cdl') if ctx.get('cache') else CDL_CACHE) if ctx.get('cdl', True) else {'status': 'skipped'}
        dis = _cdl_check(kind, cdl)
        if dis: reasons.append(f'{fid}: {dis}')
        elif cdl.get('status') == 'ready' and kind in ('generic', 'green', 'crop'):
            reasons.append(f'{fid}: CDL {cdl["year"]} majority "{cdl["majority"]}" ({cdl["share"]:.0%}) supports agriculture; not used to choose a crop')
        inside = contains_xy(inner, xs, zs)
        fops = []
        if kind == 'orchard':
            part_feats = {}
            anchor_block = next(b[3] for b in TREE if tuple(b[:3]) == (0, 0, 0))
            for (bx, bz) in _trees(fr, inner, fid, TREE_R):
                local = x0 <= bx <= x1 and z0 <= bz <= z1
                # local trunk: its own column; remote trunk: base estimated from the nearest in-tile column
                i, j = (bz - z0, bx - x0) if local else (min(max(bz - z0, 0), 63), min(max(bx - x0, 0), 63))
                if not _ok(ctx, i, j): nskip['ineligible'] += 1; continue
                if local and (bx, bz) in tcols: nskip['terrain'] += 1; continue
                ay = int(_val(ctx['base_y'], i, j)) + 1; tid = f'{fid}/tree{bx}_{bz}'; n0 = len(fops); clipped = False
                for dx, dy, dz, b in TREE:
                    x, z = bx + dx, bz + dz
                    if not (x0 <= x <= x1 and z0 <= z <= z1): clipped = True; continue
                    if (x, z) in tcols: nskip['terrain'] += 1; continue
                    if not _ok(ctx, z - z0, x - x0): nskip['ineligible'] += 1; continue
                    role = ('trunk' if '_log' in b or '_wood' in b else 'leaves') + ('' if local else '-remote')
                    fops.append((x, ay + dy, z, 'air', b, tid, role))
                if len(fops) > n0 and (clipped or not local):
                    part_feats[tid] = dict(id=tid, kind='orchard-tree', asset=TREE_ID, info=f'catalog {TREE_ID} in {fid}; crosses the tile edge',
                                           partial=True, anchor=[bx, ay, bz], anchor_block=anchor_block, anchor_in_tile=local,
                                           evidence='OSM landuse=orchard; grid inferred' + ('' if local else '; trunk y estimated from nearest in-tile column, '
                                           'plugin writes remote cells only if the trunk exists there'), confidence='inferred')
            features += list(part_feats.values())
            fops.sort(key=lambda o: (o[1], o[6].startswith('leaves'), o[0], o[2]))   # bottom-up, trunks before leaves
        else:
            for i, j in zip(*np.nonzero(inside)):
                if not _ok(ctx, i, j): nskip['ineligible'] += 1; continue
                x, z = int(x0 + j), int(z0 + i)
                if (x, z) in tcols: nskip['terrain'] += 1; continue
                for o in _cell_ops(kind, crop, fr, fid, x, z, int(_val(ctx['base_y'], i, j)), str(_val(ctx['base_block'], i, j)), seed):
                    fops.append(o[:5] + (fid, o[5]))
        partial = not tb.contains(g)
        features.append(dict(id=fid, kind=kind, info=f'{why}; landuse={t.get("landuse")}' + (f' name={t["name"]}' if 'name' in t else ''),
                             partial=partial, anchor=[round(fr[0][0], 3), None, round(fr[0][1], 3)], anchor_block=None, evidence='OSM ' + why + '; CDL ' + cdl.get('status', ''),
                             confidence=conf, row_direction=list(fr[1]), row_direction_source=fr[3], cdl=cdl, ops=len(fops)))
        ops += fops
    st = dict(fields=len(fields), ops=len(ops), skipped=nskip, seconds=round(time.time() - t0, 2))
    src = dict(osm='clark_landcover_index.sqlite (Geofabrik Nevada extract, ODbL)', cdl=f'USDA NASS CDL {CDL_YEAR} via CropScape (supporting only)')
    mx = ctx.get('limits', {}).get('max_ops', 12000)
    if len(ops) > mx: return ec.plan(CONTENT, tx, tz, ec.REFUSED_LIMITS, features=features, reasons=reasons + [f'{len(ops)} ops > {mx}'], sources=src, stats=st, complete=complete)
    if nskip['terrain']: reasons.append(f"terrain changes this column: {nskip['terrain']} agriculture cells skipped")
    status = ec.READY if ops else ec.NOTHING
    if not ops and fields: reasons.append('fields present but no eligible columns (protection / no baseline / baseline cover)')
    return ec.plan(CONTENT, tx, tz, status, ops=[list(o) for o in ops], features=features, reasons=reasons, sources=src, stats=st, complete=complete)


def _ok(ctx, i, j):
    if not (0 <= i < 64 and 0 <= j < 64): return False
    return int(ctx['protect'][i][j]) == 0 and _val(ctx['base_y'], i, j) is not None and _val(ctx['base_cover'], i, j) is None


# ---------- self-check (no server) ----------
def _ctx(geo, tx, tz, base_block='minecraft:dirt', cdl=True):
    """stand-in context: base_y from the TPM terrain model (geo.heights) because prep/egg_provenance.py (baseline reader) is not used here"""
    x0, z0, _, _ = ec.tile_bounds(tx, tz)['blocks']
    ll = geo.inv([(x0 + j + 0.5, z0 + i + 0.5) for i in range(64) for j in range(64)])
    h = np.floor(geo.heights(ll)).reshape(64, 64)
    box_ll = ec.tile_lonlat_box(geo, tx, tz)
    def lc(kinds):
        f, meta = ec.query_index(ec.LANDCOVER_INDEX, box_ll, kinds)
        return f
    return dict(tx=tx, tz=tz, geo=geo, seed=1, cache=None, base_y=h, base_block=[[base_block] * 64 for _ in range(64)],
                base_cover=[[None] * 64 for _ in range(64)], protect=np.zeros((64, 64), np.uint8),
                limits=dict(max_cut=2, max_fill=2, max_ops=12000), landcover=lc, cdl=cdl)


if __name__ == '__main__':
    import sys
    from geo import Geo
    T = time.time(); geo = Geo()
    FIELD = 'w145028142'                                    # Logandale / Moapa Valley farmland (see AGRICULTURE-SPEC.md)
    f = [r for r in ec.query_index(ec.LANDCOVER_INDEX, (-114.50, 36.59, -114.47, 36.61), ['landuse'])[0] if r['id'] == FIELD][0]
    gb = ec.to_block(geo, f['geom']); c = gb.representative_point()
    tx, tz = ec.tile_of_block(math.floor(c.x), math.floor(c.y))
    fr = frame(FIELD, gb, f['tags'])
    ax = 0 if abs(fr[1][0]) >= abs(fr[1][1]) else 1             # neighbour along the row direction, so rows cross the seam
    tb_ = lambda t: box(t[0] * 64, t[1] * 64, t[0] * 64 + 64, t[1] * 64 + 64)
    cand = [(tx + d, tz) if ax == 0 else (tx, tz + d) for d in (1, -1)]
    nb = max(cand, key=lambda t: gb.buffer(-2).intersection(tb_(t)).area)
    A = plan(_ctx(geo, tx, tz)); B = plan(_ctx(geo, *nb))
    print('field', FIELD, f['tags'], 'tiles', (tx, tz), nb, 'ops', len(A['ops']), len(B['ops']), A['status'], B['status'])
    fa = [x for x in A['features'] if x['id'] == FIELD][0]; fb = [x for x in B['features'] if x['id'] == FIELD][0]
    print('frame', fa['anchor'], fa['row_direction'], fa['row_direction_source'], 'cdl', {k: fa['cdl'].get(k) for k in ('status', 'majority', 'share', 'resolution_m', 'pixels')})
    assert A['status'] == 'ready' and B['status'] == 'ready'
    assert fa['anchor'] == fb['anchor'] and fa['row_direction'] == fb['row_direction'], 'field frame differs between tiles'
    # rows continuous across the shared edge: both tiles' ops equal the field-global pattern, and edge columns agree in band
    for P in (A, B):
        for o in P['ops']:
            if o[5] == FIELD: assert o[4] == GENERIC[row_of(fr, o[0], o[2]) % 4], o
    sgn = (nb[ax] - (tx, tz)[ax])                                # +1: B lies after A on axis ax
    ea_c = (ec.tile_bounds(tx, tz)['blocks'][2 + ax] if sgn > 0 else ec.tile_bounds(tx, tz)['blocks'][ax])
    eb_c = ea_c + sgn
    key = lambda o: o[2] if ax == 0 else o[0]
    ea = {key(o): o[4] for o in A['ops'] if (o[0], o[2])[ax] == ea_c}
    eb = {key(o): o[4] for o in B['ops'] if (o[0], o[2])[ax] == eb_c}
    common = set(ea) & set(eb); same = sum(ea[z] == eb[z] for z in common)
    print('seam rows: edge cells', len(common), 'same band', same)
    assert common and same / len(common) > 0.6, 'rows not continuous across seam'
    # determinism
    A2 = plan(_ctx(geo, tx, tz)); assert A2['ops'] == A['ops'] and A2['features'] == A['features']
    # golf course negative controls (Bali Hai, Wynn; both inside the baseline world)
    for name, lon, lat in (('Bali Hai Golf Club', -115.1767, 36.0790), ('Wynn Golf Course', -115.1596, 36.1257)):
        x, z = geo.fwd([(lon, lat)])[0]; gt = ec.tile_of_block(math.floor(x), math.floor(z))
        G = plan(_ctx(geo, *gt, base_block='minecraft:grass_block', cdl=False)); print('golf', name, gt, G['status'], len(G['ops']))
        assert not G['ops']
    # synthetic: farmland polygon double-tagged with golf -> nothing; farmyard -> reported, no ops
    from shapely.geometry import box as B2
    x0, z0, x1, z1 = ec.tile_bounds(tx, tz)['blocks']; poly = geo.inv([(x0, z0), (x1, z0), (x1, z1), (x0, z1)])
    from shapely.geometry import Polygon
    pg = Polygon(poly)
    syn = lambda tags: [dict(id='syn', kind='landuse', tags=tags, geom=pg)]
    for tags in ({'landuse': 'farmland', 'golf': 'fairway'}, {'landuse': 'farmyard'}):
        c2 = _ctx(geo, tx, tz, cdl=False); c2['landcover'] = lambda k, tags=tags: syn(tags); assert not plan(c2)['ops'], tags
    c2 = _ctx(geo, tx, tz, cdl=False); c2['landcover'] = lambda k: syn({'landuse': 'farmland', 'crop': 'wheat'}); W = plan(c2)
    assert {o[4] for o in W['ops']} == {FARMLAND, 'minecraft:wheat[age=7]', FURROW}
    assert all(o[1] == W['ops'][k - 1][1] + 1 for k, o in enumerate(W['ops']) if o[6] == 'crop')   # crop directly above its farmland
    for lu, blocks in (('orchard', {b[3] for b in TREE}), ('vineyard', {VINE}), ('meadow', {b for b, _ in MEADOW_PLANTS})):
        c2['landcover'] = lambda k, lu=lu: syn({'landuse': lu}); P = plan(c2)
        assert P['ops'] and {o[4] for o in P['ops']} <= blocks, (lu, {o[4] for o in P['ops']})
        print('synthetic', lu, len(P['ops']), sorted({o[6] for o in P['ops']}))
    o = [r for r in ec.query_index(ec.LANDCOVER_INDEX, (-115.26, 36.29, -115.23, 36.31), ['landuse'])[0] if r['id'] == 'w27631409'][0]
    ob = ec.to_block(geo, o['geom']).buffer(-8).representative_point(); ot = ec.tile_of_block(math.floor(ob.x), math.floor(ob.y))
    O = plan(_ctx(geo, *ot, cdl=False)); rem = [x for x in O['features'] if x['kind'] == 'orchard-tree']
    print('Gilcrease Orchard tile', ot, O['status'], len(O['ops']), 'remote trees', len(rem))
    assert all(op[6] in ('trunk', 'leaves', 'leaves-remote') for op in O['ops'] if op[5].startswith('w27631409'))
    assert all(any(r['id'] == op[5] for r in rem) for op in O['ops'] if op[6].endswith('-remote'))
    assert all(r['anchor_block'] and r['asset'] == TREE_ID for r in rem) and TREE_ID != 'authored-fallback'
    for r in rem:          # in-tile anchors: the trunk-base op is exactly anchor + anchor_block
        if r['anchor_in_tile']: assert any([o[0], o[1], o[2]] == r['anchor'] and o[4] == r['anchor_block'] and o[6] == 'trunk' for o in O['ops']), r
    assert O['complete'] is True
    # terrain_cols: columns the same tile's terrain plan changes are never used
    ct = _ctx(geo, *ot, cdl=False); tc = {(o[0], o[2]) for o in O['ops'][::3]}; ct['terrain_cols'] = tc; O2 = plan(ct)
    assert O2['ops'] and not any((o[0], o[2]) in tc for o in O2['ops']) and any('terrain changes this column' in r for r in O2['reasons'])
    ct = _ctx(geo, tx, tz, cdl=False); tc = {(o[0], o[2]) for o in A['ops'][::2]}; ct['terrain_cols'] = tc; A3 = plan(ct)
    assert A3['ops'] and not any((o[0], o[2]) in tc for o in A3['ops']) and A3['complete'] is True
    c2['landcover'] = None; assert plan(c2)['status'] == 'no_coverage' and plan(c2)['complete'] is False
    b = ec.tile_bounds(tx, tz)['blocks']
    print('in-game: tile', (tx, tz), 'blocks', b, 'centre', (b[0] + 32, int(A['ops'][0][1]) if A['ops'] else None, b[1] + 32))
    geo.close(); print('egg_agriculture ok', round(time.time() - T, 1), 's')
