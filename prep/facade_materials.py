"""facade_materials.py - autospec face colours -> 1.18.2 blocks (v0.6 auto facades), owner: agent V. Contract: AUTO.md 7.

  palette_for_face(face_section, cfg=None) -> {"roles": {role: block}, "zones": [zone + "block" (+ "alt", "rule")],
                                               "texture": {role: {"alt", "rule"}}, "matches": {role: {...}}, "derived": [...]}

Original lookup (no HueBlocks code or data): CIE76 dE in Lab against prep/facade_assets/colours-1.18.2.json, restricted per
role to suitable FULL blocks (glass: stained-glass blocks) through facade_palette_tools.role_ok, plus a name-based penalty
for noisy or patterned textures. Every emitted state passes facade_schema.check_block (1.18.2 validity + deny-list).
CLI: --selftest
"""
import json, os, re, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import facade_palette_tools as PT
import facade_schema as S

DEFAULTS = {'coherent_de': 6.0, 'dither': False, 'dither_min_area_m2': 60.0, 'dither_gain_de': 3.0, 'dither_pair_max_de': 12.0}
# zone/face role -> palette role used for the role rules (base is a wall-material zone)
RULE_ROLE = {'wall': 'wall', 'base': 'wall', 'band': 'band', 'parapet': 'parapet', 'trim': 'trim', 'glass': 'glass', 'frame': 'frame'}
# texture penalties in dE units. The colour table has no texture variance, so this is a name heuristic (ANALYSIS.md).
NOISY = re.compile(r'cobble|mossy|cracked|chiseled|bricks$|_log$|_wood$|_hyphae$|_stem$|bookshelf|nylium|netherrack|basalt|tuff|'
                   r'dripstone|sponge|hay_block|^(granite|diorite|andesite|blackstone|deepslate|calcite)$|gilded|lodestone|'
                   r'prismarine|purpur_pillar|magma|obsidian|_ore$|^raw_|crafting|loom|fletching|smithing|cartography|note_block|'
                   r'jukebox|target|bee|honey|slime|melon|pumpkin|mushroom|coral|sculk|moss|^clay$|mud|dirt|podzol|mycelium|'
                   r'grass|amethyst|bone_block|dried_kelp|shroomlight|_wart_block$|lapis_block|redstone_block|emerald_block|'
                   r'diamond_block|gold_block|netherite|copper|scaffold|tnt|sea_lantern|glowstone|^snow|ice$|sand$|gravel')
MILD = re.compile(r'_planks$|_wool$|end_stone|quartz_pillar|quartz_bricks|purpur_block|_terracotta$|^terracotta$|^stone$')
NOISY_PEN, MILD_PEN = 12.0, 3.0


def penalty(name):
    return NOISY_PEN if NOISY.search(name) else MILD_PEN if MILD.search(name) else 0.0


_CAND = {}
def candidates(role):
    """[(name, lab ndarray, penalty)] of blocks allowed for this face role"""
    if role in _CAND: return _CAND[role]
    rr = RULE_ROLE[role]; out = []
    for name, row in PT.load_colours().items():
        if row[7]: continue                                            # tinted (grass/leaves) colours are meaningless
        if not PT.role_ok(rr, name)[0]: continue
        k = PT.kind(name)
        if role == 'glass':
            if k != 'glass': continue                                   # stained-glass blocks only (no panes: no connection states)
        elif k != 'full' or row[6] < 0.99:
            continue                                                    # full, opaque blocks only
        if S.check_block('minecraft:' + name): continue                 # 1.18.2 validity + deny-list
        out.append((name, np.array(row[3:6], float), penalty(name)))
    _CAND[role] = out
    return out


def match(rgb, role, k=8):
    """k best (score, dE, name) for target sRGB; score = dE + texture penalty"""
    t = PT.srgb_to_lab(rgb)
    rows = [(float(np.linalg.norm(lab - t)) + pen, float(np.linalg.norm(lab - t)), name) for name, lab, pen in candidates(role)]
    return sorted(rows)[:k]


def _lab(name):
    return np.array(PT.load_colours()[name][3:6], float)


def _dither(rgb, role, best, c):
    """2-block 50/50 coursing (rows%2==1) when it beats the single block by >= dither_gain_de; pair must be similar"""
    t = PT.srgb_to_lab(rgb); top = match(rgb, role, 8); res = None
    for i, (_, _, a) in enumerate(top):
        for _, _, b in top[i + 1:]:
            la, lb = _lab(a), _lab(b)
            if np.linalg.norm(la - lb) > c['dither_pair_max_de']: continue
            de = float(np.linalg.norm((la + lb) / 2 - t)) + max(penalty(a), penalty(b))
            if de + c['dither_gain_de'] <= best[0] and (res is None or de < res[0]): res = (de, a, b)
    return res


