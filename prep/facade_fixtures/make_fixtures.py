"""make_fixtures.py - regenerates the synthetic facade-geometry-1 fixtures in this folder (no private paths or ids; bids are fake w9xxxxx).
Run: python prep/facade_fixtures/make_fixtures.py. The selftest uses the written JSON files, not this script.
Voxelisation here is a simple stand-in for prep/facade_geometry.py (B): cells just inside each edge, sampled every 0.25 block,
corner cells owned by the first face that claims them, (ox, oz) = the dominant axis of the outward normal."""
import json, math, os, sys
from shapely.geometry import Polygon
from shapely.ops import unary_union
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import facade_common as F


def ring_ccw(pts):
    """orient the ring so that n = (-tz, tx) points outward (u grows left->right seen from outside)"""
    a = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return pts if a < 0 else pts[::-1]


def faces_of(part, pts, groups, base_y, top_y, edges, curves=None):
    """groups: list of lists of edge indexes (consecutive) forming one face each; edges: edge kind per face"""
    claimed, faces = set(), []
    for fi, g in enumerate(groups):
        segs, cols, u = [], [], 0.0
        for si, e in enumerate(g):
            a, b = pts[e], pts[(e + 1) % len(pts)]
            L = math.dist(a, b); tx, tz = (b[0] - a[0]) / L, (b[1] - a[1]) / L; nx, nz = -tz, tx
            segs.append(dict(a=[round(a[0], 3), round(a[1], 3)], b=[round(b[0], 3), round(b[1], 3)], u0=round(u, 3), u1=round(u + L, 3),
                             normal=[round(nx, 4), round(nz, 4)], curve=(curves or {}).get(e)))
            ox, oz = ((1 if nx > 0 else -1), 0) if abs(nx) >= abs(nz) else (0, (1 if nz > 0 else -1))
            k = 0.125
            while k < L:
                px, pz = a[0] + tx * k - 0.5 * nx, a[1] + tz * k - 0.5 * nz
                c = (math.floor(px), math.floor(pz))
                if c not in claimed:
                    claimed.add(c); cu = u + (c[0] + 0.5 - a[0]) * tx + (c[1] + 0.5 - a[1]) * tz
                    cols.append([c[0], c[1], round(min(max(cu, u), u + L), 3), si, ox, oz])
                k += 0.25
            u += L
        cols.sort(key=lambda c: (c[2], c[3], c[0], c[1]))
        faces.append(dict(id=f'{part}:f{fi}', part=part, edge=edges[fi], label=F.cardinal(*segs[0]['normal']) + ' (display only)',
                          segments=segs, length_u=round(u, 3), base_y=base_y, top_y=top_y, columns=cols))
    return faces


def building(bid, members, parts, notes):
    """parts: [(pid, pts, base_y, top_y, groups, edges, curves)]"""
    P, faces, polys = [], [], []
    for pid, pts, by, ty, groups, edges, curves in parts:
        pts = ring_ccw(pts) if groups is None else pts
        if groups is None: groups = [[i] for i in range(len(pts))]
        P.append(dict(id=pid, osm=None, polygon_mc=[[round(x, 3), round(z, 3)] for x, z in pts], base_y=by, top_y=ty, height_src='fixture',
                      roof=dict(shape='flat', parapet=True, evidence='measured')))
        faces += faces_of(pid, pts, groups, by, ty, edges, curves); polys.append(Polygon(pts))
    env = unary_union(polys)
    g = dict(version='facade-geometry-1', bid=bid, members=members, generator='make_fixtures (synthetic)',
             sources=dict(osm=dict(index='fixture', ids=members), lidar=dict(dataset='fixture', points=0), shell='none'),
             parts=P, faces=faces, envelope=dict(polygon_mc=[[round(x, 3), round(z, 3)] for x, z in env.exterior.coords[:-1]],
                                                 y=[min(p[2] for p in parts), max(p[3] for p in parts) + 1]), notes=[notes])
    g['geometry_sha'] = F.sha_obj(g)
    return g


def rect(x0, z0, x1, z1): return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def rot(pts, deg, c):
    r = math.radians(deg); s, co = math.sin(r), math.cos(r)
    return [(c[0] + (x - c[0]) * co - (z - c[1]) * s, c[1] + (x - c[0]) * s + (z - c[1]) * co) for x, z in pts]


