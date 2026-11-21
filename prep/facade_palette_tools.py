"""facade_palette_tools.py - Facade (v0.6) asset tools, owned by agent C. Contracts: docs/facade/ARCHITECTURE.md 8a.

  blocks  <reports/blocks.json>         -> prep/facade_assets/blocks-1.18.2.json (from the 1.18.2 server data reports)
  colours <client-1.18.2.jar>           -> prep/facade_assets/colours-1.18.2.json (own mean sRGB + CIELAB per block)
  nearest <#rrggbb> <role> [k]          -> nearest blocks allowed for that role
  check                                 -> lint every palette, component and style against blocks + role rules
  sheets  <out-dir> [client.jar]        -> one PNG review sheet per palette (swatches per role + facade mock-up strip)
  (no args)                             -> self-check

Only derived numbers are written to the repo; textures are read from a local jar and never stored (docs/facade/ASSETS.md).
"""
import json, os, re, sys, zipfile, io
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'facade_assets')
BLOCKS = os.path.join(ASSETS, 'blocks-1.18.2.json')
COLOURS = os.path.join(ASSETS, 'colours-1.18.2.json')
PLAN_ROLES = ['wall', 'glass', 'mullion', 'frame', 'backing', 'sill', 'band', 'parapet', 'canopy', 'column', 'door',
              'roof', 'roof-edge', 'sign', 'trim']
PLACEHOLDERS = {'{facing}': 'north', '{facing_in}': 'south', '{facing_left}': 'west', '{facing_right}': 'east', '{axis_u}': 'x'}
TEXTURE_RULE = re.compile(r'^(rows%\d+==\d+|cols%\d+==\d+|hash\(u,v,seed\)<(0(\.\d+)?|1(\.0+)?))$')

# ---------------------------------------------------------------- stateful / unsafe classification (name based)
GRAVITY = re.compile(r'^(red_)?sand$|(^|_)(gravel|concrete_powder|anvil)$|^(dragon_egg|scaffolding|pointed_dripstone)$')
LIGHT = re.compile(r'glowstone|sea_lantern|shroomlight|jack_o_lantern|redstone_lamp|magma_block|crying_obsidian|'
                   r'respawn_anchor|torch|lantern|campfire|beacon|conduit|end_rod|sea_pickle|glow_lichen|candle|'
                   r'end_portal|end_gateway|^fire$|soul_fire|^lava$|furnace|smoker|amethyst_cluster|_bud$|light$|'
                   r'redstone_ore|brewing_stand|enchanting_table|ender_chest|cave_vines|sculk_sensor|lit_')
UNSTABLE = re.compile(r'_button$|_pressure_plate$|_carpet$|^carpet$|_sign$|_banner$|torch|rail$|_sapling$|_flower|'
                      r'tulip|^(dandelion|poppy|allium|azure_bluet|oxeye_daisy|cornflower|lily_of_the_valley|wither_rose|'
                      r'grass|fern|tall_grass|large_fern|dead_bush|vine|ladder|lever|tripwire|tripwire_hook|redstone_wire|'
                      r'repeater|comparator|snow|cactus|sugar_cane|bamboo|kelp|kelp_plant|seagrass|tall_seagrass|lily_pad|'
                      r'cocoa|wheat|carrots|potatoes|beetroots|sweet_berry_bush|nether_wart|chorus_plant|chorus_flower|'
                      r'big_dripleaf|big_dripleaf_stem|small_dripleaf|spore_blossom|hanging_roots|moss_carpet|'
                      r'twisting_vines|twisting_vines_plant|weeping_vines|weeping_vines_plant|glow_lichen|turtle_egg|'
                      r'frogspawn|flower_pot|cake|bell|scaffolding|azalea|flowering_azalea)$|_mushroom$|_fungus$|_roots$|'
                      r'^potted_|_head$|_skull$|_coral|_stem$|^(water|lava|air|cave_air|void_air|fire|soul_fire|'
                      r'bubble_column|nether_portal|end_portal|end_gateway|moving_piston|piston_head)$')
