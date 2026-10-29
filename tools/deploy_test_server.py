"""deploy_test_server.py deploy|rollback [server_dir]  - install SummerlinShellTool 0.4.2 on a TEST server only.
Default target: C:\\BTE-Migration\\07-shelltool-test-server (port 25567). Allowed: 07 and the disposable 08 copy; the pilot (06) and the
main Vegas server (03) are refused. Refuses while the server is running or if its last session did not stop cleanly.
deploy  : backs up the current tool JAR + plugin data (config, ledger, tracked edits, data file) to <server>\\shelltool-deploy-backups\\<time>,
          swaps the JAR, keeps config/ledger/cache; adds any missing 0.4.x config keys (overpass stays as configured).
rollback: restores the previous JAR and config from the newest backup (ledger kept: it is append-only and 0.4.0 reads it)."""
import glob, os, re, shutil, socket, sys, time
TARGETS = {r'C:\BTE-Migration\07-shelltool-test-server': 25567, r'C:\BTE-Migration\08-shelltool-v041-test': 25568,
           r'C:\BTE-Migration\09-shelltool-approach-test': 25569}
JAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugin', 'build', 'SummerlinShellTool-0.4.2.jar')
mode = sys.argv[1] if len(sys.argv) > 1 else 'deploy'
SRV = os.path.normpath(sys.argv[2]) if len(sys.argv) > 2 else r'C:\BTE-Migration\07-shelltool-test-server'
def refuse(m):
    print('REFUSED:', m)
    try: input('Press Enter...')
    except EOFError: pass
    sys.exit(1)
if SRV not in TARGETS: refuse(f'{SRV} is not an allowed test server (allowed: {", ".join(TARGETS)}). The pilot and main servers are never targeted.')
PORT = TARGETS[SRV]; PL = os.path.join(SRV, 'plugins'); PD = os.path.join(PL, 'SummerlinShellTool'); BK = os.path.join(SRV, 'shelltool-deploy-backups')
with socket.socket() as s:
    s.settimeout(1)
    if s.connect_ex(('127.0.0.1', PORT)) == 0: refuse(f'the test server is running (port {PORT}). Type "stop" in its window first; the loaded JAR is never swapped live.')
try: open(os.path.join(SRV, 'world', 'session.lock'), 'a').close()
except PermissionError: refuse('the world is locked by a running server.')
log = open(os.path.join(SRV, 'logs', 'latest.log'), encoding='utf-8', errors='replace').read()
last = log[log.rfind('Starting minecraft server'):]
if 'Stopping server' not in last or 'All dimensions are saved' not in last:
    refuse('the last session did not stop cleanly (no "Stopping server" / "All dimensions are saved" in logs/latest.log). Start it, type "stop", then deploy.')
if mode == 'deploy':
    if not os.path.exists(JAR): refuse(f'build not found: {JAR}')
    b = os.path.join(BK, time.strftime('%Y%m%d-%H%M%S')); os.makedirs(b)
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): shutil.copy2(j, b)
    shutil.copytree(PD, os.path.join(b, 'SummerlinShellTool'), ignore=shutil.ignore_patterns('reports'))
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    shutil.copy2(JAR, PL)
    cfg = open(os.path.join(PD, 'config.yml')).read()
    for k, v in (('overpass-enabled', 'false'), ('overpass-piers', 'false'), ('overpass-approach-grade', '0')):
        if not re.search(rf'^{k}:', cfg, re.M): cfg += f'\n{k}: {v}\n'
    cfg = re.sub(r'^prep-script: .*$', 'prep-script: ' + os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prep', 'shelltool_prep.py').replace('\\', '\\\\'), cfg, flags=re.M)
    open(os.path.join(PD, 'config.yml'), 'w').write(cfg)
    print('Deployed SummerlinShellTool 0.4.2 to', SRV, '| backup:', b)
    print('Plugins now:', sorted(os.path.basename(j) for j in glob.glob(os.path.join(PL, '*.jar'))))
    print('Kept: ledger.jsonl, tracked-edits.txt, data file, cache. Cached plans from older pipelines are re-prepared automatically.')
else:
    bs = sorted(glob.glob(os.path.join(BK, '*')))
    if not bs: refuse('no backup to roll back to.')
    b = bs[-1]
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    for j in glob.glob(os.path.join(b, 'SummerlinShellTool-*.jar')): shutil.copy2(j, PL)
    shutil.copy2(os.path.join(b, 'SummerlinShellTool', 'config.yml'), PD)
    print('Rolled back', SRV, 'to', b, '(ledger kept).'); print('Plugins now:', sorted(os.path.basename(j) for j in glob.glob(os.path.join(PL, '*.jar'))))
