"""ov_preview.py <plan.json> <region_dir> <out_dir>: read-only preview of a crossing plan over the existing world.
Writes section_long.png (along the upper road, through the deck median), section_cross_<lower>.png (along each lower road
centreline) and top.png. Planned blocks are drawn in role colours with a black outline; world blocks are dimmed."""
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, r'C:\BTE-Migration\scripts')
from anvil_tools import World
from inspect_building import color

ROLE = {'deck': (90, 90, 96), 'deck-soffit': (205, 205, 215), 'parapet': (240, 240, 120), 'median': (250, 200, 60),
        'abutment': (190, 120, 220), 'pier': (230, 80, 80), 'approach-fill': (150, 130, 110), 'approach-wall': (205, 205, 215),
        'approach-surface': (60, 60, 66), 'approach-barrier': (240, 240, 120)}
EXT = int(os.environ.get('OV_PREVIEW_EXT', '30'))          # how far the long section runs beyond the deck
plan, R, OUT = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True); W = World(R)
cells = {}
for x, z, y0, y1, b, role, fid, lab in plan['cells']:
    for y in range(y0, y1 + 1): cells[(x, y, z)] = role
S = 8
deck_cols = {(c[0], c[1]) for c in plan['cells'] if c[5] == 'deck'}
END = {(x, z) for e in plan.get('deck_ends', []) for (x, z) in deck_cols if e['x'][0] <= x <= e['x'][1] and e['z'][0] <= z <= e['z'][1]
       and any((x + dx, z + dz) not in deck_cols for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
END_COL = (230, 30, 30)
HAS_APPROACH = any(c[5].startswith('approach') for c in plan['cells'])
END_TXT = 'deck ends (connected by raised approaches)' if HAS_APPROACH else 'open deck ends (approach UNRESOLVED, not connected)'


def section(path, line, ymin, ymax, title, notes):
    """line: list of (x, z) columns in walking order"""
    n = len(line); img = Image.new('RGB', (n * S + 60, (ymax - ymin + 1) * S + 40 + 14 * len(notes)), (255, 255, 255)); d = ImageDraw.Draw(img)
    for i, (x, z) in enumerate(line):
        col = dict(W.column(x, z))
        for y in range(ymin, ymax + 1):
            px, py = 50 + i * S, 20 + (ymax - y) * S
            if (x, y, z) in cells:
                d.rectangle([px, py, px + S - 1, py + S - 1], fill=ROLE[cells[(x, y, z)]], outline=END_COL if (x, z) in END and cells[(x, y, z)] == 'deck' else (0, 0, 0), width=2 if (x, z) in END else 1)
            elif y in col:
                c = color(col[y]); d.rectangle([px, py, px + S - 1, py + S - 1], fill=tuple(int(v * 0.45 + 140) for v in c))
    for y in range(ymin, ymax + 1, 2): d.text((4, 20 + (ymax - y) * S - 4), f'Y{y}', fill=(0, 0, 0))
    d.text((50, 2), title, fill=(0, 0, 0))
    for k, t in enumerate(notes): d.text((50, 26 + (ymax - ymin + 1) * S + 14 * k), t, fill=(0, 0, 0))
    img.save(path)


deck = {(c[0], c[1]): c[2] for c in plan['cells'] if c[5] == 'deck'}
med = sorted((c[0], c[1]) for c in plan['cells'] if c[5] == 'median')
xs = [m[0] for m in med]; zs = [m[1] for m in med]; k = np.polyfit(xs, zs, 1)
line = [(x, int(round(np.polyval(k, x)))) for x in range(min(xs) - EXT, max(xs) + EXT + 1)]
appr = 'raised approaches planned (see approach cells)' if HAS_APPROACH else '; '.join(f"{a['way']} {a['end']}: deck {a['deck_end_y']} vs world {a['world_approach_y']} ({a['step_blocks']:+d}) {a['status'].upper()}" for a in plan['approaches'][:2])
section(os.path.join(OUT, 'section_long.png'), line, 928, 950, 'LONG SECTION along W Charleston (deck median line), west -> east. Planned = outlined colours; world = pale',
        ['deck grey (red outline = open deck END, approach UNRESOLVED) | soffit pale (2 blocks PROVISIONAL) | median orange (imagery) | parapet yellow (PROVISIONAL) | abutment purple (PROVISIONAL) | piers OFF',
         'Freeway below = existing world surface (accepted baseline, not lowered). Deck Y = world lower road Y + measured separation (vertical adjustment).',
         'Approaches: ' + appr])
for l in plan['lower']:
    if l.get('status') != 'measured': continue
    cx, cz = l['crossing_mc']
    other = [m for m in plan['lower'] if m is not l and m.get('crossing_mc')]
    zr = [z for (x, z) in deck if abs(x - cx) <= 1]; z0, z1 = min(zr) - 25, max(zr) + 25
    lline = [(cx, z) for z in range(z0, z1 + 1)]
    cl = next((c for c in plan['clearance'] if c['lower'] == l['id']), {})
    section(os.path.join(OUT, f"section_cross_{l['id']}.png"), lline, 928, 950,
            f"CROSS SECTION along CC-215 ({l['id']}) at x={cx}, north -> south: deck width, parapets, median, clearance",
            [f"Lower road world surface Y{l['world_block_y']} (existing, unchanged). Measured top-to-top separation {cl.get('measured_top_to_top_m')} m -> "
             f"{cl.get('min_air_blocks')}-{cl.get('max_air_blocks')} air blocks under the 2-block deck.",
             f"Lower road LiDAR {l['surface_m']} m, interpolated across {l['interpolated_span_m']} m under the deck (both sides agree within {l['fit_mismatch_m']} m)."])
# top view
allc = list(deck) + [(c[0], c[1]) for c in plan['cells']]
x0, x1 = min(c[0] for c in allc) - 30, max(c[0] for c in allc) + 30; z0, z1 = min(c[1] for c in allc) - 20, max(c[1] for c in allc) + 20
T = 4; img = Image.new('RGB', ((x1 - x0 + 1) * T, (z1 - z0 + 1) * T + 20), (255, 255, 255)); d = ImageDraw.Draw(img)
top_role = {}
for x, z, y0, y1, b, role, fid, lab in plan['cells']:
    pr = ['approach-fill', 'approach-wall', 'pier', 'abutment', 'deck-soffit', 'approach-surface', 'deck', 'approach-barrier', 'parapet', 'median']
    if (x, z) not in top_role or pr.index(role) > pr.index(top_role[(x, z)]): top_role[(x, z)] = role
for x in range(x0, x1 + 1):
    for z in range(z0, z1 + 1):
        if (x, z) in END: c = END_COL
        elif (x, z) in top_role: c = ROLE[top_role[(x, z)]]
        else: y, nm = W.top(x, z); c = tuple(int(v * 0.45 + 140) for v in color(nm)) if nm else (255, 255, 255)
        d.rectangle([(x - x0) * T, (z - z0) * T, (x - x0) * T + T - 1, (z - z0) * T + T - 1], fill=c)
d.text((4, (z1 - z0 + 1) * T + 4), 'TOP VIEW (north up). RED = ' + END_TXT + '. Dark = planned surface. Pale = existing world. Charleston W-E, CC-215 N-S underneath.', fill=(0, 0, 0))
img.save(os.path.join(OUT, 'top.png')); print('written to', OUT)
# pier line section + conflict pre-check against the current world (read-only)
pier = sorted((c[0], c[1]) for c in plan['cells'] if c[5] == 'pier')
if len(pier) >= 2:
    k = np.polyfit([p[1] for p in pier], [p[0] for p in pier], 1)
    pl = [(int(round(np.polyval(k, z))), z) for z in range(min(p[1] for p in pier) - 25, max(p[1] for p in pier) + 26)]
    section(os.path.join(OUT, 'section_pier_line.png'), pl, 928, 950, 'SECTION along the CC-215 median (pier line), north -> south',
            [f"{len(pier)} pier columns, red: INFERRED positions (no LiDAR returns under the deck), spacing ~6 blocks ESTIMATE; from the world surface up to the soffit."])
conf = {}
WALL = ('pier', 'abutment')                       # plugin mode W: built top-down from the soffit until the real ground (no ground assumption)
for x, z, y0, y1, b, role, fid, lab in plan['cells']:
    if role in WALL:
        for y in range(y1, y0 - 4, -1):
            n = W.block(x, y, z)
            if n and 'air' not in n:
                if y > y0: conf.setdefault(role, []).append((x, y, z, n, f'stops {y - y0 + 1} above expected ground'))
                break
        continue
    for y in range(y0, y1 + 1):
        n = W.block(x, y, z)
        if n and 'air' not in n: conf.setdefault(role, []).append((x, y, z, n))
rep = {r: dict(count=len(v), examples=v[:5]) for r, v in conf.items()}
json.dump(rep, open(os.path.join(OUT, 'conflicts_vs_world.json'), 'w'), indent=1)
print('planned blocks', len(cells), '| non-air in the current world:', {r: v['count'] for r, v in rep.items()} or 'none')
for r, v in rep.items(): print(' ', r, v['examples'][:3])