BLOCK_ENTITY = re.compile(r'chest$|^(furnace|smoker|blast_furnace|barrel|hopper|dispenser|dropper|jukebox|spawner|'
                          r'brewing_stand|enchanting_table|beacon|conduit|lectern|bell|campfire|soul_campfire|beehive|'
                          r'bee_nest|sculk_sensor|daylight_detector|comparator|end_portal|end_gateway|structure_block|'
                          r'jigsaw|command_block|chain_command_block|repeating_command_block|moving_piston|'
                          r'lodestone)$|_sign$|_banner$|_bed$|shulker_box$|_head$|_skull$')
OTHER_BAD = re.compile(r'_ore$|^ancient_debris$|_leaves$|^infested_|^(ice|packed_ice|blue_ice|frosted_ice|snow_block|'
                       r'powder_snow|tnt|slime_block|honey_block|sponge|wet_sponge|bedrock|barrier|structure_void|'
                       r'budding_amethyst|dirt|grass_block|mycelium|podzol|farmland|dirt_path|rooted_dirt|moss_block|'
                       r'observer|piston|sticky_piston|target|note_block|redstone_block|spawner|light|'
                       r'dried_kelp_block|hay_block|cobweb|composter|cauldron|water_cauldron|lava_cauldron|'
                       r'powder_snow_cauldron|grindstone|stonecutter|loom|smithing_table|cartography_table|'
                       r'fletching_table|crafting_table|bookshelf|melon|pumpkin|carved_pumpkin|'
                       r'red_mushroom_block|brown_mushroom_block|mushroom_stem|nether_wart_block|warped_wart_block|'
                       r'netherrack|soul_sand|soul_soil|tinted_glass|clay|mud)$|^(copper_block|cut_copper|exposed_|'
                       r'weathered_|oxidized_|cut_copper_)|^cut_copper|_copper$|_copper_(slab|stairs)$|_command_block$|'
                       r'^command_block$|^(lightning_rod|chain)$|^glazed|_glazed_terracotta$')
# waxed copper never weathers, so it is the only copper allowed (weathering would silently change the planned state)
WAXED_OK = re.compile(r'^waxed_')
GLASS = re.compile(r'(^|_)glass(_pane)?$')
CLEAR_GLASS = {'glass', 'glass_pane'}
PARTIAL = re.compile(r'_(slab|stairs|wall|trapdoor|fence|fence_gate|pane)$|^(iron_bars|end_rod|chain)$')


def short(bid):
    return bid.split('[')[0].replace('minecraft:', '')


def kind(name):
    """coarse shape kind of a block id (no namespace)"""
    if GLASS.search(name):
        return 'pane' if name.endswith('_pane') else 'glass'
    for k in ('slab', 'stairs', 'wall', 'trapdoor', 'fence_gate', 'fence', 'door'):
        if name.endswith('_' + k):
            return k
    if name in ('iron_bars', 'end_rod', 'chain'):
        return name
    return 'full'


def unsafe(name):
    """why a block is never used as structural material ('' = ok)"""
    if WAXED_OK.match(name):
        return ''
    for tag, rx in (('gravity', GRAVITY), ('block-entity', BLOCK_ENTITY), ('unstable', UNSTABLE),
                    ('light', LIGHT), ('excluded', OTHER_BAD)):
        if rx.search(name):
            return tag
    return ''


# role -> allowed coarse kinds. Glass roles: glass/pane only. Structural roles: solid, stable, full or partial.
ROLE_KINDS = {
    'glass': {'glass', 'pane'},
    'mullion': {'glass', 'pane', 'end_rod', 'iron_bars', 'fence', 'full'},   # Detail pp.3-4; light ok for end_rod only
    'frame': {'trapdoor', 'slab', 'end_rod', 'fence_gate', 'iron_bars', 'full'},  # Detail pp.10-15
    'door': {'door'},
    'wall': {'full'}, 'backing': {'full'}, 'roof': {'full', 'slab'}, 'sign': {'full'},
    'band': {'full', 'slab', 'stairs'}, 'parapet': {'full', 'slab', 'stairs', 'wall'},
    'roof-edge': {'full', 'slab', 'stairs', 'wall'}, 'trim': {'full', 'slab', 'stairs', 'trapdoor'},
    'sill': {'slab', 'stairs', 'trapdoor', 'full'}, 'canopy': {'slab', 'trapdoor', 'full'},
    'column': {'full', 'wall', 'fence', 'slab', 'stairs', 'end_rod', 'iron_bars'},  # Detail pp.28-29
}


