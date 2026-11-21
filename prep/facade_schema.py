"""facade_schema.py - stdlib validator for Facade data files (ARCHITECTURE.md sections 5 and 8a; docs/facade/PACKAGE-SCHEMA.md).
validate_package(path) / validate_style(path) / validate_component(path) / validate_palette(path) -> (obj, errors).
Every error is '<json path>: <message>' (the path starts at '$' of the file named; library errors are prefixed by the referring path).
Packages, styles, palettes and components are DATA ONLY: forbidden keys (code, script, exec, eval, command, import, __*) and
non-JSON values (YAML dates, sets, binary, NaN) are rejected. load_selection() validates and resolves everything compile() needs. Owner: D1."""
import json, math, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facade_common as F

FORBIDDEN = {'code', 'script', 'exec', 'eval', 'command', 'import'}
ID = re.compile(r'^[a-z0-9][a-z0-9_.\-]{0,63}$')
SEMVER = re.compile(r'^\d+\.\d+\.\d+$')
DATE = re.compile(r'^\d{4}(-\d{2}(-\d{2})?)?$')
SHA = re.compile(r'^[0-9a-f]{64}$')
OSMID = re.compile(r'^[nwr]\d{1,12}$')
RULE = re.compile(r'^(rows|cols)%(\d+)==(\d+)$|^hash\(u,v,seed\)<(0(\.\d+)?|1(\.0+)?)$')
SAMPLE_PH = {'facing': 'north', 'facing_in': 'south', 'facing_left': 'west', 'facing_right': 'east', 'axis_u': 'x'}
GRAVITY = re.compile(r'(^|_)(sand|gravel|concrete_powder|anvil|dragon_egg|scaffolding|pointed_dripstone)$')
Y = ('int', -4096, 4096)

# ---------------- block names and states (prep/facade_assets/blocks-1.18.2.json, written by C; small fallback when missing)
_FALLBACK = {
    'minecraft:air': {}, 'minecraft:cave_air': {}, 'minecraft:stone': {}, 'minecraft:smooth_sandstone': {}, 'minecraft:sandstone': {},
    'minecraft:smooth_quartz': {}, 'minecraft:quartz_block': {}, 'minecraft:white_concrete': {}, 'minecraft:light_gray_concrete': {},
    'minecraft:gray_concrete': {}, 'minecraft:black_concrete': {}, 'minecraft:terracotta': {}, 'minecraft:white_terracotta': {},
    'minecraft:glass': {}, 'minecraft:gray_stained_glass': {}, 'minecraft:light_blue_stained_glass': {}, 'minecraft:black_stained_glass': {},
    'minecraft:polished_andesite': {}, 'minecraft:stone_bricks': {}, 'minecraft:bricks': {}, 'minecraft:oak_planks': {},
    'minecraft:sea_lantern': {}, 'minecraft:iron_block': {}, 'minecraft:gray_wool': {},
    'minecraft:cut_sandstone': {}, 'minecraft:white_concrete': {}, 'minecraft:light_gray_wool': {}, 'minecraft:orange_wool': {}, 'minecraft:sand': {},
    'minecraft:smooth_stone_slab': {'type': ['top', 'bottom', 'double'], 'waterlogged': ['true', 'false']},
    'minecraft:spruce_trapdoor': {'facing': ['north', 'east', 'south', 'west'], 'half': ['top', 'bottom'], 'open': ['true', 'false'],
                                  'powered': ['true', 'false'], 'waterlogged': ['true', 'false']},
    'minecraft:quartz_pillar': {'axis': ['x', 'y', 'z']},
    'minecraft:end_rod': {'facing': ['north', 'east', 'south', 'west', 'up', 'down']},
    'minecraft:smooth_quartz_slab': {'type': ['top', 'bottom', 'double'], 'waterlogged': ['true', 'false']},
    'minecraft:smooth_sandstone_slab': {'type': ['top', 'bottom', 'double'], 'waterlogged': ['true', 'false']},
    'minecraft:stone_brick_stairs': {'facing': ['north', 'east', 'south', 'west'], 'half': ['top', 'bottom'],
                                     'shape': ['straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'], 'waterlogged': ['true', 'false']},
    'minecraft:smooth_quartz_stairs': {'facing': ['north', 'east', 'south', 'west'], 'half': ['top', 'bottom'],
                                       'shape': ['straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'], 'waterlogged': ['true', 'false']},
    'minecraft:oak_door': {'facing': ['north', 'east', 'south', 'west'], 'half': ['upper', 'lower'], 'hinge': ['left', 'right'],
                           'open': ['true', 'false'], 'powered': ['true', 'false']},
    'minecraft:iron_door': {'facing': ['north', 'east', 'south', 'west'], 'half': ['upper', 'lower'], 'hinge': ['left', 'right'],
                            'open': ['true', 'false'], 'powered': ['true', 'false']},
    'minecraft:glass_pane': {k: ['true', 'false'] for k in ('north', 'east', 'south', 'west', 'waterlogged')},
    'minecraft:gray_stained_glass_pane': {k: ['true', 'false'] for k in ('north', 'east', 'south', 'west', 'waterlogged')},
    'minecraft:iron_bars': {k: ['true', 'false'] for k in ('north', 'east', 'south', 'west', 'waterlogged')},
    'minecraft:oak_fence': {k: ['true', 'false'] for k in ('north', 'east', 'south', 'west', 'waterlogged')},
    'minecraft:stone_brick_wall': dict({k: ['none', 'low', 'tall'] for k in ('north', 'east', 'south', 'west')}, up=['true', 'false'], waterlogged=['true', 'false']),
    'minecraft:oak_leaves': {'distance': [str(i) for i in range(1, 8)], 'persistent': ['true', 'false']},
}
_DB = {}
def blockdb():
    """{name: {prop: [values]}} from blocks-1.18.2.json (accepts {"blocks": ...} or a bare map, props as {p:[v]} or {"properties": {...}},
    or a list of {"name","properties"|"states"}); fallback list when the file is missing. Cached per path."""
    path = os.path.join(F.assets_dir(), 'blocks-1.18.2.json')
    if path in _DB: return _DB[path]
    db, src = dict(_FALLBACK), 'fallback'
    if os.path.exists(path):
        raw = json.load(open(path, encoding='utf-8')); raw = raw.get('blocks', raw) if isinstance(raw, dict) else raw
        items = raw.items() if isinstance(raw, dict) else ((b.get('name') or b.get('id'), b) for b in raw)
        db = {}
        for name, props in items:
            if not isinstance(name, str): continue
            if ':' not in name: name = 'minecraft:' + name
            if isinstance(props, dict): props = props.get('properties', props.get('states', props))
            if isinstance(props, list): props = {p['name']: p.get('values', []) for p in props if isinstance(p, dict) and 'name' in p}
            db[name] = {k: [str(x).lower() for x in v] for k, v in (props or {}).items() if isinstance(v, list)}
        src = path
    _DB[path] = (db, src)
    return _DB[path]


