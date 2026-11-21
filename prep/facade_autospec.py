"""facade_autospec.py - facade-autospec-1 (docs/facade/AUTO.md section 3): strict validation of analysis output (untrusted data) and
autospec_to_package(): accepted spec + geometry + per-face palettes + fallback style -> (selection, assets) for facade_compile.compile().
Pure and deterministic: the same accepted spec (and palettes) compile to identical ops. Owner: P."""
import bisect, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facade_common as F
import facade_schema as S
import facade_compile as C

SCHEMA = 'facade-autospec-1'
STATUSES = ('observed', 'procedural', 'unresolved')
MAX_EVIDENCE, MAX_ZONES, MAX_ENTRANCES, MAX_CANOPIES, MAX_UNRESOLVED, MAX_FACES = 16, 32, 16, 16, 32, 1024     # = facade_analyze caps
MAX_CANOPY_DEPTH = 3          # library canopies (canopy-flat-d1..d3); deeper observations are clamped and noted
EPS = 0.01
OBSERVED_CONF = 0.5           # an observed element below this confidence is labelled `inferred` (e.g. bays extended across an occlusion)
MIN_FACE_CONF = 0.3           # the compiler's MIN_CONFIDENCE: an observed face below it falls back (orchestrator) or stays unresolved
WALL_FILL_FRAC = 0.5          # a wall zone covering at least this share of the face height fills the whole wall plane
ZONE_MIN_WIDTH = 0.6          # zones narrower than this share of the face width are not placed (layers span the full face width)
ALLOWANCE = 'auto-evidence'   # protrusion allowance id for observed canopies
# fields that would move/stretch/resize the measured shell: always rejected (also caught as unknown keys; named for a clear message)
SHELL_FIELDS = {'base_y', 'top_y', 'height', 'height_m', 'length_u', 'length_m', 'polygon', 'polygon_mc', 'columns', 'segments', 'normal',
                'envelope', 'outward', 'depth_m', 'offset_y', 'scale', 'stretch', 'extend', 'resize', 'move', 'parts', 'roof', 'shell'}
ROLE_OF_ZONE = {'wall': 'wall', 'base': 'band', 'band': 'band', 'parapet': 'parapet', 'trim': 'trim'}
AUTO_COMPONENTS = ('window-1x2', 'window-2x2', 'window-2x3', 'window-3x3', 'window-ribbon', 'storefront-bay', 'door-single', 'door-double',
                   'sill', 'canopy-flat-d1', 'canopy-flat-d2', 'canopy-flat-d3')

C01 = ('num', 0, 1)
MODEL_BACKENDS = ('ollama', 'anthropic')
FEATURES = ('glazing', 'floors', 'bays', 'entrances', 'canopies')
RGB = ('list', ('int', 0, 255), 3)
OPT = lambda s: ('or', ('const', None), s)
EVIDENCE_ITEM = {'provider': 'str', 'image_id': 'str', 'captured_at': OPT('str'), 'dist_m': OPT(('num', 0, 1e4)),
                 'view_angle_deg': OPT(('num', 0, 90)), 'assoc_score': OPT(C01), 'attribution': OPT('str')}
ZONE = {'v0_m': ('num', 0, 1e4), 'v1_m': ('num', 0, 1e4), 'u0_m': ('num', 0, 1e6), 'u1_m': ('num', 0, 1e6),
        'role': ('enum',) + tuple(ROLE_OF_ZONE), 'rgb': RGB, 'conf': C01, 'rgb_camera?': RGB}   # rgb_camera: photo colour when rgb is a white-balanced estimate
