"""deploy_v07_primary.py deploy|rollback|hashes - SummerlinShellTool 0.7.0 (parking lots + Facade) for the PRIMARY server (06-summerlin-pilot-server, port 25566) ONLY.
Run only after the user approved the v0.7 in-game result. Refuses while the server runs or if its last session did not stop cleanly.
deploy  : backs up the current JAR, the whole plugin data folder and the worlds to shelltool\\deploy-backups\\<time>-primary-pre-0.7.0;
          freezes this release's prep scripts, assets and design packages in shelltool\\releases\\v0.7.0 (hash-checked against PREP_SHA);
          installs the approved JAR (hash-checked); points Snowball's prep-script at the frozen v0.7.0 prep (pipeline v4.4, parking; needs
          osm-extract\\clark_osm_index_v2.sqlite); Egg keeps its frozen v0.5.0 prep; adds only missing facade-* keys. Ledger, tracked edits, caches and builds are kept; the world is not modified.
rollback: restores the previous JAR and config from the newest primary pre-0.7 backup (ledger kept; parking/Facade jobs stay in it and stay
          undoable once 0.7.0 is reinstalled). World backups are restored by hand only."""
import glob, hashlib, os, re, shutil, socket, sys, time
SRV = r'C:\BTE-Migration\06-summerlin-pilot-server'; PORT = 25566
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VER = '0.7.0'
JAR = os.path.join(REPO, 'plugin', 'build', f'SummerlinShellTool-{VER}.jar')
JAR_SHA = 'd617e7efff1d9d8effca9a82e7dac5912ada56505022b91fa5297b01b16e0c64'                     # sha256 of the build the user approved in-game; None = refuse
PREP_SHA = 'e983e338a775e14aa820db23912d05c03a96fe88c2eb73ccbc39d5910c51e746'                    # tree hash (tree_sha below) of prep/ + packages/ the user approved; None = refuse
REL = r'C:\BTE-Migration\shelltool\releases\v0.7.0'; BK = r'C:\BTE-Migration\shelltool\deploy-backups'
PL = os.path.join(SRV, 'plugins'); PD = os.path.join(PL, 'SummerlinShellTool')
assert os.path.basename(SRV) == '06-summerlin-pilot-server'
INDEX_V2 = r'C:\BTE-Migration\shelltool\osm-extract\clark_osm_index_v2.sqlite'
FACADE_KEYS = [('facade-enabled', 'true', 'Facade tool (v0.6/v0.7): /facadetool. docs/facade/COMMANDS.md'),
               ('facade-prep-script', os.path.join(REL, 'prep', 'facade_prep.py'), 'Frozen release prep (later repo edits cannot change the live server).'),
               ('facade-packages-dir', os.path.join(REL, 'packages'), 'Frozen, approved design packages.'),
               ('facade-max-ops-chunk', '8000', None), ('facade-max-ops-whole', '60000', 'Packages may raise this only with a written limits.exception.'),
               ('facade-blocks-per-tick', '300', None), ('facade-place-ms-per-tick', '6', None), ('facade-prep-timeout-seconds', '600', None),
               ('facade-max-jobs', '1', None), ('facade-seed', '1', None),
               ('facade-events-file', r'C:\BTE-Migration\bluemap\events\facade.jsonl', 'BlueMap job overlay (status only; the renderer is not changed).'),
               # as tested on server 13 (v0.6.0-rc5 / v0.7.0): ASSISTED zones from facade\\assist, faces without evidence left unchanged
               ('facade-fallback', 'none', 'Faces without evidence are left as the shell (no STYLE-GENERATED fallback).'),
               ('facade-providers', 'mapillary,osm', None), ('facade-analyzer', 'builtin-cv', None),
               ('facade-secrets-file', "'C:\\BTE-Migration\\secrets\\mapillary.token'", 'Outside the repo; never logged.'),
               ('facade-refs-dir', "'C:\\BTE-Migration\\facade\\assist'", 'Aligned photos + person-drawn zones (ASSISTED).'),
               ('facade-ai-max-usd-per-job', '0', 'Paid provider disabled (0).'), ('facade-ai-max-usd-per-day', '0', None)]
IGNORE = shutil.ignore_patterns('__pycache__', 'classes', '*.pyc')

def tree_sha(*dirs):
    """one sha256 over every file (relative path + bytes) under the given repo dirs, ignoring caches"""
    h = hashlib.sha256()
    for d in dirs:
        for root, ds, fs in os.walk(os.path.join(REPO, d)):
            ds[:] = sorted(x for x in ds if x != '__pycache__')
            for f in sorted(fs):
                if f.endswith('.pyc'): continue
                p = os.path.join(root, f); h.update(os.path.relpath(p, REPO).replace('\\', '/').encode()); h.update(open(p, 'rb').read())
    return h.hexdigest()

def refuse(m):
    print('REFUSED:', m)
    try: input('Press Enter...')
    except EOFError: pass
    sys.exit(1)

