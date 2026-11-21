"""facade_selftest.py - distributable tests for the Facade compiler (v0.6). Uses only prep/facade_fixtures/ (synthetic, no private data).
Run: python prep/facade_selftest.py   -> exit 0 when every check passes, 1 otherwise. Owner: D1."""
import json, os, subprocess, sys, tempfile, traceback
HERE = os.path.dirname(os.path.abspath(__file__)); FX = os.path.join(HERE, 'facade_fixtures')
os.environ['MORIA_FACADE_ASSETS'] = os.path.join(FX, 'assets'); os.environ['MORIA_FACADE_PACKAGES'] = os.path.join(FX, 'packages')
os.environ['MORIA_FACADE_REFS'] = os.path.join(FX, 'refs')
sys.path.insert(0, HERE)
import facade_common as F, facade_schema as S, facade_compile as C, facade_palette_tools as PT

RESULTS = []
def test(fn):
    try: fn(); RESULTS.append((fn.__name__, None))
    except Exception: RESULTS.append((fn.__name__, traceback.format_exc()))
    return fn

GEO = {k: json.load(open(os.path.join(FX, 'geometry', k + '.json'))) for k in ('flat', 'rot114', 'multipart', 'curved')}
def plan_of(kind, sid, geo, seed=1):
    sel, assets, errs, st = S.load_selection(kind, sid)
    assert not errs, errs
    assert not S.check_against_geometry(sel, GEO[geo]), S.check_against_geometry(sel, GEO[geo])
    return C.compile(GEO[geo], sel, assets, seed)
P = {}
def get(kind, sid, geo, seed=1):
    k = (kind, sid, geo, seed)
    if k not in P: P[k] = plan_of(kind, sid, geo, seed)
    return P[k]
def comp_ops(p, cid): c = next(c for c in p['components'] if c['id'] == cid); return p['ops'][c['first']:c['first'] + c['count']]
def face_cells(geo, fid):
    f = next(f for f in GEO[geo]['faces'] if f['id'] == fid); return {(int(c[0]), int(c[1])) for c in f['columns']}


def check_plan(p, geo):
    """invariants every plan must satisfy (section 4): unique cells, envelope, valid states, contiguous atomic components, chunk lists"""
    assert p['version'] == 'facade-plan-1' and p['status'] in F.STATUSES
    cells = [tuple(o[:3]) for o in p['ops']]; assert len(cells) == len(set(cells)), 'duplicate cells'
    for o in p['ops']:
        assert o[3] == 'replace' and o[5] == p['building'] and o[6] in F.ROLES and o[8] in F.EVIDENCE, o
        assert S.check_block(o[4], {}) is None, (o, S.check_block(o[4], {}))
        n, st = F.parse_block(o[4])
        if n.endswith('_stairs'): assert {'facing', 'half', 'shape'} <= set(st), o
        if C.conn_kind(n): assert {'north', 'east', 'south', 'west'} <= set(st), o
        if n.endswith('_door'):
            other = (o[0], o[1] + (1 if st['half'] == 'lower' else -1), o[2])
            q = next(q for q in p['ops'] if tuple(q[:3]) == other); assert '_door' in q[4] and q[7] == o[7], ('door incomplete', o, q)
    owner = {}
    for c in p['components']:
        ops = p['ops'][c['first']:c['first'] + c['count']]
        assert ops and all(o[7] == c['id'] for o in ops), ('component not contiguous', c['id'])
        assert c['chunks'] == [list(k) for k in sorted({F.chunk_of(o[0], o[2]) for o in ops})], ('chunk list', c)
        for k in range(c['first'], c['first'] + c['count']): owner[k] = c['id']
    assert all(o[7] is None for k, o in enumerate(p['ops']) if k not in owner), 'component op outside its range'
    assert p['chunks'] == [list(k) for k in sorted({F.chunk_of(o[0], o[2]) for o in p['ops']})]
    assert p['plan_key'] == C.plan_key(dict(inputs_sha=p['selection']['inputs_sha']), p['geometry_sha'], p['seed'])


@test
def flat_face_package():
    p = get('package', 'fx-flat', 'flat'); check_plan(p, 'flat')
    assert p['status'] == 'ready', p['reasons']
    ev = {f['id']: f['evidence'] for f in p['faces']}
    assert ev == {'p0:f0': 'reference', 'p0:f1': 'measured', 'p0:f2': 'inferred', 'p0:f3': 'unresolved'}, ev
    assert [u['face'] for u in p['unresolved']] == ['p0:f3'] and 'unknown' in p['unresolved'][0]['reason']
    f3 = face_cells('flat', 'p0:f3')
    assert not any((o[0], o[2]) in f3 for o in p['ops']), 'ops on an unknown face'
    fev = {}
    for c in p['components']: fev[c['id']] = ev[c['face']]
    assert all(o[8] == (fev[o[7]] if o[7] else o[8]) for o in p['ops'])
    kinds = p['stats']['components_by_kind']
    assert kinds.get('fx-door') == 1 and kinds.get('fx-canopy-d2') == 1 and kinds.get('fx-sign') == 1 and kinds.get('fx-pane-3x2'), kinds
    door = [o for o in p['ops'] if '_door' in o[4]]
    assert len(door) == 2 and all('facing=north' in o[4] for o in door), door            # f0 faces south (+z): door faces inward
    assert any(o[6] == 'canopy' for o in p['ops']) and all(o[1] >= 64 for o in p['ops'])
    assert any('smooth_quartz' in o[4] for o in p['ops'] if o[6] == 'wall'), 'zone podium on p0:f1'
    assert any(o[4] == 'minecraft:sandstone' for o in p['ops']), 'texture rows%3==2'


@test
def rotated_11_4_face_and_corner():
    p = get('style', 'fx-style', 'rot114'); check_plan(p, 'rot114')
    assert p['status'] == 'ready' and all(o[8] == 'style' for o in p['ops'])
    for f in GEO['rot114']['faces']:
        assert len({(c[4], c[5]) for c in f['columns']}) == 1, 'stepped wall keeps one outward step per straight face'
    stairs = {F.parse_block(o[4])[1]['facing'] for o in p['ops'] if '_stairs' in o[4]}
    assert stairs <= {'north', 'south', 'east', 'west'} and len(stairs) >= 2, stairs
    corner = [o for o in p['ops'] if o[6] in ('column', 'trim') and o[7] is None]
    cols = {(o[0], o[2]) for o in corner}
    own = len(C.corner_owners(GEO['rot114']['faces']))
    assert own == 4 and len(cols) == 4, ('each geometric corner gets exactly one corner column', own, cols)


