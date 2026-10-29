"""egg_catalog_build.py [--jar terraplusminus-1.3.3.jar] [--out prep/egg_assets/vegetation_catalog.json]
Builds the Egg vegetation catalog (docs/egg/VEGETATION-SPEC.md, attribution in docs/egg/ASSETS.md):
 - TerraPlusMinus 1.3.3 customTrees.json (MIT, (c) Build the Earth Germany), read straight from the jar (read-only),
   Bukkit material names -> stable Minecraft 1.18.2 block states (leaves persistent=true, logs axis=y, fence/wall
   connections computed inside the tree, saplings replaced by persistent leaves because they tick/grow/pop);
 - hand-authored procedural Las Vegas entries (palms, desert trees, pines, cypress, olive, shrubs, yucca, ground cover).
Every entry is re-anchored so the trunk base (first block above the ground) is [0,0,0]; blocks are [dx,dy,dz,state]."""
import json, math, os, sys, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
a = sys.argv
JAR = a[a.index('--jar') + 1] if '--jar' in a else r'C:\BTE-Migration\downloads\terraplusminus\terraplusminus-1.3.3.jar'
OUT = a[a.index('--out') + 1] if '--out' in a else os.path.join(HERE, 'egg_assets', 'vegetation_catalog.json')
TPM_PATH = 'assets/terraplusminus/data/customTrees.json'
TPM_SRC = dict(source='TerraPlusMinus 1.3.3 (terraplusminus-1.3.3.jar, ' + TPM_PATH + ')', licence='MIT',
               attribution='Tree models (c) 2022-2026 Build the Earth Germany, TerraPlusMinus, MIT License, https://github.com/BTE-Germany/TerraPlusMinus')
AUTH_SRC = dict(source='Moria (hand-authored procedural model)', licence='same as Moria repository', attribution='Moria contributors')

LEAF = '[distance=1,persistent=true]'          # 1.18.2 leaves have no waterlogged property; persistent so they never decay
SIDES = ('north', 'east', 'south', 'west')
DIR = {'north': (0, -1), 'east': (1, 0), 'south': (0, 1), 'west': (-1, 0)}


def leaves(kind): return f'minecraft:{kind}_leaves{LEAF}'
def log(kind, axis='y'): return f'minecraft:{kind}[axis={axis}]'


def tpm_state(mat):
    m = mat.lower()
    if m.endswith('_sapling'): return leaves(m[:-8])              # saplings tick (stage) and grow or pop off: substitute leaves
    if m.endswith('_leaves'): return f'minecraft:{m}{LEAF}'
    if m.endswith(('_log', '_wood')): return log(m)
    return 'minecraft:' + m                                       # fences/walls/gates: properties filled in by connect()


def connect(blocks):
    """complete fence / wall / fence-gate states the way the game would compute them inside the model
    (fences link to fences, gates and full blocks; walls to walls and full blocks). Outside neighbours are unknown: unlinked."""
    at = {(x, y, z): s for x, y, z, s in blocks}
    def kind(s):
        n = s.split('[')[0]
        return 'gate' if n.endswith('_fence_gate') else 'fence' if n.endswith('_fence') else 'wall' if n.endswith('_wall') else 'full'
    out = []
    for x, y, z, s in blocks:
        k = kind(s)
        if '[' in s or k == 'full': out.append([x, y, z, s]); continue
        if k == 'gate': out.append([x, y, z, s + '[facing=north,in_wall=false,open=false,powered=false]']); continue
        props = {}
        for side in SIDES:
            dx, dz = DIR[side]; nb = at.get((x + dx, y, z + dz)); nk = kind(nb) if nb else None
            link = nk is not None and (nk == 'full' or nk == k or (k == 'fence' and nk == 'gate'))
            props[side] = ('low' if link else 'none') if k == 'wall' else ('true' if link else 'false')
        if k == 'wall': props['up'] = 'true'
        props['waterlogged'] = 'false'
        out.append([x, y, z, s + '[' + ','.join(f'{p}={props[p]}' for p in sorted(props)) + ']'])
    return out


