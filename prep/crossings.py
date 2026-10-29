"""crossings.py - automatic overpass mode (v0.4.3): discover crossings, decide eligibility with specific reasons, and plan every
block of an eligible crossing (deck + approaches) once, cached, so each chunk only clips it.

Eligibility (all deterministic for a crossing, whichever chunk asks):
  data      plan ready: enough bridge-deck LiDAR, lower road measured on both sides, deck covered (<=5 % unmeasured columns)
  geometry  measured upper/lower separation in SEP_RANGE; deck length consistent with OSM; deck width plausible
  clearance min air blocks over the lower carriageways >= min_clear
  approach  raised approaches reach the existing ground without raising a fixed road (lower roads, other mainlines);
            with approaches off, every deck end must already meet its road (step <= 1)
  overlap   no other crossing's footprint (deck + raised approach area) overlaps this one; no bridge crossing this bridge
  strip     footprint outside the Strip exclusion (+500)
  protected-build conflicts are checked by the plugin over the whole crossing before anything is placed."""
import hashlib, json, math, os
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
import overpass, roadgen

SEP_RANGE = (4.0, 15.0)            # m, deck top minus lower road surface: a simple road-over-road overpass
LEN_RATIO = (0.6, 1.4)             # measured deck length / OSM bridge way length
MAX_WIDTH_M = 80.0
CELLS_MODEL = 'crossing-cells-v4'
U = overpass.LL2U

def bridges(pool):
    return [f for f in pool if f['kind'] == 'highway' and f['geom'].geom_type == 'LineString' and f['tags'].get('bridge', 'no') != 'no'
            and f['tags'].get('highway') in overpass.CARRIAGE]

def footprint(pl):
    """block-space area a crossing occupies: deck columns + raised approach roads (samples buffered by a wide road half-width)"""
    parts = [Point(c[0] + 0.5, c[1] + 0.5).buffer(0.8) for c in pl['cells'] if c[5] == 'deck'][::3]
    ap = pl.get('approach') or {}
    parts += [Point(q['x'], q['z']).buffer(12) for q in ap.get('samples', [])[::2]]
    return unary_union(parts) if parts else Polygon()

def eligibility(cr, pl, fx, env):
    """list of specific reasons this crossing is NOT generated (empty = eligible)"""
    R = []
    if pl['status'] != 'ready': return [r if r.split(':')[0] in ('geometry', 'clearance', 'approach') else 'data: ' + r for r in (pl.get('reasons') or ['plan not ready'])]
    seps = [c['measured_top_to_top_m'] for c in pl.get('clearance', [])]
    if not seps: R.append('geometry: no lower carriageway passes under the measured deck')
    elif not (SEP_RANGE[0] <= float(np.median(seps)) <= SEP_RANGE[1]):
        R.append(f'geometry: measured upper/lower separation {np.median(seps):.1f} m is outside {SEP_RANGE[0]}-{SEP_RANGE[1]} m (not a simple overpass)')
    for w, L, wd in zip(pl['upper_ways'], pl['deck']['length_m'], pl['deck']['width_m']):
        g = next(f['geom'] for f in fx if f['id'] == w); osm = LineString([U(*c) for c in g.coords]).length
        if not (LEN_RATIO[0] <= L / max(osm, 1) <= LEN_RATIO[1]): R.append(f'data: measured deck length {L:.0f} m vs OSM bridge {osm:.0f} m ({w}): inconsistent')
        if wd > MAX_WIDTH_M: R.append(f'geometry: deck width {wd:.0f} m for {w} exceeds {MAX_WIDTH_M:.0f} m (wide multi-structure)')
    mins = [c['min_air_blocks'] for c in pl.get('clearance', [])]
    if mins and min(mins) < env['min_clear']: R.append(f'clearance: {min(mins)} air blocks over the lower road < required {env["min_clear"]}')
    ap = pl.get('approach')
    if env['agrade'] > 0:
        if not ap: R.append('approach: not planned')
        elif ap['n_conflicts']:
            c = ap['conflicts'][0]; R.append(f"approach: would need to raise fixed road {c['way']} ({c['highway']}) by {c['needed_raise']} at {c['at_mc']} - freeway/lower baseline is never raised")
        nodes = [x for x in (ap or {}).get('seeds', []) if '/measured' not in x['end']]
        if ap and len(nodes) < len(pl.get('deck_ends', [])): R.append(f"approach: only {len(nodes)} of {len(pl['deck_ends'])} deck ends continue into a mapped at-grade road")
    else:
        bad = [a for a in pl.get('approaches', []) if abs(a['step_blocks']) > 1]
        if bad: R.append(f"approach: raised approaches are off and {len(bad)} deck end(s) are {max(abs(a['step_blocks']) for a in bad)}+ blocks from their road")
    fp = footprint(pl)
    if env['strip'] is not None and fp.intersects(env['strip']): R.append('strip: the crossing footprint reaches the Strip exclusion (+500)')
    ups = [f['geom'] for f in fx if f['id'] in cr['upper']]
    for b in bridges(fx):
        if b['id'] in cr['upper']: continue
        if any(b['geom'].crosses(u) for u in ups): R.append(f"overlap: bridge {b['id']} ({b['tags'].get('highway')}) crosses this bridge - stacked structure not supported")
    # neighbouring crossings: their footprint must not overlap ours (combined planning is not supported)
    seen = {cr['key']}
    for b in bridges(env['index_around'](unary_union(ups).bounds, 0.003) or []):
        if b['id'] in cr['upper']: continue
        nfx = env['index_around'](b['geom'].bounds, 0.002)
        if not nfx: continue
        ncr = overpass.crossing_of(nfx, b['id'])
        if not ncr or ncr['key'] in seen: continue
        seen.add(ncr['key'])
        npl = overpass.plan(nfx, ncr, env['geo'], env['lidar_fetch'], env['cache'], piers=env['piers'], approach_grade=env['agrade'])
        nfp = footprint(npl) if npl['status'] == 'ready' else unary_union([LineString(env['geo'].fwd(list(f['geom'].coords))).buffer(12) for f in nfx if f['id'] in ncr['upper']])
        if not fp.is_empty and fp.intersects(nfp): R.append(f"overlap: footprint overlaps crossing {ncr['key']} - combined planning of neighbouring crossings is not supported")
    return R

