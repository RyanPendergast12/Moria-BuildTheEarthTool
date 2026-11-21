"""mock_imagery_server.py - stdlib stand-in for the Mapillary Graph /images endpoint, the Panoramax /api/search endpoint and
image downloads, for facade_sources / facade_associate tests (docs/facade/SOURCES.md). Owner: R.
    python tools/mock_imagery_server.py <fixture_dir> [--port 8765] [--mode ok|outage|timeout|malformed|wrong-building]
    GET /__mode?m=<mode>  switches the mode at runtime.
fixture_dir/images.json: {"ok": [graph items], "wrong-building": [graph items], "panoramax": [STAC features]}; image files are
served from fixture_dir at /img/<name>. The text '{base}' in fixture URLs is replaced by this server's base URL.
Modes: ok; outage (503 everywhere); timeout (sleeps before answering); malformed (truncated JSON); wrong-building (serves the
"wrong-building" items: cameras in front of a neighbouring building). /images requires an 'Authorization: OAuth <token>' header (401
otherwise), like the real API. In-process use: srv = start(fixture_dir, mode); srv.url; srv.set_mode(m); srv.hits; srv.shutdown()."""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MODES = ('ok', 'outage', 'timeout', 'malformed', 'wrong-building')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype='application/json'):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b))); self.end_headers()
        try: self.wfile.write(b)
        except OSError: pass                                   # client gave up (timeout mode)

    def do_GET(self):
        s = self.server; u = urlsplit(self.path); q = parse_qs(u.query)
        if u.path == '/__mode':
            m = q.get('m', [''])[0]
            if m not in MODES: return self.send(400, '{"error":"unknown mode"}')
            s.mode = m; return self.send(200, json.dumps({'mode': m}))
        s.hits += 1
        if s.mode == 'outage': return self.send(503, '{"error":"service unavailable"}')
        if s.mode == 'timeout': time.sleep(s.sleep_s)
        fx = json.loads(open(os.path.join(s.fixture_dir, 'images.json'), encoding='utf-8').read().replace('{base}', s.url))
        if u.path == '/images':
            if not self.headers.get('Authorization', '').startswith('OAuth '): return self.send(401, '{"error":"auth"}')
            if s.mode == 'malformed': return self.send(200, '{"data": [{"id": ')
            items = fx.get('wrong-building' if s.mode == 'wrong-building' else 'ok', [])
            try:
                w, so, e, n = map(float, q['bbox'][0].split(','))
                items = [i for i in items if w <= i['geometry']['coordinates'][0] <= e and so <= i['geometry']['coordinates'][1] <= n]
            except (KeyError, ValueError): return self.send(400, '{"error":"bbox"}')
            return self.send(200, json.dumps({'data': items}))
        if u.path == '/api/search':
            if s.mode == 'malformed': return self.send(200, '<html>')
            return self.send(200, json.dumps({'type': 'FeatureCollection', 'features': fx.get('panoramax', []), 'links': []}))
        if u.path.startswith('/img/'):
            p = os.path.join(s.fixture_dir, os.path.basename(u.path))
            if not os.path.isfile(p): return self.send(404, '{"error":"no image"}')
            if s.mode == 'malformed': return self.send(200, b'not a jpeg', 'image/jpeg')
            return self.send(200, open(p, 'rb').read(), 'image/jpeg')
        self.send(404, '{"error":"not found"}')


def start(fixture_dir, mode='ok', port=0, sleep_s=5.0):
    srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    srv.daemon_threads = True; srv.fixture_dir = fixture_dir; srv.mode = mode; srv.sleep_s = sleep_s; srv.hits = 0
    srv.url = 'http://127.0.0.1:%d' % srv.server_address[1]
    def set_mode(m):
        assert m in MODES, m
        srv.mode = m
    srv.set_mode = set_mode
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == '__main__':
    a = sys.argv
    if len(a) < 2: print(__doc__); sys.exit(2)
    port = int(a[a.index('--port') + 1]) if '--port' in a else 8765
    mode = a[a.index('--mode') + 1] if '--mode' in a else 'ok'
    srv = start(a[1], mode, port); print('mock imagery server on', srv.url, 'mode', mode, flush=True)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt: srv.shutdown()
