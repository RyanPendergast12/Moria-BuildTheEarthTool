"""approaches.py - raised approaches onto an accepted deck (Option C: gentle grades, intersections raised, freeway fixed).

Network model: every at-grade carriageway near the crossing is sampled every ~2 m. Each deck end seeds the at-grade way that
continues it, at the deck's surface Y. The required road surface spreads outward along the network (Dijkstra over network
distance): h(p) = max(ground(p), seedY - grade * blocks(p)). Intersections therefore become plateaus at the height they are
reached and every leg leaving them ramps down at <= grade until it meets the existing ground. The lower freeway (and any
motorway mainline) is FIXED: if the spread would have to raise it, that is a reported conflict, never a raised freeway.
Global for the crossing (cached in its plan), so every chunk reads identical heights."""
import heapq, math
import numpy as np
from shapely.geometry import LineString, Point
from pyproj import Transformer
LL2U = Transformer.from_crs(4326, 32611, always_xy=True).transform
CARRIAGE = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link',
            'tertiary', 'tertiary_link', 'unclassified', 'residential', 'service', 'road'}
FIXED_HW = {'motorway', 'trunk'}                       # mainlines: never raised
STEP_M = 2.0

def plan_approaches(fx, cross, plan, geo, grade):
    upper = set(cross['upper']); lower = set(cross['lower'])
    ends = {(e['way'], e['end']): max(e['deck_y']) for e in plan['deck_ends']}
    up = {f['id']: f for f in fx if f['id'] in upper}
    ways = [f for f in fx if f['kind'] == 'highway' and f['geom'].geom_type == 'LineString' and f['tags'].get('highway') in CARRIAGE
            and f['id'] not in upper and f['tags'].get('bridge', 'no') == 'no' and f['tags'].get('tunnel', 'no') == 'no' and f['tags'].get('layer', '0') == '0']
    # samples
    pts, wid, key = [], [], {}
    adj = []
    def add(p, w):
        k = (round(p[0], 6), round(p[1], 6))
        if k in key and wid[key[k]] is not None: return key[k]
        i = len(pts); pts.append(p); wid.append(w); adj.append([]); key[k] = i; return i
    for f in ways:
        cs = list(f['geom'].coords); U = LineString([LL2U(*c) for c in cs]); n = max(1, int(U.length / STEP_M))
        prev = None
        for j in range(n + 1):
            q = U.interpolate(j / n, normalized=True)
            if j == 0: i = add(cs[0], f['id'])
            elif j == n: i = add(cs[-1], f['id'])
            else: i = add((q.x, q.y, f['id'], j), f['id'])            # interior samples are unique per way
            if prev is not None and prev != i:
                d = U.length / n; adj[prev].append((i, d)); adj[i].append((prev, d))
            prev = i
    # interior samples were stored in UTM with a tag; endpoints in lon/lat -> normalise all to lon/lat + UTM
    ll, utm = [], []
    Uinv = Transformer.from_crs(32611, 4326, always_xy=True).transform
    for p in pts:
        if len(p) == 4: ll.append(Uinv(p[0], p[1])); utm.append((p[0], p[1]))
        else: ll.append(p); utm.append(LL2U(*p))
    ground = np.floor(geo.heights(ll)).astype(int); mc = geo.fwd(ll)
    hw = {f['id']: f['tags'].get('highway') for f in ways}
    # blocks per metre from the samples themselves (global for the crossing; a chunk-local value would make heights chunk-dependent)
    rs = [math.dist(mc[i], mc[j]) / d for i in range(0, len(pts), 25) for j, d in adj[i][:1] if d > 0.5]
    bpm = float(np.median(rs)) if rs else 1.0
    fixed = [hw.get(wid[i]) in FIXED_HW or wid[i] in lower for i in range(len(pts))]
    # seeds: the shared node of each deck end with its continuing at-grade way
    seeds = []
    for (w, end), y in ends.items():
        c = up[w]['geom'].coords[0 if end == 'start' else -1]; k = (round(c[0], 6), round(c[1], 6))
        if k in key: seeds.append((key[k], y, w, end))
    best = np.full(len(pts), -1e9); dist = np.full(len(pts), np.inf); src = [None] * len(pts); conflicts = []
    pq = []
    for i, y, w, end in seeds:
        best[i] = y; dist[i] = 0; src[i] = f'{w}/{end}'; heapq.heappush(pq, (-y, i))
    while pq:
        ny, i = heapq.heappop(pq); h = -ny
        if h < best[i] - 1e-9: continue
        for j, d in adj[i]:
            hj = h - grade * d * bpm
            if hj <= ground[j] + 0.5 or hj <= best[j] + 1e-9: continue        # met the existing ground (or already higher)
            if fixed[j]:
                conflicts.append(dict(way=wid[j], highway=hw.get(wid[j]), at_mc=[int(mc[j][0]), int(mc[j][1])], needed_raise=round(hj - ground[j], 1),
                                      reason='spread reaches a fixed freeway mainline before meeting its ground: grade cannot be held without raising the freeway'))
                continue
            best[j] = hj; src[j] = src[i]; heapq.heappush(pq, (-hj, j))
    raised = [dict(x=mc[i][0], z=mc[i][1], h=round(float(best[i]), 2), ground=int(ground[i]), way=wid[i], highway=hw.get(wid[i]), from_end=src[i])
              for i in range(len(pts)) if best[i] > ground[i] + 0.5]
    byway = {}
    for r in raised: byway.setdefault(r['way'], []).append(r)
    legs = [dict(way=w, highway=hw.get(w), samples=len(v), max_raise=round(max(r['h'] - r['ground'] for r in v), 1)) for w, v in sorted(byway.items())]
    return dict(grade=grade, blocks_per_metre=round(bpm, 4), samples=raised, legs=legs, conflicts=conflicts[:20], n_conflicts=len(conflicts),
                seeds=[dict(way=w, end=e, deck_y=y) for _, y, w, e in seeds], fixed_rule='motorway/trunk mainlines and the crossing\'s lower roads are never raised')