def role_ok(role, name):
    """(ok, reason) - may block `name` (no namespace/state) fill `role`?"""
    k = kind(name)
    allowed = ROLE_KINDS.get(role)
    if allowed is None:
        return False, 'unknown role'
    if k not in allowed:
        return False, f'kind {k} not allowed for {role}'
    if role in ('glass',) and name in CLEAR_GLASS:
        return False, 'clear glass alone does not read as glazing (Detail pp.4-5)'
    if name == 'tinted_glass':
        return False, 'tinted glass is near-opaque black'
    if k in ('glass', 'pane'):
        return (True, '') if role in ('glass', 'mullion') else (False, 'glass only in glass/mullion roles')
    if name == 'end_rod' and role in ('mullion', 'frame', 'column'):
        return True, ''        # the guide's thin divider; its light is accepted (Detail pp.3-4, 11)
    why = unsafe(name)
    return (False, why) if why else (True, '')


# ---------------------------------------------------------------- block-state parsing
def parse_state(s):
    m = re.fullmatch(r'(minecraft:)?([a-z0-9_]+)(\[([^\]]*)\])?', s)
    if not m:
        raise ValueError(f'bad block string {s!r}')
    props = dict(kv.split('=', 1) for kv in m.group(4).split(',')) if m.group(4) else {}
    return m.group(2), props


def check_state(s, blocks):
    """'' if s (placeholders substituted) is a valid 1.18.2 block state, else the error"""
    for k, v in PLACEHOLDERS.items():
        s = s.replace(k, v)
    try:
        name, props = parse_state(s)
    except ValueError as e:
        return str(e)
    if 'minecraft:' + name not in blocks:
        return f'unknown block {name}'
    spec = blocks['minecraft:' + name]
    for k, v in props.items():
        if k not in spec:
            return f'{name} has no property {k}'
        if v not in spec[k]:
            return f'{name}[{k}={v}] not in {spec[k]}'
    return ''


def resolve_cell(cell, roles, blocks):
    """block string for a component cell under a palette layer: explicit block, or role block with cell props merged in
    only where that block has the property (COMPONENTS.md, format note 1). Placeholders are left for the compiler."""
    if 'block' in cell:
        return cell['block']
    name, props = parse_state(roles[cell['role']])
    spec = blocks['minecraft:' + name]
    props.update({k: v for k, v in cell.get('props', {}).items() if k in spec})
    return 'minecraft:' + name + ('[' + ','.join(f'{k}={v}' for k, v in props.items()) + ']' if props else '')


def load_blocks():
    with open(BLOCKS) as f:
        return json.load(f)['blocks']


