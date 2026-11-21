"""facade_sources.py - Facade v0.6 auto: evidence providers (docs/facade/AUTO.md section 4, docs/facade/SOURCES.md). Owner: R.
    fetch(bid, faces_world, cfg) -> evidence dict {schema, bid, key, providers{name: {status, ...}}, osm, candidates[], cost}
    download(cand, cfg) -> (local_path, None) | (None, reason)
    localize(cands, geo) -> adds mc [x, z], heading_mc [dx, dz] and blocks_per_m to candidates given in lon/lat
CLI:  facade_sources.py <cache> <bid> [--refresh]      reads buildings/<bid>/geometry.json, writes evidence.json, prints ONE JSON line
      facade_sources.py --selftest                     (mock server only, no real network)
Providers: local, mapillary, panoramax, osm. A provider failure (no credential, outage, timeout, HTTP error, malformed reply,
request budget) gives that provider status 'unavailable: <reason>' and never raises out of fetch(). The Mapillary token is
sent only in the Authorization header; it is never logged, cached or written to evidence (URLs are logged without query)."""
import datetime, hashlib, json, math, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SCHEMA = 'facade-evidence-1'
DEFAULTS = dict(providers='local,mapillary,panoramax,osm', timeout_s=20.0, max_requests=40, ttl_days=30.0, pad_m=80.0,
                mapillary_base='https://graph.mapillary.com', panoramax_base='https://api.panoramax.xyz/api',
                mapillary_max_bbox_deg2=0.01, refs_dir=None, secrets_file=None, cache=None, refresh=False, geo=None,
                members=None, osm_tags=None)
MLY_FIELDS = ('id,computed_geometry,geometry,computed_compass_angle,compass_angle,computed_rotation,captured_at,camera_type,camera_parameters,'
              'is_pano,thumb_2048_url,creator,width,height')
OSM_KEYS = ('building', 'building:colour', 'building:material', 'building:levels', 'height', 'min_height', 'name')


def log(*m): print('[facade-sources]', *m, file=sys.stderr, flush=True)
def _redact(url): return url.split('?', 1)[0]                     # never log a query string (tokens, signatures)
def _sha(s): return hashlib.sha256(s.encode()).hexdigest()


class Unavailable(Exception): pass


class Job:
    """one fetch run: shared request budget, per-provider deadline, response cache under <cache>/facade/evidence"""
    def __init__(self, cfg):
        self.cfg = cfg; self.requests = 0; self.by_provider = {}; self.hits = {}; self.deadline = {}

    def start(self, prov): self.deadline[prov] = time.time() + float(self.cfg['timeout_s']); self.by_provider.setdefault(prov, 0); self.hits.setdefault(prov, 0)

    def _cpath(self, prov, key, ext):
        c = self.cfg.get('cache')
        return os.path.join(c, 'facade', 'evidence', prov, key + ext) if c else None

    def _fresh(self, p): return p and os.path.exists(p) and not self.cfg['refresh'] and time.time() - os.path.getmtime(p) < float(self.cfg['ttl_days']) * 86400

    def get(self, prov, url, params=None, headers=None, binary=False, cache_key=None):
        """GET with cache, budget and deadline; returns parsed JSON (or bytes); raises Unavailable only"""
        key = cache_key or _sha(url + json.dumps(params or {}, sort_keys=True))
        p = self._cpath(prov, key, '.bin' if binary else '.json')
        if self._fresh(p):
            self.hits[prov] = self.hits.get(prov, 0) + 1
            with open(p, 'rb') as f: b = f.read()
            return b if binary else json.loads(b)
        if self.requests >= int(self.cfg['max_requests']): raise Unavailable('request budget exhausted')
        left = self.deadline.get(prov, time.time() + float(self.cfg['timeout_s'])) - time.time()
        if left <= 0: raise Unavailable('timeout')
        import requests
        self.requests += 1; self.by_provider[prov] = self.by_provider.get(prov, 0) + 1
        log(prov, 'GET', _redact(url))
        try: r = requests.get(url, params=params, headers=headers or {}, timeout=min(float(self.cfg['timeout_s']), left))
        except requests.Timeout: raise Unavailable('timeout')
        except requests.RequestException as e: raise Unavailable('network error: ' + type(e).__name__)
        if r.status_code != 200: raise Unavailable(f'HTTP {r.status_code}')
        b = r.content
        if not binary:
            try: out = json.loads(b)
            except ValueError: raise Unavailable('malformed response: not JSON')
        if p:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p + '.tmp', 'wb') as f: f.write(b)
            os.replace(p + '.tmp', p)
        return b if binary else out