FACE = {'status': ('enum',) + STATUSES, 'evidence': ('list', EVIDENCE_ITEM, MAX_EVIDENCE), 'confidence': C01,
        'zones': ('list', ZONE, MAX_ZONES),
        'glazing': ('or', ('const', None), {'fraction': C01, 'rgb': RGB, 'conf': C01, 'rgb_camera?': RGB}),
        'floors': ('or', ('const', None), {'first_m': ('num', 0, 1e3), 'height_m': ('num', 2, 12), 'count': ('int', 1, 200), 'conf': C01}),
        'bays': ('or', ('const', None), {'pitch_m': ('num', 0.5, 1e6), 'width_m': ('num', 0.3, 1e6), 'offset_m': ('num', 0, 1e6), 'conf': C01}),
        'entrances': ('list', {'u0_m': ('num', 0, 1e6), 'u1_m': ('num', 0, 1e6), 'conf': C01}, MAX_ENTRANCES),
        'canopies': ('list', {'u0_m': ('num', 0, 1e6), 'u1_m': ('num', 0, 1e6), 'v_m': ('num', 0, 1e4), 'depth': ('int', 1, 6), 'conf': C01}, MAX_CANOPIES),
        'unresolved': ('list', 'str', MAX_UNRESOLVED),
        'absent?': ('list', ('enum',) + FEATURES, len(FEATURES)),
        'assisted?': 'bool',                                       # image aligned by manual corners (not automatic)
        'openings?': ('list', {'u0_m': ('num', 0, 1e6), 'u1_m': ('num', 0, 1e6), 'v0_m': ('num', 0, 1e4), 'v1_m': ('num', 0, 1e4),
                               'kind': ('enum', 'glazing'), 'conf': C01}, 32),
        'patches?': ('list', {'u0_m': ('num', 0, 1e6), 'u1_m': ('num', 0, 1e6), 'v0_m': ('num', 0, 1e4), 'v1_m': ('num', 0, 1e4),
                              'role': ('enum',) + tuple(ROLE_OF_ZONE), 'rgb': RGB, 'conf': C01, 'block?': 'block'}, 64)}       # ASSISTED material zones (person-drawn)       # measured glazing rectangles (placed as seen)       # confirmed absent; null/[] and not listed = unknown
COST = {'usd': ('num', 0, 10000), 'requests': ('int', 0, 10 ** 6), 'by_provider': ('map', {'usd': ('num', 0, 10000), 'requests': ('int', 0, 10 ** 6)})}
SPEC = {'schema': ('const', SCHEMA), 'bid': 'str', 'members': ('list', 'str', 64), 'geometry_key': 'str', 'created': 'str',
        'analyzer': {'backend': 'id', 'version': 'str', 'model': ('or', ('const', None), 'str')},
        'mode': ('enum', 'evidence', 'procedural', 'mixed'), 'cost': COST}           # faces: validated per face against the geometry


def extent(fg):
    """(u extent m, v extent m) of a geometry face: measured length along u and rows base_y..top_y"""
    return max(float(fg.get('length_u') or 0), len(fg['columns'])), int(fg['top_y']) - int(fg['base_y']) + 1


def empty_face(status, unresolved=()):
    return dict(status=status, evidence=[], confidence=0.0, zones=[], glazing=None, floors=None, bays=None, entrances=[], canopies=[],
                unresolved=list(unresolved)[:MAX_UNRESOLVED])


def validate_face(f, fg, path='$'):
    """errors of one face section against its geometry face (extent, ranges, caps, consistency)"""
    errs = []
    if isinstance(f, dict):
        for k in f:
            if isinstance(k, str) and k.lower() in SHELL_FIELDS: errs.append(f'{path}.{k}: shell-changing field (the measured shell is never moved, stretched or resized)')
    S._data_only(f, path, errs)
    if errs: return errs
    S.chk(f, FACE, path, errs)
    if errs: return errs
    ab = f.get('absent') or []
    if len(set(ab)) != len(ab): errs.append(f'{path}.absent: duplicate feature')
    errs += [f'{path}.absent: {k} is listed absent but also described' for k in ab if f[k]]
    U, V = extent(fg)
    def urange(p, a, b):
        if not 0 <= a < b <= U + EPS: errs.append(f'{p}: u [{a}, {b}] outside the measured face extent [0, {U}] (or empty)')
    def vrange(p, a, b):
        if not 0 <= a < b <= V + EPS: errs.append(f'{p}: v [{a}, {b}] outside the measured face extent [0, {V}] (or empty)')
    for i, z in enumerate(f['zones']): urange(f'{path}.zones[{i}]', z['u0_m'], z['u1_m']); vrange(f'{path}.zones[{i}]', z['v0_m'], z['v1_m'])
    for i, e in enumerate(f['entrances']): urange(f'{path}.entrances[{i}]', e['u0_m'], e['u1_m'])
    for i, o in enumerate(f.get('openings') or []): urange(f'{path}.openings[{i}]', o['u0_m'], o['u1_m']); vrange(f'{path}.openings[{i}]', o['v0_m'], o['v1_m'])
    for i, o in enumerate(f.get('patches') or []): urange(f'{path}.patches[{i}]', o['u0_m'], o['u1_m']); vrange(f'{path}.patches[{i}]', o['v0_m'], o['v1_m'])
    for i, c in enumerate(f['canopies']):
        urange(f'{path}.canopies[{i}]', c['u0_m'], c['u1_m'])
        if c['v_m'] > V + EPS: errs.append(f"{path}.canopies[{i}].v_m: {c['v_m']} outside the measured face extent [0, {V})")
    fl = f['floors']
    if fl and (fl['first_m'] > V + EPS or fl['first_m'] + max(fl['count'] - 2, 0) * fl['height_m'] > V + EPS):     # every floor line inside the wall
        errs.append(f"{path}.floors: {fl['count']} floors (lines from {fl['first_m']} m every {fl['height_m']} m) exceed the measured height {V} m (never stretched)")
    b = f['bays']
    if b:
        if b['width_m'] > b['pitch_m']: errs.append(f'{path}.bays.width_m: wider than pitch_m')
        if b['pitch_m'] > U + EPS or b['offset_m'] > U + EPS: errs.append(f'{path}.bays: pitch/offset outside the measured face extent [0, {U}]')
    content = f.get('openings') or f.get('patches') or f['zones'] or f['glazing'] or fl or b or f['entrances'] or f['canopies'] or f['evidence']
    if f['status'] == 'observed' and not f['evidence']: errs.append(f'{path}.evidence: an observed face needs at least one evidence image')
    if f['status'] != 'observed' and content: errs.append(f"{path}: a {f['status']} face carries no observations (evidence, zones, glazing, floors, bays, entrances, canopies)")
    return errs


