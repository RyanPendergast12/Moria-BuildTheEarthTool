"""deploy_pilot.py deploy|rollback - SummerlinShellTool 0.4.3 for the PRIMARY server (06-summerlin-pilot-server, port 25566) ONLY.
Refuses while the pilot runs (deploy again after a clean 'stop') or if its last session did not stop cleanly.
deploy  : backs up the current JAR, the whole plugin data folder (config, ledger, tracked edits, data file, reports) and the pilot
          worlds to shelltool\\deploy-backups\\<time>-pilot-pre-0.4.3; freezes this release's prep scripts in
          shelltool\\releases\\v0.4.3\\prep (later repo edits cannot change the live pilot); installs the release JAR (hash-checked);
          keeps every existing config value and adds only the new 0.4.x keys (tested values); seeds the accepted crossing plan.
          Ledger, tracked edits, data file, caches and existing builds are kept; the world is not modified.
rollback: restores the previous JAR and config from the newest pilot backup (ledger kept). World backups are restored by hand only."""
import glob, hashlib, json, os, re, shutil, socket, sys, time
SRV = r'C:\BTE-Migration\06-summerlin-pilot-server'; PORT = 25566
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = os.path.join(REPO, 'plugin', 'build', 'SummerlinShellTool-0.4.3.jar')
JAR_SHA = '4c1f565d41520cc7c3b29690b054c18b76099482836621393bd21f77b090c594'          # the build tested on 10, approved in-game, published as v0.4.3
REL = r'C:\BTE-Migration\shelltool\releases\v0.4.3'; BK = r'C:\BTE-Migration\shelltool\deploy-backups'
CROSSING = None                       # v0.4.3 plans every eligible crossing itself (the accepted Charleston deck geometry is deterministic)
NEW_KEYS = [('overpass-enabled', 'true', 'Grade-separated crossings (overpasses).'),
            ('overpass-mode', 'auto', 'auto = every crossing that passes the eligibility checks (approved 2026-10-29); allowlist = only overpass-crossings.'),
            ('overpass-exclude', '[]', 'Crossing keys never generated (either mode).'),
            ('overpass-min-clearance', '4', 'Minimum air blocks between the lower road and the deck underside.'),
            ('overpass-crossings', '\n  - x_w234014889_w234014891   # W Charleston Blvd over CC-215 (approved 2026-10-28)', None),
            ('overpass-piers', 'false', 'Inferred piers: off until their layout has evidence.'),
            ('overpass-approach-grade', '0.06', 'Raised approaches onto enabled decks (approved Option C, tested at 0.06). 0 = off; allowed 0.04-0.10.')]
PL = os.path.join(SRV, 'plugins'); PD = os.path.join(PL, 'SummerlinShellTool')
assert os.path.basename(SRV) == '06-summerlin-pilot-server'

def refuse(m):
    print('REFUSED:', m)
    try: input('Press Enter...')
    except EOFError: pass
    sys.exit(1)
with socket.socket() as s:
    s.settimeout(1)
    if s.connect_ex(('127.0.0.1', PORT)) == 0:
        refuse(f'the pilot server is running (port {PORT}). Nothing was changed. Deployment is ready: type "stop" in the pilot window, wait for it to save, then run this again.')
try: open(os.path.join(SRV, 'world', 'session.lock'), 'a').close()
except PermissionError: refuse('the pilot world is locked by a running server.')
log = open(os.path.join(SRV, 'logs', 'latest.log'), encoding='utf-8', errors='replace').read()
last = log[log.rfind('Starting minecraft server'):]
if 'Stopping server' not in last or 'All dimensions are saved' not in last:
    refuse('the pilot\'s last session did not stop cleanly. Start it, type "stop", wait for "All dimensions are saved", then deploy.')

mode = sys.argv[1] if len(sys.argv) > 1 else 'deploy'
if mode == 'deploy':
    if not os.path.exists(JAR) or hashlib.sha256(open(JAR, 'rb').read()).hexdigest() != JAR_SHA: refuse(f'{JAR} is missing or is not the tested v0.4.3 build.')
    b = os.path.join(BK, time.strftime('%Y%m%d-%H%M%S') + '-pilot-pre-0.4.3'); os.makedirs(b)
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): shutil.copy2(j, b)
    shutil.copytree(PD, os.path.join(b, 'SummerlinShellTool'))
    for w in ('world', 'world_nether', 'world_the_end'):
        if os.path.isdir(os.path.join(SRV, w)): shutil.copytree(os.path.join(SRV, w), os.path.join(b, w), ignore=shutil.ignore_patterns('session.lock'))
    print('Backup:', b)
    if not os.path.isdir(os.path.join(REL, 'prep')):
        shutil.copytree(os.path.join(REPO, 'prep'), os.path.join(REL, 'prep'), ignore=shutil.ignore_patterns('__pycache__', 'classes'))
    shutil.copy2(JAR, REL)
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    shutil.copy2(JAR, PL)
    cfg = open(os.path.join(PD, 'config.yml'), encoding='utf-8').read()
    cfg = re.sub(r'^prep-script: .*$', 'prep-script: ' + os.path.join(REL, 'prep', 'shelltool_prep.py').replace('\\', '\\\\'), cfg, flags=re.M)
    for k, v, comment in NEW_KEYS:
        if not re.search(rf'^{k}:', cfg, re.M): cfg = cfg.rstrip('\n') + '\n' + (f'# {comment}\n' if comment else '') + f'{k}: {v}\n'
    open(os.path.join(PD, 'config.yml'), 'w', encoding='utf-8').write(cfg)
    m = re.search(r'^cache-dir: (.*)$', cfg, re.M); cache = m.group(1).strip() if m else os.path.join(PD, 'cache')
    os.makedirs(os.path.join(cache, 'crossings'), exist_ok=True)
    dst = os.path.join(cache, 'crossings', CROSSING[1]) if CROSSING else None
    if CROSSING and not os.path.exists(dst): shutil.copy2(os.path.join(*CROSSING), dst); print('Seeded the accepted crossing plan:', dst)
    print('Installed SummerlinShellTool 0.4.3 on the primary server. Plugins now:', sorted(os.path.basename(j) for j in glob.glob(os.path.join(PL, '*.jar'))))
    print('Prep scripts frozen at', os.path.join(REL, 'prep'), '| kept: ledger.jsonl, tracked-edits.txt, shells.txt, cache, every existing config value.')
    print('Start the server; the log should show "Enabling SummerlinShellTool v0.4.3" and "Overpass generation ENABLED: mode auto ... approaches grade 0.06".')
else:
    bs = sorted(glob.glob(os.path.join(BK, '*-pilot-pre-0.4.*')))
    if not bs: refuse('no pilot backup to roll back to.')
    b = bs[-1]
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    for j in glob.glob(os.path.join(b, 'SummerlinShellTool-*.jar')): shutil.copy2(j, PL)
    shutil.copy2(os.path.join(b, 'SummerlinShellTool', 'config.yml'), PD)
    print('Rolled the pilot back to', b, '(JAR + config; ledger kept). World backup, if ever needed, is in', b)