def crossing_cells(cr, pl, env, index_all):
    """every block of an eligible crossing (deck styled with the road model + raised approaches), cached per plan/grade/style"""
    ap = pl.get('approach') or {}
    tag = hashlib.sha256(json.dumps([pl['input_hash'], pl['model'], ap.get('model'), env['agrade'], roadgen.STYLE, CELLS_MODEL]).encode()).hexdigest()[:16]
    path = os.path.join(env['cache'], 'crossings', f"{cr['key']}.cells.json")
    if os.path.exists(path):
        c = json.load(open(path))
        if c.get('tag') == tag: return c
    deck = {(c[0], c[1]): c[2] for c in pl['cells'] if c[5] == 'deck'}
    chunks = {(x >> 4, z >> 4) for x, z in deck}
    for q in ap.get('samples', []):
        cx, cz = int(math.floor(q['x'])) >> 4, int(math.floor(q['z'])) >> 4
        chunks |= {(cx + dx, cz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
    xs = [c[0] for c in chunks]; zs = [c[1] for c in chunks]
    ll = env['geo'].inv([(min(xs) * 16 - 64, min(zs) * 16 - 64), (max(xs) * 16 + 80, max(zs) * 16 + 80)])
    pool = index_all((min(p[0] for p in ll), min(p[1] for p in ll), max(p[0] for p in ll), max(p[1] for p in ll)), 0.002)
    ups = [f['geom'] for f in pool if f['id'] in cr['upper']]
    side = {f['id'] for f in pool if f['kind'] == 'highway' and f['tags'].get('highway') == 'footway' and f['tags'].get('footway') == 'sidewalk'
            and f['tags'].get('bridge', 'no') != 'no' and f['tags'].get('layer', '1') == str(cr['layer']) and any(f['geom'].distance(u) < 0.0003 for u in ups)}
    OV = dict(deck=deck, upper=set(cr['upper']), lower=set(cr['lower']), sidewalks=side, raised=[dict(q, key=cr['key']) for q in ap.get('samples', [])],
              keys=[(cr['key'], env['agrade'])], skip_cols=set(), why={})
    cache = dict(proj={}, EY={})
    MG = 10 if OV['raised'] else 0
    roadgen._prefetch(env['geo'], cache, [], sorted({(cx * 16 + i, cz * 16 + j) for cx, cz in chunks for j in range(-MG, 16 + MG) for i in range(-MG, 16 + MG)}))
    PRI = {'abutment': 0, 'pier': 0, 'approach-fill': 0, 'approach-wall': 0, 'deck-soffit': 1, 'deck': 2, 'approach-surface': 2, 'parapet': 3, 'median': 3, 'approach-barrier': 3}
    out = []
    by_chunk = {}
    for c in pl['cells']: by_chunk.setdefault((c[0] >> 4, c[1] >> 4), []).append(list(c))
    for cx, cz in sorted(chunks):
        r = roadgen.chunk_roads(cx, cz, env['geo'], pool, OV, cache, approach=True)
        if 'failure' in r: return dict(tag=None, failure=f'chunk {cx},{cz}: ' + r['failure'])
        cells = by_chunk.get((cx, cz), [])
        bad = [c for c in r['approach_cells'] if (c[0], c[1]) in r['lower_cols']]
        if bad: return dict(tag=None, failure=f'approach would cover the lower road at {bad[0][0]} {bad[0][2]} {bad[0][1]} ({len(bad)} cells) - never built')
        for c in cells:
            if c[5] == 'deck' and (c[0], c[1]) in r['deck_styles']: c[4], c[7] = r['deck_styles'][(c[0], c[1])]
        cells = cells + r['approach_cells']
        out += sorted(cells, key=lambda c: PRI.get(c[5], 3))
    res = dict(tag=tag, key=cr['key'], cells=out, chunks=sorted(chunks), n=len(out))
    json.dump(res, open(path + '.tmp', 'w')); os.replace(path + '.tmp', path)
    return res