def entry(eid, label, species, cls, habitats, blocks, src, extra=None):
    blocks = sorted(blocks, key=lambda b: (b[1], b[0], b[2]))
    xs, ys, zs = [b[0] for b in blocks], [b[1] for b in blocks], [b[2] for b in blocks]
    assert [0, 0, 0] in [b[:3] for b in blocks], (eid, 'anchor block missing')
    assert min(ys) == 0, (eid, 'blocks below the anchor')
    for b in blocks:
        if '_leaves' in b[3]: assert 'persistent=true' in b[3], b
        if b[3].split('[')[0].endswith(('_log', '_wood', '_stem')): assert 'axis=' in b[3], b
    e = dict(id=eid, label=label, species=species, **{'class': cls}, habitats=habitats,
             dims=dict(w=max(xs) - min(xs) + 1, d=max(zs) - min(zs) + 1, h=max(ys) + 1),
             crown_radius=max(max(abs(v) for v in xs), max(abs(v) for v in zs)),
             anchor=[0, 0, 0], format='moria-blocks-1', provenance='tpm-asset' if src is TPM_SRC else 'authored', **src)
    e.update(extra or {}); e['blocks'] = blocks
    return e


# ---------------- TerraPlusMinus assets
TPM_META = {   # TPM key -> (label, species, class, habitats). Honest labels: these are generic shapes, not Las Vegas species.
    'oak': ('generic broadleaf shade tree (TPM oak model)', 'unknown broadleaf', 'broadleaf', ['irrigated-urban']),
    'birch': ('slender deciduous tree (TPM birch model; aspen stand-in)', 'unknown broadleaf (aspen-like)', 'broadleaf', ['mountain']),
    'savanna': ('flat-topped desert tree (TPM savanna/acacia model)', 'unknown broadleaf (acacia-form)', 'broadleaf', ['irrigated-urban', 'desert']),
    'spruce': ('tall conifer (TPM spruce model)', 'unknown conifer', 'conifer', ['mountain']),
}


def tpm_entries():
    with zipfile.ZipFile(JAR) as z: raw = z.read(TPM_PATH)
    T = json.loads(raw)['trees']; out = []
    for sp, sizes in T.items():
        for size, trees in sizes.items():
            for tn, t in trees.items():
                cells = {}
                for q in t['blocks']: cells[(q['x'], q['y'], q['z'])] = tpm_state(q['material'])   # later duplicates win, as in TPM
                base = sorted([c for c in cells if c[1] == 0], key=lambda c: (c[0] ** 2 + c[2] ** 2, c))[0]
                blocks = connect([[x - base[0], y, z - base[2], s] for (x, y, z), s in cells.items()])
                label, species, cls, hab = TPM_META[sp]
                out.append(entry(f'tpm-{sp}-{size}-{tn}', label, species, cls, hab, blocks, TPM_SRC,
                                 dict(source_key=f'trees.{sp}.{size}.{tn}', source_origin_offset=[-base[0], 0, -base[2]], tpm_spread=t.get('spread'))))
    return out, len(raw)


# ---------------- authored procedural models (deterministic geometry, no randomness)
def disc(r, y, state, hollow_center=False, gaps=None):
    out = []
    for dx in range(-r, r + 1):
        for dz in range(-r, r + 1):
            d = math.hypot(dx, dz)
            if d > r + 0.3 or (hollow_center and dx == 0 and dz == 0): continue
            if gaps and (dx * 7 + dz * 13 + y * 5) % gaps == 0 and d > 0.5: continue    # fixed pattern: airy crowns
            out.append([dx, y, dz, state])
    return out


def merge(*parts):
    cells = {}
    for p in parts:
        for x, y, z, s in p:
            if (x, y, z) in cells and '_leaves' in s and '_leaves' not in cells[(x, y, z)]: continue    # wood wins over leaves
            cells[(x, y, z)] = s
    return [[x, y, z, s] for (x, y, z), s in cells.items()]


def trunk(h, state, x=0, z=0, y0=0): return [[x, y, z, state] for y in range(y0, y0 + h)]


def fan_palm(h):        # Washingtonia robusta: very tall thin trunk, small dense head with drooping fronds
    L = leaves('jungle'); top = h
    head = [[0, top, 0, L]] + [[dx, top, dz, L] for dx, dz in DIR.values()] + [[2 * dx, top - 1, 2 * dz, L] for dx, dz in DIR.values()]
    head += [[dx, top - 1, dz, L] for dx in (-1, 1) for dz in (-1, 1)] + [[0, top + 1, 0, L]]
    skirt = [[dx, top - 2, dz, 'minecraft:spruce_fence'] for dx, dz in DIR.values()] if h >= 12 else []
    return merge(trunk(h, log('jungle_log')), head, skirt)


def date_palm(h):       # Phoenix dactylifera: thicker-looking trunk, large arching crown
    L = leaves('jungle'); top = h
    head = disc(1, top, L) + [[0, top + 1, 0, L]]
    for dx, dz in list(DIR.values()) + [(1, 1), (1, -1), (-1, 1), (-1, -1)]:
        head += [[2 * dx, top, 2 * dz, L], [3 * dx, top - 1, 3 * dz, L]]
    return merge(trunk(h, log('jungle_wood')), head)


