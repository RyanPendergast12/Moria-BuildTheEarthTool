"""facade_analyze.py - facade image analysis backends (v0.6 auto facades), owner: agent V. Contract: docs/facade/AUTO.md 3, 6;
method and limits: docs/facade/ANALYSIS.md.

  analyze(image_rgb, projection, face_meta, cfg=None) -> (face_section, cost)

image_rgb   HxWx3 uint8 array or PIL image, None (no image), or a LIST of them (several images of the same face; then
            `projection` is a list of the same length).
projection  {"corners_px": {"u0v0": [x, y], "u1v0": .., "u1v1": .., "u0v1": ..},   pixel positions (x right, y down) of the
                                                     face corners; v0 = ground, v1 = top
             "u_m": [ua, ub], "v_m": [va, vb],       optional: the metric face coords those corners stand for (default: the
                                                     face extents; use it when only part of the face is framed)
             "evidence": {"provider", "image_id", "captured_at", "dist_m", "view_angle_deg", "assoc_score", "attribution"}}
face_meta   {"face_id", "u_extent_m": [u0, u1] | width, "v_extent_m": [v0, v1] | height (measured), "parts": [{"u0_m",
             "u1_m", "v1_m"}...] optional stepped rooflines (cells above a part's top are not facade)}
cfg         see DEFAULTS. "backend": builtin-cv (default) | ollama | anthropic.
face_section is the autospec face object of AUTO.md 3; cost is {"usd", "requests", "by_provider"}.

The optional model backends never execute model output, never log secrets, and never fall back to another backend: a
failure gives an `unresolved` face whose `unresolved` list says why.
CLI: --selftest | --refs <cases.json> (runs builtin-cv on local reference photos; the images stay outside the repo).
"""
import base64, datetime, io, json, math, os, re, sys, time, warnings
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facade_schema as S
from facade_palette_tools import srgb_to_lab

ANALYZER_VERSION = VERSION = '0.6.8'   # 0.6.8: person-chosen block per material zone; 0.6.7: ASSISTED zone labels (person-drawn glazing/material/awning polygons); 0.6.5: opt-in warm-dark rule (provisional); 0.6.4: dark-rectangle glazing opt-in (provisional), camera colours kept; 0.6.3: white-balanced colour estimates under a cast, dark rectangular glazing, wall zone = seen extent; 0.6.2: alignment checks, glazing rectangles, pose gate, assisted corners, colour-cast confidence
DEFAULTS = {
    'backend': 'builtin-cv',
    'ppm': 4, 'ss': 3, 'border_px': 2,                 # rectified grid: 4 cells per metre, 3x3 supersampling, image-edge margin
    'min_coverage': 0.35, 'min_item_conf': 0.30, 'canopy_min_conf': 0.20,
    'veg_exg': 0.10, 'sky_blue': 0.06, 'sky_v': 0.45, 'spec_v': 0.985, 'dark_v': 0.08,
    'glass_l_max': 45.0, 'glass_dl': 18.0, 'shadow_chroma': 0.03, 'zone_de': 12.0,
    'floor_m': (3.0, 6.0), 'bay_m': (1.5, 12.0), 'period_min': 0.25, 'ground_min_m': 2.5,
    'entrance_min_m': 1.5, 'entrance_max_m': 8.0, 'max_images': 4,
    'dark_glazing': False, 'warm_ab': None,                 # PROVISIONAL opt-in (e.g. 8.0): dark warm cells = brick/wood, not glass; also drops bronze glass                             # PROVISIONAL (opt-in): large dark rectangles read as glazing; misreads wood/dark masonry
    'align_tol_m': 1.0, 'cast_max': 0.04,             # grey-world chroma deviation above which colours are not trusted as material colours                                # roofline tolerance (parapets, LiDAR returns) of the alignment check
    # model backends (off unless configured)
    'cache_dir': None, 'secrets_file': None, 'provider_timeout_s': 20, 'model_max_px': 1024, 'model_ppm': 16,
    'ollama_url': 'http://localhost:11434', 'ollama_model': None, 'ollama_keep_alive': '30s',   # unload soon: the 5 GB model evicts the game's VRAM
    'ollama_num_ctx': 4096, 'model_timeout_s': 90, 'model_max_calls_per_job': 12,   # measured in-game: 16-37 s per call (cold load ~70 s)
    'ollama_num_gpu': None,                            # 0 = CPU only (no VRAM); None = Ollama decides
    'anthropic_url': 'https://api.anthropic.com/v1/messages', 'anthropic_model': 'claude-opus-5-5',
    'anthropic_max_tokens': 4096, 'anthropic_usd_per_mtok_in': 4.0, 'anthropic_usd_per_mtok_out': 20.0,
    'ai_max_usd_per_job': 0.0, 'ai_max_usd_per_day': 0.0, 'job_id': 'job',
}
ROLES = ('wall', 'base', 'band', 'parapet', 'trim')
VERIFIED_POSE_PROVIDERS = ('local',)          # refs.json: pose stated by whoever supplied the image (fixtures, own photos)


# ---------------------------------------------------------------- schema of the face section (AUTO.md 3)
CONF = ('num', 0, 1)
def _rgb(v, p, e):
    if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255 for x in v)):
        e.append(f'{p}: expected [r,g,b] with ints 0..255')
NUM = ('num', -1e5, 1e5)
OPT = lambda s: ('or', ('const', None), s)
EVIDENCE = {'provider': 'str', 'image_id': 'str', 'captured_at': OPT('str'), 'dist_m': OPT(('num', 0, 1e4)),
            'view_angle_deg': OPT(('num', 0, 90)), 'assoc_score': OPT(CONF), 'attribution': OPT('str')}
ZONE = {'v0_m': NUM, 'v1_m': NUM, 'u0_m': NUM, 'u1_m': NUM, 'role': ('enum',) + ROLES, 'rgb': _rgb, 'conf': CONF, 'rgb_camera?': _rgb}
FEATURES = ('glazing', 'floors', 'bays', 'entrances', 'canopies')
MODEL_FACE = {
    'confidence': CONF,
    'zones': ('list', ZONE, 32),
    'glazing': OPT({'fraction': CONF, 'rgb': _rgb, 'conf': CONF, 'rgb_camera?': _rgb}),
    'floors': OPT({'first_m': ('num', 0, 1e3), 'height_m': ('num', 2.5, 8), 'count': ('int', 1, 200), 'conf': CONF}),
    'bays': OPT({'pitch_m': ('num', 0.5, 50), 'width_m': ('num', 0.3, 50), 'offset_m': NUM, 'conf': CONF}),
    'entrances': ('list', {'u0_m': NUM, 'u1_m': NUM, 'conf': CONF}, 16),
    'canopies': ('list', {'u0_m': NUM, 'u1_m': NUM, 'v_m': NUM, 'depth': ('int', 1, 6), 'conf': CONF}, 16),
    'unresolved': ('list', 'str', 32),
    # features the image shows do NOT exist (a blank wall: no glazing). A feature that is null/[] and not listed here is UNKNOWN.
    'absent': ('list', ('enum',) + FEATURES, len(FEATURES)),
}
FACE = dict({k: v for k, v in MODEL_FACE.items() if k != 'absent'}, status=('enum', 'observed', 'procedural', 'unresolved'),
            evidence=('list', EVIDENCE, 16), **{'absent?': MODEL_FACE['absent'], 'assisted?': 'bool',
            'openings?': ('list', {'u0_m': NUM, 'u1_m': NUM, 'v0_m': NUM, 'v1_m': NUM, 'kind': ('enum', 'glazing'), 'conf': CONF}, 32),
            # ASSISTED material patches: person-drawn opaque zones (any position/size), colour sampled from the photo inside the polygon
            'patches?': ('list', {'u0_m': NUM, 'u1_m': NUM, 'v0_m': NUM, 'v1_m': NUM, 'role': ('enum',) + ROLES, 'rgb': _rgb, 'conf': CONF,
                                  'block?': 'block'}, 64)})   # block: chosen by the person (else matched to rgb)
LABEL_KINDS = ('glazing', 'material', 'awning')


def _poly_rects(poly_uv, U, V):
    """face-metre polygon -> [(u0, u1, v0, v1)] 1 m cells whose centre is inside (even-odd), row runs merged vertically"""
    P = np.asarray(poly_uv, float); cu = np.arange(int(np.ceil(U))) + 0.5; cv = np.arange(int(np.ceil(V))) + 0.5
    uu, vv = np.meshgrid(cu, cv); inside = np.zeros(uu.shape, bool)
    for (x0, y0), (x1, y1) in zip(P, np.roll(P, -1, 0)):
        if y0 == y1: continue
        inside ^= ((y0 > vv) != (y1 > vv)) & (uu < x0 + (vv - y0) * (x1 - x0) / (y1 - y0))
    out, open_ = [], {}
    for j in range(inside.shape[0]):
        runs = {(int(a), int(b)) for a, b in _runs(inside[j])}
        for k in [k for k in open_ if k not in runs]: out.append(open_.pop(k))
        for a, b in runs:
            if (a, b) in open_: open_[(a, b)][3] = float(j + 1)
            else: open_[(a, b)] = [float(a), float(min(b, U)), float(j), float(min(j + 1, V))]
    return sorted(tuple(r) for r in out + list(open_.values()))


def apply_labels(face, labels, img, proj, face_meta, occ=None):
    """ASSISTED zones (assist.json "labels": [{kind: glazing|material|awning, poly_px: [[x, y], ...], role?, depth?}]) drawn by a person on the
    aligned photo -> face openings / patches / canopies in face metres, replacing what automatic detection found for each labelled kind.
    Colours are sampled from the photo inside each polygon (occluders excluded). -> notes"""
    from PIL import Image, ImageDraw
    if face.get('status') != 'observed': return ['ASSISTED zones not applied: the face itself was not accepted (see the reasons above)']
    u_lo, U = _extent(face_meta, 'u_extent_m'); _, V = _extent(face_meta, 'v_extent_m')
    pu = proj.get('u_m') or [u_lo, U]; pv = proj.get('v_m') or [0, V]; c = proj['corners_px']
    Hinv = homography([c['u0v0'], c['u1v0'], c['u1v1'], c['u0v1']], [(pu[0], pv[0]), (pu[1], pv[0]), (pu[1], pv[1]), (pu[0], pv[1])])
    def uv(p): x = Hinv @ [p[0], p[1], 1.0]; return [min(max(x[0] / x[2], 0), U), min(max(x[1] / x[2], 0), V)]
    gain = None                                            # the white balance the analyzer applied to the wall (colour cast), reused for labels
    wz = next((z for z in face.get('zones') or [] if z['role'] == 'wall' and z.get('rgb_camera')), None)
    if wz: gain = _lin(np.array(wz['rgb']) / 255) / np.maximum(_lin(np.array(wz['rgb_camera']) / 255), 1e-4)
    def colour(poly_px):
        m = Image.new('L', (img.shape[1], img.shape[0]), 0); ImageDraw.Draw(m).polygon([tuple(p) for p in poly_px], fill=1)
        m = np.asarray(m, bool) & ((occ[..., 0] < 0.5) if occ is not None else True)
        if m.sum() < 9: return None
        cam = np.median(img[m].reshape(-1, 3), axis=0) / 255
        return _rgb255(_srgb(_lin(cam) * gain) if gain is not None else cam)
    by = {k: [] for k in LABEL_KINDS}; notes = []
    for i, lb in enumerate(labels[:64]):
        k, poly = lb.get('kind'), lb.get('poly_px') or []
        if k not in LABEL_KINDS or len(poly) < 3: notes.append(f'label {i}: unknown kind or fewer than 3 points, ignored'); continue
        P = [uv(p) for p in poly]
        # an awning is a thin edge-on line in the photo: its extent along u and its underside height, not its filled cells
        rects = [(min(p[0] for p in P), max(p[0] for p in P), min(p[1] for p in P), max(p[1] for p in P))] if k == 'awning' else _poly_rects(P, U, V)
        if not rects or rects[0][1] - rects[0][0] < 1: notes.append(f'label {i} ({k}): under one block, ignored'); continue
        by[k].append((lb, poly, rects))
    if by['glazing']:
        face['openings'] = [dict(u0_m=r[0], u1_m=r[1], v0_m=r[2], v1_m=r[3], kind='glazing', conf=0.95) for _, _, rs in by['glazing'] for r in rs][:32]
        g = colour(by['glazing'][0][1]); area = sum((r[1] - r[0]) * (r[3] - r[2]) for o in by['glazing'] for r in o[2])
        if g: face['glazing'] = dict(fraction=round(min(1.0, area / max(1.0, U * V)), 3), rgb=g, conf=0.95)
    if by['material']:
        face['patches'] = []
        for lb, poly, rs in by['material']:
            rgb = colour(poly); role = lb.get('role') if lb.get('role') in ROLES else 'wall'
            blk = lb.get('block') or None
            if blk and S.check_block(blk): notes.append(f'material label: block {blk!r} rejected ({S.check_block(blk)}), colour match used'); blk = None
            if rgb is None and not blk: notes.append('material label: polygon fully occluded, no colour, ignored'); continue
            face['patches'] += [dict(u0_m=r[0], u1_m=r[1], v0_m=r[2], v1_m=r[3], role=role, rgb=rgb or [128, 128, 128], conf=0.95, **({'block': blk} if blk else {}))
                                for r in rs]
        face['patches'] = face['patches'][:64]
    if by['awning']:
        face['canopies'] = [dict(u0_m=round(rs[0][0], 2), u1_m=round(rs[0][1], 2), v_m=round(rs[0][2], 2),
                                 depth=int(min(6, max(1, lb.get('depth') or 2))), conf=0.95) for lb, _, rs in by['awning']][:16]
    face['absent'] = [a for a in face.get('absent') or [] if not ((a == 'glazing' and by['glazing']) or (a == 'canopies' and by['awning']))]
    notes.append('ASSISTED zones (person-drawn, not automatic): ' + ', '.join(f'{len(by[k])} {k}' for k in LABEL_KINDS if by[k]))
    return notes


