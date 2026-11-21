"""facade_compile.py - compile(geometry, selection, assets, seed) -> facade-plan-1 (ARCHITECTURE.md section 4).
Pure and deterministic: no I/O, no clock, no randomness beyond det(). Algorithms, priorities and connection rules are
documented in docs/facade/COMPILER-SPEC.md (keep the two in step). Owner: D1."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facade_common as F
import facade_schema as S
import facade_palette_tools as PT          # resolve_cell() / kind(): C's reference implementation (contract extension 1)

GROUND_ROWS = 2                # face rows v < 2 never sit inside a component (except doors): terrain may occupy them
LONG_BAYS = 6                 # a facade with >= this many bays may gain one bay to absorb a large residual (Detail p.19)
MIN_COVERAGE, MIN_CONFIDENCE = 0.5, 0.3      # package face evidence below either -> unresolved
LAYER_PRI = {'wall': 0, 'roof': 0, 'roof-edge': 1, 'band': 2, 'parapet': 3, 'cap': 4, 'column': 5, 'glazing': 6}   # higher wins; any component beats every layer
ORDER = {'backing': 0, 'frame': 1, 'wall': 1, 'column': 1, 'glass': 2, 'mullion': 2, 'door': 2, 'canopy': 4}   # inside a component; others 3
WALL_ONLY = ('shared', 'party')      # party = coincident with a separate non-member building (B, review M3): the neighbour owns it
DIRS = (('north', 0, -1), ('east', 1, 0), ('south', 0, 1), ('west', -1, 0))


def is_solid(name):
    """full block for connection purposes (C's coarse kind 'full' or 'glass'); air is never solid"""
    n = name.split(':')[1]
    return n not in ('air', 'cave_air', 'void_air') and PT.kind(n) in ('full', 'glass')


def conn_kind(name):
    n = name.split(':')[1]
    if n.endswith('_pane') or n == 'iron_bars': return 'pane'
    if n.endswith('_fence'): return 'fence-nether' if n.startswith('nether_brick') else 'fence'
    if S.CONNECTS(name): return 'wall'
    return None


def connects_to(kind, other):
    """does a `kind` block connect toward neighbour block `other` (vanilla 1.18.2 rules, simplified; COMPILER-SPEC 'connections')"""
    on = other.split(':')[1]; ok = conn_kind(other)
    if on.endswith('_fence_gate'): return kind in ('fence', 'fence-nether', 'wall')
    if kind == 'pane': return ok in ('pane', 'wall') or is_solid(other)
    if kind in ('fence', 'fence-nether'): return ok == kind or is_solid(other)
    return ok in ('wall', 'pane') or is_solid(other)


# ---------------- layout primitives (face-local: i = column index along u, v = y - base_y, w = outward offset)
def floors(f, usable):
    """[(v0, height)] bottom-up; count is clipped to the wall (the building is never stretched)"""
    out, v, i = [], f['base_v'], 0
    while f['count'] == 'auto' or i < f['count']:
        h = f.get('ground_height', f['height']) if i == 0 else f['height']
        if v + h > usable: break
        out.append((v, h)); v += h; i += 1
    return out


def bays(n_cols, width, residual, margin=0):
    """[(i0, width)] covering exactly n_cols - margins. Detail p.19: adjust gaps (center), then widths (ends|absorb); only a long facade
    (>= LONG_BAYS bays) adds one bay when the residual exceeds half a bay and every bay loses at most one block."""
    avail = n_cols - 2 * margin
    n = avail // width if width > 0 and avail > 0 else 0
    if n <= 0: return [], ''
    r = avail - n * width; widths = [width] * n; left = right = margin; note = ''
    def mid_out(n): return sorted(range(n), key=lambda k: (abs(2 * k - (n - 1)), k))   # symmetric, deterministic
    if n >= LONG_BAYS and 2 * r > width and width - r <= n + 1 and width > 2:
        n += 1; widths = [width] * n
        for k in mid_out(n)[:width - r]: widths[k] -= 1
        note = f'added a bay ({n}) to absorb residual {r}'
    elif residual == 'center':
        left += r // 2; right += r - r // 2
    elif residual == 'ends':
        widths[0] += r // 2; widths[-1] += r - r // 2
    else:
        o = mid_out(n)
        for k in range(r): widths[o[k % n]] += 1
    out, i = [], left
    for w in widths: out.append((i, w)); i += w
    assert i + right == n_cols, ('bays must cover the face exactly', n_cols, widths, left, right)
    return out, note


class Face:
    def __init__(self, g):
        self.g, self.id, self.cols = g, g['id'], g['columns']
        self.N, self.base, self.H = len(self.cols), int(g['base_y']), int(g['top_y']) - int(g['base_y']) + 1   # rows base_y..top_y inclusive (shell: roof-edge at top_y)
        self.seg = g['segments']

    def cell(self, i, v, w):
        x, z, _, _, ox, oz = self.cols[i]
        return (int(x) + w * int(ox), self.base + v, int(z) + w * int(oz))

    def ph(self, i):
        s = self.seg[int(self.cols[i][3])]
        return F.placeholders(s['normal'], (s['b'][0] - s['a'][0], s['b'][1] - s['a'][1]))

    def corner_ends(self, owners=None):
        """(start, end) corner ownership; owners from corner_owners() (default: this face alone decides)"""
        if owners is None: owners = corner_owners([self.g])
        return (self.id, 0) in owners, (self.id, 1) in owners


def corner_owners(faces):
    """{(face id, 0=start|1=end)}: at each polyline vertex of a part, the face end column nearest the vertex (Chebyshev distance of the
    cell centre, then face order) owns the corner; only ends within 1.5 blocks of the vertex compete"""
    best = {}
    for k, f in enumerate(faces):
        if not f['columns']: continue
        for end, col, pt in ((0, f['columns'][0], f['segments'][0]['a']), (1, f['columns'][-1], f['segments'][-1]['b'])):
            d = max(abs(col[0] + 0.5 - pt[0]), abs(col[1] + 0.5 - pt[1]))
            if d > 1.5: continue
            key = (f['part'], round(pt[0], 2), round(pt[1], 2))
            if key not in best or (d, k) < best[key][0]: best[key] = ((d, k), (f['id'], end))
    return {v[1] for v in best.values()}


class Palette:
    def __init__(self, pal, seed, db):
        self.p, self.seed, self.db = pal or {}, seed, db

    def base(self, role, zone, i, v):
        p = self.p; z = (p.get('zones') or {}).get(zone) or {}
        if role not in z and role not in p['roles'] and role == 'backing': role = 'wall'
        b = z.get(role, p['roles'].get(role))
        if b is None: raise ValueError(f'role {role!r} not in palette')
        b = b[0] if isinstance(b, list) else b
        t = (p.get('texture') or {}).get(role)
        if t and _rule(t['rule'], i, v, self.seed): b = t['alt']
        return b

    def cell(self, cell, zone, i, v, ph):
        """component cell -> block: explicit block, or palette role with props merged only where the block has them (PT.resolve_cell)"""
        if cell.get('block'): return F.fill(cell['block'], ph)
        r = cell['role']; base = self.base(r, zone, i, v)
        return F.fill(PT.resolve_cell(dict(role=r, props=cell.get('props', {})), {r: base}, self.db), ph)


def _rule(rule, u, v, seed):
    m = S.RULE.match(rule.replace(' ', ''))
    if m.group(1): return ((v if m.group(1) == 'rows' else u) % int(m.group(2))) == int(m.group(3))
    return F.det_unit(u, v, seed) < float(m.group(4))


def op_role(role, block, v_rel):
    if role in F.ROLES: return role
    n = block.split(':')[1].split('[')[0]
    if n.endswith('_door'): return 'door'
    if 'glass' in n: return 'glass'
    return 'sill' if v_rel < 0 else 'trim'


def element(spec, comps, dflt_role):
    """a layout element -> template component: {component: name} uses the library component; {role|block, height?, w?} becomes a 1-wide
    repeat_u/repeat_v strip (COMPILER-SPEC 'elements')"""
    if spec.get('component'): return comps[spec['component']]
    c = {'block': spec['block']} if spec.get('block') else {'role': spec.get('role', dflt_role)}
    h = int(spec.get('height', 1))
    return dict(id=dflt_role, size=dict(u=1, v=h), repeat_u=True, repeat_v=True,
                cells=[dict(c, u=0, v=k, w=int(spec.get('w', 0))) for k in range(h)])


def _floor_rows(sel, fl, lo, hi):
    if sel is None: return list(range(lo, hi))
    return sorted({v for k in S.select(sel, len(fl)) for v in range(fl[k][0], fl[k][0] + fl[k][1])})


# ---------------- compile
def compile(geometry, selection, assets, seed):
    g, doc, style = geometry, selection['doc'], selection['kind'] == 'style'
    bid = g['bid']; comps = assets['components']; db = assets['blocks']
    pal = Palette(assets['palette'], seed, db)
    env_doc = doc.get('envelope') or {}
    outward, inward = int(env_doc.get('outward', 1)), int(env_doc.get('inward_backing', 2))
    prot = [] if style else env_doc.get('protrusions', [])
    massing = not style and doc['building'].get('shell', 'existing') == 'none'
    # ---------------- envelope: footprint (each parts_mc piece) + every face column at w in [-inward, outward] + protrusion cells
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union
    from shapely import contains_xy
    boxes = [Polygon(q) for q in (g['envelope'].get('parts_mc') or [g['envelope']['polygon_mc']])]
    fmap = {fg['id']: Face(fg) for fg in g['faces']}
    for fc in fmap.values():
        for i in range(fc.N):
            for w in range(-inward, outward + 1):
                x, _, z = fc.cell(i, 0, w); boxes.append(box(x, z, x + 1, z + 1))
    for p in prot:
        fc = fmap[p['face']]
        for i in range(max(int(p['u'][0]), 0), min(int(-(-p['u'][1] // 1)), fc.N)):
            for w in range(1, p['depth'] + 1):
                x, _, z = fc.cell(i, 0, w); boxes.append(box(x, z, x + 1, z + 1))
    pieces = sorted((Polygon(q.exterior) for q in getattr(unary_union(boxes), 'geoms', [unary_union(boxes)])), key=lambda q: -q.area)
    holes = [Polygon(h) for h in g['envelope'].get('holes_mc') or []]       # geometry v0.6.1: enclosed neighbours / courtyards are outside
    env = unary_union(pieces)            # disjoint members stay separate pieces (envelope.parts_mc, as in B's geometry)
    if holes: env = env.difference(unary_union(holes))
    import shapely; shapely.prepare(env)
    ylo, yhi = g['envelope']['y']
    def in_env(x, y, z): return ylo <= y <= yhi and bool(contains_xy(env, x + 0.5, z + 0.5))
    reasons, unresolved, faces_out, notes = [], [], [], {}
    layers, insts = {}, []      # layers: cell -> (pri, seq, block, role, face, layer name, evidence); insts: component instances
    stats = dict(skipped={}, conflicts={}, conflict_samples=[], bays={}, floors={})
    seq = [0]

    def skip(face, why):
        stats['skipped'][why] = stats['skipped'].get(why, 0) + 1; notes.setdefault(face, set()).add(why)

    def conflict(k, face=None, anchor=None):
        stats['conflicts'][k] = stats['conflicts'].get(k, 0) + 1
        if face and len(stats['conflict_samples']) < 20: stats['conflict_samples'].append(dict(face=face, anchor=anchor, reason=k))

    def put(c, b, role, pri, fid, ev):
        if not in_env(*c): skip(fid, f'{role} outside the envelope (dropped)'); return
        seq[0] += 1; new = (LAYER_PRI[pri], seq[0], b, op_role(role, b, 0), fid, pri, ev); old = layers.get(c)
        if old:
            win, lose = (old, new) if old[0] >= new[0] else (new, old)
            conflict(f'{win[5]}>{lose[5]}'); layers[c] = win
        else: layers[c] = new

    pkg_faces = {} if style else {f['face']: f for f in doc['faces']}
    owners = corner_owners(g['faces'])
    pkg_unres = {} if style else {u['face']: u['reason'] for u in doc['unresolved']}
    face_ev = {}
    for fg in g['faces']:
        fc = Face(fg); fid = fc.id; edge = fg['edge']
        # ---- face selection (COMPILER-SPEC 'face selection')
        lay, ev, why = None, None, None
        if style:
            d = doc['defaults']; d = {'exterior': d} if 'floors' in d else d
            ev = 'style'; e = d.get('shared' if edge == 'party' else edge) if edge != 'exterior' else d['exterior']
            if edge in WALL_ONLY:
                if e and 'floors' in e: lay = e
                else: notes.setdefault(fid, set()).add('shared wall: wall only, no ops (existing shell kept)')
            elif edge == 'unknown':
                if e and 'floors' in e: lay = e
                elif e is None:
                    lay = d['exterior']; notes.setdefault(fid, set()).add('unknown edge: the style has no `unknown` default, exterior layout applied')
                else: why = 'edge kind unknown and the style skips unknown edges'
            else: lay = e
        elif fid in pkg_unres or ('*' in pkg_unres and fid not in pkg_faces): why = pkg_unres.get(fid, pkg_unres.get('*'))
        elif edge == 'unknown': why = 'edge kind unknown (resolve the geometry or select a style)'
        else:
            lay = pkg_faces.get(fid) or (pkg_faces.get('*') if edge == 'exterior' else None)
            if lay is None: why = 'no layout for this face in the package'
            else:
                e = lay['evidence']
                if e['coverage'] < MIN_COVERAGE or e['confidence'] < MIN_CONFIDENCE:
                    why = f"insufficient evidence (coverage {e['coverage']}, confidence {e['confidence']})"; lay = None
                else: ev = e['class']
        if not why and fc.N == 0: why = 'no wall columns'
        if not why and fc.H <= 0: why = 'no wall height'
        # ---- massing (building.shell = none): plain palette wall, base..top, on every non-shared face; measured where no detail
        if massing and edge not in WALL_ONLY and fc.N and fc.H > 0:
            mev = ev if (lay is not None and not why) else 'measured'
            for i in range(fc.N):
                for v in range(fc.H): put(fc.cell(i, v, 0), pal.cell({'role': 'wall'}, None, i, v, fc.ph(i)), 'wall', 'wall', fid, mev)
            if mev == 'measured': notes.setdefault(fid, set()).add('massing only (building.shell = none): detail unresolved')
        if why:
            unresolved.append(dict(part=fg['part'], face=fid, reason=why)); face_ev[fid] = ('unresolved', 0.0, []); continue
        face_ev[fid] = (ev, 1.0 if style else lay['evidence']['coverage'] if lay else 0.0,
                        [f"style:{selection['id']}"] if style else (lay['evidence']['sources'] if lay else []))
        if lay is None: continue
        zone = lay.get('zone')

        def layer(i, v, w, b, role, pri, e=None):
            if not -inward <= w <= outward: skip(fid, f'{role} outside the envelope allowance (w={w})'); return
            if 0 <= i < fc.N and 0 <= v < fc.H: put(fc.cell(i, v, w), b, role, pri, fid, e or ev)

        def stamp(comp, cols, rows, pri, spec=None):
            """layer element: column i uses template u = (i - cols[0]) % size.u; row v uses template v = (v - rows[0]) % size.v when
            repeat_v, else v - rows[0]. spec (the layout element) may carry `evidence` and `zone` overrides (automatic facades, AUTO.md 3)"""
            if not cols or not rows: return
            su, sv = comp['size']['u'], comp['size']['v']; by = {}; e, zn = (spec or {}).get('evidence'), (spec or {}).get('zone', zone)
            for c in comp['cells']: by.setdefault((c['u'], c['v']), []).append(c)
            for i in cols:
                for v in rows:
                    dv = v - rows[0]
                    for c in by.get(((i - cols[0]) % su, dv % sv if comp.get('repeat_v') else dv), ()):
                        layer(i, v, c['w'], pal.cell(c, zn, i, v, fc.ph(i)), c.get('role', 'trim'), pri, e)

        par = lay.get('parapet')
        pcomp = element(par, comps, 'parapet') if par else None
        ph_ = (pcomp['size']['v'] if par.get('component') else int(par.get('height', 0))) if par else 0
        usable = fc.H - ph_
        fl = floors(lay['floors'], usable)
        if lay['floors']['count'] != 'auto' and len(fl) < lay['floors']['count']: skip(fid, 'floor count clipped to the wall height (never stretched)')
        if style and doc['applies_to'].get('max_levels') and len(fl) > doc['applies_to']['max_levels']:
            unresolved.append(dict(part=fg['part'], face=fid, reason=f"{len(fl)} floors > style max_levels {doc['applies_to']['max_levels']}"))
            face_ev[fid] = ('unresolved', 0.0, []); continue
        bw = lay['bays']; bl, bnote = bays(fc.N, bw['width'], bw['residual'], bw.get('margin', 0))
        stats['bays'][fid] = [w for _, w in bl]; stats['floors'][fid] = len(fl)
        if bnote: notes.setdefault(fid, set()).add(bnote)
        allc = list(range(fc.N)); gnd, upp = lay.get('ground') or {}, lay.get('upper') or {}
        g_end = fl[0][0] + fl[0][1] if fl else 0
        # ---- layers: wall fill, upper fill, parapet, bands, top, corners, piers, roof edge
        if (lay.get('wall') or {}).get('fill'): stamp(element(lay['wall'], comps, 'wall'), allc, list(range(fc.H)), 'wall', lay['wall'])
        if upp.get('fill'): stamp(element({'role': upp['fill']}, comps, 'band'), allc, list(range(g_end, usable)), 'wall')
        if par: stamp(pcomp, allc, list(range(usable, fc.H)), 'parapet', par)
        if par and par.get('cap'): stamp(element({'block': par['cap']}, comps, 'parapet'), allc, [fc.H - 1], 'cap', par)
        for band in lay.get('bands') or []:
            at = band['at']; bc = element(band, comps, 'band')
            vs = ([v0 for v0, _ in fl[1:]] if at in ('floors', 'floor_lines') else [usable - bc['size']['v']] if at == 'top'
                  else [int(at[1:])] if isinstance(at, str) else [at])
            for v0 in vs: stamp(bc, allc, [v for v in range(v0, v0 + bc['size']['v']) if v < usable], 'band', band)
        if upp.get('top'):
            tc = element(upp['top'], comps, 'band'); stamp(tc, allc, list(range(usable - tc['size']['v'], usable)), 'band', upp['top'])
        if lay.get('corners'):
            cc = element(lay['corners'], comps, 'column'); k = lay['corners'].get('width', cc['size']['u'])
            s0, s1 = fc.corner_ends(owners); rows = _floor_rows(lay['corners'].get('floors'), fl, 0, usable)
            if s0: stamp(cc, list(range(min(k, fc.N))), rows, 'column', lay['corners'])
            if s1: stamp(cc, list(range(max(fc.N - k, 0), fc.N)), rows, 'column', lay['corners'])
        for zsel, zz in (('ground', gnd), ('upper', upp)):
            if zz.get('piers') and fl and bl:
                pc = element(zz['piers'], comps, 'column')
                edges = sorted({i0 for i0, _ in bl} | {bl[-1][0] + bl[-1][1] - 1})
                rows = _floor_rows(zsel, fl, 0, usable)
                for i in edges: stamp(pc, [i], rows, 'column', zz['piers'])
        if lay.get('roof_edge'):
            rc = element(lay['roof_edge'], comps, 'roof-edge'); stamp(rc, allc, list(range(fc.H - rc['size']['v'], fc.H)), 'roof-edge', lay['roof_edge'])
        # ---- measured glazing rectangles (automatic facades): placed as seen, never re-templated into per-bay windows
        for r in lay.get('rects') or []:
            stamp(element(r, comps, 'glass'), list(range(max(0, r['u0']), min(fc.N, r['u1']))), list(range(max(0, r['v0']), min(usable, r['v1']))), 'glazing', r)
        # ---- openings (component instances): never on shared faces; style min_face_len
        ops_ok = edge not in WALL_ONLY and not (style and fc.N < doc['applies_to'].get('min_face_len', 0))
        olist = [(o, o.get('floors', 'all')) for o in lay.get('openings') or []] + [(o, 'ground') for o in gnd.get('openings') or []] + \
                [(o, 'upper') for o in upp.get('openings') or []]
        if olist and not ops_ok: notes.setdefault(fid, set()).add(f'{edge} wall: no openings' if edge in WALL_ONLY else 'face shorter than style min_face_len: no openings')
        made = {}
        bx = {i for a_, b_ in lay.get('backing_exclude_u') or [] for i in range(a_, b_ + 1)}
        for o, fsel in olist if ops_ok else []:
            comp = comps[o['component']]
            for fi in S.select(fsel, len(fl)):
                v0, fh = fl[fi]
                for bi in S.select(o.get('bays', 'all'), len(bl)):
                    if o.get('replace'):
                        for old in made.pop((o['replace'], fi, bi), []): insts.remove(old)
                    i0, w_ = bl[bi]; cu = comp['size']['u']; ins = o.get('u_inset', 0); span = w_ - 2 * ins
                    if comp.get('repeat_u'):
                        if span < 1 or (comp.get('widths') and span not in comp['widths']): skip(fid, f"{o['component']}: no allowed width in bay {w_}"); continue
                        du, width = ins, span
                    else:
                        if cu > span: skip(fid, f"{o['component']}: wider than bay ({cu} > {span})"); continue
                        al = o.get('align', 'center')
                        du = o.get('u', ins + ((span - cu) // 2 if al == 'center' else 0 if al == 'left' else span - cu)); width = cu
                        if du + cu > w_: skip(fid, f"{o['component']}: u offset leaves the bay"); continue
                    vv = v0 + o.get('v', 0)
                    if vv + comp['size']['v'] > v0 + fh: skip(fid, f"{o['component']}: taller than the floor"); continue
                    parts = [(comp, vv)] + [(comps[o[k]], vv - comps[o[k]]['size']['v'] if k == 'below' else vv + comp['size']['v'])
                                            for k in ('below', 'above') if o.get(k)]
                    inst = _instance(fc, parts, o['component'], i0 + du, width, pal, zone, o.get('evidence', ev), inward, outward, o.get('backing', lay.get('backing', 'auto')), bx, in_env)
                    if isinstance(inst, str): skip(fid, inst); continue
                    inst['pri'] = 3 if any('_door' in c[1] for c in inst['cells']) else 2
                    insts.append(inst); made.setdefault((o['component'], fi, bi), []).append(inst)
        # ---- canopies: w beyond `outward` needs a package protrusion allowance covering the columns (styles have none)
        cl = lay.get('canopy') or []; cl = [cl] if isinstance(cl, dict) else list(cl)
        gc = gnd.get('canopy') or []; cl += [gc] if isinstance(gc, dict) else list(gc)
        for cn in cl if ops_ok else []:
            cc = element(cn, comps, 'canopy') if cn.get('component') else \
                dict(element(cn, comps, 'canopy'), cells=[dict({'block': cn['block']} if cn.get('block') else {'role': cn.get('role', 'canopy')}, u=0, v=0, w=w)
                                                          for w in range(1, int(cn.get('depth', 1)) + 1)])
            depth = max(c['w'] for c in cc['cells'])
            if 'u' in cn: rngs = [list(range(max(cn['u'][0], 0), min(cn['u'][1], fc.N)))]
            else: rngs = [list(range(bl[b][0], bl[b][0] + bl[b][1])) for b in S.select(cn.get('bays', 'all'), len(bl))]
            for rng in rngs:
                if not rng or not 0 <= cn['v'] < fc.H: skip(fid, 'canopy outside the face'); continue
                ok = depth <= outward or any(p['face'] == fid and p['u'][0] <= rng[0] and rng[-1] < p['u'][1] and depth <= p['depth'] for p in prot)
                if not ok: skip(fid, f'canopy depth {depth} > envelope outward {outward} without a covering protrusion allowance (dropped)'); continue
                inst = _instance(fc, [(cc, cn['v'])], cn.get('component', 'canopy'), rng[0], len(rng), pal, zone, cn.get('evidence', ev), inward, max(outward, depth), inside=in_env)
                if isinstance(inst, str): skip(fid, inst); continue
                inst['pri'] = 1; insts.append(inst)
        # ---- simplified lettering (sign-text): only where the package/style assigns text
        for sg, zr in ((lay.get('sign'), (0, usable)), (upp.get('sign'), (g_end, usable)), (gnd.get('sign'), (0, g_end))):
            if not sg: continue
            if not sg.get('text'): notes.setdefault(fid, set()).add('sign: no text assigned (none placed)'); continue
            inst = _sign(fc, comps[sg['component']], sg, zr, pal, zone, ev)
            if isinstance(inst, str): skip(fid, inst); continue
            inst['pri'] = 1; insts.append(inst)

    # ---------------- roof element (packages): cover = cells inside the part polygon that are not wall columns, edge = the part's wall
    # columns (party faces excluded), at top_y ('top') or top_y + 1 ('top+1'); the writer still only replaces air or owned cells
    from shapely import contains_xy as _cxy
    import numpy as np
    for k, rf in enumerate([] if style else doc.get('roofs') or []):
        for p in g['parts']:
            if rf['part'] not in ('*', p['id']): continue
            walls = {(int(c[0]), int(c[1])) for f in g['faces'] if f['part'] == p['id'] and f['edge'] not in ('party',) for c in f['columns']}
            poly = Polygon(p['polygon_mc']); x0, z0, x1, z1 = [int(t // 1) for t in poly.bounds]
            xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1)); m = _cxy(poly, xs + 0.5, zs + 0.5)
            inside = [(x, z) for x, z in zip(xs[m].tolist(), zs[m].tolist()) if (x, z) not in walls]
            for key, cells, role, pri in (('cover', inside, 'roof', 'roof'), ('edge', sorted(walls), 'roof-edge', 'roof-edge')):
                spec = rf.get(key)
                if not spec: continue
                y = int(p['top_y']) + (1 if spec.get('rows') == 'top+1' else 0); ev_ = spec.get('evidence', 'measured')
                if not ylo <= y <= yhi: skip(f"{p['id']}:roof", f'roofs[{k}].{key}: row {y} outside the envelope y range'); continue
                b = spec['block'] if spec.get('block') else pal.cell({'role': spec.get('role', role), 'props': spec.get('props', {})}, None, 0, 0, {})
                for x, z in cells: put((x, y, z), b, spec.get('role', role), pri, f"{p['id']}:roof", ev_)
    # ---------------- extras: explicit reference-matched cells (packages, <= 256), one single-cell atomic instance each, top priority
    for k, (x, y, z, b, r, ev_) in enumerate([] if style else doc.get('extras') or []):
        if not in_env(x, y, z): skip('extras', f'extras[{k}] outside the envelope (dropped)'); continue
        insts.append(dict(kind='extra', face=None, anchor=[x, y, z], ev=ev_, pri=4, cells=[((x, y, z), b, r, None, None)]))

    # ---------------- massing roof: flat roof cap at top_y inside the part polygon (the wall columns already reach top_y); a 1-block
    # parapet ring at top_y + 1 on the part's exterior wall columns when the geometry envelope allows it (B adds +1 for measured parts)
    if massing:
        for fg in g['faces']:
            if fg['edge'] in WALL_ONLY: continue
            fc = Face(fg); y = int(fg['top_y']) + 1
            pr = next((p for p in g['parts'] if p['id'] == fg['part']), {})
            if (pr.get('roof') or {}).get('shape', 'flat') != 'flat' or y > g['envelope']['y'][1]: continue
            for i in range(fc.N):
                c = fc.cell(i, fc.H, 0)
                if c not in layers: put(c, pal.cell({'role': 'parapet'}, None, i, fc.H, fc.ph(i)), 'parapet', 'parapet', fg['id'], 'measured')
        _P = Polygon
        for p in g['parts']:
            if (p.get('roof') or {}).get('shape', 'flat') != 'flat': continue
            poly = _P(p['polygon_mc']); x0, z0, x1, z1 = [int(t // 1) for t in poly.bounds]
            xs, zs = np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1))
            ins = _cxy(poly, xs + 0.5, zs + 0.5); y = int(p['top_y'])
            for x, z in zip(xs[ins].tolist(), zs[ins].tolist()):
                if (x, y, z) not in layers: put((x, y, z), pal.cell({'role': 'roof'}, None, 0, 0, {}), 'roof', 'roof', f"{p['id']}:roof", 'measured')

    # ---------------- conflicts: higher-priority instances claim first; lower ones are clipped (a door claims its whole doorway
    # through every w); a clipped door or an emptied instance is dropped whole; components beat layers; layers by LAYER_PRI
    taken, doorway, accepted = {}, set(), []
    for inst in sorted(insts, key=lambda t: -t['pri']):         # stable: generation order inside a priority
        keep, lost = [], 0
        for cell in inst['cells']:
            if cell[0] in taken or (inst['face'], cell[3], cell[4]) in doorway: lost += 1
            else: keep.append(cell)
        if lost:
            hit = next((taken[c[0]] for c in inst['cells'] if c[0] in taken), 'doorway')
            if any('_door' in c[1] for c in inst['cells']) or not keep:
                conflict(f"{inst['kind']} dropped (overlaps {hit})", inst['face'], inst['anchor']); continue
            conflict(f"{inst['kind']} clipped by {hit}", inst['face'], inst['anchor']); inst['cells'] = keep
        for c, b, r, i, v in inst['cells']:
            taken[c] = inst['kind']
            if '_door' in b: doorway.add((inst['face'], i, v))
        accepted.append(inst)
    order = {id(t): k for k, t in enumerate(insts)}
    accepted.sort(key=lambda t: order[id(t)])
    for c in list(layers):
        if c in taken: conflict(f'component>{layers[c][5]}'); del layers[c]
    # ---------------- ground-contact rows: face rows v = 0 and 1 (base_y, base_y + 1) may coincide with the TPM terrain top (LiDAR ground
    # and TPM differ by ~1 block). Their cells leave every non-door instance and become plain per-cell ops (same block, role, evidence),
    # so a terrain-occupied base cell is skipped alone instead of skipping the whole component. Doors stay atomic (COMPILER-SPEC 4a).
    kept = []
    for inst in accepted:
        if any('_door' in c[1] for c in inst['cells']): kept.append(inst); continue
        base = [c for c in inst['cells'] if c[4] is not None and c[4] < GROUND_ROWS]
        for c, b, r, i, v in base:
            seq[0] += 1; layers[c] = (99, seq[0], b, r, inst['face'], 'ground-contact', inst['ev'])
        if base: stats['ground_contact_cells'] = stats.get('ground_contact_cells', 0) + len(base)
        inst['cells'] = [c for c in inst['cells'] if not (c[4] is not None and c[4] < GROUND_ROWS)]
        if inst['cells']: kept.append(inst)
    accepted = kept

    # ---------------- full plan map, connection states, state completion
    plan_map = {c: (v[2], v[3], None, v[4]) for c, v in layers.items()}
    for k, inst in enumerate(accepted):
        inst['id'] = f'c{k}'
        for c, b, r, i, v in inst['cells']: plan_map[c] = (b, r, inst['id'], inst['face'])
    final = {}
    for c, (b, r, cid, fid) in plan_map.items():
        name, st = F.parse_block(b)
        kind = conn_kind(name)
        if kind:
            con = {}
            for d, dx, dz in DIRS:
                n = (c[0] + dx, c[1], c[2] + dz); o = plan_map.get(n)
                if o:                                   # seam rule (COMPILER-SPEC 6): same instance, or a layer op in the same chunk
                    on = F.parse_block(o[0])[0]
                    same = (o[2] is not None and o[2] == cid) or (o[2] is None and F.chunk_of(n[0], n[2]) == F.chunk_of(c[0], c[2]))
                    con[d] = same and connects_to(kind, on)
                else: con[d] = False                    # never assume an unplanned neighbour
            if kind == 'wall':
                for d in con: st[d] = 'low' if con[d] else 'none'
                straight = (con['north'] and con['south'] and not con['east'] and not con['west']) or (con['east'] and con['west'] and not con['north'] and not con['south'])
                st['up'] = 'false' if straight else 'true'
            else:
                for d in con: st[d] = 'true' if con[d] else 'false'
            st.setdefault('waterlogged', 'false')
        if name.endswith('_stairs'): st.setdefault('shape', 'straight'); st.setdefault('waterlogged', 'false')
        if name.endswith('_slab'): st.setdefault('waterlogged', 'false')
        if name.endswith('_trapdoor'): st.setdefault('open', 'false'); st.setdefault('powered', 'false'); st.setdefault('waterlogged', 'false')
        if name.endswith('_door'): st.setdefault('open', 'false'); st.setdefault('powered', 'false'); st.setdefault('hinge', 'left')
        if name.endswith('_leaves'): st['persistent'] = 'true'
        b2 = F.fmt_block(name, st)
        err = S.check_block(b2, {})
        if err: raise ValueError(f'invalid block state at {c}: {err}')
        final[c] = (b2, r, cid, fid)
    for c, (b, r, cid, fid) in final.items():           # doors complete: every lower half has its upper half in the same component
        if '_door' in b and 'half=lower' in b:
            up = final.get((c[0], c[1] + 1, c[2]))
            assert up and '_door' in up[0] and 'half=upper' in up[0] and up[2] == cid, ('incomplete door', c)

    # ---------------- op list
    lay_ops = sorted(((c, v) for c, v in final.items() if v[2] is None), key=lambda cv: (cv[1][3], cv[0][0], cv[0][2], cv[0][1]))
    ops = [[c[0], c[1], c[2], 'replace', v[0], bid, v[1], None, layers[c][6]] for c, v in lay_ops]
    components = []
    for inst in accepted:
        cells = sorted(inst['cells'], key=lambda t: (ORDER.get(final[t[0]][1], 3), t[3], t[0][1], t[0][0], t[0][2]))
        first = len(ops)
        for c, *_ in cells:
            b, r, cid, fid = final[c]; ops.append([c[0], c[1], c[2], 'replace', b, bid, r, cid, inst['ev']])
        components.append(dict(id=inst['id'], kind=inst['kind'], face=inst['face'], anchor=inst['anchor'], evidence=inst['ev'],
                               chunks=[list(k) for k in sorted({F.chunk_of(c[0], c[2]) for c, *_ in cells})], first=first, count=len(cells)))
    if ops:
        inside = contains_xy(env, [o[0] + 0.5 for o in ops], [o[2] + 0.5 for o in ops])
        bad = [ops[k][:3] for k in range(len(ops)) if not inside[k] or not ylo <= ops[k][1] <= yhi]
        assert not bad, ('ops outside the envelope', bad[:5])
        assert len({tuple(o[:3]) for o in ops}) == len(ops), 'duplicate cells in the op list'
        assert all(o[8] in F.EVIDENCE for o in ops) and (not style or all(o[8] == 'style' for o in ops))

    # ---------------- limits
    lim = doc.get('limits') or {}
    max_ops = min(lim.get('max_ops', F.MAX_OPS_DEFAULT), F.MAX_OPS_DEFAULT) if style else lim.get('max_ops', F.MAX_OPS_DEFAULT)
    max_ext = min(lim.get('max_extent_m', F.MAX_EXTENT_DEFAULT), F.MAX_EXTENT_DEFAULT) if style else lim.get('max_extent_m', F.MAX_EXTENT_DEFAULT)
    x0, z0, x1, z1 = env.bounds; extent = round(max(x1 - x0, z1 - z0), 2)
    per_chunk = {}
    for o in ops: k = F.chunk_of(o[0], o[2]); per_chunk[k] = per_chunk.get(k, 0) + 1
    mc = F.max_ops_chunk()
    limits = dict(ops=len(ops), max_ops=max_ops, max_extent_m=max_ext, extent_m=extent, exception=None if style else lim.get('exception'),
                  max_ops_chunk=mc, chunks_over=[[k[0], k[1], n] for k, n in sorted(per_chunk.items()) if n > mc])
    n_ops = len(ops); fev = {}
    for o in ops: d = fev.setdefault(final[tuple(o[:3])][3], {}); d[o[8]] = d.get(o[8], 0) + 1
    for fo in g['faces']:
        fid = fo['id']; e = face_ev.get(fid, ('unresolved', 0.0, []))
        faces_out.append(dict(id=fid, part=fo['part'], edge=fo['edge'], evidence=e[0], coverage=e[1], sources=e[2],
                              ops=sum(1 for v in final.values() if v[3] == fid), ops_by_evidence=dict(sorted(fev.get(fid, {}).items())),
                              notes='; '.join(sorted(notes.get(fid, ())))))
    if style: reasons.append(f"STYLE-GENERATED (not observed): style {selection['id']}")
    if massing: reasons.append('building.shell = none: plain massing walls and flat roof caps (evidence measured) where detail is unresolved')
    if unresolved: reasons.append(f'{len(unresolved)} face(s) unresolved: no detail there' + (' (massing only)' if massing else ' (existing shell kept)'))
    for k, n in sorted(stats['skipped'].items()): reasons.append(f'skipped {n}x: {k}')
    for k, n in sorted(stats['conflicts'].items()): reasons.append(f'conflict {n}x: {k}')
    detail = any(o[8] != 'measured' or o[7] for o in ops) or not massing
    status = F.READY if ops and (detail or not unresolved or massing) else F.UNRESOLVED if unresolved else F.NOTHING
    if n_ops > max_ops or extent > max_ext:
        reasons.insert(0, f'{n_ops} ops (max {max_ops}) / extent {extent} m (max {max_ext}): refused' + ('' if style else ' (a package may raise limits only with limits.exception)'))
        status, ops, components = F.REFUSED_LIMITS, [], []
    if limits['chunks_over']: reasons.append(f"{len(limits['chunks_over'])} chunk(s) exceed {mc} ops: chunk scope refused there; preview whole")
    sel = {k: selection[k] for k in ('kind', 'id', 'version', 'sha256', 'inputs_sha')}
    sel['files'] = dict(sorted(assets['files'].items()))
    stats.update(components=len(components), components_by_kind=_count(c['kind'] for c in components), ops_by_role=_count(o[6] for o in ops),
                 ops_by_evidence=_count(o[8] for o in ops), ops_per_chunk_max=max(per_chunk.values(), default=0), blocks=assets.get('blocks_source'),
                 massing=massing)
    return dict(version=F.PLAN_VERSION, building=bid, members=list(g.get('members') or [bid]), selection=sel, generator=F.GENERATOR, seed=seed,
                geometry_sha=g['geometry_sha'], plan_key=plan_key(selection, g['geometry_sha'], seed), status=status, reasons=reasons,
                envelope=dict(polygon_mc=[[round(x, 3), round(z, 3)] for x, z in pieces[0].exterior.coords[:-1]], y=[ylo, yhi],
                              protrusions=[dict(face=p['face'], u=p['u'], depth=p['depth'], allowance=p['allowance']) for p in prot],
                              **({'parts_mc': [[[round(x, 3), round(z, 3)] for x, z in q.exterior.coords[:-1]] for q in pieces]} if len(pieces) > 1 else {}),
                              **({'holes_mc': g['envelope']['holes_mc']} if g['envelope'].get('holes_mc') else {})),
                chunks=[list(k) for k in sorted({F.chunk_of(o[0], o[2]) for o in ops})],
                faces=faces_out, components=components, unresolved=unresolved, limits=limits, stats=stats, timings_s={}, ops=ops)


def plan_key(selection, geometry_sha, seed):
    """sha256(selection inputs sha + geometry_sha + generator + seed)[:16]; inputs_sha covers the package/style file and every library file"""
    return F.sha_bytes(f"{selection['inputs_sha']}|{geometry_sha}|{F.GENERATOR}|{seed}".encode())[:16]


def _count(it):
    d = {}
    for k in it: d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items()))


def _instance(fc, parts, name, i0, width, pal, zone, ev, inward, outward, backing='auto', bexcl=frozenset(), inside=None):
    """one component instance over columns [i0, i0+width): parts = [(component, v_anchor)] (the opening plus its below/above).
    repeat_u components tile across the width (partial last tile clipped); placeholders come from the anchor column's segment;
    a glass cell at w=0 with nothing behind gets an automatic backing cell (Detail pp.4-5). Cells leaving the face or the envelope
    allowance skip the whole instance (returns the reason)."""
    ph = fc.ph(i0); cells, seen = [], set()
    for comp, va in parts:
        cu = comp['size']['u']; reps = -(-width // cu) if comp.get('repeat_u') else 1
        for k in range(reps):
            for cell in comp['cells']:
                du = k * cu + cell['u']
                if comp.get('repeat_u') and du >= width: continue
                i, v, w = i0 + du, va + cell['v'], cell['w']
                if not (0 <= i < fc.N and 0 <= v < fc.H): return f'{name}: cells leave the face'
                if not -inward <= w <= outward: return f'{name}: cells outside the envelope allowance (w={w})'
                c = fc.cell(i, v, w)
                if inside and not inside(*c): return f'{name}: cells outside the envelope (hole or allowance)'
                if c in seen: continue
                b = pal.cell(cell, zone, i, v, ph)
                seen.add(c); cells.append((c, b, op_role(cell.get('role'), b, cell['v']), i, v))
    if inward >= 1 and backing == 'auto':
        for (c, b, r, i, v) in list(cells):
            if r == 'glass' and c == fc.cell(i, v, 0):
                bc = fc.cell(i, v, -1)
                if bc not in seen and (not inside or inside(*bc)): seen.add(bc); cells.append((bc, pal.cell({'role': 'backing'}, zone, i, v, ph), 'backing', i, v))
    # backing opt-out: `backing: none` drops every backing cell of the instance, backing_exclude_u drops those columns (auto or authored)
    cells = [c for c in cells if not (c[2] == 'backing' and (backing == 'none' or c[3] in bexcl))]
    if not cells: return f'{name}: no cells left after the backing opt-out'
    return dict(kind=parts[0][0].get('id', name), face=fc.id, anchor=list(fc.cell(i0, parts[0][1], 0)), ev=ev, cells=cells)


def _sign(fc, comp, sg, zr, pal, zone, ev):
    """simplified lettering from the sign-text glyph table: text upper-cased, advance glyph_u + spacing, centred on the face (or at u),
    vertically centred in its zone (or at v); unknown characters are refused"""
    gu, gv = comp['glyph_size']['u'], comp['glyph_size']['v']; sp = comp.get('spacing', 1); txt = str(sg['text']).upper()
    bad = sorted({ch for ch in txt if ch not in comp['glyphs']})
    if bad: return f'sign: characters {bad} not in the glyph table'
    width = len(txt) * (gu + sp) - sp
    u0 = sg.get('u', (fc.N - width) // 2); v0 = sg.get('v', zr[0] + max((zr[1] - zr[0] - gv) // 2, 0))
    if u0 < 0 or u0 + width > fc.N or v0 < 0 or v0 + gv > fc.H: return 'sign: text does not fit the face'
    ph = fc.ph(u0); cells = []; w = comp.get('w', 0); role = comp.get('role', 'sign')
    for k, ch in enumerate(txt):
        for row, bits in enumerate(comp['glyphs'][ch]):
            for col, bit in enumerate(bits):
                if bit == '1':
                    i, v = u0 + k * (gu + sp) + col, v0 + gv - 1 - row
                    cells.append((fc.cell(i, v, w), pal.cell({'role': role}, zone, i, v, ph), 'sign', i, v))
    return dict(kind=comp.get('id', 'sign-text'), face=fc.id, anchor=list(fc.cell(u0, v0, 0)), ev=ev, cells=cells)


if __name__ == '__main__':
    assert bays(11, 4, 'center') == ([(1, 4), (5, 4)], '')                 # short facade: residual 3 -> gaps 1 | 2
    assert bays(11, 4, 'ends')[0] == [(0, 5), (5, 6)] and bays(11, 4, 'absorb')[0] == [(0, 6), (6, 5)]
    b, note = bays(31, 4, 'center'); assert len(b) == 8 and sum(w for _, w in b) == 31 and note        # long: 7 bays + r 3 -> 8 bays
    assert bays(3, 4, 'center') == ([], '')
    assert floors(dict(base_v=0, height=4, count='auto'), 13) == [(0, 4), (4, 4), (8, 4)]
    assert floors(dict(base_v=0, height=4, ground_height=5, count=9), 13) == [(0, 5), (5, 4), (9, 4)]
    assert connects_to('pane', 'minecraft:glass') and not connects_to('pane', 'minecraft:stone_slab') and connects_to('wall', 'minecraft:iron_bars')
    print('facade_compile ok (run prep/facade_selftest.py for the full suite)')