@test
def multipart_setback_and_shared_wall():
    p = get('package', 'fx-multipart', 'multipart'); check_plan(p, 'multipart')
    shared = [f['id'] for f in GEO['multipart']['faces'] if f['edge'] == 'shared']
    assert sorted(shared) == ['p0:f1', 'p2:f3']
    assert not [c for c in p['components'] if c['face'] in shared], 'openings on a shared wall'
    assert {u['face'] for u in p['unresolved']} >= {'p1:f3'}
    tower = [c for c in p['components'] if c['face'].startswith('p1:')]
    assert tower and min(c['anchor'][1] for c in tower) >= 76, 'setback tower starts on the podium roof'
    s = get('style', 'fx-style', 'multipart'); check_plan(s, 'multipart')
    assert not any(c['face'] in shared for c in s['components'])
    for sid in shared: assert next(f for f in s['faces'] if f['id'] == sid)['ops'] == 0


@test
def curved_segmented_face():
    p = get('style', 'fx-style', 'curved'); check_plan(p, 'curved')
    arc = next(f for f in GEO['curved']['faces'] if len(f['segments']) > 1)
    us = [s['u0'] for s in arc['segments']]; assert all(abs(a['u1'] - b['u0']) < 1e-6 for a, b in zip(arc['segments'], arc['segments'][1:])) and us == sorted(us)
    comps = [c for c in p['components'] if c['face'] == arc['id']]
    assert len(comps) >= 6
    fac = {F.parse_block(o[4])[1]['facing'] for c in comps for o in comp_ops(p, c['id']) if '_stairs' in o[4] or '_door' in o[4]}
    assert len(fac) >= 3, ('placeholders follow the per-segment normal', fac)
    on = face_cells('curved', arc['id'])
    wall = [o for c in comps for o in comp_ops(p, c['id']) if o[6] == 'glass']
    assert all((o[0], o[2]) in on for o in wall), 'glass sits on the voxelised wall line (columns followed, w=0)'


@test
def short_residual_bays():
    assert C.bays(11, 4, 'center') == ([(1, 4), (5, 4)], '')            # gaps first
    assert C.bays(11, 4, 'ends')[0] == [(0, 5), (5, 6)] and C.bays(11, 4, 'absorb')[0] == [(0, 6), (6, 5)]
    b, note = C.bays(9, 4, 'center'); assert len(b) == 2 and not note     # short facade: never adds or drops a bay
    b, note = C.bays(31, 4, 'center'); assert len(b) == 8 and note        # long facade: one bay added, widths -1
    for geo in GEO:
        p = get('style', 'fx-style', geo)
        for f in GEO[geo]['faces']:
            w = p['stats']['bays'].get(f['id'])
            if w: assert sum(w) <= len(f['columns']), 'never stretch the building'


@test
def style_labelled_style():
    p = get('style', 'fx-style', 'flat'); check_plan(p, 'flat')
    assert p['reasons'][0].startswith('STYLE-GENERATED') and p['selection']['kind'] == 'style'
    assert p['ops'] and all(o[8] == 'style' for o in p['ops']) and all(c['evidence'] == 'style' for c in p['components'])
    assert next(f for f in p['faces'] if f['id'] == 'p0:f3')['evidence'] == 'style', 'a selected style covers unknown faces'


@test
def insufficient_package_unresolved():
    p = get('package', 'fx-insufficient', 'flat')
    assert p['status'] == 'unresolved' and not p['ops'] and len(p['unresolved']) == 4, (p['status'], p['unresolved'])
    assert any('insufficient evidence' in u['reason'] for u in p['unresolved'])


@test
def invalid_package_rejected_with_path():
    sel, assets, errs, st = S.load_selection('package', 'fx-invalid')
    assert st == 'invalid_package' and '$.faces[0].script: forbidden key (packages are data only)' in errs, errs
    assert '$.faces[0].floors.height: expected integer' in errs, errs
    _, errs, _, st = S.load_selection('package', 'fx-noexception')[0:1] + S.load_selection('package', 'fx-noexception')[2:3] + (None, None)
    assert any(e.startswith('$.limits.exception') for e in errs), errs
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, 'p'))
        y = os.path.join(d, 'p', 'package.yaml'); open(y, 'w').write('schema: facade-package-1\nsources:\n  - date: 2024-01-01\n    __class__: x\n')
        _, e = S.validate_package(y)
        assert '$.sources[0].date: non-JSON value of type date (quote YAML dates and avoid tags)' in e and '$.sources[0].__class__: forbidden key (packages are data only)' in e, e
        d0 = json.load(open(os.path.join(FX, 'packages', 'fx-flat', 'package.json')))
        d0['package']['id'] = 'p'; d0['sources'][0].update(local='../../secret.jpg', sha256='0' * 64)
        json.dump(d0, open(os.path.join(d, 'p', 'package.json'), 'w')); os.remove(y)
        _, e = S.validate_package(os.path.join(d, 'p', 'package.json'))
        assert any(x.startswith('$.sources[0].local:') and 'escapes' in x for x in e), e
        big = os.path.join(d, 'p', 'package.json'); open(big, 'w').write(' ' * (F.PKG_MAX_BYTES + 1))
        _, e = S.validate_package(big); assert 'limit' in e[0], e


@test
def component_atomicity_and_chunks():
    p = get('package', 'fx-flat', 'flat')
    for c in p['components']:
        ops = comp_ops(p, c['id'])
        roles = [o[6] for o in ops]
        order = [C.ORDER.get(r, 3) for r in roles]; assert order == sorted(order), ('backing, frame, glass, trim, canopy order', c['kind'], roles)
    win = [c for c in p['components'] if c['kind'] == 'fx-window-2x3']
    assert win and all(any(o[6] == 'backing' for o in comp_ops(p, c['id'])) for c in win), 'automatic backing behind glass is inside the component'
    assert min(o[0] for o in p['ops']) < 0 and all(x < 0 for x, _ in [tuple(k) for k in p['chunks']]), 'negative chunk math'
    assert F.chunk_of(-1, -17) == (-1, -2) and F.chunk_of(-16, 15) == (-1, 0)


