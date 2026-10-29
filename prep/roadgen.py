"""roadgen.py - road model + per-chunk road/approach generation, shared by the per-chunk prep and the crossing-level planner.
chunk_roads() is the former shelltool_prep.do_roads (v0.4.2) parameterised by chunk; projections and terrain heights are cached
so a whole crossing can be generated in one process with bulk geo calls."""
import math, os
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from pyproj import Transformer
# ---------------- road model: one marking/sidewalk model for ground roads AND bridge decks (ROAD-IMPLEMENTATION-SPEC.md)
# Palette (roadstyle v2): read-only survey of the builders' Las Vegas Blvd frontage at Bellagio + BTE Roads Guidebook p.63-64:
# asphalt gray_concrete_powder/cyan_terracotta ~50/50, lines white/yellow concrete POWDER, sidewalk light_gray_concrete_powder,
# flush smooth_stone kerb (guide pp.15-18, 61-62). Dash 3 on / 9 off (guide; MUTCD 10 ft / 30 ft scaled ESTIMATE).
STYLE = 'roadstyle-v2'
CARRIAGE = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link',
            'unclassified', 'residential', 'living_street', 'service', 'road'}
DEFAULT_LANES = {'motorway': 2, 'trunk': 2, 'primary': 2, 'secondary': 2, 'tertiary': 1, 'unclassified': 1, 'residential': 1, 'living_street': 1, 'service': 1, 'road': 1}
MARKED = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link'}
CURBED = CARRIAGE - {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'service'}
LANE_M, SIDEWALK_M, BIKE_M, CURB_M, CROSS_M, DASH_ON, DASH_PERIOD = 3.6, 1.5, 1.5, 1.0, 3.0, 3.0, 12.0
SHOULDER = {'motorway': (3.0, 1.2), 'motorway_link': (2.4, 1.2), 'trunk': (3.0, 1.2), 'trunk_link': (2.4, 1.2)}   # (right, left) ESTIMATES
PAVE_A, PAVE_B = 'minecraft:gray_concrete_powder', 'minecraft:cyan_terracotta'
WHITE, YELLOW, SIDEWALK_BLK, CURB_BLK = 'minecraft:white_concrete_powder', 'minecraft:yellow_concrete_powder', 'minecraft:light_gray_concrete_powder', 'minecraft:smooth_stone'
def num(v):
    try: return float(str(v).lower().replace('m', '').strip())
    except Exception: return None
def hsh(x, z): return ((x * 73856093) ^ (z * 19349663)) & 0xffff
def pave(x, z): return PAVE_B if hsh(x, z) % 2 == 0 else PAVE_A

