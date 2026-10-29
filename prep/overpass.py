"""overpass.py - grade-separated crossing planner (v0.4 pilot).

One crossing = the connected OSM bridge ways of one structure plus the ground ways that pass beneath it.
The plan is computed for the WHOLE crossing from global inputs (complete OSM ways, LiDAR, TPM terrain), cached under
<cache>/crossings/<key>.json, and only then clipped to a chunk, so every chunk sees the identical plan no matter
which neighbour is generated first.

Vertical model (user decision 2026-10-28): the world's existing lower road is the accepted baseline.
  upper surface block Y = lower road surface block Y (world) + round(measured deck top - measured lower road surface)
The separation is MEASURED (LiDAR class 17 deck top vs class 2 lower road surface); the result is a vertical
adjustment to fit the world, not an absolute real-world elevation. OSM layer gives ordering only.
Deck thickness, parapets, piers and abutments are INFERRED (airborne LiDAR sees only the deck top) and labelled.
Approaches are never built by changing terrain: a deck end that does not meet its world approach within 1 block is
reported as an unresolved connection."""
import hashlib, json, math, os
import numpy as np
from shapely import distance, points
from shapely.geometry import LineString, Point, Polygon
from pyproj import Transformer

DECK_TOP, SOFFIT, PARAPET, MEDIAN, PIER, ABUTMENT = ('minecraft:gray_concrete_powder', 'minecraft:light_gray_terracotta',
                                                     'minecraft:smooth_stone', 'minecraft:smooth_stone', 'minecraft:light_gray_concrete', 'minecraft:light_gray_concrete')
DECK_THICKNESS = 2          # blocks incl. surface: INFERRED (Vegas Harmon / NYC builder convention; real structure depth unknown)
PIER_SPACING = 6            # blocks along the pier line: INFERRED
MODEL = 'world-baseline-v2'
LANE_M, SHOULDER = 3.6, {'motorway': (3.0, 1.2), 'motorway_link': (2.4, 1.2), 'trunk': (3.0, 1.2), 'trunk_link': (2.4, 1.2)}   # as the v0.3 road spec (ESTIMATES)
LL2U = Transformer.from_crs(4326, 32611, always_xy=True).transform
M2U = Transformer.from_crs(3857, 32611, always_xy=True).transform
U2M = Transformer.from_crs(32611, 3857, always_xy=True).transform
_U2LL = Transformer.from_crs(32611, 4326, always_xy=True).transform
def u2ll(e, n): return tuple(float(v) for v in _U2LL(e, n))
CARRIAGE = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link',
            'tertiary', 'tertiary_link', 'unclassified', 'residential', 'service', 'road'}


def _u(g): xs, ys = LL2U(*np.array(g.coords).T); return LineString(list(zip(xs, ys)))
def _layer(t):
    try: return int(t.get('layer', '1' if t.get('bridge', 'no') != 'no' else '0'))
    except ValueError: return 0


def crossing_of(feats, way_id):
    """The structure containing bridge way way_id: bridge ways (same layer) chained by shared nodes or running
    alongside within 30 m (parallel carriageways on one deck), and ground ways crossing beneath any of them."""
    by = {f['id']: f for f in feats if f['kind'] == 'highway' and f['geom'].geom_type == 'LineString'}
    br = {i: f for i, f in by.items() if f['tags'].get('bridge', 'no') != 'no' and f['tags'].get('highway') in CARRIAGE}
    if way_id not in br: return None
    lay = _layer(br[way_id]['tags']); U = {i: _u(f['geom']) for i, f in br.items() if _layer(f['tags']) == lay}
    comp, todo = {way_id}, [way_id]
    while todo:
        a = todo.pop()
        for b in U:
            if b not in comp and U[a].distance(U[b]) < 30 and abs(U[a].length - U[b].length) < 0.5 * max(U[a].length, U[b].length) + 30:
                comp.add(b); todo.append(b)
    upper = sorted(comp)
    lower = sorted(i for i, f in by.items() if i not in br and f['tags'].get('highway') in CARRIAGE and f['tags'].get('tunnel', 'no') == 'no'
                   and _layer(f['tags']) < lay and any(f['geom'].crosses(by[u]['geom']) for u in upper))
    return dict(upper=upper, lower=lower, layer=lay, key='x_' + '_'.join(upper))


