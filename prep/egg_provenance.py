"""egg_provenance.py - read-only reader of the APPROVED BASELINE WORLD (Minecraft 1.18.2 Anvil) -> context columns (ARCHITECTURE.md section 5).
baseline_columns(tx, tz, world_dir) -> dict(base_y, base_block, base_cover [64][64] lists (index [z-z0][x-x0], None = unknown), chunks_found, ...)
 base_y     : y of the top TERRAIN block (material in NATURAL, section 6) of the column
 base_block : its block state in Bukkit BlockData.getAsString() form, e.g. 'minecraft:grass_block[snowy=false]' (properties sorted by name,
              as Minecraft's StateDefinition stores them)
 base_cover : non-terrain blocks above it up to the first air, top-down (grass, dead_bush, TPM tree log/leaves, road concrete powder, water)
              or None. A covered column keeps what is there: generators never plan over it.
Sources, per chunk, in this order (anything else -> None columns: no baseline = ineligible, never 'empty land'):
 1 captured   : <captured_dir>/<cx>_<cz>.json written by the plugin when the chunk was newly generated (egg-baseline-1)
 2 pilot-generation : the baseline world, ONLY for the 910 chunks TerraPlusMinus freshly generated for the pilot (generation_chunks.json);
                the same folder also holds converted purchased-world regions, which are NOT approved terrain. Chunk Status must be 'full'.
Self-contained: minimal NBT + region reader (zlib), 1.18 palette unpacking (entries do not span longs). Files are opened read-only."""
import json, os, struct, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egg_common as E

PILOT_CHUNKS = os.environ.get('MORIA_PILOT_CHUNKS', r'C:\BTE-Migration\summerlin\pilot\generation_chunks.json')
CAPTURED = os.environ.get('MORIA_EGG_CAPTURED', r'C:\BTE-Migration\11-egg-test\plugins\SummerlinShellTool\egg-baseline')
MIN_Y = -1024                                   # BTE world (TerraPlusMinus datapack): min_y -1024, height 3056
NATURAL = {'sand', 'red_sand', 'sandstone', 'red_sandstone', 'smooth_sandstone', 'terracotta', 'dirt', 'coarse_dirt', 'grass_block', 'gravel',
           'stone', 'andesite', 'diorite', 'granite', 'tuff', 'clay', 'podzol'}
AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
MAX_COVER = 64                                  # deeper than this without terrain -> unknown (structure, not ground)


def natural(state):
    n = state.split('[', 1)[0].removeprefix('minecraft:')
    return n in NATURAL or n.endswith('_terracotta')


# ---------------- NBT (big-endian, uncompressed payload)
def _nbt(b, i, t):
    if t == 1: return struct.unpack_from('>b', b, i)[0], i + 1
    if t == 2: return struct.unpack_from('>h', b, i)[0], i + 2
    if t == 3: return struct.unpack_from('>i', b, i)[0], i + 4
    if t == 4: return struct.unpack_from('>q', b, i)[0], i + 8
    if t == 5: return struct.unpack_from('>f', b, i)[0], i + 4
    if t == 6: return struct.unpack_from('>d', b, i)[0], i + 8
    if t == 7: n = struct.unpack_from('>i', b, i)[0]; return b[i + 4:i + 4 + n], i + 4 + n
    if t == 8: n = struct.unpack_from('>H', b, i)[0]; return b[i + 2:i + 2 + n].decode('utf-8', 'replace'), i + 2 + n
    if t == 9:
        et, n = struct.unpack_from('>bi', b, i); i += 5; out = []
        for _ in range(n): v, i = _nbt(b, i, et); out.append(v)
        return out, i
    if t == 10:
        d = {}
        while True:
            ct = b[i]; i += 1
            if ct == 0: return d, i
            n = struct.unpack_from('>H', b, i)[0]; k = b[i + 2:i + 2 + n].decode('utf-8', 'replace'); i += 2 + n
            d[k], i = _nbt(b, i, ct)
    if t == 11: n = struct.unpack_from('>i', b, i)[0]; return list(struct.unpack_from(f'>{n}i', b, i + 4)), i + 4 + 4 * n
    if t == 12: n = struct.unpack_from('>i', b, i)[0]; return list(struct.unpack_from(f'>{n}q', b, i + 4)), i + 4 + 8 * n
    raise ValueError(f'bad NBT tag {t} at {i}')


