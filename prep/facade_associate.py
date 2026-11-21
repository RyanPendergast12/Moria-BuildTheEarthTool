"""facade_associate.py - Facade v0.6 auto: image -> face association (docs/facade/AUTO.md section 5, docs/facade/SOURCES.md). Owner: R.
    associate(geometry, candidates, obstacles, cfg=None, now=None) -> {schema, params, faces: {face_id: {status, accepted[], rejected[]}}}
    obstacles_from_index(geometry, geo, pad_m=100) -> [{id, poly}]   non-member OSM building footprints in MC (occlusion test)
    project(proj, x, y, z) -> (nx, ny) normalised image position (0..1) of a world point, or None
CLI:  facade_associate.py <cache> <bid>     reads geometry.json + evidence.json, writes association.json, prints ONE JSON line
      facade_associate.py --selftest
Reject when unsure: every rule of AUTO.md section 5 must hold, every failed rule is kept as a reason. All distances are metres
(MC horizontal deltas are divided by the candidate's blocks_per_m; vertical is 1 block = 1 m)."""
import datetime, json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SCHEMA = 'facade-association-1'
DEFAULTS = dict(min_dist_m=5.0, max_dist_m=80.0, max_view_deg=60.0, min_fov_frac=0.4, max_age_years=8.0, max_images_per_face=4,
                camera_height_m=2.5, pano_crop_deg=120.0, yaw_margin_deg=5.0, ray_trim_m=0.3, sample_m=0.5)


# ---------------------------------------------------------------- face geometry (MC)
def face_poly(face):
    """[(u, x, z)] vertices along the face, u continuous"""
    s = face['segments']
    return [(s[0]['u0'], *s[0]['a'])] + [(g['u1'], *g['b']) for g in s]


def point_at(face, u):
    for g in face['segments']:
        if u <= g['u1'] or g is face['segments'][-1]:
            t = 0.0 if g['u1'] == g['u0'] else min(1.0, max(0.0, (u - g['u0']) / (g['u1'] - g['u0'])))
            return (g['a'][0] + t * (g['b'][0] - g['a'][0]), g['a'][1] + t * (g['b'][1] - g['a'][1]))


def mean_normal(face):
    nx = sum(g['normal'][0] * (g['u1'] - g['u0']) for g in face['segments']); nz = sum(g['normal'][1] * (g['u1'] - g['u0']) for g in face['segments'])
    n = math.hypot(nx, nz) or 1.0
    return nx / n, nz / n


def obstacles_from_index(geometry, geo, pad_m=100.0):
    """every non-member OSM building within ~pad_m of the faces, projected to MC (the identify/geometry index, read only)"""
    import egg_common as E, facade_geometry as fg
    pts = [tuple(s[k]) for f in geometry['faces'] for s in f['segments'] for k in ('a', 'b')]
    ll = geo.inv(pts); d = pad_m / 90000.0
    feats, meta = E.query_index(fg.INDEX, (min(p[0] for p in ll) - d, min(p[1] for p in ll) - d, max(p[0] for p in ll) + d, max(p[1] for p in ll) + d), kinds=('building',))
    if feats is None: raise RuntimeError(f'no neighbour footprints: {meta}')     # unknown occluders: caller must not associate blind
    mem = set(geometry.get('members') or [geometry.get('bid')])
    return [dict(id=f['id'], poly=E.to_block(geo, f['geom'])) for f in sorted(feats, key=lambda f: f['id']) if f['id'] not in mem and f['geom'].is_valid]


