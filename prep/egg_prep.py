"""egg_prep.py <cache_dir> <tx> <tz> [--content terrain|vegetation|agriculture|all] [--seed N] [--max-cut N] [--max-fill N] [--max-ops N]
Egg (v0.5) preparation for ONE 4x4-chunk tile, called by the plugin off the server thread. Builds the shared context
(baseline provenance + protection mask), runs the requested generators (pure functions of cached data + context) and writes
<cache>/egg/tiles/<tx>_<tz>/{manifest,context,terrain,vegetation,agriculture}.json. Contracts: docs/egg/ARCHITECTURE.md.
Failures are reported per content (provider_failure, no_coverage, ...): missing data never means empty land.
Fault injection for tests: EGG_PREP_FAIL=<content> raises inside that generator."""
import json, os, sys, time, traceback
import numpy as np
from shapely.geometry import box
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egg_common as E

EGG_PIPELINE = 'egg_prep v0.5.0'          # the plugin re-prepares tiles whose manifest pipeline differs
CONTEXT_VERSION = 'egg-context-1'
CONTENTS = ('terrain', 'vegetation', 'agriculture')
T = {}; t00 = time.time()
def log(*m): print('[egg-prep %.1fs]' % (time.time() - t00), *m, flush=True)
def arg(name, d, f=str):
    a = sys.argv; return f(a[a.index(name) + 1]) if name in a else d

CACHE, TX, TZ = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
want = arg('--content', 'all'); WANT = CONTENTS if want == 'all' else (want,)
assert all(c in CONTENTS for c in WANT), want
LIMITS = dict(max_cut=arg('--max-cut', 2, int), max_fill=arg('--max-fill', 2, int), max_ops=arg('--max-ops', 12000, int))
SEED = arg('--seed', 1, int)
EGG = os.path.join(CACHE, 'egg'); TD = os.path.join(EGG, 'tiles', f'{TX}_{TZ}'); os.makedirs(TD, exist_ok=True)
MAN = os.path.join(TD, 'manifest.json')
old = json.load(open(MAN)) if os.path.exists(MAN) else {}
if old.get('pipeline') != EGG_PIPELINE: old = {}
B = E.tile_bounds(TX, TZ)

def finish(sections, **kw):
    m = dict(old); m.update(sections); m.update(kw)
    m.update(pipeline=EGG_PIPELINE, tile=[TX, TZ], bounds=B, prepared=time.strftime('%Y-%m-%dT%H:%M:%S'), seed=SEED, limits=LIMITS)
    T['total'] = round(time.time() - t00, 3); m['timings_s'] = m['timings'] = T
    E.write_json(MAN, m); log('manifest', {c: (m.get(c) or {}).get('status') for c in CONTENTS}, T); sys.exit(0)

# ---------------- Strip: refused before anything is fetched
if E.strip_poly().intersects(box(B['blocks'][0], B['blocks'][1], B['blocks'][2] + 1, B['blocks'][3] + 1)):
    msg = 'Tile touches the protected Strip exclusion (+500 blocks). Nothing fetched or generated.'
    finish({c: dict(status=E.PROTECTED, reasons=[msg]) for c in WANT})

from geo import Geo
geo = Geo()

# ---------------- context (cached; rebuilt when its inputs change)
def landcover_fn():
    if not os.path.exists(E.LANDCOVER_INDEX): return None, None
    llb = E.tile_lonlat_box(geo, TX, TZ); cache = {}
    def q(kinds=None):
        k = tuple(sorted(kinds)) if kinds else None
        if k not in cache:
            f, meta = E.query_index(E.LANDCOVER_INDEX, llb, set(kinds) if kinds else None)
            cache[k] = f
        return cache[k]
    _, meta = E.query_index(E.LANDCOVER_INDEX, llb, {'none'})
    return q, meta

def landcover_bits(mask, lc):
    """water / landscaped / developed bits from the landcover index (OR-ed into the OSM protection mask)"""
    from shapely import contains_xy
    x0, z0 = B['blocks'][0], B['blocks'][1]
    xs, zs = np.meshgrid(np.arange(x0, x0 + 64) + 0.5, np.arange(z0, z0 + 64) + 0.5)
    LAND = {'park', 'garden', 'golf_course', 'pitch', 'playground', 'sports_centre', 'stadium', 'dog_park', 'schoolyard', 'track'}
    LANDUSE_LS = {'grass', 'recreation_ground', 'cemetery', 'village_green', 'flowerbed'}
    DEV = {'residential', 'commercial', 'industrial', 'retail', 'construction', 'railway', 'military', 'garages', 'education', 'institutional', 'religious', 'landfill', 'quarry', 'brownfield'}
    n = {}
    for f in lc() or []:
        t, k = f['tags'], f['kind']; bit = None
        if k == 'water' or t.get('natural') == 'water' or t.get('landuse') in ('reservoir', 'basin') or 'waterway' in t: bit = E.P_WATER
        elif k == 'leisure' and t.get('leisure') in LAND: bit = E.P_LANDSCAPED
        elif k == 'landuse' and t.get('landuse') in LANDUSE_LS: bit = E.P_LANDSCAPED
        elif k == 'landuse' and t.get('landuse') in DEV: bit = E.P_DEVELOPED
        if bit is None: continue
        try: g = E.to_block(geo, f['geom'])
        except Exception: continue
        if g.geom_type in ('LineString', 'MultiLineString'): g = g.buffer(3)          # waterways/washes: keep a margin
        if not g.intersects(box(x0, z0, x0 + 64, z0 + 64)): continue
        mask[contains_xy(g, xs, zs)] |= bit; n[bit] = n.get(bit, 0) + 1
    return n