# ---------------------------------------------------------------- helpers
def _iso(v):
    """ms epoch or ISO string -> 'YYYY-MM-DDTHH:MM:SSZ' (UTC), None when unknown"""
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool): d = datetime.datetime.fromtimestamp(v / 1000, datetime.timezone.utc)
        elif isinstance(v, str) and v: d = datetime.datetime.fromisoformat(v.replace('Z', '+00:00'))
        else: return None
        if d.tzinfo is None: d = d.replace(tzinfo=datetime.timezone.utc)
        return d.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, OverflowError, OSError): return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _focal_from_hfov(hfov, w, h):
    """OpenSfM-normalised focal (pixels / max(w, h)) from a horizontal FOV"""
    if not (hfov and w and h) or not 0 < hfov < 180: return None
    return (w / max(w, h)) / (2 * math.tan(math.radians(hfov) / 2))


def orientation(rv):
    """Mapillary computed_rotation (OpenSfM angle-axis, world ENU -> camera x right / y down / z forward) -> (heading_deg compass,
    pitch_deg + up, roll_deg + = right side down). None when missing/degenerate."""
    try:
        v = [float(t) for t in rv]; th = math.sqrt(sum(t * t for t in v))
        if len(v) != 3 or not th < 10: return None
    except (TypeError, ValueError): return None
    if th < 1e-12: R = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    else:
        k = [t / th for t in v]; c, s = math.cos(th), math.sin(th); K = [[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]]
        R = [[(1 if i == j else 0) + s * K[i][j] + (1 - c) * sum(K[i][m] * K[m][j] for m in range(3)) for j in range(3)] for i in range(3)]
    right, down, fwd = R[0], R[1], R[2]                       # camera axes in world (rows of world->camera)
    return (math.degrees(math.atan2(fwd[0], fwd[1])) % 360, math.degrees(math.asin(max(-1, min(1, fwd[2])))),
            math.degrees(math.atan2(-right[2], -down[2])))


def _camera(ctype, focal=None, hfov=None, w=None, h=None):
    t = {'perspective': 'perspective', 'equirectangular': 'equirectangular', 'spherical': 'equirectangular'}.get(ctype, ctype or 'unknown')
    if t == 'perspective' and focal is None: focal = _focal_from_hfov(hfov, w, h)
    if t == 'perspective' and focal and w and h: hfov = math.degrees(2 * math.atan((w / max(w, h)) / (2 * focal)))
    return dict(type=t, focal=focal, hfov_deg=round(hfov, 3) if hfov else (360.0 if t == 'equirectangular' else None), width=w, height=h)


def bbox_lonlat(faces, geo, pad_m):
    pts = [tuple(s[k]) for f in faces for s in f['segments'] for k in ('a', 'b')]
    ll = geo.inv(pts)
    w, s = min(p[0] for p in ll), min(p[1] for p in ll); e, n = max(p[0] for p in ll), max(p[1] for p in ll)
    dlat = pad_m / 111320.0; dlon = pad_m / (111320.0 * math.cos(math.radians((s + n) / 2)))
    return [w - dlon, s - dlat, e + dlon, n + dlat]


def split_bbox(b, max_area):
    """tiles no larger than max_area square degrees (Mapillary bbox limit)"""
    w, s, e, n = b; k = max(1, math.ceil(math.sqrt((e - w) * (n - s) / max_area)))
    dx, dy = (e - w) / k, (n - s) / k
    return [[w + i * dx, s + j * dy, w + (i + 1) * dx, s + (j + 1) * dy] for j in range(k) for i in range(k)]


