"""facade_prep.py - Facade (v0.6) preparation CLI, called by the plugin off the server thread (ARCHITECTURE.md section 2).
  facade_prep.py <cache> identify <x> <z> [--packages-dir D]
  facade_prep.py <cache> plan <bid> (--package <id> | --style <id>) [--seed N] [--packages-dir D] [--geometry <geometry.json>]
  facade_prep.py <cache> auto <bid> [--member W]... [--fallback procedural|none] [--fallback-style S] [--refresh] [--seed N] [--geometry G]
                                                          -> resolve, fetch evidence, associate, analyze, materials, compile (AUTO.md 2);
                                                             progress in buildings/<bid>/auto-progress.json, autospec-<n>.json versioned
  facade_prep.py <cache> adopt <bid>                      -> buildings/<bid>/adopt-candidates.json (read-only on every source)
  facade_prep.py validate <path>                          (package, style, component or palette; exit 1 when invalid)
  facade_prep.py inspect <plan.json> [--geometry G] [--out DIR]   per-face evidence/component summary + elevation PNG per face
Writes <cache>/facade/buildings/<bid>/{geometry,plan-<key>,manifest}.json and prints ONE JSON status line on stdout (logs go to stderr).
Deterministic, no network (geometry may read cached EPT only). Fault injection: FACADE_PREP_FAIL=geometry|compile. Owner: D1."""
import json, os, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facade_common as F
import facade_schema as S

PIPELINE = 'facade_prep v0.6.0'
t00 = time.time()
def log(*m): print('[facade-prep %.1fs]' % (time.time() - t00), *m, file=sys.stderr, flush=True)
def arg(a, name, d=None, f=str): return f(a[a.index(name) + 1]) if name in a else d
def emit(d): print(json.dumps(d, separators=(',', ':')), flush=True)


def bdir(cache, bid): return os.path.join(cache, 'facade', 'buildings', bid)


# ---------------------------------------------------------------- plan
def index_sha(path):
    """identity of the OSM index: its recorded extract sha (meta), else size + mtime (never hashes the whole file)"""
    try:
        import sqlite3
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try: meta = dict(con.execute('select k,v from meta'))
        finally: con.close()
        return str(meta.get('extract_sha256') or meta.get('built') or '') + f'|{os.path.getsize(path)}|{int(os.path.getmtime(path))}'
    except Exception: return 'missing'


def geometry_key(bid, members, parts, generator, idx):
    """review M4: sha256(bid + sorted members + package parts + geometry generator + input index sha)"""
    return F.sha_obj(dict(bid=bid, members=sorted(members), parts=parts, generator=generator, index=idx))


def get_geometry(cache, bid, members, sel, gpath=None):
    """(geometry, source): --geometry file, else B's cached geometry.json when its cache key (geometry.key) matches, else B's build()"""
    if os.environ.get('FACADE_PREP_FAIL') == 'geometry': raise RuntimeError('injected test failure (geometry)')
    if gpath: return json.load(open(gpath, encoding='utf-8')), 'file:' + gpath
    import facade_geometry as FG
    doc = sel['doc'] if sel and sel['kind'] == 'package' else {}
    parts = [x for x in doc.get('building', {}).get('parts', []) if isinstance(x, dict)] or None
    outward = int(doc.get('envelope', {}).get('outward', 1))                          # the package's validated strip width
    nodes = 4000 if doc.get('limits', {}).get('exception') else 300                   # landmark packages (written size exception) cover ~10x the LiDAR area
    key = geometry_key(bid, members, parts, FG.GENERATOR + f'|outward={outward}|nodes={nodes}', index_sha(FG.INDEX))
    p = os.path.join(bdir(cache, bid), 'geometry.json'); kp = p[:-5] + '.key'
    if os.path.exists(p) and os.path.exists(kp) and open(kp).read().strip() == key:
        g = json.load(open(p, encoding='utf-8'))
        if g.get('bid') == bid: return g, 'cached'
    g = FG.build(bid, members=members, package_parts=parts, cache=cache, outward=outward, max_nodes=nodes)
    FG.write(cache, g)
    with open(kp, 'w') as f: f.write(key)
    return g, 'built'


def protected(g):
    """the Strip exclusion (+500) from egg_common, when its polygon file exists"""
    try:
        import egg_common as E
        if not os.path.exists(E.POLYGONS): return None
        from shapely.geometry import Polygon
        return E.strip_poly().intersects(Polygon(g['envelope']['polygon_mc']))
    except Exception: return True          # fail closed: an unreadable Strip polygon refuses the plan (review pass 2)


