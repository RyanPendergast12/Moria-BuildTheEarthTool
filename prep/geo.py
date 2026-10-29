"""geo.py — persistent TerraPlusMinus projection + terrain-height helper (one precompiled JVM per process).
    g = Geo(); g.fwd([(lon, lat)]) -> [(x, z)]; g.inv([(x, z)]) -> [(lon, lat)]; g.heights([(lon, lat)]) -> np.array (NaN on failure)
Same code path as proj.py / Heights.java (BTE_DEFAULT_SETTINGS), without per-call JVM start + source compilation."""
import glob, os, subprocess, threading
import numpy as np
S = os.path.dirname(os.path.abspath(__file__))
JAVA = glob.glob(r'C:\BTE-Migration\runtimes\jdk-17*\bin\java.exe')[0]
JAR = r'C:\BTE-Migration\downloads\terraplusminus\terraplusminus-1.3.3.jar'
CLS = os.path.join(S, 'projcheck', 'classes')
TPP_CACHE = r'C:\BTE-Migration\shelltool\tpp-cwd'          # Terra++ keeps its tile cache relative to the working directory

def _compile():
    src = os.path.join(S, 'projcheck', 'GeoServer.java'); out = os.path.join(CLS, 'GeoServer.class')
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
        os.makedirs(CLS, exist_ok=True)
        subprocess.run([JAVA.replace('java.exe', 'javac.exe'), '-cp', JAR, '-d', CLS, src], check=True, capture_output=True)

class Geo:
    def __init__(self):
        _compile(); os.makedirs(TPP_CACHE, exist_ok=True)
        self.p = subprocess.Popen([JAVA, '-cp', JAR + ';' + CLS, 'GeoServer'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, cwd=TPP_CACHE, bufsize=1 << 16)
        self.lock = threading.Lock()
    def _pairs(self, cmd, pts):
        out = []
        with self.lock:
            for k in range(0, len(pts), 256):                       # bounded batches: no pipe deadlock on large requests
                batch = pts[k:k + 256]
                self.p.stdin.write(''.join(f'{cmd} {float(a)!r} {float(b)!r}\n' for a, b in batch)); self.p.stdin.flush()
                got = 0
                while got < len(batch):
                    l = self.p.stdout.readline()
                    if not l: raise RuntimeError('GeoServer exited')
                    t = l.split()
                    if len(t) == 2: out.append((float(t[0]), float(t[1]))); got += 1
        return out
    def fwd(self, lonlats): return self._pairs('fwd', lonlats)
    def inv(self, xzs): return self._pairs('inv', xzs)
    def heights(self, lonlats):
        vals = []
        with self.lock:
            for k in range(0, len(lonlats), 2048):                  # answers only come at 'sync', so input never blocks on output
                self.p.stdin.write(''.join(f'h {float(a)!r} {float(b)!r}\n' for a, b in lonlats[k:k + 2048]) + 'sync\n'); self.p.stdin.flush()
                while True:
                    l = self.p.stdout.readline().strip()
                    if not l: raise RuntimeError('GeoServer exited')
                    if l == 'ok': break
                    if l.startswith('WARNING'): continue          # JVM warnings can reach stdout before the first answer
                    vals.append(float(l))
        return np.array(vals)
    def close(self):
        try: self.p.stdin.close(); self.p.wait(10)
        except Exception: self.p.kill()

if __name__ == '__main__':      # self-check against the slower reference helpers
    import sys, time; sys.path.insert(0, S); import proj
    g = Geo(); pts = [(-115.338830, 36.161700), (-115.33, 36.15)]
    t = time.time(); a = g.fwd(pts); b = g.inv(a); hh = g.heights(pts); print('geo.py', round(time.time() - t, 2), 's', a, b, hh)
    ref = proj.fwd(pts); assert all(abs(p[0] - q[0]) < 1e-3 and abs(p[1] - q[1]) < 1e-3 for p, q in zip(a, ref)), (a, ref)
    assert all(abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 for p, q in zip(b, pts)); assert np.isfinite(hh).all(); g.close(); print('ok')