def build_model(ways_mc, areas_mc, B):
    """ways_mc: [(kind, feature, LineString in block space)] -> carriageways (width, markings, kerbs), sidewalks, crossings, areas"""
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
        marks = []                                  # (offset m, left of travel positive; colour; style; why)
        if hw in MARKED or (hw in ('residential', 'unclassified') and 'lanes' in t and lanes >= 2):
            edge = -Wtot / 2 + sh[0]; e0 = edge
            if oneway:
                turns = (t.get('turn:lanes') or '').split('|')[::-1] if t.get('turn:lanes') else []        # OSM order is left->right
                cat = lambda i: ('turn' if any(s in (turns[i] if i < len(turns) else '') for s in ('left', 'right')) and 'through' not in (turns[i] if i < len(turns) else '') else 'through')
                marks.append((e0 + 0.5 / B, 'white', 'solid', 'right edge line'))
                if nbike: marks.append((edge + BIKE_M, 'white', 'solid', 'bike lane line (cycleway lane)')); edge += BIKE_M
                for kk in range(1, lanes):
                    turnsep = bool(turns) and cat(kk - 1) != cat(kk)
                    marks.append((edge + kk * lw, 'white', 'solid' if turnsep else 'dashed', 'turn-lane separation (turn:lanes; solid = ESTIMATE)' if turnsep else 'lane line'))
                marks.append((edge + lanes * lw - 0.5 / B * (0 if hw in SHOULDER else 1), 'yellow', 'solid', 'left edge line (one-way roadway, MUTCD)'))
            else:
                nf = int(num(t.get('lanes:forward'))) if num(t.get('lanes:forward')) else (lanes + 1) // 2
                twl = 1 if num(t.get('lanes:both_ways')) == 1 else 0
                nbw = max(lanes - nf - twl, 0)
                if nbike: edge += BIKE_M; marks.append((edge, 'white', 'solid', 'bike lane line'))
                for kk in range(1, nf): marks.append((edge + kk * lw, 'white', 'dashed', 'lane line'))
                c1 = edge + nf * lw; marks.append((c1, 'yellow', 'solid', 'centre line'))
                if twl: marks.append((c1 + lw, 'yellow', 'solid', 'centre line (two-way left turn lane)'))
                c2 = c1 + twl * lw
                for kk in range(1, nbw): marks.append((c2 + kk * lw, 'white', 'dashed', 'lane line'))
                if nbike: marks.append((c2 + nbw * lw, 'white', 'solid', 'bike lane line'))
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
        c0 = L.coords[0]; c1_ = L.coords[1]; u0 = np.subtract(c1_, c0); u0 = u0 / (np.linalg.norm(u0) or 1)
        carr.append(dict(id=f['id'], hw=hw, L=L, poly=poly, half=half, marks=marks, curb=hw in CURBED, inferred=inferred, est=not wtag,
                         phase0=float(np.dot(c0, u0))))      # dash phase continues across chained, collinear ways (road -> bridge)
        feats_out.append(dict(id=f['id'], name=t.get('name'), highway=hw, oneway=oneway, lanes=lanes, lanes_source=lane_src, width_m=round(Wtot, 2),
                              width_source=wsrc + (f' + shoulders {sh[0]}/{sh[1] if oneway else sh[0]} m (ESTIMATE)' if sh[0] else ''), lane_width_m=round(lw, 2),
                              sidewalks=('mapped separately (footway=sidewalk); not inferred' if mapped_near else (f'INFERRED from sidewalk={sw or "side tags"} ({SIDEWALK_M} m ESTIMATE)' if sides else 'none tagged/mapped')),
                              markings=[f'{c} {sty} at {o:+.1f} m ({why})' for o, c, sty, why in marks] or ['none (class/lanes unmarked by this spec)'],
                              curbs='flush 1-block kerb (ESTIMATE)' if hw in CURBED else 'none (highway/service class)'))
    vert = {}
    for c in carr:
        for p in c['L'].coords: vert.setdefault((round(p[0], 3), round(p[1], 3)), set()).add(c['id'])
    byid = {c['id']: c for c in carr}
    return dict(carr=carr, feats_out=feats_out,
                junc=[(Point(p), max(byid[i]['half'] for i in ids) + 2 * B) for p, ids in vert.items() if len(ids) > 1],
                sw_polys=[(f, L.buffer((num(f['tags'].get('width')) or SIDEWALK_M) / 2 * B, cap_style=2)) for kind, f, L in ways_mc if kind == 'sidewalk'],
                cr_polys=[(f, L, L.buffer(CROSS_M / 2 * B, cap_style=2)) for kind, f, L in ways_mc if kind == 'crossing'],
                carea=[(g, p) for g, p in areas_mc if g['tags'].get('area:highway') in CARRIAGE],
                sarea=[(g, p) for g, p in areas_mc if g['tags'].get('area:highway') in ('footway', 'sidewalk')],
                curbs=[(c, c['poly'].buffer(CURB_M * B)) for c in carr if c['curb']])