# ---------------------------------------------------------------- task 1: blocks-1.18.2.json
def build_blocks(report_path, out=BLOCKS):
    rep = json.load(open(report_path))
    blocks = {k: v.get('properties', {}) for k, v in sorted(rep.items())}
    stateful = {}
    for bid, props in blocks.items():
        n = short(bid)
        cats = []
        if kind(n) in ('pane', 'fence', 'wall', 'iron_bars'):
            cats.append('connecting')
        if 'half' in props and set(props['half']) == {'upper', 'lower'} or n.endswith('_bed') or n in ('tall_grass', 'large_fern', 'sunflower', 'lilac', 'rose_bush', 'peony', 'tall_seagrass', 'small_dripleaf'):
            cats.append('multiblock')
        if 'shape' in props and n.endswith('_stairs'):
            cats.append('stairs-shape')
        if n.endswith('_slab'):
            cats.append('slab')
        if n.endswith('_trapdoor'):
            cats.append('trapdoor')
        if n.endswith('_leaves'):
            cats.append('leaves')
        if GRAVITY.search(n):
            cats.append('gravity')
        if BLOCK_ENTITY.search(n):
            cats.append('block-entity')
        if n.endswith('_door'):
            cats.append('door')
        for c in cats:
            stateful.setdefault(c, []).append(bid)
    doc = {'version': 'facade-blocks-1', 'minecraft': '1.18.2',
           'source': 'net.minecraft.data.Main --reports on the official 1.18.2 server jar (reports/blocks.json)',
           'count': len(blocks), 'blocks': blocks, 'stateful': stateful}
    with open(out, 'w', newline='\n') as f:
        json.dump(doc, f, separators=(',', ':'), sort_keys=False)
    return doc


# ---------------------------------------------------------------- task 2: colours
def srgb_to_lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


class Jar:
    """read-only access to a 1.18.2 client jar: blockstate -> model -> facade-facing texture"""
    KEYS = ('side', 'pane', 'all', 'texture', 'wall', 'end', 'cross', 'bottom', 'particle')

    def __init__(self, path):
        self.z = zipfile.ZipFile(path)
        self.names = set(self.z.namelist())

    def _json(self, p):
        return json.loads(self.z.read(p)) if p in self.names else None

    def _model(self, ref):
        ref = ref.replace('minecraft:', '')
        tex, tinted = {}, False
        while ref:
            m = self._json(f'assets/minecraft/models/{ref}.json')
            if m is None:
                break
            for k, v in m.get('textures', {}).items():
                tex.setdefault(k, v)
            tinted = tinted or any('tintindex' in f for e in m.get('elements', []) for f in e.get('faces', {}).values())
            ref = m.get('parent', '').replace('minecraft:', '')
        return tex, tinted

    def texture(self, name):
        """(RGBA uint8 16x16 array, tinted) of the side texture of block `name`, or None"""
        bs = self._json(f'assets/minecraft/blockstates/{name}.json')
        if not bs:
            return None
        if 'variants' in bs:
            v = next(iter(bs['variants'].values()))
        else:
            v = bs['multipart'][0]['apply']
        v = v[0] if isinstance(v, list) else v
        tex, tinted = self._model(v['model'])
        for k in self.KEYS:
            t = tex.get(k)
            for _ in range(5):          # resolve '#ref' chains
                if t and t.startswith('#'):
                    t = tex.get(t[1:])
            if t and not t.startswith('#'):
                p = f"assets/minecraft/textures/{t.replace('minecraft:', '')}.png"
                if p in self.names:
                    from PIL import Image
                    im = np.asarray(Image.open(io.BytesIO(self.z.read(p))).convert('RGBA'))
                    return im[:im.shape[1]], tinted        # first animation frame
        return None


def build_colours(jar_path, out=COLOURS):
    blocks = load_blocks()
    jar = Jar(jar_path)
    rows = {}
    for bid in blocks:
        r = jar.texture(short(bid))
        if r is None:
            continue
        im, tinted = r
        a = im[..., 3].astype(float) / 255
        if a.sum() == 0:
            continue
        rgb = (im[..., :3] * a[..., None]).reshape(-1, 3).sum(0) / a.sum()
        lab = srgb_to_lab(rgb)
        rows[short(bid)] = [int(round(x)) for x in rgb] + [round(float(x), 1) for x in lab] + [round(float(a.mean()), 2), int(tinted)]
    doc = {'version': 'facade-colours-1', 'minecraft': '1.18.2',
           'method': 'alpha-weighted mean sRGB of the first frame of the side-facing texture (blockstate first variant '
                     '-> model chain -> first of side/pane/all/texture/wall/end/cross/bottom/particle); CIELAB D65. '
                     'Tinted textures (grass, leaves) are untinted greys: tinted=1.',
           'columns': ['r', 'g', 'b', 'L', 'a', 'b*', 'coverage', 'tinted'], 'count': len(rows), 'blocks': rows}
    with open(out, 'w', newline='\n') as f:
        f.write('{' + ',\n'.join(f'{json.dumps(k)}:{json.dumps(v)}' for k, v in doc.items() if k != 'blocks'))
        f.write(',\n"blocks":{\n' + ',\n'.join(f'{json.dumps(k)}:{json.dumps(v)}' for k, v in sorted(rows.items())) + '}}\n')
    return doc