def check_block(s, ph=SAMPLE_PH):
    """None if s (placeholders filled with sample values) is a known 1.18.2 block with valid state names/values, else a message"""
    p = F.parse_block(F.fill(s, ph)) if isinstance(s, str) else None
    if not p: return f'malformed block string {s!r}'
    db, _ = blockdb(); name, st = p
    bad = denied(name)
    if bad: return bad
    if name not in db: return f'unknown block {name}'
    for k, v in st.items():
        if k not in db[name]: return f'{name} has no state {k!r}'
        if db[name][k] and v not in db[name][k]: return f'{name}[{k}] does not allow {v!r}'
    return None


HAZARD = re.compile(r'^(lava|water|bubble_column|fire|soul_fire|tnt|spawner|barrier|jigsaw|structure_block|structure_void|light|'
                    r'command_block|chain_command_block|repeating_command_block|end_portal|end_gateway|nether_portal|moving_piston|'
                    r'piston_head|void_air)$|_command_block$')
ALLOW_EXPLICIT = {'air', 'cave_air', 'end_rod'}      # inset-window air (eligible owned cells only) and the guide's end-rod divider


def denied(name):
    """None, or why `name` may never be planned (review M2): hazards (fluids, fire, TNT, spawners, command/structure/jigsaw, barrier),
    any block entity (blocks-1.18.2.json stateful.block-entity + C's BLOCK_ENTITY rule), gravity blocks, and everything C's
    facade_palette_tools.unsafe() refuses (unstable, light, excluded) except air/cave_air/end_rod"""
    n = name.split(':')[1]
    if HAZARD.search(n): return f'{name} is a hazardous block (denied)'
    if name in _block_entities() or PT().BLOCK_ENTITY.search(n): return f'{name} is a block entity (denied)'
    if GRAVITY.search(n): return f'{name} is a gravity block (denied)'
    if n not in ALLOW_EXPLICIT:
        why = PT().unsafe(n)
        if why: return f'{name} is refused ({why})'
    return None


_BE = {}
def _block_entities():
    path = os.path.join(F.assets_dir(), 'blocks-1.18.2.json')
    if path not in _BE:
        try: _BE[path] = set((json.load(open(path, encoding='utf-8')).get('stateful') or {}).get('block-entity') or [])
        except Exception: _BE[path] = set()
    return _BE[path]

# ---------------- generic walker
def _data_only(v, path, errs):
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str): errs.append(f'{path}: non-string key {k!r}'); continue
            kl = k.lower()
            if kl in FORBIDDEN or kl.startswith('__'): errs.append(f'{path}.{k}: forbidden key (packages are data only)'); continue
            _data_only(x, f'{path}.{k}', errs)
    elif isinstance(v, list):
        for i, x in enumerate(v): _data_only(x, f'{path}[{i}]', errs)
    elif isinstance(v, float):
        if not math.isfinite(v): errs.append(f'{path}: non-finite number')
    elif not (v is None or isinstance(v, (str, bool, int))):
        errs.append(f'{path}: non-JSON value of type {type(v).__name__} (quote YAML dates and avoid tags)')


def chk(v, s, path, errs):
    """mini schema: 'str'|'bool'|'id'|'ver'|'date'|'sha'|'osm'|'block'|'any'|('int',lo,hi)|('num',lo,hi)|('enum',..)|('const',x)
    |('list',spec,max)|('map',spec)|('or',..)|dict (keys ending '?' optional; unknown keys rejected)|callable(v,path,errs)"""
    n0 = len(errs)
    def bad(m): errs.append(f'{path}: {m}')
    if callable(s): return s(v, path, errs)
    if isinstance(s, dict):
        if not isinstance(v, dict): return bad(f'expected object, got {type(v).__name__}')
        keys = {k.rstrip('?'): k.endswith('?') for k in s}
        for k, opt in keys.items():
            if k not in v and not opt: bad(f'missing required key {k!r}')
        for k, x in v.items():
            if k not in keys: errs.append(f'{path}.{k}: unknown key'); continue
            chk(x, s[k + '?' if keys[k] else k], f'{path}.{k}', errs)
        return
    if isinstance(s, str):
        if s == 'any': return
        if s == 'bool': return None if isinstance(v, bool) else bad('expected boolean')
        if not isinstance(v, str): return bad(f'expected string, got {type(v).__name__}')
        if len(v) > 4000: return bad('string too long (max 4000)')
        rx = dict(id=ID, ver=SEMVER, date=DATE, sha=SHA, osm=OSMID).get(s)
        if rx and not rx.match(v): return bad(f'not a valid {s}: {v!r}')
        if s == 'block':
            m = check_block(v)
            if m: bad(m)
        return
    t = s[0]
    if t in ('int', 'num'):
        if isinstance(v, bool) or not isinstance(v, (int, float) if t == 'num' else int): return bad(f'expected {"integer" if t == "int" else "number"}')
        if not s[1] <= v <= s[2]: bad(f'{v} outside [{s[1]}, {s[2]}]')
    elif t == 'enum':
        if v not in s[1:]: bad(f'{v!r} not one of {list(s[1:])}')
    elif t == 'const':
        if v != s[1]: bad(f'expected {s[1]!r}')
    elif t == 'list':
        if not isinstance(v, list): return bad('expected list')
        if len(s) > 2 and len(v) > s[2]: bad(f'too many items ({len(v)} > {s[2]})')
        for i, x in enumerate(v): chk(x, s[1], f'{path}[{i}]', errs)
    elif t == 'map':
        if not isinstance(v, dict): return bad('expected object')
        for k, x in v.items():
            if not ID.match(k): errs.append(f'{path}.{k}: key is not a valid id')
            chk(x, s[1], f'{path}.{k}', errs)
    elif t == 'or':
        for alt in s[1:]:
            e = []; chk(v, alt, path, e)
            if not e: return
        same = [a for a in s[1:] if isinstance(a, dict) == isinstance(v, dict) and (isinstance(a, tuple) and a[0] == 'list') == isinstance(v, list)]
        e = []; chk(v, (same or s[1:])[-1] if isinstance(v, dict) else (same or s[1:])[0], path, e)
        errs.extend(e or [f'{path}: no alternative matched'])
    else: raise ValueError(s)