lc, lc_meta = landcover_fn()
CTX = os.path.join(TD, 'context.json')
CAPTURED = os.environ.get('MORIA_EGG_CAPTURED')
osm_meta = E.query_index(E.OSM_INDEX, E.tile_lonlat_box(geo, TX, TZ), {'none'})[1]
key = dict(version=CONTEXT_VERSION, baseline=E.BASELINE_WORLD, captured=CAPTURED, osm=str(osm_meta.get('extract_sha256') if isinstance(osm_meta, dict) else osm_meta),
           landcover=str(lc_meta.get('extract_sha256') if isinstance(lc_meta, dict) else None), landcover_built=str(lc_meta.get('built') if isinstance(lc_meta, dict) else None))
ctxd = json.load(open(CTX)) if os.path.exists(CTX) else None
if not ctxd or ctxd.get('key') != key:
    t = time.time()
    import egg_provenance
    base = egg_provenance.baseline_columns(TX, TZ, E.BASELINE_WORLD, captured_dir=CAPTURED); T['baseline'] = round(time.time() - t, 3)
    t = time.time(); mask, pinfo = E.protection_mask(geo, TX, TZ)
    lcn = landcover_bits(mask, lc) if lc else None; T['protection'] = round(time.time() - t, 3)
    ctxd = dict(key=key, tile=[TX, TZ], bounds=B, base_y=base['base_y'], base_block=base['base_block'], base_cover=base['base_cover'],
                protect=mask.tolist(), protection_info=pinfo, landcover_counts=lcn, baseline_info={k: v for k, v in base.items() if k not in ('base_y', 'base_block', 'base_cover')},
                created=time.strftime('%Y-%m-%dT%H:%M:%S'))
    E.write_json(CTX, ctxd)
else: T['context_cached'] = True
by = np.array([[(-10 ** 9 if v is None else v) for v in r] for r in ctxd['base_y']])
eligible = int(((by > -10 ** 9) & (np.array(ctxd['protect']) == 0)).sum())
log('context', 'baseline columns', int((by > -10 ** 9).sum()), 'eligible (unprotected)', eligible)

ctx = dict(captured=CAPTURED, baseline_world=E.BASELINE_WORLD, captured_dir=CAPTURED, tx=TX, tz=TZ, bounds=B, seed=SEED, geo=geo, cache=EGG, snow_cache=CACHE, limits=LIMITS,
           base_y=[[None if v == -10 ** 9 else int(v) for v in r] for r in by], base_block=ctxd['base_block'], base_cover=ctxd['base_cover'],
           protect=np.array(ctxd['protect'], dtype=np.uint8), landcover=lc)

def protect_ring():
    """OSM/Strip protection bits of the 32-block ring (from the 8 neighbour tiles), so whole-feature planners can drop features anchored there"""
    out = {}; x0, z0 = B['blocks'][0], B['blocks'][1]
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if dx == dz == 0: continue
            m, _ = E.protection_mask(geo, TX + dx, TZ + dz); nx, nz = x0 + 64 * dx, z0 + 64 * dz
            for j in range(64):
                for i in range(64):
                    x, z = nx + i, nz + j
                    if x0 - E.RING <= x < x0 + 64 + E.RING and z0 - E.RING <= z < z0 + 64 + E.RING: out[(x, z)] = int(m[j, i])
    return out
ctx['protect_ring'] = protect_ring() if 'vegetation' in WANT or 'agriculture' in WANT else {}
def terrain_cols():
    f = os.path.join(TD, 'terrain.json')
    try: p = json.load(open(f))
    except Exception: return set()
    return {(o[0], o[2]) for o in p['ops']} if p.get('status') == E.READY else set()

# ---------------- generators: independent sections; one failing never blanks another
sections = {}
for c in WANT:
    t = time.time()
    try:
        if os.environ.get('EGG_PREP_FAIL') == c: raise RuntimeError(f'injected test failure ({c})')
        if c != 'terrain': ctx['terrain_cols'] = terrain_cols()           # plants never go onto columns the terrain plan changes
        if lc is None:
            p = E.plan(c, TX, TZ, E.NO_COVERAGE, reasons=['landcover index missing or not covering this tile: water, washes and landscaping are unknown, nothing may be changed'], complete=False)
        elif (by == -10 ** 9).all():
            p = E.plan(c, TX, TZ, E.NO_COVERAGE, reasons=['no approved baseline for this tile: provenance of the existing ground is unknown, nothing may be changed'])
        else:
            mod = __import__('egg_' + c)
            p = mod.plan(ctx)
        if p['status'] == E.READY and len(p['ops']) > LIMITS['max_ops']:
            p = dict(p, status=E.REFUSED_LIMITS, reasons=p['reasons'] + [f"{len(p['ops'])} ops > max {LIMITS['max_ops']} per tile: refused (raise egg-max-ops-per-tile to allow)"], ops=[])
        E.write_json(os.path.join(TD, f'{c}.json'), p)
        sections[c] = dict(status=p['status'], reasons=p['reasons'][:5], ops=len(p['ops']), features=len(p['features']),
                           partial=sum(1 for f in p['features'] if f.get('partial')), complete=p.get('complete', True), plan=f'{c}.json', sources=p.get('sources', {}), stats=p.get('stats', {}))
    except Exception as ex:
        log(c, 'FAILED', traceback.format_exc())
        sections[c] = dict(status=E.PROVIDER_FAILURE, reasons=[f'{type(ex).__name__}: {ex}'[:300]], retry=True,
                           message='Generation failed (data provider or code error). NOT empty land; retried on the next throw.')
        try: os.remove(os.path.join(TD, f'{c}.json'))
        except OSError: pass
    T[c] = round(time.time() - t, 3)
geo.close()
finish(sections, context=dict(file='context.json', baseline_columns=int((by > -10 ** 9).sum()), eligible_unprotected=eligible))