def main():
    out = {}
    # 1. flat rectangle at negative x (chunk math); the 4th face is 'unknown'
    pts = ring_ccw(rect(-40, 100, -10, 120))
    out['flat'] = building('w9000001', ['w9000001'], [('p0', pts, 64, 80, [[0], [1], [2], [3]], ['exterior', 'exterior', 'exterior', 'unknown'], None)],
                           '30 x 20 rectangle, 16 high; face p0:f3 unknown')
    # 2. rotated 11.4 degrees (stepped walls and corners)
    pts = ring_ccw(rot(rect(200, -50, 232, -34), 11.4, (216, -42)))
    out['rot114'] = building('w9000002', ['w9000002'], [('p0', pts, 64, 76, None, ['exterior'] * 4, None)], '32 x 16 rotated 11.4 deg, 12 high')
    # 3. podium + setback tower + annex sharing a party wall (two OSM members)
    p0 = ring_ccw(rect(0, 0, 40, 24)); p2 = ring_ccw(rect(40, 0, 52, 24)); p1 = ring_ccw(rect(6, 6, 22, 18))
    def kinds(pts, shared_x):
        return ['shared' if abs(pts[i][0] - shared_x) < 1e-6 and abs(pts[(i + 1) % 4][0] - shared_x) < 1e-6 else 'exterior' for i in range(4)]
    out['multipart'] = building('w9000003', ['w9000003', 'w9000004'], [
        ('p0', p0, 64, 76, [[i] for i in range(4)], kinds(p0, 40), None),
        ('p1', p1, 76, 100, [[i] for i in range(4)], ['exterior'] * 4, None),
        ('p2', p2, 64, 72, [[i] for i in range(4)], kinds(p2, 40), None)], 'podium p0, setback tower p1 on the podium roof, annex p2 sharing x=40')
    # 4. curved front: a 12-segment arc bulging toward +z (south) between two straight walls
    arc = [(15 + 15 * math.cos(math.radians(a)), 10 + 15 * math.sin(math.radians(a))) for a in range(180, -1, -15)]
    pts = [(round(x, 4), round(z, 4)) for x, z in arc] + [(30, 0), (0, 0)]     # edges 0-11 arc, 12 east, 13 north, 14 west
    groups = [list(range(12)), [12], [13], [14]]
    out['curved'] = building('w9000005', ['w9000005'], [('p0', pts, 64, 76, groups, ['exterior'] * 4, {i: 'arc:a0' for i in range(12)})],
                             '30 wide, curved 12-segment south front (radius 15), 12 high')
    # 5. party wall: the flat building again, its east face coincident with a separate non-member building (edge 'party')
    pts = ring_ccw(rect(-40, 100, -10, 120))
    out['party'] = building('w9000006', ['w9000006'], [('p0', pts, 64, 80, [[0], [1], [2], [3]], ['exterior', 'party', 'exterior', 'exterior'], None)],
                            'flat rectangle; p0:f1 is a party wall')
    for k, g in out.items():
        with open(os.path.join(HERE, 'geometry', k + '.json'), 'w') as f: json.dump(g, f, indent=None, separators=(',', ':'))
        print(k, g['bid'], [(fc['id'], fc['edge'], len(fc['segments']), len(fc['columns'])) for fc in g['faces']])