def _libref(v, path, errs):
    if not (isinstance(v, str) and v.startswith('lib:') and ID.match(v[4:])): errs.append(f'{path}: expected "lib:<id>"')

def _sel(v, path, errs):
    if isinstance(v, int) and not isinstance(v, bool): return
    if isinstance(v, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in v): return
    if not (isinstance(v, str) and selector_ok(v)): errs.append(f'{path}: bad selector {v!r} (all|ground|upper|top|first|last|center|odd|even|i|i:j|every n)')

def selector_ok(s):
    return bool(re.match(r'^(all|ground|upper|top|first|last|center|odd|even|-?\d+|-?\d*:-?\d*|every [1-9]\d*( from \d+)?)$', s.strip()))

def select(s, n):
    """selector -> sorted index list over range(n) (deterministic; see COMPILER-SPEC)"""
    if isinstance(s, bool): return []
    if isinstance(s, int): return [s % n] if -n <= s < n else []
    if isinstance(s, list): return sorted({i % n for i in s if -n <= i < n})
    s = s.strip(); r = list(range(n))
    if s == 'all': return r
    if s in ('ground', 'first'): return r[:1]
    if s in ('top', 'last'): return r[-1:]
    if s == 'center': return r[(n - 1) // 2:(n - 1) // 2 + 1]
    if s == 'upper': return r[1:]
    if s == 'odd': return r[1::2]
    if s == 'even': return r[0::2]
    m = re.match(r'^every (\d+)(?: from (\d+))?$', s)
    if m: return r[int(m.group(2) or 0)::int(m.group(1))]
    if ':' in s:
        a, b = s.split(':'); return r[int(a) if a else None:int(b) if b else None]
    i = int(s); return [i % n] if -n <= i < n else []


ROLE_OR_BLOCK = {'role?': 'id', 'block?': 'block', 'component?': 'id'}
OPENING = {'component': 'id', 'bays?': _sel, 'floors?': _sel, 'v?': ('int', 0, 16), 'u?': ('int', 0, 31), 'u_inset?': ('int', 0, 8),
           'align?': ('enum', 'center', 'left', 'right'), 'below?': 'id', 'above?': 'id', 'replace?': 'id', 'backing?': ('enum', 'auto', 'none'),
           'notes?': 'str'}
CANOPY = dict(ROLE_OR_BLOCK, v=('int', 0, 64), **{'depth?': ('int', 1, 6), 'u?': ('list', ('int', 0, 100000), 2), 'bays?': _sel, 'notes?': 'str'})
SIGN = {'component': 'id', 'text': ('or', ('const', None), 'str'), 'v?': ('int', 0, 512), 'u?': ('int', 0, 100000), 'note?': 'str', 'notes?': 'str'}
PIERS = {'component': 'id', 'at': ('const', 'bay_edges')}
ZONE = {'openings?': ('list', OPENING, 64), 'canopy?': ('or', CANOPY, ('list', CANOPY, 16)), 'piers?': PIERS, 'fill?': 'id',
        'top?': {'component': 'id'}, 'sign?': SIGN, 'notes?': 'str'}


def _at(v, path, errs):
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 512: return
    if isinstance(v, str) and re.match(r'^(floors|floor_lines|top|v\d{1,3})$', v): return
    errs.append(f'{path}: band "at" must be floors|floor_lines|top|v<n>|<int>')


def _glyphs(v, p, e):
    if not (isinstance(v, dict) and all(isinstance(k, str) and len(k) == 1 and isinstance(r, list) and all(isinstance(x, str) and set(x) <= {'0', '1'} for x in r)
                                        for k, r in v.items())):
        e.append(f'{p}: glyphs must map one character to rows of "0"/"1" strings')


def _rule_ok(v, p, e):
    if not (isinstance(v, str) and RULE.match(v.replace(' ', ''))): e.append(f'{p}: rule not in whitelist (rows%k==r, cols%k==r, hash(u,v,seed)<p)')


def _label(v, p, e):
    if not (isinstance(v, str) and 'STYLE-GENERATED' in v): e.append(f'{p}: must contain "STYLE-GENERATED"')


def _urange(v, p, e):
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 100000 for x in v) and v[0] <= v[1]):
        e.append(f'{p}: expected [u0, u1] with 0 <= u0 <= u1 (column indexes, inclusive)')


EV3 = ('enum', 'reference', 'measured', 'inferred')
def _extra(v, p, e):
    """[x, y, z, block, role, evidence]: an explicit reference-matched cell (data, never code)"""
    if not (isinstance(v, list) and len(v) == 6): return e.append(f'{p}: expected [x, y, z, block, role, evidence]')
    for k in range(3): chk(v[k], ('int', -30000000, 30000000) if k != 1 else Y, f'{p}[{k}]', e)
    chk(v[3], 'block', f'{p}[3]', e); chk(v[4], ('enum',) + F.ROLES, f'{p}[4]', e); chk(v[5], EV3, f'{p}[5]', e)
    if isinstance(v[3], str) and '{' in v[3]: e.append(f'{p}[3]: placeholders are not allowed in extras')