def load_colours():
    with open(COLOURS) as f:
        return json.load(f)['blocks']


def hex_rgb(h):
    h = h.lstrip('#')
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def nearest(target, role, k=8, colours=None):
    """k nearest blocks (CIE76 dE in Lab) to target '#rrggbb' that are allowed for `role`"""
    colours = colours or load_colours()
    t = srgb_to_lab(hex_rgb(target))
    out = []
    for name, row in colours.items():
        if row[7] or not role_ok(role, name)[0]:
            continue
        if kind(name) == 'full' and row[6] < 0.99 and role not in ('glass', 'mullion'):
            continue                  # see-through full blocks (spawner-like cages) never pass as solid
        out.append((float(np.linalg.norm(np.array(row[3:6]) - t)), name))
    return sorted(out)[:k]


# ---------------------------------------------------------------- lint of the asset library
def check_assets():
    """list of 'path: error' for every palette, component and style"""
    blocks = load_blocks()
    errs = []
    pals = {}
    for fn in sorted(os.listdir(os.path.join(ASSETS, 'palettes'))):
        p = json.load(open(os.path.join(ASSETS, 'palettes', fn)))
        pals[p['id']] = p
        where = f'palettes/{fn}'
        if p.get('schema') != 'facade-palette-1' or p['id'] + '.json' != fn:
            errs.append(f'{where}: schema/id')
        layers = [('roles', p['roles'])] + [(f'zones.{z}', r) for z, r in p.get('zones', {}).items()]
        missing = set(PLAN_ROLES) - set(p['roles'])
        if missing:
            errs.append(f'{where}: roles missing {sorted(missing)}')
        for lname, roles in layers:
            for role, s in roles.items():
                e = check_state(s, blocks) or ('' if role_ok(role, parse_state(s)[0])[0] else role_ok(role, parse_state(s)[0])[1])
                if e:
                    errs.append(f'{where}: {lname}.{role} {s}: {e}')
            merged = {**p['roles'], **roles}
            g, b = short(merged['glass']), short(merged['backing'])
            if b == 'black_concrete' and g in ('gray_stained_glass', 'black_stained_glass', 'gray_stained_glass_pane', 'black_stained_glass_pane'):
                errs.append(f'{where}: {lname} black backing behind gray/black glass (Detail p.5)')
            if b in ('ice', 'packed_ice', 'blue_ice'):
                errs.append(f'{where}: {lname} ice backing (Detail p.5)')
        for role, t in p.get('texture', {}).items():
            if not TEXTURE_RULE.match(t.get('rule', '')):
                errs.append(f'{where}: texture.{role} rule {t.get("rule")!r} not whitelisted')
            e = check_state(t['alt'], blocks) or ('' if role_ok(role, short(t['alt']))[0] else 'alt not allowed for role')
            if e:
                errs.append(f'{where}: texture.{role} {e}')
    comps = set()
    for fn in sorted(os.listdir(os.path.join(ASSETS, 'components'))):
        path = os.path.join(ASSETS, 'components', fn)
        where = f'components/{fn}'
        if os.path.getsize(path) > 64 * 1024:
            errs.append(f'{where}: > 64 KB')
        c = json.load(open(path))
        comps.add(c['id'])
        if c.get('schema') != 'facade-component-1' or c['id'] + '.json' != fn or c.get('license') != 'original (Moria)':
            errs.append(f'{where}: schema/id/license')
        if not re.search(r'p\.\d', c.get('notes', '')) and 'glyphs' not in c:
            errs.append(f'{where}: notes without a page cite')
        seen = set()
        for cell in c.get('cells', []):
            key = (cell['u'], cell['v'], cell['w'])
            if key in seen:
                errs.append(f'{where}: duplicate cell {key}')
            seen.add(key)
            if ('role' in cell) == ('block' in cell):
                errs.append(f'{where}: cell {key} needs exactly one of role/block')
            if cell.get('role') and cell['role'] not in PLAN_ROLES:
                errs.append(f'{where}: cell {key} role {cell["role"]}')
            if cell.get('block'):
                e = check_state(cell['block'], blocks)
                if e:
                    errs.append(f'{where}: cell {key} {e}')
                if kind(short(cell['block'])) in ('pane', 'fence', 'wall', 'iron_bars') and '[' in cell['block']:
                    errs.append(f'{where}: cell {key} connecting block must not carry connection states')
        for cell in c.get('cells', []):
            if not (0 <= cell['u'] < c['size']['u'] and 0 <= cell['v'] < c['size']['v'] and -2 <= cell['w'] <= 4):
                errs.append(f'{where}: cell {(cell["u"], cell["v"], cell["w"])} outside size/depth')
        for ch, rows in c.get('glyphs', {}).items():
            if len(rows) != c['glyph_size']['v'] or any(len(r) != c['glyph_size']['u'] or set(r) - {'0', '1'} for r in rows):
                errs.append(f'{where}: glyph {ch!r} malformed')
    compdocs = {json.load(open(os.path.join(ASSETS, 'components', fn)))['id']: json.load(open(os.path.join(ASSETS, 'components', fn)))
                for fn in os.listdir(os.path.join(ASSETS, 'components'))}
    for pid, p in pals.items():          # every component resolves to valid states under every palette layer
        for lname, roles in [('roles', p['roles'])] + [(f'zones.{z}', {**p['roles'], **r}) for z, r in p.get('zones', {}).items()]:
            for cid, cd in compdocs.items():
                for cell in cd['cells']:
                    e = check_state(resolve_cell(cell, roles, blocks), blocks)
                    if e:
                        errs.append(f'{cid} under {pid}.{lname}: {e}')
    for fn in sorted(os.listdir(os.path.join(ASSETS, 'styles'))):
        s = json.load(open(os.path.join(ASSETS, 'styles', fn)))
        where = f'styles/{fn}'
        if s.get('schema') != 'facade-style-1' or s.get('kind') != 'style' or 'STYLE-GENERATED' not in s.get('label', ''):
            errs.append(f'{where}: schema/kind/label')
        if s['palette'].replace('lib:', '') not in pals:
            errs.append(f'{where}: palette {s["palette"]}')
        for name, ref in s['components'].items():
            if ref['ref'].replace('lib:', '') not in comps:
                errs.append(f'{where}: component {name} -> {ref["ref"]}')
        used = re.findall(r'"component":\s*"([^"]+)"', json.dumps(s))
        for u in used:
            if u not in s['components']:
                errs.append(f'{where}: layout uses undeclared component {u}')
        mull = kind(short(pals[s['palette'].replace('lib:', '')]['roles']['mullion']))
        for name, ref in s['components'].items():
            cid = ref['ref'].replace('lib:', '')
            if mull not in ('glass', 'pane') and (cid in ('storefront-bay', 'curtain-wall-bay') or cid.endswith('-grid') or 'ribbon' in cid):
                errs.append(f'{where}: {cid} needs a glass mullion, palette has {mull}')
        sh = s['defaults'].get('shared', {})
        if sh != {'wall_only': True}:
            errs.append(f'{where}: shared edge must be wall_only')
    return errs