def cmd_plan(cache, a):
    bid = a[3]; T = {}
    kind, sid = ('package', arg(a, '--package')) if '--package' in a else ('style', arg(a, '--style')) if '--style' in a else (None, None)
    pdir = arg(a, '--packages-dir'); bd = bdir(cache, bid); os.makedirs(bd, exist_ok=True)
    base = dict(bid=bid, selection=dict(kind=kind, id=sid), pipeline=PIPELINE)

    def finish(status, reasons=(), **kw):
        T['total'] = round(time.time() - t00, 3)
        out = dict(base, status=status, reasons=list(reasons)[:12], timings_s=T, **kw)
        man = os.path.join(bd, 'manifest.json')
        m = json.load(open(man)) if os.path.exists(man) else {}
        if m.get('pipeline') != PIPELINE: m = {}
        m.setdefault('selections', {})[f'{kind}:{sid}'] = dict(out, prepared=time.strftime('%Y-%m-%dT%H:%M:%S'))
        m.update(pipeline=PIPELINE, bid=bid, latest=f'{kind}:{sid}', timings_s=T)
        F.write_json(man, m, pretty=True); emit(out); sys.exit(0)

    if not sid:
        finish(F.NO_PACKAGE, ['select a package (--package <id>) or a style (--style <id>); there is no default style'],
               styles=S.list_styles(), packages=S.list_packages(pdir))
    t = time.time(); sel, assets, errs, st = S.load_selection(kind, sid, pdir); T['validate'] = round(time.time() - t, 3)
    if st == F.NO_PACKAGE:
        finish(st, errs + [f'available styles: {", ".join(S.list_styles()) or "none"}; packages: {", ".join(S.list_packages(pdir)) or "none"}'],
               styles=S.list_styles(), packages=S.list_packages(pdir))
    if st: finish(st, [f'{len(errs)} validation error(s); nothing placed'], errors=errs[:50])
    base['selection'] = {k: sel[k] for k in ('kind', 'id', 'version', 'sha256', 'inputs_sha')}
    members = sel['doc']['building']['osm'] if kind == 'package' else [bid] + [a[i + 1] for i, x in enumerate(a) if x == '--member']
    if kind == 'package' and bid not in members:          # a package describes its own buildings only, never the one you happen to target
        finish(F.NO_PACKAGE, [f'package {sid} does not describe {bid} (its buildings: {", ".join(members)})'], packages=S.list_packages(pdir))
    t = time.time()
    try: g, gsrc = get_geometry(cache, bid, members, sel, arg(a, '--geometry'))
    except Exception as ex:
        log('geometry FAILED', traceback.format_exc())
        finish(F.NO_GEOMETRY, [f'geometry failed: {type(ex).__name__}: {ex}'[:300], 'NOT empty: nothing planned; retried next time'])
    T['geometry'] = round(time.time() - t, 3)
    gerr = S.validate_geometry(g)
    if gerr: finish(F.NO_GEOMETRY, ['geometry does not follow facade-geometry-1'], errors=gerr[:50])
    if not g['faces']: finish(F.NO_GEOMETRY, ['geometry has no faces'] + list(g.get('notes') or [])[:5])
    gerr = S.check_against_geometry(sel, g)
    if gerr: finish(F.INVALID_PACKAGE, ['package does not match this building geometry'], errors=gerr[:50])
    if protected(g): finish(F.PROTECTED, ['building envelope touches the protected Strip exclusion (+500 blocks); nothing planned'])
    seed = arg(a, '--seed', None, int)
    if seed is None: seed = sel['doc'].get('seed', 1)
    import facade_compile as C
    key = C.plan_key(sel, g['geometry_sha'], seed); pp = os.path.join(bd, f'plan-{key}.json'); t = time.time()
    plan = None
    if os.path.exists(pp):
        try:
            plan = json.load(open(pp)); T['plan_cached'] = True
        except Exception: plan = None
    if plan is None:
        try:
            if os.environ.get('FACADE_PREP_FAIL') == 'compile': raise RuntimeError('injected test failure (compile)')
            plan = C.compile(g, sel, assets, seed)
        except Exception as ex:
            log('compile FAILED', traceback.format_exc())
            finish(F.INVALID_PACKAGE, [f'compile failed: {type(ex).__name__}: {ex}'[:300], 'nothing placed (report this selection/geometry pair)'])
        F.write_json(pp, plan)
    T['compile'] = round(time.time() - t, 3)
    finish(plan['status'], plan['reasons'], plan=os.path.relpath(pp, cache).replace('\\', '/'), plan_key=key, geometry_sha=g['geometry_sha'], geometry_source=gsrc,
           seed=seed, ops=len(plan['ops']), components=len(plan['components']), chunks=len(plan['chunks']), unresolved=len(plan['unresolved']),
           envelope_y=plan['envelope']['y'], limits=plan['limits'],
           evidence={f['id']: f['evidence'] for f in plan['faces']})


# ---------------------------------------------------------------- auto (docs/facade/AUTO.md section 2)
def _env(name, d, f=str):
    v = os.environ.get(name)
    try: return f(v) if v not in (None, '') else d
    except ValueError: return d


def auto_cfg(a, cache=None, members=None):
    """ONE job config for every stage (R: fetch/associate/download, V: analyze): plugin config.yml facade-* keys arrive as MORIA_FACADE_*
    environment variables (never a secret value: only the secrets file path)"""
    return dict(providers=_env('MORIA_FACADE_PROVIDERS', 'local,mapillary,panoramax,osm'),
                analyzer=_env('MORIA_FACADE_ANALYZER', 'builtin-cv'), ollama_model=_env('MORIA_FACADE_OLLAMA_MODEL', None),
                refs_dir=_env('MORIA_FACADE_REFS_DIR', F.refs_dir()), secrets_file=_env('MORIA_FACADE_SECRETS_FILE', None),
                timeout_s=_env('MORIA_FACADE_PROVIDER_TIMEOUT', 20.0, float), max_requests=_env('MORIA_FACADE_MAX_REQUESTS', 40, int),
                max_images_per_face=_env('MORIA_FACADE_MAX_IMAGES_PER_FACE', 4, int), max_age_years=_env('MORIA_FACADE_MAX_IMAGE_AGE_YEARS', 8.0, float),
                ttl_days=_env('MORIA_FACADE_EVIDENCE_TTL_DAYS', 30.0, float), ai_max_usd_per_job=_env('MORIA_FACADE_AI_MAX_USD_JOB', 0.0, float),
                ai_max_usd_per_day=_env('MORIA_FACADE_AI_MAX_USD_DAY', 0.0, float), refresh='--refresh' in a, cache=cache, members=members, geo=None)