def mapillary_token(cfg):
    t = os.environ.get('MAPILLARY_ACCESS_TOKEN', '').strip()
    sf = cfg.get('secrets_file')
    if not t and sf and os.path.exists(sf):
        txt = open(sf, encoding='utf-8').read()
        try: t = str(json.loads(txt).get('mapillary_access_token') or json.loads(txt).get('MAPILLARY_ACCESS_TOKEN') or '')
        except (ValueError, AttributeError):
            for line in txt.splitlines():
                k, _, v = line.partition('=')
                if k.strip() in ('MAPILLARY_ACCESS_TOKEN', 'mapillary_access_token'): t = v.strip().strip('"\'')
            if not t and len(txt.split()) == 1 and '=' not in txt: t = txt.strip()         # a file holding just the bare token
    return t.strip() or None


# ---------------------------------------------------------------- providers: (bid, bbox, cfg, job) -> candidates, raise Unavailable
def p_mapillary(bid, bbox, cfg, job):
    tok = mapillary_token(cfg)
    if not tok: raise Unavailable('no credential')
    out, seen, bad = [], set(), 0
    for t in split_bbox(bbox, float(cfg['mapillary_max_bbox_deg2'])):
        params = dict(bbox=','.join('%.7f' % v for v in t), fields=MLY_FIELDS, limit=500)
        j = job.get('mapillary', cfg['mapillary_base'].rstrip('/') + '/images', params, {'Authorization': 'OAuth ' + tok})
        if not isinstance(j, dict) or not isinstance(j.get('data'), list): raise Unavailable('malformed response: no data list')
        for it in j['data']:                          # ponytail: first page only (limit 500 per tile); add paging.next if a tile ever fills
            try:
                iid = str(it['id']); g = it.get('computed_geometry') or it['geometry']; lon, lat = map(float, g['coordinates'][:2])
                head = _num(it.get('computed_compass_angle')); head = head if head is not None else _num(it.get('compass_angle'))
                w, h = it.get('width'), it.get('height'); cp = it.get('camera_parameters') or []
                ctype = 'equirectangular' if it.get('is_pano') and it.get('camera_type') in (None, 'spherical', 'equirectangular') else it.get('camera_type')
                cam = _camera(ctype, focal=_num(cp[0]) if cp else None, w=w, h=h)
                ori = orientation(it.get('computed_rotation'))      # SfM pose: heading, pitch and roll of THIS photo (phones tilt)
                if ori: head = ori[0]; cam.update(pitch_deg=round(ori[1], 3), roll_deg=round(ori[2], 3), pose='computed_rotation')
                else: cam.update(pitch_deg=0.0, roll_deg=0.0, pose='compass only (pitch/roll unknown)')
                url = it.get('thumb_2048_url')
                if not url or head is None: raise KeyError('url/heading')
            except (KeyError, TypeError, ValueError, IndexError): bad += 1; continue
            if iid in seen: continue
            seen.add(iid); who = (it.get('creator') or {}).get('username') or 'unknown'
            out.append(dict(provider='mapillary', image_id=iid, captured_at=_iso(it.get('captured_at')), lon=lon, lat=lat,
                            heading_deg=head % 360, camera=cam, url=url, licence='CC BY-SA 4.0',
                            attribution=f'Mapillary image {iid} by {who}, CC BY-SA 4.0 (mapillary.com)'))
    if bad: log('mapillary: skipped', bad, 'malformed item(s)')
    return out


