"""facade_common.py - shared helpers for the Facade tool (v0.6): chunk math, sha helpers, MORIA_* path overrides,
block-string parse/format, deterministic choices. Contract: docs/facade/ARCHITECTURE.md. Owner: D1."""
import time, hashlib, json, os, re

PLAN_VERSION = 'facade-plan-1'
GENERATOR = 'facade_compile v0.6.0'
GENERATOR_VERSION = (0, 6, 0)
# status taxonomy (ARCHITECTURE.md section 4): missing data never means "empty"
READY, UNRESOLVED, NO_GEOMETRY, NO_PACKAGE, INVALID_PACKAGE, REFUSED_LIMITS, PROTECTED, NOTHING = (
    'ready', 'unresolved', 'no_geometry', 'no_package', 'invalid_package', 'refused_limits', 'protected', 'nothing_to_do')
STATUSES = (READY, UNRESOLVED, NO_GEOMETRY, NO_PACKAGE, INVALID_PACKAGE, REFUSED_LIMITS, PROTECTED, NOTHING)
EVIDENCE = ('observed', 'reference', 'measured', 'inferred', 'style')     # observed = image-derived (automatic facades, AUTO.md 3)
ROLES = ('wall', 'glass', 'mullion', 'frame', 'backing', 'sill', 'band', 'parapet', 'canopy', 'column', 'door', 'roof', 'roof-edge', 'sign', 'trim')

# global limits (section 5/7); a package may raise them only with a written limits.exception
MAX_OPS_DEFAULT, MAX_OPS_EXCEPTION = 60000, 400000
MAX_EXTENT_DEFAULT, MAX_EXTENT_EXCEPTION = 400, 800
PKG_MAX_BYTES, COMPONENT_MAX_BYTES = 2 * 1024 * 1024, 64 * 1024

HERE = os.path.dirname(os.path.abspath(__file__))
def _p(env, default): return os.environ.get(env, default)
def assets_dir(): return _p('MORIA_FACADE_ASSETS', os.path.join(HERE, 'facade_assets'))
def refs_dir(): return _p('MORIA_FACADE_REFS', r'C:\BTE-Migration\facade\refs')
def packages_dir(): return _p('MORIA_FACADE_PACKAGES', os.path.join(os.path.dirname(HERE), 'packages'))
def max_ops_chunk(): return int(_p('MORIA_FACADE_MAX_OPS_CHUNK', '8000'))


def chunk_of(x, z):
    """block -> chunk, floor division (negative coordinates included)"""
    return x // 16, z // 16


def det(*parts):
    """deterministic 64-bit value (stable across runs and machines)"""
    return int.from_bytes(hashlib.sha256('|'.join(map(str, parts)).encode()).digest()[:8], 'big')


def det_unit(*parts): return det(*parts) / 2 ** 64


def canon(obj):
    """canonical JSON text: sorted keys, no whitespace (the basis of every sha in the Facade tool)"""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha_obj(obj): return sha_bytes(canon(obj).encode())
def sha_file(path):
    with open(path, 'rb') as f: return sha_bytes(f.read())


def write_json(path, obj, pretty=False):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':') if not pretty else None, indent=1 if pretty else None, sort_keys=pretty)
    for k in range(20):                    # Windows: the replace is denied while a reader (the plugin's poller) has the target open
        try: os.replace(tmp, path); return
        except PermissionError:
            if k == 19: raise
            time.sleep(0.05)


_BLOCK = re.compile(r'^([a-z0-9_.\-]+:)?([a-z0-9_./\-]+)(\[([^\[\]]*)\])?$')
def parse_block(s):
    """'minecraft:oak_door[half=lower,facing=north]' -> ('minecraft:oak_door', {'half':'lower','facing':'north'}); None if malformed"""
    m = _BLOCK.match(s.strip()) if isinstance(s, str) else None
    if not m: return None
    name = (m.group(1) or 'minecraft:') + m.group(2); st = {}
    if m.group(4):
        for kv in m.group(4).split(','):
            if kv.count('=') != 1: return None
            k, v = (t.strip() for t in kv.split('='))
            if not k or not v or k in st: return None
            st[k] = v
    return name, st


def fmt_block(name, states=None):
    """canonical block string: states sorted by key (deterministic)"""
    return name + ('[' + ','.join(f'{k}={states[k]}' for k in sorted(states)) + ']' if states else '')


# cardinals (MC: x east, z south). The outward normal picks {facing}; ties (45 deg) go to the x axis.
CARD = {'east': (1, 0), 'west': (-1, 0), 'south': (0, 1), 'north': (0, -1)}
OPP = {'east': 'west', 'west': 'east', 'north': 'south', 'south': 'north'}
def cardinal(nx, nz):
    if abs(nx) >= abs(nz): return 'east' if nx > 0 else 'west'
    return 'south' if nz > 0 else 'north'


def placeholders(normal, tangent):
    """{facing},{facing_in},{facing_left},{facing_right},{axis_u} from an outward normal and the +u tangent.
    facing_right is the cardinal perpendicular to {facing} that points along +u (u grows left->right seen from outside)."""
    f = cardinal(*normal); fx, fz = CARD[f]
    a, b = (-fz, fx), (fz, -fx)                     # the two perpendicular cardinals
    r = a if a[0] * tangent[0] + a[1] * tangent[1] >= b[0] * tangent[0] + b[1] * tangent[1] else b
    right = cardinal(*r)
    return {'facing': f, 'facing_in': OPP[f], 'facing_right': right, 'facing_left': OPP[right], 'axis_u': 'x' if right in ('east', 'west') else 'z'}


def fill(s, ph):
    for k, v in ph.items(): s = s.replace('{' + k + '}', v)
    return s


if __name__ == '__main__':      # self-check
    assert chunk_of(0, 0) == (0, 0) and chunk_of(15, 15) == (0, 0) and chunk_of(16, -1) == (1, -1) and chunk_of(-16, -17) == (-1, -2)
    assert parse_block('minecraft:oak_door[half=lower,facing=north]') == ('minecraft:oak_door', {'half': 'lower', 'facing': 'north'})
    assert parse_block('stone') == ('minecraft:stone', {}) and parse_block('minecraft:stone[') is None and parse_block('a:b[x]') is None
    assert fmt_block('minecraft:oak_door', {'half': 'lower', 'facing': 'north'}) == 'minecraft:oak_door[facing=north,half=lower]'
    assert det('a', 1) == det('a', 1) != det('a', 2) and sha_obj({'b': 1, 'a': 2}) == sha_obj({'a': 2, 'b': 1})
    p = placeholders((0, 1), (1, 0))        # south face, u grows east (seen from outside, looking north: east is right)
    assert p == {'facing': 'south', 'facing_in': 'north', 'facing_right': 'east', 'facing_left': 'west', 'axis_u': 'x'}, p
    assert placeholders((0.98, 0.2), (-0.2, 0.98))['facing'] == 'east'
    print('facade_common ok')