def providers_of(cfg): return [p.strip() for p in str(cfg['providers']).split(',') if p.strip()]

def _module(name):
    """R's / V's stage modules, imported lazily: None while the module has not landed (its stage then runs a labelled stub)"""
    try: return __import__(name)
    except ModuleNotFoundError as e:
        if e.name == name: return None
        raise


_SECRET = __import__('re').compile(r'(?i)(token|key|secret|signature|sig)=([^&\s"\']+)')
def redact(s): return _SECRET.sub(r'\1=REDACTED', str(s))


def stub_evidence(bid, providers, why):
    return dict(schema='facade-evidence-1', bid=bid, providers={p: dict(status='unavailable', reason=why) for p in providers}, images=[], osm={},
                cost=dict(usd=0.0, requests=0, by_provider={}))


VOLATILE = ('fetched_at', 't', 'cached', 'cache', 'timings_s', 'created')
def evidence_sha(ev): return F.sha_obj({k: v for k, v in ev.items() if k not in VOLATILE})


def _cost(*cs):
    out = dict(usd=0.0, requests=0, by_provider={})
    for c in cs:
        if not isinstance(c, dict): continue
        out['usd'] = round(out['usd'] + float(c.get('usd') or 0), 6); out['requests'] += int(c.get('requests') or 0)
        for p, v in sorted((c.get('by_provider') or {}).items()):
            q = out['by_provider'].setdefault(p, dict(usd=0.0, requests=0))
            if not isinstance(v, dict): v = dict(requests=v)              # facade_sources reports {provider: request count}
            q['usd'] = round(q['usd'] + float(v.get('usd') or 0), 6); q['requests'] += int(v.get('requests') or 0)
    return out


def obstacles(RA, g, cfg):
    """R's occluders (non-member OSM footprints, TPM projection) with the job's one Geo (opened here when fetch did not need it).
    Raises when the index is missing: unknown occluders = no association (never blind)."""
    if cfg.get('geo') is None:
        from geo import Geo
        cfg['geo'] = Geo()
    return RA.obstacles_from_index(g, cfg['geo'])


def merge_assist(assoc, g, ev, cfg):
    """ASSISTED mode (diagnostic): <refs_dir>/<bid>/assist.json {"images": [{provider, image_id, face, corners_px: {u0v0, u1v0, u1v1, u0v1},
    image_size: [w, h], mask_px: [[x0, y0, x1, y1], ...] occluders, marked_by, note}]} - a person marked the face corners in a fetched image. Each entry becomes an accepted image
    of THAT face with that projection (pose_verified 'manual corners', assisted); every analysis check still applies. -> count"""
    d = cfg.get('refs_dir'); bid = g['bid']
    p = os.path.join(d, bid, 'assist.json') if d else None
    if not p or not os.path.exists(p): return 0
    try: items = json.load(open(p, encoding='utf-8')).get('images') or []
    except (OSError, ValueError, AttributeError): log('assist.json unreadable'); return 0
    cands = {(c.get('provider'), str(c.get('image_id'))): c for c in ev.get('candidates') or []}
    faces = {f['id']: f for f in g['faces']}; n = 0
    for it in items:
        c = cands.get((it.get('provider'), str(it.get('image_id')))); f = faces.get(it.get('face')); cp = it.get('corners_px') or {}; wh = it.get('image_size') or []
        if not c or not f or set(cp) != {'u0v0', 'u1v0', 'u1v1', 'u0v1'} or len(wh) != 2:
            log('assist entry skipped (image not fetched this job, unknown face or bad corners)', it.get('image_id'), it.get('face')); continue
        U = max(float(f.get('length_u') or 0), len(f.get('columns') or []))
        q = dict(seg=0, u0=0.0, u1=U, y0=f['base_y'], y1=f['top_y'] + 1,
                 corners_norm=[[cp[k][0] / wh[0], cp[k][1] / wh[1]] for k in ('u0v0', 'u1v0', 'u1v1', 'u0v1')])
        acc = dict({k: c.get(k) for k in ('provider', 'image_id', 'captured_at', 'url', 'path', 'licence', 'attribution')},
                   dist_m=None, view_angle_deg=None, assoc_score=None, pose_verified='manual corners', assisted=True,
                   marked_by=str(it.get('marked_by') or 'unknown')[:80], projection=dict(model='perspective', quads=[q]),
                   # occluders the person marked (vehicles, poles): those pixels are unknown, never read as wall or glass
                   mask_norm=[[r[0] / wh[0], r[1] / wh[1], r[2] / wh[0], r[3] / wh[1]] for r in (it.get('mask_px') or []) if len(r) == 4][:16],
                   # ASSISTED zones the person drew on this photo (glazing / material / awning polygons, image pixels); part of the association sha
                   labels=[dict(kind=str(l.get('kind')), role=l.get('role'), depth=l.get('depth'), block=l.get('block') if isinstance(l.get('block'), str) else None, poly_px=[[float(x), float(y)] for x, y in l['poly_px']][:64])
                           for l in (it.get('labels') or []) if isinstance(l, dict) and isinstance(l.get('poly_px'), list) and len(l['poly_px']) >= 3][:64])
        fa = assoc.setdefault('faces', {}).setdefault(f['id'], {'status': 'assisted', 'accepted': [], 'rejected': []})
        fa['accepted'] = [acc] + [x for x in fa.get('accepted') or [] if x.get('image_id') != acc['image_id']]; n += 1
    return n