def feature_states(face):
    """{feature: 'present' | 'absent' | 'unknown'}"""
    ab = set(face.get('absent') or [])
    return {k: 'present' if face.get(k) else 'absent' if k in ab else 'unknown' for k in FEATURES}


def _extent(meta, key):
    e = meta.get(key)
    if isinstance(e, (int, float)) and not isinstance(e, bool): return 0.0, float(e)
    return float(e[0]), float(e[1])


def validate_face(face, face_meta, spec=FACE):
    """list of errors ('' none): schema, data-only, and every coordinate inside the measured face extent"""
    errs = []
    S._data_only(face, '$', errs)
    S.chk(face, spec, '$', errs)
    if errs: return errs
    ab = face.get('absent') or []
    if len(set(ab)) != len(ab): errs.append('$.absent: duplicate feature')
    errs += [f'$.absent: {k} is listed absent but also described' for k in ab if face.get(k)]
    u0, u1 = _extent(face_meta, 'u_extent_m'); v0, v1 = _extent(face_meta, 'v_extent_m'); eps = 0.01
    def inu(x): return u0 - eps <= x <= u1 + eps
    def inv(x): return v0 - eps <= x <= v1 + eps
    for i, z in enumerate(face['zones']):
        if not (inu(z['u0_m']) and inu(z['u1_m']) and inv(z['v0_m']) and inv(z['v1_m']) and z['u0_m'] < z['u1_m'] and z['v0_m'] < z['v1_m']):
            errs.append(f'$.zones[{i}]: outside the measured face extent or empty')
    for i, z in enumerate(face.get('openings') or []):
        if not (inu(z['u0_m']) and inu(z['u1_m']) and inv(z['v0_m']) and inv(z['v1_m']) and z['u0_m'] < z['u1_m'] and z['v0_m'] < z['v1_m']):
            errs.append(f'$.openings[{i}]: outside the measured face extent or empty')
    for key in ('entrances', 'canopies'):
        for i, z in enumerate(face[key]):
            if not (inu(z['u0_m']) and inu(z['u1_m']) and z['u0_m'] < z['u1_m'] and inv(z.get('v_m', v0))):
                errs.append(f'$.{key}[{i}]: outside the measured face extent or empty')
    f, b = face['floors'], face['bays']
    if f and not (inv(f['first_m']) and f['first_m'] + (f['count'] - 2) * f['height_m'] <= v1 + eps):
        errs.append('$.floors: floor lines outside the measured height')
    if b and not (inu(b['offset_m']) and b['width_m'] <= b['pitch_m'] and b['pitch_m'] <= (u1 - u0) + eps):
        errs.append('$.bays: outside the measured width or width > pitch')
    return errs


def _empty_face(evidence, reasons):
    return {'status': 'unresolved', 'evidence': evidence, 'confidence': 0.0, 'zones': [], 'glazing': None, 'floors': None,
            'bays': None, 'entrances': [], 'canopies': [], 'unresolved': list(reasons)[:32]}


def _cost(backend=None, usd=0.0, requests=0):
    return {'usd': round(usd, 6), 'requests': requests,
            'by_provider': {backend: {'usd': round(usd, 6), 'requests': requests}} if backend and requests else {}}


def _evidence(proj):
    ev = (proj or {}).get('evidence') or {}
    def num(x, lo, hi):
        try: return min(hi, max(lo, abs(float(x)) if lo == 0 else float(x))) if x is not None and math.isfinite(float(x)) else None
        except (TypeError, ValueError): return None
    return {'provider': str(ev.get('provider') or 'unknown')[:200], 'image_id': str(ev.get('image_id') or 'unknown')[:200],
            'captured_at': None if ev.get('captured_at') is None else str(ev['captured_at'])[:64],
            'dist_m': num(ev.get('dist_m'), 0, 1e4), 'view_angle_deg': num(ev.get('view_angle_deg'), 0, 90),
            'assoc_score': num(ev.get('assoc_score'), 0, 1), 'attribution': None if ev.get('attribution') is None else str(ev['attribution'])[:1000]}


# ---------------------------------------------------------------- geometry: homography and rectification (numpy only)
def homography(src, dst):
    """3x3 H with dst ~ H @ [src, 1] from 4 point pairs (DLT, h33 = 1)"""
    A, b = [], []
    for (u, v), (x, y) in zip(src, dst):
        A.append([u, v, 1, 0, 0, 0, -x * u, -x * v]); b.append(x)
        A.append([0, 0, 0, u, v, 1, -y * u, -y * v]); b.append(y)
    return np.append(np.linalg.solve(np.array(A, float), np.array(b, float)), 1.0).reshape(3, 3)


def _sample(img, x, y):
    h, w = img.shape[:2]
    x0 = np.clip(np.floor(x).astype(int), 0, w - 2); y0 = np.clip(np.floor(y).astype(int), 0, h - 2)
    fx = np.clip(x - x0, 0, 1)[..., None]; fy = np.clip(y - y0, 0, 1)[..., None]
    top = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    bot = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bot * fy


def _block(a, ss):
    R, W = a.shape[0] // ss, a.shape[1] // ss
    return a.reshape(R, ss, W, ss, *a.shape[2:]).mean(axis=(1, 3))


def rectify(img, projection, face_meta, ppm, ss=1, border_px=2):
    """-> (rgb float 0..1 of shape (R*ss, W*ss, 3), inside mask). Row 0 = v_lo (ground), rows grow upward; col 0 = u_lo."""
    u_lo, u_hi = _extent(face_meta, 'u_extent_m'); v_lo, v_hi = _extent(face_meta, 'v_extent_m')
    pu = projection.get('u_m') or [u_lo, u_hi]; pv = projection.get('v_m') or [v_lo, v_hi]
    c = projection['corners_px']
    src = [(pu[0], pv[0]), (pu[1], pv[0]), (pu[1], pv[1]), (pu[0], pv[1])]
    dst = [c['u0v0'], c['u1v0'], c['u1v1'], c['u0v1']]
    Hm = homography(src, dst)
    if min((Hm @ [u, v, 1])[2] for u, v in src) <= 0: raise ValueError('degenerate projection (corner behind the camera)')
    W = max(4, int(round((u_hi - u_lo) * ppm))); R = max(4, int(round((v_hi - v_lo) * ppm)))
    jj, ii = np.mgrid[0:R * ss, 0:W * ss].astype(float)
    u = u_lo + (ii + 0.5) / (ppm * ss); v = v_lo + (jj + 0.5) / (ppm * ss)
    X = Hm[0, 0] * u + Hm[0, 1] * v + Hm[0, 2]; Y = Hm[1, 0] * u + Hm[1, 1] * v + Hm[1, 2]; Z = Hm[2, 0] * u + Hm[2, 1] * v + Hm[2, 2]
    ok = Z > 1e-9
    x = np.where(ok, X / np.where(ok, Z, 1), -1e9) - 0.5; y = np.where(ok, Y / np.where(ok, Z, 1), -1e9) - 0.5
    h, w = img.shape[:2]; m = border_px
    inside = ok & (x >= m) & (x <= w - 1 - m) & (y >= m) & (y <= h - 1 - m)
    return _sample(img, np.where(inside, x, 0), np.where(inside, y, 0)), inside


def _as_array(img):
    if img is None: return None
    if not isinstance(img, np.ndarray):
        img = np.asarray(img.convert('RGB'))
    img = np.asarray(img)
    if img.ndim != 3 or img.shape[2] < 3 or img.shape[0] < 8 or img.shape[1] < 8: return None
    return img[..., :3].astype(np.float32) / 255.0


def _outside_parts(face_meta, R, W, ppm):
    """True where a cell lies above every part's roofline (stepped faces); all False without parts"""
    parts = face_meta.get('parts') or []
    if not parts or not all(isinstance(p, dict) and {'u0_m', 'u1_m', 'v1_m'} <= set(p) for p in parts):
        return np.zeros((R, W), bool)
    u_lo, _ = _extent(face_meta, 'u_extent_m'); v_lo, _ = _extent(face_meta, 'v_extent_m')
    u = u_lo + (np.arange(W) + 0.5) / ppm; v = v_lo + (np.arange(R) + 0.5) / ppm
    inside = np.zeros((R, W), bool)
    for p in parts:
        cols = (u >= p['u0_m']) & (u <= p['u1_m'])
        inside |= (v[:, None] <= p['v1_m']) & cols[None, :]
    return ~inside


def _in_quad(projection, face_meta, R, W, ppm):
    """cells inside the framed u_m x v_m rectangle (no extrapolation of the homography beyond the quad)"""
    u_lo, _ = _extent(face_meta, 'u_extent_m'); v_lo, _ = _extent(face_meta, 'v_extent_m')
    u = u_lo + (np.arange(W) + 0.5) / ppm; v = v_lo + (np.arange(R) + 0.5) / ppm
    pu = projection.get('u_m') or [-1e9, 1e9]; pv = projection.get('v_m') or [-1e9, 1e9]
    return ((v >= pv[0]) & (v <= pv[1]))[:, None] & ((u >= pu[0]) & (u <= pu[1]))[None, :]


# ---------------------------------------------------------------- masks (one image)
def _masks(img, projection, face_meta, c):
    ppm, ss = c['ppm'], c['ss']
    rgbs, inside = rectify(img, projection, face_meta, ppm, ss, c['border_px'])
    r, g, b = rgbs[..., 0], rgbs[..., 1], rgbs[..., 2]
    mx, mn = rgbs.max(-1), rgbs.min(-1); s = rgbs.sum(-1) + 1e-6
    veg = ((2 * g - r - b) / s > c['veg_exg']) & (mx > 0.08)                    # excess-green index
    skyc = ((b > r + c['sky_blue']) & (b >= g - 0.02) & (mx > c['sky_v'])) | ((mx > 0.8) & (mx - mn < 0.1) & (b >= r + 0.015))
    spec = mx >= c['spec_v']; dark = mx < c['dark_v']
    fin = _block(inside.astype(float), ss)
    good = inside & ~veg & ~spec
    cnt = _block(good.astype(float), ss)
    rgb = _block(rgbs * good[..., None], ss) / np.maximum(cnt, 1e-9)[..., None]
    rgb_all = _block(rgbs * inside[..., None], ss) / np.maximum(fin, 1e-9)[..., None]
    rgb = np.where((cnt > 0)[..., None], rgb, rgb_all)
    frac = lambda m: _block((m & inside).astype(float), ss) / np.maximum(fin, 1e-9)
    border = fin < 0.999
    R, W = border.shape
    border |= ~_in_quad(projection, face_meta, R, W, ppm)
    if projection.get('occluder') is not None:   # marked occluders count like the frame edge: not seen
        oc, _ = rectify(projection['occluder'], projection, face_meta, ppm, ss, c['border_px']); border |= _block(oc[..., 0], ss) > 0.01
    outside = _outside_parts(face_meta, R, W, ppm)
    vegc, skyc_c, specc, darkc = frac(veg) > 0.34, frac(skyc) > 0.5, frac(spec) > 0.5, frac(dark) > 0.5
    notes_dark = []
    if darkc.any() and c.get('dark_glazing'):   # PROVISIONAL, opt-in: underexposed glazing: a large, rectangular too-dark region is a glass candidate, not "unseen"
        from scipy import ndimage
        lb_, _ = ndimage.label(darkc)
        for k_, sl_ in enumerate(ndimage.find_objects(lb_), 1):
            hh_, ww_ = (sl_[0].stop - sl_[0].start) / ppm, (sl_[1].stop - sl_[1].start) / ppm
            fill_ = float((lb_[sl_] == k_).sum()) / ((sl_[0].stop - sl_[0].start) * (sl_[1].stop - sl_[1].start))
            if hh_ >= 1.0 and ww_ >= 1.0 and fill_ >= 0.7:
                darkc[lb_ == k_] = False; notes_dark.append(f'very dark rectangular region {ww_:.1f} x {hh_:.1f} m read as glazing (underexposed photo)')
    cand = skyc_c | vegc | outside | border                                       # sky floods down from the top row (v_hi)
    flood = np.flip(np.cumprod(np.flip(cand, 0), axis=0), 0).astype(bool)
    sky = flood & skyc_c
    notes = list(notes_dark)
    face_cells = max(1, int((~outside & ~border).sum()))
    if (specc & ~outside & ~border).sum() > 0.25 * face_cells:
        specc = np.zeros_like(specc); notes.append('overexposed image: clipped pixels kept as wall')
    refl = skyc_c & ~sky                                                          # sky reflected in the facade: glass
    valid = ~border & ~outside & ~sky & ~vegc & ~specc & ~darkc
    stats = {k: round(float((m & ~outside).sum()) / max(1, (~outside).sum()), 3) for k, m in
             (('sky', sky), ('vegetation', vegc), ('shadow_deep', darkc), ('specular', specc), ('border', border))}
    return {'rgb': rgb, 'valid': valid, 'refl': refl & valid, 'outside': outside, 'stats': stats, 'notes': notes}