ROOF_PART = {'role?': ('enum',) + F.ROLES, 'block?': 'block', 'props?': ('map', 'str'), 'rows?': ('enum', 'top', 'top+1'), 'evidence?': EV3, 'notes?': 'str'}
LAYOUT = {
    'zone?': 'id',
    'backing?': ('enum', 'auto', 'none'), 'backing_exclude_u?': ('list', _urange, 64),
    'wall?': {'fill': 'bool', 'role?': 'id', 'block?': 'block'},
    'floors': {'base_v': ('int', 0, 64), 'height': ('int', 2, 12), 'ground_height?': ('int', 2, 16), 'count': ('or', ('int', 1, 200), ('const', 'auto'))},
    'bays': {'width': ('int', 1, 32), 'residual': ('enum', 'center', 'ends', 'absorb'), 'margin?': ('int', 0, 16)},
    'openings?': ('list', OPENING, 64),
    'rects?': ('list', dict(ROLE_OR_BLOCK, u0=('int', 0, 100000), u1=('int', 1, 100000), v0=('int', 0, 512), v1=('int', 1, 512),
                            **{'evidence?': ('enum', 'observed', 'reference', 'measured', 'inferred'), 'zone?': 'id'}), 64),
    'ground?': ZONE, 'upper?': ZONE,
    'bands?': ('list', dict(ROLE_OR_BLOCK, at=_at, **{'w?': ('int', 0, 1), 'height?': ('int', 1, 3)}), 16),
    'corners?': dict(ROLE_OR_BLOCK, **{'width?': ('int', 1, 3), 'floors?': _sel}),
    'parapet?': dict(ROLE_OR_BLOCK, **{'height?': ('int', 0, 4), 'cap?': 'block'}),
    'canopy?': ('or', CANOPY, ('list', CANOPY, 16)),
    'roof_edge?': dict(ROLE_OR_BLOCK),
    'sign?': SIGN,
    'notes?': 'str',
}
FACE = dict(LAYOUT, face='str', evidence={'class': ('enum', 'reference', 'measured', 'inferred'), 'sources': ('list', 'id'),
                                         'confidence': ('num', 0, 1), 'coverage': ('num', 0, 1), 'notes?': 'str'})
CELL = {'u': ('int', 0, 31), 'v': ('int', 0, 31), 'w': ('int', -2, 4), 'role?': ('enum',) + F.ROLES, 'block?': 'block',
        'props?': ('map', 'str'), 'notes?': 'str'}
COMPONENT = {'schema': ('const', 'facade-component-1'), 'id': 'id', 'version': 'ver', 'title?': 'str', 'size': {'u': ('int', 1, 32), 'v': ('int', 1, 32)},
             'repeat_u?': 'bool', 'repeat_v?': 'bool', 'widths?': ('list', ('int', 1, 32), 32), 'cells': ('list', CELL, 4096),
             'glyph_size?': {'u': ('int', 1, 8), 'v': ('int', 1, 8)}, 'spacing?': ('int', 0, 4), 'role?': ('enum',) + F.ROLES, 'w?': ('int', -1, 1),
             'label?': 'str', 'glyphs?': _glyphs,
             'notes?': 'str', 'license?': 'str', 'attribution?': 'str', 'sources?': ('list', 'any', 32), 'tags?': ('list', 'str', 32)}
BLOCKS = ('or', 'block', ('list', 'block', 16))
PALETTE = {'schema': ('const', 'facade-palette-1'), 'id': 'id', 'version': 'ver', 'title?': 'str', 'status?': 'str', 'roles': ('map', BLOCKS),
           'texture?': ('map', {'alt': 'block', 'rule': _rule_ok}),
           'zones?': ('map', ('map', BLOCKS)), 'notes?': 'str', 'colour_refs?': ('list', 'any', 256), 'hueblocks?': 'any',
           'source_job?': 'str', 'job_palette_map?': 'any',
           'license?': 'str', 'attribution?': 'str', 'sources?': ('list', 'any', 64), 'tags?': ('list', 'str', 32)}
INLINE_COMPONENT = {k: v for k, v in COMPONENT.items() if k not in ('schema', 'id', 'version')}
COMPONENTS = ('map', ('or', {'ref': _libref}, INLINE_COMPONENT))
LIMITS = {'max_ops': ('int', 1, F.MAX_OPS_EXCEPTION), 'max_extent_m': ('num', 1, F.MAX_EXTENT_EXCEPTION),
          'exception?': ('or', ('const', None), {'reason': 'str', 'by': 'str', 'date': 'date'})}
SOURCE = {'id': 'id', 'kind': ('enum', 'street', 'aerial', 'ortho', 'lidar', 'osm', 'doc', 'site-photo'), 'date': 'date', 'url?': 'str',
          'local?': 'str', 'sha256?': 'sha', 'license': 'str', 'attribution': 'str', 'coverage': ('list', 'str'), 'notes?': 'str'}
PACKAGE = {
    'schema': ('const', 'facade-package-1'), 'generator_min': 'ver',
    'package': {'id': 'id', 'version': 'ver', 'kind': ('enum', 'reference', 'style'), 'title': 'str', 'license': 'str', 'attribution': 'str', 'notes?': 'str'},
    'building': {'osm': ('list', 'osm', 64), 'shell?': ('enum', 'existing', 'none'),
                 'parts': ('list', ('or', 'id', {'id': 'id', 'osm?': 'osm', 'polygon_mc?': ('list', ('list', ('num', -3e7, 3e7), 2), 4096),
                                                 'base_y?': Y, 'top_y?': Y, 'source?': 'id', 'notes?': 'str'}), 256)},
    'crs': {'projection': ('const', 'bte-tpm-geo.py'), 'units': ('const', 'block=1m'), 'y': ('const', 'world')},
    'sources': ('list', SOURCE, 256),
    'elevations': {'measured': ('map', 'any'), 'accepted': ('map', {'base_y': Y, 'top_y': Y, 'source': 'str'})},
    'palette': ('or', _libref, {k: v for k, v in PALETTE.items() if k not in ('schema', 'id', 'version')}),
    'components': COMPONENTS,
    'faces': ('list', FACE, 1024),
    'unresolved': ('list', {'part?': 'str', 'face': 'str', 'reason': 'str'}, 1024),
    'envelope': {'outward': ('int', 0, 4), 'inward_backing': ('int', 0, 2),
                 'protrusions': ('list', {'face': 'str', 'u': ('list', ('num', 0, 100000), 2), 'depth': ('int', 1, 6), 'allowance': 'id', 'notes?': 'str'}, 256)},
    'limits': LIMITS, 'seed': ('int', 0, 2 ** 31 - 1), 'notes?': 'str',
    'roofs?': ('list', {'part': 'str', 'cover?': ROOF_PART, 'edge?': ROOF_PART, 'notes?': 'str'}, 64),
    'extras?': ('list', _extra, 256),
}
STYLE = {
    'schema': ('const', 'facade-style-1'), 'id': 'id', 'version': 'ver', 'kind': ('const', 'style'), 'title?': 'str', 'license?': 'str',
    'label': _label, 'evidence?': {'class': ('const', 'style')}, 'attribution?': 'str', 'notes?': 'str', 'generator_min?': 'ver',
    'applies_to': {'building?': ('list', 'str', 64), 'max_levels?': ('int', 1, 200), 'min_face_len?': ('int', 0, 1000), 'notes?': 'str'},
    'palette': PACKAGE['palette'], 'components': COMPONENTS,
    'defaults': ('or', LAYOUT, {'exterior': LAYOUT, 'shared?': ('or', {'wall_only': ('const', True)}, LAYOUT),
                               'unknown?': ('or', {'skip': ('const', True)}, LAYOUT)}),
    'envelope?': {'outward': ('int', 0, 1), 'inward_backing': ('int', 0, 2)},
    'limits?': {'max_ops?': ('int', 1, F.MAX_OPS_DEFAULT), 'max_extent_m?': ('num', 1, F.MAX_EXTENT_DEFAULT), 'note?': 'str'},
    'seed?': ('int', 0, 2 ** 31 - 1),
}