def plan(feats, cross, geo, lidar_fetch, cache_dir, piers=False, approach_grade=0.0):
    """Full plan for one crossing (cached). lidar_fetch(box3857) -> dict(x,y,z,cls) or raises. geo: Geo (fwd/inv/heights)."""
    by = {f['id']: f for f in feats}
    src = json.dumps([[i, by[i]['tags'], list(by[i]['geom'].coords)] for i in cross['upper'] + cross['lower']], sort_keys=True)
    h = hashlib.sha256(src.encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, 'crossings', cross['key'] + '.json')
    if os.path.exists(path):
        p = json.load(open(path))
        if p.get('input_hash') == h and p.get('model') == MODEL and p.get('piers_enabled') == piers:
            return _with_approach(p, feats, cross, geo, approach_grade, path)      # accepted deck geometry is reused untouched
    p = _plan(by, cross, geo, lidar_fetch, piers)
    p.update(input_hash=h, model=MODEL, piers_enabled=piers, key=cross['key'], upper_ways=cross['upper'], lower_ways=cross['lower'], osm_layer_order=f"upper layer {cross['layer']} above lower (ordering only)")
    os.makedirs(os.path.dirname(path), exist_ok=True); json.dump(p, open(path + '.tmp', 'w')); os.replace(path + '.tmp', path)
    return _with_approach(p, feats, cross, geo, approach_grade, path)


def _with_approach(p, feats, cross, geo, grade, path):
    """raised approaches (approaches.py) for an authorized grade; computed once per crossing+grade and cached in the plan file"""
    if not grade or p.get('status') != 'ready': return p
    if (p.get('approach') or {}).get('grade') != grade or (p.get('approach') or {}).get('model') != 'network-spread-v1':
        import approaches
        a = approaches.plan_approaches(feats, cross, p, geo, grade); a['model'] = 'network-spread-v1'; p['approach'] = a
        json.dump(p, open(path + '.tmp', 'w')); os.replace(path + '.tmp', path)
    return p


def _unresolved(why, **kw): return dict(status='unresolved', reasons=why if isinstance(why, list) else [why], cells=[], **kw)