# ---------------------------------------------------------------- small numeric helpers
def kmeans(X, k, iters=30, seed=0):
    """deterministic k-means++ / Lloyd on rows of X -> centres (k' <= k)"""
    rng = np.random.default_rng(seed)
    if len(X) > 20000: X = X[rng.choice(len(X), 20000, replace=False)]
    C = [X[rng.integers(len(X))]]
    for _ in range(1, k):
        d = ((X[:, None, :] - np.array(C)[None]) ** 2).sum(-1).min(1)
        if d.sum() <= 1e-9: break
        C.append(X[rng.choice(len(X), p=d / d.sum())])
    C = np.array(C, float)
    for _ in range(iters):
        lbl = assign(X, C)
        N = np.array([X[lbl == j].mean(0) if (lbl == j).any() else C[j] for j in range(len(C))])
        if np.allclose(N, C): break
        C = N
    return C


def assign(X, C):
    return ((X[:, None, :] - C[None]) ** 2).sum(-1).argmin(1)


def _runs(mask):
    """[(start, end_exclusive)] of True runs in a 1D bool array"""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.flatnonzero(m[1:] != m[:-1])
    return list(zip(d[::2], d[1::2]))


def period(profile, lo, hi):
    """(lag_px float, strength 0..1) of the strongest periodicity with lag in [lo, hi] (unbiased autocorrelation, first
    local peak >= 0.85 of the best; parabolic refinement), or None. Needs >= 2.5 periods of signal."""
    x = np.asarray(profile, float); n = len(x)
    hi = min(int(hi), int(n / 2.5)); lo = max(2, int(lo))
    if hi <= lo + 1: return None
    x = x - x.mean(); var = (x * x).mean()
    if var < 1e-6: return None
    r = np.array([(x[:n - l] * x[l:]).mean() / var if l else 1.0 for l in range(hi + 2)])
    peaks = [l for l in range(lo, hi + 1) if r[l] >= r[l - 1] and r[l] >= r[l + 1] and r[l] > 0]
    if not peaks: return None
    best = max(r[l] for l in peaks)
    l = next(l for l in peaks if r[l] >= 0.85 * best)
    a, b, cc = r[l - 1], r[l], r[l + 1]; den = a - 2 * b + cc
    off = 0.5 * (a - cc) / den if abs(den) > 1e-9 else 0.0
    return l + float(np.clip(off, -0.5, 0.5)), float(np.clip(r[l], 0, 1))