# ---------------- loading
def _no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d: raise ValueError(f'duplicate key {k!r}')
        d[k] = v
    return d

def load(path, max_bytes):
    """(obj, errors): size limit, JSON (no duplicate keys / NaN) or YAML via yaml.safe_load, then the data-only walk"""
    try: n = os.path.getsize(path)
    except OSError as e: return None, [f'$: cannot read {path}: {e}']
    if n > max_bytes: return None, [f'$: file is {n} bytes > limit {max_bytes}']
    try:
        if path.lower().endswith(('.yaml', '.yml')):
            obj = _yaml(open(path, encoding='utf-8').read())
        else:
            obj = json.load(open(path, encoding='utf-8'), object_pairs_hook=_no_dupes, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f'{c} not allowed')))
    except Exception as e: return None, [f'$: parse error: {type(e).__name__}: {e}'[:300]]
    errs = []; _data_only(obj, '$', errs)
    return obj, errs


YAML_MAX_NODES = 200000
def _yaml(text):
    """yaml.safe_load without anchors/aliases (review M7: alias bombs) and with a node-count cap"""
    import yaml
    class L(yaml.SafeLoader):
        n = 0
        def compose_node(self, parent, index):
            ev = self.peek_event()
            if isinstance(ev, yaml.AliasEvent) or getattr(ev, 'anchor', None): raise ValueError('YAML anchors/aliases are not allowed')
            L.n += 1
            if L.n > YAML_MAX_NODES: raise ValueError(f'YAML has more than {YAML_MAX_NODES} nodes')
            return super().compose_node(parent, index)
    ld = L(text)
    try: return ld.get_single_data()
    finally: ld.dispose()


def lib_path(kind, ref):
    return os.path.join(F.assets_dir(), kind, ref[4:] + '.json')


def validate_palette(path):
    """library palette: schema, every plan role present, and every role/zone/texture block allowed for its role (C's role_ok, so this
    agrees with `facade_palette_tools.py check`)"""
    obj, errs = load(path, F.COMPONENT_MAX_BYTES)
    if obj is not None and not errs:
        chk(obj, PALETTE, '$', errs); _id_matches(obj, path, errs)
        if not errs:
            missing = sorted(set(F.ROLES) - set(obj['roles']))
            if missing: errs.append(f'$.roles: missing plan roles {missing}')
            _palette_roles(obj, '$', errs)
    return obj, errs


def _palette_roles(pal, path, errs):
    layers = [('roles', pal['roles'])] + [(f'zones.{z}', r) for z, r in (pal.get('zones') or {}).items()]
    layers += [(f'texture.{r}', {r: t['alt']}) for r, t in (pal.get('texture') or {}).items()]
    for lname, roles in layers:
        for role, b in roles.items():
            for k, x in enumerate(b if isinstance(b, list) else [b]):
                if role not in F.ROLES: errs.append(f'{path}.{lname}.{role}: not a plan role {list(F.ROLES)}'); break
                ok, why = PT().role_ok(role, F.parse_block(x)[0].split(':')[1]) if F.parse_block(x) else (True, '')
                if not ok: errs.append(f'{path}.{lname}.{role}' + (f'[{k}]' if isinstance(b, list) else '') + f': {x} not allowed for {role} ({why})')


def PT():
    import facade_palette_tools
    return facade_palette_tools


def validate_component(path):
    obj, errs = load(path, F.COMPONENT_MAX_BYTES)
    if obj is not None and not errs: chk(obj, COMPONENT, '$', errs); _id_matches(obj, path, errs); _component_rules(obj, '$', errs)
    return obj, errs


def _id_matches(obj, path, errs):
    if isinstance(obj, dict) and obj.get('id') and os.path.splitext(os.path.basename(path))[0] != obj['id']:
        errs.append(f"$.id: {obj['id']!r} does not match file name {os.path.basename(path)!r}")