def p_panoramax(bid, bbox, cfg, job):
    j = job.get('panoramax', cfg['panoramax_base'].rstrip('/') + '/search', dict(bbox=','.join('%.7f' % v for v in bbox), limit=100))
    if not isinstance(j, dict) or not isinstance(j.get('features'), list): raise Unavailable('malformed response: no features list')
    out, bad = [], 0
    for f in j['features']:
        try:
            p = f['properties']; lon, lat = map(float, f['geometry']['coordinates'][:2]); head = _num(p.get('view:azimuth'))
            io = p.get('pers:interior_orientation') or {}; dims = io.get('sensor_array_dimensions') or [None, None]
            fov = _num(io.get('field_of_view')); a = f.get('assets') or {}
            url = ((a.get('sd') or a.get('hd') or {}).get('href'))
            if not url or head is None: raise KeyError('url/heading')
            pano = fov == 360 or (p.get('exif') or {}).get('Xmp.GPano.ProjectionType') == 'equirectangular'
            cam = _camera('equirectangular' if pano else 'perspective', hfov=fov, w=dims[0], h=dims[1])
        except (KeyError, TypeError, ValueError, IndexError): bad += 1; continue
        lic = p.get('license') or 'unknown'; who = ', '.join(x.get('name', '') for x in f.get('providers') or [] if x.get('name')) or 'unknown'
        out.append(dict(provider='panoramax', image_id=str(f.get('id')), captured_at=_iso(p.get('datetime')), lon=lon, lat=lat,
                        heading_deg=head % 360, camera=cam, url=url, licence=lic,
                        attribution=f'Panoramax picture {f.get("id")} by {who}, {lic} (panoramax.xyz)'))
    if bad: log('panoramax: skipped', bad, 'malformed item(s)')
    return out


def p_local(bid, bbox, cfg, job):
    """<refs_dir>/<bid>/refs.json: {"images": [{file, lon+lat | x+z, heading_deg | heading_mc, camera_type, focal | hfov_deg,
    width, height, captured_at, licence, attribution, image_id?}]}"""
    d = cfg.get('refs_dir')
    if not d: raise Unavailable('no refs dir configured')
    p = os.path.join(d, bid, 'refs.json')
    if not os.path.exists(p): raise Unavailable('no refs.json for this building')
    try: refs = json.load(open(p, encoding='utf-8'))['images']
    except (ValueError, KeyError, TypeError): raise Unavailable('malformed refs.json')
    out = []
    for k, r in enumerate(refs):
        try:
            path = os.path.join(d, bid, os.path.basename(r['file']))       # no path escape out of the refs dir
            if not os.path.exists(path): log('local: missing file', os.path.basename(path)); continue
            w, h = r.get('width'), r.get('height')
            if not (w and h):
                from PIL import Image
                with Image.open(path) as im: w, h = im.size
            c = dict(provider='local', image_id=str(r.get('image_id') or os.path.basename(path)), captured_at=_iso(r.get('captured_at')),
                     camera=_camera(r.get('camera_type', 'perspective'), focal=_num(r.get('focal')), hfov=_num(r.get('hfov_deg')), w=w, h=h),
                     path=path, licence=r.get('licence') or 'unknown', attribution=r.get('attribution') or 'local reference')
            if 'x' in r: c.update(mc=[float(r['x']), float(r['z'])], heading_mc=_unit(r['heading_mc']), blocks_per_m=float(r.get('blocks_per_m', 1.0)))
            else: c.update(lon=float(r['lon']), lat=float(r['lat']), heading_deg=float(r['heading_deg']) % 360)
            out.append(c)
        except (KeyError, TypeError, ValueError): log('local: malformed entry', k)
    return out


def p_osm(bid, members, cfg):
    tags = cfg.get('osm_tags')
    if tags is None:
        import facade_geometry as fg
        if not os.path.exists(fg.INDEX): raise Unavailable('OSM index missing')
        tags = {m: f['tags'] for m, f in fg.osm_feats(members).items()}
    return {m: {k: v for k, v in (tags.get(m) or {}).items() if k in OSM_KEYS or k.startswith('roof:')} for m in members}


def _unit(v):
    n = math.hypot(v[0], v[1]); return [v[0] / n, v[1] / n]