def build_spec(g, evidence, assoc, analysis, fallback, analyzer, assoc_err=None):
    """the autospec from V's per-face sections: each one is normalised and validated alone; a rejected, low-confidence or missing face
    becomes procedural (fallback allowed) or unresolved, with the reason"""
    import facade_autospec as A
    faces, rej = {}, []
    an = (analysis or {}).get('faces') or {}
    for fg in g['faces']:
        fid = fg['id']; fb = 'procedural' if fallback == 'procedural' else 'unresolved'
        if fg['edge'] in ('shared', 'party'): faces[fid] = A.empty_face('unresolved', ['shared/party wall: the neighbour owns it']); continue
        f = an.get(fid); acc = (((assoc or {}).get('faces') or {}).get(fid) or {}).get('accepted') or []
        if not isinstance(f, dict):
            why = assoc_err or ('no accepted image for this face' if not acc else 'analysis returned nothing for this face')
            faces[fid] = A.empty_face(fb, [why]); continue
        f = dict(f); f.setdefault('status', 'observed')
        if f['status'] != 'observed':          # analysis declined/failed: keep ITS reasons (an unresolved section still lists the images it read)
            faces[fid] = A.empty_face(fb, [str(u)[:200] for u in (f.get('unresolved') or [])][:A.MAX_UNRESOLVED] or ['analysis could not resolve this face']); continue
        for k, v in A.empty_face('observed').items(): f.setdefault(k, v)
        if not f['evidence']: f['evidence'] = [{k: e.get(k) for k in A.EVIDENCE_ITEM} for e in acc[:A.MAX_EVIDENCE]]
        errs = A.validate_face(f, fg, f'$.faces.{fid}')
        known = {(e.get('provider'), e.get('image_id')) for e in acc}           # evidence must cite an image associated with THIS face
        errs += [f"$.faces.{fid}.evidence: {e['provider']}:{e['image_id']} is not an accepted image of this face"
                 for e in f['evidence'] if isinstance(e, dict) and (e.get('provider'), e.get('image_id')) not in known] if not errs else []
        if errs: rej.append(errs[0]); faces[fid] = A.empty_face(fb, ['analysis output rejected: ' + errs[0][:200]]); continue
        if f['status'] == 'observed' and f['confidence'] < A.MIN_FACE_CONF:
            faces[fid] = A.empty_face(fb, [f"analysis confidence {f['confidence']} < {A.MIN_FACE_CONF}"] + list(f['unresolved'])[:8]); continue
        faces[fid] = f
    spec = dict(schema=A.SCHEMA, bid=g['bid'], members=list(g.get('members') or [g['bid']]), geometry_key=g['geometry_sha'],
                created=time.strftime('%Y-%m-%dT%H:%M:%S'), analyzer=analyzer, mode=A.mode_of([f['status'] for f in faces.values()]),
                cost=_cost(evidence.get('cost'), (analysis or {}).get('cost')), faces=faces)
    return spec, rej