# ---------------------------------------------------------------- camera model
def project(proj, x, y, z):
    """world point (MC x, world Y, MC z) -> normalised image (nx, ny), y down; None when behind a perspective camera.
    perspective (OpenSfM): px = w/2 + f*max(w,h)*X/Z; equirectangular: nx = 0.5 + yaw/360, ny = 0.5 - pitch/180 (yaw 0 = heading)."""
    c = proj['camera']; s = c['blocks_per_m']; hx, hz = c['heading_mc']
    dx, dz, dy = (x - c['mc'][0]) / s, (z - c['mc'][1]) / s, y - c['y']
    fwd = dx * hx + dz * hz; right = dx * -hz + dz * hx                  # right of heading (x east, z south): (-hz, hx)
    if proj['model'] == 'equirectangular':
        yaw = math.atan2(right, fwd); pitch = math.atan2(dy, math.hypot(fwd, right))
        return (0.5 + yaw / (2 * math.pi), 0.5 - pitch / math.pi)
    p, r = math.radians(c.get('pitch_deg') or 0.0), math.radians(c.get('roll_deg') or 0.0)   # camera tilt (SfM pose); level when unknown
    fwd, up = fwd * math.cos(p) + dy * math.sin(p), dy * math.cos(p) - fwd * math.sin(p)
    right, up = right * math.cos(r) + up * math.sin(r), up * math.cos(r) - right * math.sin(r)
    if fwd <= 1e-6: return None
    w, h, f = proj['image']['width'], proj['image']['height'], proj['focal']; F = f * max(w, h)
    return ((w / 2 + F * right / fwd) / w, (h / 2 - F * up / fwd) / h)


def _yaw(c, p):
    """yaw (deg, + = right of heading) and horizontal distance (m) of MC point p from camera candidate c"""
    s = c.get('blocks_per_m', 1.0); hx, hz = c['heading_mc']; dx, dz = (p[0] - c['mc'][0]) / s, (p[1] - c['mc'][1]) / s
    return math.degrees(math.atan2(dx * -hz + dz * hx, dx * hx + dz * hz)), math.hypot(dx, dz)


def _age_years(iso, now):
    try: d = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except (AttributeError, ValueError): return None
    if d.tzinfo is None: d = d.replace(tzinfo=datetime.timezone.utc)
    return (now - d).total_seconds() / (365.25 * 86400)


def _ray_hit(line, poly):
    """distance along line to its first contact with poly, or None"""
    from shapely.geometry import Point
    i = line.intersection(poly)
    if i.is_empty: return None
    p0 = Point(line.coords[0]); return min(p0.distance(Point(c)) for g in getattr(i, 'geoms', [i]) for c in g.coords)


