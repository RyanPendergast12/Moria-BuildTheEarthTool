"""ov_approach_plan.py <crossing_plan.json> <region_dir> <out_dir>: PLAN ONLY (writes no world).
For each open deck end: the at-grade continuation of the upper road outward, the existing world surface along it, and what a
continuous approach profile would need at several grades: length, fill, retaining walls, intersections/crosswalks/driveways/
buildings in the way, and existing non-air blocks it would touch. Writes approach_plan.json + one profile PNG per deck end."""
import json, math, os, sqlite3, sys
import numpy as np
from PIL import Image, ImageDraw
from shapely import wkb
from shapely.geometry import LineString, Point
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, r'C:\BTE-Migration\scripts')
from geo import Geo
from anvil_tools import World
IDX = r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite'
GRADES = (0.05, 0.08, 0.10)          # 5 % typical arterial design; 8 % steep arterial; 10 % beyond normal arterial practice
plan, R, OUT = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]; os.makedirs(OUT, exist_ok=True)
W = World(R); geo = Geo()
con = sqlite3.connect(f'file:{IDX}?mode=ro', uri=True)
def feat(o): r = con.execute('select tags,wkb from feat where osm=?', (o,)).fetchone(); return json.loads(r[0]), wkb.loads(r[1])
up = {w: feat(w) for w in plan['upper_ways']}
b = [g.bounds for _, g in up.values()]; W0, S0, E0, N0 = min(x[0] for x in b) - .003, min(x[1] for x in b) - .002, max(x[2] for x in b) + .003, max(x[3] for x in b) + .002
rows = con.execute('select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=?', (W0, E0, S0, N0)).fetchall()
F = [(o, k, json.loads(t), wkb.loads(g)) for o, k, t, g in rows]
HW = [f for f in F if f[1] == 'highway' and f[3].geom_type == 'LineString']
BLD = [f for f in F if f[1] == 'building']
def mcline(g): return LineString(geo.fwd(list(g.coords)))
def surface(x, z, below=None):
    col = [(y, n) for y, n in W.column(x, z) if below is None or y < below]
    return col[-1] if col else (None, None)