def cmd_auto(cache, a):
    import facade_autospec as A, facade_compile as C
    bid = a[3]; bd = bdir(cache, bid); os.makedirs(bd, exist_ok=True); T = {}
    fallback = arg(a, '--fallback', _env('MORIA_FACADE_FALLBACK', 'procedural'))
    fstyle = arg(a, '--fallback-style', _env('MORIA_FACADE_FALLBACK_STYLE', 'summerlin-stucco-retail'))
    seed = arg(a, '--seed', 1, int); cfg = auto_cfg(a); members = [bid] + [a[i + 1] for i, x in enumerate(a) if x == '--member']
    prog = os.path.join(bd, 'auto-progress.json')
    base = dict(bid=bid, mode=None, selection=dict(kind='auto', id=None), fallback=fallback, fallback_style=fstyle if fallback == 'procedural' else None,
                pipeline=PIPELINE)
    def stage(name, detail=''):
        try: F.write_json(prog, dict(stage=name, detail=redact(detail)[:300], t=round(time.time(), 3)))
        except OSError as ex: log('progress write skipped', type(ex).__name__)      # informational only: never fails the job
        log('auto stage', name, redact(detail)[:200])
    def finish(status, reasons=(), **kw):
        T['total'] = round(time.time() - t00, 3)
        out = dict(base, status=status, reasons=[redact(r) for r in list(reasons)[:16]], timings_s=T, **kw)
        man = os.path.join(bd, 'manifest.json')
        m = json.load(open(man)) if os.path.exists(man) else {}
        if m.get('pipeline') != PIPELINE: m = {}
        m.setdefault('selections', {})['auto'] = dict(out, prepared=time.strftime('%Y-%m-%dT%H:%M:%S'))
        m.update(pipeline=PIPELINE, bid=bid, latest='auto', timings_s=T)
        F.write_json(man, m, pretty=True)
        stage('ready' if status == F.READY else 'failed' if status not in (F.UNRESOLVED, F.NOTHING) else status, (list(reasons) or [''])[0])
        emit(out); sys.exit(0)
    if fallback not in ('procedural', 'none'): finish(F.INVALID_PACKAGE, [f'--fallback must be procedural|none (got {fallback!r})'])
    try:
        # ---- resolve
        stage('resolving', 'building geometry'); t = time.time()
        try: g, gsrc = get_geometry(cache, bid, members, None, arg(a, '--geometry'))
        except Exception as ex:
            log('geometry FAILED', traceback.format_exc())
            finish(F.NO_GEOMETRY, [f'geometry failed: {type(ex).__name__}: {ex}'[:300], 'NOT empty: nothing planned; retried next time'])
        T['geometry_s'] = round(time.time() - t, 3)
        gerr = S.validate_geometry(g)
        if gerr: finish(F.NO_GEOMETRY, ['geometry does not follow facade-geometry-1'], errors=gerr[:50])
        if not g['faces']: finish(F.NO_GEOMETRY, ['geometry has no faces'] + list(g.get('notes') or [])[:5])
        if protected(g): finish(F.PROTECTED, ['building envelope touches the protected Strip exclusion (+500 blocks); nothing planned'])
        # ---- fetch evidence (an outage marks the provider unavailable; it never aborts the job)
        cfg['cache'] = cache; cfg['members'] = sorted(set(members) | set(g.get('members') or [])); provs_req = providers_of(cfg)
        stage('fetching evidence', ','.join(provs_req)); t = time.time(); rej = []
        if any(p in provs_req for p in ('mapillary', 'panoramax')):          # one Geo per job, shared by fetch and the occluder lookup
            try:
                from geo import Geo
                cfg['geo'] = Geo()
            except Exception as ex: log('geo FAILED', ex)                       # fetch then reports those providers unavailable
        try:
            if os.environ.get('FACADE_PREP_FAIL') == 'sources': raise RuntimeError('injected provider outage (sources)')
            if os.environ.get('FACADE_PREP_FAIL') == 'slow': time.sleep(120)                 # test hook: a long fetch (cancel / timeout tests)
            R = _module('facade_sources')
            ev = R.fetch(bid, g, cfg) if R else stub_evidence(bid, provs_req, 'facade_sources not installed')
        except Exception as ex:
            log('fetch FAILED', redact(traceback.format_exc())); ev = stub_evidence(bid, provs_req, redact(f'{type(ex).__name__}: {ex}')[:200])
        F.write_json(os.path.join(bd, 'evidence.json'), ev)
        T['retrieval_s'] = round(time.time() - t, 3); esha = evidence_sha(ev)
        provs = {p: redact((v or {}).get('status', '?'))[:120] for p, v in sorted((ev.get('providers') or {}).items())}
        # ---- associate (an index/projection failure means no association: those faces fall back, with the reason)
        stage('associating', f"{len(ev.get('candidates') or [])} image candidate(s)"); t = time.time(); assoc, assoc_err = {}, None
        if ev.get('candidates'):
            try:
                RA = _module('facade_associate')
                if not RA: raise RuntimeError('facade_associate not installed')
                assoc = RA.associate(g, ev['candidates'], obstacles(RA, g, cfg), {k: v for k, v in cfg.items() if k != 'geo'})   # its params are written out
            except Exception as ex:
                log('associate FAILED', redact(traceback.format_exc())); assoc = {}
                assoc_err = redact(f'association failed: {type(ex).__name__}: {ex}')[:200]; rej.append(assoc_err)
            F.write_json(os.path.join(bd, 'association.json'), assoc)
        T['association_s'] = round(time.time() - t, 3)
        assisted = merge_assist(assoc, g, ev, cfg)
        if assisted: F.write_json(os.path.join(bd, 'association.json'), assoc)
        n_acc = sum(len((v or {}).get('accepted') or []) for v in (assoc.get('faces') or {}).values()) if isinstance(assoc, dict) else 0
        # ---- analyze (cached by evidence sha + analyzer id/version; versioned, never overwritten)
        t = time.time()
        V = _module('facade_analyze') if n_acc else None
        analyzer = V.analyzer_id(V._job_cfg(cfg)) if V else dict(backend='none', version='0', model=None)
        akey = F.sha_obj(dict(evidence=esha, association=F.sha_obj(assoc), geometry=g['geometry_sha'], analyzer=analyzer, fallback=fallback, schema=A.SCHEMA, p='auto-1'))
        idxp = os.path.join(bd, 'autospec-index.json'); idx = json.load(open(idxp)) if os.path.exists(idxp) else {}
        spec, cached = None, False
        if not cfg['refresh'] and akey in idx and os.path.exists(os.path.join(bd, f'autospec-{idx[akey]}.json')):
            spec = json.load(open(os.path.join(bd, f'autospec-{idx[akey]}.json'), encoding='utf-8')); n = idx[akey]
            if A.validate(spec, g): spec = None                               # a tampered/stale file is never compiled: analyse again
            else: cached = True; stage('analyzing', f'cached autospec-{n}')
        if spec is None:
            stage('analyzing', f'{n_acc} accepted image(s), backend {analyzer["backend"]}')
            analysis = None
            if V:                                                              # downloads url candidates in this job (signed URLs expire)
                try: analysis = V.analyze_building(g, ev, assoc, cfg)
                except Exception as ex: log('analyze FAILED', redact(traceback.format_exc())); rej.append(redact(f'analysis failed: {type(ex).__name__}: {ex}')[:200])
            elif n_acc: rej.append('facade_analyze not installed: accepted images were not analysed')
            spec, r2 = build_spec(g, ev, assoc, analysis, fallback, analyzer, assoc_err); rej += r2
            err = A.validate(spec, g)
            if err: finish('provider_failure', ['autospec failed validation (nothing planned)'] + err[:10])
            n = max([int(v) for v in idx.values()] + [0]) + 1
            F.write_json(os.path.join(bd, f'autospec-{n}.json'), spec, pretty=True); idx[akey] = n; F.write_json(idxp, idx)
        T['analysis_s'] = round(time.time() - t, 3)
        base.update(mode='inferred' if spec['analyzer']['backend'] in A.MODEL_BACKENDS and spec['mode'] != 'procedural' else spec['mode'])
        if any(f.get('assisted') for f in spec['faces'].values()): base.update(mode='assisted')     # never reported as automatic
        # ---- materials (V: colour matching per observed face; a face it cannot match keeps the fallback style palette)
        n_obs = sum(1 for f in spec['faces'].values() if f['status'] == 'observed')
        stage('materials', f'{n_obs} observed face(s)'); t = time.time(); pals = {}
        if n_obs:
            try:
                M = _module('facade_materials')
                if M: pals = M.palettes(spec, g, cfg)
                else: rej.append('facade_materials not installed: observed faces use the fallback style palette')
            except Exception as ex: log('materials FAILED', redact(traceback.format_exc())); rej.append(f'materials failed: {type(ex).__name__} (style palette used)'); pals = {}
        T['materials_s'] = round(time.time() - t, 3)
        # ---- compile (the accepted spec is the compile input: same spec -> same plan key -> identical ops)
        stage('compiling', f'autospec-{n}'); t = time.time()
        try:
            if os.environ.get('FACADE_PREP_FAIL') == 'compile': raise RuntimeError('injected test failure (compile)')
            sel, assets, notes = A.autospec_to_package(spec, g, pals, fstyle if fallback == 'procedural' else None)
            key = C.plan_key(sel, g['geometry_sha'], seed); pp = os.path.join(bd, f'plan-{key}.json'); plan = None
            if os.path.exists(pp):
                try: plan = json.load(open(pp)); T['plan_cached'] = True
                except Exception: plan = None
            if plan is None:
                plan = A.label(C.compile(g, sel, assets, seed), spec, sel, notes, fstyle if fallback == 'procedural' else None); F.write_json(pp, plan)
        except Exception as ex:
            log('compile FAILED', traceback.format_exc())
            finish('provider_failure', [f'compile failed: {type(ex).__name__}: {ex}'[:300], 'nothing placed'])
        T['compile_s'] = round(time.time() - t, 3)
        base['selection'] = {k: sel[k] for k in ('kind', 'id', 'version', 'sha256', 'inputs_sha')}
        pev = {f['id']: f['evidence'] for f in plan['faces']}
        faces = {fid: dict(status=f['status'], plan_evidence=pev.get(fid, 'unresolved'), images=len(f['evidence']), confidence=f['confidence'],
                           ops_by_evidence=(plan.get('auto') or {}).get('faces', {}).get(fid, {}).get('ops_by_evidence', {}),
                           **({'features': A._states(f)} if f['status'] == 'observed' else {}),
                           **({'why': f['unresolved'][0][:120]} if f['unresolved'] and f['status'] != 'observed' else {}))
                 for fid, f in sorted(spec['faces'].items())}
        finish(plan['status'], plan['reasons'] + rej, plan=os.path.relpath(pp, cache).replace('\\', '/'), plan_key=key, geometry_sha=g['geometry_sha'],
               geometry_source=gsrc, seed=seed, ops=len(plan['ops']), components=len(plan['components']), chunks=len(plan['chunks']),
               unresolved=len(plan['unresolved']), envelope_y=plan['envelope']['y'], limits=plan['limits'],
               evidence={f['id']: f['evidence'] for f in plan['faces']}, ops_by_evidence=plan['stats']['ops_by_evidence'],
               autospec=os.path.relpath(os.path.join(bd, f'autospec-{n}.json'), cache).replace('\\', '/'), autospec_cached=cached,
               evidence_sha=esha, analyzer=spec['analyzer'], providers=provs, faces=faces, cost=spec['cost'])
    except SystemExit:
        if cfg.get('geo') is not None: cfg['geo'].close(); cfg['geo'] = None
        raise
    except Exception as ex:
        log('auto FAILED', redact(traceback.format_exc()))
        finish('provider_failure', [redact(f'auto failed: {type(ex).__name__}: {ex}')[:300], 'nothing planned'])


