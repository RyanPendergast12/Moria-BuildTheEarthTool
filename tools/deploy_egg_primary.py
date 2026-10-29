"""deploy_egg_primary.py deploy|rollback - SummerlinShellTool 0.5.0 (Egg) for the PRIMARY server (06-summerlin-pilot-server, port 25566) ONLY.
Run only after the user approved the v0.5 in-game result. Refuses while the server runs or if its last session did not stop cleanly.
deploy  : backs up the current JAR, the whole plugin data folder and the worlds to shelltool\\deploy-backups\\<time>-primary-pre-0.5.0;
          freezes this release's prep scripts in shelltool\\releases\\v0.5.0\\prep; installs the tested JAR (hash-checked); keeps every
          existing config value (Snowball keeps its frozen v0.4.3 prep) and adds only the egg-* keys; writes the converted-region list
          (capture protection) from the initial world manifest. Ledger, tracked edits, caches and builds are kept; the world is not modified.
rollback: restores the previous JAR and config from the newest primary backup (ledger kept; Egg jobs stay in it and stay undoable
          once 0.5.0 is reinstalled). World backups are restored by hand only."""
import glob, hashlib, os, re, shutil, socket, sys, time
SRV = r'C:\BTE-Migration\06-summerlin-pilot-server'; PORT = 25566
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = os.path.join(REPO, 'plugin', 'build', 'SummerlinShellTool-0.5.0.jar')
JAR_SHA = 'c29977b62771e45b123e4ce657fe74d77249702d4b7887b68c86ddc0a8498a8c'   # v0.5.0, approved in-game 2026-10-29
REL = r'C:\BTE-Migration\shelltool\releases\v0.5.0'; BK = r'C:\BTE-Migration\shelltool\deploy-backups'
MANIFEST = r'C:\BTE-Migration\summerlin\pilot\pilotserver-world-initial.manifest.tsv'
PL = os.path.join(SRV, 'plugins'); PD = os.path.join(PL, 'SummerlinShellTool')
assert os.path.basename(SRV) == '06-summerlin-pilot-server'
EGG_KEYS = [('egg-enabled', 'true', 'Egg tool (v0.5): one 64x64 tile per egg block hit. docs/egg/COMMANDS.md'),
            ('egg-prep-script', os.path.join(REL, 'prep', 'egg_prep.py'), 'Frozen release prep (later repo edits cannot change the live server).'),
            ('egg-baseline-world', "''", 'Approved pilot snapshot (empty = prep default: the 910 pilot-generated chunks only).'),
            ('egg-prep-workers', '1', None), ('egg-max-prep-queue', '16', None), ('egg-prep-timeout-seconds', '600', None), ('egg-max-jobs', '2', None),
            ('egg-max-cut', '2', 'Terrain limits (blocks) and per-tile op cap: plans over the cap are refused.'), ('egg-max-fill', '2', None),
            ('egg-max-ops-per-tile', '12000', None), ('egg-blocks-per-tick', '300', None), ('egg-place-ms-per-tick', '6', None),
            ('egg-seed', '1', None), ('egg-cooldown-ms', '3000', None),
            ('egg-events-file', r'C:\BTE-Migration\bluemap\events\egg.jsonl', 'BlueMap job overlay (status only; the renderer is not changed).'),
            ('egg-capture-baseline', 'true', 'Record the untouched state of every chunk generated from now on (provenance for Egg).'),
            ('egg-generate-missing', 'true', 'Generate missing tile chunks one at a time. Stop the server only when /eggtool status shows no Egg jobs.'),
            ('egg-capture-protected-regions', 'egg-converted-regions.txt', 'Converted purchased-world regions: only chunks absent from disk may be captured.')]

def refuse(m):
    print('REFUSED:', m)
    try: input('Press Enter...')
    except EOFError: pass
    sys.exit(1)
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
    if JAR_SHA is None: refuse('no approved build pinned (JAR_SHA). Deploy only after the in-game approval of v0.5.')
    if not os.path.exists(JAR) or hashlib.sha256(open(JAR, 'rb').read()).hexdigest() != JAR_SHA: refuse(f'{JAR} is missing or is not the approved build.')
    b = os.path.join(BK, time.strftime('%Y%m%d-%H%M%S') + '-primary-pre-0.5.0'); os.makedirs(b)
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
    for k, v, comment in EGG_KEYS:
        if not re.search(rf'^{k}:', cfg, re.M): cfg = cfg.rstrip('\n') + '\n' + (f'# {comment}\n' if comment else '') + f'{k}: {v}\n'
    open(os.path.join(PD, 'config.yml'), 'w', encoding='utf-8').write(cfg)
    regs = sorted({l.split('\t')[0].split('/')[-1] for l in open(MANIFEST) if l.startswith('region/') and l.split('\t')[0].endswith('.mca')})
    open(os.path.join(PD, 'egg-converted-regions.txt'), 'w').write('# Converted purchased-world regions (initial manifest): capture only chunks absent from disk.\n' + '\n'.join(regs) + '\n')
    print('Installed SummerlinShellTool 0.5.0 (Egg). Protected converted regions:', len(regs))
    print('Start the server; the log should show "Enabling SummerlinShellTool v0.5.0" and "Egg tool enabled".')
else:
    bs = sorted(glob.glob(os.path.join(BK, '*-primary-pre-0.5.*')))
    if not bs: refuse('no primary pre-0.5 backup to roll back to.')
    b = bs[-1]
    for j in glob.glob(os.path.join(PL, 'SummerlinShellTool-*.jar')): os.remove(j)
    for j in glob.glob(os.path.join(b, 'SummerlinShellTool-*.jar')): shutil.copy2(j, PL)
    shutil.copy2(os.path.join(b, 'SummerlinShellTool', 'config.yml'), PD)
    print('Rolled back to', b, '(JAR + config; ledger kept). World backup, if ever needed, is in', b)