def wj(rel, obj):
    p = os.path.join(HERE, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f: json.dump(obj, f, indent=1)


def cell(u, v, w, role=None, block=None, **props):
    c = dict(u=u, v=v, w=w)
    if block: c['block'] = block
    else: c['role'] = role
    if props: c['props'] = props
    return c


def comp(cid, su, sv, cells, **kw):
    return dict(schema='facade-component-1', id=cid, version='1.0.0', title=cid, size=dict(u=su, v=sv), repeat_u=kw.pop('repeat_u', False),
                cells=cells, notes='synthetic fixture (Detail pp.4-5)', license='original (Moria)', **kw)


def library():
    """synthetic palette / components / style (fixtures only; block names limited to facade_schema's fallback list)"""
    wj('assets/palettes/fx-tan.json', dict(schema='facade-palette-1', id='fx-tan', version='1.0.0', title='fixture tan', roles={
        'wall': 'minecraft:smooth_sandstone', 'glass': 'minecraft:gray_stained_glass', 'mullion': 'minecraft:black_stained_glass',
        'frame': 'minecraft:spruce_trapdoor', 'backing': 'minecraft:gray_concrete', 'sill': 'minecraft:smooth_quartz_slab',
        'band': 'minecraft:cut_sandstone', 'parapet': 'minecraft:smooth_sandstone', 'canopy': 'minecraft:smooth_stone_slab',
        'column': 'minecraft:cut_sandstone', 'door': 'minecraft:iron_door', 'roof': 'minecraft:white_concrete',
        'roof-edge': 'minecraft:smooth_sandstone_slab', 'sign': 'minecraft:black_concrete', 'trim': 'minecraft:cut_sandstone'},
        texture={'wall': dict(alt='minecraft:sandstone', rule='rows%3==2')}, zones={'podium': {'wall': 'minecraft:smooth_quartz'}}, notes='fixture'))
    G = lambda u, v: [cell(u, v, 0, 'glass')]
    wj('assets/components/fx-window-2x3.json', comp('fx-window-2x3', 2, 3, [c for u in (0, 1) for v in (1, 2) for c in G(u, v)] +
                                                [cell(u, 0, 1, 'sill', type='top', half='top', facing='{facing}') for u in (0, 1)]))
    wj('assets/components/fx-door.json', comp('fx-door', 1, 2, [cell(0, 0, 0, 'door', facing='{facing_in}', half='lower', hinge='left'),
                                                             cell(0, 1, 0, 'door', facing='{facing_in}', half='upper', hinge='left')]))
    wj('assets/components/fx-pane-3x2.json', comp('fx-pane-3x2', 3, 2, [cell(u, v, 0, block='minecraft:gray_stained_glass_pane') for u in range(3) for v in range(2)] +
                                                   [cell(u, v, -1, 'backing') for u in range(3) for v in range(2)]))
    wj('assets/components/fx-stair-lintel.json', comp('fx-stair-lintel', 1, 1, [cell(0, 0, 1, block='minecraft:stone_brick_stairs[facing={facing_in},half=top]')], repeat_u=True))
    wj('assets/components/fx-band.json', comp('fx-band', 1, 1, [cell(0, 0, 0, 'band')], repeat_u=True))
    wj('assets/components/fx-parapet.json', comp('fx-parapet', 1, 2, [cell(0, 0, 0, 'parapet'), cell(0, 1, 0, 'roof-edge', type='bottom')], repeat_u=True))
    wj('assets/components/fx-corner.json', comp('fx-corner', 1, 2, [cell(0, 0, 0, 'trim'), cell(0, 1, 0, 'wall')], repeat_v=True))
    wj('assets/components/fx-canopy-d2.json', comp('fx-canopy-d2', 1, 1, [cell(0, 0, 0, 'band')] + [cell(0, 0, w, 'canopy', type='top') for w in (1, 2)], repeat_u=True))
    wj('assets/components/fx-sign.json', dict(comp('fx-sign', 3, 5, []), glyph_size=dict(u=3, v=5), spacing=1, role='sign', w=0,
                                              glyphs={'F': ['111', '100', '110', '100', '100'], 'X': ['101', '101', '010', '101', '101'], ' ': ['000'] * 5}))
    layout = dict(floors=dict(base_v=0, height=4, count='auto'), bays=dict(width=4, residual='center'),
                  ground=dict(openings=[dict(component='win', bays='all', v=0), dict(component='door', bays='center', replace='win')]),
                  upper=dict(openings=[dict(component='win', bays='all', v=0, above='lintel')]),
                  bands=[dict(component='band', at='floor_lines')], corners=dict(component='corner'), parapet=dict(component='parapet'))
    wj('assets/styles/fx-style.json', dict(schema='facade-style-1', id='fx-style', version='1.0.0', kind='style', title='fixture style',
                                           label='STYLE-GENERATED (not observed)', license='original (Moria)', evidence={'class': 'style'},
                                           applies_to=dict(building=['yes'], max_levels=8, min_face_len=6), palette='lib:fx-tan',
                                           components={'win': {'ref': 'lib:fx-window-2x3'}, 'door': {'ref': 'lib:fx-door'}, 'band': {'ref': 'lib:fx-band'},
                                                       'corner': {'ref': 'lib:fx-corner'}, 'parapet': {'ref': 'lib:fx-parapet'}, 'lintel': {'ref': 'lib:fx-stair-lintel'}},
                                           defaults=dict(exterior=layout, shared={'wall_only': True}), limits={'note': 'styles cannot raise limits'}))
    # ---- packages
    src = [dict(id='s1', kind='street', date='2024-08', license='fixture', attribution='synthetic', coverage=['p0:f0', 'p0:f1', 'p0:f2'])]
    ev = lambda c=1.0, k='reference': dict({'class': k}, sources=['s1'], confidence=0.8, coverage=c)
    def pkg(pid, bids, faces, **kw):
        d = dict(schema='facade-package-1', generator_min='0.6.0',
                 package=dict(id=pid, version='1.0.0', kind='reference', title=pid, license='fixture', attribution='synthetic'),
                 building=dict(osm=bids, parts=['p0']), crs=dict(projection='bte-tpm-geo.py', units='block=1m', y='world'),
                 sources=src, elevations=dict(measured={}, accepted={}), palette='lib:fx-tan',
                 components={'win': {'ref': 'lib:fx-window-2x3'}, 'door': {'ref': 'lib:fx-door'}, 'pane': {'ref': 'lib:fx-pane-3x2'},
                             'canopy': {'ref': 'lib:fx-canopy-d2'}, 'sign': {'ref': 'lib:fx-sign'}, 'band': {'ref': 'lib:fx-band'}},
                 faces=faces, unresolved=[], envelope=dict(outward=1, inward_backing=1, protrusions=[]),
                 limits=dict(max_ops=20000, max_extent_m=200, exception=None), seed=7)
        for k, v in kw.items():
            if k == 'building': d['building'].update(v)
            else: d[k] = v
        wj(f'packages/{pid}/package.json', d)
    F0 = dict(face='p0:f0', floors=dict(base_v=0, height=4, ground_height=5, count='auto'), bays=dict(width=5, residual='ends'),
              openings=[dict(component='win', bays='all', floors='1'), dict(component='door', bays='center', floors='ground')],
              canopy=[dict(component='canopy', v=4, bays='center')], bands=[dict(role='band', at='floors')], parapet=dict(role='parapet', height=1),
              sign=dict(component='sign', text='FX', v=10), evidence=ev())
    F1 = dict(face='p0:f1', floors=dict(base_v=1, height=4, count=2), bays=dict(width=4, residual='absorb'), wall=dict(fill=True),
              zone='podium', openings=[dict(component='pane', bays='all')], evidence=ev(k='measured'))
    F2 = dict(face='p0:f2', floors=dict(base_v=0, height=4, count='auto'), bays=dict(width=6, residual='center'),
              openings=[dict(component='win', bays='every 2')], corners=dict(role='column'), roof_edge=dict(role='roof-edge'), evidence=ev(k='inferred'))
    prot = [dict(face='p0:f0', u=[0, 40], depth=2, allowance='s1')]
    pkg('fx-flat', ['w9000001'], [F0, F1, F2], envelope=dict(outward=1, inward_backing=1, protrusions=prot))
    pkg('fx-insufficient', ['w9000001'], [dict(F0, evidence=ev(c=0.2)), dict(F2, evidence=ev(c=0.3))])
    pkg('fx-limits', ['w9000001'], [F0, F1, F2], limits=dict(max_ops=50, max_extent_m=200, exception=None))
    pkg('fx-massing', ['w9000001'], [F2], building=dict(shell='none'))
    pkg('fx-multipart', ['w9000003', 'w9000004'], [dict(F2, face='*', openings=[dict(component='win', bays='all')], corners=None) | {'corners': dict(role='column')}],
        unresolved=[dict(part='p1', face='p1:f3', reason='not visible from any source')])
    bad = dict(F0, script='rm -rf /', floors=dict(base_v=0, height='4', count='auto'))
    pkg('fx-invalid', ['w9000001'], [bad], palette='lib:no-such-palette')
    pkg('fx-noexception', ['w9000001'], [F2], limits=dict(max_ops=100000, max_extent_m=200, exception=None))
    # review M2: hazardous explicit blocks everywhere they can appear
    pkg('fx-hazard', ['w9000001'], [dict(F2, parapet=dict(role='parapet', height=1, cap='minecraft:tnt'), bands=[dict(block='minecraft:lava', at='floors')])],
        components={'win': {'ref': 'lib:fx-window-2x3'}, 'trap': dict(size=dict(u=1, v=1), cells=[cell(0, 0, 0, block='minecraft:spawner')])},
        extras=[[-30, 70, 120, 'minecraft:chest[facing=south]', 'trim', 'reference']])
    # roof element, backing opt-out, extras, parapet cap
    F2b = dict(F2, backing_exclude_u=[[0, 8]], parapet=dict(role='parapet', height=1, cap='minecraft:smooth_stone_slab[type=bottom]'),
               openings=[dict(component='win', bays='every 2'), dict(component='win', bays='odd', backing='none')])
    pkg('fx-roof', ['w9000001'], [F2b], roofs=[dict(part='p0', cover=dict(role='roof', evidence='reference'),
                                                    edge=dict(block='minecraft:smooth_stone_slab[type=bottom]', rows='top+1', evidence='reference'))],
        extras=[[-25, 70, 120, 'minecraft:smooth_stone_slab[type=top]', 'canopy', 'reference'], [-25, 70, 200, 'minecraft:stone', 'trim', 'reference']])
    pkg('fx-party', ['w9000006'], [dict(F2, face='*')], building=dict(shell='none'))


if __name__ == '__main__':
    os.makedirs(os.path.join(HERE, 'geometry'), exist_ok=True); main(); library()