# ---------------------------------------------------------------- identify / adopt
def cmd_identify(cache, a):
    import facade_identify as FI
    x, z = int(a[3]), int(a[4]); pdir = arg(a, '--packages-dir', S.F.packages_dir())
    memo = os.path.join(cache, 'facade', 'index', f'identify-{x}_{z}.json'); psha = packages_sha(pdir) + '|identify-2'   # bump when identify logic changes
    r = json.load(open(memo)) if os.path.exists(memo) else None
    if r and r.get('packages_sha') == psha: r['cached'] = True
    else:
        r = part_hit(x, z, pdir) or FI.identify(x, z, packages_dir=pdir)
        if r.get('package_id') and not valid_package(r['package_id'], pdir):     # review M4: an invalid package never claims ids
            r = dict(r, invalid_package=r['package_id'], package_id=None, bid=r.get('hit'), members=[r['hit']] if r.get('hit') else [])
        r['packages_sha'] = psha; F.write_json(memo, r)
    r = dict(r, styles=S.list_styles(), status='ready' if r.get('bid') else 'no_geometry')
    emit(r)


def part_hit(x, z, pdir, tol=0.75):
    """a valid package's measured part polygon at (x,z) wins over OSM: the reviewed parts can sit far from a misplaced OSM outline
    (Red Rock's tower is ~21 m off in OSM)"""
    from shapely.geometry import Point, Polygon
    pt = Point(x + 0.5, z + 0.5)
    for pid in S.list_packages(pdir):
        if not valid_package(pid, pdir): continue
        doc = json.load(open(os.path.join(pdir, pid, 'package.json'), encoding='utf-8'))
        osm = doc['building']['osm']
        for p in doc['building'].get('parts', []):
            if isinstance(p, dict) and p.get('polygon_mc') and Polygon(p['polygon_mc']).distance(pt) <= tol:
                bid = p.get('osm') if p.get('osm') in osm else osm[0]
                return dict(bid=bid, members=list(osm), package_id=pid, hit=bid, part=p.get('id'), via='package part')
    return None