if len(sys.argv) > 1 and sys.argv[1] == 'hashes':          # print the values to pin after approval
    print('JAR_SHA  =', hashlib.sha256(open(JAR, 'rb').read()).hexdigest() if os.path.exists(JAR) else 'missing')
    print('PREP_SHA =', tree_sha('prep', 'packages')); sys.exit(0)
with socket.socket() as s:
    s.settimeout(1)
    if s.connect_ex(('127.0.0.1', PORT)) == 0: refuse(f'the primary server is running (port {PORT}). Nothing was changed. Type "stop", wait for it to save, then run this again.')
try: open(os.path.join(SRV, 'world', 'session.lock'), 'a').close()
except PermissionError: refuse('the world is locked by a running server.')
log = open(os.path.join(SRV, 'logs', 'latest.log'), encoding='utf-8', errors='replace').read()
last = log[log.rfind('Starting minecraft server'):]
if 'Stopping server' not in last or 'All dimensions are saved' not in last: refuse('the last session did not stop cleanly. Start it, type "stop", wait for "All dimensions are saved", then deploy.')

mode = sys.argv[1] if len(sys.argv) > 1 else 'deploy'
if mode == 'deploy':
    if JAR_SHA is None or PREP_SHA is None: refuse('no approved build pinned (JAR_SHA/PREP_SHA). Deploy only after the in-game approval of v0.7.')
    if not os.path.exists(INDEX_V2): refuse(f'{INDEX_V2} is missing (build it with prep/osm_index_build.py; see CHANGELOG v0.7.0).')
    if not os.path.exists(JAR) or hashlib.sha256(open(JAR, 'rb').read()).hexdigest() != JAR_SHA: refuse(f'{JAR} is missing or is not the approved build.')
    if tree_sha('prep', 'packages') != PREP_SHA: refuse('prep/ or packages/ differ from the approved state.')
    b = os.path.join(BK, time.strftime('%Y%m%d-%H%M%S') + f'-primary-pre-{VER}'); os.makedirs(b)
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): shutil.copy2(j, b)
    shutil.copytree(PD, os.path.join(b, 'SummerlinShellTool'))
    for w in ('world', 'world_nether', 'world_the_end'):
        if os.path.isdir(os.path.join(SRV, w)): shutil.copytree(os.path.join(SRV, w), os.path.join(b, w), ignore=shutil.ignore_patterns('session.lock'))
    print('Backup:', b)
    if not os.path.isdir(REL):
        shutil.copytree(os.path.join(REPO, 'prep'), os.path.join(REL, 'prep'), ignore=IGNORE)
        shutil.copytree(os.path.join(REPO, 'packages'), os.path.join(REL, 'packages'), ignore=IGNORE)
    shutil.copy2(JAR, REL)
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    shutil.copy2(JAR, PL)
    if hashlib.sha256(open(os.path.join(PL, os.path.basename(JAR)), 'rb').read()).hexdigest() != JAR_SHA:
        refuse('the installed JAR does not match the approved hash after copying; restore from ' + b)
    cfg = open(os.path.join(PD, 'config.yml'), encoding='utf-8').read()
    cfg = re.sub(r'(?m)^prep-script: .*$', lambda m: 'prep-script: ' + os.path.join(REL, 'prep', 'shelltool_prep.py'), cfg)
    for k, v, comment in FACADE_KEYS:
        if not re.search(rf'^{k}:', cfg, re.M): cfg = cfg.rstrip('\n') + '\n' + (f'# {comment}\n' if comment else '') + f'{k}: {v}\n'
    open(os.path.join(PD, 'config.yml'), 'w', encoding='utf-8').write(cfg)
    print(f'Installed SummerlinShellTool {VER} (parking + Facade). Frozen prep + packages:', REL)
    print(f'Start the server; the log should show "Enabling SummerlinShellTool v{VER}", "Egg tool enabled" and the Facade line.')
else:
    # newest pre-0.7 backup that really holds an older JAR (a second 0.7.0 deploy makes a backup holding 0.7.0 itself)
    bs = [x for x in sorted(glob.glob(os.path.join(BK, '*-primary-pre-0.7.*')))
          if any(not os.path.basename(j).startswith('SummerlinShellTool-0.7') for j in glob.glob(os.path.join(x, 'SummerlinShellTool-*.jar')))]
    if not bs: refuse('no primary backup holding a pre-0.7 JAR to roll back to. Nothing was changed.')
    b = bs[-1]
    old = [j for j in glob.glob(os.path.join(b, 'SummerlinShellTool-*.jar')) if not os.path.basename(j).startswith('SummerlinShellTool-0.7')]
    if not old or not os.path.exists(os.path.join(b, 'SummerlinShellTool', 'config.yml')): refuse(f'backup {b} is incomplete. Nothing was changed.')
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    for j in old: shutil.copy2(j, PL)
    shutil.copy2(os.path.join(b, 'SummerlinShellTool', 'config.yml'), PD)
    print('Rolled back to', b, '(JAR + config; ledger kept). World backup, if ever needed, is in', b)
