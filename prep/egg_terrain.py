"""egg_terrain.py - Egg terrain correction (v0.5): plan(ctx) -> egg-plan-1 dict (docs/egg/ARCHITECTURE.md, rules: docs/egg/TERRAIN-SPEC.md).
Moves the baseline ground surface of ELIGIBLE undeveloped columns toward the measured USGS 3DEP lidar ground (class 2), bounded:
 target = 5x5-smoothed (lidar_ground_m + BIAS_M)   (BIAS_M: one fixed regional world-vs-lidar constant, never per tile -> no seams)
 a column changes only where |target - base_y| >= THRESH, by at most max_cut / max_fill, feathered to zero next to data-protected columns.
Order independence: target and feather cap are computed over the tile + 32-block ring ONLY from data that does not depend on capture timing
(lidar points within a fixed radius, OSM/landcover protection, water). The baseline (presence, base_y, cover) is read for the tile's own
columns only and decides whether and how far each column is written, so a column's change is the same whichever tile is prepared first.
Never: protected/covered/unknown columns, mapped water or washes, lidar water (class 9), cave filling, deep excavation, whole-tile flattening.
python egg_terrain.py [--measure-bias]   self-check (no server) / re-measure the regional bias from the pilot baseline."""
import json, os, sys, time
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from pyproj import Transformer
from shapely import contains_xy
from shapely.geometry import box
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import egg_common as E, egg_provenance, ept_cached

EPT = 'https://s3-us-west-2.amazonaws.com/usgs-lidar-public/'
DATASETS = [('NV_ClarkCo_2_B22', 2022), ('NV_ClarkCo_1_B22', 2022), ('NV_ClarkCounty_B1_2018', 2018), ('NV_LasVegasValley_2010', 2010)]   # = shelltool_prep
# World minus lidar ground, measured ONCE over the pilot baseline (python egg_terrain.py --measure-bias; provenance in TERRAIN-SPEC.md section 4).
# Regional constant: per-tile values would make seams. Do not add it twice: it is applied only here, to the lidar side.
BIAS_M = -1.02
BIAS_PROVENANCE = ('median(base_y + 0.5 - lidar_ground) = -1.024 m over 145388 uncovered natural columns of the 910 pilot-generated chunks '
                   '(69 tiles), NV_ClarkCo_2_B22 class 2; std 1.92; measured 2026-10-29; TERRAIN-SPEC.md section 4')
THRESH = 2.0          # blocks: smoothed residuals below this are lidar/model noise and are left alone
FEATHER = 4.0         # blocks: change ramps from 0 at an ineligible column to full at this distance
WATER_KEEP = 6        # blocks kept untouched around mapped water / washes / lidar class 9 (no cuts below water, no filling washes)
R1, R2, MIN_PTS = 1.0, 2.5, 3     # m: ground-point search radius (wider radius only as a gap fill); points needed per column
MIN_DENSITY, MIN_COVER = 0.5, 0.8  # ground pts per m^2 over the tile; share of eligible tile columns with a ground value
RING, W = E.RING, E.TILE + 2 * E.RING
SUB = {'grass_block': 'minecraft:dirt', 'podzol': 'minecraft:dirt', 'smooth_sandstone': 'minecraft:sandstone'}   # buried surface -> subsurface
LANDSCAPED = {'park', 'garden', 'golf_course', 'pitch', 'playground', 'sports_centre', 'stadium', 'dog_park', 'schoolyard', 'track'}
LANDUSE_LS = {'grass', 'recreation_ground', 'cemetery', 'village_green', 'flowerbed'}
DEVELOPED = {'residential', 'commercial', 'industrial', 'retail', 'construction', 'railway', 'military', 'garages', 'education', 'institutional',
             'religious', 'landfill', 'quarry', 'brownfield'}          # same classes as egg_prep.landcover_bits (keep in sync)


def sub_of(state):
    n = state.split('[', 1)[0]; return SUB.get(n.removeprefix('minecraft:'), n)


