"""facade_label.py <assist_dir> <bid> <face_id> <cache_dir> [--port 8765] [--no-browser]
ASSISTED zone labelling (prototype). Opens the aligned photo of one face (the image + corners already in <assist_dir>/<bid>/assist.json) in the
browser; a person draws polygons for glazing, opaque material zones and awnings; Save writes them into that assist.json entry as "labels"
(image pixels). The next facade job (prep) maps them onto the measured shell and labels the result ASSISTED (evidence: reference).
Local only: binds 127.0.0.1, reads the cached photo, writes only that assist.json (previous version kept as assist.json.bak)."""
import http.server, json, os, shutil, sys, webbrowser
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', 'prep'))

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Facade zones (ASSISTED)</title><style>
body{margin:0;font:14px system-ui,sans-serif;background:#222;color:#eee}#bar{padding:6px 10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#tag{background:#b8860b;color:#000;font-weight:700;padding:2px 8px;border-radius:3px}canvas{display:block;cursor:crosshair}button,select,input{font:inherit}
#help{color:#aaa;font-size:12px;padding:0 10px 6px}</style></head><body>
<div id="bar"><span id="tag">ASSISTED: person-drawn zones, not automatic</span><b id="title"></b>
<select id="kind"><option value="glazing">glazing</option><option value="material:wall">material: wall</option><option value="material:band">material: band</option>
<option value="material:trim">material: trim</option><option value="material:parapet">material: parapet</option><option value="awning">awning</option></select>
<label>block <input id="block" list="blocks" placeholder="from photo colour" style="width:16em"></label>
<datalist id="blocks"><option>minecraft:smooth_sandstone</option><option>minecraft:white_terracotta</option><option>minecraft:white_concrete</option><option>minecraft:light_gray_concrete</option><option>minecraft:smooth_quartz</option><option>minecraft:stone_bricks</option><option>minecraft:bricks</option><option>minecraft:brown_terracotta</option><option>minecraft:terracotta</option><option>minecraft:granite</option><option>minecraft:polished_andesite</option><option>minecraft:gray_concrete</option><option>minecraft:spruce_planks</option></datalist>
<label>awning depth <input id="depth" type="number" min="1" max="6" value="2" style="width:3em"></label>
<button id="save">Save</button><span id="msg"></span></div>
<div id="help">Click to add points; double-click or Enter closes the polygon; Backspace removes the last point; Esc cancels.
Click a polygon to select it, drag its points to edit, Delete removes it. Material zones: type a block (else it is matched to the photo colour); with a zone selected, editing the block changes that zone. Yellow = face corners, grey = masked occluders (not sampled).</div>
<canvas id="c"></canvas><script>
const D = __DATA__, C = {glazing:'#3cf', 'material:wall':'#f5f', 'material:band':'#9f6', 'material:trim':'#fc6', 'material:parapet':'#c9f', awning:'#f80'};
const cv = document.getElementById('c'), g = cv.getContext('2d'), img = new Image();
document.getElementById('title').textContent = D.bid + ' ' + D.face + ' | ' + D.provider + ' ' + D.image_id;
const q = D.corners_px, xs = Object.values(q).map(p => p[0]), ys = Object.values(q).map(p => p[1]);
const pad = 0.25 * (Math.max(...xs) - Math.min(...xs)), X0 = Math.max(0, Math.min(...xs) - pad), Y0 = Math.max(0, Math.min(...ys) - pad);
const X1 = Math.min(D.size[0], Math.max(...xs) + pad), Y1 = Math.min(D.size[1], Math.max(...ys) + pad);
let s = 1, labels = (D.labels || []).map(l => ({key: l.kind === 'material' ? 'material:' + (l.role || 'wall') : l.kind, depth: l.depth || 2, block: l.block || '', pts: l.poly_px})),
    cur = [], sel = -1, drag = null;
const toImg = e => { const r = cv.getBoundingClientRect(); return [X0 + (e.clientX - r.left) / s, Y0 + (e.clientY - r.top) / s]; };
const toCv = p => [(p[0] - X0) * s, (p[1] - Y0) * s];
function inside(p, P) { let c = false; for (let i = 0, j = P.length - 1; i < P.length; j = i++)
  if ((P[i][1] > p[1]) != (P[j][1] > p[1]) && p[0] < (P[j][0] - P[i][0]) * (p[1] - P[i][1]) / (P[j][1] - P[i][1]) + P[i][0]) c = !c; return c; }
function poly(P, col, fill, close) { g.beginPath(); P.forEach((p, i) => { const [x, y] = toCv(p); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  if (close) g.closePath(); g.strokeStyle = col; g.lineWidth = 2; g.stroke(); if (fill) { g.fillStyle = col + '44'; g.fill(); } }
function draw() {
  g.drawImage(img, X0, Y0, X1 - X0, Y1 - Y0, 0, 0, cv.width, cv.height);
  (D.mask_px || []).forEach(m => { g.fillStyle = '#8888'; g.fillRect((m[0] - X0) * s, (m[1] - Y0) * s, (m[2] - m[0]) * s, (m[3] - m[1]) * s); });
  poly([q.u0v0, q.u1v0, q.u1v1, q.u0v1], '#ff0', false, true);
  labels.forEach((l, i) => { poly(l.pts, C[l.key], true, true); if (i === sel) l.pts.forEach(p => { const [x, y] = toCv(p); g.fillStyle = '#fff'; g.fillRect(x - 4, y - 4, 8, 8); }); });
  if (cur.length) { poly(cur, C[kind.value], false, false); cur.forEach(p => { const [x, y] = toCv(p); g.fillStyle = C[kind.value]; g.fillRect(x - 3, y - 3, 6, 6); }); }
}
img.onload = () => { s = Math.min((innerWidth - 20) / (X1 - X0), (innerHeight - 90) / (Y1 - Y0)); cv.width = (X1 - X0) * s; cv.height = (Y1 - Y0) * s; draw(); };
img.src = '/photo';
function close_() { if (cur.length >= 3) labels.push({key: kind.value, depth: +depth.value || 2, block: block.value.trim(), pts: cur}); cur = []; draw(); }
cv.onmousedown = e => { const p = toImg(e);
  if (!cur.length && sel >= 0) { const k = labels[sel].pts.findIndex(v => Math.hypot(v[0] - p[0], v[1] - p[1]) * s < 8); if (k >= 0) { drag = k; return; } }
  if (!cur.length) { const i0 = labels.map((l, i) => inside(p, l.pts) ? i : -1).filter(i => i >= 0).pop(); if (i0 !== undefined) block.value = labels[i0].block || ''; }
  if (!cur.length) { const i = labels.map((l, i) => inside(p, l.pts) ? i : -1).filter(i => i >= 0).pop(); if (i !== undefined && !e.shiftKey) { sel = i; draw(); return; } }
  sel = -1; cur.push(p); draw(); };
cv.onmousemove = e => { if (drag !== null) { labels[sel].pts[drag] = toImg(e); draw(); } };
cv.onmouseup = () => { drag = null; };
cv.ondblclick = () => { cur.pop(); close_(); };
block.oninput = () => { if (sel >= 0) labels[sel].block = block.value.trim(); };
onkeydown = e => { if (e.target === block) return; if (e.key === 'Enter') close_(); else if (e.key === 'Escape') { cur = []; sel = -1; draw(); }
  else if (e.key === 'Backspace' && cur.length) { cur.pop(); draw(); } else if (e.key === 'Delete' && sel >= 0) { labels.splice(sel, 1); sel = -1; draw(); } };
document.getElementById('save').onclick = async () => {
  const out = labels.map(l => { const [k, r] = l.key.split(':'); const o = {kind: k, poly_px: l.pts.map(p => [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10])};
    if (r) o.role = r; if (k === 'awning') o.depth = l.depth; if (k === 'material' && l.block) o.block = l.block.includes(':') ? l.block : 'minecraft:' + l.block; return o; });
  const r = await fetch('/save', {method: 'POST', body: JSON.stringify(out)}); msg.textContent = r.ok ? 'saved ' + out.length + ' zone(s) at ' + new Date().toLocaleTimeString() : 'save failed: ' + await r.text();
};
</script></body></html>"""


def main():
    a = sys.argv[1:]; port = int(a[a.index('--port') + 1]) if '--port' in a else 8765
    assist_dir, bid, face, cache = a[:4]
    ap = os.path.join(assist_dir, bid, 'assist.json'); doc = json.load(open(ap, encoding='utf-8'))
    it = next((x for x in doc.get('images') or [] if x.get('face') == face), None)
    if not it: sys.exit(f'{ap}: no aligned image for face {face} (mark the face corners first)')
    import facade_sources as R
    photo = it.get('path') or os.path.join(cache, 'facade', 'evidence', it['provider'], 'img', R._sha(str(it['image_id']))[:32] + '.jpg')
    if not os.path.exists(photo): sys.exit(f'photo not cached: {photo} (run one facade preview of {bid} first)')
    data = dict(bid=bid, face=face, provider=it['provider'], image_id=str(it['image_id']), size=it['image_size'], corners_px=it['corners_px'],
                mask_px=it.get('mask_px') or [], labels=it.get('labels') or [])

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *x): pass
        def _send(self, body, ctype, code=200):
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path == '/photo': return self._send(open(photo, 'rb').read(), 'image/jpeg')
            self._send(PAGE.replace('__DATA__', json.dumps(data)).encode(), 'text/html; charset=utf-8')
        def do_POST(self):
            if self.path != '/save': return self.send_error(404)
            try:
                labels = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))
                ok = isinstance(labels, list) and len(labels) <= 64 and all(
                    isinstance(l, dict) and l.get('kind') in ('glazing', 'material', 'awning') and isinstance(l.get('poly_px'), list) and 3 <= len(l['poly_px']) <= 64
                    and all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p) for p in l['poly_px']) for l in labels)
            except (ValueError, TypeError): ok = False
            if not ok: return self.send_error(400, 'invalid labels')
            import facade_schema as S
            bad = [f"{l['block']}: {S.check_block(l['block'])}" for l in labels if 'block' in l and (not isinstance(l['block'], str) or S.check_block(l['block']))]
            if bad: return self._send(('invalid block ' + '; '.join(bad)).encode(), 'text/plain', 400)
            d = json.load(open(ap, encoding='utf-8'))
            for x in d['images']:
                if x.get('face') == face and str(x.get('image_id')) == data['image_id']: x['labels'] = labels; x['labels_by'] = 'person (facade_label.py)'
            shutil.copyfile(ap, ap + '.bak'); tmp = ap + '.tmp'; json.dump(d, open(tmp, 'w', encoding='utf-8'), indent=1); os.replace(tmp, ap)
            self._send(b'ok', 'text/plain')

    srv = http.server.HTTPServer(('127.0.0.1', port), H); url = f'http://127.0.0.1:{port}/'
    print(f'labelling {bid} {face} at {url}  (Ctrl+C to stop; saves to {ap})', flush=True)
    if '--no-browser' not in a: webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == '__main__':
    main()