def read_chunk(region_dir, cx, cz):
    """root compound of chunk (cx, cz) or None when the region/chunk does not exist"""
    p = os.path.join(region_dir, f'r.{cx >> 5}.{cz >> 5}.mca')
    if not os.path.exists(p): return None
    with open(p, 'rb') as f:
        f.seek(4 * ((cx & 31) + 32 * (cz & 31))); loc = int.from_bytes(f.read(4), 'big')
        if loc == 0: return None
        f.seek((loc >> 8) * 4096); n, comp = struct.unpack('>iB', f.read(5)); data = f.read(n - 1)
    if comp & 128: raise ValueError(f'external chunk file (.mcc) not supported: {cx},{cz}')
    if comp not in (1, 2): raise ValueError(f'compression {comp} not supported: {cx},{cz}')
    raw = zlib.decompress(data, 47)             # wbits 47: auto-detect zlib (2) or gzip (1) header
    if raw[0] != 10: raise ValueError('chunk root is not a compound')
    n = struct.unpack_from('>H', raw, 1)[0]
    return _nbt(raw, 3 + n, 10)[0]


def _state(e):
    s = e['Name']; P = e.get('Properties')
    return s + '[' + ','.join(f'{k}={P[k]}' for k in sorted(P)) + ']' if P else s


def _unpack(longs, bits, n=4096):
    """1.16+ packing: floor(64/bits) entries per long, entries never span longs"""
    per, mask, out = 64 // bits, (1 << bits) - 1, []
    for L in longs:
        L &= (1 << 64) - 1
        for k in range(per): out.append((L >> (k * bits)) & mask)
    return out[:n]


class Chunk:
    def __init__(self, root):
        self.status = root.get('Status'); self.secs = {}
        for s in root.get('sections', []):
            bs = s.get('block_states')
            if bs: self.secs[s['Y']] = (bs['palette'], bs.get('data'))
        self.dec = {}
        hm = root.get('Heightmaps', {}).get('WORLD_SURFACE')       # 12-bit (height 3056) values relative to MIN_Y: first air above
        self.surf = _unpack(hm, 12, 256) if hm else None
    def block(self, x, y, z):                                   # local x, z; absolute y
        sy = y >> 4
        if sy not in self.dec:
            if sy not in self.secs: self.dec[sy] = None
            else:
                pal, data = self.secs[sy]; st = [_state(e) for e in pal]
                self.dec[sy] = (st, _unpack(data, max(4, (len(pal) - 1).bit_length())) if data else None)
        d = self.dec[sy]
        if d is None: return 'minecraft:air'
        st, idx = d
        return st[0] if idx is None else st[idx[((y & 15) * 16 + z) * 16 + x]]


def column(ch, x, z):
    """(base_y, base_block, cover) for local column x, z of a full chunk; (None, None, None) if no terrain found"""
    top = (ch.surf[z * 16 + x] + MIN_Y - 1) if ch.surf else max(ch.secs) * 16 + 15
    cover = []
    for y in range(top, MIN_Y - 1, -1):
        b = ch.block(x, y, z)
        if b in AIR:
            if cover: cover = []                # an air gap: what was above is not resting on this column's ground (keep only the contiguous stack)
            continue
        if natural(b): return y, b, (cover or None)
        cover.append(b)
        if len(cover) > MAX_COVER: break
    return None, None, None


_pilot = None
def pilot_chunks():
    global _pilot
    if _pilot is None: _pilot = {tuple(c) for c in json.load(open(PILOT_CHUNKS))['chunks']}
    return _pilot


def captured_chunk(captured_dir, cx, cz):
    """plugin-captured new-chunk baseline -> (base_y, base_block, base_cover) [16][16] lists indexed [z][x], or None"""
    p = os.path.join(captured_dir, f'{cx}_{cz}.json')
    if not os.path.exists(p): return None
    d = json.load(open(p))
    if d.get('version') != 'egg-baseline-1' or d.get('chunk') != [cx, cz]: raise ValueError(f'bad captured baseline {p}')
    cov = [[None if c is None else (c if isinstance(c, list) else [c]) for c in row] for row in d['base_cover']]   # same list form as the world reader
    return d['base_y'], d['base_block'], cov