def _highpass(p, win):
    """profile minus its moving average (window `win`), so slow lighting gradients do not dominate the autocorrelation"""
    win = max(3, win | 1); k = np.ones(win) / win
    return p - np.convolve(np.pad(p, win // 2, mode='edge'), k, mode='valid')[:len(p)]


def _lin(x): x = np.asarray(x, dtype=np.float64); return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
def _srgb(x): x = np.clip(np.asarray(x, dtype=np.float64), 0, 1); return np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


def _rgb255(x):
    return [int(round(float(t))) for t in np.clip(np.asarray(x) * 255, 0, 255)]


# ---------------------------------------------------------------- builtin-cv
def _alignment(img, proj, face_meta, c):
    """None, or why this image's facade quad does not match the measured face (the image is then not used). Rectifies a margin above
    the measured top: sky inside the top band = the photographed roofline is lower than the measured shell (a taller face than the
    building in view, or a mis-registered pose); the wall continuing above the top with the same colour = the photographed frontage is
    taller than the face (e.g. a 16 m frontage registered onto a 6 m face). Stepped faces (parts) skip the roofline test."""
    if face_meta.get('parts'): return None
    u_lo, u_hi = _extent(face_meta, 'u_extent_m'); v_lo, v_hi = _extent(face_meta, 'v_extent_m'); H = v_hi - v_lo
    ext = max(1.5, 0.25 * H); top = min(1.5, 0.3 * H)
    pj = dict(proj, u_m=proj.get('u_m') or [u_lo, u_hi], v_m=proj.get('v_m') or [v_lo, v_hi])
    if pj['v_m'][1] < v_hi - 0.25: return None                                   # the framed part ends below the roof: no roofline in view
    rgbs, inside = rectify(img, pj, dict(face_meta, v_extent_m=[v_lo, v_hi + ext]), c['ppm'], 1, c['border_px'])
    r, g, b = rgbs[..., 0], rgbs[..., 1], rgbs[..., 2]; mx = rgbs.max(-1); mn = rgbs.min(-1)
    sky = ((b > r + c['sky_blue']) & (b >= g - 0.02) & (mx > c['sky_v'])) | ((mx > 0.8) & (mx - mn < 0.1) & (b >= r + 0.015))
    veg = ((2 * g - r - b) / (rgbs.sum(-1) + 1e-6) > c['veg_exg']) & (mx > 0.08)
    v = v_lo + (np.arange(rgbs.shape[0]) + 0.5) / c['ppm']
    t = c['align_tol_m']; ext += t
    below = inside & ((v >= v_hi - t - top) & (v < v_hi - t))[:, None]; above = inside & ((v >= v_hi + t) & (v < v_hi + ext))[:, None]
    if below.sum() < 8 or above.sum() < 8: return None                           # roof band out of frame: nothing to compare
    if (sky & below).sum() / below.sum() > 0.35:
        return f'alignment: sky {t:.0f}-{t + top:.1f} m below the measured top (photographed roofline lower than the shell)'
    wa, wb = above & ~sky & ~veg, below & ~sky & ~veg
    if (sky & above).sum() / above.sum() < 0.15 and wa.sum() >= 8 and wb.sum() >= 8:
        de = float(np.linalg.norm(np.median(srgb_to_lab(rgbs[wa] * 255), 0) - np.median(srgb_to_lab(rgbs[wb] * 255), 0)))
        if de < 6: return f'alignment: the wall continues above the measured top (dE {de:.1f}): photographed frontage taller than the face'
    return None


def _openings(glass, known, cov, ppm, u_of, v_of, data_bot, c):
    """measured glazing geometry: connected glass regions (gaps < 0.5 m bridged: storefront mullions) -> rectangles, kept as seen.
    -> (openings, notes). A ground-touching opening shorter than a door is a scale mismatch (alignment), reported by the caller."""
    from scipy import ndimage
    gm = ndimage.binary_closing(ndimage.binary_opening(glass, iterations=1), iterations=1) & known
    lb, _ = ndimage.label(gm); out, notes = [], []
    for k, sl in enumerate(ndimage.find_objects(lb), 1):
        rs, cs = sl; h, w = (rs.stop - rs.start) / ppm, (cs.stop - cs.start) / ppm
        if h < 0.5 or w < 0.5 or h * w < 0.75: continue
        fill = float((lb[sl] == k).sum()) / ((rs.stop - rs.start) * (cs.stop - cs.start))
        if fill < 0.6: notes.append(f'irregular glazing region u={u_of(cs.start):.1f}-{u_of(cs.stop):.1f} m (fill {fill:.2f}) not used as an opening'); continue
        conf = round(cov * fill, 3)
        if conf < c['min_item_conf'] or len(out) >= 32: continue
        out.append({'u0_m': round(float(u_of(cs.start)), 2), 'u1_m': round(float(u_of(cs.stop)), 2), 'v0_m': round(float(v_of(rs.start)), 2),
                    'v1_m': round(float(v_of(rs.stop)), 2), 'kind': 'glazing', 'conf': conf})
    small_door = [o for o in out if o['v0_m'] <= data_bot + 0.3 and o['v1_m'] - o['v0_m'] < 1.8 and o['u1_m'] - o['u0_m'] >= 0.8]
    return out, notes, small_door


def _fuse(obs):
    """per-cell weighted mean over images (weight = assoc_score * cos(view angle)) of valid cells"""
    num = 0; den = 0; refl = 0
    for o, w in obs:
        vw = o['valid'] * w
        num = num + o['rgb'] * vw[..., None]; den = den + vw; refl = refl + o['refl'] * w
    valid = den > 0
    rgb = num / np.maximum(den, 1e-9)[..., None]
    return rgb, valid, valid & (refl / np.maximum(den, 1e-9) > 0.5)


def _weight(proj):
    ev = (proj or {}).get('evidence') or {}
    a = ev.get('assoc_score'); va = ev.get('view_angle_deg')
    a = 1.0 if a is None else float(a); va = 0.0 if va is None else float(va)
    return max(0.0, a) * max(0.1, math.cos(math.radians(min(89.0, abs(va)))))


def _builtin(images, projections, face_meta, c):
    ppm = c['ppm']
    u_lo, u_hi = _extent(face_meta, 'u_extent_m'); v_lo, v_hi = _extent(face_meta, 'v_extent_m')
    evidence, obs, reasons, notes = [], [], [], []
    for img, proj in list(zip(images, projections))[:c['max_images']]:
        a = _as_array(img)
        if a is None:
            reasons.append(f'image {_evidence(proj)["image_id"]}: missing or unreadable'); continue
        try:
            bad = _alignment(a, proj, face_meta, c)
            if bad: reasons.append(f'image {_evidence(proj)["image_id"]}: {bad}'); continue
            o = _masks(a, proj, face_meta, c)
            m_ = a.reshape(-1, 3).mean(0); o['cast'] = float(np.abs(m_ / max(m_.sum(), 1e-6) - 1 / 3).max())   # grey-world colour cast
            lo_ = _lin(a[a.shape[0] // 2:].reshape(-1, 3)).mean(0)                     # grey-world (lower half: ground + buildings, not sky)
            o['wb'] = lo_.mean() / np.maximum(lo_, 1e-6) * float(np.clip(0.18 / max(lo_.mean(), 1e-6), 0.5, 4.0))
        except (KeyError, TypeError, ValueError, np.linalg.LinAlgError) as e:
            reasons.append(f'image {_evidence(proj)["image_id"]}: bad projection ({type(e).__name__})'); continue
        w = _weight(proj)
        if w <= 0 or not o['valid'].any():
            reasons.append(f'image {_evidence(proj)["image_id"]}: no usable facade pixels'); continue
        obs.append((o, w)); notes += o['notes']
        e = _evidence(proj)
        if all(x['image_id'] != e['image_id'] or x['provider'] != e['provider'] for x in evidence): evidence.append(e)
    if not obs:
        return _empty_face(evidence, reasons or ['no usable image']), {}
    rgb, valid, refl = _fuse(obs)
    outside = obs[0][0]['outside']
    R, W = valid.shape
    coverage = float(valid.sum()) / max(1, int((~outside).sum()))
    stats = {'coverage': round(coverage, 3), 'masks': [o['stats'] for o, _ in obs], 'images': len(obs)}
    if coverage < c['min_coverage']:
        f = _empty_face(evidence, reasons + [f'facade coverage {coverage:.2f} < {c["min_coverage"]} (occluded or out of frame)'])
        return f, stats
    cov = min(1.0, coverage / 0.6)                                                # coverage factor for every confidence
    cast = max(o.get('cast', 0.0) for o, _ in obs)                                  # strong cast: camera colour is not material colour
    ccol = 0.6 if cast > c['cast_max'] else 1.0
    wb = obs[0][0].get('wb') if ccol < 1 else None
    rep = (lambda x: _rgb255(_srgb(_lin(x) * wb))) if wb is not None else _rgb255   # reported colours: white-balanced estimate under a cast

    lab = srgb_to_lab(rgb * 255.0)
    L = lab[..., 0]
    # wall reference: largest bright cluster of the non-reflective facade cells
    pts = lab[valid & ~refl]
    C = kmeans(pts, 4); lbl = assign(pts, C); share = np.bincount(lbl, minlength=len(C)) / len(pts)
    # local texture: std of L over 3x3 cells. Stucco/concrete is smooth; reflective glass and foliage remnants are busy.
    Lp = np.pad(np.where(valid, L, np.nan), 1, mode='edge')
    with np.errstate(all='ignore'), warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        tex = np.nanstd(np.stack([Lp[a:a + R, b:b + W] for a in range(3) for b in range(3)]), 0)
    ptex = tex[valid & ~refl]
    cand = [j for j in range(len(C)) if share[j] >= 0.08 and C[j][0] >= c['glass_l_max']] or [int(np.argmax(share))]
    ctex = {j: float(np.nanmedian(ptex[lbl == j])) for j in cand}
    smooth = min(ctex.values())
    wj = max((j for j in cand if ctex[j] <= smooth + 1.5), key=lambda j: share[j])
    near = np.zeros_like(valid); near[valid & ~refl] = assign(lab[valid & ~refl], C) == wj
    near &= (L >= c['glass_l_max']) & (np.linalg.norm(lab - C[wj], axis=-1) < 15)
    wall_rgb = np.median(rgb[near], 0); wall_lab = srgb_to_lab(wall_rgb * 255)
    chrom = rgb / (rgb.sum(-1, keepdims=True) + 1e-6); wall_chrom = wall_rgb / (wall_rgb.sum() + 1e-6)
    neutral = float(np.hypot(wall_lab[1], wall_lab[2])) < 6
    darker = L < wall_lab[0] - c['glass_dl']
    shadow = valid & ~refl & darker & (np.abs(chrom - wall_chrom)[..., :2].sum(-1) < c['shadow_chroma']) & ~np.bool_(neutral)
    glass = valid & ~shadow & (refl | (L < c['glass_l_max']) | (darker & (lab[..., 2] < -3)))
    # dark WARM material is not glass: brick, wood and terracotta stay warm (brown/red) once a colour cast is removed; glass reads
    # neutral or cool. Hue on the white-balanced colour when a cast was detected.
    lab_n = srgb_to_lab(_srgb(_lin(rgb) * wb) * 255) if wb is not None else lab
    if c.get('warm_ab') is not None:
        glass &= ~(valid & ~refl & (L < c['glass_l_max']) & (lab_n[..., 1] + lab_n[..., 2] > c['warm_ab']))
    opaque = valid & ~glass & ~shadow
    known = glass | opaque | shadow                                               # shadowed wall is wall (excluded from colours only)
    if isinstance(c.get('debug'), dict): c['debug'].update(rgb=rgb, valid=valid, glass=glass, shadow=shadow, opaque=opaque, refl=refl, wall_rgb=wall_rgb)   # diagnostics only
    v_of = lambda i: v_lo + i / ppm; u_of = lambda j: u_lo + j / ppm
    rows_with = np.flatnonzero(valid.any(1))
    data_top = v_of(rows_with.max() + 1); data_bot = v_of(rows_with.min())
    if near.sum() < 4:                     # no clean wall pixels (all glass / shadow / mixed): no wall colour to report
        return _empty_face(evidence, reasons + ['no clean wall pixels: wall colour not determinable']), stats
    face = _empty_face(evidence, [])
    unres = face['unresolved']

    # zones: main wall + horizontal runs whose colour differs from the wall (k-means per band)
    wall_conf = round(cov * float(min(1.0, near.sum() / max(1, opaque.sum()) + 0.3)), 3)
    if ccol < 1: wall_conf = round(wall_conf * ccol, 3); unres.append(f'colour cast {cast:.3f} > {c["cast_max"]}: colours are grey-world white-balanced ESTIMATES (inferred)')
    # the wall zone covers only what was seen (opaque/shadow cells): unseen parts of the face are never painted from this image
    sr, sc = np.flatnonzero((opaque | shadow).any(1)), np.flatnonzero((opaque | shadow).any(0))
    zones = [{'v0_m': round(float(v_lo + sr.min() / ppm), 2), 'v1_m': round(float(v_lo + (sr.max() + 1) / ppm), 2), 'u0_m': round(float(u_lo + sc.min() / ppm), 2),
              'u1_m': round(float(u_lo + (sc.max() + 1) / ppm), 2), 'role': 'wall', 'rgb': rep(wall_rgb), 'conf': wall_conf,
              **({'rgb_camera': _rgb255(wall_rgb)} if wb is not None else {})}]
    def band_runs(lab_a, op_a, n_min):
        de = np.zeros(lab_a.shape[0])
        for i in range(lab_a.shape[0]):
            m = op_a[i]
            if m.sum() >= n_min: de[i] = np.linalg.norm(np.median(lab_a[i][m], 0) - wall_lab)
        return _runs(de > c['zone_de'])
    def zone_rgb(sel_lab, sel_rgb):
        if len(sel_lab) < 4: return None, 0.0
        Cz = kmeans(sel_lab, 2); lz = assign(sel_lab, Cz); big = np.bincount(lz).argmax()
        m_ = np.median(sel_rgb[lz == big], 0); return (rep(m_), _rgb255(m_)), float((lz == big).mean())
    for a, b in band_runs(lab, opaque, max(3, 0.15 * W)):
        if b - a < 2: continue                                                    # 1-row runs are mixed-pixel edges at 4 px/m
        if b - a >= 4 and float(np.nanmedian(tex[a:b][opaque[a:b]])) > ctex[wj] + 4:           # busy texture: reflections / foliage, not a material
            unres.append(f'busy band v={v_of(a):.1f}-{v_of(b):.1f} m (reflection?) not used as a zone'); continue
        zr, sh = zone_rgb(lab[a:b][opaque[a:b]], rgb[a:b][opaque[a:b]])
        if zr is None: continue
        zr, zcam = zr; v0, v1 = v_of(a), v_of(b)
        role = 'base' if v0 <= data_bot + 0.5 else 'parapet' if v1 >= data_top - 0.5 else 'band' if v1 - v0 <= 1.5 else 'wall'
        zones.append({'v0_m': round(float(v0), 2), 'v1_m': round(float(v1), 2), 'u0_m': u_lo, 'u1_m': u_hi, 'role': role, 'rgb': zr,
                      'conf': round(cov * sh * ccol, 3), **({'rgb_camera': zcam} if wb is not None else {})})
    # trim: vertical runs touching the face ends (piers / end frames), <= 3 m wide
    cols_with = np.flatnonzero(valid.any(0))
    for a, b in band_runs(lab.transpose(1, 0, 2), opaque.T, max(3, 0.15 * R)):
        if (a > cols_with.min() + 1 and b < cols_with.max()) or (b - a) / ppm > 3.0: continue
        zr, sh = zone_rgb(lab[:, a:b][opaque[:, a:b]], rgb[:, a:b][opaque[:, a:b]])
        if zr: zr, zcam = zr
        if zr: zones.append({'v0_m': v_lo, 'v1_m': v_hi, 'u0_m': round(float(u_of(a)), 2), 'u1_m': round(float(u_of(b)), 2), 'role': 'trim',
                             'rgb': zr, 'conf': round(cov * sh * ccol, 3), **({'rgb_camera': zcam} if wb is not None else {})})
    face['zones'] = [z for z in zones if z['conf'] >= c['min_item_conf']][:32]
    if not face['zones']: unres.append('wall colour (low confidence)')

    # glazing
    n_known = int(known.sum())
    gl_px = glass & ~refl
    if n_known and glass.sum() >= 0.01 * n_known:
        frac = float(glass.sum()) / n_known
        g_rgb = np.median(rgb[gl_px], 0) if gl_px.sum() >= 4 else np.median(rgb[glass], 0)
        sep = float(np.clip((wall_lab[0] - np.median(L[glass])) / 40.0, 0, 1))
        conf = cov * (0.5 + 0.5 * sep)
        if conf >= c['min_item_conf']: face['glazing'] = {'fraction': round(frac, 3), 'rgb': rep(g_rgb), 'conf': round(conf, 3), **({'rgb_camera': _rgb255(g_rgb)} if wb is not None else {})}
        else: unres.append('glazing (low contrast to wall)')
    elif n_known:
        if cov * 0.5 >= c['min_item_conf']: face.setdefault('absent', []).append('glazing')     # enough wall seen, no glass in it
        else: unres.append('glazing')

    def frac_profile(axis_sum_g, axis_sum_k, n_need):
        p = np.where(axis_sum_k >= n_need, axis_sum_g / np.maximum(axis_sum_k, 1), np.nan)
        return np.where(np.isnan(p), np.nanmean(p) if np.isfinite(p).any() else 0.0, p)

    # floors: row-profile periodicity, 3-6 m
    rowp = frac_profile(glass.sum(1), known.sum(1), max(3, 0.2 * W))
    pr = period(rowp, c['floor_m'][0] * ppm, c['floor_m'][1] * ppm)
    Lk = np.where(known, L, 0.0)
    if not (pr and pr[1] >= c['period_min']):          # curtain walls: glass everywhere, floors show as luminance lines
        lum = _highpass(frac_profile(Lk.sum(1), known.sum(1), max(3, 0.2 * W)), int(c['floor_m'][1] * ppm))
        pl = period(lum, c['floor_m'][0] * ppm, c['floor_m'][1] * ppm)
        if pl and pl[1] >= c['period_min']: pr, rowp = pl, -lum                  # bright lines -> treated like spandrels
    if pr and pr[1] >= c['period_min'] and cov * pr[1] >= c['min_item_conf']:
        P = pr[0]; idx = np.arange(R)
        inner = (idx >= (data_bot - v_lo + c['ground_min_m']) * ppm) & (idx < (data_top - v_lo - 1.0) * ppm)   # skip ground floor and parapet
        phases = np.arange(int(math.ceil(P)))
        fold = [np.mean(rowp[inner & (idx % P >= ph) & (idx % P < ph + 1)]) if (inner & (idx % P >= ph) & (idx % P < ph + 1)).any() else 1.0 for ph in phases]
        fold = np.array(fold); low = phases[fold <= fold.min() + 0.05] + 0.5      # centre of the low (spandrel) plateau, circular
        ang = np.angle(np.exp(2j * np.pi * low / P).mean()); ph = (ang % (2 * np.pi)) * P / (2 * np.pi)
        med = np.median(rowp)
        lines = [ph + k * P for k in range(-1, int(R / P) + 2)]
        lines = [l for l in lines if v_of(l) >= max(c['ground_min_m'], data_bot + c['ground_min_m']) and v_of(l) <= data_top - 1.5
                 and rowp[int(np.clip(l, 0, R - 1))] <= med]
        if lines:
            first = v_of(lines[0])
            face['floors'] = {'first_m': round(float(first), 2), 'height_m': round(P / ppm, 2), 'count': 1 + len(lines),
                              'conf': round(cov * pr[1], 3)}
    if not face['floors']: unres.append('floors (no periodic row profile in 3-6 m)')

    # bays: column-profile periodicity over the upper floors
    r0 = int((face['floors']['first_m'] - v_lo) * ppm) if face['floors'] else int(c['ground_min_m'] * ppm)
    up = slice(min(r0, R - 1), R)
    colp = frac_profile(glass[up].sum(0), known[up].sum(0), max(3, 0.2 * (R - up.start)))
    pc = period(colp, c['bay_m'][0] * ppm, c['bay_m'][1] * ppm)
    if not (pc and pc[1] >= c['period_min']):
        lum = _highpass(frac_profile(Lk[up].sum(0), known[up].sum(0), max(3, 0.2 * (R - up.start))), int(c['bay_m'][1] * ppm))
        pl = period(lum, c['bay_m'][0] * ppm, c['bay_m'][1] * ppm)
        if pl and pl[1] >= c['period_min']: pc, colp = pl, -lum
    if pc and pc[1] >= c['period_min'] and cov * pc[1] >= c['min_item_conf']:
        thr = 0.5 * (colp.max() + colp.min())
        runs = [(a, b) for a, b in _runs(colp >= thr) if a > 0 and b < W]
        width = float(np.median([b - a for a, b in runs])) / ppm if runs else 0.0
        if runs and width < 0.3: unres.append(f'bays (opening width {width:.2f} m < 0.3 m: noise, not a rhythm)'); runs = []
        if runs:
            face['bays'] = {'pitch_m': round(pc[0] / ppm, 2), 'width_m': round(min(width, pc[0] / ppm), 2),
                            'offset_m': round(float(u_of(runs[0][0])), 2), 'conf': round(cov * pc[1], 3)}
    if not face['bays']: unres.append('bays (no periodic column profile in 1.5-12 m)')

    # entrances: ground-level dark/glass openings reaching the ground, 1.5-8 m wide
    ground = data_bot <= v_lo + 0.5                                               # entrances/canopies only when the ground is framed
    g0 = int(round((data_bot - v_lo) * ppm)); gb = slice(g0, g0 + int(0.5 * ppm)); gh = slice(g0, g0 + int(2.2 * ppm))
    kb, kh = known[gb].sum(0), known[gh].sum(0)
    ok = (kb >= 0.5 * (gb.stop - gb.start)) & (glass[gb].sum(0) >= 0.6 * np.maximum(kb, 1)) & (glass[gh].sum(0) >= 0.6 * np.maximum(kh, 1))
    seen = (kh >= 0.5 * (gh.stop - gh.start)).mean()
    for a, b in (_runs(ok) if ground else []):
        wm = (b - a) / ppm
        if wm < c['entrance_min_m']: continue
        if wm > c['entrance_max_m']:
            unres.append(f'ground glazing u={u_of(a):.1f}-{u_of(b):.1f} m (storefront? door position not determinable)'); continue
        conf = cov * float(glass[gh][:, a:b].sum() / max(1, known[gh][:, a:b].sum()))
        if conf >= c['min_item_conf']: face['entrances'].append({'u0_m': round(float(u_of(a)), 2), 'u1_m': round(float(u_of(b)), 2), 'conf': round(conf, 3)})
    ops_, onotes, small_door = _openings(glass, known, cov, ppm, u_of, v_of, data_bot, c)
    if small_door and ground:              # a ground-level opening lower than a door: the photo's scale does not match the face
        o = small_door[0]
        return _empty_face(evidence, reasons + [f"alignment: ground opening u={o['u0_m']}-{o['u1_m']} m only {o['v1_m'] - o['v0_m']:.1f} m tall "
                                                f"(< 1.8 m door): photo scale does not match the measured face"]), stats
    if ops_: face['openings'] = ops_
    unres += onotes
    if not ground: unres.append('entrances and canopies (ground level not in view)')
    elif seen < 0.5: unres.append('entrances (ground level mostly occluded)')

    # canopies: lit horizontal line, distinct from the wall, with shadowed wall directly below (low confidence)
    de_wall = np.linalg.norm(lab - wall_lab, axis=-1)
    fascia = opaque & (de_wall > 8) & (L >= wall_lab[0] - 10)
    for i in range(max(2, int((data_bot - v_lo + 2.0) * ppm)), min(R, int((data_bot - v_lo + 6.0) * ppm)) if ground else 0):
        below = shadow[i - 1] | shadow[i - 2]
        colok = fascia[i] & (below | glass[i - 1] | glass[i - 2])                 # doors / glass often sit under a canopy
        for a, b in _runs(colok):
            if below[a:b].mean() < 0.3 or (b - a) / ppm < c['entrance_min_m'] or any(abs(cn['v_m'] - v_of(i)) < 0.6 and cn['u0_m'] < u_of(b) and u_of(a) < cn['u1_m'] for cn in face['canopies']):
                continue
            conf = round(min(0.35, cov * 0.35), 3)
            if conf >= c['canopy_min_conf'] and len(face['canopies']) < 16:
                face['canopies'].append({'u0_m': round(float(u_of(a)), 2), 'u1_m': round(float(u_of(b)), 2), 'v_m': round(float(v_of(i)), 2), 'depth': 2, 'conf': conf})
    if face['canopies']: unres.append('canopy depth (not observable in a facade view; default 2)')

    if neutral: unres.append('shadow vs glass on a neutral wall (shadows may read as glazing)')
    confs = [z['conf'] for z in face['zones'][:1]] + [face[k]['conf'] for k in ('glazing', 'floors', 'bays') if face[k]]
    face['confidence'] = round(float(np.mean(confs)) if confs else 0.0, 3)
    face['status'] = 'observed' if face['zones'] and face['zones'][0]['role'] == 'wall' else 'unresolved'
    face['unresolved'] = (reasons + notes + unres)[:32]
    stats.update(wall_lab=[round(float(t), 1) for t in wall_lab], shadow_frac=round(float(shadow.sum()) / max(1, valid.sum()), 3),
                 floor_period=pr, bay_period=pc)
    return face, stats


# ---------------------------------------------------------------- model backends (optional, off unless configured)
class BackendError(Exception):
    pass


def _secret(name, c):
    v = os.environ.get(name)
    if v: return v.strip()
    p = c.get('secrets_file')
    if p and os.path.isfile(p):
        txt = open(p, encoding='utf-8').read()
        try:
            d = json.loads(txt); v = d.get(name) if isinstance(d, dict) else None
        except ValueError:
            v = next((ln.split('=', 1)[1].strip() for ln in txt.splitlines() if ln.split('=', 1)[0].strip() == name and '=' in ln), None)
    return v or None


def _scrub(msg, secret):
    msg = str(msg)
    if secret: msg = msg.replace(secret, '<redacted>')
    return re.sub(r'(?i)(key|token|authorization)=[^&\s]+', r'\1=<redacted>', msg)[:300]


def _model_image(images, projections, face_meta, c):
    """fused rectified facade as PNG bytes (long side <= model_max_px), oriented top = v_hi"""
    from PIL import Image
    u_lo, u_hi = _extent(face_meta, 'u_extent_m'); v_lo, v_hi = _extent(face_meta, 'v_extent_m')
    ppm = min(c['model_ppm'], c['model_max_px'] / max(u_hi - u_lo, v_hi - v_lo))
    acc = 0; den = 0
    for img, proj in list(zip(images, projections))[:c['max_images']]:
        a = _as_array(img)
        if a is None: continue
        try:
            rgb, inside = rectify(a, proj, face_meta, ppm, 1, c['border_px'])
        except (KeyError, TypeError, ValueError, np.linalg.LinAlgError):
            continue
        w = _weight(proj) * inside * _in_quad(proj, face_meta, rgb.shape[0], rgb.shape[1], ppm)
        acc = acc + rgb * w[..., None]; den = den + w
    if isinstance(den, int): raise BackendError('no usable image')
    out = np.flip(acc / np.maximum(den, 1e-9)[..., None], 0)
    buf = io.BytesIO(); Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).save(buf, 'PNG')
    return buf.getvalue(), out.shape[1], out.shape[0]


def _prompt(face_meta, w, h):
    u0, u1 = _extent(face_meta, 'u_extent_m'); v0, v1 = _extent(face_meta, 'v_extent_m')
    return ('The image is a rectified building facade: x spans u=%.2f..%.2f m (left to right), y spans v=%.2f..%.2f m '
            '(bottom = ground, top = measured roof height). Image size %dx%d px. Describe ONLY what is visible; omit what you '
            'cannot see and list it under "unresolved". Reply with ONE JSON object and nothing else, with exactly these keys:\n'
            '{"confidence": 0..1, "zones": [{"v0_m","v1_m","u0_m","u1_m","role": "wall|base|band|parapet|trim", "rgb": [r,g,b], '
            '"conf"}], "glazing": {"fraction","rgb","conf"} or null, "floors": {"first_m","height_m","count","conf"} or null, '
            '"bays": {"pitch_m","width_m","offset_m","conf"} or null, "entrances": [{"u0_m","u1_m","conf"}], "canopies": '
            '[{"u0_m","u1_m","v_m","depth": 1..6,"conf"}], "unresolved": ["..."], "absent": ["glazing|floors|bays|entrances|canopies"]}\n'
            'Never use 0 as a placeholder: an unknown glazing/floors/bays object is null, an unknown list is []. '
            'UNKNOWN vs ABSENT: if a feature is hidden, cropped or unclear, set it null / [] and do NOT list it in "absent". List a '
            'feature in "absent" only when the visible wall clearly shows it does not exist (e.g. a blank wall has no glazing).\n'
            'All coordinates in metres inside the ranges above; rgb ints 0..255; conf 0..1. No other keys, no prose, no code.' % (u0, u1, v0, v1, w, h))


def _repair_note(errs):
    return ('\n\nYour previous reply was REJECTED by the validator:\n- ' + '\n- '.join(e[:160] for e in errs[:8]) +
            '\nReply again with ONE corrected JSON object. Remove or fix anything you cannot place inside the measured ranges.')


def parse_model_output(text, face_meta):
    """model text -> (model_face, errors). Data only: parsed as JSON, never evaluated; extra keys / out-of-range rejected."""
    if not isinstance(text, str) or len(text) > 65536: return None, ['output missing or too large']
    t = text.strip()
    t = re.sub(r'^```(json)?\s*|\s*```$', '', t)
    def bad_const(x): raise ValueError(f'non-finite number {x}')
    try:
        obj = json.loads(t, parse_constant=bad_const)
    except ValueError as e:
        return None, [f'not JSON: {str(e)[:80]}']
    if not isinstance(obj, dict): return None, ['not a JSON object']
    errs = validate_face(obj, face_meta, MODEL_FACE)
    return (None, errs) if errs else (obj, [])


def _ledger_path(c, day):
    return os.path.join(c['cache_dir'], 'facade', 'ai-ledger', f'{day}.json')


def _ledger_load(c, day):
    p = _ledger_path(c, day)
    try:
        with open(p, encoding='utf-8') as f: d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get('jobs'), dict): return d
    except (OSError, ValueError):
        pass
    return {'date': day, 'total_usd': 0.0, 'jobs': {}, 'calls': []}


def _ledger_add(c, day, job, usd, entry):
    # ponytail: read-modify-write without a lock; concurrent prep jobs could lose an entry. Add a lock file if jobs run in parallel.
    d = _ledger_load(c, day)
    d['total_usd'] = round(d['total_usd'] + usd, 6); d['jobs'][job] = round(d['jobs'].get(job, 0.0) + usd, 6)
    d['calls'] = (d['calls'] + [entry])[-500:]
    os.makedirs(os.path.dirname(_ledger_path(c, day)), exist_ok=True)
    tmp = _ledger_path(c, day) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(d, f, indent=1)
    os.replace(tmp, _ledger_path(c, day))


def estimate_anthropic_usd(prompt, w, h, c):
    """upper-bound estimate: prompt text + image tokens (w*h/750) at the input price, max_tokens at the output price"""
    tin = len(prompt) / 3.0 + (w * h) / 750.0 + 100
    return (tin * c['anthropic_usd_per_mtok_in'] + c['anthropic_max_tokens'] * c['anthropic_usd_per_mtok_out']) / 1e6


def _anthropic(images, projections, face_meta, c, extra=''):
    import requests
    key = _secret('ANTHROPIC_API_KEY', c)
    if not (c['ai_max_usd_per_job'] > 0 and c['ai_max_usd_per_day'] > 0 and key and c.get('cache_dir')):
        raise BackendError('anthropic backend not configured (needs ai caps > 0, cache_dir and a key)')
    png, w, h = _model_image(images, projections, face_meta, c)
    prompt = _prompt(face_meta, w, h) + extra
    est = estimate_anthropic_usd(prompt, w, h, c)
    day = datetime.date.today().isoformat(); job = str(c['job_id'])
    led = _ledger_load(c, day)
    if led['jobs'].get(job, 0.0) + est > c['ai_max_usd_per_job']:
        raise BackendError(f'anthropic cost cap: job would reach ${led["jobs"].get(job, 0.0) + est:.4f} > ${c["ai_max_usd_per_job"]:.4f}')
    if led['total_usd'] + est > c['ai_max_usd_per_day']:
        raise BackendError(f'anthropic cost cap: day would reach ${led["total_usd"] + est:.4f} > ${c["ai_max_usd_per_day"]:.4f}')
    body = {'model': c['anthropic_model'], 'max_tokens': c['anthropic_max_tokens'], 'output_config': {'effort': 'low'},
            'messages': [{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(png).decode()}},
                {'type': 'text', 'text': prompt}]}]}
    headers = {'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'}
    entry = {'t': datetime.datetime.now().isoformat(timespec='seconds'), 'job': job, 'est_usd': round(est, 6)}
    try:
        r = requests.post(c['anthropic_url'], json=body, headers=headers, timeout=c['model_timeout_s'])
    except requests.RequestException as e:
        _ledger_add(c, day, job, 0.0, dict(entry, status='error', usd=0.0))
        raise BackendError('anthropic request failed: ' + _scrub(type(e).__name__, key))
    if r.status_code != 200:
        _ledger_add(c, day, job, 0.0, dict(entry, status=f'http {r.status_code}', usd=0.0))
        raise BackendError(f'anthropic HTTP {r.status_code}')
    try:
        data = r.json(); u = data.get('usage') or {}
        tin = int(u.get('input_tokens', 0)) + int(u.get('cache_creation_input_tokens', 0) or 0) + int(u.get('cache_read_input_tokens', 0) or 0)
        tout = int(u.get('output_tokens', 0))
    except (ValueError, TypeError, AttributeError):
        _ledger_add(c, day, job, est, dict(entry, status='unparsable response, charged estimate', usd=round(est, 6)))
        raise BackendError('anthropic response not JSON')
    usd = (tin * c['anthropic_usd_per_mtok_in'] + tout * c['anthropic_usd_per_mtok_out']) / 1e6
    _ledger_add(c, day, job, usd, dict(entry, status='ok', usd=round(usd, 6), input_tokens=tin, output_tokens=tout))
    if data.get('stop_reason') == 'refusal':
        raise BackendError('anthropic refused the request', usd)
    text = ''.join(b.get('text', '') for b in data.get('content') or [] if isinstance(b, dict) and b.get('type') == 'text')
    return text, usd