def packages_sha(pdir):
    """sha of the package listing (relative path -> file sha) so that editing, adding or removing a package refreshes identify memos"""
    out = {}
    for d in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
        for ext in ('json', 'yaml', 'yml'):
            q = os.path.join(pdir, d, 'package.' + ext)
            if os.path.exists(q): out[f'{d}/package.{ext}'] = F.sha_file(q)
    return F.sha_obj(out)


def valid_package(pid, pdir):
    q = S.find_package(pid, pdir)
    return bool(q) and not S.validate_package(q)[1]


def shells_txt(): return os.environ.get('MORIA_SHELLS_TXT', r'C:\BTE-Migration\06-summerlin-pilot-server\plugins\SummerlinShellTool\shells.txt')
def jobs_dir(): return os.environ.get('MORIA_DETAIL_JOBS', r'C:\BTE-Migration\summerlin\detailing\jobs')


def adopt_candidates(bid, members, envelope):
    """exact expected blocks only (never colour-inferred). (a) pilot shell B cells of the member features inside the envelope, expected
    block = the P palette line; (b) accepted detail jobs whose job.json feature is a member: owned.json cells with their after-state
    from after.json ({"[x, y, z]": block}) or plan.jsonl rows ("new"). A job without an exact after-state source adopts nothing and is
    listed under missing (its cells stay ambiguous). Later sources override earlier ones cell by cell."""
    from shapely.geometry import Polygon
    from shapely import contains_xy
    cells, sources, missing = {}, [], []
    env = Polygon(envelope['polygon_mc']) if envelope else None; ylo, yhi = envelope['y'] if envelope else (None, None)
    st = shells_txt()
    if os.path.exists(st):
        pal, fid, n = [], [], 0; cand = []
        with open(st, encoding='utf-8') as f:
            for l in f:
                if l.startswith('P '): pal.append(l.rstrip('\n').split(' ', 2)[2])
                elif l.startswith('F '): fid.append(l.split(' ', 2)[2].split('\t')[0])
                elif l.startswith('B '):
                    t = l.split(); x, y, z = int(t[1]), int(t[2]), int(t[3])
                    if fid[int(t[5])] in members: cand.append((x, y, z, pal[int(t[4])]))
        if cand and env is not None:
            ins = contains_xy(env, [c[0] + 0.5 for c in cand], [c[2] + 0.5 for c in cand])
            cand = [c for c, k in zip(cand, ins) if k and ylo <= c[1] <= yhi]
        elif env is None: missing.append('no cached geometry envelope: pilot shell cells not filtered (run plan first)'); cand = []
        for x, y, z, b in cand: cells[(x, y, z)] = b
        sources.append(dict(kind='pilot-shell', path=st, sha256=F.sha_file(st), cells=len(cand)))
    else: missing.append(f'shells.txt not found: {st}')
    jd = jobs_dir()
    for job in sorted(os.listdir(jd)) if os.path.isdir(jd) else []:
        jp = os.path.join(jd, job, 'job.json')
        if not os.path.exists(jp): continue
        try: feat = json.load(open(jp, encoding='utf-8')).get('feature')
        except Exception: continue
        if feat not in members: continue
        op = os.path.join(jd, job, 'owned.json')
        owned = [tuple(c) for c in json.load(open(op, encoding='utf-8'))] if os.path.exists(op) else []
        after, src = {}, None
        ap, pl = os.path.join(jd, job, 'after.json'), os.path.join(jd, job, 'plan.jsonl')
        if os.path.exists(ap):
            a = json.load(open(ap, encoding='utf-8'))         # {"[x, y, z]": block} or detail-after-1 {"cells": [[x, y, z, block]], provenance...}
            after = {tuple(c[:3]): c[3] for c in a['cells']} if 'cells' in a else {tuple(json.loads(k)): v for k, v in a.items()}; src = ap
        elif os.path.exists(pl) and os.path.getsize(pl):
            after = {(r['x'], r['y'], r['z']): r['new'] for r in map(json.loads, open(pl, encoding='utf-8'))}; src = pl
        got = [c for c in owned if c in after]
        for c in owned:                  # a job superseded the pilot shell there: never expect the shell block on a job-owned cell
            if c not in after: cells.pop(c, None)
        if not src or len(got) < len(owned):
            missing.append(f'job {job}: {len(owned) - len(got)} of {len(owned)} owned cells have no exact after-state '
                           f'(after.json missing; plan.jsonl {"empty" if os.path.exists(pl) and not os.path.getsize(pl) else "incomplete"}); those cells stay ambiguous')
        for c in got: cells[c] = after[c]
        if src: sources.append(dict(kind='detail-job', job=job, path=src, sha256=F.sha_file(src), cells=len(got)))
    out = dict(version='facade-adopt-1', bid=bid, members=members, cells=[[x, y, z, b] for (x, y, z), b in sorted(cells.items())],
               source='; '.join(f"{s['kind']}:{s['path']}" for s in sources), source_sha=F.sha_obj(sources), sources=sources, missing=missing)
    return out