def localize(cands, geo):
    """lon/lat + compass heading -> Minecraft mc [x, z], heading_mc unit vector and local blocks_per_m (TPM is ~2% off 1:1 and
    rotated ~11 degrees near Las Vegas, so headings are projected, never used as-is)"""
    from pyproj import Geod
    G = Geod(ellps='WGS84'); todo = [c for c in cands if 'mc' not in c]
    if not todo: return cands
    ahead = [G.fwd(c['lon'], c['lat'], c['heading_deg'], 10.0)[:2] for c in todo]
    pts = geo.fwd([(c['lon'], c['lat']) for c in todo] + ahead)
    for k, c in enumerate(todo):
        (x, z), (x2, z2) = pts[k], pts[len(todo) + k]
        c['mc'] = [round(x, 3), round(z, 3)]; c['heading_mc'] = [round(v, 6) for v in _unit((x2 - x, z2 - z))]
        c['blocks_per_m'] = round(math.hypot(x2 - x, z2 - z) / 10.0, 5)
    return cands


# ---------------------------------------------------------------- public API
def config(cfg=None):
    c = dict(DEFAULTS); c.update({k: v for k, v in (cfg or {}).items() if v is not None})
    return c


def fetch(bid, faces_world, cfg=None):
    """faces_world: geometry.json dict (faces + members) or a list of its faces. Never raises for a provider failure."""
    cfg = config(cfg); job = Job(cfg)
    geomd = faces_world if isinstance(faces_world, dict) else {'faces': faces_world}
    faces, members = geomd['faces'], cfg.get('members') or geomd.get('members') or [bid]
    provs = [p.strip() for p in str(cfg['providers']).split(',') if p.strip()]
    ev = dict(schema=SCHEMA, bid=bid, geometry_sha=geomd.get('geometry_sha'), providers_requested=provs,
              key=_sha(json.dumps([bid, geomd.get('geometry_sha'), sorted(provs)])),
              created=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
              providers={}, osm=None, candidates=[])
    own_geo = cfg['geo'] is None and any(p in provs for p in ('mapillary', 'panoramax', 'local'))
    geo = cfg['geo']
    try:
        bbox = None
        for name in provs:
            job.start(name); st = dict(status='ok', candidates=0)
            try:
                if name == 'osm': ev['osm'] = p_osm(bid, members, cfg)
                elif name in ('mapillary', 'panoramax', 'local'):
                    if name != 'local' and bbox is None:
                        if geo is None:
                            from geo import Geo
                            geo = Geo()
                        bbox = bbox_lonlat(faces, geo, float(cfg['pad_m'])); ev['bbox_lonlat'] = [round(v, 7) for v in bbox]
                    got = {'mapillary': p_mapillary, 'panoramax': p_panoramax, 'local': p_local}[name](bid, bbox, cfg, job)
                    if any('mc' not in c for c in got):
                        if geo is None:
                            from geo import Geo
                            geo = Geo()
                        localize(got, geo)
                    ev['candidates'] += got; st['candidates'] = len(got)
                else: raise Unavailable('unknown provider (KartaView and Google are not supported, see SOURCES.md)')
            except Unavailable as e: st['status'] = f'unavailable: {e}'
            except Exception as e:                                     # adapter bug or odd data: still never fatal for the job
                st['status'] = f'unavailable: internal error {type(e).__name__}'
            st.update(requests=job.by_provider.get(name, 0), cache_hits=job.hits.get(name, 0)); ev['providers'][name] = st
            log(name, st['status'], st['candidates'], 'candidate(s)')
    finally:
        if own_geo and geo is not None: geo.close()
    ev['cost'] = dict(usd=0.0, requests=job.requests, by_provider=dict(job.by_provider))
    return ev


def download(cand, cfg=None):
    """the candidate image as a local file (cached under <cache>/facade/evidence/<provider>/img/); (path, None) or (None, reason)"""
    cfg = config(cfg)
    if cand.get('path'): return (cand['path'], None) if os.path.exists(cand['path']) else (None, 'local file missing')
    c = cfg.get('cache')
    if not c: return None, 'no cache dir'
    p = os.path.join(c, 'facade', 'evidence', cand['provider'], 'img', _sha(cand['image_id'])[:32] + '.jpg')
    if os.path.exists(p) and not cfg['refresh']: return p, None
    job = Job(dict(cfg, cache=None)); job.start(cand['provider'])
    try: b = job.get(cand['provider'], cand['url'], binary=True)
    except Unavailable as e: return None, str(e)
    try:
        import io
        from PIL import Image
        Image.open(io.BytesIO(b)).verify()
    except Exception: return None, 'malformed image'
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'wb') as f: f.write(b)
    os.replace(p + '.tmp', p)
    return p, None