def validate(spec, geometry):
    """all errors of an autospec against its geometry ('<json path>: <message>'); [] = accepted"""
    errs = []
    if not isinstance(spec, dict): return ['$: expected object']
    S._data_only(spec, '$', errs)
    if errs: return errs
    S.chk({k: v for k, v in spec.items() if k != 'faces'}, SPEC, '$', errs)
    faces = spec.get('faces')
    if not isinstance(faces, dict): errs.append('$.faces: expected object'); return errs
    if len(faces) > MAX_FACES: errs.append(f'$.faces: too many faces ({len(faces)} > {MAX_FACES})')
    if errs: return errs
    if spec['bid'] != geometry['bid']: errs.append(f"$.bid: {spec['bid']!r} is not the geometry's building {geometry['bid']!r}")
    if spec['geometry_key'] != geometry['geometry_sha']: errs.append('$.geometry_key: does not match the measured geometry (geometry_sha)')
    gf = {f['id']: f for f in geometry['faces']}
    for fid, f in faces.items():
        if fid not in gf: errs.append(f'$.faces.{fid}: not a face of the measured geometry'); continue
        errs += validate_face(f, gf[fid], f'$.faces.{fid}')
    if not errs:
        st = [f['status'] for f in faces.values()]
        want = mode_of(st)
        if spec['mode'] != want: errs.append(f"$.mode: {spec['mode']!r} does not match the face statuses (expected {want!r})")
    return errs


def mode_of(statuses):
    o, p = 'observed' in statuses, 'procedural' in statuses
    return 'evidence' if o and not p else 'mixed' if o else 'procedural'


# ---------------------------------------------------------------- spec -> package
def _cls(conf): return 'observed' if conf >= OBSERVED_CONF else 'inferred'


def _cols_in(fc, u0, u1):
    """column indexes whose centre u lies in [u0, u1)"""
    us = [float(c[2]) for c in fc.cols]
    return list(range(bisect.bisect_left(us, u0), bisect.bisect_left(us, u1)))


def _palette_ok(role, block):
    p = F.parse_block(block) if isinstance(block, str) else None
    if not p or S.check_block(block) or '{' in block: return False
    ok, _ = S.PT().role_ok(role, p[0].split(':')[1]) if role in F.ROLES else (True, '')
    return ok


def face_palette(raw, notes):
    """V's {roles:{role:block}, zones:[block|None]} (or a bare {role: block}) -> (roles, zone blocks), every block checked (deny-list, role_ok)"""
    raw = raw or {}
    roles = raw.get('roles', raw) if isinstance(raw, dict) else {}
    roles = roles if isinstance(roles, dict) else {}
    out = {}
    for r, b in sorted(roles.items()):
        r2 = ROLE_OF_ZONE.get(r, r)
        if r2 not in F.ROLES or (r2 in out and r != r2): continue
        if _palette_ok(r2, b): out[r2] = b
        else: notes.append(f'materials: {b!r} refused for role {r2}')
    zones = raw.get('zones') if isinstance(raw, dict) and isinstance(raw.get('zones'), list) else []
    return out, zones


