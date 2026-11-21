"""facade_geometry.py - building geometry for the Facade tool (v0.6), contract `facade-geometry-1` (docs/facade/ARCHITECTURE.md 8, 8a).
    build(bid, members=None, package_parts=None, cache=..., outward=1, use_accepted=True, geo=None) -> dict
    CLI: python prep/facade_geometry.py <cache> <bid> [--member w..] [--no-accepted]  -> <cache>/facade/buildings/<bid>/geometry.json
         python prep/facade_geometry.py            (no arguments: self-check)
Rules and limits: docs/facade/GEOMETRY-SPEC.md. Cells are voxelised exactly like shelltool_prep.do_shells (block centre ->
TPM inverse -> UTM 11N -> contains footprint; walls = 4-neighbour outside the building or next to a lower part)."""
import hashlib, json, math, os, sqlite3, sys
import numpy as np
from scipy import ndimage as ndi
from shapely import contains_xy
from shapely.geometry import Polygon, MultiPolygon, LineString, Point, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, transform
from shapely import wkb as swkb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
VERSION, GENERATOR = 'facade-geometry-1', 'facade_geometry v0.6.2'
INDEX = os.environ.get('MORIA_OSM_INDEX', r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index.sqlite')
SHARED_EPT = r'C:\BTE-Migration\shelltool\cache\ept'
HARLO = r'C:\BTE-Migration\summerlin\detailing\jobs\harlo-w348127357\geometry.json'
PILOT_SHELLS = r'C:\BTE-Migration\06-summerlin-pilot-server\plugins\SummerlinShellTool\shells.txt'
# copied from shelltool_prep (that module runs its pipeline at import, so it cannot be imported)
DATASETS = [('NV_ClarkCo_2_B22', 2022), ('NV_ClarkCo_1_B22', 2022), ('NV_ClarkCounty_B1_2018', 2018), ('NV_LasVegasValley_2010', 2010)]
EPT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'
SIMPLIFY_M, FACE_TURN_DEG, ARC_MAX_RES, ARC_MIN_SEGS, PARAPET = 0.25, 30.0, 0.25, 3, 1
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))        # (dx, dz) 4-neighbours, same set as the shell planner


# ---------------------------------------------------------------- data access
def osm_feats(ids):
    con = sqlite3.connect(f'file:{INDEX}?mode=ro', uri=True)
    try:
        rows = con.execute(f"select osm,kind,tags,wkb from feat where osm in ({','.join('?' * len(ids))})", list(ids)).fetchall()
    finally: con.close()
    return {o: dict(id=o, kind=k, tags=json.loads(t), geom=swkb.loads(g)) for o, k, t, g in rows}


def neighbour_footprints(fp_ll, members, geo, pad=0.0005):
    """MC footprints of non-member OSM buildings (R-tree query, ~50 m pad) within 2 m of the footprint union, sorted by id"""
    con = sqlite3.connect(f'file:{INDEX}?mode=ro', uri=True); b = fp_ll.bounds
    try:
        rows = con.execute("select f.osm,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=? "
                           "and f.kind='building' order by f.osm", (b[0] - pad, b[2] + pad, b[1] - pad, b[3] + pad)).fetchall()
    finally: con.close()
    near = fp_ll.buffer(2e-5); out = []                                       # ~2 m in lon/lat
    for o, w in rows:
        g = swkb.loads(w)
        if o in members or not g.is_valid or not g.intersects(near): continue
        for p in getattr(g, 'geoms', [g]):
            out.append(Polygon(geo.fwd(list(p.exterior.coords)), [geo.fwd(list(r.coords)) for r in p.interiors]))
    return out


def osm_parts_within(fp_ll):
    """building:part features whose representative point lies in the footprint (lon/lat)"""
    con = sqlite3.connect(f'file:{INDEX}?mode=ro', uri=True); b = fp_ll.bounds
    try:
        rows = con.execute("select f.osm,f.kind,f.tags,f.wkb from feat f join feat_idx i on f.id=i.id where i.maxx>=? and i.minx<=? and i.maxy>=? and i.miny<=? "
                           "and f.kind='building_part'", (b[0], b[2], b[1], b[3])).fetchall()
    finally: con.close()
    out = [dict(id=o, kind=k, tags=json.loads(t), geom=swkb.loads(g)) for o, k, t, g in rows]
    return sorted([f for f in out if f['geom'].is_valid and fp_ll.contains(f['geom'].representative_point())], key=lambda f: f['id'])


def _pilot_index():
    idx, cols = {}, {}
    for l in open(PILOT_SHELLS):
        if l.startswith('F '):
            t = l.split(' ', 2); idx[t[2].split('\t')[0]] = int(t[1])
        elif l.startswith('B '):
            _, x, y, z, p, f = l.split(); cols.setdefault(int(f), []).append((int(x), int(y), int(z)))
    return idx, cols


def accepted_shell(bid):
    """frozen shell columns {(x,z): (ground_y, top_y)} + source string, or None"""
    if bid == 'w348127357' and os.path.exists(HARLO):
        H = json.load(open(HARLO))
        return {(c['x'], c['z']): (c['ground_y'], c['top_y']) for c in H['columns']}, 'accepted:' + HARLO
    if not os.path.exists(PILOT_SHELLS): return None
    idx, cols = _pilot_index()
    if bid not in idx or idx[bid] not in cols: return None
    lo, hi = {}, {}
    for x, y, z in cols[idx[bid]]:
        lo[(x, z)] = min(lo.get((x, z), y), y); hi[(x, z)] = max(hi.get((x, z), y), y)
    return {k: (lo[k] - 1, hi[k]) for k in hi}, 'accepted:' + PILOT_SHELLS


def shell_wall_cells(shell):
    """wall columns of a frozen shell: columns with a block below the cap (outer ring + step walls)"""
    return {k for k, (g, t) in shell.items() if t - g > 1 and any((k[0] + dx, k[1] + dz) not in shell or shell[(k[0] + dx, k[1] + dz)][1] < t for dx, dz in DIRS)}