def classify(x, z, M, B, only=None):
    """surface for block column (x, z): (block, role, feature, label) or None. only: allowed carriageway ids (None = all)"""
    P_ = Point(x + 0.5, z + 0.5); carr = [c for c in M['carr'] if only is None or c['id'] in only]
    inside = [c for c in carr if c['poly'].contains(P_)]
    ca = next((g for g, p in M['carea'] if p.contains(P_)), None) if only is None else None
    if only is None and inside and all(c['est'] for c in inside) and not any(pc.contains(P_) for _, _, pc in M['cr_polys']):
        # mapped sidewalk = positional evidence of the road edge: it overrides an ESTIMATED carriageway width
        sm = next((f for f, p in M['sw_polys'] if p.contains(P_)), None)
        if sm: return (SIDEWALK_BLK, 'sidewalk', sm['id'], 'MAPPED sidewalk (overrides estimated road width)')
        if any(p.buffer(CURB_M * B).contains(P_) for f, p in M['sw_polys']) and any(c['curb'] for c in inside):
            return (CURB_BLK, 'curb', inside[0]['id'], 'kerb at mapped sidewalk (ESTIMATE)')
    if inside:
        c = min(inside, key=lambda c: c['L'].distance(P_)); L = c['L']
        s_ = L.project(P_); q = L.interpolate(s_); q2 = L.interpolate(min(s_ + 0.5, L.length)); q1 = L.interpolate(max(s_ - 0.5, 0))
        ux, uz = q2.x - q1.x, q2.y - q1.y; nrm = math.hypot(ux, uz) or 1; ux, uz = ux / nrm, uz / nrm
        d = ((P_.x - q.x) * uz - (P_.y - q.y) * ux) / B                   # metres; left of travel direction positive
        cr = next(((f, Lc) for f, Lc, pc in M['cr_polys'] if pc.contains(P_)), None) if only is None else None
        if cr and cr[0]['tags'].get('crossing') not in ('unmarked', 'no'):
            return ('minecraft:white_concrete_powder' if int(cr[1].project(P_) / B) % 2 == 0 else PAVE_A, 'crossing', cr[0]['id'], 'mapped crossing; bar pattern = engineering choice')
        thin = 0.5 * max(abs(ux), abs(uz)) / B                              # 1-block digital line (guide 4.4 thin lines)
        injunc = len({cc['id'] for cc in inside}) > 1 or any(jp.distance(P_) < r for jp, r in M['junc'])
        if not injunc:
            ph = ((s_ + c['phase0']) / B) % DASH_PERIOD
            for o, col, sty, why in c['marks']:
                if abs(d - o) < thin and (sty == 'solid' or ph < DASH_ON):
                    return (WHITE if col == 'white' else YELLOW, 'marking', c['id'], f'{col} {sty} {why} at {o:+.1f} m (dash phase continuous along the route)')
        return (pave(x, z), 'pavement', c['id'], 'carriageway (width per feature report)')
    if ca: return (pave(x, z), 'pavement', ca['id'], 'mapped area:highway (measured boundary)')
    if only is not None: return None
    cb = next((c for c, p in M['curbs'] if p.contains(P_)), None)
    if cb: return (CURB_BLK, 'curb', cb['id'], 'kerb (ESTIMATE)')
    sm = next((f for f, p in M['sw_polys'] if p.contains(P_)), None) or next((g for g, p in M['sarea'] if p.contains(P_)), None)
    if sm: return (SIDEWALK_BLK, 'sidewalk', sm['id'], 'MAPPED sidewalk (width tag or 1.5 m ESTIMATE)')
    si = next((c for c in carr for bnd in c['inferred'] if bnd.contains(P_)), None)
    if si: return (SIDEWALK_BLK, 'sidewalk', si['id'], 'INFERRED sidewalk (sidewalk tag; 1.5 m ESTIMATE)')
    return None

def grade_of(t): return [k + '=' + t[k] for k in ('bridge', 'tunnel') if t.get(k, 'no') != 'no'] + (['layer=' + t['layer']] if t.get('layer', '0') != '0' else []) + (['covered'] if t.get('covered') == 'yes' else [])

UU = Transformer.from_crs(4326, 32611, always_xy=True).transform

def _prefetch(geo, cache, feats, grid):
    """bulk projection of feature coordinates and bulk terrain heights for grid columns (cached across chunks)"""
    need = [f for f in feats if f['id'] not in cache['proj'] and f['geom'].geom_type in ('LineString', 'Polygon', 'MultiPolygon')]
    rings = []
    for f in need:
        g = f['geom']
        if g.geom_type == 'LineString': rings.append((f['id'], None, list(g.coords)))
        else:
            for k, gg in enumerate(g.geoms if g.geom_type == 'MultiPolygon' else [g]): rings.append((f['id'], k, list(gg.exterior.coords)))
    pts = [c for _, _, cs in rings for c in cs]
    if pts:
        mc = iter(geo.fwd(pts))
        for fid, k, cs in rings:
            cache['proj'].setdefault(fid, {})[k] = [next(mc) for _ in cs]
    miss = [c for c in grid if c not in cache['EY']]
    if miss:
        hh = geo.heights(geo.inv([(x + 0.5, z + 0.5) for x, z in miss]))
        for c, v in zip(miss, hh): cache['EY'][c] = int(math.floor(v)) if np.isfinite(v) else None