@test
def valid_states_and_props():
    db, _ = S.blockdb()
    sill = PT.resolve_cell({'role': 'sill', 'props': {'type': 'top', 'half': 'top', 'facing': '{facing}'}}, {'sill': 'minecraft:smooth_quartz_slab'}, db)
    assert sill == 'minecraft:smooth_quartz_slab[type=top]', sill                       # props merge only where the block has them
    pal = C.Palette({'roles': {'sill': 'minecraft:spruce_trapdoor'}}, 1, db)
    b = pal.cell({'role': 'sill', 'props': {'type': 'top', 'half': 'top', 'facing': '{facing}'}}, None, 0, 0, {'facing': 'south'})
    assert b == 'minecraft:spruce_trapdoor[half=top,facing=south]', b
    for k in list(P): check_plan(P[k], k[2])
    assert S.check_block('minecraft:oak_door[half=middle]') and S.check_block('minecraft:sand') and S.check_block('minecraft:stone') is None


@test
def pane_connections():
    p = get('package', 'fx-flat', 'flat')
    panes = {tuple(o[:3]): F.parse_block(o[4])[1] for o in p['ops'] if o[4].startswith('minecraft:gray_stained_glass_pane')}
    comp = {tuple(o[:3]): o[7] for o in p['ops']}
    assert panes
    f1 = C.Face(next(f for f in GEO['flat']['faces'] if f['id'] == 'p0:f1'))
    ph = f1.ph(0); along = (ph['facing_left'], ph['facing_right'])
    seam = 0
    for c, st in panes.items():                                  # along u: true unless the neighbour lies across a chunk seam (M5)
        for d in along:
            dx, dz = F.CARD[d]; n = (c[0] + dx, c[1], c[2] + dz)
            cross = F.chunk_of(n[0], n[2]) != F.chunk_of(c[0], c[2]) and not (comp[c] and comp.get(n) == comp[c]); seam += cross
            assert st[d] == ('false' if cross else 'true'), (c, d, st)
    assert seam, 'fixture must include a pane run crossing a chunk seam'
    out = ph['facing']; assert all(st[out] == 'false' for st in panes.values()), 'nothing planned outside the pane'


@test
def determinism():
    a = plan_of('package', 'fx-flat', 'flat'); b = plan_of('package', 'fx-flat', 'flat')
    assert F.sha_obj(a) == F.sha_obj(b) and a['plan_key'] == b['plan_key']
    c = plan_of('package', 'fx-flat', 'flat', seed=2); assert c['plan_key'] != a['plan_key']
    s1, s2 = plan_of('style', 'fx-style', 'curved'), plan_of('style', 'fx-style', 'curved'); assert F.sha_obj(s1) == F.sha_obj(s2)


@test
def limit_refusal():
    p = get('package', 'fx-limits', 'flat')
    assert p['status'] == 'refused_limits' and not p['ops'] and not p['components'] and p['limits']['max_ops'] == 50
    assert p['reasons'][0].endswith('(a package may raise limits only with limits.exception)')


@test
def massing_shell_none():
    p = get('package', 'fx-massing', 'flat'); check_plan(p, 'flat')
    assert p['status'] == 'ready' and p['stats']['massing']
    f3 = face_cells('flat', 'p0:f3')
    m = [o for o in p['ops'] if (o[0], o[2]) in f3 and o[1] <= 80]
    assert m and all(o[8] == 'measured' and o[6] == 'wall' for o in m), 'unresolved face gets a measured massing wall'
    roof = [o for o in p['ops'] if o[6] == 'roof']; assert roof and all(o[1] == 80 and o[8] == 'measured' for o in roof)
    assert any(o[1] == 81 and o[6] == 'parapet' for o in p['ops']) and {u['face'] for u in p['unresolved']} == {'p0:f0', 'p0:f1', 'p0:f3'}


@test
def prep_cli():
    py = sys.executable; prep = os.path.join(HERE, 'facade_prep.py'); g = os.path.join(FX, 'geometry', 'flat.json')
    with tempfile.TemporaryDirectory() as cache:
        def run(*a, **env):
            r = subprocess.run([py, prep, cache, *a], capture_output=True, text=True, env=dict(os.environ, **env), timeout=120)
            lines = r.stdout.strip().splitlines(); assert len(lines) == 1, (r.stdout, r.stderr[-2000:]); return json.loads(lines[0])
        r = run('plan', 'w9000001', '--package', 'fx-flat', '--geometry', g)
        assert r['status'] == 'ready' and os.path.exists(os.path.join(cache, r['plan'])), r
        m = json.load(open(os.path.join(cache, 'facade', 'buildings', 'w9000001', 'manifest.json'))); assert 'total' in m['timings_s']
        assert run('plan', 'w9000001', '--package', 'fx-flat', '--geometry', g)['plan_key'] == r['plan_key']
        assert run('plan', 'w9000001', '--style', 'nope')['status'] == 'no_package'
        r2 = run('plan', 'w9000001'); assert r2['status'] == 'no_package' and 'fx-style' in r2['styles'], r2
        r3 = run('plan', 'w9000001', '--package', 'fx-invalid', '--geometry', g); assert r3['status'] == 'invalid_package' and r3['errors'][0].startswith('$.faces[0]')
        assert run('plan', 'w9000001', '--style', 'fx-style', '--geometry', g, FACADE_PREP_FAIL='geometry')['status'] == 'no_geometry'
        r4 = run('plan', 'w9000001', '--style', 'fx-style', '--geometry', g, '--seed', '5', FACADE_PREP_FAIL='compile')
        assert r4['status'] == 'invalid_package' and 'injected' in r4['reasons'][0]
        v = subprocess.run([py, prep, 'validate', os.path.join(FX, 'packages', 'fx-flat', 'package.json')], capture_output=True, text=True, env=os.environ)
        assert v.returncode == 0 and json.loads(v.stdout)['status'] == 'valid', v.stdout + v.stderr
        import facade_prep as FP
        txt, pngs = FP.inspect(json.load(open(os.path.join(cache, r['plan']))), GEO['flat'], os.path.join(cache, 'inspect'))
        assert 'p0:f0' in txt and 'reference' in txt and len(pngs) == 4 and all(os.path.getsize(x) > 100 for x in pngs)