def _rename(x, prefix):
    """style layout -> package layout: component names get the style prefix"""
    if isinstance(x, dict):
        return {k: (prefix + v if k in ('component', 'below', 'above', 'replace') and isinstance(v, str) else _rename(v, prefix)) for k, v in x.items()}
    if isinstance(x, list): return [_rename(v, prefix) for v in x]
    return x


def _style_layout(sdoc, edge):
    """(layout | None, why | None, note | None): the compiler's style face rules (COMPILER-SPEC 'face selection')"""
    d = sdoc['defaults']; d = {'exterior': d} if 'floors' in d else d
    if edge in C.WALL_ONLY: return None, 'shared/party wall: the neighbour owns it (wall only, no ops)', None
    if edge == 'unknown':
        e = d.get('unknown')
        if e is None: return d['exterior'], None, 'unknown edge: the style has no `unknown` default, exterior layout applied'
        if 'floors' in e: return e, None, None
        return None, 'edge kind unknown and the style skips unknown edges', None
    return d['exterior'], None, None


def _observed_layout(f, fg, fc, pal_roles, zone_blocks, zid, zones_out, notes):
    # ASSISTED faces (manual corner alignment): what would be `observed` is `reference` - aligned by a person, not automatically
    _cls_ = (lambda conf: 'reference' if conf >= OBSERVED_CONF else 'inferred') if f.get('assisted') else _cls
    """an observed face section -> package LAYOUT with per-element evidence classes (observed | inferred)"""
    U, V = extent(fg); N, H = fc.N, fc.H
    lay = {'zone': zid, 'backing': 'auto'}
    fl, b, gz = f['floors'], f['bays'], f['glazing']
    if fl: lay['floors'] = dict(base_v=0, ground_height=max(2, min(16, round(fl['first_m']))), height=max(2, min(12, round(fl['height_m']))), count=fl['count'])
    else: lay['floors'] = dict(base_v=0, height=4, count='auto')
    width = max(1, min(32, round(b['pitch_m']))) if b else 4
    margin = max(0, min(16, round(b['offset_m']))) if b and b['offset_m'] < b['pitch_m'] else 0
    lay['bays'] = dict(width=width, residual='center', margin=margin)
    bl, _ = C.bays(N, width, 'center', margin)
    # ---- zones (coherent material layers across the face width)
    parapet = None; bands = []
    for k, z in sorted(enumerate(f['zones']), key=lambda kz: (kz[1]['v0_m'], kz[0])):
        if (z['u1_m'] - z['u0_m']) < ZONE_MIN_WIDTH * U: notes.append(f"zone {k} ({z['role']}) narrower than {int(ZONE_MIN_WIDTH * 100)}% of the face: not placed"); continue
        role = ROLE_OF_ZONE[z['role']]
        zb = zone_blocks[k] if k < len(zone_blocks) else None
        zb = zb.get('block') if isinstance(zb, dict) else zb                       # facade_materials: zone dicts with "block"
        blk = zb if zb and _palette_ok(role, zb) else pal_roles.get(role)
        if not blk: notes.append(f"zone {k} ({z['role']}): no material, not placed"); continue
        zk = f'{zid}z{k}'; zones_out[zk] = {role: blk}
        v0 = max(0, int(round(z['v0_m']))); h = max(1, int(round(z['v1_m'])) - v0)
        el = dict(role=role, zone=zk, evidence=_cls_(z['conf']))
        if role == 'wall' and h >= WALL_FILL_FRAC * H and 'wall' not in lay: lay['wall'] = dict(el, fill=True)
        elif role == 'parapet' and v0 + h >= H - 1 and parapet is None: parapet = dict(el, height=max(1, min(4, H - v0)))
        else: bands.append(dict(el, at=v0, height=min(h, 6)))
    if parapet: lay['parapet'] = parapet
    if bands: lay['bands'] = bands
    # ---- glazing: windows per bay on every floor (ground: storefront when the glazing is high and the floor tall enough)
    openings = []
    fls = C.floors(lay['floors'], H - (parapet['height'] if parapet else 0))
    rects = []
    for k, p in enumerate(f.get('patches') or []):   # ASSISTED material zones: placed where drawn, block matched to the sampled photo colour
        import facade_materials as M
        cols = _cols_in(fc, p['u0_m'], p['u1_m']); v0, v1 = int(round(p['v0_m'])), int(round(p['v1_m']))
        if not cols or v1 <= v0: notes.append(f'patch {k}: under one block after snapping, not placed'); continue
        role = ROLE_OF_ZONE[p['role']]
        if p.get('block') and _palette_ok(role, p['block']): blk = p['block']        # the person's choice wins over the photo colour
        else:
            if p.get('block'): notes.append(f"patch {k}: block {p['block']} not allowed for {role}, colour match used")
            blk = 'minecraft:' + M.match(p['rgb'], 'wall' if role == 'wall' else role, 1)[0][2]
        zk = f'{zid}p{k}'; zones_out[zk] = {role: blk}
        rects.append(dict(role=role, zone=zk, u0=cols[0], u1=cols[-1] + 1, v0=v0, v1=min(v1, H), evidence=_cls_(p['conf'])))
    if f.get('openings'):                  # measured glazing geometry wins: rectangles as seen (continuous storefronts stay continuous)
        for k, o in enumerate(f['openings']):
            cols = _cols_in(fc, o['u0_m'], o['u1_m']); v0, v1 = int(round(o['v0_m'])), int(round(o['v1_m']))
            if not cols or v1 <= v0: notes.append(f'opening {k}: under one block after snapping, not placed'); continue
            rects.insert(0, dict(role='glass', u0=cols[0], u1=cols[-1] + 1, v0=v0, v1=min(v1, H), evidence=_cls_(o['conf'])))   # glass first: equal layers, first wins
    elif gz and gz['fraction'] >= 0.1:     # a fraction says glass exists, not where: positions are never invented (no template windows)
        notes.append(f"glazing seen ({gz['fraction']:.0%} of the wall) but window positions not measured: not placed (unresolved)")
    if len(rects) > 64: notes.append(f'{len(rects) - 64} rectangles over the 64 limit: not placed')
    if rects: lay['rects'] = rects[:64]
    # ---- entrances: a door in the bay holding the entrance centre (replaces that bay's ground glazing)
    for k, e in enumerate(f['entrances']):
        cols = _cols_in(fc, e['u0_m'], e['u1_m'])
        if not cols or not bl: notes.append(f'entrance {k}: no wall column/bay there, not placed'); continue
        ic = cols[len(cols) // 2]; bi = next((j for j, (i0, wb) in enumerate(bl) if i0 <= ic < i0 + wb), None)
        if bi is None: notes.append(f'entrance {k}: in the bay margin, not placed'); continue
        comp = 'door-double' if len(cols) >= 2 and bl[bi][1] >= 2 else 'door-single'; cu = 2 if comp == 'door-double' else 1
        du = max(0, min(bl[bi][1] - cu, cols[0] - bl[bi][0]))           # aligned to the entrance start, kept inside its bay
        door = dict(component=comp, bays=[bi], floors=[0], u=du, v=0, evidence=_cls_(e['conf']))
        if openings and openings[0]['floors'] == 'ground': door['replace'] = openings[0]['component']
        openings.append(door)
    if openings: lay['openings'] = openings
    # ---- canopies (observed protrusions: their own allowance)
    can, prot = [], []
    for k, c in enumerate(f['canopies']):
        cols = _cols_in(fc, c['u0_m'], c['u1_m']); v = int(round(c['v_m']))
        if not cols or not 0 <= v <= H: notes.append(f'canopy {k}: outside the wall columns, not placed'); continue
        d = min(c['depth'], MAX_CANOPY_DEPTH)
        if d < c['depth']: notes.append(f"canopy {k}: depth {c['depth']} clamped to {d} (library)")
        can.append(dict(component=f'canopy-flat-d{d}', u=[cols[0], cols[-1] + 1], v=min(v, H - 1), evidence=_cls_(c['conf'])))
        if d > 1: prot.append(dict(face=fg['id'], u=[cols[0], cols[-1] + 1], depth=d, allowance=ALLOWANCE))
    if can: lay['canopy'] = can
    return lay, prot


def autospec_to_package(spec, geometry, palettes_by_face, fallback_style):
    """-> (selection, assets, notes) for facade_compile.compile(). fallback_style = style id (its palette is the base palette; it lays out
    procedural faces, labelled `style`) or None (procedural faces then get no ops). Raises ValueError when the spec is not accepted."""
    errs = validate(spec, geometry)
    if errs: raise ValueError('autospec rejected: ' + '; '.join(errs[:5]))
    style_id = fallback_style or 'summerlin-stucco-retail'
    ssel, sassets, serrs, _ = S.load_selection('style', style_id)
    if serrs:
        if fallback_style: raise ValueError(f'fallback style {style_id} invalid: ' + '; '.join(serrs[:3]))
        ssel, sassets = None, None
    sdoc = ssel['doc'] if ssel else None
    files = dict(sassets['files']) if sassets else {}
    comps = {('s-' + k): v for k, v in (sassets['components'] if sassets else {}).items()}
    for cid in AUTO_COMPONENTS:
        lp = S.lib_path('components', 'lib:' + cid)
        c, e = S.validate_component(lp)
        if e: raise ValueError(f'library component {cid} invalid: {e[:2]}')
        comps[cid] = c; files[S._rel(lp)] = F.sha_file(lp)
    base = sassets['palette'] if sassets else None
    if base is None: raise ValueError('no base palette (the fallback style is needed for its palette roles)')
    palette = dict(roles=dict(base['roles']), zones=dict(base.get('zones') or {}))
    if base.get('texture'): palette['texture'] = base['texture']
    faces, unresolved, prot, notes = [], [], [], {}
    # a vision-model analysis is never `observed`: its confidence is uncalibrated, so its faces are `inferred` (AUTO.md 6)
    face_cls = 'inferred' if spec['analyzer']['backend'] in MODEL_BACKENDS else 'observed'
    for k, fg in enumerate(geometry['faces']):
        fid = fg['id']; f = spec['faces'].get(fid) or empty_face('unresolved', ['not in the autospec'])
        fc = C.Face(fg); n = notes.setdefault(fid, [])
        if fg['edge'] in C.WALL_ONLY:
            unresolved.append(dict(part=fg['part'], face=fid, reason='shared/party wall: the neighbour owns it (no ops)')); continue
        if f['status'] == 'observed':
            zid = f'af{k}'; proles, zblocks = face_palette((palettes_by_face or {}).get(fid), n)
            if not proles: n.append('materials: none for this face, base palette used')
            palette['zones'][zid] = proles
            lay, pr = _observed_layout(f, fg, fc, proles, zblocks, zid, palette['zones'], n)
            prot += pr
            src = [f"{e['provider']}:{e['image_id']}" for e in f['evidence']]
            faces.append(dict(lay, face=fid, evidence={'class': 'reference' if f.get('assisted') else face_cls, 'sources': src, 'confidence': f['confidence'], 'coverage': 1.0}))
        elif f['status'] == 'procedural' and fallback_style and sdoc:
            lay, why, note = _style_layout(sdoc, fg['edge'])
            if why: unresolved.append(dict(part=fg['part'], face=fid, reason=why)); continue
            lay = _rename(lay, 's-')
            if note: n.append(note)
            ap = sdoc['applies_to']
            if fc.N < ap.get('min_face_len', 0):
                lay = {k2: v for k2, v in lay.items() if k2 not in ('openings', 'canopy', 'sign')}
                lay['ground'] = {k2: v for k2, v in (lay.get('ground') or {}).items() if k2 not in ('openings', 'canopy')}
                lay['upper'] = {k2: v for k2, v in (lay.get('upper') or {}).items() if k2 != 'openings'}
                n.append('face shorter than style min_face_len: no openings')
            par = lay.get('parapet')
            ph = (comps[par['component']]['size']['v'] if par.get('component') else int(par.get('height', 0))) if par else 0
            nfl = len(C.floors(lay['floors'], fc.H - ph))
            if ap.get('max_levels') and nfl > ap['max_levels']:
                unresolved.append(dict(part=fg['part'], face=fid, reason=f"{nfl} floors > style max_levels {ap['max_levels']}")); continue
            faces.append(dict(lay, face=fid, evidence={'class': 'style', 'sources': [f'style:{style_id}'], 'confidence': 1.0, 'coverage': 1.0}))
        else:
            why = '; '.join(f['unresolved'][:3]) or ('no observation and fallback none' if f['status'] != 'observed' else 'unresolved')
            unresolved.append(dict(part=fg['part'], face=fid, reason=why if f['status'] == 'unresolved' else 'procedural face without a fallback style: ' + why))
    pkg = {'schema': 'facade-package-1', 'generator_min': '0.6.0',
           'package': dict(id='auto', version='1.0.0', kind='reference', title=f"automatic facade {spec['bid']}", license='derived',
                           attribution='; '.join(sorted({e['attribution'] for f in spec['faces'].values() for e in f['evidence']}))[:2000]),
           'building': dict(osm=list(spec['members']) or [spec['bid']], parts=[], shell='existing'),
           'sources': [], 'faces': faces, 'unresolved': unresolved, 'palette': palette, 'components': comps,
           'envelope': dict(outward=1, inward_backing=2, protrusions=prot), 'limits': dict(max_ops=F.MAX_OPS_DEFAULT, max_extent_m=F.MAX_EXTENT_DEFAULT),
           'seed': 1}
    spec_sha = F.sha_obj(spec); pal_sha = F.sha_obj(palettes_by_face or {})
    db, src = S.blockdb()
    if src != 'fallback': files[S._rel(src)] = F.sha_file(src)
    files['autospec'] = spec_sha; files['palettes'] = pal_sha
    if fallback_style: files['fallback_style'] = style_id
    assets = dict(palette=palette, components=comps, files=files, blocks=db, blocks_source=S._rel(src) if src != 'fallback' else 'fallback')
    sel = dict(kind='auto', id='autospec-' + spec_sha[:12], version=spec['analyzer']['version'], sha256=spec_sha, inputs_sha=F.sha_obj(files), doc=pkg)
    return sel, assets, {k: v for k, v in notes.items() if v}


def compile_autospec(spec, geometry, palettes_by_face, fallback_style, seed=1):
    """accepted spec -> facade-plan-1 (deterministic), labelled: reasons say which faces are observed and which are STYLE-GENERATED"""
    sel, assets, notes = autospec_to_package(spec, geometry, palettes_by_face, fallback_style)
    return label(C.compile(geometry, sel, assets, seed), spec, sel, notes, fallback_style)


def label(plan, spec, sel, notes, fallback_style):
    """observed vs approximated labels on a compiled automatic plan (reasons first lines + plan['auto'] per-face summary)"""
    st = {fid: f['status'] for fid, f in spec['faces'].items()}
    ev = {f['id']: f['evidence'] for f in plan['faces']}
    obs = sorted(k for k, v in ev.items() if v == 'observed'); sty = sorted(k for k, v in ev.items() if v == 'style')
    inf = sorted(k for k, v in ev.items() if v == 'inferred' and st.get(k) == 'observed')
    asi = sorted(k for k, f in spec['faces'].items() if f.get('assisted') and ev.get(k) == 'reference')
    lead = [f"AUTO {spec['mode']}: {len(obs)} observed face(s), "
            + (f"{len(asi)} ASSISTED face(s) (manual corner alignment, not automatic), " if asi else '')
            + (f"{len(inf)} model-INFERRED face(s) ({spec['analyzer']['model']}, uncalibrated, not observed), " if inf else '')
            + f"{len(sty)} STYLE-GENERATED (not observed) face(s)"
            + (f' [style {fallback_style}]' if sty else '') + f", {len(plan['unresolved'])} unresolved"]
    if sty: lead.append(f"STYLE-GENERATED (not observed) on: {', '.join(sty)[:300]}")
    if notes: lead.append(('notes: ' + ' | '.join(f"{fid}: {'; '.join(notes[fid])}" for fid in sorted(notes)))[:400])
    plan['reasons'] = lead + plan['reasons']
    plan['auto'] = dict(mode=spec['mode'], autospec_sha=sel['sha256'], analyzer=spec['analyzer'], cost=spec['cost'], fallback_style=fallback_style,
                        faces={f['id']: dict(status=st.get(f['id'], 'unresolved'), plan_evidence=f['evidence'], ops_by_evidence=f['ops_by_evidence'],
                                             images=len((spec['faces'].get(f['id']) or {}).get('evidence') or []),
                                             confidence=(spec['faces'].get(f['id']) or {}).get('confidence', 0.0),
                                             features=_states(spec['faces'].get(f['id']))) for f in plan['faces']})
    return plan


def _states(f):
    """observed face -> {feature: present|absent|unknown}; non-observed faces have no feature claims"""
    if not f or f['status'] != 'observed': return None
    ab = set(f.get('absent') or [])
    return {k: 'present' if f[k] else 'absent' if k in ab else 'unknown' for k in FEATURES}