def _ollama_schema(face_meta):
    """exact JSON schema for Ollama structured output: MODEL_FACE keys, types, enums, item caps and THIS face's u/v ranges.
    The decoder may not enforce every numeric bound, so validate_face still checks everything (incl. cross-field rules)."""
    u0, u1 = _extent(face_meta, 'u_extent_m'); v0, v1 = _extent(face_meta, 'v_extent_m')
    def rng(lo, hi, t='number'): return {'type': t, 'minimum': lo, 'maximum': hi}
    U, V, conf = rng(u0, u1), rng(v0, v1), rng(0, 1)
    rgb = {'type': 'array', 'items': rng(0, 255, 'integer'), 'minItems': 3, 'maxItems': 3}
    def obj(props, nullable=False):
        o = {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}
        return {'anyOf': [o, {'type': 'null'}]} if nullable else o
    def arr(item, n): return {'type': 'array', 'items': item, 'maxItems': n}
    return obj({'confidence': conf,
                'zones': arr(obj({'v0_m': V, 'v1_m': V, 'u0_m': U, 'u1_m': U,
                                  'role': {'enum': list(ROLES)}, 'rgb': rgb, 'conf': conf}), 32),
                'glazing': obj({'fraction': conf, 'rgb': rgb, 'conf': conf}, True),
                'floors': obj({'first_m': V, 'height_m': rng(2.5, 8), 'count': rng(1, 200, 'integer'), 'conf': conf}, True),
                'bays': obj({'pitch_m': rng(0.5, max(0.5, min(50, u1 - u0))), 'width_m': rng(0.3, 50), 'offset_m': U, 'conf': conf}, True),
                'entrances': arr(obj({'u0_m': U, 'u1_m': U, 'conf': conf}), 16),
                'canopies': arr(obj({'u0_m': U, 'u1_m': U, 'v_m': V, 'depth': rng(1, 6, 'integer'), 'conf': conf}), 16),
                'unresolved': arr({'type': 'string', 'maxLength': 300}, 32),
                'absent': {'type': 'array', 'items': {'enum': list(FEATURES)}, 'maxItems': len(FEATURES), 'uniqueItems': True}})