@test
def adopt_candidates():
    import facade_prep as FP
    os.environ['MORIA_SHELLS_TXT'] = os.path.join(FX, 'adopt', 'shells.txt'); os.environ['MORIA_DETAIL_JOBS'] = os.path.join(FX, 'adopt', 'jobs')
    d = FP.adopt_candidates('w9000001', ['w9000001'], GEO['flat']['envelope'])
    assert d['cells'] == [[-40, 65, 100, 'minecraft:stone'], [-39, 65, 100, 'minecraft:smooth_quartz'], [-38, 80, 105, 'minecraft:light_gray_wool'],
                          [-37, 70, 100, 'minecraft:gray_stained_glass']], d['cells']
    assert len(d['missing']) == 1 and d['missing'][0].startswith('job j2: 1 of 1') and len(d['source_sha']) == 64


GEO['party'] = json.load(open(os.path.join(FX, 'geometry', 'party.json')))


@test
def m2_hazardous_blocks_denied():
    _, _, errs, st = S.load_selection('package', 'fx-hazard')
    assert st == 'invalid_package', errs
    for want in ('$.components.trap.cells[0].block: minecraft:spawner is a hazardous block', '$.faces[0].parapet.cap: minecraft:tnt is a hazardous block',
                 '$.faces[0].bands[0].block: minecraft:lava is a hazardous block', '$.extras[0][3]: minecraft:chest is a block entity'):
        assert any(e.startswith(want) for e in errs), (want, errs)
    for b in ('minecraft:water', 'minecraft:fire', 'minecraft:command_block', 'minecraft:jigsaw', 'minecraft:barrier', 'minecraft:hopper',
              'minecraft:oak_sign', 'minecraft:sand', 'minecraft:bubble_column', 'minecraft:structure_block'):
        assert S.denied(b), b
    assert S.denied('minecraft:air') is None and S.denied('minecraft:end_rod') is None
    sel, assets, errs, _ = S.load_selection('package', 'fx-flat')           # compile re-checks final states (palette bypassing the validator)
    bad = dict(assets, palette=dict(assets['palette'], roles=dict(assets['palette']['roles'], band='minecraft:sand')))
    try: C.compile(GEO['flat'], sel, bad, 1); raise AssertionError('compile accepted a gravity block')
    except ValueError as e: assert 'sand' in str(e) and 'denied' in str(e), e


@test
def m3_party_wall():
    party = face_cells('party', 'p0:f1')
    p = get('style', 'fx-style', 'party'); check_plan(p, 'party')
    assert not any((o[0], o[2]) in party for o in p['ops']), 'style ops on a party wall'
    m = get('package', 'fx-party', 'party'); check_plan(m, 'party')
    assert m['stats']['massing'] and not any((o[0], o[2]) in party and o[6] in ('wall', 'parapet') for o in m['ops']), 'massing wall on a party wall'
    assert not [c for c in m['components'] if c['face'] == 'p0:f1']


@test
def m4_cache_keys():
    import facade_prep as FP
    k = FP.geometry_key('w1', ['w1', 'w2'], None, 'g', 'i')
    assert k == FP.geometry_key('w1', ['w2', 'w1'], None, 'g', 'i') != FP.geometry_key('w1', ['w1'], None, 'g', 'i')
    assert k != FP.geometry_key('w1', ['w1', 'w2'], [{'id': 'p9'}], 'g', 'i') and k != FP.geometry_key('w1', ['w1', 'w2'], None, 'g', 'j')
    pdir = os.path.join(FX, 'packages')
    assert FP.valid_package('fx-flat', pdir) and not FP.valid_package('fx-invalid', pdir)
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, 'a')); q = os.path.join(d, 'a', 'package.json'); open(q, 'w').write('{}')
        s1 = FP.packages_sha(d); open(q, 'w').write('{"x":1}'); assert FP.packages_sha(d) != s1


@test
def m5_connections_never_cross_chunk_seams():
    n_conn = 0
    for k, p in list(P.items()):
        byc = {tuple(o[:3]): o for o in p['ops']}
        for o in p['ops']:
            n, st = F.parse_block(o[4])
            if not C.conn_kind(n): continue
            for d, dx, dz in C.DIRS:
                if st.get(d) not in ('true', 'low', 'tall'): continue
                q = byc.get((o[0] + dx, o[1], o[2] + dz)); n_conn += 1
                assert q, ('connection toward an unplanned cell', k, o, d)
                assert (o[7] and q[7] == o[7]) or (q[7] is None and F.chunk_of(q[0], q[2]) == F.chunk_of(o[0], o[2])), ('seam', k, o, d, q)
    assert n_conn


@test
def m6_stairs_shape_explicit():
    n = 0
    for p in P.values():
        for o in p['ops']:
            if '_stairs' in o[4]: assert 'shape=' in o[4], o; n += 1
    assert n


@test
def m7_yaml_alias_bomb():
    import time
    with tempfile.TemporaryDirectory() as d:
        q = os.path.join(d, 'package.yaml')
        open(q, 'w').write('a: &a ["x","x","x","x","x","x","x","x","x"]\n' + ''.join(f'{c}: &{c} [*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p}]\n'
                                                                                  for p, c in zip('abcdefgh', 'bcdefghi')))
        t = time.time(); _, e = S.validate_package(q)
        assert time.time() - t < 2 and e and 'anchors/aliases' in e[0], e
        open(q, 'w').write('a: [' + ','.join(['1'] * (S.YAML_MAX_NODES + 5)) + ']\n')
        _, e = S.load(q, 10 ** 8); assert e and 'nodes' in e[0], e[:1]


@test
def cap_roof_backing_extras():
    p = get('package', 'fx-roof', 'flat'); check_plan(p, 'flat')
    f2 = C.Face(next(f for f in GEO['flat']['faces'] if f['id'] == 'p0:f2'))
    caps = [o for o in p['ops'] if o[4] == 'minecraft:smooth_stone_slab[type=bottom,waterlogged=false]' and o[1] == 80]
    assert len(caps) >= f2.N - 2, ('parapet cap placed on the top row', len(caps), f2.N)
    walls = {(int(c[0]), int(c[1])) for f in GEO['flat']['faces'] for c in f['columns']}
    roof = [o for o in p['ops'] if o[6] == 'roof']
    assert roof and all(o[1] == 80 and (o[0], o[2]) not in walls and o[8] == 'reference' for o in roof)
    assert len(roof) == 28 * 18, len(roof)
    edge = [o for o in p['ops'] if o[6] == 'roof-edge' and o[1] == 81]; assert len(edge) == len(walls), (len(edge), len(walls))
    excl = {f2.cell(i, 0, -1)[0::2] for i in range(9)}
    assert not any((o[0], o[2]) in excl for o in p['ops'] if o[6] == 'backing'), 'backing_exclude_u'
    assert [c for c in p['components'] if c['kind'] == 'fx-window-2x3' and not any(o[6] == 'backing' for o in comp_ops(p, c['id']))], 'backing: none'
    ex = [c for c in p['components'] if c['kind'] == 'extra']
    assert len(ex) == 1 and comp_ops(p, ex[0]['id'])[0][:3] == [-25, 70, 120] and any('extras[1] outside the envelope' in r for r in p['reasons']), p['reasons']