# ---------------------------------------------------------------- association
def judge(face, c, obstacles, own, cfg, now):
    """(reasons[], metrics{}, projection|None) for one candidate against one face"""
    from shapely.geometry import LineString
    R, M = [], {}
    s = c.get('blocks_per_m', 1.0); cam = c.get('camera') or {}; L = face['length_u']
    model = cam.get('type')
    if model == 'perspective' and not (cam.get('focal') and cam.get('width') and cam.get('height')): R.append('perspective camera without intrinsics')
    elif model not in ('perspective', 'equirectangular'): R.append(f'unsupported camera type {model}')
    if 'mc' not in c or 'heading_mc' not in c: return R + ['no camera pose'], M, None
    C = c['mc']; n = mean_normal(face); P = point_at(face, L / 2)
    side = ((C[0] - P[0]) * n[0] + (C[1] - P[1]) * n[1]) / s
    if side <= 0: R.append('camera behind face (negative normal side)')
    # distance: nearest point of the face polyline (horizontal)
    from shapely.geometry import Point
    line = LineString([(x, z) for _, x, z in face_poly(face)])
    d = line.distance(Point(C)) / s; M['dist_m'] = round(d, 2)
    if d < cfg['min_dist_m']: R.append(f'too close ({d:.1f} m < {cfg["min_dist_m"]:g})')
    if d > cfg['max_dist_m']: R.append(f'too far ({d:.1f} m > {cfg["max_dist_m"]:g})')
    vc = math.hypot(C[0] - P[0], C[1] - P[1]) or 1.0
    view = math.degrees(math.acos(max(-1.0, min(1.0, ((C[0] - P[0]) * n[0] + (C[1] - P[1]) * n[1]) / vc)))); M['view_angle_deg'] = round(view, 1)
    if view > cfg['max_view_deg']: R.append(f'view angle {view:.0f} deg > {cfg["max_view_deg"]:g}')
    # FOV: share of the face's u-extent inside the horizontal field of view (panorama: inside a crop window centred on the face)
    k = max(4, int(math.ceil(L / cfg['sample_m']))); yaws = [_yaw(c, point_at(face, L * i / k))[0] for i in range(k + 1)]
    ycen = _yaw(c, P)[0]
    if model == 'equirectangular':
        half = cfg['pano_crop_deg'] / 2; rel = [((y - ycen + 180) % 360) - 180 for y in yaws]
        inside = [ycen + r for r in rel if abs(r) <= half]
    else:
        half = (cam.get('hfov_deg') or 0) / 2
        inside = [y for y in yaws if abs(y) < half and abs(y) < 89]            # |yaw| < 89: in front of the camera
    frac = len(inside) / len(yaws); M['fov_frac'] = round(frac, 3)
    if frac < cfg['min_fov_frac']: R.append(f'face u-extent {frac:.0%} inside FOV < {cfg["min_fov_frac"]:.0%}')
    # occlusion: rays camera -> centre and quarter points, trimmed short of the face so the target wall itself never counts
    for tag, u in (('centre', L / 2), ('q1', L / 4), ('q3', 3 * L / 4)):
        Q = point_at(face, u); dd = math.hypot(Q[0] - C[0], Q[1] - C[1])
        if dd <= cfg['ray_trim_m'] * s: continue
        t = 1 - cfg['ray_trim_m'] * s / dd; ray = LineString([tuple(C), (C[0] + t * (Q[0] - C[0]), C[1] + t * (Q[1] - C[1]))])
        hit = next((o['id'] for o in obstacles if ray.intersects(o['poly'])), None) or ('own building' if own is not None and ray.intersects(own) else None)
        if hit: R.append(f'occluded: ray to {tag} crosses {hit}'); break
    # wrong building: the principal ray of a perspective image must not meet another footprint before ours
    if model == 'perspective':
        hx, hz = c['heading_mc']; far = cfg['max_dist_m'] * s
        pr = LineString([tuple(C), (C[0] + hx * far, C[1] + hz * far)])
        dn = min(((_ray_hit(pr, o['poly']), o['id']) for o in obstacles if pr.intersects(o['poly'])), default=(None, None))
        mine = min([v for v in (_ray_hit(pr, line.buffer(0.5 * s)), _ray_hit(pr, own) if own is not None else None) if v is not None], default=None)
        if dn[0] is not None and (mine is None or dn[0] < mine): R.append(f'wrong building: image centre looks at {dn[1]}')
    # capture date
    age = _age_years(c.get('captured_at'), now); M['age_years'] = None if age is None else round(age, 2)
    if age is None: R.append('capture date unknown')
    elif age > cfg['max_age_years']: R.append(f'image {age:.1f} years old > {cfg["max_age_years"]:g}')
    if R: return R, M, None
    dist_f = 1.0 if 8 <= d <= 40 else max(0.5, 1 - (d - 40) / 80) if d > 40 else 0.75
    M['assoc_score'] = round(frac * math.cos(math.radians(view)) * dist_f * (1 - 0.5 * age / cfg['max_age_years']), 3)
    return R, M, projection(face, c, inside, cfg)