def _ollama(images, projections, face_meta, c, extra=''):
    import requests
    if not c.get('ollama_model'): raise BackendError('ollama backend not configured (ollama_model)')
    png, w, h = _model_image(images, projections, face_meta, c)
    body = {'model': c['ollama_model'], 'prompt': _prompt(face_meta, w, h) + extra, 'images': [base64.b64encode(png).decode()],
            'stream': False, 'format': _ollama_schema(face_meta), 'options': dict({'temperature': 0, 'num_ctx': c['ollama_num_ctx']}, **({} if c['ollama_num_gpu'] is None else {'num_gpu': int(c['ollama_num_gpu'])})), 'keep_alive': c['ollama_keep_alive']}
    try:
        r = requests.post(c['ollama_url'].rstrip('/') + '/api/generate', json=body, timeout=c['model_timeout_s'])
    except requests.RequestException as e:
        raise BackendError(f'ollama request failed: {type(e).__name__}')
    if r.status_code != 200: raise BackendError(f'ollama HTTP {r.status_code}')
    try:
        return r.json().get('response'), 0.0
    except (ValueError, AttributeError):
        raise BackendError('ollama response not JSON')


def _model(kind, images, projections, face_meta, c):
    evidence = [_evidence(p) for img, p in zip(images, projections) if img is not None][:16]
    if not evidence: return _empty_face([], ['no usable image']), _cost()
    # the deterministic occlusion/coverage gate runs first: a model never reads a face builtin-cv found blocked (measured: qwen2.5vl:7b
    # described a 73%-tree photo as a grey wall with a 10 m entrance). No model call, no cost, when the gate fails.
    gate, _ = _builtin(images, projections, face_meta, c)
    if gate['status'] != 'observed' and any('coverage' in u for u in gate.get('unresolved', [])):
        return _empty_face(evidence, [f'{kind} not asked: ' + next(u for u in gate['unresolved'] if 'coverage' in u)]), _cost()
    fn = _anthropic if kind == 'anthropic' else _ollama
    usd_all, sent_all, extra = 0.0, 0, ''
    trace = c.get('trace') if isinstance(c.get('trace'), list) else []      # caller-owned diagnostics: raw replies, errors, timings
    for attempt in (1, 2):             # one bounded repair retry: the validator's errors go back to the model once
        t0 = time.time()
        budget = c.get('_call_budget')                       # per job (analyze_building): bounds total model calls
        try:
            if budget is not None:
                if budget[0] <= 0: raise BackendError(f"model call budget for this job ({c['model_max_calls_per_job']}) used up: not analysed")
                budget[0] -= 1
            text, usd = fn(images, projections, face_meta, c, extra)
        except BackendError as e:
            trace.append({'attempt': attempt, 'seconds': round(time.time() - t0, 2), 'raw': None, 'errors': [str(e.args[0])[:300]]})
            usd = e.args[1] if len(e.args) > 1 else 0.0
            sent = 0 if ('not configured' in e.args[0] or 'cost cap' in e.args[0] or 'no usable' in e.args[0] or 'budget' in e.args[0]) else 1
            what = 'backend failed (no fallback)' if attempt == 1 else 'ANALYSIS FAILED: repair retry failed'
            return _empty_face(evidence, [f'{kind} {what}: {e.args[0]}']), _cost(kind, usd_all + usd, sent_all + sent)
        usd_all += usd; sent_all += 1
        obj, errs = parse_model_output(text, face_meta)
        trace.append({'attempt': attempt, 'seconds': round(time.time() - t0, 2), 'raw': text if isinstance(text, str) else None, 'errors': errs})
        if not errs: break
        if attempt == 1: extra = _repair_note(errs)
    cost = _cost(kind, usd_all, sent_all)
    if errs:
        return _empty_face(evidence, [f'{kind} ANALYSIS FAILED: output invalid after 1 repair retry: ' + '; '.join(errs[:3])[:260]]), cost
    face = dict(obj, evidence=evidence)
    if sent_all > 1: face['unresolved'] = list(face['unresolved']) + [f'{kind}: first reply rejected, repaired on retry']
    face['status'] = 'observed' if obj['zones'] and obj['confidence'] >= c['min_item_conf'] else 'unresolved'
    # model self-reported confidence is not calibrated: every model item is capped below the 0.5 "observed" line, so the
    # compiler labels it `inferred`, never `observed`
    for k in ('zones', 'entrances', 'canopies'):
        for it in face.get(k) or []: it['conf'] = min(it['conf'], 0.49)
    for k in ('glazing', 'floors', 'bays'):
        if face.get(k): face[k]['conf'] = min(face[k]['conf'], 0.49)
    face['unresolved'] = list(face.get('unresolved') or []) + [f'{kind} model output: labelled inferred (not observed)']
    return face, cost


# ---------------------------------------------------------------- public entry
def analyze(image_rgb, projection, face_meta, cfg=None):
    """-> (face_section, cost). See the module docstring."""
    c = dict(DEFAULTS); c.update(cfg or {})
    images = image_rgb if isinstance(image_rgb, (list, tuple)) else [image_rgb]
    projs = projection if isinstance(projection, (list, tuple)) else [projection]
    if len(projs) != len(images): projs = (list(projs) + [None] * len(images))[:len(images)]
    backend = c['backend']
    if backend == 'builtin-cv':
        face, _ = _builtin(images, [p or {} for p in projs], face_meta, c)
        cost = _cost()
    elif backend in ('ollama', 'anthropic'):
        face, cost = _model(backend, images, [p or {} for p in projs], face_meta, c)
    else:
        face, cost = _empty_face([], [f'unknown analyzer backend {backend!r}']), _cost()
    errs = validate_face(face, face_meta)
    if errs:      # never hand an invalid section downstream
        face = _empty_face(face.get('evidence', [])[:16], ['ANALYSIS FAILED: output invalid: ' + '; '.join(errs[:3])[:270]])
    elif face['status'] == 'observed':
        unk = [k for k, s in feature_states(face).items() if s == 'unknown']
        if unk: face['unresolved'] = (list(face['unresolved']) + ['unknown (not determined, not placed): ' + ', '.join(unk)])[:32]
    return face, cost


def _job_cfg(cfg):
    """facade_prep.auto_cfg keys -> DEFAULTS keys (analyzer, timeout_s, max_images_per_face, cache)"""
    cfg = dict(cfg or {}); c = {k: cfg[k] for k in DEFAULTS if k in cfg}
    for src, dst in (('analyzer', 'backend'), ('timeout_s', 'provider_timeout_s'), ('max_images_per_face', 'max_images'), ('cache', 'cache_dir')):
        if cfg.get(src) not in (None, ''): c.setdefault(dst, cfg[src])
    return c


def _face_meta(fg):
    """geometry face (facade-geometry-1) -> face_meta; extents as facade_autospec.extent: u 0..length_u, v rows base_y..top_y"""
    U = max(float(fg.get('length_u') or 0), len(fg.get('columns') or []))
    return {'face_id': fg['id'], 'u_extent_m': [0.0, U], 'v_extent_m': [0.0, float(int(fg['top_y']) - int(fg['base_y']) + 1)]}


def projections_of(acc, fg, img_wh):
    """one facade_associate accepted entry -> [projection] (one per face segment quad). v = Y - base_y (SOURCES.md 3.1)."""
    P = acc.get('projection') or {}
    if P.get('model') != 'perspective': return [], 'panorama rectification not implemented in builtin-cv'
    w, h = img_wh; out = []
    ev = {k: acc.get(k) for k in EVIDENCE}
    for q in P.get('quads') or []:
        cn = q.get('corners_norm') or []
        if len(cn) != 4 or any(c is None for c in cn): continue                  # a corner behind the camera
        out.append({'corners_px': dict(zip(('u0v0', 'u1v0', 'u1v1', 'u0v1'), ([c[0] * w, c[1] * h] for c in cn))),
                    'u_m': [float(q['u0']), float(q['u1'])], 'v_m': [float(q['y0']) - fg['base_y'], float(q['y1']) - fg['base_y']], 'evidence': ev})
    return out, None if out else 'no quad with all corners in front of the camera'


def analyze_building(g, ev, assoc, cfg=None):
    """facade_prep stage: -> {"faces": {face_id: face_section}, "cost": {...}, "model": str|None}. Faces without accepted images are
    left out (prep makes them procedural/unresolved)."""
    from PIL import Image
    c = _job_cfg(cfg); c['_call_budget'] = [int(c.get('model_max_calls_per_job', DEFAULTS['model_max_calls_per_job']))]
    af = assoc.get('faces') if isinstance(assoc, dict) and isinstance(assoc.get('faces'), dict) else (assoc or {})
    out, cost = {}, _cost()
    R = None
    for fg in g['faces']:
        acc = ((af.get(fg['id']) or {}).get('accepted') or [])[:int(c.get('max_images', DEFAULTS['max_images']))]
        if not acc: continue
        meta = _face_meta(fg); imgs, projs, why, lab = [], [], [], None
        for a in acc:
            # provider poses (GPS a few m, SfM/compass a few deg) are not facade-accurate and no verifier is reliable yet (2026-10-30:
            # edge registration "verified" wrong poses on real photos). Unverified provider images are never used: reject when unsure.
            if a.get('provider') not in VERIFIED_POSE_PROVIDERS and not a.get('pose_verified'):        # assisted: 'manual corners'
                why.append(f"image {a.get('image_id')}: provider pose not verified to facade accuracy (GPS/heading error of metres/degrees)"); continue
            path = a.get('path')
            if not path and a.get('url'):
                if R is None:
                    try: import facade_sources as R
                    except ImportError: R = False
                path, err = R.download(a, dict(cfg or {})) if R else (None, 'facade_sources missing')
                if err: why.append(f'image {a.get("image_id")}: {err}'); continue
            try:
                with Image.open(path) as im: arr = np.asarray(im.convert('RGB'))
            except (OSError, TypeError, ValueError):
                why.append(f'image {a.get("image_id")}: unreadable'); continue
            ps, err = projections_of(a, fg, (arr.shape[1], arr.shape[0]))
            if a.get('mask_norm'):                 # assisted occluder marks (vehicles, poles): those pixels are unknown, never wall or glass
                occ = np.zeros(arr.shape[:2] + (3,), np.float32); hh, ww = arr.shape[:2]
                for x0, y0, x1, y1 in a['mask_norm']: occ[int(y0 * hh):int(y1 * hh), int(x0 * ww):int(x1 * ww)] = 1.0
                for p_ in ps: p_['occluder'] = occ
            if err: why.append(f'image {a.get("image_id")}: {err}')
            imgs += [arr] * len(ps); projs += ps
            if a.get('labels') and ps: lab = (a['labels'], arr, ps[0])
        try: face, fc = analyze(imgs or None, projs or [{}], meta, c)
        except Exception as ex:            # one face's failure never drops the others; reported on that face
            face, fc = _empty_face([], [f'ANALYSIS FAILED on this face: {type(ex).__name__}: {str(ex)[:120]}']), _cost()
        if lab:                            # ASSISTED zones drawn by a person on that aligned photo (replace detection per labelled kind)
            try: why += apply_labels(face, lab[0], lab[1], lab[2], meta, lab[2].get('occluder'))
            except Exception as ex: why.append(f'ASSISTED zones failed: {type(ex).__name__}: {str(ex)[:120]}')
            errs = validate_face(face, meta)
            if errs: face, why = _empty_face(face.get('evidence', [])[:16], []), why + ['ASSISTED zones invalid: ' + '; '.join(errs[:3])[:270]]
        if why: face['unresolved'] = (why + face['unresolved'])[:32]
        if face['status'] == 'observed' and any(a.get('assisted') for a in acc): face['assisted'] = True
        out[fg['id']] = face
        cost['usd'] = round(cost['usd'] + fc['usd'], 6); cost['requests'] += fc['requests']
        for k, v in fc['by_provider'].items():
            q = cost['by_provider'].setdefault(k, {'usd': 0.0, 'requests': 0}); q['usd'] = round(q['usd'] + v['usd'], 6); q['requests'] += v['requests']
    return {'faces': out, 'cost': cost, 'model': analyzer_id(c)['model']}