def cfg_from_env():
    return config(dict(refs_dir=os.environ.get('MORIA_FACADE_REFS'), secrets_file=os.environ.get('MORIA_FACADE_SECRETS'),
                       providers=os.environ.get('MORIA_FACADE_PROVIDERS'), max_requests=os.environ.get('MORIA_FACADE_MAX_REQUESTS'),
                       timeout_s=os.environ.get('MORIA_FACADE_PROVIDER_TIMEOUT')))


# ---------------------------------------------------------------- self-test (mock server, fake projection; no real network)
class FakeGeo:
    """linear TPM stand-in near the pilot: rotated 11.4 degrees and scaled 1.02 like the real projection there"""
    LON0, LAT0, S, R = -115.3333, 36.1528, 1.02, math.radians(11.4)
    MX, MY = 111320.0 * math.cos(math.radians(36.1528)), 110950.0
    def fwd(self, ll):
        out = []
        for lon, lat in ll:
            e, n = (lon - self.LON0) * self.MX, (lat - self.LAT0) * self.MY
            out.append((self.S * (e * math.cos(self.R) + n * math.sin(self.R)), self.S * (e * math.sin(self.R) - n * math.cos(self.R))))
        return out
    def inv(self, xz):
        out = []
        for x, z in xz:
            x, z = x / self.S, z / self.S
            e, n = x * math.cos(self.R) + z * math.sin(self.R), x * math.sin(self.R) - z * math.cos(self.R)
            out.append((self.LON0 + e / self.MX, self.LAT0 + n / self.MY))
        return out
    def close(self): pass


def selftest_fixture(td, geo):
    """fixture dir for tools/mock_imagery_server.py: a 20 m face at MC z=0 (x 0..20, normal +z = south-ish), cameras in front of it
    (ok) and in front of a neighbour at x 30..50 (wrong-building); returns (fixture_dir, geometry)"""
    from PIL import Image
    fx = os.path.join(td, 'fixtures'); os.makedirs(fx, exist_ok=True)
    Image.new('RGB', (64, 48), (200, 180, 150)).save(os.path.join(fx, 'a.jpg'))
    def item(iid, x, z, heading_mc):
        (lon, lat), = geo.inv([(x, z)])
        # compass heading of an MC direction: project a point ahead back to lon/lat
        (lon2, lat2), = geo.inv([(x + heading_mc[0], z + heading_mc[1])])
        from pyproj import Geod
        az = Geod(ellps='WGS84').inv(lon, lat, lon2, lat2)[0] % 360
        return dict(id=iid, geometry=dict(type='Point', coordinates=[lon, lat]), computed_compass_angle=az, captured_at=1735689600000,
                    camera_type='perspective', camera_parameters=[0.8, 0, 0], is_pano=False, width=2048, height=1536,
                    thumb_2048_url='{base}/img/a.jpg', creator=dict(username='fixture'))
    pano = dict(id='pmx1', type='Feature', geometry=dict(type='Point', coordinates=list(geo.inv([(10, 20)])[0])),
                properties={'datetime': '2024-05-01T10:00:00Z', 'view:azimuth': 90, 'license': 'CC-BY-SA-4.0',
                            'pers:interior_orientation': {'field_of_view': 360, 'sensor_array_dimensions': [4096, 2048]}},
                assets={'sd': {'href': '{base}/img/a.jpg'}}, providers=[{'name': 'fixture'}])
    json.dump({'ok': [item('m1', 10, 20, (0, -1)), item('m2', 4, 15, (0.3, -1))],
               'wrong-building': [item('w1', 40, 15, (0, -1)), item('w2', 45, 12, (-0.2, -1))],
               'panoramax': [pano]}, open(os.path.join(fx, 'images.json'), 'w'))
    seg = dict(a=[0.0, 0.0], b=[20.0, 0.0], normal=[0.0, 1.0], u0=0.0, u1=20.0, curve=None)
    geom = dict(bid='t', members=['t'], geometry_sha='x', faces=[dict(id='p0:f0', part='p0', edge='exterior', base_y=60, top_y=68,
                length_u=20.0, segments=[seg], label='S')],
                parts=[dict(id='p0', polygon_mc=[[0, 0], [20, 0], [20, -15], [0, -15]], base_y=60, top_y=68)])
    return fx, geom