def lidar(ub_utm, cache_ept, max_nodes=300):
    """first LiDAR dataset covering ub (UTM box); EPT read-through to the shared cache, new tiles only under cache_ept"""
    import ept_cached
    from pyproj import Transformer
    if not getattr(ept_cached, '_facade_rt', False):
        orig = ept_cached._get
        def _get(url, path, timeout):
            if path and not os.path.exists(path):
                alt = os.path.join(SHARED_EPT, os.path.relpath(path, cache_ept))
                if os.path.exists(alt): return open(alt, 'rb').read(), True
            return orig(url, path, timeout)
        ept_cached._get = _get; ept_cached._facade_rt = True
    u2m = Transformer.from_crs(32611, 3857, always_xy=True).transform
    (bx0, by0), (bx1, by1) = u2m(ub_utm[0], ub_utm[1]), u2m(ub_utm[2], ub_utm[3]); errs = []
    for name, year in DATASETS:
        try:
            r = ept_cached.fetch(EPT + name, (min(bx0, bx1), min(by0, by1), max(bx0, bx1), max(by0, by1)), cache_ept, max_nodes=max_nodes)
            if r is None: errs.append(f'{name}: outside bounds'); continue
            if (r['cls'] == 2).sum() < 50: errs.append(f'{name}: too few ground points'); continue
            X, Y = Transformer.from_crs(3857, 32611, always_xy=True).transform(r['x'], r['y'])
            return dict(name=name, year=year, X=X, Y=Y, Z=r['z'], C=r['cls'], nret=r['nret'], points=int(len(X))), errs
        except Exception as ex: errs.append(f'{name}: {type(ex).__name__} {ex}')
    return None, errs


def ndsm_sampler(lid, ub):
    """nDSM (single-return non-ground max minus mean-ground DTM) on a 1 m UTM grid; identical to the shell planner"""
    keep = (lid['C'] != 7) & (lid['C'] != 18)
    X, Y, Z, C, nr = lid['X'][keep], lid['Y'][keep], lid['Z'][keep], lid['C'][keep], lid['nret'][keep]
    gx0, gy0 = np.floor(ub[0]), np.floor(ub[1]); gnx, gny = int(ub[2] - gx0) + 2, int(ub[3] - gy0) + 2
    ok = (X >= gx0) & (Y >= gy0) & (X < gx0 + gnx) & (Y < gy0 + gny); X, Y, Z, C, nr = X[ok], Y[ok], Z[ok], C[ok], nr[ok]
    g = C == 2; gi = ((Y[g] - gy0).astype(int), (X[g] - gx0).astype(int))
    dsum = np.zeros((gny, gnx)); dcnt = np.zeros((gny, gnx)); np.add.at(dsum, gi, Z[g]); np.add.at(dcnt, gi, 1)
    dtm = np.where(dcnt > 0, dsum / np.maximum(dcnt, 1), np.nan)
    dtm = dtm[tuple(ndi.distance_transform_edt(np.isnan(dtm), return_distances=False, return_indices=True))]
    ng = (C == 1) & (nr == 1); dsm = np.full((gny, gnx), np.nan); ii = ((Y[ng] - gy0).astype(int), (X[ng] - gx0).astype(int))
    o = np.argsort(Z[ng]); dsm[ii[0][o], ii[1][o]] = Z[ng][o]; ndsm = dsm - dtm
    def at(Ea, Na):
        j, i = (Ea - gx0).astype(int), (Na - gy0).astype(int); okk = (i >= 0) & (j >= 0) & (i < gny) & (j < gnx)
        v = np.full(Ea.shape, np.nan); v[okk] = ndsm[i[okk], j[okk]]; return v
    return at


# ---------------------------------------------------------------- pure geometry (MC block space, x east, z south; north = -z)
def r3(v): return round(float(v), 3)


def turn_deg(a, b, c):
    d1, d2 = np.subtract(b, a), np.subtract(c, b)
    return math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1]))


def fit_circle(pts):
    """Kasa least-squares circle -> (cx, cz, r, max |radial residual|)"""
    p0 = np.asarray(pts, float); o = p0.mean(0); p = p0 - o                       # centred: MC coordinates (~1e7) make Kasa ill-conditioned
    A = np.c_[2 * p, np.ones(len(p))]; bb = (p ** 2).sum(1)
    (cx, cz, c), *_ = np.linalg.lstsq(A, bb, rcond=None); r = math.sqrt(max(c + cx * cx + cz * cz, 0))
    return cx + o[0], cz + o[1], r, float(np.abs(np.hypot(p[:, 0] - cx, p[:, 1] - cz) - r).max())


def find_arcs(ring, tag):
    """runs of >= ARC_MIN_SEGS segments turning the same way by 1..25 deg each whose vertices fit a circle within ARC_MAX_RES.
    ring: closed list of vertices (last != first). Returns {segment index: arc id}, [arc dicts]"""
    n = len(ring); turns = [turn_deg(ring[i - 1], ring[i], ring[(i + 1) % n]) for i in range(n)]   # turn at vertex i
    curvy = [1.0 <= abs(t) <= 25.0 for t in turns]
    seg_arc, arcs = {}, []
    if all(curvy) and n >= 6:                                                   # a whole round ring (cylinder)
        cx, cz, r, res = fit_circle(ring)
        if res <= ARC_MAX_RES:
            aid = f'{tag}a0'; arcs.append(dict(id=aid, center=[r3(cx), r3(cz)], radius=r3(r), max_residual=r3(res), segments=n)); return {i: aid for i in range(n)}, arcs
    start = next((i for i in range(n) if not curvy[i]), 0); i = 0
    while i < n:                                                               # walk vertices from a non-curvy one
        v = (start + i) % n
        if curvy[v]:
            run = [v]; j = i + 1
            while j < n and curvy[(start + j) % n] and np.sign(turns[(start + j) % n]) == np.sign(turns[v]): run.append((start + j) % n); j += 1
            segs = [(run[0] - 1) % n] + run                                    # segment k goes ring[k] -> ring[k+1]; interior vertices = run
            if len(segs) >= ARC_MIN_SEGS:
                pts = [ring[s] for s in segs] + [ring[(segs[-1] + 1) % n]]; cx, cz, r, res = fit_circle(pts)
                if res <= ARC_MAX_RES and r < 2000:
                    aid = f'{tag}a{len(arcs)}'; arcs.append(dict(id=aid, center=[r3(cx), r3(cz)], radius=r3(r), max_residual=r3(res), segments=len(segs)))
                    for s in segs: seg_arc[s] = aid
            i = j
        else: i += 1
    return seg_arc, arcs


def label_of(nx, nz):
    return ('E' if nx > 0 else 'W') if abs(nx) >= abs(nz) else ('S' if nz > 0 else 'N')