deck = {(c[0], c[1]): c[2] for c in plan['cells'] if c[5] == 'deck'}
res = []
for wid, (t, g) in up.items():
    name = t.get('name')
    for endi, ll in (('start', g.coords[0]), ('end', g.coords[-1])):
        # the at-grade way that continues this carriageway beyond the deck end (shares the end node, same name, not a bridge)
        cont = [f for f in HW if f[0] != wid and f[2].get('name') == name and f[2].get('bridge', 'no') == 'no'
                and (Point(f[3].coords[0]).distance(Point(ll)) < 1e-6 or Point(f[3].coords[-1]).distance(Point(ll)) < 1e-6)]
        if not cont: continue
        chain, cur, pt = [], cont[0], ll
        for _ in range(6):                                                  # follow up to ~250 m of continuing ways
            o, k, tt, gg = cur; cs = list(gg.coords)
            if Point(cs[-1]).distance(Point(pt)) < 1e-6: cs = cs[::-1]
            chain += cs if not chain else cs[1:]; pt = cs[-1]
            nxt = [f for f in HW if f[0] != o and f[2].get('name') == name and f[2].get('bridge', 'no') == 'no' and f is not cur
                   and (Point(f[3].coords[0]).distance(Point(pt)) < 1e-6) and f[2].get('oneway') == tt.get('oneway')]
            if not nxt or LineString(chain).length * 92000 > 250: break
            cur = nxt[0]
        L = mcline(LineString(chain))
        # stations every block outward from the deck end
        st = []
        for d in range(0, int(min(L.length, 220))):
            p = L.interpolate(d); x, z = int(math.floor(p.x)), int(math.floor(p.y))
            if (x, z) in deck: continue
            y, n = surface(x, z, below=941 if False else None)
            st.append((d, x, z, y, n))
        if not st: continue
        d0 = st[0][0]; ende = next(e for e in plan['deck_ends'] if e['way'] == wid and e['end'] == endi)
        deck_y = max(ende['deck_y']); ground0 = st[0][3]
        # obstacles along the approach: junction nodes with other carriageways, crosswalks, driveways, buildings within 12 m
        lonlat_line = LineString(chain); obst = []
        for o, k, tt, gg in HW:
            if o in up or tt.get('name') == name and tt.get('highway') == t.get('highway'): continue
            if not gg.intersects(lonlat_line.buffer(0.00002)): continue
            ip = gg.intersection(lonlat_line.buffer(0.00002)).centroid; (mx, mz), = geo.fwd([(ip.x, ip.y)])
            dd = L.project(Point(mx, mz)) - d0
            kind = ('crosswalk' if tt.get('footway') == 'crossing' or tt.get('cycleway') == 'crossing' else 'driveway/service' if tt.get('highway') == 'service'
                    else 'path' if tt.get('highway') in ('footway', 'cycleway', 'path') else 'intersection (' + (tt.get('name') or tt.get('highway')) + ')')
            obst.append(dict(kind=kind, osm=o, dist_from_deck_end=round(dd, 1)))
        near_b = sorted({round(LineString(geo.fwd(list((g2.exterior if g2.geom_type == 'Polygon' else g2.geoms[0].exterior).coords))).distance(L), 1)
                         for o, k, tt, g2 in BLD if g2.distance(lonlat_line) < 0.00015})
        obst.sort(key=lambda o: o['dist_from_deck_end'])
        first_int = next((o['dist_from_deck_end'] for o in obst if o['kind'].startswith('intersection')), None)
        opts = []
        for gr in GRADES:
            prof = []
            for d, x, z, y, n in st:
                ry = deck_y - (d - d0) * gr
                if y is None or ry <= y + 0.5: break
                prof.append((d - d0, x, z, y, ry))
            L_need = (prof[-1][0] + 1) if prof else 0
            hits = [o for o in obst if o['dist_from_deck_end'] <= L_need]
            # footprint: carriageway half width ~ lanes x 3.6 (+ bike) / 2 (ESTIMATE) either side of the centreline
            lanes = int(t.get('lanes', 2)); half = (lanes * 3.6 + (1.5 if 'lane' in (t.get('cycleway:right'), t.get('cycleway:left')) else 0)) / 2 + 2.0
            fill, conflicts, wall = 0, [], 0
            for dd, x, z, y, ry in prof:
                p = L.interpolate(dd + d0); q = L.interpolate(min(dd + d0 + 1, L.length)); ux, uz = q.x - p.x, q.y - p.y; nn = math.hypot(ux, uz) or 1
                for k in range(-int(half), int(half) + 1):
                    cx, cz = int(math.floor(p.x - uz / nn * k)), int(math.floor(p.y + ux / nn * k))
                    gy, gn = surface(cx, cz)
                    if gy is None: continue
                    h = int(round(ry)) - gy; fill += max(0, h)
                    for yy in range(gy + 1, int(round(ry)) + 1):
                        bn = W.block(cx, yy, cz)
                        if bn and 'air' not in bn: conflicts.append([cx, yy, cz, bn])
                    if abs(k) >= int(half) and h >= 2: wall += h
            opts.append(dict(grade=gr, length_blocks=L_need, ends_at_existing_y=prof[-1][3] if prof else None, crosses=[f"{o['kind']} @ {o['dist_from_deck_end']} m" for o in hits],
                             feasible_without_changing_intersections=(first_int is None or L_need <= first_int - 3),
                             fill_blocks_estimate=fill, retaining_wall_face_blocks_estimate=wall, existing_nonair_blocks_in_fill=len(conflicts), conflict_examples=conflicts[:5],
                             profile=[[dd, y, round(ry, 2)] for dd, x, z, y, ry in prof]))
        rec = dict(way=wid, end=endi, name=name, deck_end_y=deck_y, deck_end_block=[st[0][1], st[0][2]], existing_road_y_at_deck_end=ground0,
                   step_blocks=deck_y - ground0 if ground0 is not None else None, first_intersection_m=first_int, obstacles=obst[:12], buildings_within_15m_m=near_b[:5], options=opts)
        res.append(rec)
        # profile drawing
        S = 6; dmax = max(60, max((o['length_blocks'] for o in opts), default=0) + 10); ys = [s_[3] for s_ in st if s_[3] is not None]
        ymin, ymax = min(ys + [deck_y]) - 3, max(ys + [deck_y]) + 4
        img = Image.new('RGB', (dmax * S + 70, (ymax - ymin) * S * 3 + 120), 'white'); dr = ImageDraw.Draw(img)
        def P(d, y): return (60 + d * S, 20 + (ymax - y) * S * 3)
        for d, x, z, y, n in st:
            if d - d0 > dmax or y is None: continue
            dr.rectangle([P(d - d0, y), (P(d - d0, y)[0] + S - 1, P(ymin, ymin)[1])], fill=(200, 190, 160))
        dr.line([P(0, deck_y), P(-0.01, deck_y)], fill='black')
        dr.rectangle([P(-8, deck_y)[0], P(-8, deck_y)[1], P(0, deck_y)[0], P(0, deck_y - 1)[1]], fill=(90, 90, 96))
        cols = {0.05: (40, 140, 40), 0.08: (230, 140, 0), 0.10: (200, 30, 30)}
        for o in opts:
            if o['profile']: dr.line([P(p_[0], p_[2]) for p_ in o['profile']], fill=cols[o['grade']], width=2)
        for ob in obst:
            if ob['dist_from_deck_end'] <= dmax:
                xx = P(ob['dist_from_deck_end'], ymax)[0]; dr.line([(xx, 20), (xx, P(0, ymin)[1])], fill=(0, 0, 200) if ob['kind'].startswith('intersection') else (150, 150, 255))
        for yy in range(ymin, ymax + 1, 2): dr.text((4, P(0, yy)[1] - 5), f'Y{yy}', fill='black')
        dr.text((60, 2), f"APPROACH PLAN (not applied) {name} {wid} deck {endi}: deck end Y{deck_y}, existing road Y{ground0} (step {deck_y - ground0:+d}). Vertical x3.", fill='black')
        lines = [f"green 5%: {opts[0]['length_blocks']} blocks | orange 8%: {opts[1]['length_blocks']} | red 10%: {opts[2]['length_blocks']}  (dark blue = intersection, light blue = crosswalk/path/driveway)"]
        lines += [f"first intersection at {first_int} m: " + ', '.join(f"{int(o['grade'] * 100)}% {'fits' if o['feasible_without_changing_intersections'] else 'CROSSES IT'}" for o in opts)]
        for k, tx in enumerate(lines): dr.text((60, img.height - 40 + 14 * k), tx, fill='black')
        img.save(os.path.join(OUT, f'approach_{wid}_{endi}.png'))
geo.close()
json.dump(res, open(os.path.join(OUT, 'approach_plan.json'), 'w'), indent=1)
for r in res:
    print(r['way'], r['end'], 'deck', r['deck_end_y'], 'road', r['existing_road_y_at_deck_end'], 'step', r['step_blocks'], 'first intersection', r['first_intersection_m'], 'm')
    for o in r['options']: print('   ', int(o['grade'] * 100), '%: length', o['length_blocks'], 'fits' if o['feasible_without_changing_intersections'] else 'CROSSES', o['crosses'][:4], 'fill', o['fill_blocks_estimate'], 'walls', o['retaining_wall_face_blocks_estimate'], 'conflicts', o['existing_nonair_blocks_in_fill'])