def _plan(by, cross, geo, lidar_fetch, piers_on):
    UP = {i: _u(by[i]['geom']) for i in cross['upper']}; LO = {i: _u(by[i]['geom']) for i in cross['lower']}
    if not LO: return _unresolved('no ground road mapped beneath the bridge: nothing to measure the separation against')
    allu = [c for L in UP.values() for c in L.coords]
    x0, y0, x1, y1 = min(c[0] for c in allu) - 40, min(c[1] for c in allu) - 40, max(c[0] for c in allu) + 40, max(c[1] for c in allu) + 40
    (a, b), (c, d) = U2M(x0, y0), U2M(x1, y1)
    try: r = lidar_fetch((a, b, c, d))
    except Exception as ex: return _unresolved(f'LiDAR provider failure: {ex}', retry=True)
    if r is None: return _unresolved('outside published LiDAR coverage: deck elevation cannot be measured')
    X, Y = M2U(r['x'], r['y']); Z, C = np.asarray(r['z']), np.asarray(r['cls'])
    lid = dict(dataset=r.get('dataset'), points=int(len(X)), class17=int((C == 17).sum()))
    # ---- upper deck: class 17 points assigned to their nearest upper way; per-way frame (station s, offset o)
    k17 = np.nonzero(C == 17)[0]
    if len(k17) < 200: return _unresolved(f'only {len(k17)} bridge-deck (class 17) LiDAR points: deck not measurable', lidar=lid)
    P17 = points(X[k17], Y[k17]); dist = np.array([distance(P17, L) for L in UP.values()]); near = np.argmin(dist, 0)
    ways, deck_grid = [], {}
    for wi, (wid, L) in enumerate(UP.items()):
        kk = k17[(near == wi) & (dist[wi] < 30)]
        p0, p1 = np.array(L.coords[0]), np.array(L.coords[-1]); dv = (p1 - p0) / np.linalg.norm(p1 - p0); nv = np.array([-dv[1], dv[0]])
        s = (X[kk] - p0[0]) * dv[0] + (Y[kk] - p0[1]) * dv[1]; o = (X[kk] - p0[0]) * nv[0] + (Y[kk] - p0[1]) * nv[1]
        if len(kk) < 100: return _unresolved(f'way {wid}: only {len(kk)} deck points', lidar=lid)
        ways.append(dict(id=wid, p0=p0, dv=dv, nv=nv, s=(float(np.percentile(s, 0.5)), float(np.percentile(s, 99.5))),
                         o=(float(np.percentile(o, 0.5)), float(np.percentile(o, 99.5))), n=int(len(kk))))
    # deck top surface: 1 m grid median of class 17 (measured); columns read it with a 1.5 m search
    gi = np.floor(X[k17]).astype(np.int64), np.floor(Y[k17]).astype(np.int64)
    order = np.lexsort((Z[k17], gi[1], gi[0])); keys = np.stack([gi[0][order], gi[1][order]], 1); zz = Z[k17][order]
    uk, start, cnt = np.unique(keys, axis=0, return_index=True, return_counts=True)
    for (gx, gy), st, n in zip(uk, start, cnt): deck_grid[(int(gx), int(gy))] = float(np.median(zz[st:st + n]))
    def deck_at(e, n_):                     # 3x3 m window; 5x5 m where the deck has non-deck returns (raised median, barriers)
        for r in (1, 2):
            v = [deck_grid[(gx, gy)] for gx in range(int(e) - r, int(e) + r + 1) for gy in range(int(n_) - r, int(n_) + r + 1) if (gx, gy) in deck_grid]
            if len(v) >= 3: return float(np.median(v))
        return None
    # ---- lower roads: class 2 within 6 m of each centreline, profile by station, linear across the occluded span
    k2 = np.nonzero(C == 2)[0]; P2 = points(X[k2], Y[k2]); lower = []
    deck_poly = Polygon()
    for w in ways:
        q = [w['p0'] + w['dv'] * s_ + w['nv'] * o_ for s_, o_ in ((w['s'][0], w['o'][0]), (w['s'][1], w['o'][0]), (w['s'][1], w['o'][1]), (w['s'][0], w['o'][1]))]
        deck_poly = deck_poly.union(Polygon(q))
    deck_poly = deck_poly.buffer(1.5, join_style=2).buffer(-1.5, join_style=2)   # close the sliver between per-way extents (one structure)
    for lid_, L in LO.items():
        X0 = L.intersection(deck_poly)
        if X0.is_empty: continue
        sc = L.project(X0.centroid); dd = distance(P2, L); kk = k2[dd < 6]
        st = np.array([L.project(Point(X[i], Y[i])) for i in kk]) - sc
        outside = np.array([not deck_poly.buffer(1).contains(Point(X[i], Y[i])) for i in kk])
        bins = {}
        for sv, zv in zip(st[outside], Z[kk][outside]):
            if abs(sv) <= 80: bins.setdefault(int(math.floor(sv / 4)), []).append(zv)
        prof = sorted((b * 4 + 2, float(np.median(v))) for b, v in bins.items() if len(v) >= 5)
        neg, pos = [p for p in prof if p[0] < 0], [p for p in prof if p[0] > 0]
        if len(neg) < 3 or len(pos) < 3:
            lower.append(dict(id=lid_, status='unresolved', reason='lower road surface not measured on both sides of the deck')); continue
        fn = np.polyfit([p[0] for p in neg[-8:]], [p[1] for p in neg[-8:]], 1); fp = np.polyfit([p[0] for p in pos[:8]], [p[1] for p in pos[:8]], 1)
        zc = (np.polyval(fn, 0) + np.polyval(fp, 0)) / 2; mis = abs(np.polyval(fn, 0) - np.polyval(fp, 0))
        cp = X0.centroid; ll = u2ll(cp.x, cp.y)
        lower.append(dict(id=lid_, name=by[lid_]['tags'].get('name') or by[lid_]['tags'].get('ref'), status='measured' if mis < 0.5 else 'unresolved',
                          reason=None if mis < 0.5 else f'profiles either side disagree by {mis:.2f} m under the deck',
                          surface_m=round(float(zc), 2), interpolated_span_m=round(pos[0][0] - neg[-1][0], 1), fit_mismatch_m=round(float(mis), 2),
                          crossing_utm=[round(cp.x, 1), round(cp.y, 1)], crossing_lonlat=ll, profile=[[p[0], round(p[1], 2)] for p in prof]))
    good = [l for l in lower if l['status'] == 'measured']
    if not good: return _unresolved(['lower road elevation uncertain: ' + '; '.join(f"{l['id']}: {l['reason']}" for l in lower)], lidar=lid, lower=lower)
    mc = geo.fwd([tuple(l['crossing_lonlat']) for l in good]); th = geo.heights([tuple(l['crossing_lonlat']) for l in good])
    for l, (mx, mz), t in zip(good, mc, th): l.update(world_block_y=int(math.floor(t)), world_tpm=float(t), crossing_mc=[int(math.floor(mx)), int(math.floor(mz))])
    # ---- columns: every block whose centre lies in the deck footprint
    ring = geo.fwd([u2ll(*c) for c in deck_poly.exterior.coords])
    xs, zs = [p[0] for p in ring], [p[1] for p in ring]
    cols = [(x, z) for x in range(math.floor(min(xs)) - 2, math.ceil(max(xs)) + 2) for z in range(math.floor(min(zs)) - 2, math.ceil(max(zs)) + 2)]   # floor: coordinates are negative
    cll = geo.inv([(x + 0.5, z + 0.5) for x, z in cols]); cu = [LL2U(*q) for q in cll]
    inside = [deck_poly.contains(Point(e, n_)) for e, n_ in cu]
    tpm = geo.heights([q for q, i in zip(cll, inside) if i]); ti = iter(tpm)
    lowU = {l['id']: LO[l['id']] for l in good}
    carr = {}
    for l in good:
        t = by[l['id']]['tags']; hw = t.get('highway'); oneway = t.get('oneway') in ('yes', '1', '-1') or hw in ('motorway', 'motorway_link')
        try: lanes = int(t.get('lanes'))
        except (TypeError, ValueError): lanes = 2 if oneway else 2
        try: W_, src = float(str(t['width']).replace('m', '')), 'osm width'
        except (KeyError, ValueError): sh = SHOULDER.get(hw, (0, 0)); W_, src = lanes * LANE_M + sh[0] + (sh[1] if oneway else sh[0]), f'ESTIMATE {lanes} x {LANE_M} m + shoulders {SHOULDER.get(hw, (0, 0))}'
        carr[l['id']] = (lowU[l['id']].buffer(W_ / 2, cap_style=2), round(W_, 1), src)
    def in_carriageway(e, n_): return any(poly.contains(Point(e, n_)) for poly, _, _ in carr.values())
    deck = {}; uncertain = 0; cuv = dict(zip(cols, cu))
    for (x, z), (e, n_), i in zip(cols, cu, inside):
        if not i: continue
        ground = int(math.floor(next(ti)))
        dz = deck_at(e, n_)
        if dz is None: uncertain += 1; continue
        # reference = the lower roads' world surface and measured surface, blended by distance (two nearest), so one deck
        # spanning carriageways at different world Y stays continuous instead of stepping at the midline
        ds = sorted((lowU[l['id']].distance(Point(e, n_)), l) for l in good)[:2]
        wts = [1.0] if len(ds) == 1 else [ds[1][0] / (ds[0][0] + ds[1][0] + 1e-9), ds[0][0] / (ds[0][0] + ds[1][0] + 1e-9)]
        wref = sum(wt * l['world_tpm'] for wt, (_, l) in zip(wts, ds)); lref = sum(wt * l['surface_m'] for wt, (_, l) in zip(wts, ds))
        sep = dz - lref; y = int(math.floor(wref + sep))                             # surface block whose top is the adjusted deck top
        deck[(x, z)] = dict(y=y, ground=ground, sep=round(sep, 2), ref=ds[0][1]['id'], e=e, n=n_)
    if not deck: return _unresolved('no deck column could be measured', lidar=lid, lower=lower)
    reasons = []
    if uncertain > 0.05 * (len(deck) + uncertain): reasons.append(f'{uncertain} deck columns without class-17 coverage (>5 %): deck elevation uncertain')
    # ---- cells
    cells = []; P = lambda x, z, y0, y1, b, role, fid, lab: cells.append([x, z, y0, y1, b, role, fid, lab])
    UPL = list(UP.values())
    median_cols = set()
    if len(UPL) == 2:                                         # one column per block row along the deck: the one closest to midway
        along_x = abs(ways[0]['dv'][0]) >= abs(ways[0]['dv'][1]); best = {}
        for (x, z), v in deck.items():
            r = abs(abs((v['e'] - ways[0]['p0'][0]) * ways[0]['nv'][0] + (v['n'] - ways[0]['p0'][1]) * ways[0]['nv'][1])
                       - abs((v['e'] - ways[1]['p0'][0]) * ways[1]['nv'][0] + (v['n'] - ways[1]['p0'][1]) * ways[1]['nv'][1]))   # infinite-line distances: ways end before the deck does
            k = x if along_x else z
            if r < 1.5 and (k not in best or r < best[k][0]): best[k] = (r, (x, z))
        median_cols = {c for _, c in best.values()}
    side_edge, end_edge = set(), set()
    for (x, z), v in deck.items():
        for dx, dz_ in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, z + dz_) in deck: continue
            e2, n2 = cuv[(x + dx, z + dz_)]
            # outside neighbour beyond a deck END (along a way) or beyond a SIDE (across)?
            w = min(ways, key=lambda w: abs((e2 - w['p0'][0]) * w['nv'][0] + (n2 - w['p0'][1]) * w['nv'][1]))
            s_ = (e2 - w['p0'][0]) * w['dv'][0] + (n2 - w['p0'][1]) * w['dv'][1]
            (end_edge if (s_ < w['s'][0] + 0.5 or s_ > w['s'][1] - 0.5) else side_edge).add((x, z))
    fid = cross['key']
    for (x, z), v in sorted(deck.items()):
        y = v['y']
        P(x, z, y, y, DECK_TOP, 'deck', fid, f"deck surface: world lower road Y + measured separation {v['sep']:+.2f} m (vertical adjustment to fit the world, not absolute elevation)")
        P(x, z, y - DECK_THICKNESS + 1, y - 1, SOFFIT, 'deck-soffit', fid, f'deck structure: thickness {DECK_THICKNESS} blocks INFERRED (convention; real depth not measured)')
        if (x, z) in side_edge and (x, z) not in end_edge:
            P(x, z, y + 1, y + 1, PARAPET, 'parapet', fid, 'parapet 1 block: INFERRED (convention)')
        elif (x, z) in median_cols:
            P(x, z, y + 1, y + 1, MEDIAN, 'median', fid, 'raised median between carriageways: observed in imagery 2025-09-10; 1-block width ESTIMATE')
    # supports: abutments under deck ends (INFERRED), pier columns on the median line of the lower roads (INFERRED)
    supports_refused = 0
    for (x, z) in sorted(end_edge):
        v = deck[(x, z)]
        if in_carriageway(v['e'], v['n']): supports_refused += 1; continue          # never a support inside a freeway carriageway
        if v['y'] - DECK_THICKNESS >= v['ground'] + 1:
            P(x, z, v['ground'] + 1, v['y'] - DECK_THICKNESS, ABUTMENT, 'abutment', fid, 'abutment wall under deck end: INFERRED (position = measured deck end)')
    piers = []
    if piers_on and len(good) >= 2:
        A, B_ = lowU[good[0]['id']], lowU[good[1]['id']]
        line = [(A.project(Point(v['e'], v['n'])), x, z, v) for (x, z), v in deck.items()
                if abs(A.distance(Point(v['e'], v['n'])) - B_.distance(Point(v['e'], v['n']))) < 0.8 and (x, z) not in end_edge and not in_carriageway(v['e'], v['n']) and v['y'] - DECK_THICKNESS >= v['ground'] + 1]
        line.sort(key=lambda t: t[0]); last = -1e9
        for s_, x, z, v in line:                                    # one column every PIER_SPACING blocks along the lower-road median
            if s_ - last < PIER_SPACING * 0.8: continue
            last = s_; piers.append((x, z))
            P(x, z, v['ground'] + 1, v['y'] - DECK_THICKNESS, PIER, 'pier', fid, f'pier column on the lower-road median: INFERRED position (none measured); spacing ~{PIER_SPACING} blocks ESTIMATE')
    # ---- clearance across the FULL carriageway width (every deck column over a carriageway, incl. stepped edges):
    #      air blocks between the expected world lower surface (TPM floor) and the lowest block of the deck (soffit underside)
    clear, clear_cols = [], []
    for l in good:
        poly, W_, src = carr[l['id']]
        cs = sorted((v['y'] - DECK_THICKNESS - v['ground'], x, z) for (x, z), v in deck.items() if poly.contains(Point(v['e'], v['n'])))
        if not cs: continue
        clear_cols += [[x, z, l['id']] for _, x, z in cs]
        clear.append(dict(lower=l['id'], name=l['name'], carriageway_width_m=W_, width_source=src, columns_checked=len(cs),
                          min_air_blocks=int(cs[0][0]), max_air_blocks=int(cs[-1][0]), min_at=[cs[0][1], cs[0][2]],
                          measured_top_to_top_m=round(float(np.median([deck[(x, z)]['sep'] for _, x, z in cs])), 2)))
        if cs[0][0] < 4: reasons.append(f"clearance over {l['id']} only {cs[0][0]} air blocks (full-width check)")
    # ---- approaches: the world road beyond each deck end vs the deck end height
    appr = []
    for w in ways:
        for end, sgn in (('start', -1), ('end', 1)):
            s_end = w['s'][0] if sgn < 0 else w['s'][1]
            q = w['p0'] + w['dv'] * (s_end + sgn * 2.0); lonlat = u2ll(*q); (mx, mz), = geo.fwd([lonlat]); wy = int(math.floor(geo.heights([lonlat])[0]))
            dend = [v for v in deck.values() if abs((v['e'] - w['p0'][0]) * w['dv'][0] + (v['n'] - w['p0'][1]) * w['dv'][1] - s_end) < 2.5
                    and abs((v['e'] - w['p0'][0]) * w['nv'][0] + (v['n'] - w['p0'][1]) * w['nv'][1]) < 4]
            if not dend: continue
            dy = int(np.median([v['y'] for v in dend])); step = dy - wy
            rec = dict(way=w['id'], end=end, approach_block=[int(mx), wy, int(mz)], deck_end_y=dy, world_approach_y=wy, step_blocks=step)
            if abs(step) <= 1: rec.update(status='connected', note='deck end meets the world approach within 1 block')
            else: rec.update(status='unresolved', note=f'deck end is {step:+d} blocks from the world approach road; a smooth connection needs terrain modification '
                                                         f'(about {abs(step) * 20} m of approach at a 5 % grade, as fill or cut). Not built; reported only.')
            appr.append(rec)
    ends = []
    for w in ways:
        for end, s_end in (('start', w['s'][0]), ('end', w['s'][1])):
            cs = [(x, z, deck[(x, z)]['y']) for (x, z) in end_edge if abs((deck[(x, z)]['e'] - w['p0'][0]) * w['nv'][0] + (deck[(x, z)]['n'] - w['p0'][1]) * w['nv'][1]) < 14
                  and abs((deck[(x, z)]['e'] - w['p0'][0]) * w['dv'][0] + (deck[(x, z)]['n'] - w['p0'][1]) * w['dv'][1] - s_end) < 6]
            if cs: ends.append(dict(way=w['id'], end=end, columns=len(cs), x=[min(c[0] for c in cs), max(c[0] for c in cs)], z=[min(c[1] for c in cs), max(c[1] for c in cs)],
                                    deck_y=sorted({c[2] for c in cs}), status='UNRESOLVED approach (open deck end; not connected)'))
    status = 'ready' if not reasons else 'unresolved'
    return dict(status=status, reasons=reasons, lidar=lid, lower=lower, clearance=clear, approaches=appr,
                deck=dict(columns=len(deck), y_min=min(v['y'] for v in deck.values()), y_max=max(v['y'] for v in deck.values()),
                          width_m=[round(w['o'][1] - w['o'][0], 1) for w in ways], length_m=[round(w['s'][1] - w['s'][0], 1) for w in ways],
                          class17_points=[w['n'] for w in ways], thickness_blocks=f'{DECK_THICKNESS} INFERRED'),
                piers=dict(enabled=piers_on, count=len(piers), status=('INFERRED: positions follow the lower-road median' if piers_on else 'DISABLED (default): no evidence of the pier layout (no LiDAR returns under the deck)')),
                supports_refused_in_carriageway=supports_refused, deck_ends=ends, clearance_columns=clear_cols,
                provisional=['deck thickness 2 blocks', 'parapets 1 block smooth_stone', 'abutment walls under deck ends'],
                cells=cells, cells_planned=len(cells))