def mesquite(h):        # Prosopis: low, multi-stem, wide flat irregular crown
    L, W = leaves('oak'), log('dark_oak_log')
    stems = trunk(2, W) + [[1, 1, 0, log('dark_oak_log', 'x')], [-1, 2, 1, W], [1, 2, -1, W]]
    crown = disc(3, h - 2, L, gaps=5) + disc(2, h - 1, L) + disc(1, h, L, gaps=4)
    return merge(stems, trunk(h - 2, W, y0=2), crown)


def palo_verde(h):      # Parkinsonia: green bark, airy open crown, small
    L, W = leaves('azalea'), log('stripped_jungle_log')
    fence = 'minecraft:jungle_fence'
    arms = [[dx, h - 2, dz, fence] for dx, dz in DIR.values()]
    return merge(trunk(h - 1, W), arms, disc(3, h - 1, L, gaps=3), disc(2, h, L, gaps=3))


def desert_willow(h):   # Chilopsis linearis: slender, open, flowering
    L, W = leaves('flowering_azalea'), log('acacia_log')
    return merge(trunk(h - 1, W), [[1, h - 2, 0, log('acacia_log', 'x')]], disc(2, h - 1, L, gaps=4), disc(1, h, L))


def pine(h, conical):   # Aleppo (rounded, open) / Mondell = eldarica (conical, denser)
    L, W = leaves('spruce'), log('spruce_log'); parts = [trunk(h, W)]
    for y in range(h // 3, h + 1):
        f = (h - y) / max(1, h - h // 3)
        r = round(1 + 2 * f) if conical else (3 if h // 3 + 1 < y < h - 1 else 2)
        parts.append(disc(r, y, L, gaps=None if conical else 6))
    parts.append([[0, h + 1, 0, L]])
    return merge(*parts)


def cypress(h):         # Cupressus sempervirens 'Stricta': narrow column
    L = leaves('spruce')
    col = [[dx, y, dz, L] for y in range(1, h + 1) for dx, dz in DIR.values()] + [[0, h, 0, L], [0, h + 1, 0, L]]
    return merge(trunk(h, log('spruce_log')), col)


def olive(h):           # Olea europaea: short gnarled trunk, round dense crown
    L, W = leaves('oak'), log('oak_log')
    return merge(trunk(2, W), [[1, 2, 0, log('oak_log', 'x')], [1, 3, 0, W]], disc(2, h - 1, L), disc(2, h - 2, L, gaps=5), disc(1, h, L))


def crop_tree(h):       # generic small orchard tree (citrus / pomegranate / fig form) for irrigated yards; agriculture may reuse
    L, W = leaves('oak'), log('oak_log')
    return merge(trunk(2, W), disc(2, h - 1, L), disc(1, h, L), disc(1, h - 2, L))


def creosote(v):        # Larrea tridentata: 1-2 m open shrub of several stems
    L = leaves('jungle')
    if v == 1: return [[0, 0, 0, L], [1, 0, 0, L], [0, 1, 0, L]]
    return [[0, 0, 0, L], [-1, 0, 1, L], [1, 0, 0, L], [0, 1, 0, L], [0, 1, 1, L]]


def joshua(h):          # Yucca brevifolia: trunk with up-turned arms ending in leaf clusters
    W = log('dark_oak_log'); L = leaves('jungle'); xa = log('dark_oak_log', 'x'); za = log('dark_oak_log', 'z')
    arms = [[1, h - 2, 0, xa], [2, h - 2, 0, W], [2, h - 1, 0, L], [-1, h - 1, 0, xa], [-1, h, 0, L], [0, h - 1, 1, za], [0, h - 1, 2, W], [0, h, 2, L]]
    return merge(trunk(h - 1, W), arms, [[0, h - 1, 0, L], [0, h, 0, L]])


def yucca(h):           # Mojave yucca / Spanish dagger: short stem, spiky head
    L = leaves('jungle')
    return merge(trunk(h - 1, log('dark_oak_log')), [[0, h - 1, 0, L], [0, h, 0, L]] + [[dx, h - 1, dz, L] for dx, dz in DIR.values()])


def authored():
    A, E = AUTH_SRC, []
    def add(eid, label, species, cls, hab, blocks, **x): E.append(entry(eid, label, species, cls, hab, connect(blocks), A, x or None))
    for h in (9, 12, 15, 18): add(f'lv-washingtonia-{h}', f'Mexican fan palm, {h + 2} m', 'Washingtonia robusta', 'palm', ['irrigated-urban'], fan_palm(h))
    for h in (6, 8, 10): add(f'lv-date-palm-{h}', f'date palm, {h + 2} m', 'Phoenix dactylifera', 'palm', ['irrigated-urban'], date_palm(h))
    for h in (5, 7): add(f'lv-mesquite-{h}', f'mesquite, {h + 1} m', 'Prosopis spp.', 'broadleaf', ['irrigated-urban', 'desert', 'riparian'], mesquite(h))
    for h in (5, 6): add(f'lv-palo-verde-{h}', f'palo verde, {h + 1} m', 'Parkinsonia spp.', 'broadleaf', ['irrigated-urban', 'desert'], palo_verde(h))
    for h in (5, 6): add(f'lv-desert-willow-{h}', f'desert willow, {h + 1} m', 'Chilopsis linearis', 'broadleaf', ['irrigated-urban', 'riparian'], desert_willow(h))
    for h in (9, 12): add(f'lv-aleppo-pine-{h}', f'Aleppo pine, {h + 2} m', 'Pinus halepensis', 'conifer', ['irrigated-urban'], pine(h, False))
    for h in (8, 11): add(f'lv-mondell-pine-{h}', f'Mondell (eldarica) pine, {h + 2} m', 'Pinus eldarica', 'conifer', ['irrigated-urban'], pine(h, True))
    for h in (8, 11): add(f'lv-italian-cypress-{h}', f'Italian cypress, {h + 2} m', 'Cupressus sempervirens', 'conifer', ['irrigated-urban'], cypress(h))
    for h in (4, 5): add(f'lv-olive-{h}', f'olive, {h + 1} m', 'Olea europaea', 'broadleaf', ['irrigated-urban'], olive(h))
    add('lv-orchard-tree-4', 'small orchard tree (citrus/pomegranate form), 5 m', 'unknown fruit tree', 'crop-tree', ['irrigated-urban'], crop_tree(4))
    for v in (1, 2): add(f'lv-creosote-{v}', 'creosote bush', 'Larrea tridentata', 'desert-shrub', ['desert'], creosote(v))
    add('lv-white-bursage', 'white bursage (dead bush stand-in)', 'Ambrosia dumosa', 'desert-shrub', ['desert'], [[0, 0, 0, 'minecraft:dead_bush']], support='dead_bush')
    add('lv-mojave-yucca-3', 'Mojave yucca', 'Yucca schidigera', 'desert-shrub', ['desert'], yucca(3))
    add('lv-joshua-tree-5', 'Joshua tree (only where mapped as Yucca brevifolia)', 'Yucca brevifolia', 'desert-shrub', ['desert'], joshua(5), mapped_only=True)
    add('lv-dead-bush', 'sparse desert ground cover: dead bush', 'unknown (desert annual / dormant shrub)', 'groundcover', ['desert'], [[0, 0, 0, 'minecraft:dead_bush']], support='dead_bush')
    add('lv-grass', 'sparse ground cover: grass', 'unknown grass', 'groundcover', ['desert', 'riparian'], [[0, 0, 0, 'minecraft:grass']], support='grass')
    return E


if __name__ == '__main__':
    tpm, raw_bytes = tpm_entries(); auth = authored()
    ids = [e['id'] for e in tpm + auth]; assert len(ids) == len(set(ids))
    cat = dict(catalog_version='veg-1', minecraft='1.18.2', built=time.strftime('%Y-%m-%d'), builder='prep/egg_catalog_build.py',
               block_format='blocks: [dx, dy, dz, block_state]; [0,0,0] = trunk base = first block above the ground (base_y + 1)',
               classes=['palm', 'broadleaf', 'conifer', 'shrub', 'desert-shrub', 'groundcover', 'crop-tree'],
               habitats=['desert', 'irrigated-urban', 'riparian', 'mountain'],
               sources=dict(tpm=dict(TPM_SRC, jar=os.path.basename(JAR), path=TPM_PATH, bytes=raw_bytes,
                                     conversion='Bukkit Material -> 1.18.2 state; leaves persistent; *_SAPLING -> persistent leaves; re-anchored on the y=0 block'),
                            authored=AUTH_SRC),
               entries=tpm + auth)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f: json.dump(cat, f, separators=(',', ':'))
    print('catalog', OUT, len(tpm), 'TPM +', len(auth), 'authored =', len(ids), 'entries,', os.path.getsize(OUT), 'bytes')