def chunk_roads(cx, cz, geo, feats, OV, cache, approach=True):
    """One chunk. OV: deck {(x,z): y}, upper/lower/sidewalks sets, why {way: reason}, raised [samples], keys [(key, grade)],
    skip_cols (columns owned by eligible crossings' raised approaches; ground roads skip them).
    Returns ground road cells, counts, unsupported, feats_out, deck_styles {(x,z): (block, label)}, approach_cells (or failure)."""
    x0, z0 = cx * 16, cz * 16
    corners = geo.inv([(x0, z0), (x0 + 16, z0), (x0 + 16, z0 + 16), (x0, z0 + 16)])
    chunk_ll = Polygon(corners); ctx = chunk_ll.buffer(0.0006)
    (ea, na), (eb, nb2) = UU(*corners[0]), UU(*corners[1]); BPM = 16 / math.hypot(eb - ea, nb2 - na)
    ways, deck_ways, unsupported = [], [], []
    for f in feats:
        if f['kind'] != 'highway' or f['geom'].geom_type != 'LineString' or len(f['geom'].coords) < 2 or not f['geom'].intersects(ctx): continue
        t = f['tags']; hw = t.get('highway'); fid = f['id']; grade = grade_of(t)
        # bridge ways/sidewalks: styled on the deck (deck model) and, outside the deck footprint, ordinary road that may be raised
        if fid in OV['upper']: deck_ways.append(('carriage', f)); ways.append(('carriage', f)); continue
        if fid in OV['sidewalks']: deck_ways.append(('sidewalk', f)); ways.append(('sidewalk', f)); continue
        if hw in CARRIAGE and grade:
            if f['geom'].intersects(chunk_ll):
                why = OV.get('why', {}).get(fid) or ('grade-separated (' + ','.join(grade) + '): elevation not resolved from evidence; skipped (ground road beneath untouched)')
                unsupported.append(dict(id=fid, highway=hw, name=t.get('name'), reason=why))
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
    blds = [f for f in feats if f['kind'] == 'building' and f['geom'].intersects(chunk_ll.buffer(0.0002))]
    raised_on = approach and bool(OV.get('raised'))
    MG = 10 if raised_on else 0                                           # margin: neighbour + 8-block enclosure scan
    grid = [(x0 + i, z0 + j) for j in range(-MG, 16 + MG) for i in range(-MG, 16 + MG)]
    _prefetch(geo, cache, [f for _, f in ways + deck_ways] + areas + blds, grid)
    EY = cache['EY']
    if any(EY.get((x0 + i, z0 + j)) is None for j in range(16) for i in range(16)):
        return dict(failure='TPM terrain heights incomplete; retried next throw')
    P = cache['proj']
    ways_mc = [(kind, f, LineString(P[f['id']][None])) for kind, f in ways]
    deck_mc = [(kind, f, LineString(P[f['id']][None])) for kind, f in deck_ways]
    areas_mc = [(g, Polygon(P[g['id']][0])) for g in areas]
    bld_mc = [Polygon(r) for f in blds for r in P[f['id']].values()]
    M = build_model(ways_mc, areas_mc, BPM)
    cells, counts = [], {}
    def inb(x, z): return any(b.contains(Point(x + 0.5, z + 0.5)) for b in bld_mc)
    lower_polys = [c['poly'] for c in M['carr'] if c['id'] in OV['lower']]
    def on_lower(x, z): return any(pp.contains(Point(x + 0.5, z + 0.5)) for pp in lower_polys)   # the accepted baseline: never raised or filled
    if raised_on:
        from scipy.spatial import cKDTree
        RW = {}
        for r_ in OV['raised']: RW.setdefault(r_['way'], []).append(r_)
        TR = {w: (cKDTree([(q['x'], q['z']) for q in v]), v) for w, v in RW.items()}
        TA = (cKDTree([(q['x'], q['z']) for q in OV['raised']]), OV['raised'])
        def near(T, x, z, lim):
            d, i = T[0].query((x + 0.5, z + 0.5)); return T[1][i] if d <= lim else None
        memo = {}
        def tgt(x, z):
            if (x, z) in memo: return memo[(x, z)]
            g = EY.get((x, z)); res = (None, None, g)
            if g is not None and (x, z) not in OV['deck'] and not inb(x, z) and not on_lower(x, z):
                r = classify(x, z, M, BPM)
                if r is not None:
                    # own way's samples (way id rules out other roads; 16 covers the half-width of a 6-lane carriageway); kerbs/sidewalks/crossings: nearest raised road
                    q = near(TR[r[2]], x, z, 16) if (r[1] in ('pavement', 'marking') and r[2] in TR) else near(TA, x, z, 14)
                    res = ((int(round(q['h'])) if q and round(q['h']) > g else None), r, g, q)
                else: res = (None, r, g)
            memo[(x, z)] = res; return res
        def sandwich(x, z):                                       # any unraised column enclosed by raised road (median, island, hole)
            g = EY.get((x, z))
            if g is None or (x, z) in OV['deck'] or on_lower(x, z) or tgt(x, z)[0] is not None: return None
            for dx, dz in ((1, 0), (0, 1), (1, 1), (1, -1)):
                a_ = b_ = None
                for k in range(1, 9):
                    if a_ is None: a_ = tgt(x + dx * k, z + dz * k)[0]
                    if b_ is None: b_ = tgt(x - dx * k, z - dz * k)[0]
                if a_ is not None and b_ is not None and min(a_, b_) > g: return min(a_, b_)
            return None
        def top(x, z):
            if (x, z) in OV['deck']: return OV['deck'][(x, z)]
            t = tgt(x, z)[0]
            if t is None: t = sandwich(x, z)
            return t if t is not None else EY.get((x, z))
        KEY = OV['keys'][0]
    fills, surfs, bars = [], [], []
    skip = OV.get('skip_cols', set())
    for j in range(16):
        for i in range(16):
            x, z = x0 + i, z0 + j
            if inb(x, z): counts['skipped_under_building'] = counts.get('skipped_under_building', 0) + 1; continue
            under_deck = (x, z) in OV['deck']
            if raised_on and not under_deck:
                t, r, g = tgt(x, z)[:3]
                if t is None:
                    t = sandwich(x, z)
                    if t is not None and r is None: r = ('minecraft:sand', 'median', KEY[0], 'enclosed median/landscape fill (desert landscaping ESTIMATE)')
                    elif t is not None: r = (r[0], r[1], r[2], r[3] + ' (enclosed by raised road)')
                if t is not None:
                    nb = [top(x + dx, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                    low = min(v for v in nb if v is not None) if any(v is not None for v in nb) else t
                    edge = low < t - 1
                    if t - 1 >= g + 1:
                        fills.append([x, z, g + 1, t - 1, 'minecraft:light_gray_concrete' if edge else 'minecraft:stone', 'approach-wall' if edge else 'approach-fill', KEY[0],
                                      ('retaining wall face (PROVISIONAL construction choice)' if edge else 'approach fill (hidden core)') + ' | existing ground below kept'])
                    surfs.append([x, z, t, t, r[0], 'approach-surface', KEY[0], f"raised approach {r[1]}: {r[3]} | Y{t} (approach max grade {KEY[1]:.0%}; existing road buried, not removed)"])
                    if edge and t - low >= 2: bars.append([x, z, t + 1, t + 1, 'minecraft:smooth_stone', 'approach-barrier', KEY[0], 'edge barrier on a drop of 2+ blocks (PROVISIONAL)'])
                    counts['raised_' + r[1]] = counts.get('raised_' + r[1], 0) + 1
                    continue
            if (x, z) in skip: counts['owned by a crossing approach'] = counts.get('owned by a crossing approach', 0) + 1; continue
            r = classify(x, z, M, BPM, only=OV['lower'] if under_deck else None)    # under a deck: only the roads that pass beneath it
            if r is None:
                if under_deck: counts['skipped_under_deck (upper road is on the deck)'] = counts.get('skipped_under_deck (upper road is on the deck)', 0) + 1
                continue
            cells.append([x, z, int(EY[(x, z)]), r[0], r[1], r[2], r[3]]); counts[r[1]] = counts.get(r[1], 0) + 1
    deck_styles, deck_feats = {}, []
    if deck_mc:
        MD = build_model(deck_mc, [], BPM); deck_feats = MD['feats_out']
        for (x, z) in [c for c in OV['deck'] if x0 <= c[0] < x0 + 16 and z0 <= c[1] < z0 + 16]:
            r = classify(x, z, MD, BPM)
            if r: deck_styles[(x, z)] = (r[0], f"deck {r[1]}: {r[3]} | deck Y = world lower road Y + measured separation (vertical adjustment)")
    lower_cols = {(x0 + i, z0 + j) for j in range(16) for i in range(16) if lower_polys and on_lower(x0 + i, z0 + j)}
    return dict(cells=cells, counts=counts, unsupported=unsupported, feats_out=M['feats_out'], deck_feats=deck_feats, deck_styles=deck_styles,
                approach_cells=fills + surfs + bars, bpm=BPM, lower_cols=lower_cols)