def ring_faces(ring_pts, part, tag, classify, base_y, top_y):
    """one oriented ring (u grows left->right seen from outside) -> faces. classify(a, b, normal) -> (edge, base_y)."""
    ring = [tuple(p) for p in ring_pts[:-1]]
    changed = True                                                         # drop spikes (~180 deg) and collinear vertices (< 0.1 deg)
    while changed and len(ring) > 3:
        changed = False
        for i in range(len(ring)):
            t = abs(turn_deg(ring[i - 1], ring[i], ring[(i + 1) % len(ring)]))
            if t > 175 or t < 0.1 or math.dist(ring[i - 1], ring[i]) < 1e-6: del ring[i]; changed = True; break
    n = len(ring); seg_arc, arcs = find_arcs(ring, tag)
    segs = []
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]; L = math.dist(a, b)
        if L < 1e-9: continue
        nrm = (-(b[1] - a[1]) / L, (b[0] - a[0]) / L)                     # outward for the orientation used (see GEOMETRY-SPEC)
        ns = max(2, int(L / 0.5)); runs = []                               # classify every 0.5 m, split where the kind changes
        for i in range(ns):
            t = (i + 0.5) / ns; key = classify(a[0] + (b[0] - a[0]) * t + nrm[0] * 0.3, a[1] + (b[1] - a[1]) * t + nrm[1] * 0.3)
            if runs and runs[-1][0] == key: runs[-1][2] = i + 1
            else: runs.append([key, i, i + 1])
        while len(runs) > 1:                                               # runs < 1 m are noise: absorbed by the previous run (or the next)
            j = min(range(len(runs)), key=lambda j: (runs[j][2] - runs[j][1], j))
            if (runs[j][2] - runs[j][1]) * L / ns >= 1.0: break
            o = j - 1 if j > 0 else 1; lo, hi = min(runs[j][1], runs[o][1]), max(runs[j][2], runs[o][2])
            runs[o][1:] = [lo, hi]; del runs[j]
            r2 = []
            for r in runs:
                if r2 and r2[-1][0] == r[0]: r2[-1][2] = r[2]
                else: r2.append(r)
            runs = r2
        for (edge, by), i0, i1 in runs:
            p0 = a if i0 == 0 else (a[0] + (b[0] - a[0]) * i0 / ns, a[1] + (b[1] - a[1]) * i0 / ns)
            p1 = b if i1 == ns else (a[0] + (b[0] - a[0]) * i1 / ns, a[1] + (b[1] - a[1]) * i1 / ns)
            segs.append(dict(a=p0, b=p1, L=math.dist(p0, p1), normal=nrm, curve=seg_arc.get(k), edge=edge, base=by))
    def brk(s0, s1):                                                       # face break between consecutive segments
        if (s0['edge'], s0['base']) != (s1['edge'], s1['base']): return True
        if s0['curve'] and s0['curve'] == s1['curve']: return False
        if s0['normal'] == s1['normal']: return False                      # pieces of one split edge
        return abs(turn_deg(s0['a'], s0['b'], s1['b'])) > FACE_TURN_DEG
    m = len(segs); starts = [k for k in range(m) if brk(segs[k - 1], segs[k])]
    if not starts: starts = [0]
    faces = []
    for fi, st in enumerate(starts):
        en = starts[(fi + 1) % len(starts)]; run = []; k = st
        while True:
            run.append(segs[k]); k = (k + 1) % m
            if k == en: break
        u = 0.0; out = []
        for s in run:
            out.append(dict(a=[r3(s['a'][0]), r3(s['a'][1])], b=[r3(s['b'][0]), r3(s['b'][1])], u0=round(u, 3), u1=round(u + s['L'], 3),
                            normal=[round(s['normal'][0], 4), round(s['normal'][1], 4)], curve=('arc:' + s['curve']) if s['curve'] else None))
            u += s['L']
        nx = sum(s['normal'][0] * s['L'] for s in run); nz = sum(s['normal'][1] * s['L'] for s in run)
        faces.append(dict(part=part, edge=run[0]['edge'], label=label_of(nx, nz) + ' (display only)', segments=out, length_u=round(u, 3),
                          base_y=int(run[0]['base']), top_y=int(top_y), columns=[]))
    return faces, arcs


def part_faces(pid, poly, classify, base_y, top_y):
    """all rings of a part polygon (holes included) -> faces with ids pid:f0.."""
    P = orient(poly.simplify(SIMPLIFY_M, preserve_topology=True), sign=-1.0)
    faces, arcs = [], []
    for ri, ring in enumerate([P.exterior] + list(P.interiors)):
        f, a = ring_faces(list(ring.coords), pid, f'{pid}r{ri}', classify, base_y, top_y); faces += f; arcs += a
    for i, f in enumerate(faces): f['id'] = f'{pid}:f{i}'
    return faces, arcs


def make_classifier(pid, polys, tops, neighbours=()):
    """outward sample point (0.3 m off the wall) -> (edge, base_y): inside another part with top >= ours -> shared; a lower part ->
    exterior from its top; inside a non-member OSM building -> party; else exterior"""
    others = [(q, polys[q]) for q in sorted(polys) if q != pid]
    def classify(x, z):
        p = Point(x, z); q = next((q for q, g in others if g.covers(p)), None)
        if q is not None:
            if tops[q][1] >= tops[pid][1]: return 'shared', tops[pid][0]
            return 'exterior', max(tops[q][1], tops[pid][0])
        if any(g.covers(p) for g in neighbours): return 'party', tops[pid][0]
        return 'exterior', tops[pid][0]
    return classify


def wall_dirs(L, tops, iz, ix):
    """shell-planner wall test for cell (iz, ix): directions to a neighbour outside the building or in a lower part"""
    p = L[iz, ix]; NZ, NX = L.shape; out = []
    for dx, dz in DIRS:
        a, b = iz + dz, ix + dx
        q = L[a, b] if (0 <= a < NZ and 0 <= b < NX) else 0
        if q == 0 or tops[q] < tops[p]: out.append((dx, dz))
    return out


def assign_columns(faces, L, tops, X0, Z0, label_of_part):
    """every wall cell -> exactly one non-shared face of its part (nearest segment facing one of its wall directions).
    Cells whose nearest facing segment is a party wall get no column (never written)."""
    by_part = {}
    for fi, f in enumerate(faces):
        if f['edge'] == 'shared': continue
        for si, s in enumerate(f['segments']): by_part.setdefault(f['part'], []).append((fi, si, s))
    unassigned = party = 0
    for iz, ix in zip(*np.nonzero(L)):
        d = wall_dirs(L, tops, iz, ix)
        if not d: continue
        pid = label_of_part[L[iz, ix]]; cx, cz = X0 + ix + 0.5, Z0 + iz + 0.5; best = None
        for strict in (True, False):
            for fi, si, s in by_part.get(pid, []):
                nx, nz = s['normal']
                if strict and max(nx * dx + nz * dz for dx, dz in d) <= 0.3: continue
                ax, az = s['a']; bx, bz = s['b']; vx, vz = bx - ax, bz - az; LL = vx * vx + vz * vz
                t = min(1.0, max(0.0, ((cx - ax) * vx + (cz - az) * vz) / LL)); dist = math.hypot(ax + t * vx - cx, az + t * vz - cz)
                key = (round(dist, 6), fi, si)
                if best is None or key < best[0]: best = (key, fi, si, s, t)
            if best: break
        if best is None: unassigned += 1; continue
        _, fi, si, s, t = best; nx, nz = s['normal']
        if faces[fi]['edge'] == 'party': party += 1; continue
        ox, oz = max(d, key=lambda e: (e[0] * nx + e[1] * nz, e))
        faces[fi]['columns'].append([int(X0 + ix), int(Z0 + iz), round(s['u0'] + t * (s['u1'] - s['u0']), 2), si, ox, oz])
    for f in faces: f['columns'].sort(key=lambda c: (c[2], c[0], c[1]))
    return unassigned, party