def projection(face, c, inside_yaws, cfg):
    """what analysis needs to rectify the face from this image (SOURCES.md, 'projection')"""
    cam = c['camera']; y_bot = face['base_y'] + 1; y_top = face['top_y'] + 1
    proj = dict(model=cam['type'], camera=dict(mc=c['mc'], y=round(y_bot + cfg['camera_height_m'], 2), heading_mc=c['heading_mc'],
                                               blocks_per_m=c.get('blocks_per_m', 1.0), pitch_deg=cam.get('pitch_deg') or 0.0, roll_deg=cam.get('roll_deg') or 0.0,
                                               pose=cam.get('pose', 'level (no tilt data)')),
                image=dict(width=cam.get('width'), height=cam.get('height')), focal=cam.get('focal'))
    if cam['type'] == 'equirectangular':
        a, b = min(inside_yaws) - cfg['yaw_margin_deg'], max(inside_yaws) + cfg['yaw_margin_deg']
        proj['yaw_window_deg'] = [round(a, 2), round(b, 2)]
        proj['crop_nx'] = [round(0.5 + a / 360, 5), round(0.5 + b / 360, 5)]          # may lie outside 0..1: wrap modulo 1
    quads = []
    for k, g in enumerate(face['segments']):
        cw = [(g['a'][0], y_bot, g['a'][1]), (g['b'][0], y_bot, g['b'][1]), (g['b'][0], y_top, g['b'][1]), (g['a'][0], y_top, g['a'][1])]
        nn = [project(proj, *p) for p in cw]
        quads.append(dict(seg=k, u0=g['u0'], u1=g['u1'], y0=y_bot, y1=y_top, corners_world=[[round(v, 3) for v in p] for p in cw],
                          corners_norm=[None if q is None else [round(q[0], 5), round(q[1], 5)] for q in nn]))
    proj['quads'] = quads
    return proj


def associate(geometry, candidates, obstacles, cfg=None, now=None):
    from shapely.ops import unary_union
    from shapely.geometry import Polygon
    cfg = dict(DEFAULTS, **(cfg or {})); now = now or datetime.datetime.now(datetime.timezone.utc)
    own = unary_union([Polygon(p['polygon_mc']).buffer(0) for p in geometry.get('parts', []) if len(p.get('polygon_mc', [])) >= 3]) or None
    if own is not None and own.is_empty: own = None
    out = dict(schema=SCHEMA, bid=geometry.get('bid'), geometry_sha=geometry.get('geometry_sha'), params=cfg, faces={})
    for face in geometry['faces']:
        if face.get('edge') != 'exterior':
            out['faces'][face['id']] = dict(status=f'skipped: {face.get("edge")} wall', accepted=[], rejected=[]); continue
        acc, rej = [], []
        for c in candidates:
            ref = dict(provider=c.get('provider'), image_id=c.get('image_id'), captured_at=c.get('captured_at'), attribution=c.get('attribution'))
            try: R, M, P = judge(face, c, obstacles, own, cfg, now)
            except Exception as e: R, M, P = [f'association error {type(e).__name__}'], {}, None   # odd data never accepted
            (rej if R else acc).append(dict(ref, **M, reasons=R) if R else dict(ref, **M, url=c.get('url'), path=c.get('path'), projection=P))
        acc.sort(key=lambda a: (-a['assoc_score'], a['provider'] or '', a['image_id'] or ''))
        for a in acc[cfg['max_images_per_face']:]:
            a.pop('projection'); a['reasons'] = [f'over per-face limit ({cfg["max_images_per_face"]})']; rej.append(a)
        acc = acc[:cfg['max_images_per_face']]
        out['faces'][face['id']] = dict(status='observed-candidates' if acc else 'no-evidence', accepted=acc, rejected=rej)
    return out