def _component_rules(c, path, errs):
    """cells unique, role or block on each, doors complete (lower + upper above), stairs carry half+facing, panes carry no connections"""
    seen = set(); size = c.get('size') or {}
    def door(x):
        """(is_door, half) for a cell given as an explicit door block or as role door + props"""
        b = F.parse_block(F.fill(x.get('block', ''), SAMPLE_PH)) if x.get('block') else None
        if b: return b[0].endswith('_door'), b[1].get('half')
        return x.get('role') == 'door', (x.get('props') or {}).get('half')
    for i, cell in enumerate(c.get('cells') or []):
        p = f'{path}.cells[{i}]'; key = (cell.get('u'), cell.get('v'), cell.get('w'))
        if key in seen: errs.append(f'{p}: duplicate cell {key}')
        seen.add(key)
        if ('role' in cell) == ('block' in cell): errs.append(f'{p}: needs exactly one of role/block')
        if 'block' in cell and 'props' in cell: errs.append(f'{p}: props apply to role cells only')
        if size and not (0 <= cell.get('u', 0) < size.get('u', 99) and 0 <= cell.get('v', 0) < size.get('v', 99)): errs.append(f'{p}: cell outside size {size}')
        isd, half = door(cell)
        if isd:
            other = {'lower': 1, 'upper': -1}.get(half)
            if other is None: errs.append(f'{p}: door needs half=lower|upper'); continue
            o = [x for x in c['cells'] if (x.get('u'), x.get('v'), x.get('w')) == (key[0], key[1] + other, key[2])]
            if not (o and door(o[0]) == (True, 'upper' if other == 1 else 'lower')):
                errs.append(f'{p}: door half={half} has no matching {"upper" if other == 1 else "lower"} half (doors must be complete)')
        conn = set((cell.get('props') or {})) & {'north', 'east', 'south', 'west', 'up'}
        b = F.parse_block(F.fill(cell.get('block', ''), SAMPLE_PH)) if cell.get('block') else None
        if b and b[0].endswith('_stairs') and not {'facing', 'half'} <= set(b[1]): errs.append(f'{p}: stairs need explicit facing and half')
        if (b and CONNECTS(b[0]) and set(b[1]) & {'north', 'east', 'south', 'west', 'up'}) or conn:
            errs.append(f'{p}: pane/fence/wall connections are computed by the compiler; remove north/east/south/west/up')
    if c.get('glyphs') is not None and c.get('glyph_size'):
        for ch, rows in c['glyphs'].items():
            if len(rows) != c['glyph_size']['v'] or any(len(r) != c['glyph_size']['u'] for r in rows): errs.append(f'{path}.glyphs.{ch}: malformed for glyph_size')


def CONNECTS(name):
    n = name.split(':')[1]
    return n.endswith('_pane') or n == 'iron_bars' or n.endswith('_fence') or (n.endswith('_wall') and not n.endswith('_wall_sign') and 'banner' not in n and 'torch' not in n and 'head' not in n and 'skull' not in n and 'fan' not in n)


def _resolve_libs(obj, errs, files, base='$'):
    """palette and component refs -> (palette, {name: component}); records file shas"""
    pal = obj.get('palette')
    if isinstance(pal, str):
        lp = lib_path('palettes', pal)
        if not os.path.exists(lp): errs.append(f'{base}.palette: {pal} does not resolve ({lp} missing)'); pal = None
        else:
            pal, e = validate_palette(lp); errs.extend(f'{base}.palette ({lp}) {x}' for x in e); files[_rel(lp)] = F.sha_file(lp)
    comps = {}
    for name, c in (obj.get('components') or {}).items():
        if isinstance(c, dict) and 'ref' in c:
            lp = lib_path('components', c['ref'])
            if not os.path.exists(lp): errs.append(f'{base}.components.{name}: {c["ref"]} does not resolve ({lp} missing)'); continue
            cc, e = validate_component(lp); errs.extend(f'{base}.components.{name} ({lp}) {x}' for x in e); files[_rel(lp)] = F.sha_file(lp)
            comps[name] = cc
        elif isinstance(c, dict):
            _component_rules(c, f'{base}.components.{name}', errs); comps[name] = dict(c, id=name)
    return pal, comps


def _rel(p):
    try: return os.path.relpath(p, os.path.dirname(F.HERE)).replace('\\', '/')
    except ValueError: return p.replace('\\', '/')


def _check_layout(lay, path, comps, pal, errs):
    """ids that resolve: opening components, roles in the palette (backing falls back to wall)"""
    roles = set((pal or {}).get('roles') or {})
    if lay.get('zone') and lay['zone'] not in ((pal or {}).get('zones') or {}): errs.append(f'{path}.zone: {lay["zone"]!r} not in palette zones')
    def walk(x, p):
        if isinstance(x, dict):
            for k, v in x.items():
                q = f'{p}.{k}'
                if k in ('component', 'below', 'above', 'replace') and isinstance(v, str) and v not in comps:
                    errs.append(f'{q}: {v!r} not in components {sorted(comps)}')
                elif k in ('role', 'fill') and isinstance(v, str) and v not in roles and not (v == 'backing' and 'wall' in roles):
                    errs.append(f'{q}: role {v!r} not in palette roles {sorted(roles)}')
                elif k == 'sign' and isinstance(v, dict) and v.get('component') in comps and v.get('text'):
                    g = comps[v['component']].get('glyphs')
                    if not g: errs.append(f'{q}.component: {v["component"]!r} has no glyph table')
                    else:
                        bad = sorted({ch for ch in str(v['text']).upper() if ch not in g})
                        if bad: errs.append(f'{q}.text: characters {bad} not in the glyph table')
                walk(v, q)
        elif isinstance(x, list):
            for i, v in enumerate(x): walk(v, f'{p}[{i}]')
    walk(lay, path)


def _comp_under_palette(pal, comps, errs, base='$'):
    """every component cell resolves (PT.resolve_cell semantics) to a valid 1.18.2 state under every palette layer (roles, each zone)"""
    if not pal: return
    db, _ = blockdb(); P = PT()
    layers = [('roles', pal['roles'])] + [(f'zones.{z}', {**pal['roles'], **r}) for z, r in (pal.get('zones') or {}).items()]
    for lname, roles in layers:
        roles = {k: (v[0] if isinstance(v, list) else v) for k, v in roles.items()}
        roles.setdefault('backing', roles.get('wall'))
        for name, c in comps.items():
            for j, cell in enumerate(c.get('cells') or []):
                if cell.get('role') and cell['role'] not in roles: errs.append(f'{base}.components.{name}.cells[{j}].role: {cell["role"]!r} not in palette'); continue
                try: b = P.resolve_cell(cell, roles, db)
                except Exception as e: errs.append(f'{base}.components.{name}.cells[{j}]: {e}'); continue
                m = check_block(b)
                if m: errs.append(f'{base}.components.{name}.cells[{j}] under palette {lname}: {m}')


def _contained(rel, pkg_dir, pkg_id):
    """a local reference must be a plain relative name inside the package dir or <refs>/<package-id>/ (no drive, no '..', no symlink escape)"""
    if not rel or os.path.isabs(rel) or re.match(r'^[a-zA-Z]:', rel) or rel.startswith(('\\', '/')): return None
    if '..' in re.split(r'[\\/]', rel): return None
    for root in (pkg_dir, os.path.join(F.refs_dir(), pkg_id)):
        r = os.path.realpath(root); p = os.path.realpath(os.path.join(root, rel))
        if p.startswith(r + os.sep) and os.path.exists(p): return p
    return ''         # contained but not present locally (refs are not distributed)