@test
def envelope_holes_v061():
    g = json.loads(json.dumps(GEO['flat'])); hole = [[-35, 120], [-30, 120], [-30, 121.5], [-35, 121.5]]
    g['envelope']['holes_mc'] = [hole]; g['geometry_sha'] = F.sha_obj({k: v for k, v in g.items() if k != 'geometry_sha'})
    assert not S.validate_geometry(g)
    sel, assets, errs, _ = S.load_selection('package', 'fx-flat'); p = C.compile(g, sel, assets, 1); check_plan(p, 'flat')
    from shapely.geometry import Polygon, Point
    H = Polygon(hole)
    assert p['status'] == 'ready' and not any(H.contains(Point(o[0] + 0.5, o[2] + 0.5)) for o in p['ops']), 'op inside a hole'
    assert p['envelope']['holes_mc'] == [hole] and any('outside the envelope' in r for r in p['reasons']), p['reasons']
    base = get('package', 'fx-flat', 'flat'); assert any(H.contains(Point(o[0] + 0.5, o[2] + 0.5)) for o in base['ops']), 'fixture must hit the hole'



@test
def ground_contact_rows_plain():
    import copy
    sel, assets, errs, _ = S.load_selection('package', 'fx-flat')
    a2 = copy.deepcopy(assets)
    a2['components']['pier'] = dict(id='pier10', size=dict(u=1, v=10), cells=[dict(u=0, v=k, w=0, role='frame') for k in range(10)])
    sel2 = dict(sel, doc=copy.deepcopy(sel['doc']))
    sel2['doc']['faces'][2]['floors'] = dict(base_v=0, height=12, count=1)
    sel2['doc']['faces'][2]['openings'] = [dict(component='pier', bays='all', v=0), dict(component='door', bays='center', v=0, replace='pier')]
    p = C.compile(GEO['flat'], sel2, a2, 1); check_plan(p, 'flat')
    piers = [c for c in p['components'] if c['kind'] == 'pier10']; doors = [c for c in p['components'] if c['kind'] == 'fx-door']
    assert piers and doors
    for c in piers:
        ops = comp_ops(p, c['id']); assert len(ops) == 8 and min(o[1] for o in ops) == 64 + 2, ('component starts at v = 2', ops[:2])
    base = [o for o in p['ops'] if o[7] is None and o[1] in (64, 65) and o[6] == 'frame']
    assert len(base) == 2 * len(piers) and all(o[8] == 'inferred' for o in base), ('v 0/1 are plain ops with the same role/evidence', len(base))
    for c in doors:
        ops = comp_ops(p, c['id']); assert sorted(o[1] for o in ops) == [64, 65], 'door stays atomic (lower + upper)'
    assert p['stats']['ground_contact_cells'] >= len(base)


# ---------------------------------------------------------------- automatic facades (AUTO.md): autospec, modes, auto command
REAL_ASSETS = os.path.join(HERE, 'facade_assets'); AUTO_STYLE = 'lowrise-office'       # the library: auto components + a style without max_levels 2
class real_assets:
    def __enter__(self): self.o = os.environ['MORIA_FACADE_ASSETS']; os.environ['MORIA_FACADE_ASSETS'] = REAL_ASSETS
    def __exit__(self, *e): os.environ['MORIA_FACADE_ASSETS'] = self.o

def observed_face(U=30, **kw):
    import facade_autospec as A
    m = U / 2
    f = dict(A.empty_face('observed'), evidence=[dict(provider='local', image_id='fx1', captured_at='2024-05-01', dist_m=20, view_angle_deg=10,
                                                       assoc_score=0.9, attribution='synthetic fixture')], confidence=0.8,
             zones=[dict(v0_m=0, v1_m=17, u0_m=0, u1_m=U, role='wall', rgb=[200, 190, 150], conf=0.8),
                    dict(v0_m=0, v1_m=1, u0_m=0, u1_m=U, role='base', rgb=[90, 90, 90], conf=0.7)],
             glazing=dict(fraction=0.45, rgb=[60, 80, 100], conf=0.7), floors=dict(first_m=5, height_m=4, count=3, conf=0.7),
             bays=dict(pitch_m=5, width_m=2, offset_m=0, conf=0.4), entrances=[dict(u0_m=m - 1, u1_m=m + 1, conf=0.9)],
             canopies=[dict(u0_m=m - 5, u1_m=m + 5, v_m=4, depth=2, conf=0.6)])
    f.update(kw); return f

def autospec(statuses, geo='flat'):
    import facade_autospec as A
    g = GEO[geo]; gf = {f['id']: f for f in g['faces']}
    faces = {fid: observed_face(A.extent(gf[fid])[0]) if st == 'observed' else A.empty_face(st, ['fixture']) for fid, st in statuses.items()}
    return dict(schema=A.SCHEMA, bid=g['bid'], members=g['members'], geometry_key=g['geometry_sha'], created='2026-10-29T00:00:00',
                analyzer=dict(backend='builtin-cv', version='fx', model=None), mode=A.mode_of(list(statuses.values())),
                cost=dict(usd=0.0, requests=0, by_provider={}), faces=faces)
FX_PAL = {'p0:f0': {'roles': {'wall': 'minecraft:smooth_sandstone', 'glass': 'minecraft:light_blue_stained_glass', 'base': 'minecraft:stone_bricks',
                              'band': 'minecraft:tnt'}, 'zones': ['minecraft:white_concrete', 'minecraft:polished_andesite']}}