# ---------------------------------------------------------------- self-test
def _selftest():
    import tempfile
    from shapely.geometry import box
    from shapely import affinity
    import facade_sources as S
    now = datetime.datetime(2026, 10, 29, tzinfo=datetime.timezone.utc); ok = []

    def scene(theta, s=1.02):
        """20 m face along x at z=0 (normal +z), building behind it, rotated by theta about the origin and scaled by s"""
        ct, st = math.cos(math.radians(theta)), math.sin(math.radians(theta))
        T = lambda x, z: (s * (x * ct - z * st), s * (x * st + z * ct)); V = lambda x, z: (x * ct - z * st, x * st + z * ct)
        g = dict(bid='t', members=['t'], faces=[dict(id='f', edge='exterior', base_y=60, top_y=68, length_u=20.0,
                 segments=[dict(a=list(T(0, 0)), b=list(T(20, 0)), normal=list(V(0, 1)), u0=0.0, u1=20.0)])],
                 parts=[dict(id='p0', polygon_mc=[list(T(*p)) for p in ((0, 0), (20, 0), (20, -15), (0, -15))])])
        cam = lambda x, z, h, kind='perspective', when='2025-01-01T00:00:00Z': dict(provider='t', image_id=f'{x},{z}', captured_at=when, mc=list(T(x, z)),
              heading_mc=list(V(*h)), blocks_per_m=s, camera=S._camera(kind, focal=0.8, w=2048, h=1536) if kind == 'perspective' else S._camera(kind))
        ob = lambda *b: dict(id='nb', poly=affinity.scale(affinity.rotate(box(*b), theta, origin=(0, 0)), s, s, origin=(0, 0)))
        return g, cam, ob

    def one(g, c, obs=()):
        r = associate(g, [c], list(obs), now=now)['faces']['f']; return (r['accepted'] or r['rejected'])[0], bool(r['accepted'])
    base = None
    for th in (0, 37, 90, 211.4, -11.4):
        g, cam, ob = scene(th); a, acc = one(g, cam(10, 20, (0, -1)))
        assert acc and a['dist_m'] == 20.0 and a['view_angle_deg'] == 0 and a['fov_frac'] == 1.0, (th, a)
        q = a['projection']['quads'][0]['corners_norm']
        assert q[0][0] < 0.5 < q[1][0] and q[0][1] > 0.5 > q[3][1], q          # u0 left, u1 right, bottom below / top above the horizon
        if base is None: base = q
        assert all(abs(x - y) < 1e-4 for p, r in zip(q, base) for x, y in zip(p, r)), (th, q, base)
    ok.append('rotated faces (0/37/90/211/-11 deg, scale 1.02): accepted, identical image corners, u0 left of u1')
    g, cam, ob = scene(30)
    a, acc = one(g, cam(10, -40, (0, 1))); assert not acc and any('behind' in r for r in a['reasons']), a; ok.append('camera behind face rejected')
    a, acc = one(g, cam(10, 100, (0, -1))); assert not acc and any('too far' in r for r in a['reasons']), a
    a, acc = one(g, cam(10, 3, (0, -1))); assert not acc and any('too close' in r for r in a['reasons']), a; ok.append('too far / too close rejected')
    a, acc = one(g, cam(-50, 8, (1, -0.1))); assert not acc and any('view angle' in r for r in a['reasons']), a; ok.append('grazing view rejected')
    a, acc = one(g, cam(10, 20, (1, 0))); assert not acc and any('inside FOV' in r for r in a['reasons']), a; ok.append('face outside FOV rejected')
    a, acc = one(g, cam(10, 20, (0, -1)), [ob(7, 8, 13, 12)]); assert not acc and any('occluded' in r and 'nb' in r for r in a['reasons']), a
    a, acc = one(g, cam(10, 20, (0, -1)), [ob(30, -15, 50, 0)]); assert acc, a; ok.append('occlusion by neighbour footprint rejected; non-blocking neighbour ignored')
    a, acc = one(g, cam(40, 15, (0, -1)), [ob(30, -15, 50, 0)]); assert not acc and any('wrong building' in r for r in a['reasons']), a
    ok.append('wrong-building camera (in front of the neighbour, facing it) rejected')
    a, acc = one(g, cam(10, 20, (0, -1), when='2015-01-01T00:00:00Z')); assert not acc and any('years old' in r for r in a['reasons'])
    a, acc = one(g, cam(10, 20, (0, -1), when=None)); assert not acc and 'capture date unknown' in a['reasons']; ok.append('old / undated image rejected')
    a, acc = one(g, cam(10, 20, (0, -1), kind='fisheye')); assert not acc and any('unsupported' in r for r in a['reasons']); ok.append('fisheye rejected')
    # panorama heading east: the face (south of nothing, it is north of the camera) lies to the left, yaw -90 +- atan(10/20)
    a, acc = one(g, cam(10, 20, (1, 0), kind='equirectangular')); assert acc, a
    w = a['projection']['yaw_window_deg']; e = math.degrees(math.atan(10 / 20))
    assert abs(w[0] - (-90 - e - 5)) < 0.6 and abs(w[1] - (-90 + e + 5)) < 0.6, w
    q = a['projection']['quads'][0]['corners_norm']; assert abs(q[0][0] - (0.5 + (-90 - e) / 360)) < 1e-3 and q[0][1] > 0.5 > q[3][1], q
    ok.append(f'panorama yaw window {w} (expected -90 -+ {e:.1f} -+ 5 deg), corner columns match')
    g2 = dict(g, faces=g['faces'] + [dict(g['faces'][0], id='party', edge='party')])
    cs = [cam(10 + i, 20, (0, -1)) for i in range(6)]; r = associate(g2, cs, [], now=now)['faces']
    assert len(r['f']['accepted']) == 4 and len(r['f']['rejected']) == 2 and r['party']['status'] == 'skipped: party wall'
    ok.append('per-face limit 4 (rest rejected with reason); party wall skipped')
    # end to end: mock server (wrong-building mode) -> facade_sources.fetch -> associate: nothing accepted
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
    import mock_imagery_server as MS, io, contextlib
    geo = S.FakeGeo()
    with tempfile.TemporaryDirectory() as td:
        fx, geom = S.selftest_fixture(td, geo); srv = MS.start(fx, mode='wrong-building'); os.environ['MAPILLARY_ACCESS_TOKEN'] = 'x'
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                cf = dict(geo=geo, mapillary_base=srv.url, providers='mapillary', timeout_s=2)
                nb = [dict(id='w-neighbour', poly=box(30, -15, 50, 0))]
                ev = S.fetch('t', geom, cf); r = associate(geom, ev['candidates'], nb, now=now)['faces']['p0:f0']
                assert len(ev['candidates']) == 2 and not r['accepted'] and all(x['reasons'] for x in r['rejected']), r
                srv.set_mode('ok'); ev = S.fetch('t', geom, cf); r = associate(geom, ev['candidates'], nb, now=now)['faces']['p0:f0']
                assert {x['image_id'] for x in r['accepted']} == {'m1', 'm2'}, r
        finally: srv.shutdown(); os.environ.pop('MAPILLARY_ACCESS_TOKEN', None)
    ok.append('end to end via mock: wrong-building images all rejected, ok-mode images accepted')
    for o in ok: print('  ok -', o)
    print('facade_associate self-test ok (%d checks)' % len(ok))


if __name__ == '__main__':
    a = sys.argv
    if '--selftest' in a or len(a) < 3: _selftest(); sys.exit(0)
    cache, bid = a[1], a[2]; bd = os.path.join(cache, 'facade', 'buildings', bid)
    try:
        from geo import Geo
        import facade_common as FC
        g = json.load(open(os.path.join(bd, 'geometry.json'), encoding='utf-8')); ev = json.load(open(os.path.join(bd, 'evidence.json'), encoding='utf-8'))
        geo = Geo()
        try: obs = obstacles_from_index(g, geo)
        finally: geo.close()
        r = associate(g, ev['candidates'], obs); FC.write_json(os.path.join(bd, 'association.json'), r)
        print(json.dumps(dict(status='ok', bid=bid, faces={k: len(v['accepted']) for k, v in r['faces'].items() if v['accepted']})))
    except Exception as e:
        print(json.dumps(dict(status='error', bid=bid, reason=f'{type(e).__name__}: {e}'))); sys.exit(1)