def analyzer_id(cfg=None):
    c = dict(DEFAULTS); c.update(cfg or {})
    model = c['anthropic_model'] if c['backend'] == 'anthropic' else c['ollama_model'] if c['backend'] == 'ollama' else None
    return {'backend': c['backend'], 'version': ANALYZER_VERSION, 'model': model}


# ---------------------------------------------------------------- self-test (synthetic facades + stub HTTP)
SKY = (120, 165, 228); TREE = (92, 138, 48); WALL = (205, 185, 145)


def synth(corners, size=(1024, 768), tree=None, seed=0, v_claim=17.0):
    """synthetic facade photo. Facade 24 x 16 m: base 0-0.6 gray; storefront glass 0.6-3.8 (u 1-7, 12-23); double door
    u 8.5-10.5 v 0-2.6 with a fascia (v 3.0-3.3) and its shadow; floors at 4.5/8/11.5 m (3.5 m), windows 2 m wide at 4 m
    pitch (offset 1 m), sill +1.0, head +2.75; cream parapet 15-16 m; cast shadow band 9.6-10.4 m. The face is CLAIMED
    v_claim m tall (corners map v=0..v_claim), so the top metre is sky. -> (PIL image, truth dict)"""
    from PIL import Image, ImageDraw
    P = 20; Wm, Hm = 24.0, 16.0
    lab = np.zeros((int(v_claim * P), int(Wm * P)), np.uint8)       # 0 none, 1 wall, 2 glass, 3 base, 4 parapet, 5 fascia
    can = np.zeros(lab.shape + (3,), float)
    def rect(u0, u1, v0, v1, k):
        lab[int((v_claim - v1) * P):int((v_claim - v0) * P), int(u0 * P):int(u1 * P)] = k
    rect(0, Wm, 0, Hm, 1); rect(0, Wm, 0, 0.6, 3); rect(0, Wm, 15, 16, 4)
    rect(1, 7, 0.6, 3.8, 2); rect(12, 23, 0.6, 3.8, 2); rect(8.5, 10.5, 0, 2.6, 2); rect(6.5, 12.5, 3.0, 3.3, 5)
    for f in (4.5, 8.0, 11.5):
        for k in range(6): rect(1 + 4 * k, 3 + 4 * k, f + 1.0, f + 2.75, 2)
    cols = {1: WALL, 2: (55, 70, 92), 3: (122, 120, 114), 4: (238, 230, 212), 5: (232, 232, 228)}
    for k, col in cols.items(): can[lab == k] = col
    def shade(u0, u1, v0, v1, f):
        can[int((v_claim - v1) * P):int((v_claim - v0) * P), int(u0 * P):int(u1 * P)] *= f
    shade(0, Wm, 9.6, 10.4, 0.5)
    m = np.zeros(lab.shape, bool); m[int((v_claim - 3.0) * P):int((v_claim - 2.6) * P), int(6.5 * P):int(12.5 * P)] = True
    can[m & (lab == 1)] *= 0.5
    fac = Image.fromarray(np.clip(can, 0, 255).astype(np.uint8)); alpha = Image.fromarray(((lab > 0) * 255).astype(np.uint8))
    H = homography([(0, 0), (Wm, 0), (Wm, v_claim), (0, v_claim)], [corners['u0v0'], corners['u1v0'], corners['u1v1'], corners['u0v1']])
    Cv = np.array([[P, 0, 0], [0, -P, v_claim * P], [0, 0, 1.0]])          # metric -> canvas px
    M = Cv @ np.linalg.inv(H); M = M / M[2, 2]
    coeff = tuple(M.flatten()[:8])
    W, Hh = size
    yy = np.linspace(0, 1, Hh)[:, None, None]
    bg = (np.array(SKY) * (1 - 0.4 * yy) + np.array((200, 215, 240)) * 0.4 * yy) * np.ones((1, W, 1))
    bg[int(Hh * 0.82):] = (110, 108, 104)
    img = Image.fromarray(bg.astype(np.uint8))
    img.paste(fac.transform(size, Image.PERSPECTIVE, coeff, Image.BILINEAR), (0, 0),
              alpha.transform(size, Image.PERSPECTIVE, coeff, Image.NEAREST))
    d = ImageDraw.Draw(img)
    if tree:
        cx, cy, rx, ry = tree
        d.rectangle([cx - 10, cy, cx + 10, cy + ry * 2], fill=(88, 62, 40))
        rng = np.random.default_rng(seed)
        for _ in range(60):
            x, y = cx + rng.normal(0, rx / 2), cy + rng.normal(0, ry / 2); rr = rng.uniform(15, 35)
            gcol = tuple(int(t) for t in np.clip(np.array(TREE) + rng.normal(0, 12, 3), 0, 255))
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=gcol)
    a = np.asarray(img).astype(float) + np.random.default_rng(seed + 1).normal(0, 3, (Hh, W, 3))
    face = lab[int((v_claim - Hm) * P):]
    truth = {'glazing': float((face == 2).sum()) / (face > 0).sum(), 'floor_h': 3.5, 'first': 4.5, 'count': 4, 'pitch': 4.0,
             'width': 2.0, 'offset': 1.0, 'wall': WALL, 'door': (8.5, 10.5)}
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), truth


def _de(a, b):
    return float(np.linalg.norm(srgb_to_lab(a) - srgb_to_lab(b)))


class _Stub:
    """local stub HTTP server; replies with a canned JSON body, counts hits"""
    def __init__(self, reply, status=200):
        import http.server, threading
        stub = self; self.hits = 0; self.reply = reply; self.status = status; self.headers = []; self.bodies = []
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(s):
                stub.hits += 1; stub.headers.append(dict(s.headers))
                stub.bodies.append(s.rfile.read(int(s.headers.get('content-length', 0))))
                b = json.dumps(stub.reply.pop(0) if isinstance(stub.reply, list) else stub.reply).encode()     # list: one reply per call
                s.send_response(stub.status); s.send_header('content-type', 'application/json'); s.send_header('content-length', str(len(b)))
                s.end_headers(); s.wfile.write(b)
            def log_message(s, *a): pass
        self.srv = http.server.HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.srv.server_port}'
    def close(self): self.srv.shutdown(); self.srv.server_close()