def _window_protect(geo, tx, tz, landcover):
    """protection bits over the window: OSM roads/buildings/Strip per tile (egg_common) + landcover water/landscaped/developed"""
    m = np.zeros((192, 192), np.uint8)
    for j in (-1, 0, 1):
        for i in (-1, 0, 1): m[(j + 1) * 64:(j + 2) * 64, (i + 1) * 64:(i + 2) * 64] = E.protection_mask(geo, tx + i, tz + j)[0]
    m = m[64 - RING:128 + RING, 64 - RING:128 + RING]
    feats = None if landcover is None else landcover()
    if feats is None: return m, None                          # index missing / not covering: caller refuses (never 'no water')
    x0, z0 = E.tile_bounds(tx, tz)['blocks'][:2]; X0, Z0 = x0 - RING, z0 - RING
    xs, zs = np.meshgrid(np.arange(X0, X0 + W) + 0.5, np.arange(Z0, Z0 + W) + 0.5); n = 0
    for f in feats:
        t, k = f['tags'], f['kind']
        if k == 'water' or t.get('natural') in ('water', 'wetland') or t.get('landuse') in ('reservoir', 'basin') or 'waterway' in t: bit = E.P_WATER
        elif (k == 'leisure' and t.get('leisure') in LANDSCAPED) or (k == 'landuse' and t.get('landuse') in LANDUSE_LS): bit = E.P_LANDSCAPED
        elif k == 'landuse' and t.get('landuse') in DEVELOPED: bit = E.P_DEVELOPED
        else: continue
        try: g = E.to_block(geo, f['geom'])
        except Exception: continue
        if g.geom_type in ('LineString', 'MultiLineString'): g = g.buffer(3)
        if g.intersects(box(X0, Z0, X0 + W, Z0 + W)): m[contains_xy(g, xs, zs)] |= bit; n += 1
    return m, n


def lidar_ground(geo, tx, tz, snow_cache):
    """per-column lidar ground (m, NAVD88 orthometric) over the window: median of class-2 z within R1 (gap fill R2) of the block centre.
    Returns (ground[W,W], npts[W,W], water[W,W] bool, info) or (None, None, None, info) without coverage."""
    x0, z0 = E.tile_bounds(tx, tz)['blocks'][:2]
    ll = np.array(geo.inv([(x0 - RING + i + 0.5, z0 - RING + j + 0.5) for j in range(W) for i in range(W)]))
    to_utm = Transformer.from_crs(4326, 32611, always_xy=True).transform
    E_, N_ = to_utm(ll[:, 0], ll[:, 1])
    m2u = Transformer.from_crs(3857, 32611, always_xy=True).transform; u2m = Transformer.from_crs(32611, 3857, always_xy=True).transform
    pad = R2 + 5; bx, by = u2m([E_.min() - pad, E_.max() + pad, E_.min() - pad, E_.max() + pad], [N_.min() - pad, N_.min() - pad, N_.max() + pad, N_.max() + pad])
    bb = (min(bx), min(by), max(bx), max(by)); errs = []
    for name, year in DATASETS:
        try: r = ept_cached.fetch(EPT + name, bb, os.path.join(snow_cache, 'ept'), max_nodes=600)
        except ept_cached.Limit as ex: errs.append(f'{name}: {ex}'); continue
        if r is None: errs.append(f'{name}: outside published bounds'); continue
        if (r['cls'] == 2).sum() < 50: errs.append(f'{name}: too few ground points'); continue
        X, Y = m2u(r['x'], r['y']); q = np.c_[E_, N_]
        g = r['cls'] == 2; tg = cKDTree(np.c_[X[g], Y[g]]); Zg = r['z'][g]
        ground = np.full(len(q), np.nan); npts = np.zeros(len(q), int)
        for rad in (R1, R2):
            todo = np.nonzero(np.isnan(ground))[0]
            for k, idx in zip(todo, tg.query_ball_point(q[todo], rad)):
                if len(idx) >= MIN_PTS: ground[k] = np.median(Zg[idx]); npts[k] = len(idx)
        w = r['cls'] == 9; water = np.zeros(len(q), bool)
        if w.any(): water = np.isfinite(cKDTree(np.c_[X[w], Y[w]]).query(q, distance_upper_bound=2.0)[0])
        info = dict(dataset=name, year=year, ept=EPT + name, nodes=r['nodes'], fetched=r['fetched'], reused=r['reused'], ground_points=int(g.sum()), errors=errs)
        return ground.reshape(W, W), npts.reshape(W, W), water.reshape(W, W), info
    return None, None, None, dict(errors=errs)