def _selftest():
    import io, tempfile, contextlib
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
    import mock_imagery_server as M
    geo = FakeGeo(); TOK = 'SELFTEST-SECRET-TOKEN-123'; ok = []
    with tempfile.TemporaryDirectory() as td:
        fx, geom = selftest_fixture(td, geo)
        srv = M.start(fx, mode='ok'); base = srv.url
        err = io.StringIO()
        try:
            def run(**kw):
                c = dict(geo=geo, cache=os.path.join(td, 'cache'), mapillary_base=base, panoramax_base=base + '/api', providers='mapillary', timeout_s=2)
                c.update(kw); return fetch('t', geom, c)
            with contextlib.redirect_stderr(err):
                os.environ.pop('MAPILLARY_ACCESS_TOKEN', None)
                e = run(); assert e['providers']['mapillary']['status'] == 'unavailable: no credential' and e['cost']['requests'] == 0, e['providers']
                ok.append('no token -> unavailable: no credential, 0 requests')
                os.environ['MAPILLARY_ACCESS_TOKEN'] = TOK
                e = run(); m = e['providers']['mapillary']
                assert m['status'] == 'ok' and m['candidates'] == 2 and m['requests'] == 1, m
                c = next(x for x in e['candidates'] if x['image_id'] == 'm1')
                assert abs(c['mc'][0] - 10) < 0.05 and abs(c['mc'][1] - 20) < 0.05, c['mc']
                assert abs(c['heading_mc'][0]) < 0.01 and abs(c['heading_mc'][1] + 1) < 0.001, c['heading_mc']   # compass -> MC rotation undone
                assert abs(c['blocks_per_m'] - 1.02) < 0.01 and c['camera']['type'] == 'perspective' and c['captured_at'] == '2025-01-01T00:00:00Z'
                assert c['url'].startswith(base + '/img/') and 'CC BY-SA' in c['attribution']
                ok.append('mapillary ok: 2 candidates, compass heading -> MC heading (11.4 deg rotation, scale 1.02) round-trips')
                srv.set_mode('outage'); h0 = srv.hits
                e = run(); assert e['providers']['mapillary']['status'] == 'ok' and e['cost']['requests'] == 0 and srv.hits == h0, e['providers']
                assert e['providers']['mapillary']['cache_hits'] == 1
                ok.append('cache hit: same fetch during an outage served from cache with 0 network calls')
                e = run(refresh=True); assert e['providers']['mapillary']['status'] == 'unavailable: HTTP 503', e['providers']
                ok.append('outage -> unavailable: HTTP 503')
                srv.set_mode('timeout'); t = time.time()
                e = run(refresh=True, timeout_s=1); assert e['providers']['mapillary']['status'] == 'unavailable: timeout' and time.time() - t < 3, e['providers']
                ok.append('timeout -> unavailable: timeout (bounded by provider timeout)')
                srv.set_mode('malformed')
                e = run(refresh=True); assert e['providers']['mapillary']['status'].startswith('unavailable: malformed'), e['providers']
                ok.append('malformed -> ' + e['providers']['mapillary']['status'])
                srv.set_mode('ok')
                e = run(refresh=True, max_requests=0); assert e['providers']['mapillary']['status'] == 'unavailable: request budget exhausted'
                e = run(refresh=True, mapillary_max_bbox_deg2=1e-7); n = e['providers']['mapillary']['requests']
                assert n > 1 and e['providers']['mapillary']['candidates'] == 2, e['providers']      # split tiles, de-duplicated
                ok.append(f'budget exhausted -> unavailable; bbox split into {n} requests, de-duplicated')
                e = run(refresh=True, providers='panoramax,kartaview')
                p = e['candidates'][0]; assert e['providers']['panoramax']['status'] == 'ok' and p['camera']['type'] == 'equirectangular' and p['licence'] == 'CC-BY-SA-4.0'
                assert e['providers']['kartaview']['status'].startswith('unavailable: unknown provider')
                ok.append('panoramax STAC parsed (equirectangular, azimuth, licence); kartaview -> unavailable')
                e = run(refresh=True, mapillary_base='http://127.0.0.1:9')       # nothing listens there
                assert e['providers']['mapillary']['status'] in ('unavailable: network error: ConnectionError', 'unavailable: timeout'), e['providers']   # Windows may retry a refused SYN until the timeout
                ok.append('connection refused -> ' + e['providers']['mapillary']['status'])
                os.makedirs(os.path.join(td, 'refs', 't'))
                import shutil; shutil.copy(os.path.join(fx, 'a.jpg'), os.path.join(td, 'refs', 't', 'r1.jpg'))
                json.dump({'images': [{'file': 'r1.jpg', 'x': 10, 'z': 18, 'heading_mc': [0, -2], 'hfov_deg': 70, 'captured_at': '2024-03-01',
                                       'licence': 'own photo', 'attribution': 'test'}, {'file': '../../escape.jpg', 'x': 0, 'z': 0, 'heading_mc': [1, 0]}]},
                          open(os.path.join(td, 'refs', 't', 'refs.json'), 'w'))
                e = run(providers='local,osm', refs_dir=os.path.join(td, 'refs'), osm_tags={'t': {'building': 'retail', 'building:colour': 'beige', 'shop': 'x'}})
                assert len(e['candidates']) == 1 and e['candidates'][0]['camera']['width'] == 64 and e['candidates'][0]['heading_mc'] == [0.0, -1.0]
                assert e['osm'] == {'t': {'building': 'retail', 'building:colour': 'beige'}}
                ok.append('local refs.json (path escape refused, size from file) + osm attributes')
                e = run(refresh=True); c = e['candidates'][0]
                p1, why = download(c, dict(cache=os.path.join(td, 'cache'))); assert p1 and os.path.exists(p1), why
                h0 = srv.hits; p2, _ = download(c, dict(cache=os.path.join(td, 'cache'))); assert p2 == p1 and srv.hits == h0
                srv.set_mode('outage'); _, why = download(c, dict(cache=os.path.join(td, 'cache'), refresh=True)); assert why == 'HTTP 503', why
                ok.append('image download cached; outage on download -> reason, no exception')
            dump = err.getvalue() + json.dumps(e)
            for root, _, files in os.walk(td):
                for f in files:
                    if f.endswith('.json'): dump += open(os.path.join(root, f), encoding='utf-8', errors='replace').read()
            assert TOK not in dump; ok.append('token never appears in logs, evidence or cache files')
        finally:
            srv.shutdown(); os.environ.pop('MAPILLARY_ACCESS_TOKEN', None)
    for o in ok: print('  ok -', o)
    print('facade_sources self-test ok (%d checks)' % len(ok))


if __name__ == '__main__':
    a = sys.argv
    if '--selftest' in a or len(a) < 3: _selftest(); sys.exit(0)
    cache, bid = a[1], a[2]; bd = os.path.join(cache, 'facade', 'buildings', bid)
    try:
        g = json.load(open(os.path.join(bd, 'geometry.json'), encoding='utf-8'))
        ev = fetch(bid, g, dict(cfg_from_env(), cache=cache, refresh='--refresh' in a))
        import facade_common as FC
        FC.write_json(os.path.join(bd, 'evidence.json'), ev)
        print(json.dumps(dict(status='ok', bid=bid, candidates=len(ev['candidates']), providers={k: v['status'] for k, v in ev['providers'].items()})))
    except Exception as e:
        print(json.dumps(dict(status='error', bid=bid, reason=f'{type(e).__name__}: {e}'))); sys.exit(1)