def _limits(lim, path, errs, style=False):
    if not isinstance(lim, dict): return
    exc = lim.get('exception')
    raised = lim.get('max_ops', 0) > F.MAX_OPS_DEFAULT or lim.get('max_extent_m', 0) > F.MAX_EXTENT_DEFAULT
    if style and (raised or exc): errs.append(f'{path}: styles cannot raise limits (max_ops <= {F.MAX_OPS_DEFAULT}, max_extent_m <= {F.MAX_EXTENT_DEFAULT}, no exception)')
    elif raised and not (isinstance(exc, dict) and len(str(exc.get('reason', '')).strip()) >= 10):
        errs.append(f'{path}.exception: max_ops > {F.MAX_OPS_DEFAULT} or max_extent_m > {F.MAX_EXTENT_DEFAULT} needs a written exception {{reason (>=10 chars), by, date}}')


def _gen_min(v, path, errs):
    if isinstance(v, str) and SEMVER.match(v) and tuple(map(int, v.split('.'))) > F.GENERATOR_VERSION:
        errs.append(f'{path}: requires generator {v} > {".".join(map(str, F.GENERATOR_VERSION))}')


def _package(path):
    files = {}
    obj, errs = load(path, F.PKG_MAX_BYTES)
    if obj is None: return obj, errs, None
    chk(obj, PACKAGE, '$', errs)        # schema errors are reported even next to data-only errors
    if errs: return obj, errs, None
    files[_rel(path)] = F.sha_file(path)
    pid = obj['package']['id']; pdir = os.path.dirname(os.path.abspath(path))
    if os.path.basename(pdir) != pid: errs.append(f'$.package.id: {pid!r} does not match its folder {os.path.basename(pdir)!r}')
    _gen_min(obj['generator_min'], '$.generator_min', errs)
    src = {s['id'] for s in obj['sources']}
    if len(src) != len(obj['sources']): errs.append('$.sources: duplicate source ids')
    for i, s in enumerate(obj['sources']):
        if 'local' in s:
            p = _contained(s['local'], pdir, pid)
            if p is None: errs.append(f'$.sources[{i}].local: {s["local"]!r} escapes the package folder and {F.refs_dir()}')
            elif p and s.get('sha256') and F.sha_file(p) != s['sha256']: errs.append(f'$.sources[{i}].sha256: does not match {s["local"]}')
            elif not s.get('sha256'): errs.append(f'$.sources[{i}]: local references need sha256')
    for k, e in obj['elevations']['accepted'].items():
        if e['top_y'] <= e['base_y']: errs.append(f'$.elevations.accepted.{k}: top_y must be above base_y')
    pal, comps = _resolve_libs(obj, errs, files)
    if isinstance(obj['palette'], dict): _palette_roles(obj['palette'], '$.palette', errs)
    _comp_under_palette(pal, comps, errs)
    for i, f in enumerate(obj['faces']):
        _check_layout(f, f'$.faces[{i}]', comps, pal, errs)
        for j, s in enumerate(f['evidence']['sources']):
            if s not in src: errs.append(f'$.faces[{i}].evidence.sources[{j}]: {s!r} not in sources')
    if len({f['face'] for f in obj['faces']}) != len(obj['faces']): errs.append('$.faces: a face id is listed twice')
    for i, p in enumerate(obj['envelope']['protrusions']):
        if p['allowance'] not in src: errs.append(f'$.envelope.protrusions[{i}].allowance: {p["allowance"]!r} not in sources')
        if len(p['u']) != 2 or p['u'][1] <= p['u'][0]: errs.append(f'$.envelope.protrusions[{i}].u: expected [a, b] with b > a')
    _limits(obj['limits'], '$.limits', errs, style=obj['package']['kind'] == 'style')
    return obj, errs, dict(palette=pal, components=comps, files=files)


def _style(path):
    files = {}
    obj, errs = load(path, F.COMPONENT_MAX_BYTES)
    if obj is None: return obj, errs, None
    chk(obj, STYLE, '$', errs); _id_matches(obj, path, errs)
    if errs: return obj, errs, None
    files[_rel(path)] = F.sha_file(path)
    _gen_min(obj.get('generator_min', '0.0.0'), '$.generator_min', errs)
    pal, comps = _resolve_libs(obj, errs, files)
    if isinstance(obj['palette'], dict): _palette_roles(obj['palette'], '$.palette', errs)
    _comp_under_palette(pal, comps, errs)
    d = obj['defaults']
    for k, lay in ({'exterior': d} if 'floors' in d else d).items():
        if 'floors' in lay: _check_layout(lay, f'$.defaults' + ('' if 'floors' in d else f'.{k}'), comps, pal, errs)
    _limits(obj.get('limits'), '$.limits', errs, style=True)
    return obj, errs, dict(palette=pal, components=comps, files=files)


def validate_package(path):
    o, e, _ = _package(path); return o, e


def validate_style(path):
    o, e, _ = _style(path); return o, e


def validate(path):
    """dispatch on the file's schema key (the `facade_prep.py validate <path>` command)"""
    obj, errs = load(path, F.PKG_MAX_BYTES)
    if not isinstance(obj, dict): return obj, errs or ['$: expected object']
    fn = {'facade-package-1': validate_package, 'facade-style-1': validate_style, 'facade-component-1': validate_component,
          'facade-palette-1': validate_palette}.get(obj.get('schema'))
    return fn(path) if fn else (obj, [f'$.schema: unknown schema {obj.get("schema")!r}'])


def find_package(pid, pdir=None):
    pdir = pdir or F.packages_dir()
    for ext in ('json', 'yaml', 'yml'):
        p = os.path.join(pdir, pid, 'package.' + ext)
        if os.path.exists(p): return p
    return None


def list_styles():
    d = os.path.join(F.assets_dir(), 'styles')
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith('.json')) if os.path.isdir(d) else []


def list_packages(pdir=None):
    pdir = pdir or F.packages_dir()
    return sorted(d for d in os.listdir(pdir) if find_package(d, pdir)) if os.path.isdir(pdir) else []