def cmd_adopt(cache, a):
    bid = a[3]; bd = bdir(cache, bid); gp = os.path.join(bd, 'geometry.json')
    g = json.load(open(gp)) if os.path.exists(gp) else None
    members = (g or {}).get('members') or [bid] + [a[i + 1] for i, x in enumerate(a) if x == '--member']
    out = adopt_candidates(bid, members, (g or {}).get('envelope'))
    F.write_json(os.path.join(bd, 'adopt-candidates.json'), out)
    emit(dict(status='ready' if out['cells'] else F.NOTHING, bid=bid, cells=len(out['cells']), sources=out['sources'], missing=out['missing'],
              file=f'facade/buildings/{bid}/adopt-candidates.json'))


# ---------------------------------------------------------------- validate / inspect
def cmd_validate(path):
    obj, errs = S.validate(path)
    print(json.dumps(dict(status='valid' if not errs else F.INVALID_PACKAGE, path=path, schema=(obj or {}).get('schema') if isinstance(obj, dict) else None,
                          errors=errs), indent=1))
    sys.exit(1 if errs else 0)


ROLE_RGB = {'wall': (205, 190, 150), 'glass': (70, 110, 140), 'mullion': (30, 30, 35), 'frame': (110, 80, 50), 'backing': (90, 90, 90),
            'sill': (230, 230, 220), 'band': (170, 150, 110), 'parapet': (190, 175, 135), 'canopy': (240, 240, 240), 'column': (150, 130, 100),
            'door': (120, 60, 30), 'roof': (250, 250, 250), 'roof-edge': (140, 140, 140), 'sign': (200, 40, 40), 'trim': (160, 140, 120)}


def inspect(plan, g, out_dir, px=8):
    """text summary + one PNG per face: columns along u (left->right seen from outside), rows up; the outermost op per (u, v) is drawn,
    an air op is drawn as a hatched cell; unresolved faces are drawn grey with a red frame"""
    from PIL import Image, ImageDraw
    lines = [f"{plan['building']} {plan['selection']['kind']}:{plan['selection']['id']} status={plan['status']} ops={len(plan['ops'])} "
             f"components={len(plan['components'])} chunks={len(plan['chunks'])} key={plan.get('plan_key')}"]
    byface = {}
    for c in plan['components']: byface.setdefault(c['face'], {}).setdefault(c['kind'], 0); byface[c['face']][c['kind']] += 1
    for f in plan['faces']:
        lines.append(f"  {f['id']:<12} {f['edge']:<8} {f['evidence']:<10} ops={f['ops']:<6} comps={byface.get(f['id'], {})} {f['notes']}")
    for u in plan['unresolved']: lines.append(f"  UNRESOLVED {u['face']}: {u['reason']}")
    lines += ['  ' + r for r in plan['reasons'][:20]]
    pngs = []
    if g:
        os.makedirs(out_dir, exist_ok=True)
        cell = {}
        for o in plan['ops']: cell[(o[0], o[1], o[2])] = o
        for fg in g['faces']:
            N, base, H = len(fg['columns']), int(fg['base_y']), int(fg['top_y']) - int(fg['base_y']) + 2
            if not N: continue
            im = Image.new('RGB', (N * px, H * px), (255, 255, 255)); d = ImageDraw.Draw(im)
            for i, (x, z, _, _, ox, oz) in enumerate(fg['columns']):
                for v in range(H):
                    for w in (4, 3, 2, 1, 0, -1, -2):
                        o = cell.get((int(x) + w * int(ox), base + v, int(z) + w * int(oz)))
                        if not o: continue
                        x0, y0 = i * px, (H - 1 - v) * px
                        if 'air' in o[4].split('[')[0]: d.line([x0, y0, x0 + px - 1, y0 + px - 1], fill=(120, 120, 120)); break
                        rgb = ROLE_RGB.get(o[6], (255, 0, 255)); k = 1 - 0.08 * max(-w, 0) + 0.06 * max(w, 0)
                        d.rectangle([x0, y0, x0 + px - 1, y0 + px - 1], fill=tuple(min(255, int(c * k)) for c in rgb)); break
            ev = next((f['evidence'] for f in plan['faces'] if f['id'] == fg['id']), '?')
            if ev == 'unresolved': d.rectangle([0, 0, N * px - 1, H * px - 1], outline=(220, 0, 0))
            p = os.path.join(out_dir, f"{plan['building']}-{plan.get('plan_key', 'plan')}-{fg['id'].replace(':', '_')}.png"); im.save(p); pngs.append(p)
    return '\n'.join(lines), pngs


def cmd_inspect(a):
    pp = a[2]; plan = json.load(open(pp)); gp = arg(a, '--geometry') or os.path.join(os.path.dirname(pp), 'geometry.json')
    g = json.load(open(gp)) if os.path.exists(gp) else None
    txt, pngs = inspect(plan, g, arg(a, '--out', os.path.join(os.path.dirname(os.path.abspath(pp)), 'inspect')))
    print(txt); print('elevations:', *pngs, sep='\n  ') if pngs else print('(no geometry.json beside the plan: pass --geometry to render elevations)')


if __name__ == '__main__':
    a = sys.argv
    if len(a) < 3: print(__doc__); sys.exit(2)
    if a[1] == 'validate': cmd_validate(a[2])
    elif a[1] == 'inspect': cmd_inspect(a)
    elif a[2] == 'plan': cmd_plan(a[1], a)
    elif a[2] == 'identify': cmd_identify(a[1], a)
    elif a[2] == 'adopt': cmd_adopt(a[1], a)
    elif a[2] == 'auto': cmd_auto(a[1], a)
    else: print(__doc__); sys.exit(2)