def snowball_parts(h, cells, min_cells=25, tol=1.5):
    """the shell planner's part segmentation (rounded nDSM levels, merge < 25 cells, merge neighbours within 1.5 m; defaults = planner)"""
    q = np.round(h); parts = np.zeros(cells.shape, np.int32); nparts = 0
    for v in np.unique(q[np.isfinite(q)]):
        lab, nn = ndi.label(q == v)
        for i in range(1, nn + 1): nparts += 1; parts[lab == i] = nparts
    for _ in range(50):
        sizes = np.bincount(parts.ravel(), minlength=nparts + 1); small = [p for p in range(1, nparts + 1) if 0 < sizes[p] < min_cells]
        if not small: break
        for p in small:
            mk = parts == p
            if not mk.any(): continue
            nb = ndi.binary_dilation(mk) & ~mk & (parts > 0)
            if not nb.any(): parts[mk] = 0; continue
            hp = np.nanmedian(h[mk]); cand = np.unique(parts[nb]); parts[mk] = min(cand, key=lambda c: abs(np.nanmedian(h[parts == c]) - hp))
    changed = True
    while changed:
        changed = False
        for p in np.unique(parts[parts > 0]):
            mk = parts == p
            if not mk.any(): continue
            for c in np.unique(parts[ndi.binary_dilation(mk) & ~mk & (parts > 0)]):
                if abs(np.nanmedian(h[parts == c]) - np.nanmedian(h[mk])) <= tol: parts[parts == c] = p; changed = True
    return parts


def regions_polys(regions, X0, Z0, clip, fill):
    """raster part regions [(mask, top)] -> polygons that tile the footprint. Footprint cells in no region (LiDAR holes) are
    lent to the nearest region for vectorising only (wall columns still use the real masks). Highest part first: squares grown
    1.5 m (past the footprint edge) minus the other regions' squares, stairs simplified (1 m), then clipped to the footprint and
    minus the parts already placed, so internal boundaries coincide exactly. Largest piece of each."""
    lab = np.zeros(fill.shape, np.int32)
    for k, (m, _) in enumerate(regions): lab[m] = k + 1
    near = lab[tuple(ndi.distance_transform_edt(lab == 0, return_distances=False, return_indices=True))]
    lab = np.where(lab > 0, lab, np.where(fill, near, 0))
    sq = [unary_union([box(X0 + i, Z0 + j, X0 + i + 1, Z0 + j + 1) for j, i in zip(*np.nonzero(lab == k + 1))]) for k in range(len(regions))]
    out, placed = [None] * len(regions), None
    order = sorted(range(len(regions)), key=lambda k: (-regions[k][1], -int(regions[k][0].sum()), k))
    for n, k in enumerate(order):
        g = sq[k].buffer(1.5, join_style=2).difference(unary_union([sq[j] for j in order[n + 1:]]))
        g = g.simplify(1.0, preserve_topology=True).intersection(clip)
        if placed is not None: g = g.difference(placed)
        polys = [x for x in getattr(g, 'geoms', [g]) if x.geom_type == 'Polygon' and not x.is_empty]
        out[k] = max(polys, key=lambda x: x.area) if polys else sq[k]
        placed = out[k] if placed is None else placed.union(out[k])
    return out


def roof_top(hv_interior_grid, mask_interior):
    """robust measured top: drop rooftop machinery (connected cells > median + 1 m, area < min(150 m2, 10 % of the part)), then p90"""
    hv = hv_interior_grid[mask_interior & np.isfinite(hv_interior_grid)]
    if hv.size < 20: return None
    med = float(np.median(hv)); hi = mask_interior & np.isfinite(hv_interior_grid) & (hv_interior_grid > med + 1.0)
    lab, nn = ndi.label(hi, structure=np.ones((3, 3))); mach = np.zeros_like(hi); lim = min(150, 0.1 * hv.size)
    sizes = np.bincount(lab.ravel())
    for i in range(1, nn + 1):
        if sizes[i] < lim: mach |= lab == i
    keep = hv_interior_grid[mask_interior & np.isfinite(hv_interior_grid) & ~mach]
    return dict(p90=float(np.percentile(keep, 90)), median=float(np.median(keep)), n=int(keep.size), machinery_cells=int(mach.sum()),
                machinery_max=float(hv_interior_grid[mach].max() - med) if mach.any() else 0.0, raised=float((hv > 2.0).mean()))