def load_selection(kind, sid, packages_dir=None):
    """-> (selection, assets, errors, status). selection = {kind,id,version,sha256,inputs_sha,doc}; assets = {palette, components, files, blocks}.
    status is None when valid, else no_package or invalid_package."""
    if kind == 'package':
        if not ID.match(sid or ''): return None, None, [f'bad package id {sid!r}'], F.NO_PACKAGE
        path = find_package(sid, packages_dir)
        if not path: return None, None, [f'package {sid!r} not found in {packages_dir or F.packages_dir()}'], F.NO_PACKAGE
        obj, errs, assets = _package(path); ver = (obj or {}).get('package', {}).get('version') if isinstance(obj, dict) else None
    else:
        if not ID.match(sid or ''): return None, None, [f'bad style id {sid!r}'], F.NO_PACKAGE
        path = os.path.join(F.assets_dir(), 'styles', sid + '.json')
        if not os.path.exists(path): return None, None, [f'style {sid!r} not found in {os.path.dirname(path)}'], F.NO_PACKAGE
        obj, errs, assets = _style(path); ver = obj.get('version') if isinstance(obj, dict) else None
    if errs: return dict(kind=kind, id=sid, path=path), None, errs, F.INVALID_PACKAGE
    db, src = blockdb()
    assets['blocks'] = db; assets['blocks_source'] = _rel(src) if src != 'fallback' else 'fallback'
    if src != 'fallback': assets['files'][_rel(src)] = F.sha_file(src)
    sel = dict(kind=kind, id=sid, version=ver, sha256=F.sha_file(path), inputs_sha=F.sha_obj(assets['files']), doc=obj)
    return sel, assets, [], None


def check_against_geometry(sel, geom):
    """package face ids, unresolved entries and protrusions must name faces of this building's geometry; bid must be a member"""
    if sel['kind'] != 'package': return []
    d, errs = sel['doc'], []
    ids = {f['id'] for f in geom['faces']}
    if not set(geom.get('members') or [geom['bid']]) & set(d['building']['osm']):
        errs.append(f"$.building.osm: {d['building']['osm']} does not include {geom['bid']} or its members")
    for i, f in enumerate(d['faces']):
        if f['face'] != '*' and f['face'] not in ids: errs.append(f'$.faces[{i}].face: {f["face"]!r} not in geometry faces')
    for i, u in enumerate(d['unresolved']):
        if u['face'] != '*' and u['face'] not in ids: errs.append(f'$.unresolved[{i}].face: {u["face"]!r} not in geometry faces')
    party = {f['id'] for f in geom['faces'] if f['edge'] in ('shared', 'party')}
    for i, p in enumerate(d['envelope']['protrusions']):
        if p['face'] not in ids: errs.append(f'$.envelope.protrusions[{i}].face: {p["face"]!r} not in geometry faces')
        elif p['face'] in party: errs.append(f'$.envelope.protrusions[{i}].face: {p["face"]!r} is a shared/party wall (no protrusions)')
    parts = {p['id'] for p in geom['parts']}
    for i, r in enumerate(d.get('roofs') or []):
        if r['part'] != '*' and r['part'] not in parts: errs.append(f'$.roofs[{i}].part: {r["part"]!r} not in geometry parts')
    return errs


GEOMETRY = {
    'version': ('const', 'facade-geometry-1'), 'bid': 'str', 'members': ('list', 'str'), 'geometry_sha': 'str', 'generator': 'str',
    'sources': 'any', 'parts': ('list', 'any'), 'envelope': {'polygon_mc': ('list', ('list', ('num', -3e7, 3e7), 2)), 'y': ('list', Y, 2), 'parts_mc?': ('list', ('list', ('list', ('num', -3e7, 3e7), 2))), 'holes_mc?': ('list', ('list', ('list', ('num', -3e7, 3e7), 2)))},
    'faces': ('list', {'id': 'str', 'part': 'str', 'edge': ('enum', 'exterior', 'shared', 'party', 'unknown'), 'label?': 'str',
                       'segments': ('list', {'a': ('list', ('num', -3e7, 3e7), 2), 'b': ('list', ('num', -3e7, 3e7), 2), 'u0': ('num', 0, 1e6), 'u1': ('num', 0, 1e6),
                                             'normal': ('list', ('num', -1.01, 1.01), 2), 'curve?': ('or', ('const', None), 'str')}),
                       'length_u': ('num', 0, 1e6), 'base_y': Y, 'top_y': Y,
                       'columns': ('list', ('list', ('num', -3e7, 3e7), 6)), 'notes?': 'any'}),
    'notes?': 'any', 'roof?': 'any', 'status?': 'str', 'reasons?': 'any', 'stats?': 'any', 'identity?': 'any', 'created?': 'str', 'timings_s?': 'any',
}
def validate_geometry(g):
    """facade-geometry-1 interchange check (ARCHITECTURE.md 8a); unknown top-level keys are allowed only from the list above"""
    errs = []; chk(g, GEOMETRY, '$', errs)
    return errs


if __name__ == '__main__':
    if len(sys.argv) > 1:
        o, e = validate(sys.argv[1]); print('\n'.join(e) or 'valid'); sys.exit(1 if e else 0)
    e = []; _data_only({'a': [{'eval': 1}], '__x': 2, 'd': __import__('datetime').date(2024, 1, 1)}, '$', e)
    assert e == ['$.a[0].eval: forbidden key (packages are data only)', '$.__x: forbidden key (packages are data only)',
                 '$.d: non-JSON value of type date (quote YAML dates and avoid tags)'], e
    assert select('all', 3) == [0, 1, 2] and select('1:', 4) == [1, 2, 3] and select('every 2', 5) == [0, 2, 4] and select(-1, 3) == [2]
    assert select('upper', 3) == [1, 2] and select('every 3 from 1', 8) == [1, 4, 7] and select([0, 9], 3) == [0]
    assert check_block('minecraft:oak_door[half=lower,facing={facing}]') is None and 'unknown block' in check_block('minecraft:nope')
    assert 'gravity' in check_block('minecraft:sand') if 'minecraft:sand' in blockdb()[0] else True
    e = []; chk({'x': 1}, {'y': 'str'}, '$', e); assert e == ["$: missing required key 'y'", '$.x: unknown key'], e
    print('facade_schema ok', blockdb()[1])