def baseline_columns(tx, tz, world_dir=E.BASELINE_WORLD, captured_dir=None):
    b = E.tile_bounds(tx, tz); c0x, c0z = b['chunks'][0], b['chunks'][1]; rd = os.path.join(world_dir, 'region')
    captured_dir = captured_dir or CAPTURED; pilot = pilot_chunks()
    by = [[None] * 64 for _ in range(64)]; bb = [[None] * 64 for _ in range(64)]; bc = [[None] * 64 for _ in range(64)]
    src, notfull = [], []
    for i in range(4):
        for j in range(4):
            cx, cz = c0x + i, c0z + j; got = captured_chunk(captured_dir, cx, cz); s = 'captured'
            if got is None and (cx, cz) in pilot:
                root = read_chunk(rd, cx, cz); s = 'pilot-generation'
                if root is not None:
                    ch = Chunk(root)
                    if str(ch.status).removeprefix('minecraft:') != 'full': notfull.append([cx, cz, ch.status])
                    else:
                        cols = [[column(ch, x, z) for x in range(16)] for z in range(16)]
                        got = tuple([[c[k] for c in row] for row in cols] for k in range(3))
            if got is None: src.append([cx, cz, 'none']); continue
            src.append([cx, cz, s])
            for z in range(16):
                r = j * 16 + z
                by[r][i * 16:i * 16 + 16], bb[r][i * 16:i * 16 + 16], bc[r][i * 16:i * 16 + 16] = got[0][z], got[1][z], got[2][z]
    known = sum(v is not None for r in by for v in r)
    return dict(base_y=by, base_block=bb, base_cover=bc, chunk_sources=src, chunks_found=sum(s[2] != 'none' for s in src), chunks_not_full=notfull,
                columns_known=known, status='ok' if known == 4096 else ('partial' if known else 'no_baseline'), world=world_dir, captured_dir=captured_dir)


if __name__ == '__main__':      # self-check on a real pilot tile against TPM's own height model (independent reading)
    import time, collections
    import numpy as np
    from geo import Geo
    di = E.tile_of_block(-12160205, -5448042)          # Desert Inn sizing tile: region r.-23751.-10641 is NOT in the baseline
    assert baseline_columns(*di)['status'] == 'no_baseline'
    tx, tz = -189993, -85162                           # pilot tile around block -12159520, -5450336 (Summerlin, inside the baseline)
    t = time.time(); r = baseline_columns(tx, tz); dt = time.time() - t
    print('tile', tx, tz, 'chunks', r['chunks_found'], 'known', r['columns_known'], 'status', r['status'], f'{dt:.3f}s')
    assert r['chunks_found'] == 16 and r['columns_known'] == 4096, r['status']
    surf = collections.Counter(s for row in r['base_block'] for s in row); print('surface', surf.most_common(6))
    print('cover', collections.Counter(tuple(c) if c else None for row in r['base_cover'] for c in row).most_common(6))
    assert all(natural(s) for s in surf) and surf['minecraft:sand'] > 2000
    assert _state({'Name': 'minecraft:grass_block', 'Properties': {'snowy': 'false'}}) == 'minecraft:grass_block[snowy=false]'
    assert _state({'Name': 'minecraft:oak_log', 'Properties': {'axis': 'y'}}) == 'minecraft:oak_log[axis=y]'
    x0, z0 = E.tile_bounds(tx, tz)['blocks'][:2]; g = Geo()
    S = [(x0 + 8 * k + 3, z0 + 7 * k + 2) for k in range(8)] + [(x0 + 60 - 7 * k, z0 + 5 + 7 * k) for k in range(8)]
    hh = g.heights(g.inv([(x + 0.5, z + 0.5) for x, z in S])); g.close()
    d = [int(r['base_y'][z - z0][x - x0] - np.floor(h)) for (x, z), h in zip(S, hh) if r['base_cover'][z - z0][x - x0] is None]
    print('base_y - floor(TPM height) at', len(d), 'samples', d)       # TPM puts the top block at floor(height)
    assert d and max(abs(v) for v in d) <= 1, d
    assert all(c[2] == 'pilot-generation' for c in r['chunk_sources'])
    p = baseline_columns(-189992, -85162)            # 8 of its 16 chunks were generated; the others are purchased-world terrain -> None
    assert p['chunks_found'] == 8 and p['columns_known'] == 8 * 256, p['chunk_sources']
    import tempfile                                  # captured source wins over the snapshot and gives the same list form
    with tempfile.TemporaryDirectory() as td:
        cx, cz = tx * 4, tz * 4
        json.dump(dict(version='egg-baseline-1', chunk=[cx, cz], captured='x', source='new-chunk', world='world', base_y=[[700] * 16] * 16,
                       base_block=[['minecraft:sand'] * 16] * 16, base_cover=[['minecraft:dead_bush'] + [None] * 15] * 16), open(os.path.join(td, f'{cx}_{cz}.json'), 'w'))
        c = baseline_columns(tx, tz, captured_dir=td)
        assert c['base_y'][0][0] == 700 and c['base_cover'][5][0] == ['minecraft:dead_bush'] and c['base_cover'][5][1] is None and c['chunk_sources'][0][2] == 'captured'
    print('egg_provenance ok')
