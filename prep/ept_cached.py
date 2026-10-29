"""ept_cached.py — USGS 3DEP Entwine (EPT) point fetch with a persistent node cache, shared by all chunk preparations.
fetch(root_url, (x0, y0, x1, y1) in EPSG:3857, cache_dir) -> dict(x, y, z, cls, nret, nodes, fetched, reused)
Hierarchy pages and point tiles are cached on disk, so neighbouring chunks reuse downloads. Bounded: max_nodes per call,
per-request timeout, 2 retries with backoff. prune(cache_dir, max_gb) trims the oldest files."""
import io, json, os, time, urllib.request
import numpy as np
UA = {'User-Agent': 'BTE-local-shelltool/1.0'}

class Limit(Exception): pass

def _get(url, path, timeout):
    if path and os.path.exists(path):
        os.utime(path, None); return open(path, 'rb').read(), True
    last = None
    for i in range(3):
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True); open(path + '.part', 'wb').write(b); os.replace(path + '.part', path)
            return b, False
        except Exception as ex:
            last = ex; time.sleep(2 ** i)
    raise last

def fetch(root, box, cache_dir, max_nodes=300, timeout=45):
    import laspy
    root = root.rstrip('/'); name = root.split('/')[-1]; cd = os.path.join(cache_dir, name)
    meta = json.loads(_get(root + '/ept.json', os.path.join(cd, 'ept.json'), timeout)[0]); B = meta['bounds']
    bx0, by0, bx1, by1 = box
    if bx1 < B[0] or bx0 > B[3] or by1 < B[1] or by0 > B[4]: return None          # outside the dataset's published bounds
    def nb(d, x, y):
        span = (B[3] - B[0]) / 2 ** d; return B[0] + x * span, B[1] + y * span, B[0] + (x + 1) * span, B[1] + (y + 1) * span
    keys, pending = [], ['0-0-0-0']
    while pending:
        hk = pending.pop(); h = json.loads(_get(f'{root}/ept-hierarchy/{hk}.json', os.path.join(cd, 'h', hk + '.json'), timeout)[0])
        for k, cnt in h.items():
            d, x, y, z = map(int, k.split('-')); x0, y0, x1, y1 = nb(d, x, y)
            if x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1: continue
            if cnt == -1:
                if k != hk: pending.append(k)
            elif cnt > 0: keys.append(k)
    if len(keys) > max_nodes: raise Limit(f'{len(keys)} point tiles needed > max {max_nodes}')
    parts = []; fetched = reused = 0
    for k in sorted(keys):
        b, cached = _get(f'{root}/ept-data/{k}.laz', os.path.join(cd, 'd', k + '.laz'), timeout); reused += cached; fetched += not cached
        las = laspy.read(io.BytesIO(b)); X, Y = np.asarray(las.x), np.asarray(las.y)
        m = (X >= bx0) & (X <= bx1) & (Y >= by0) & (Y <= by1)
        if m.any(): parts.append((X[m], Y[m], np.asarray(las.z)[m], np.asarray(las.classification)[m], np.asarray(las.number_of_returns)[m]))
    if not parts: return dict(x=np.zeros(0), y=np.zeros(0), z=np.zeros(0), cls=np.zeros(0, int), nret=np.zeros(0, int), nodes=len(keys), fetched=fetched, reused=reused)
    c = [np.concatenate([p[j] for p in parts]) for j in range(5)]
    return dict(x=c[0], y=c[1], z=c[2], cls=c[3], nret=c[4], nodes=len(keys), fetched=fetched, reused=reused)

def prune(cache_dir, max_gb):
    fs = []
    for dp, _, names in os.walk(cache_dir):
        for n in names:
            p = os.path.join(dp, n)
            try: st = os.stat(p); fs.append((st.st_mtime, st.st_size, p))
            except OSError: pass
    total = sum(f[1] for f in fs); lim = max_gb * 1e9
    for mt, sz, p in sorted(fs):
        if total <= lim: break
        if p.endswith('ept.json'): continue
        try: os.remove(p); total -= sz
        except OSError: pass
    return total