def surface(ctx):
    """data-only field over the window (NO baseline): target surface + feather caps, a pure function of lidar + protection + water"""
    tx, tz, geo = ctx['tx'], ctx['tz'], ctx['geo']; L = ctx['limits']
    prot, nlc = _window_protect(geo, tx, tz, ctx.get('landcover'))
    out = dict(prot=prot, landcover_features=nlc, info={})
    if nlc is None: return out
    ground, npts, water, info = lidar_ground(geo, tx, tz, ctx['snow_cache']); out['info'] = info
    if ground is None: return out
    wet = ((prot & E.P_WATER) > 0) | water
    if wet.any(): wet = ndi.distance_transform_edt(~wet) <= WATER_KEEP
    ok = (prot == 0) & np.isfinite(ground) & ~wet                            # anything else is protected by data
    t = np.where(ok, ground + BIAS_M, 0.0); k = ndi.uniform_filter(ok.astype(float), 5, mode='constant')
    target = np.where(ok, ndi.uniform_filter(t, 5, mode='constant') / np.maximum(k, 1e-9), np.nan)
    d = ndi.distance_transform_edt(ok) if (~ok).any() else np.full(ok.shape, 99.0)
    wgt = np.clip((d - 1) / FEATHER, 0, 1)                                   # 0 right next to a data-protected column
    out.update(ground=ground, npts=npts, water=water, wet=wet, ok=ok, target=target,
               cap_cut=np.floor(wgt * L['max_cut'] + 0.5).astype(int), cap_fill=np.floor(wgt * L['max_fill'] + 0.5).astype(int))
    return out


def change(target, cap_cut, cap_fill, by):
    """per-column change (blocks) from the data field and the column's OWN base_y: 0 below THRESH, else just enough to bring the residual
    under THRESH (floor(|r| - THRESH + 1)), capped by the feather. Per column -> independent of the neighbours' baselines."""
    r = target - (by + 0.5); a = np.abs(np.nan_to_num(r)); m = np.floor(a - THRESH + 1)
    return np.where(np.isfinite(r) & (a >= THRESH), np.where(r > 0, np.minimum(m, cap_fill), -np.minimum(m, cap_cut)), 0).astype(int), r