@test
def autospec_validation():
    import facade_autospec as A, copy
    g = GEO['flat']; ok = autospec({'p0:f0': 'observed', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'unresolved'})
    assert A.validate(ok, g) == [], A.validate(ok, g)
    def errs(mut):
        s = copy.deepcopy(ok); mut(s); return A.validate(s, g)
    e = errs(lambda s: s['faces']['p0:f0'].update(colour='red')); assert any(x == '$.faces.p0:f0.colour: unknown key' for x in e), e
    e = errs(lambda s: s.update(extra=1)); assert '$.extra: unknown key' in e, e
    e = errs(lambda s: s['faces']['p0:f0']['zones'][0].update(rgb=[300, 0, 0])); assert any('rgb[0]: 300 outside [0, 255]' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(confidence=1.5)); assert any('confidence: 1.5 outside' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0']['zones'][0].update(u1_m=31.5)); assert any('outside the measured face extent' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0']['canopies'][0].update(v_m=40)); assert any('canopies[0].v_m' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(zones=[s['faces']['p0:f0']['zones'][0]] * 33)); assert any('too many items' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(top_y=120)); assert any('shell-changing field' in x for x in e), e            # shell stretch
    e = errs(lambda s: s['faces']['p0:f0'].update(floors=dict(first_m=5, height_m=4, count=9, conf=0.9))); assert any('never stretched' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f1'].update(glazing=dict(fraction=0.5, rgb=[1, 2, 3], conf=1))); assert any('carries no observations' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(evidence=[])); assert any('needs at least one evidence image' in x for x in e), e
    e = errs(lambda s: s['faces'].update({'p9:f9': A.empty_face('unresolved')})); assert any('not a face of the measured geometry' in x for x in e), e
    e = errs(lambda s: s.update(mode='evidence')); assert any('$.mode' in x for x in e), e
    e = errs(lambda s: s.update(geometry_key='0' * 64)); assert any('$.geometry_key' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(script='x')); assert any('forbidden key' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(confidence=float('nan'))); assert any('non-finite' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(absent=['glazing'])); assert any('glazing is listed absent but also described' in x for x in e), e
    e = errs(lambda s: s['faces']['p0:f0'].update(absent=['doors'])); assert any('absent[0]' in x for x in e), e
    s2 = copy.deepcopy(ok); s2['faces']['p0:f0'].update(bays=None, absent=['bays']); assert A.validate(s2, g) == [], A.validate(s2, g)
    assert A._states(s2['faces']['p0:f0'])['bays'] == 'absent' and A._states(ok['faces']['p0:f0'])['bays'] != 'absent'
    try: A.autospec_to_package(dict(ok, mode='bogus'), g, {}, AUTO_STYLE); raise AssertionError('accepted an invalid spec')
    except ValueError as x: assert 'autospec rejected' in str(x)


@test
def autospec_deterministic_compile():
    import facade_autospec as A
    with real_assets():
        s = autospec({'p0:f0': 'observed', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'unresolved'})
        a = A.compile_autospec(s, GEO['flat'], FX_PAL, AUTO_STYLE); b = A.compile_autospec(json.loads(json.dumps(s)), GEO['flat'], json.loads(json.dumps(FX_PAL)), AUTO_STYLE)
        assert F.canon(a['ops']) == F.canon(b['ops']) and F.sha_obj(a) == F.sha_obj(b) and a['plan_key'] == b['plan_key']
        c = A.compile_autospec(dict(s, created='2027-01-01'), GEO['flat'], FX_PAL, AUTO_STYLE); assert c['plan_key'] != a['plan_key'], 'the spec is the compile input'
        check_plan(a, 'flat')
        assert not any('tnt' in o[4] for o in a['ops']) and any('refused for role band' in r for r in a['reasons']), 'materials go through the deny-list'


@test
def autospec_modes_evidence_procedural_mixed():
    import facade_autospec as A
    with real_assets():
        g = GEO['flat']; f0 = face_cells('flat', 'p0:f0')
        ev = A.compile_autospec(autospec({'p0:f0': 'observed', 'p0:f1': 'observed', 'p0:f2': 'observed', 'p0:f3': 'unresolved'}), g, FX_PAL, AUTO_STYLE)
        check_plan(ev, 'flat'); assert ev['status'] == 'ready' and ev['auto']['mode'] == 'evidence'
        cls = ev['stats']['ops_by_evidence']; assert cls.get('observed') and 'style' not in cls, cls
        assert set(cls) <= {'observed', 'inferred'} and ev['reasons'][0].startswith('AUTO evidence: 3 observed'), ev['reasons'][0]
        kinds = ev['stats']['components_by_kind']; assert kinds.get('door-double') == 3 and kinds.get('canopy-flat-d2') == 3, kinds
        assert any(o[4] == 'minecraft:white_concrete' and o[6] == 'wall' and o[8] == 'observed' for o in ev['ops']), 'observed wall zone material'
        assert any(o[4] == 'minecraft:polished_andesite' and o[1] == 64 for o in ev['ops'] if (o[0], o[2]) in f0), 'observed base zone'
        win = [c for c in ev['components'] if c['kind'].startswith('window') or c['kind'] == 'storefront-bay']
        assert not win and any('window positions not measured' in r for r in ev['reasons']), 'a glazing fraction alone never places template windows'
        pr = A.compile_autospec(autospec({'p0:f0': 'procedural', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'procedural'}), g, {}, AUTO_STYLE)
        check_plan(pr, 'flat'); assert pr['auto']['mode'] == 'procedural' and pr['ops'] and all(o[8] == 'style' for o in pr['ops'])
        assert 'STYLE-GENERATED (not observed)' in pr['reasons'][0] and 'observed' not in pr['stats']['ops_by_evidence']
        mx = A.compile_autospec(autospec({'p0:f0': 'observed', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'unresolved'}), g, FX_PAL, AUTO_STYLE)
        check_plan(mx, 'flat'); fe = {f['id']: f for f in mx['faces']}
        assert fe['p0:f0']['evidence'] == 'observed' and set(fe['p0:f0']['ops_by_evidence']) <= {'observed', 'inferred'}
        assert fe['p0:f1']['evidence'] == 'style' and set(fe['p0:f1']['ops_by_evidence']) == {'style'} and fe['p0:f3']['evidence'] == 'unresolved'
        assert mx['auto']['faces']['p0:f0']['images'] == 1 and 'STYLE-GENERATED (not observed) on: p0:f1, p0:f2' in mx['reasons']


@test
def autospec_model_backend_never_observed():
    import facade_autospec as A
    with real_assets():
        s = autospec({'p0:f0': 'observed', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'unresolved'})
        s['analyzer'] = dict(backend='ollama', version='fx', model='test-vlm')
        for k in ('zones', 'entrances', 'canopies'):
            for it in s['faces']['p0:f0'][k]: it['conf'] = min(it['conf'], 0.49)
        for k in ('glazing', 'floors', 'bays'): s['faces']['p0:f0'][k]['conf'] = min(s['faces']['p0:f0'][k]['conf'], 0.49)
        p = A.compile_autospec(s, GEO['flat'], FX_PAL, AUTO_STYLE)
        fe = {f['id']: f for f in p['faces']}
        assert fe['p0:f0']['evidence'] == 'inferred' and 'observed' not in p['stats']['ops_by_evidence'], p['stats']['ops_by_evidence']
        assert '0 observed face(s), 1 model-INFERRED face(s) (test-vlm, uncalibrated' in p['reasons'][0], p['reasons'][0]


@test
def measured_glazing_stays_continuous():
    import facade_autospec as A
    with real_assets():
        s = autospec({'p0:f0': 'observed', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'unresolved'})
        U = A.extent({f['id']: f for f in GEO['flat']['faces']}['p0:f0'])[0]
        s['faces']['p0:f0'].update(openings=[dict(u0_m=2.0, u1_m=U - 2.0, v0_m=1.0, v1_m=4.0, kind='glazing', conf=0.8)], entrances=[])
        p = A.compile_autospec(s, GEO['flat'], FX_PAL, AUTO_STYLE); check_plan(p, 'flat')
        kinds = {c['kind'] for c in p['components'] if c['face'] == 'p0:f0'}
        assert not any(k.startswith('window') or k == 'storefront-bay' for k in kinds), kinds
        f0 = face_cells('flat', 'p0:f0')
        glass = sorted({(o[0], o[2]) for o in p['ops'] if (o[0], o[2]) in f0 and o[6] == 'glass' and o[1] == 66})
        assert len(glass) >= int(U) - 5, (len(glass), U)          # one unbroken run across the measured width (no per-bay gaps)


@test
def cost_merges_source_request_counts():
    import facade_prep as FP
    c = FP._cost({'usd': 0.0, 'requests': 1, 'by_provider': {'local': 0, 'panoramax': 1}}, {'usd': 0.0, 'requests': 2, 'by_provider': {'ollama': {'usd': 0.0, 'requests': 2}}})
    assert c['requests'] == 3 and c['by_provider']['panoramax'] == {'usd': 0.0, 'requests': 1} and c['by_provider']['ollama']['requests'] == 2, c


@test
def build_spec_rejects_uncited_evidence():
    import facade_prep as FP
    g = GEO['flat']; f = observed_face(30)
    acc = [dict(provider='local', image_id='fx1')]
    an = {'faces': {'p0:f0': f}}
    ok, rej = FP.build_spec(g, {}, {'faces': {'p0:f0': {'accepted': acc}}}, an, 'none', dict(backend='builtin-cv', version='fx', model=None))
    assert not rej and ok['faces']['p0:f0']['status'] == 'observed', rej
    bad, rej = FP.build_spec(g, {}, {'faces': {'p0:f0': {'accepted': [dict(provider='local', image_id='other')]}}}, an, 'none', dict(backend='builtin-cv', version='fx', model=None))
    assert bad['faces']['p0:f0']['status'] == 'unresolved' and any('not an accepted image of this face' in r for r in rej), rej
    # a declined/failed analysis keeps its own reason (it lists the image it read; that must not turn into a validation rejection)
    dec = dict(A_empty := __import__('facade_autospec').empty_face('unresolved', ['panorama rectification not implemented in builtin-cv']), evidence=f['evidence'])
    sp, rej = FP.build_spec(g, {}, {'faces': {'p0:f0': {'accepted': acc}}}, {'faces': {'p0:f0': dec}}, 'procedural', dict(backend='builtin-cv', version='fx', model=None))
    assert not rej and sp['faces']['p0:f0']['status'] == 'procedural' and 'panorama' in sp['faces']['p0:f0']['unresolved'][0], (rej, sp['faces']['p0:f0'])


@test
def autospec_fallback_none():
    import facade_autospec as A
    with real_assets():
        p = A.compile_autospec(autospec({'p0:f0': 'unresolved', 'p0:f1': 'unresolved', 'p0:f2': 'unresolved', 'p0:f3': 'unresolved'}), GEO['flat'], {}, None)
        assert not p['ops'] and p['status'] == 'unresolved' and len(p['unresolved']) == 4, (p['status'], p['unresolved'])
        q = A.compile_autospec(autospec({'p0:f0': 'procedural', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'procedural'}), GEO['flat'], {}, None)
        assert not q['ops'] and all('without a fallback style' in u['reason'] for u in q['unresolved'] if u['face'] != 'p0:f3'), q['unresolved']


@test
def auto_build_spec_from_analysis():
    import facade_prep as FP, facade_autospec as A
    g = GEO['flat']; good = observed_face(); good.pop('evidence'); bad = observed_face(stretch=2); low = observed_face(confidence=0.1)
    acc = {'p0:f0': {'accepted': [dict(provider='local', image_id='a', captured_at=None, dist_m=10, view_angle_deg=5, assoc_score=0.8, attribution='x', extra='dropped')]}}
    s, rej = FP.build_spec(g, {'cost': {'usd': 0.01, 'requests': 2, 'by_provider': {'local': {'usd': 0, 'requests': 2}}}}, {'faces': acc},
                           {'faces': {'p0:f0': good, 'p0:f1': bad, 'p0:f2': low}, 'cost': {'usd': 0.02, 'requests': 1}}, 'procedural', dict(backend='builtin-cv', version='1', model=None))
    assert A.validate(s, g) == [] and s['mode'] == 'mixed' and s['cost']['usd'] == 0.03 and s['cost']['requests'] == 3
    st = {k: f['status'] for k, f in s['faces'].items()}; assert st == {'p0:f0': 'observed', 'p0:f1': 'procedural', 'p0:f2': 'procedural', 'p0:f3': 'procedural'}, st
    assert s['faces']['p0:f0']['evidence'][0]['image_id'] == 'a' and 'rejected' in s['faces']['p0:f1']['unresolved'][0] and rej
    s2, _ = FP.build_spec(g, {}, {}, None, 'none', dict(backend='none', version='0', model=None))
    assert s2['mode'] == 'procedural' and all(f['status'] == 'unresolved' for f in s2['faces'].values())
    assert FP.redact('https://x/y?access_token=abc123&z=1') == 'https://x/y?access_token=REDACTED&z=1'


@test
def auto_cli_outage_cache_fallback():
    py = sys.executable; prep = os.path.join(HERE, 'facade_prep.py'); g = os.path.join(FX, 'geometry', 'flat.json')
    with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as refs:
        env0 = dict(os.environ, MORIA_FACADE_ASSETS=REAL_ASSETS, MORIA_FACADE_PROVIDERS='local', MORIA_FACADE_REFS_DIR=refs)
        def run(*a, **env):
            r = subprocess.run([py, prep, cache, 'auto', 'w9000001', '--geometry', g, '--fallback-style', AUTO_STYLE, *a], capture_output=True, text=True,
                               env=dict(env0, **env), timeout=300)
            lines = r.stdout.strip().splitlines(); assert len(lines) == 1, (r.stdout, r.stderr[-2000:]); return json.loads(lines[0])
        o = run(FACADE_PREP_FAIL='sources')                                     # provider outage: never aborts, labelled procedural
        assert o['status'] == 'ready' and o['mode'] == 'procedural' and o['providers'] == {'local': 'unavailable'}, o
        assert o['reasons'][0].startswith('AUTO procedural') and 'STYLE-GENERATED (not observed)' in o['reasons'][0] and o['ops_by_evidence'].keys() == {'style'}
        assert {'retrieval_s', 'association_s', 'analysis_s', 'compile_s', 'total'} <= set(o['timings_s']) and o['cost']['usd'] == 0
        assert o['selection']['kind'] == 'auto' and o['faces']['p0:f0']['status'] == 'procedural' and o['faces']['p0:f0']['plan_evidence'] == 'style'
        bd = os.path.join(cache, 'facade', 'buildings', 'w9000001')
        assert json.load(open(os.path.join(bd, 'auto-progress.json')))['stage'] == 'ready'
        r1 = run(); r2 = run()                                                   # cache hit on repeat
        assert not r1['autospec_cached'] and r2['autospec_cached'] and r2['autospec'] == r1['autospec'] and r2['plan_key'] == r1['plan_key'], (r1, r2)
        assert r2['timings_s'].get('plan_cached') and r2['ops'] == r1['ops'] > 0
        r3 = run('--refresh'); assert not r3['autospec_cached'] and r3['autospec'] != r1['autospec'] and os.path.exists(os.path.join(cache, r1['autospec']))
        n = run('--fallback', 'none'); assert n['ops'] == 0 and n['status'] in ('unresolved', 'nothing_to_do') and n['fallback_style'] is None, n
        assert run(FACADE_PREP_FAIL='compile')['status'] == 'provider_failure'
        assert run('--fallback', 'maybe')['status'] == 'invalid_package'


@test
def auto_glue_local_image_end_to_end():
    """R (local provider, association) -> V (builtin-cv, materials) -> autospec -> compile, in process, on a synthetic photo of p0:f0"""
    import facade_prep as FP, facade_autospec as A
    try: import facade_sources as R, facade_associate as RA, facade_analyze as V, facade_materials as M
    except ModuleNotFoundError as e: print('  (skipped: R/V module missing:', e.name, ')'); return
    from PIL import Image
    g = GEO['flat']
    with tempfile.TemporaryDirectory() as refs, tempfile.TemporaryDirectory() as cache, real_assets():
        os.makedirs(os.path.join(refs, g['bid'])); img = os.path.join(refs, g['bid'], 'a.jpg'); Image.new('RGB', (1024, 768)).save(img)
        json.dump(dict(images=[dict(file='a.jpg', x=-25.0, z=145.0, heading_mc=[0, -1], camera_type='perspective', hfov_deg=90, width=1024, height=768,
                                    captured_at='2025-06-01', licence='CC0', attribution='synthetic fixture')]), open(os.path.join(refs, g['bid'], 'refs.json'), 'w'))
        cfg = dict(FP.auto_cfg([], cache, g['members']), providers='local', refs_dir=refs)
        ev = R.fetch(g['bid'], g, cfg); assert ev['providers']['local']['status'] == 'ok' and len(ev['candidates']) == 1, ev['providers']
        assoc = RA.associate(g, ev['candidates'], [], cfg)
        acc = assoc['faces']['p0:f0']['accepted']; assert len(acc) == 1 and not assoc['faces']['p0:f2']['accepted'], assoc['faces']['p0:f0']['rejected']
        pj = V.projections_of(acc[0], next(f for f in g['faces'] if f['id'] == 'p0:f0'), (1024, 768))[0][0]
        V.synth(pj['corners_px'], (1024, 768), v_claim=pj['v_m'][1] - pj['v_m'][0])[0].convert('RGB').save(img)
        an = V.analyze_building(g, ev, assoc, cfg)
        assert an['faces']['p0:f0']['status'] == 'observed', an['faces']['p0:f0']['unresolved']
        s, rej = FP.build_spec(g, ev, assoc, an, 'procedural', V.analyzer_id(V._job_cfg(cfg)))
        assert A.validate(s, g) == [] and s['faces']['p0:f0']['status'] == 'observed', (s['faces']['p0:f0'], rej)
        assert s['mode'] == 'mixed' and s['faces']['p0:f0']['evidence'][0]['provider'] == 'local'
        pals = M.palettes(s, g, cfg); assert 'p0:f0' in pals
        p = A.compile_autospec(s, g, pals, AUTO_STYLE); check_plan(p, 'flat')
        fe = {f['id']: f for f in p['faces']}
        assert fe['p0:f0']['evidence'] == 'observed' and fe['p0:f0']['ops_by_evidence'].get('observed'), fe['p0:f0']
        assert fe['p0:f1']['evidence'] == 'style' and p['auto']['mode'] == 'mixed'


if __name__ == '__main__':
    bad = [(n, e) for n, e in RESULTS if e]
    for n, e in RESULTS: print(('FAIL ' if e else 'ok   ') + n)
    for n, e in bad: print(f'\n---- {n}\n{e}')
    print(f'\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed')
    sys.exit(1 if bad else 0)