def selftest(verbose=True):
    import tempfile
    out = {}
    meta = {'face_id': 'f0', 'u_extent_m': [0, 24.0], 'v_extent_m': [0, 17.0]}
    # ASSISTED labels: an L-shaped polygon -> exact 1 m cell rectangles; a polygon past the face is clipped to it
    assert _poly_rects([[0, 0], [4, 0], [4, 2], [2, 2], [2, 5], [0, 5]], 10, 7) == [(0.0, 2.0, 2.0, 5.0), (0.0, 4.0, 0.0, 2.0)]
    assert _poly_rects([[8, 5], [12, 5], [12, 9], [8, 9]], 10, 7) == [(8.0, 10.0, 5.0, 7.0)]
    C1 = {'u0v0': (140, 660), 'u1v0': (930, 610), 'u1v1': (905, 95), 'u0v1': (165, 40)}
    img, T = synth(C1, tree=(300, 470, 110, 90))
    ev1 = {'provider': 'test', 'image_id': 'synth-1', 'captured_at': '2026-10', 'dist_m': 30.0, 'view_angle_deg': 15.0, 'assoc_score': 0.9, 'attribution': 'synthetic'}
    face, cost = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta)
    assert not validate_face(face, meta), validate_face(face, meta)
    fl, by, gl = face['floors'], face['bays'], face['glazing']
    wall = face['zones'][0]
    out['single'] = {'floors': fl, 'bays': by, 'glazing': gl, 'wall_rgb': wall['rgb'], 'wall_dE': round(_de(wall['rgb'], T['wall']), 2),
                     'zones': [(z['role'], z['v0_m'], z['v1_m'], z['rgb']) for z in face['zones']], 'entrances': face['entrances'],
                     'canopies': face['canopies'], 'truth_glazing': round(T['glazing'], 3), 'confidence': face['confidence'],
                     'unresolved': face['unresolved']}
    assert face['status'] == 'observed'
    assert fl and abs(fl['height_m'] - T['floor_h']) <= 0.25 and abs(fl['first_m'] - T['first']) <= 0.6 and fl['count'] == T['count'], fl
    assert by and abs(by['pitch_m'] - T['pitch']) <= 0.25 and abs(by['width_m'] - T['width']) <= 0.5 and abs(by['offset_m'] - T['offset']) <= 0.5, by
    assert gl and abs(gl['fraction'] - T['glazing']) <= 0.07, (gl, T['glazing'])
    assert wall['role'] == 'wall' and _de(wall['rgb'], T['wall']) <= 6, wall
    for z in face['zones']:
        assert _de(z['rgb'], SKY) > 20 and _de(z['rgb'], TREE) > 20, ('sky/tree colour leaked into a zone', z)
    roles = {z['role'] for z in face['zones']}
    assert 'parapet' in roles and 'base' in roles, roles
    assert any(abs(e['u0_m'] - 8.5) <= 0.75 and abs(e['u1_m'] - 10.5) <= 0.75 for e in face['entrances']), face['entrances']
    import facade_materials as FM
    pal = FM.palette_for_face(face)
    assert not re.search(r'blue|cyan|green|lime|leaves|light_blue', pal['roles']['wall']), pal['roles']
    out['palette'] = pal['roles']

    # two images: the second is oblique, cropped (right part out of frame) and tree-occluded elsewhere -> fused coverage up
    C2 = {'u0v0': (300, 700), 'u1v0': (1250, 560), 'u1v1': (1230, 150), 'u0v1': (310, 60)}
    img2, _ = synth(C2, tree=(700, 380, 120, 120), seed=3)
    ev2 = dict(ev1, image_id='synth-2', view_angle_deg=40.0, assoc_score=0.7)
    _, st1 = _builtin([np.asarray(img)], [{'corners_px': C1, 'evidence': ev1}], meta, dict(DEFAULTS))
    f2, st2 = _builtin([np.asarray(img), np.asarray(img2)], [{'corners_px': C1, 'evidence': ev1}, {'corners_px': C2, 'evidence': ev2}], meta, dict(DEFAULTS))
    assert st2['coverage'] > st1['coverage'] and len(f2['evidence']) == 2 and abs(f2['floors']['height_m'] - 3.5) <= 0.25, (st1['coverage'], st2['coverage'])
    out['fusion'] = {'coverage_1': st1['coverage'], 'coverage_2': st2['coverage'], 'masks_1': st1['masks'][0]}

    # pipeline adapter: facade_associate accepted entries (normalised corners, block-unit quads) -> analyze_building
    with tempfile.TemporaryDirectory() as td:
        ip = os.path.join(td, 'synth.png'); img.save(ip)
        fg = {'id': 'f0', 'edge': 'exterior', 'length_u': 24.0, 'columns': [], 'base_y': 0, 'top_y': 16}
        cn = [[C1[k][0] / 1024, C1[k][1] / 768] for k in ('u0v0', 'u1v0', 'u1v1', 'u0v1')]
        quads = [{'seg': 0, 'u0': 0, 'u1': 12, 'y0': 0, 'y1': 17, 'corners_norm': [cn[0], [(cn[0][0] + cn[1][0]) / 2, (cn[0][1] + cn[1][1]) / 2], [(cn[3][0] + cn[2][0]) / 2, (cn[3][1] + cn[2][1]) / 2], cn[3]]},
                 {'seg': 1, 'u0': 12, 'u1': 24, 'y0': 0, 'y1': 17, 'corners_norm': [[(cn[0][0] + cn[1][0]) / 2, (cn[0][1] + cn[1][1]) / 2], cn[1], cn[2], [(cn[3][0] + cn[2][0]) / 2, (cn[3][1] + cn[2][1]) / 2]]}]
        acc = dict(ev1, provider='local', path=ip, projection={'model': 'perspective', 'quads': quads})
        pano = dict(ev1, provider='local', image_id='pano', path=ip, projection={'model': 'equirectangular', 'quads': quads})
        mly = dict(ev1, provider='mapillary', image_id='mly-1', path=ip, projection={'model': 'perspective', 'quads': quads})
        rm = analyze_building({'faces': [fg]}, {}, {'faces': {'f0': {'accepted': [mly]}}}, {'analyzer': 'builtin-cv'})['faces']['f0']
        assert rm['status'] == 'unresolved' and 'pose not verified' in rm['unresolved'][0], rm['unresolved']     # unverified provider pose: never used
        res = analyze_building({'faces': [fg]}, {}, {'faces': {'f0': {'accepted': [acc, pano]}}}, {'analyzer': 'builtin-cv'})
        fb = res['faces']['f0']
        assert fb['status'] == 'observed' and abs(fb['floors']['height_m'] - 3.5) <= 0.25 and len(fb['evidence']) == 1, fb
        assert any('panorama' in u for u in fb['unresolved']) and res['cost']['requests'] == 0
        out['adapter'] = {'floors': fb['floors'], 'bays': fb['bays'], 'glazing': fb['glazing']}

    # alignment (reject when unsure): (a) true-scale corners of the lower 8 m of the 16 m frontage, face claimed 8 m tall -> the wall
    # continues above the measured top; (b) the 17 m photo registered onto an 8 m face (squeezed) -> the 2.6 m door reads 1.2 m tall
    lerp = lambda p, q, t: (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
    C8 = dict(C1, u1v1=lerp(C1['u1v0'], C1['u1v1'], 8 / 17), u0v1=lerp(C1['u0v0'], C1['u0v1'], 8 / 17))
    m8 = dict(meta, v_extent_m=[0, 8.0])
    fa, _ = analyze(np.asarray(img), {'corners_px': C8, 'evidence': ev1}, m8)
    assert fa['status'] == 'unresolved' and 'continues above the measured top' in ' '.join(fa['unresolved']), fa['unresolved']
    fs_, _ = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, m8)
    assert fs_['status'] == 'unresolved' and any('alignment' in u for u in fs_['unresolved']), fs_['unresolved']
    out['alignment'] = {'taller_frontage': fa['unresolved'][0], 'squeezed': next(u for u in fs_['unresolved'] if 'alignment' in u)}
    assert face.get('openings') and any(o['u0_m'] <= 13 and o['u1_m'] >= 22 for o in face['openings']), face.get('openings')   # storefront 12-23 kept whole

    # missing image -> unresolved
    f0, c0 = analyze(None, {'corners_px': C1}, meta)
    assert f0['status'] == 'unresolved' and f0['unresolved'] and c0['requests'] == 0 and not validate_face(f0, meta)

    # model output validation
    good = {'confidence': 0.7, 'zones': [{'v0_m': 0, 'v1_m': 17, 'u0_m': 0, 'u1_m': 24, 'role': 'wall', 'rgb': [200, 180, 150], 'conf': 0.8}],
            'glazing': None, 'floors': {'first_m': 4.5, 'height_m': 3.5, 'count': 4, 'conf': 0.6}, 'bays': None,
            'entrances': [], 'canopies': [], 'unresolved': [], 'absent': ['canopies']}
    assert parse_model_output(json.dumps(good), meta)[0] is not None
    for mut in (lambda d: d.update(extra=1), lambda d: d['zones'][0].update(rgb=[300, 0, 0]), lambda d: d['zones'][0].update(u1_m=99),
                lambda d: d['zones'][0].update(code='rm -rf'), lambda d: d.update(confidence=1.5), lambda d: d['floors'].update(count=True),
                lambda d: d.update(status='observed'), lambda d: d.pop('absent'), lambda d: d.update(absent=['floors']),
                lambda d: d.update(absent=['canopies', 'canopies']), lambda d: d.update(absent=['doors'])):
        d = json.loads(json.dumps(good)); mut(d)
        assert parse_model_output(json.dumps(d), meta)[0] is None, d
    assert parse_model_output('NaN', meta)[0] is None and parse_model_output('{"confidence": Infinity}', meta)[0] is None

    # anthropic via stub: cap blocks the call; a configured call records actual usage; bad JSON rejected; key never leaks
    key = 'sk-test-' + 'x' * 20
    os.environ['ANTHROPIC_API_KEY'] = key
    try:
        with tempfile.TemporaryDirectory() as td:
            reply = {'content': [{'type': 'text', 'text': json.dumps(good)}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 1500, 'output_tokens': 400}}
            stub = _Stub(reply)
            base = {'backend': 'anthropic', 'anthropic_url': stub.url, 'cache_dir': td, 'job_id': 'j1'}
            fc, cc = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(base, ai_max_usd_per_job=0.01, ai_max_usd_per_day=1.0))
            assert stub.hits == 0 and fc['status'] == 'unresolved' and 'cost cap' in fc['unresolved'][0] and cc['requests'] == 0, (fc, stub.hits)
            fn, _ = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(base))      # caps 0 = disabled
            assert stub.hits == 0 and 'not configured' in fn['unresolved'][0]
            fa, ca = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(base, ai_max_usd_per_job=1.0, ai_max_usd_per_day=1.0))
            assert stub.hits == 1 and fa['status'] == 'observed' and abs(ca['usd'] - (1500 * 4 + 400 * 20) / 1e6) < 1e-9, (fa, ca)
            assert stub.headers[0].get('x-api-key') == key
            led = _ledger_load(dict(cache_dir=td), datetime.date.today().isoformat())
            assert abs(led['jobs']['j1'] - ca['usd']) < 1e-9 and key not in json.dumps(led)
            stub.reply = dict(reply, content=[{'type': 'text', 'text': json.dumps(dict(good, extra_key=1))}])
            fb, cb = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(base, ai_max_usd_per_job=1.0, ai_max_usd_per_day=1.0))
            # invalid twice: exactly one repair retry (2 calls, both charged), then a clear failure, nothing kept
            assert fb['status'] == 'unresolved' and 'ANALYSIS FAILED' in fb['unresolved'][0] and cb['requests'] == 2 and not fb['zones'] and key not in json.dumps(fb), (fb, cb)
            assert stub.hits == 3 and b'REJECTED by the validator' in stub.bodies[-1] and b'REJECTED' not in stub.bodies[-2]
            # per-day cap: today's ledger + estimate over the cap blocks even a new job
            led = _ledger_load(dict(cache_dir=td), datetime.date.today().isoformat())
            fd, _ = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(base, job_id='j2', ai_max_usd_per_job=1.0, ai_max_usd_per_day=led['total_usd'] + 0.02))
            assert stub.hits == 3 and 'cost cap: day' in fd['unresolved'][0], fd['unresolved']
            stub.status = 500
            fe, ce = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(base, ai_max_usd_per_job=1.0, ai_max_usd_per_day=5.0))
            assert 'HTTP 500' in fe['unresolved'][0] and 'no fallback' in fe['unresolved'][0] and ce['requests'] == 1
            stub.close()
            # ollama via stub
            so = _Stub({'response': json.dumps(good)})
            fo, co = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, {'backend': 'ollama', 'ollama_url': so.url, 'ollama_model': 'test-vlm'})
            assert so.hits == 1 and fo['status'] == 'observed' and co['usd'] == 0 and co['requests'] == 1
            st = feature_states(fo)
            assert st == {'glazing': 'unknown', 'floors': 'present', 'bays': 'unknown', 'entrances': 'unknown', 'canopies': 'absent'}, st
            assert any(u.startswith('unknown') and 'glazing' in u and 'canopies' not in u for u in fo['unresolved']), fo['unresolved']
            sch = json.loads(so.bodies[-1])['format']
            assert sch['properties']['zones']['items']['properties']['u1_m']['maximum'] == 24.0 and sch['additionalProperties'] is False
            so.reply = [{'response': '{"confidence": 0.5, "zones": "wall", "__class__": 1}'}] * 2
            ocfg = {'backend': 'ollama', 'ollama_url': so.url, 'ollama_model': 'test-vlm'}
            fo2, co2 = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, ocfg)
            assert fo2['status'] == 'unresolved' and 'ANALYSIS FAILED' in fo2['unresolved'][0] and co2['requests'] == 2 and so.hits == 3
            # repaired on the retry: second reply valid -> accepted, labelled as repaired
            so.reply = [{'response': json.dumps(dict(good, absent=['floors']))}, {'response': json.dumps(good)}]
            fo3, co3 = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, ocfg)
            assert fo3['status'] == 'observed' and co3['requests'] == 2 and any('repaired on retry' in u for u in fo3['unresolved'])
            assert b'floors is listed absent but also described' in so.bodies[-1]
            # backend error on the retry -> clear failure, no partial result
            so.reply = [{'response': '{}'}]; so.status = 200
            def boom(*a, **k): raise BackendError('ollama request failed: ReadTimeout')
            real = _ollama
            globals()['_ollama'] = lambda *a: real(*a) if not a[-1] else boom()
            try: fo4, co4 = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, ocfg)
            finally: globals()['_ollama'] = real
            assert 'ANALYSIS FAILED: repair retry failed' in fo4['unresolved'][0] and not fo4['zones'] and co4['requests'] == 2, (fo4, co4)
            # per-job call budget: the retry would be call 2 of a budget of 1 -> refused before any request, clear failure
            so.reply = [{'response': '{}'}]; h0 = so.hits
            fo5, co5 = analyze(np.asarray(img), {'corners_px': C1, 'evidence': ev1}, meta, dict(ocfg, _call_budget=[1]))
            assert so.hits == h0 + 1 and co5['requests'] == 1 and 'budget' in fo5['unresolved'][0] and 'ANALYSIS FAILED' in fo5['unresolved'][0], (fo5, co5)
            assert json.loads(so.bodies[-1])['options']['num_ctx'] == 4096 and json.loads(so.bodies[-1])['keep_alive'] == '30s'
            so.close()
            fu, _ = analyze(np.asarray(img), {'corners_px': C1}, meta, {'backend': 'ollama'})       # not configured, no call
            assert 'not configured' in fu['unresolved'][0]
    finally:
        os.environ.pop('ANTHROPIC_API_KEY', None)
    if verbose: print(json.dumps(out, indent=1, default=str))
    print('facade_analyze selftest ok')
    return out


def run_refs(cases_path):
    """builtin-cv on local reference photos. cases.json: [{"image": path, "corners_px": {...}, "u_extent_m", "v_extent_m", "note"}]"""
    from PIL import Image
    import facade_materials as FM
    for case in json.load(open(cases_path, encoding='utf-8')):
        meta = {'face_id': case.get('name', 'ref'), 'u_extent_m': case['u_extent_m'], 'v_extent_m': case['v_extent_m'], 'parts': case.get('parts')}
        proj = {'corners_px': case['corners_px'], 'u_m': case.get('u_m'), 'v_m': case.get('v_m'), 'evidence': {'provider': 'local', 'image_id': os.path.basename(case['image'])}}
        img = Image.open(case['image']).convert('RGB')
        face, stats = _builtin([np.asarray(img)], [proj], meta, dict(DEFAULTS))
        pal = FM.palette_for_face(face) if face['status'] == 'observed' else None
        print(json.dumps({'case': meta['face_id'], 'note': case.get('note'), 'status': face['status'], 'confidence': face['confidence'],
                          'coverage': stats.get('coverage'), 'masks': stats.get('masks'), 'zones': [(z['role'], z['v0_m'], z['v1_m'], z['rgb'], z['conf']) for z in face['zones']],
                          'glazing': face['glazing'], 'floors': face['floors'], 'bays': face['bays'], 'entrances': face['entrances'],
                          'canopies': face['canopies'], 'unresolved': face['unresolved'], 'palette': pal and pal['roles']}, default=str))


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--refs': run_refs(sys.argv[2])
    else: selftest()