def plan(ctx):
    t0 = time.time(); tx, tz = ctx['tx'], ctx['tz']; L = ctx['limits']; C = E.tile_bounds(tx, tz)['blocks']; x0, z0 = C[0], C[1]
    T = np.s_[RING:RING + 64, RING:RING + 64]
    cby = np.array([[np.nan if v is None else v for v in row] for row in ctx['base_y']], float)
    nc = lambda why: E.plan('terrain', tx, tz, E.NO_COVERAGE, reasons=[why], complete=False)
    if ctx.get('landcover') is None: return nc('landcover index missing: water, washes and landscaping are unknown, nothing may be changed')   # B4
    if not np.isfinite(cby).any(): return nc('no approved baseline for this tile: provenance of the ground is unknown, nothing may be changed')
    S = surface(ctx); info = S['info']
    if S['landcover_features'] is None: return nc('landcover index does not cover this tile + ring: water, washes and landscaping are unknown')
    ccov = np.array([[c is not None for c in row] for row in ctx['base_cover']]); cprot = np.asarray(ctx['protect'])
    nat = np.array([[bool(b) and egg_provenance.natural(b) for b in row] for row in ctx['base_block']])
    unknown_osm = bool((S['prot'] & E.P_UNKNOWN).any())
    src = dict(lidar=info, bias_m=BIAS_M, bias_provenance=BIAS_PROVENANCE, crs='EPT EPSG:3857 horizontal (reprojected to UTM 11N here); z metres NAVD88 (GEOID18) orthometric',
               baseline=ctx.get('world', E.BASELINE_WORLD), params=dict(thresh=THRESH, feather=FEATHER, water_keep=WATER_KEEP, r1=R1, r2=R2, min_pts=MIN_PTS))
    st = dict(columns_known=int(np.isfinite(cby).sum()), bias_m=BIAS_M, landcover_features=S['landcover_features'], unknown_osm_in_window=unknown_osm)
    def done(status, reasons, ops=(), feats=(), complete=True):
        st['seconds'] = round(time.time() - t0, 2); return E.plan('terrain', tx, tz, status, ops, feats, reasons, src, st, complete=complete)
    if 'ground' not in S: return done(E.NO_COVERAGE, ['no USGS 3DEP lidar ground for this tile: ' + '; '.join(info.get('errors', []))], complete=False)
    free = (cprot == 0) & (S['prot'][T] == 0) & ~S['wet'][T]                 # not protected by data
    cand = free & np.isfinite(cby) & ~ccov & nat                            # ... and writable (baseline known, bare natural top)
    elig = cand & S['ok'][T]
    gaps = int((cand & ~np.isfinite(S['ground'][T])).sum())
    complete = not unknown_osm and info['dataset'] == DATASETS[0][0] and not gaps      # degraded data -> complete False
    st.update(columns_eligible=int(elig.sum()), lidar_gaps=gaps, wet_columns=int((S['wet'][T] & (cprot == 0)).sum()),
              skipped_no_baseline=int((free & ~np.isfinite(cby)).sum()), skipped_covered=int((free & np.isfinite(cby) & ccov).sum()))
    if not cand.any():
        return done(E.PROTECTED, ['no writable column: every column is protected, covered (vegetation/structure), water, or has no approved baseline'], complete=complete)
    cover = (cand & np.isfinite(S['ground'][T])).sum() / cand.sum()
    dens = float(S['npts'][T][elig].mean() / (np.pi * R1 ** 2)) if elig.any() else 0.0
    st.update(lidar_cover=round(float(cover), 3), ground_density_per_m2=round(dens, 2))
    if cover < MIN_COVER or dens < MIN_DENSITY:
        return done(E.LOW_CONFIDENCE, [f'lidar ground too sparse: {cover:.0%} of writable columns covered, {dens:.2f} pts/m2 (need {MIN_COVER:.0%}, {MIN_DENSITY})'], complete=False)
    dl, rs = change(S['target'][T], S['cap_cut'][T], S['cap_fill'][T], cby); dl = np.where(elig, dl, 0)
    ok = elig & np.isfinite(rs); ra, rb = np.abs(rs[ok]), np.abs(rs[ok] - dl[ok])
    st.update(max_residual_before=round(float(ra.max()), 2) if ok.any() else None, max_residual_after=round(float(rb.max()), 2) if ok.any() else None,
              mean_abs_residual_before=round(float(ra.mean()), 2) if ok.any() else None, mean_abs_residual_after=round(float(rb.mean()), 2) if ok.any() else None)
    lab, n = ndi.label(dl != 0)
    ops, feats, cut, fill = [], [], 0, 0
    for k in range(1, n + 1):
        fid = f'terrain:{tx}_{tz}:r{k}'; zz, xx = np.nonzero(lab == k); dd = dl[zz, xx]
        touches = bool(((zz == 0) | (zz == 63) | (xx == 0) | (xx == 63)).any())
        for j, i, d in sorted(zip(zz.tolist(), xx.tolist(), dd.tolist())):          # python ints: ops go to JSON
            x, z, y0, b = x0 + int(i), z0 + int(j), int(cby[j, i]), ctx['base_block'][j][i]; sb = sub_of(b)
            if d < 0:                                               # cut top-down, then the new top takes the old surface material
                ops += [[x, y, z, 'base', 'minecraft:air', fid, 'cut'] for y in range(y0, y0 + d, -1)]
                ops.append([x, y0 + d, z, 'base', b, fid, 'surface'])      # always: TPM puts 1 sand block over stone
                cut += int(-d)
            else:                                                   # buried surface becomes subsurface, fill bottom-up, top = surface material
                if sb != b: ops.append([x, y0, z, 'base', sb, fid, 'surface'])
                ops += [[x, y, z, 'air', b if y == y0 + d else sb, fid, 'fill'] for y in range(y0 + 1, y0 + d + 1)]
                fill += int(d)
        feats.append(dict(id=fid, kind='terrain-correction', info=f'{len(dd)} columns, cut {int(-dd[dd < 0].sum())} / fill {int(dd[dd > 0].sum())} blocks',
                          partial=touches, anchor=None, evidence=f"USGS 3DEP lidar class 2 {info['dataset']} ({info['year']}), bias {BIAS_M} m", confidence='measured'))
    st.update(columns_corrected=int((dl != 0).sum()), cut_blocks=cut, fill_blocks=fill, regions=n, ops=len(ops))
    if not ops: return done(E.NOTHING, [f'baseline already within {THRESH:g} blocks of the lidar ground (after bias) in every eligible column'], complete=complete)
    if len(ops) > L['max_ops']: return done(E.REFUSED_LIMITS, [f"{len(ops)} ops > max {L['max_ops']}: refused, nothing planned"], complete=complete)
    return done(E.READY, [f'{n} region(s): moved toward lidar ground by at most {L["max_cut"]} cut / {L["max_fill"]} fill blocks, feathered {FEATHER:g} blocks'], ops, feats, complete)