# ---------------------------------------------------------------- review sheets
def _tile(jar, colours, state, px=24):
    from PIL import Image
    name = short(state)
    r = jar.texture(name) if jar else None
    if r is not None:
        im = Image.fromarray(r[0]).resize((px, px), Image.NEAREST)
        bg = Image.new('RGBA', (px, px), (40, 44, 52, 255))   # backing-ish dark behind translucent glass
        bg.alpha_composite(im)
        if name.endswith('_slab') and 'type=top' in state:
            bg.paste((0, 0, 0, 0), (0, px // 2, px, px))
        if name.endswith('_slab') and 'type=top' not in state:
            bg.paste((0, 0, 0, 0), (0, 0, px, px // 2))
        return bg
    row = colours.get(name)
    return Image.new('RGBA', (px, px), tuple(row[:3]) + (255,) if row else (255, 0, 255, 255))


MOCKUP = [  # 18 wide x 12 high elevation, top row first. Letters -> roles. Storefront base, opaque band, punched windows.
    'pppppppppppppppppp',
    'PPPPPPPPPPPPPPPPPP',
    'wwwwwwwwwwwwwwwwww',
    'wwtttwwwtttwwwtttw',
    'wwgmgwwwgmgwwwgmgw',
    'wwgmgwwwgmgwwwgmgw',
    'wwssswwwssswwwsssw',
    'bbbbbbbbbbbbbbbbbb',
    'cccccccccccccccccc',
    'CgmgmgmgDDgmgmgmgC',
    'CgmgmgmgDDgmgmgmgC',
    'CffffffffffffffffC',
]
MOCK_ROLES = {'p': 'roof-edge', 'P': 'parapet', 'w': 'wall', 't': 'trim', 'g': 'glass', 'm': 'mullion', 's': 'sill',
              'b': 'band', 'c': 'canopy', 'C': 'column', 'D': 'door', 'f': 'wall'}


def render_sheet(pal, out_png, jar=None, colours=None):
    from PIL import Image, ImageDraw
    colours = colours or load_colours()
    px = 24
    layers = [('base', pal['roles'])] + [(z, {**pal['roles'], **r}) for z, r in pal.get('zones', {}).items()]
    w = max(18 * px + 40, 640)
    h = 60 + len(layers) * (len(MOCKUP) * px + 40) + len(PLAN_ROLES) * (px + 6) * len(layers) + 40
    img = Image.new('RGBA', (w, h), (245, 245, 240, 255))
    d = ImageDraw.Draw(img)
    d.text((10, 10), f"{pal['id']} v{pal['version']}  {pal.get('status', '')}", fill=(0, 0, 0, 255))
    d.text((10, 26), 'unshaded review sheet; Bliss-shader comparison is an in-game step', fill=(90, 90, 90, 255))
    y = 50
    for lname, roles in layers:
        d.text((10, y), f'zone: {lname}', fill=(0, 0, 0, 255))
        y += 16
        for role in PLAN_ROLES:
            s = roles.get(role, '')
            if s:
                img.alpha_composite(_tile(jar, colours, s, px), (10, y))
                row = colours.get(short(s))
                hx = '#%02x%02x%02x' % tuple(row[:3]) if row else '?'
                d.text((10 + px + 8, y + 6), f'{role:10s} {s.replace("minecraft:", "")}  {hx}', fill=(0, 0, 0, 255))
            y += px + 6
        for r, line in enumerate(MOCKUP):
            for c, ch in enumerate(line):
                s = roles[MOCK_ROLES[ch]]
                t = pal.get('texture', {}).get(MOCK_ROLES[ch])
                m = re.fullmatch(r'(rows|cols)%(\d+)==(\d+)', t['rule']) if t else None
                if m and ((len(MOCKUP) - 1 - r) if m[1] == 'rows' else c) % int(m[2]) == int(m[3]):
                    s = t['alt']      # hash() rules are not drawn; the compiler owns the hash
                if MOCK_ROLES[ch] == 'door':
                    s = roles['glass']   # door block textures are two-part; glass stands in for the leaf
                img.alpha_composite(_tile(jar, colours, s, px), (10 + c * px, y + r * px))
        y += len(MOCKUP) * px + 24
    img = img.crop((0, 0, w, y + 10))
    img.convert('RGB').save(out_png)
    return out_png


def render_all(out_dir, jar_path=None):
    os.makedirs(out_dir, exist_ok=True)
    jar = Jar(jar_path) if jar_path and os.path.exists(jar_path) else None
    colours = load_colours()
    outs = []
    for fn in sorted(os.listdir(os.path.join(ASSETS, 'palettes'))):
        pal = json.load(open(os.path.join(ASSETS, 'palettes', fn)))
        outs.append(render_sheet(pal, os.path.join(out_dir, pal['id'] + '.png'), jar, colours))
    return outs


# ---------------------------------------------------------------- self-check
def self_check():
    blocks = load_blocks()
    assert len(blocks) == 898, len(blocks)
    assert check_state('minecraft:oak_door[facing=north,half=upper,hinge=left,open=false,powered=false]', blocks) == ''
    assert check_state('minecraft:oak_door[half=middle]', blocks)
    assert check_state('minecraft:end_rod[facing={facing}]', blocks) == ''
    assert role_ok('glass', 'gray_stained_glass')[0] and not role_ok('glass', 'glass')[0]
    assert not role_ok('glass', 'white_concrete')[0] and not role_ok('wall', 'cyan_stained_glass')[0]
    for bad in ('sand', 'red_concrete_powder', 'oak_leaves', 'iron_ore', 'glowstone', 'sea_lantern', 'torch',
                'chest', 'oak_sign', 'copper_block', 'ice', 'gravel', 'oak_button', 'white_carpet', 'oak_door'):
        assert not role_ok('wall', bad)[0], bad
    assert role_ok('wall', 'waxed_cut_copper')[0] and role_ok('wall', 'smooth_sandstone')[0]
    assert role_ok('canopy', 'smooth_stone_slab')[0] and not role_ok('canopy', 'oak_fence')[0]
    assert role_ok('mullion', 'end_rod')[0] and not role_ok('wall', 'end_rod')[0]
    assert role_ok('door', 'iron_door')[0] and not role_ok('door', 'iron_trapdoor')[0]
    assert resolve_cell({'role': 'sill', 'props': {'type': 'top', 'half': 'top'}}, {'sill': 'minecraft:smooth_stone_slab'},
                        blocks) == 'minecraft:smooth_stone_slab[type=top]'
    assert TEXTURE_RULE.match('rows%3==2') and TEXTURE_RULE.match('hash(u,v,seed)<0.2') and not TEXTURE_RULE.match('rand()<.5')
    assert 'minecraft:oak_door' in sum(load_blocks_doc()['stateful'].values(), [])
    if os.path.exists(COLOURS):
        top = nearest('#3a3f45', 'glass', 3)
        assert all(kind(n) in ('glass', 'pane') for _, n in top), top
        assert all(role_ok('wall', n)[0] for _, n in nearest('#cdb38b', 'wall', 10))
        assert nearest('#ffffff', 'wall', 1)[0][1] in ('white_concrete', 'snow_block', 'white_wool', 'smooth_quartz', 'quartz_block', 'quartz_bricks', 'white_terracotta'), nearest('#ffffff', 'wall', 3)
    errs = check_assets() if os.path.isdir(os.path.join(ASSETS, 'palettes')) else []
    assert not errs, '\n'.join(errs)
    print('self-check ok')


def load_blocks_doc():
    with open(BLOCKS) as f:
        return json.load(f)


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        self_check()
    elif a[0] == 'blocks':
        print(build_blocks(a[1])['count'], 'blocks')
    elif a[0] == 'colours':
        print(build_colours(a[1])['count'], 'colours')
    elif a[0] == 'nearest':
        for dE, n in nearest(a[1], a[2], int(a[3]) if len(a) > 3 else 8):
            print(f'{dE:6.1f}  {n}')
    elif a[0] == 'check':
        e = check_assets()
        print('\n'.join(e) or 'assets ok')
        sys.exit(1 if e else 0)
    elif a[0] == 'sheets':
        for p in render_all(a[1], a[2] if len(a) > 2 else None):
            print(p)
    else:
        sys.exit(__doc__)