def canon_sha(d):
    return hashlib.sha256(json.dumps({k: v for k, v in d.items() if k != 'geometry_sha'}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def assemble(bid, members, parts, label_grid, tops_by_label, label_of_part, X0, Z0, footprints_mc, outward, sources, notes, neighbours=()):
    """parts: [{id, osm, polygon (shapely, MC), base_y, top_y, height_src, roof}] -> facade-geometry-1 dict.
    neighbours: MC footprints of non-member OSM buildings near the building (party walls, envelope exclusion)"""
    polys = {p['id']: p['polygon'] for p in parts}; ptops = {p['id']: (p['base_y'], p['top_y']) for p in parts}
    faces, arcs = [], []
    for p in parts:
        f, a = part_faces(p['id'], p['polygon'], make_classifier(p['id'], polys, ptops, neighbours), p['base_y'], p['top_y']); faces += f; arcs += a
    un, party = assign_columns(faces, label_grid, tops_by_label, X0, Z0, label_of_part)
    if un: notes.append(f'{un} wall cells had no facing segment (left unassigned)')
    if party: notes.append(f'{party} wall cells face a non-member building (party wall): no columns')
    for a in arcs: notes.append(f"arc {a['id']}: centre {a['center']} r={a['radius']} max residual {a['max_residual']} m over {a['segments']} segments")
    # envelope = member footprints + every part polygon (a part is never a hole, even off a wrong OSM outline) + the outward strip:
    # each column cell dilated by `outward` with a SQUARE kernel, so diagonal outer-corner cells are inside (square/mitre join)
    strip = sorted({(c[0] + dx, c[1] + dz) for f in faces for c in f['columns'] for dx in range(-outward, outward + 1) for dz in range(-outward, outward + 1)})
    partu = unary_union([p['polygon'] for p in parts])
    env = unary_union([orient(fp) for fp in footprints_mc] + [partu] + [box(x, z, x + 1, z + 1) for x, z in strip])
    env = env.buffer(0.01, join_style=2).simplify(0.05)
    # holes are only (a) true courtyards: member footprint ring holes that no part covers, or (b) neighbour footprints (below)
    env = unary_union([Polygon(g.exterior) for g in getattr(env, 'geoms', [env]) if g.geom_type == 'Polygon'])
    court = unary_union([Polygon(r) for fp in footprints_mc for g in getattr(fp, 'geoms', [fp]) for r in g.interiors]).difference(partu)
    strip_u = unary_union([box(x, z, x + 1, z + 1) for x, z in strip]) if strip else None
    if not court.is_empty: env = env.difference(court.difference(strip_u) if strip_u is not None else court)
    if neighbours:                                                        # never any cell inside a neighbouring building, except where our own part is
        nb = unary_union(list(neighbours)); ov = sum(fp.intersection(nb).area for fp in footprints_mc); op = partu.intersection(nb).area
        if ov > 1: notes.append(f'non-member buildings overlap the footprint by {ov:.0f} m2; removed from the envelope outside our parts')
        if op > 1: notes.append(f'non-member buildings overlap our parts by {op:.0f} m2; kept (parts are never subtracted)')
        env = env.difference(nb.difference(partu))
    pieces = sorted([g for g in getattr(env, 'geoms', [env]) if g.geom_type == 'Polygon' and g.area > 0.5], key=lambda g: (-g.area, g.bounds))
    holes = [[[r3(x), r3(z)] for x, z in r.coords[:-1]] for g in pieces for r in g.interiors]
    envd = dict(polygon_mc=[[r3(x), r3(z)] for x, z in pieces[0].exterior.coords[:-1]],
                y=[min(p['base_y'] for p in parts), max(p['top_y'] + (0 if p['height_src'].startswith('accepted') else PARAPET) for p in parts)])
    if len(pieces) > 1:
        envd['parts_mc'] = [[[r3(x), r3(z)] for x, z in g.exterior.coords[:-1]] for g in pieces]
        notes.append(f'envelope has {len(pieces)} disjoint pieces; all in envelope.parts_mc (contract extension)')
    if holes:
        envd['holes_mc'] = holes
        notes.append(f'envelope has {len(holes)} hole(s) (enclosed neighbour buildings or courtyards); in envelope.holes_mc (contract extension)')
    out = dict(version=VERSION, bid=bid, members=list(members), geometry_sha='', generator=GENERATOR, sources=sources,
               parts=[dict(id=p['id'], osm=p.get('osm'), polygon_mc=[[r3(x), r3(z)] for x, z in orient(p['polygon'], -1.0).exterior.coords[:-1]],
                           base_y=int(p['base_y']), top_y=int(p['top_y']), height_src=p['height_src'], roof=p['roof']) for p in parts],
               faces=faces, envelope=envd, notes=notes)
    out['geometry_sha'] = canon_sha(out)
    return out


# ---------------------------------------------------------------- build (projection + LiDAR + shells)
def build(bid, members=None, package_parts=None, cache=r'C:\BTE-Migration\shelltool\test-cache-v06', outward=1, use_accepted=True, geo=None, max_nodes=300, grids=None):
    from pyproj import Transformer
    from geo import Geo
    members = sorted(set(members or [bid]) | {bid}); own_geo = geo is None; geo = geo or Geo(); notes = []
    try:
        F = osm_feats(members); miss = [m for m in members if m not in F]
        if miss: raise ValueError(f'not in the OSM index: {miss}')
        to_utm = Transformer.from_crs(4326, 32611, always_xy=True).transform
        fps_mc = {}
        for m in members:
            g = F[m]['geom']; gs = list(getattr(g, 'geoms', [g]))
            fps_mc[m] = unary_union([Polygon(geo.fwd(list(p.exterior.coords)), [geo.fwd(list(r.coords)) for r in p.interiors]) for p in gs])
        allc = [c for fp in fps_mc.values() for p in getattr(fp, 'geoms', [fp]) for c in p.exterior.coords] + [tuple(c) for p in (package_parts or []) for c in p['polygon_mc']]
        X0, Z0 = int(math.floor(min(p[0] for p in allc))) - 2, int(math.floor(min(p[1] for p in allc))) - 2
        NX, NZ = int(math.ceil(max(p[0] for p in allc))) - X0 + 3, int(math.ceil(max(p[1] for p in allc))) - Z0 + 3
        pts = [(X0 + i + 0.5, Z0 + j + 0.5) for j in range(NZ) for i in range(NX)]
        ll = np.array(geo.inv(pts)); E, N = (a.reshape(NZ, NX) for a in to_utm(ll[:, 0], ll[:, 1]))
        Gs = {m: transform(to_utm, F[m]['geom']) for m in members}
        member_mask = {m: contains_xy(Gs[m], E, N) for m in members}           # the shell planner's cell rule
        fp_cells = np.zeros((NZ, NX), bool)
        for v in member_mask.values(): fp_cells |= v
        shells = {m: accepted_shell(m) for m in members} if use_accepted else {}
        shell_src = sorted({s[1] for s in shells.values() if s})
        need_tpm = any(not shells.get(m) for m in members)
        ground = np.floor(geo.heights([tuple(p) for p in ll])).astype(int).reshape(NZ, NX) if need_tpm else None
        ub = unary_union(list(Gs.values())).bounds; ub = (ub[0] - 10, ub[1] - 10, ub[2] + 10, ub[3] + 10)
        lid, lerr = lidar(ub, os.path.join(cache, 'ept'), max_nodes=max_nodes)
        H = ndsm_sampler(lid, ub)(E, N) if lid else np.full((NZ, NX), np.nan)
        if not lid: notes.append('LiDAR unavailable: ' + '; '.join(lerr))
        parts, L, tops_by_label, label_of_part = [], np.zeros((NZ, NX), np.int32), {}, {}
        def add_part(pid, osm, poly, mask, base, top, hsrc, roof):
            lab = len(parts) + 1; parts.append(dict(id=pid, osm=osm, polygon=poly, base_y=int(base), top_y=int(top), height_src=hsrc, roof=roof))
            L[mask & (L == 0)] = lab; tops_by_label[lab] = int(top); label_of_part[lab] = pid
        def measured(poly):
            inner = poly.buffer(-1.0); inner = inner if not inner.is_empty else poly
            cm = contains_xy(inner, X0 + np.arange(NX)[None, :] + 0.5 + 0 * np.arange(NZ)[:, None], Z0 + np.arange(NZ)[:, None] + 0.5 + 0 * np.arange(NX)[None, :])
            return roof_top(H, cm)
        def roof_of(tags, rt):
            shape = (tags or {}).get('roof:shape')
            if shape: return dict(shape=shape, parapet=shape == 'flat', evidence='osm')
            return dict(shape='flat', parapet=True, evidence='measured' if rt and rt['p90'] - rt['median'] < 1.5 else 'inferred')
        pk, Hs = 0, None
        CXg, CZg = X0 + np.arange(NX)[None, :] + 0.5 + 0 * E, Z0 + np.arange(NZ)[:, None] + 0.5 + 0 * E
        def given_part(pid, osm, poly, tags, top, pbase, base, mask, member):
            """package or OSM building:part -> part; LiDAR validates (package/accepted top wins, LiDAR wins over OSM tags)"""
            rt = measured(poly)
            try: mh = float(str(tags['min_height']).replace(' m', '')) if 'min_height' in tags else 3.5 * float(tags.get('building:min_level', 0))
            except ValueError: mh = 0.0
            pb = pbase if pbase is not None else base + int(round(mh))
            if top is not None:
                hs = 'package top_y'
                if rt: hs += f"; lidar p90 {base + round(rt['p90'])}"
                if rt and abs(base + round(rt['p90']) - top) > 2: notes.append(f'{pid}: package top {top} vs LiDAR p90 {base + round(rt["p90"])} (> 2 m apart)')
            elif rt and rt['raised'] >= 0.5:
                top, hs = base + int(round(rt['p90'])), f"lidar {lid['name']} ({lid['year']}) p90 {rt['p90']:.1f} m, median {rt['median']:.1f}, n={rt['n']}, machinery cells excluded {rt['machinery_cells']}"
                if tags.get('height'): hs += f"; osm height {tags['height']}"
            elif tags.get('height'): top, hs = base + int(round(float(str(tags['height']).split()[0]))), 'osm height'
            elif tags.get('building:levels'): top, hs = base + int(round(3.5 * float(tags['building:levels']))), 'osm building:levels x 3.5 m (inferred)'
            else: notes.append(f'{pid}: no height evidence; part skipped (unresolved)'); return
            add_part(pid, osm or member, poly, mask, pb, top, hs, roof_of(tags, rt))
        # 1. accepted shells: frozen cells, tops and grounds win over everything
        for m in members:
            sh = shells.get(m)
            if not sh: continue
            mm, tags = member_mask[m], F[m]['tags']
            cols, src = sh; sm = np.zeros((NZ, NX), bool); tg = np.zeros((NZ, NX), np.int32); gg = np.zeros((NZ, NX), np.int32)
            for (x, z), (g, t) in cols.items():
                i, j = z - Z0, x - X0
                if 0 <= i < NZ and 0 <= j < NX: sm[i, j] = True; tg[i, j] = t; gg[i, j] = g
            walls = shell_wall_cells(cols); base = int(np.median([cols[k][0] for k in walls])) if walls else int(np.median(gg[sm]))
            extra = int((mm & ~sm).sum())
            if extra: notes.append(f'{m}: {extra} footprint cells are not in the accepted shell (kept out; shell is authoritative)')
            regs = []
            for t in sorted(set(tg[sm].tolist())):
                lab, nn = ndi.label(sm & (tg == t)); regs += [(lab == r, t) for r in range(1, nn + 1)]
            polys = [fps_mc[m]] if len(regs) == 1 else regions_polys(regs, X0, Z0, fps_mc[m], sm | mm)
            for (reg, t), poly in zip(regs, polys):
                add_part(f'p{pk}', m, poly, reg, base, t, f'accepted shell top ({src.split(":", 1)[1]})', roof_of(tags, None)); pk += 1
        free = [m for m in members if not shells.get(m)]
        # 2. package parts describe the whole building and may extend past wrong OSM outlines (cells claimed by a shell stay the shell's)
        if package_parts:
            for p in package_parts:
                poly = Polygon(p['polygon_mc'])
                if not poly.is_valid:
                    fixed = poly.buffer(0); poly = max(getattr(fixed, 'geoms', [fixed]), key=lambda g: g.area)
                    notes.append(f"{p['id']}: invalid package polygon repaired (buffer 0, largest piece {poly.area:.0f} m2)")
                ov = {m: fps_mc[m].intersection(poly).area for m in members}
                m = p.get('member') or max(sorted(ov), key=lambda k: ov[k])
                if shells.get(m): notes.append(f"{p['id']}: {m} has an accepted shell; package part ignored (shell wins)"); continue
                mask = contains_xy(poly, CXg, CZg); mb = mask & member_mask[m]
                base = int(np.median(ground[mb if mb.any() else mask]))
                given_part(p['id'], p.get('osm'), poly, p.get('tags', {}), p.get('top_y'), p.get('base_y'), base, mask, m)
            for m in free:
                rest = member_mask[m] & (L == 0)
                if rest.sum() >= 25: notes.append(f'{m}: {int(rest.sum())} footprint cells outside every package part (not faced)')
            free = []
        # 3. OSM building:parts, else the shell planner's LiDAR segmentation, else an OSM height
        for m in free:
            mm, tags = member_mask[m], F[m]['tags']
            base = int(np.median(ground[mm])) if mm.any() else 0
            osm_parts = [p for p in osm_parts_within(F[m]['geom']) if p['geom'].geom_type == 'Polygon']
            if osm_parts:
                for p in osm_parts:
                    poly = Polygon(geo.fwd(list(p['geom'].exterior.coords)))
                    pm = mm & contains_xy(poly, CXg, CZg)
                    given_part(f'p{pk}', p['id'], poly, p['tags'], None, None, int(np.median(ground[pm])) if pm.any() else base, pm, m); pk += 1
                rest = mm & (L == 0)
                if rest.sum() >= 25: notes.append(f'{m}: {int(rest.sum())} footprint cells outside every building:part (not faced)')
                continue
            if lid is not None:
                if Hs is None: Hs = ndi.median_filter(np.nan_to_num(H, nan=-1), size=3)   # == planner's generic_filter(nanmedian): no NaN left after nan_to_num
                h = np.where(mm, Hs, np.nan); h[h < 2.0] = np.nan
                sp = snowball_parts(h, mm)
                ids = [p for p in np.unique(sp) if p > 0]
                if ids and (mm & (sp > 0)).sum() >= 0.5 * mm.sum():
                    if (mm & (sp == 0)).any(): notes.append(f'{m}: {int((mm & (sp == 0)).sum())} footprint cells without LiDAR height >= 2 m (open; no wall, as in the shell planner)')
                    ids = sorted(ids, key=lambda p: -int((sp == p).sum()))
                    regs = [(sp == p, float(np.nanmedian(h[sp == p]))) for p in ids]
                    polys = [fps_mc[m]] if len(ids) == 1 and regs[0][0].sum() >= 0.97 * mm.sum() else regions_polys(regs, X0, Z0, fps_mc[m], mm)
                    for (reg, hm), poly in zip(regs, polys):
                        rt = measured(poly) if poly.area > 9 else None; snow = base + int(round(hm))
                        top = base + int(round(rt['p90'])) if rt else snow
                        hs = (f"lidar {lid['name']} ({lid['year']}) p90 {rt['p90']:.1f} m, median {rt['median']:.1f}, n={rt['n']}, machinery cells excluded {rt['machinery_cells']}; snowball top {snow}"
                              if rt else f"lidar {lid['name']} snowball median (part too small for p90)")
                        add_part(f'p{pk}', m, poly, reg, base, top, hs, roof_of(tags, rt)); pk += 1
                    continue
            ht = str(tags.get('height', '')).replace(' m', '')
            if ht.replace('.', '', 1).isdigit():
                add_part(f'p{pk}', m, fps_mc[m], mm, base, base + int(round(float(ht))), 'osm height (LiDAR unusable)', roof_of(tags, None)); pk += 1
            else: notes.append(f'{m}: no reliable height (LiDAR unusable, no OSM height); unresolved, no faces')
        if grids is not None: grids.update(X0=X0, Z0=Z0, H=H, L=L, member_mask=member_mask, ground=ground, fps_mc=fps_mc, lidar=lid and lid['name'])
        if not parts: raise ValueError('no part with height evidence')
        sources = dict(osm=dict(index=INDEX, ids=members), lidar=dict(dataset=f"{lid['name']} ({lid['year']})" if lid else None, points=lid['points'] if lid else 0),
                       shell=shell_src[0] if len(shell_src) == 1 else ('|'.join(shell_src) if shell_src else ('snowball-rules' if use_accepted else 'snowball-rules (accepted shells ignored)')))
        nbs = neighbour_footprints(unary_union([F[m]['geom'] for m in members]), set(members), geo)
        return assemble(bid, members, parts, L, tops_by_label, label_of_part, X0, Z0, list(fps_mc.values()), outward, sources, notes, nbs)
    finally:
        if own_geo: geo.close()


def write(cache, d):
    p = os.path.join(cache, 'facade', 'buildings', d['bid'], 'geometry.json'); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w') as f: json.dump(d, f, sort_keys=True, separators=(',', ':'))
    os.replace(p + '.tmp', p); return p


# ---------------------------------------------------------------- self-check (pure geometry, no projection or LiDAR)
def _synthetic(polys_tops, outward=1, neighbours=(), footprints=None):
    """polys_tops: {pid: (Polygon in MC, base, top)} -> geometry via the same voxeliser, with MC containment instead of UTM"""
    allc = [c for p, _, _ in polys_tops.values() for c in p.exterior.coords]
    X0, Z0 = int(math.floor(min(c[0] for c in allc))) - 2, int(math.floor(min(c[1] for c in allc))) - 2
    NX, NZ = int(math.ceil(max(c[0] for c in allc))) - X0 + 3, int(math.ceil(max(c[1] for c in allc))) - Z0 + 3
    xs, zs = np.meshgrid(X0 + np.arange(NX) + 0.5, Z0 + np.arange(NZ) + 0.5)
    L = np.zeros((NZ, NX), np.int32); tb, lp, parts = {}, {}, []
    for k, (pid, (poly, b, t)) in enumerate(sorted(polys_tops.items()), 1):
        L[contains_xy(poly, xs, zs) & (L == 0)] = k; tb[k] = t; lp[k] = pid
        parts.append(dict(id=pid, osm=None, polygon=poly, base_y=b, top_y=t, height_src='synthetic', roof=dict(shape='flat', parapet=True, evidence='inferred')))
    return assemble('t', ['t'], parts, L, tb, lp, X0, Z0, footprints or [p for p, _, _ in polys_tops.values()], outward, {}, [], neighbours), L


def _check_faces(d, L):
    seen = {}
    for f in d['faces']:
        for s0, s1 in zip(f['segments'], f['segments'][1:]): assert abs(s0['u1'] - s1['u0']) < 1e-6, ('u not continuous', f['id'])
        assert abs(f['segments'][-1]['u1'] - f['length_u']) < 1e-6
        us = [c[2] for c in f['columns']]; assert us == sorted(us)
        for c in f['columns']:
            assert (c[0], c[1]) not in seen, ('corner owned twice', c, seen.get((c[0], c[1])), f['id']); seen[(c[0], c[1])] = f['id']
            assert -0.01 <= c[2] <= f['length_u'] + 0.01 and abs(c[4]) + abs(c[5]) == 1
    return seen


if __name__ == '__main__' and len(sys.argv) == 1:
    # 1. rotated rectangle at 11.4 deg, negative coordinates: 4 faces, continuous u, walls = outer ring, each cell once
    th = math.radians(11.4); cx, cz = -12159724.0, -5450625.0
    R = lambda u, v: (cx + u * math.cos(th) - v * math.sin(th), cz + u * math.sin(th) + v * math.cos(th))
    rect = Polygon([R(-16.5, -11), R(16.5, -11), R(16.5, 11), R(-16.5, 11)])
    d, L = _synthetic({'p0': (rect, 924, 930)})
    assert len(d['faces']) == 4 and all(f['edge'] == 'exterior' and len(f['segments']) == 1 for f in d['faces']), [f['segments'] for f in d['faces']]
    assert sorted(round(f['length_u'], 1) for f in d['faces']) == [22.0, 22.0, 33.0, 33.0]
    seen = _check_faces(d, L)
    ring = {(int(x), int(z)) for (iz, ix) in zip(*np.nonzero(L)) for x, z in [(int(ix), int(iz))] if any(not (0 <= iz + dz < L.shape[0] and 0 <= ix + dx < L.shape[1]) or L[iz + dz, ix + dx] == 0 for dx, dz in DIRS)}
    assert len(seen) == len(ring), (len(seen), len(ring))
    assert all(x < 0 and z < 0 for x, z in seen)
    north = next(f for f in d['faces'] if f['label'].startswith('N')); s = north['segments'][0]
    assert s['normal'][1] < -0.9 and s['b'][0] < s['a'][0], 'N face: outward -z, u grows westward (left->right seen from outside)'
    assert d['envelope']['y'] == [924, 931] and d['geometry_sha'] == canon_sha(d)
    d2, _ = _synthetic({'p0': (rect, 924, 930)}); assert d2['geometry_sha'] == d['geometry_sha'], 'deterministic'
    # 2. sampled arc (quarter circle r=20, 2 deg samples) + straight legs: one arc face with continuous u
    arcp = [(-12159700 + 20 * math.cos(math.radians(a)), -5450600 - 20 * math.sin(math.radians(a))) for a in range(0, 91, 2)]
    shape = Polygon(arcp + [(-12159700, -5450600)])
    d, L = _synthetic({'p0': (shape, 900, 910)}); _check_faces(d, L)
    af = [f for f in d['faces'] if any(s['curve'] for s in f['segments'])]
    assert len(af) == 1 and len(af[0]['segments']) >= 5 and abs(af[0]['length_u'] - 20 * math.pi / 2) < 0.6, [(f['length_u'], len(f['segments'])) for f in af]
    assert any('r=20.0' in n or 'r=19.9' in n or 'r=20.1' in n for n in d['notes']), d['notes']
    assert len(d['faces']) == 3
    # 3. shared edges: tower beside a podium on the same footprint line
    pod = Polygon([(-100, -100), (-80, -100), (-80, -90), (-100, -90)]); tow = Polygon([(-80, -100), (-70, -100), (-70, -90), (-80, -90)])
    d, L = _synthetic({'p0': (pod, 60, 66), 'p1': (tow, 60, 90)}); seen = _check_faces(d, L)
    sh = [f for f in d['faces'] if f['edge'] == 'shared']; st = [f for f in d['faces'] if f['part'] == 'p1' and f['base_y'] == 66]
    assert len(sh) == 1 and sh[0]['part'] == 'p0' and not sh[0]['columns'], sh
    assert len(st) == 1 and st[0]['edge'] == 'exterior' and len(st[0]['columns']) == 10 and all(c[0] == -80 and c[4] == -1 for c in st[0]['columns'])
    # 4. corner ownership: the 4 corner cells of the podium each belong to exactly one face (checked in _check_faces); they exist
    assert {(-100, -100), (-100, -91), (-81, -100), (-81, -91)} <= set(seen) and {(-71, -100), (-71, -91)} <= set(seen)
    # 5. party wall: a separate (non-member) building against the whole east side -> party face, no columns, envelope excludes it
    bld = Polygon([(-100, -100), (-80, -100), (-80, -90), (-100, -90)]); nb = Polygon([(-80, -102), (-60, -102), (-60, -88), (-80, -88)])
    d, L = _synthetic({'p0': (bld, 60, 66)}, neighbours=[nb]); seen = _check_faces(d, L)
    pf = [f for f in d['faces'] if f['edge'] == 'party']
    assert len(pf) == 1 and not pf[0]['columns'] and abs(pf[0]['length_u'] - 10) < 1e-6, [(f['edge'], f['length_u']) for f in d['faces']]
    assert not any(x == -81 and -99 <= z <= -92 for x, z in seen), 'no column on the party wall'
    env = Polygon(d['envelope']['polygon_mc']); assert env.intersection(nb).area < 1e-6 and env.contains(Point(-80.5, -95))
    # 6. partial party wall: neighbour against the north half of the east side only -> that side splits into exterior + party
    nb2 = Polygon([(-80, -104), (-60, -104), (-60, -95), (-80, -95)])
    d, L = _synthetic({'p0': (bld, 60, 66)}, neighbours=[nb2]); seen = _check_faces(d, L)
    east = [f for f in d['faces'] if f['label'].startswith('E')]
    assert sorted((f['edge'], round(f['length_u'], 3)) for f in east) == [('exterior', 5.0), ('party', 5.0)], [(f['edge'], f['length_u'], f['segments']) for f in east]
    ext = next(f for f in east if f['edge'] == 'exterior'); assert ext['columns'] and all(c[0] == -81 and c[1] >= -95 and c[4] == 1 for c in ext['columns']), ext['columns']
    assert Polygon(d['envelope']['polygon_mc']).intersection(nb2).area < 1e-6
    # 7. a part enclosed by another member's ring is NOT a hole; a true courtyard (no part covers it) IS one
    outer = Polygon([(-200, -200), (-160, -200), (-160, -160), (-200, -160)], [[(-190, -190), (-170, -190), (-170, -170), (-190, -170)]])
    inner = Polygon([(-190, -190), (-170, -190), (-170, -170), (-190, -170)])
    d, L = _synthetic({'p0': (outer, 60, 70), 'p1': (inner, 60, 120)}, footprints=[outer]); _check_faces(d, L)
    assert 'holes_mc' not in d['envelope'] and Polygon(d['envelope']['polygon_mc']).contains(Point(-180, -180)), d['envelope'].get('holes_mc')
    d, L = _synthetic({'p0': (outer, 60, 70)}); _check_faces(d, L)
    assert len(d['envelope'].get('holes_mc', [])) == 1, 'a true courtyard stays a hole'
    # 8. square-join strip (11.4 deg rectangle): every cell within Chebyshev `outward` of a column, diagonal outer corners included, is in the envelope
    for ow in (1, 2):
        d, L = _synthetic({'p0': (rect, 924, 930)}, outward=ow); env = Polygon(d['envelope']['polygon_mc'])
        cols = {(c[0], c[1]) for f in d['faces'] for c in f['columns']}
        need = {(x + dx, z + dz) for x, z in cols for dx in range(-ow, ow + 1) for dz in range(-ow, ow + 1)}
        corners = [(x + a * ow, z + b * ow) for x, z in cols for a in (-1, 1) for b in (-1, 1) if not rect.contains(Point(x + a * ow + 0.5, z + b * ow + 0.5))]
        assert corners and all(env.contains(Point(x + 0.5, z + 0.5)) for x, z in need), 'strip cells (incl. diagonal corners) outside the envelope'
    print('facade_geometry self-check ok')
elif __name__ == '__main__':
    a = sys.argv; cache, bid = a[1], a[2]
    mem = [a[i + 1] for i, x in enumerate(a) if x == '--member']
    d = build(bid, members=[bid] + mem, cache=cache, use_accepted='--no-accepted' not in a)
    p = write(cache, d)
    print(p, d['geometry_sha'][:16], len(d['parts']), 'parts', len(d['faces']), 'faces', sum(len(f['columns']) for f in d['faces']), 'columns')