def palette_for_face(face, cfg=None):
    c = dict(DEFAULTS); c.update(cfg or {})
    zones = face.get('zones') or []
    if not zones or zones[0]['role'] != 'wall': raise ValueError('face has no observed wall zone')
    wall_rgb = zones[0]['rgb']; wall_lab = PT.srgb_to_lab(wall_rgb)
    roles, texture, matches, out_zones, derived = {}, {}, {}, [], []
    wall_block = None
    for z in zones:
        role = z['role']
        best = match(z['rgb'], role, 1)[0]
        zb = dict(z)
        if role != 'wall' and wall_block and float(np.linalg.norm(PT.srgb_to_lab(z['rgb']) - wall_lab)) < c['coherent_de']:
            zb['block'] = wall_block                                    # coherent: near-wall colours stay the wall block
        else:
            zb['block'] = 'minecraft:' + best[2]
            area = (z['u1_m'] - z['u0_m']) * (z['v1_m'] - z['v0_m'])
            if c['dither'] and role in ('wall', 'base') and area >= c['dither_min_area_m2']:
                d = _dither(z['rgb'], role, best, c)
                if d:
                    zb['block'], zb['alt'], zb['rule'] = 'minecraft:' + d[1], 'minecraft:' + d[2], 'rows%2==1'
        if wall_block is None: wall_block = zb['block']
        out_zones.append(zb)
        pr = 'wall' if role == 'base' else role
        if pr not in roles and role != 'base':
            roles[pr] = zb['block']
            if 'alt' in zb: texture[pr] = {'alt': zb['alt'], 'rule': zb['rule']}
            matches[pr] = {'block': zb['block'], 'delta_e': round(best[1], 1), 'target_rgb': z['rgb']}
    gl = face.get('glazing')
    if gl and gl.get('fraction', 0) > 0:
        g = match(gl['rgb'], 'glass', 1)[0]
        roles['glass'] = 'minecraft:' + g[2]; matches['glass'] = {'block': roles['glass'], 'delta_e': round(g[1], 1), 'target_rgb': gl['rgb']}
    # frame: never observed directly at 4 px/m; derived as a darker tone of the glazing (or wall) and labelled so
    base = gl['rgb'] if gl and gl.get('fraction', 0) > 0 else wall_rgb
    f = match([int(x * 0.6) for x in base], 'frame', 1)[0]
    roles['frame'] = 'minecraft:' + f[2]; derived.append('frame')
    states = list(roles.values()) + [t['alt'] for t in texture.values()] + [z['block'] for z in out_zones] + [z['alt'] for z in out_zones if 'alt' in z]
    bad = [(s, S.check_block(s)) for s in states if S.check_block(s)]
    if bad: raise AssertionError(f'invalid block states {bad}')
    return {'roles': roles, 'zones': out_zones, 'texture': texture, 'matches': matches, 'derived': derived}


def palettes(spec, g=None, cfg=None):
    """facade_prep stage: {face_id: palette_for_face(...)} for every observed face with a wall zone; a face that cannot be
    matched is left out (prep then uses the fallback style palette for it)"""
    out = {}
    for fid, f in sorted((spec.get('faces') or {}).items()):
        if f.get('status') == 'observed' and f.get('zones'):
            try: out[fid] = palette_for_face(f, cfg)
            except ValueError: pass
    return out


def selftest():
    # exact table colours map back to themselves for structural roles
    for name in ('white_concrete', 'smooth_sandstone', 'gray_concrete', 'light_gray_concrete', 'terracotta'):
        rgb = PT.load_colours()[name][:3]
        top = match(rgb, 'wall', 3)
        assert top[0][1] < 3.0, (name, top)
    # noisy textures lose to a smooth block of similar colour
    rgb = PT.load_colours()['cobblestone'][:3]
    assert 'cobblestone' not in match(rgb, 'wall', 1)[0][2]
    # no leaves, glass, panes, gravity or denied blocks for the wall; glass only stained-glass blocks
    for role in ('wall', 'base', 'band', 'parapet', 'trim', 'frame'):
        for name, _, _ in candidates(role):
            assert PT.kind(name) == 'full' and not re.search(r'leaves|glass|sand$|gravel|concrete_powder|_ore$', name), (role, name)
    assert all(n.endswith('stained_glass') for n, _, _ in candidates('glass')), [n for n, _, _ in candidates('glass')]
    face = {'zones': [{'v0_m': 0, 'v1_m': 16, 'u0_m': 0, 'u1_m': 24, 'role': 'wall', 'rgb': [205, 185, 145], 'conf': 0.9},
                      {'v0_m': 0, 'v1_m': 0.6, 'u0_m': 0, 'u1_m': 24, 'role': 'base', 'rgb': [122, 120, 114], 'conf': 0.7},
                      {'v0_m': 15, 'v1_m': 16, 'u0_m': 0, 'u1_m': 24, 'role': 'parapet', 'rgb': [238, 230, 212], 'conf': 0.7},
                      {'v0_m': 8, 'v1_m': 8.3, 'u0_m': 0, 'u1_m': 24, 'role': 'band', 'rgb': [207, 186, 146], 'conf': 0.5}],
            'glazing': {'fraction': 0.4, 'rgb': [55, 70, 92], 'conf': 0.8}}
    p = palette_for_face(face)
    assert p['zones'][3]['block'] == p['roles']['wall'], 'near-wall band must stay coherent with the wall'
    assert p['roles']['glass'].endswith('stained_glass') and 'frame' in p['derived']
    pd = palette_for_face(face, {'dither': True, 'dither_gain_de': 0.0})
    for z in pd['zones']:
        if 'alt' in z: assert z['rule'] == 'rows%2==1' and not S.check_block(z['alt'])
    out = {'roles': p['roles'], 'matches': p['matches'], 'dither_wall': {k: pd['zones'][0].get(k) for k in ('block', 'alt', 'rule')}}
    print(json.dumps(out, indent=1))
    print('facade_materials selftest ok')


if __name__ == '__main__':
    selftest()