def make_ctx(geo, tx, tz, snow_cache, limits=None):
    """context like egg_prep builds it (self-checks / bias measurement): baseline from the world, OSM + landcover protection"""
    r = egg_provenance.baseline_columns(tx, tz); llb = E.tile_lonlat_box(geo, tx, tz)
    lc = (lambda kinds=None: E.query_index(E.LANDCOVER_INDEX, llb, set(kinds) if kinds else None)[0]) if os.path.exists(E.LANDCOVER_INDEX) else None
    return dict(tx=tx, tz=tz, bounds=E.tile_bounds(tx, tz), seed=1, geo=geo, cache=os.path.join(snow_cache, 'egg'), snow_cache=snow_cache,
                base_y=r['base_y'], base_block=r['base_block'], base_cover=r['base_cover'], landcover=lc,
                protect=_window_protect(geo, tx, tz, lc)[0][RING:RING + 64, RING:RING + 64], limits=limits or dict(max_cut=2, max_fill=2, max_ops=12000))


def measure_bias(geo, tiles, snow_cache):
    """world-minus-lidar (base_y + 0.5 - lidar ground) over approved baseline columns: the regional constant (run once; record the result
    in BIAS_M + TERRAIN-SPEC). 'bare' = every uncovered natural column (the datum/model offset does not depend on land use; the constant),
    'eligible' = only unprotected ones (cross-check; few, clustered, partly graded lots)."""
    vals = {'bare': [], 'eligible': []}
    for tx, tz in tiles:
        ctx = make_ctx(geo, tx, tz, snow_cache); S = surface(ctx); T = np.s_[RING:RING + 64, RING:RING + 64]
        if 'ground' not in S: continue
        by = np.array([[np.nan if v is None else v for v in row] for row in ctx['base_y']], float)
        bare = np.array([[c is None and bool(b) and egg_provenance.natural(b) for c, b in zip(rc, rb)] for rc, rb in zip(ctx['base_cover'], ctx['base_block'])])
        d = by + 0.5 - S['ground'][T]; g = np.isfinite(d) & bare
        vals['bare'] += list(d[g]); vals['eligible'] += list(d[g & S['ok'][T] & (ctx['protect'] == 0)])
    st = lambda v: dict(n=len(v), median=round(float(np.median(v)), 3), mean=round(float(np.mean(v)), 3), std=round(float(np.std(v)), 3),
                        p05=round(float(np.percentile(v, 5)), 2), p95=round(float(np.percentile(v, 95)), 2))
    return {k: st(v) for k, v in vals.items() if v}


