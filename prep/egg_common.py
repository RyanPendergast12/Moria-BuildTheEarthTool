"""egg_common.py - shared helpers for the Egg tool (v0.5): tile math, paths, OSM index access, block-space projection,
protection mask, deterministic choices, plan output. Contracts: docs/egg/ARCHITECTURE.md. Coordinator-owned."""
import hashlib, json, math, os, sqlite3, time
import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import transform
from shapely import wkb as swkb, contains_xy

TILE = 64                       # 4 x 4 chunks
RING = 32                       # read-only context around the tile (never written)
PLAN_VERSION = 'egg-plan-1'
# status taxonomy (manifest + plans): never let missing data mean "empty land"
READY, NO_COVERAGE, PROVIDER_FAILURE, LOW_CONFIDENCE, UNSUPPORTED, PROTECTED, REFUSED_LIMITS, NOTHING = (
    'ready', 'no_coverage', 'provider_failure', 'low_confidence', 'unsupported', 'protected', 'refused_limits', 'nothing_to_do')

# paths (overridable by environment so other installs need no source edits)
def _p(env, default): return os.environ.get(env, default)
OSM_INDEX = _p('MORIA_OSM_INDEX', r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite')
LANDCOVER_INDEX = _p('MORIA_LANDCOVER_INDEX', r'C:\BTE-Migration\shelltool\osm-extract\clark_landcover_index.sqlite')
POLYGONS = _p('MORIA_POLYGONS', r'C:\BTE-Migration\summerlin\pilot\polygons.json')
BASELINE_WORLD = _p('MORIA_BASELINE_WORLD', r'C:\BTE-Migration\summerlin\pilot\baseline-world-after-generation')


def tile_of_block(x, z):
    """block -> (tx, tz): floor division at every step (negative coordinates included)"""
    return (x // 16) // 4, (z // 16) // 4


def tile_bounds(tx, tz):
    """inclusive chunk and block bounds of tile (tx, tz)"""
    c0x, c0z = tx * 4, tz * 4
    return dict(chunks=(c0x, c0z, c0x + 3, c0z + 3), blocks=(c0x * 16, c0z * 16, c0x * 16 + 63, c0z * 16 + 63))


def in_tile(tx, tz, x, z):
    b = tile_bounds(tx, tz)['blocks']
    return b[0] <= x <= b[2] and b[1] <= z <= b[3]


def det(*parts):
    """deterministic 64-bit value from feature id / position / seed (stable across runs and machines)"""
    return int.from_bytes(hashlib.sha256('|'.join(map(str, parts)).encode()).digest()[:8], 'big')


def det_unit(*parts): return det(*parts) / 2 ** 64


def tile_lonlat_box(geo, tx, tz, ring=RING):
    b = tile_bounds(tx, tz)['blocks']
    x0, z0, x1, z1 = b[0] - ring, b[1] - ring, b[2] + 1 + ring, b[3] + 1 + ring
    ll = geo.inv([(x0, z0), (x1, z0), (x1, z1), (x0, z1)])
    lons, lats = [p[0] for p in ll], [p[1] for p in ll]
    return min(lons), min(lats), max(lons), max(lats)


def query_index(path, wsen, kinds=None):
    """features whose bounds intersect wsen from a Moria SQLite+RTree index (feat/feat_idx/meta schema).
    Returns (features, meta) or (None, reason) when the index is missing or does not cover the box (NOT empty land)."""
    if not os.path.exists(path): return None, f'index missing: {path}'
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        meta = {k: json.loads(v) for k, v in con.execute('select k,v from meta')}
        C = meta.get('coverage_lonlat_box')
        w, s, e, n = wsen
        if C and not (C[0] <= w and C[1] <= s and C[2] >= e and C[3] >= n): return None, f'outside index coverage {C}'
        q = 'select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=?'
        rows = con.execute(q, (w, e, s, n)).fetchall()
    finally:
        con.close()
    out = [dict(id=o, kind=k, tags=json.loads(t), geom=swkb.loads(g)) for o, k, t, g in rows if kinds is None or k in kinds]
    return out, meta


def to_block(geo, g):
    """lon/lat shapely geometry -> block-space geometry (x east, z south) using the exact TPM projection"""
    def f(xs, ys, zs=None):
        pts = geo.fwd(list(zip(xs, ys)))
        return [p[0] for p in pts], [p[1] for p in pts]
    return transform(f, g)


# protection buffers (metres = blocks). Conservative: generators never write inside these.
ROAD_HALF_M = {'motorway': 14, 'motorway_link': 8, 'trunk': 12, 'trunk_link': 8, 'primary': 11, 'primary_link': 7, 'secondary': 10,
               'secondary_link': 7, 'tertiary': 8, 'tertiary_link': 6, 'residential': 7, 'unclassified': 6, 'living_street': 6, 'service': 4,
               'road': 6, 'pedestrian': 4, 'footway': 2, 'path': 2, 'cycleway': 2, 'steps': 2, 'track': 3, 'bridleway': 2, 'construction': 8}
BUILDING_BUFFER_M = 3

# protection bits in context['protect'] (per column of the tile)
P_STRIP, P_ROAD, P_BUILDING, P_AREA_HWY, P_WATER, P_LANDSCAPED, P_DEVELOPED, P_UNKNOWN = 1, 2, 4, 8, 16, 32, 64, 128


def strip_poly():
    P = json.load(open(POLYGONS))
    return Polygon(P['strip_mc']).buffer(500)


def protection_mask(geo, tx, tz, feats=None):
    """uint8 [64,64] (index [z-z0, x-x0]) of protection bits from the local OSM index + Strip.
    Landcover bits (water/landscaped/developed) are OR-ed in by callers that read the landcover index.
    Returns (mask, info). If the OSM index does not cover the tile every column is P_UNKNOWN (refuse, never assume empty)."""
    b = tile_bounds(tx, tz)['blocks']; x0, z0 = b[0], b[1]
    mask = np.zeros((TILE, TILE), np.uint8); info = {}
    xs, zs = np.meshgrid(np.arange(x0, x0 + TILE) + 0.5, np.arange(z0, z0 + TILE) + 0.5)
    sp = strip_poly()
    if sp.intersects(box(x0, z0, x0 + TILE, z0 + TILE)): mask[contains_xy(sp, xs, zs)] |= P_STRIP
    if feats is None:
        feats, meta = query_index(OSM_INDEX, tile_lonlat_box(geo, tx, tz))
        if feats is None:
            mask |= P_UNKNOWN; info['osm'] = meta; return mask, info
        info['osm'] = dict(source=meta.get('source'), extract_date=meta.get('extract_last_modified'))
    n = {'road': 0, 'building': 0, 'area_highway': 0}
    for f in feats:
        k, t = f['kind'], f['tags']
        try: g = to_block(geo, f['geom'])
        except Exception: continue
        if k == 'highway':
            hw = t.get('highway', '')
            if t.get('tunnel', 'no') not in ('no', 'building_passage') and t.get('layer', '0').startswith('-'): continue   # underground: no surface footprint
            g = g.buffer(ROAD_HALF_M.get(hw, 6) + 0.5 * float(_num(t.get('lanes'), 0)), cap_style=2); bit = P_ROAD; n['road'] += 1
        elif k in ('building', 'building_part'): g = g.buffer(BUILDING_BUFFER_M); bit = P_BUILDING; n['building'] += 1
        elif k == 'area_highway': g = g.buffer(1); bit = P_AREA_HWY; n['area_highway'] += 1
        else: continue
        if not g.intersects(box(x0, z0, x0 + TILE, z0 + TILE)): continue
        mask[contains_xy(g, xs, zs)] |= bit
    info['protected_features'] = n
    return mask, info


def _num(v, d):
    try: return float(str(v).split(';')[0])
    except Exception: return d


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f, separators=(',', ':'))
    os.replace(tmp, path)


def plan(content, tx, tz, status, ops=(), features=(), reasons=(), sources=None, stats=None, **extra):
    """the plan contract (ARCHITECTURE.md section 4). ops: [x, y, z, expect, block, feature, role]"""
    d = dict(version=PLAN_VERSION, content=content, tile=[tx, tz], bounds=tile_bounds(tx, tz), status=status, reasons=list(reasons),
             features=list(features), ops=[list(o) for o in ops], sources=sources or {}, stats=stats or {}, created=time.strftime('%Y-%m-%dT%H:%M:%S'))
    d.update(extra)
    for o in d['ops']:
        assert in_tile(tx, tz, o[0], o[2]), ('op outside tile', o)
        assert o[3] in ('air', 'base', 'egg'), ('bad expect', o)
    return d


if __name__ == '__main__':      # self-check: tile math incl. negative coordinates
    assert tile_of_block(0, 0) == (0, 0) and tile_of_block(63, 63) == (0, 0) and tile_of_block(64, 0) == (1, 0)
    assert tile_of_block(-1, -1) == (-1, -1) and tile_of_block(-64, -64) == (-1, -1) and tile_of_block(-65, 0) == (-2, 0)
    assert tile_of_block(-12160205, -5448042) == ((-12160205 // 16) // 4, (-5448042 // 16) // 4)
    b = tile_bounds(-1, -1); assert b['chunks'] == (-4, -4, -1, -1) and b['blocks'] == (-64, -64, -1, -1)
    tx, tz = tile_of_block(-12160205, -5448042); bb = tile_bounds(tx, tz)['blocks']
    assert bb[0] <= -12160205 <= bb[2] and bb[1] <= -5448042 <= bb[3] and bb[2] - bb[0] == 63
    assert det('a', 1) == det('a', 1) != det('a', 2)
    print('egg_common ok', tx, tz, bb)