if __name__ == '__main__':
    from geo import Geo
    SNOW = os.environ.get('MORIA_SNOW_CACHE', r'C:\BTE-Migration\shelltool\test-cache-v05')
    geo = Geo()
    if '--measure-bias' in sys.argv:          # all fully generated pilot tiles of the baseline
        tiles = [(x, z) for x in range(-190000, -189990) for z in range(-85170, -85160) if egg_provenance.baseline_columns(x, z)['columns_known']]
        print(len(tiles), 'tiles', measure_bias(geo, tiles, SNOW)); geo.close(); sys.exit()
    # Inside the 910 pilot-generated chunks there is NO sloped undeveloped land (developed Summerlin; eligible relief <= 4 blocks per tile).
    FILL = (-189993, -85164)                  # sand lot strip, 261 eligible columns: world ~2.8 blocks below lidar -> fills
    CUT = (-189998, -85167)                   # south-west pilot edge, 346 eligible columns: world above lidar -> cuts
    DI = E.tile_of_block(-12160205, -5448042)  # Desert Inn sizing tile: outside the approved baseline -> must refuse (no_coverage)
    for name, (tx, tz) in (('fill', FILL), ('cut', CUT), ('desert-inn', DI)):
        for run in ('cold', 'cached'):
            t = time.time(); p = plan(make_ctx(geo, tx, tz, SNOW)); dt = time.time() - t
            print(name, (tx, tz), run, f'{dt:.1f}s', p['status'], {k: p['stats'].get(k) for k in (
                'columns_eligible', 'columns_corrected', 'cut_blocks', 'fill_blocks', 'max_residual_before', 'max_residual_after', 'mean_abs_residual_before', 'mean_abs_residual_after', 'lidar_cover', 'ground_density_per_m2', 'ops')})
        if name == 'desert-inn': assert p['status'] == E.NO_COVERAGE and not p['ops'], p['status']; continue
        assert p['status'] == E.READY and p['stats'][name + '_blocks'] > 0 and p['complete'] is True, p['reasons']
        b = E.tile_bounds(tx, tz)['blocks']; ctx = make_ctx(geo, tx, tz, SNOW); top = {}
        for x, y, z, ex, blk, f, role in p['ops']:          # bounded, only eligible columns, surface material kept
            j, i = z - b[1], x - b[0]; y0, bb = ctx['base_y'][j][i], ctx['base_block'][j][i]
            assert ctx['protect'][j, i] == 0 and ctx['base_cover'][j][i] is None and y0 - 2 <= y <= y0 + 2, (x, y, z)
            assert role in ('cut', 'fill', 'surface') and blk in ('minecraft:air', bb, sub_of(bb)) and f.startswith(f'terrain:{tx}_{tz}:r')
            if role in ('fill', 'cut'): top[(x, z)] = (min(y, top.get((x, z), (y,))[0]) if role == 'cut' else y, blk, role)
        surf = {(o[0], o[2]): o[1] for o in p['ops'] if o[6] == 'surface'}
        for (x, z), (y, blk, role) in top.items():       # filled columns are capped with the surface; cut columns get it back one below the lowest cut
            assert blk == ctx['base_block'][z - b[1]][x - b[0]] if role == 'fill' else surf.get((x, z)) == y - 1, (x, z, role)
        assert p['stats']['max_residual_after'] <= p['stats']['max_residual_before'] and p['stats']['mean_abs_residual_after'] < p['stats']['mean_abs_residual_before']
        sl = plan(dict(ctx, limits=dict(max_cut=2, max_fill=2, max_ops=10)))
        assert sl['status'] == E.REFUSED_LIMITS and not sl['ops']
        json.dumps(p); print(json.dumps(p['ops'][:3]), p['features'][0]['id'])          # plan must be JSON as written by egg_prep
    # B4: no landcover (missing index, or not covering) -> no_coverage, never a plan
    for lc in (None, lambda kinds=None: None):
        q = plan(dict(make_ctx(geo, *FILL, SNOW), landcover=lc)); assert q['status'] == E.NO_COVERAGE and not q['ops'] and q['complete'] is False
    # M3 order independence.
    # (1) plan() never reads a neighbour's baseline: with the baseline reader disabled (= ring never generated/captured) ops are identical.
    ctx = make_ctx(geo, *FILL, SNOW); ref = plan(ctx)['ops']; real = egg_provenance.baseline_columns
    def no_ring(*a, **k): raise AssertionError('ring baseline read')
    egg_provenance.baseline_columns = no_ring
    try: assert plan(ctx)['ops'] == ref
    finally: egg_provenance.baseline_columns = real
    # (2) for 16 columns on each side of a shared edge, the change from either tile's window (with the column's own base_y) is identical.
    by_of = lambda c: np.array([[np.nan if v is None else v for v in row] for row in c['base_y']], float)
    ch = lambda S, sl, y: change(S['target'][sl], S['cap_cut'][sl], S['cap_fill'][sl], y)[0]
    R, I = slice(RING, RING + 64), lambda a, b: slice(RING + a, RING + b)
    for A0, B0 in ((FILL, (FILL[0], FILL[1] + 1)), (FILL, (FILL[0] + 1, FILL[1])), (CUT, (CUT[0] + 1, CUT[1]))):
        cA, cB = make_ctx(geo, *A0, SNOW), make_ctx(geo, *B0, SNOW); A, B = surface(cA), surface(cB); yA, yB = by_of(cA), by_of(cB)
        if B0[0] != A0[0]:   # east neighbour: A's last 16 columns sit at B's window -16..0; B's first 16 at A's window 64..80
            pa, pb = (ch(A, (R, I(48, 64)), yA[:, 48:]), ch(B, (R, I(-16, 0)), yA[:, 48:])), (ch(B, (R, I(0, 16)), yB[:, :16]), ch(A, (R, I(64, 80)), yB[:, :16]))
        else:
            pa, pb = (ch(A, (I(48, 64), R), yA[48:]), ch(B, (I(-16, 0), R), yA[48:])), (ch(B, (I(0, 16), R), yB[:16]), ch(A, (I(64, 80), R), yB[:16]))
        assert (pa[0] == pa[1]).all() and (pb[0] == pb[1]).all(), (A0, B0)
        print('seam ok', A0, B0, 'both windows give the same change for 2 x 16 x 64 edge columns; nonzero:', int((pa[0] != 0).sum() + (pb[0] != 0).sum()))
    geo.close(); print('egg_terrain ok')
