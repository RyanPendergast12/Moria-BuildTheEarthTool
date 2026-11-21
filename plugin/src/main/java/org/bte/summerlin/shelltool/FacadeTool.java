package org.bte.summerlin.shelltool;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import org.bte.summerlin.shelltool.ShellToolPlugin.Placement;
import org.bukkit.Bukkit;
import org.bukkit.Chunk;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.block.BlockState;
import org.bukkit.block.TileState;
import org.bukkit.block.data.BlockData;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.TabExecutor;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.BoundingBox;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;

import static org.bte.summerlin.shelltool.ShellToolPlugin.key;
import static org.bte.summerlin.shelltool.ShellToolPlugin.pos;

/**
 * Facade tool (v0.6, docs/facade/ARCHITECTURE.md sections 2, 4, 6, 7): compiled facade plans (prep "plan") applied to the targeted
 * building, clipped to a scope (the targeted chunk, or every chunk of the building after a preview + confirm), through the shared
 * ledger. Writes only air or eligible owned cells; component instances are all-or-nothing; undo/retirement follow the Egg rules.
 * No item or projectile binding: commands only.
 */
final class FacadeTool implements Listener, TabExecutor {
    static final String TAG = "[Facade] ";
    /** ledger contents whose cells a facade may replace (with feature = this building); "" (pre-0.4 records) is NOT eligible */
    static final Set<String> OWNABLE = Set.of("shells", "facade", "facade-adopt");
    static final Set<String> STATUSES = Set.of("ready", "unresolved", "no_geometry", "no_package", "invalid_package", "refused_limits", "protected", "nothing_to_do", "provider_failure");
    static final long PREVIEW_VALID_MS = 600_000;
    static final int P_SCOPE = 0, P_CLIP = 1, P_RSCAN = 2, P_WAIT = 3, P_RUN = 4;
    /** a component with more cells than this has its pre-check spread over ticks (its writes stay atomic in one tick) */
    static final int SPLIT = 500;
    /** B1 (physical): how a block may depend on a neighbour, per material (computed once at enable; dependsOn() refines it by state).
     *  FREE: stands on its own (full/occluding or solid-collision blocks: containers, glass, slabs, fences ...); GRAVITY: falls without the
     *  block below; WALL: fixed to the block behind it (facing points away); FACING: fixed to the block it faces (cocoa); MULTI: its
     *  MultipleFacing faces (+ above: vines hang); HANG: hangs from the block above; UPDOWN: either; STAND: stands on the block below
     *  (an unlisted Directional one: any face); ANY: any face (bells, materials not in the table). */
    static final byte FREE = 0, GRAVITY = 1, WALL = 2, FACING = 3, MULTI = 4, HANG = 5, UPDOWN = 6, STAND = 7, ANY = 8;
    static final Map<Material, Byte> DEP = new EnumMap<>(Material.class);
    static final BlockFace[] FACES = {BlockFace.UP, BlockFace.DOWN, BlockFace.NORTH, BlockFace.SOUTH, BlockFace.EAST, BlockFace.WEST};
    /** B1: blocks that hang on, stand on or are fixed to a face although their collision box may be solid (never FREE) */
    static final java.util.regex.Pattern ATTACH = java.util.regex.Pattern.compile(
            "TORCH|SIGN|LADDER|BUTTON|LEVER|TRIPWIRE|VINE|LANTERN|BELL|BANNER|HEAD|SKULL|RAIL|REDSTONE_WIRE|PRESSURE_PLATE|CARPET|^SNOW$|SCAFFOLDING|DOOR|BED$|CANDLE|LICHEN|AMETHYST_CLUSTER|BUD$"
            + "|CACTUS|SUGAR_CANE|BAMBOO|KELP|CHORUS|SEA_PICKLE|TURTLE_EGG|LILY_PAD|AZALEA$|DRIPLEAF|FLOWER_POT|^POTTED_|COMPARATOR|REPEATER");
    static final java.util.regex.Pattern WALLP = java.util.regex.Pattern.compile("WALL_TORCH|_WALL_|^LADDER$|^TRIPWIRE_HOOK$|AMETHYST_CLUSTER|_BUD$"),
            HANGP = java.util.regex.Pattern.compile("^HANGING_ROOTS$|^SPORE_BLOSSOM$|^CAVE_VINES|^WEEPING_VINES");
    static byte depCat(Material m) {
        String n = m.name();
        if (m.isAir() || m == Material.WATER || m == Material.LAVA) return FREE;
        if (m.hasGravity()) return GRAVITY;
        if (WALLP.matcher(n).find()) return WALL;
        if (m == Material.COCOA) return FACING;
        if (m == Material.VINE || m == Material.GLOW_LICHEN) return MULTI;
        if (HANGP.matcher(n).find()) return HANG;
        if (m == Material.POINTED_DRIPSTONE) return UPDOWN;
        if (m == Material.BELL) return ANY;
        if (ATTACH.matcher(n).find()) return STAND;
        return m.isOccluding() || m.isSolid() ? FREE : STAND;
    }
    /** B1: the block d needs the neighbour in direction toX (toward the cell being changed) to stay where it is */
    static boolean dependsOn(BlockData d, BlockFace toX) {
        byte c = DEP.getOrDefault(d.getMaterial(), ANY);
        if (c == FREE) return false;
        if (d instanceof org.bukkit.block.data.FaceAttachable a) return switch (a.getAttachedFace()) {      // buttons, levers, grindstones
            case FLOOR -> toX == BlockFace.DOWN; case CEILING -> toX == BlockFace.UP;
            default -> !(d instanceof org.bukkit.block.data.Directional r) || toX == r.getFacing().getOppositeFace(); };
        if (d instanceof org.bukkit.block.data.type.Lantern l) return toX == (l.isHanging() ? BlockFace.UP : BlockFace.DOWN);
        if (d instanceof org.bukkit.block.data.type.TrapDoor) return false;                                    // needs no support since 1.9
        return switch (c) {
            case GRAVITY -> toX == BlockFace.DOWN;
            case WALL -> !(d instanceof org.bukkit.block.data.Directional r) || toX == r.getFacing().getOppositeFace();
            case FACING -> !(d instanceof org.bukkit.block.data.Directional r) || toX == r.getFacing();
            case MULTI -> toX == BlockFace.UP || !(d instanceof org.bukkit.block.data.MultipleFacing mf) || mf.hasFace(toX);
            case HANG -> toX == BlockFace.UP;
            case UPDOWN -> toX == BlockFace.UP || toX == BlockFace.DOWN;
            case STAND -> toX == BlockFace.DOWN || (d instanceof org.bukkit.block.data.Directional && !(d instanceof org.bukkit.block.data.type.Door) && !(d instanceof org.bukkit.block.data.type.Bed));
            default -> true; };
    }
    enum Kind { PREVIEW, APPLY, ADOPT, UNDO }

    final ShellToolPlugin pl;
    final boolean enabled; final String prepScript, eventsFile; final File packagesDir, fdir, assetsDir;
    final int maxOpsChunk, maxOpsWhole, blocksPerTick, timeoutS, maxJobs, maxLoaded; final double msPerTick; final long seed;
    /** chunk window: outstanding async loads per job; items admitted per tick / held in the window (bounds the lookahead's allocation) */
    static final int MAX_LOADS = 8, ADMIT_PER_TICK = 4096, MAX_WINDOW_ITEMS = 32768;
    /** automatic facades (docs/facade/AUTO.md section 8): passed to the prep as MORIA_FACADE_* environment variables (never a secret value) */
    final boolean autoEnabled; final String fallback, fallbackStyle; final Map<String, String> autoEnv = new LinkedHashMap<>();
    final Map<UUID, String> selection = new HashMap<>();                      // "auto" (default) | "package:<id>" | "style:<id>"; in memory, cleared on login
    final Map<String, Long> previews = new HashMap<>();                       // owner|plan_key (whole preview) -> time
    /** an owner's latest adopt dry run (key = owner|adopt|bid|candidate-file sha): 'adopt confirm' adopts only these cells, 'adopt show' outlines them */
    record AdoptDry(String key, String world, long[] cells, long at) {}
    final Map<String, AdoptDry> adoptDry = new HashMap<>(); final Map<UUID, BukkitTask> shows = new HashMap<>();
    final Map<String, FJob> jobs = new LinkedHashMap<>();                     // running facade jobs (main thread)
    final Map<String, LongSet> bidIndex = new HashMap<>();                  // feature -> positions with live-or-dead facade placements (retirement union)
    final Map<String, JsonObject> identified = new ConcurrentHashMap<>();     // "x,z" -> identify status line (ready results only)
    final Map<String, Plan> planCache = Collections.synchronizedMap(new LinkedHashMap<>(8, 0.75f, true) {
        protected boolean removeEldestEntry(Map.Entry<String, Plan> e) { return size() > 4; } });
    final LinkedBlockingQueue<Prep> queue = new LinkedBlockingQueue<>();
    final Map<String, Prep> preps = new ConcurrentHashMap<>();                // dedup: one process per identical request
    Thread worker; volatile boolean stopping;
    int failNext = -1;
    final Object eventLock = new Object();
    /** JVM GC time so far (all collectors, ms): a step's wall time minus this delta is the plugin's own work */
    static final List<java.lang.management.GarbageCollectorMXBean> GCS = java.lang.management.ManagementFactory.getGarbageCollectorMXBeans();
    static long gcMillis() { long t = 0; for (java.lang.management.GarbageCollectorMXBean b : GCS) t += Math.max(0, b.getCollectionTime()); return t; }
    /** chunk holds go through the plugin's shared ticket count (pl.ticket/unticket); a finished job's chunks are released a few per tick */
    final Deque<Chunk> release = new ArrayDeque<>(); BukkitTask releaser;
    void releaseSome() {
        long end = System.nanoTime() + 2_000_000;
        while (!release.isEmpty() && System.nanoTime() < end) pl.unticket(release.poll());
        if (release.isEmpty() && releaser != null) { releaser.cancel(); releaser = null; }
    }
    BukkitTask mon; long monLast, monDuring, monAfter; int monAfterTicks = -1; String monJob;
    /** longest interval between two server ticks while a job runs (and for 10 s after it), so lag outside its own steps is visible */
    void monitorStart(String job) { monJob = job; monDuring = 0; monAfter = 0; monAfterTicks = -1; monLast = 0; if (mon == null) mon = Bukkit.getScheduler().runTaskTimer(pl, this::monTick, 1, 1); }
    void monTick() {
        long now = System.nanoTime();
        if (monLast != 0) { long gap = now - monLast; if (monAfterTicks < 0) monDuring = Math.max(monDuring, gap); else monAfter = Math.max(monAfter, gap); }
        monLast = now;
        if (monAfterTicks >= 0 && ++monAfterTicks > 200) {
            pl.getLogger().info("facade job " + monJob + ": longest server tick interval " + Math.round(monDuring / 1e5) / 10.0 + " ms during the job, " + Math.round(monAfter / 1e5) / 10.0 + " ms in the 10 s after it (50 = no lag)");
            mon.cancel(); mon = null; }
    }                                                         // admin test hook (simfail): the next component fails its n-th write re-check
    final Gson gson = new Gson();

    FacadeTool(ShellToolPlugin pl) {
        this.pl = pl; FileConfiguration c = pl.getConfig();
        enabled = c.getBoolean("facade-enabled", true);
        prepScript = c.getString("facade-prep-script", "C:\\BTE-Migration\\repo\\Moria-BuildTheEarthTool\\prep\\facade_prep.py");
        packagesDir = new File(c.getString("facade-packages-dir", "C:\\BTE-Migration\\repo\\Moria-BuildTheEarthTool\\packages"));
        assetsDir = new File(new File(prepScript).getParentFile(), "facade_assets");
        maxOpsChunk = c.getInt("facade-max-ops-chunk", 8000); maxOpsWhole = c.getInt("facade-max-ops-whole", 60000);
        blocksPerTick = Math.max(1, c.getInt("facade-blocks-per-tick", 300)); msPerTick = c.getDouble("facade-place-ms-per-tick", 6.0);
        timeoutS = c.getInt("facade-prep-timeout-seconds", 600); maxJobs = Math.max(1, c.getInt("facade-max-jobs", 1));
        maxLoaded = Math.max(9, c.getInt("facade-max-loaded-chunks", 64));    // >= 3 (one cell's chunk + ring) with room for prefetch
        eventsFile = c.getString("facade-events-file", ""); seed = c.getLong("facade-seed", 1);
        fdir = new File(pl.cacheDir, "facade");
        autoEnabled = c.getBoolean("facade-auto-enabled", true);
        fallback = "none".equalsIgnoreCase(c.getString("facade-fallback", "procedural")) ? "none" : "procedural";
        fallbackStyle = c.getString("facade-fallback-style", "summerlin-stucco-retail");
        String[][] env = {{"MORIA_FACADE_PROVIDERS", "facade-providers", "local,mapillary,panoramax,osm"}, {"MORIA_FACADE_ANALYZER", "facade-analyzer", "builtin-cv"},
                {"MORIA_FACADE_SECRETS_FILE", "facade-secrets-file", "C:\\BTE-Migration\\secrets\\mapillary.token"}, {"MORIA_FACADE_OLLAMA_MODEL", "facade-ollama-model", ""}, {"MORIA_FACADE_REFS_DIR", "facade-refs-dir", ""},
                {"MORIA_FACADE_PROVIDER_TIMEOUT", "facade-provider-timeout-seconds", "20"}, {"MORIA_FACADE_MAX_REQUESTS", "facade-max-requests-per-job", "40"},
                {"MORIA_FACADE_AI_MAX_USD_JOB", "facade-ai-max-usd-per-job", "0"}, {"MORIA_FACADE_AI_MAX_USD_DAY", "facade-ai-max-usd-per-day", "0"}};
        for (String[] e : env) { String v = Objects.toString(c.get(e[1], e[2]), e[2]); if (!v.isEmpty()) autoEnv.put(e[0], v); }
        for (Material m : Material.values()) if (m.isBlock() && !m.isLegacy()) DEP.put(m, depCat(m));
        for (String b : List.of("minecraft:stone", "minecraft:air", "minecraft:glass_pane[east=true,north=false,south=false,waterlogged=false,west=false]",
                "minecraft:stone_brick_stairs[facing=north,half=top,shape=straight,waterlogged=false]", "minecraft:smooth_stone_slab[type=top,waterlogged=false]")) {
            BlockData d = pl.egg.parse(b); if (d != null) ShellToolPlugin.matchesAfter("facade", b, d); }        // warm-up: first parses are slow
        new GsonBuilder().serializeNulls().setPrettyPrinting().create().toJson(new JsonObject());              // and the first report serialisation
        World ww = Bukkit.getWorld(pl.worldName);                                                              // and the first world reads: block-state
        if (ww != null) { Block b0 = ww.getSpawnLocation().getBlock();                                         // classes load on the first getState (~100 ms)
            b0.getState(false); b0.getBlockData().getAsString(); ww.getNearbyEntities(BoundingBox.of(b0)); new Plan().raster(null); new LongSet().add(1L); }
        for (List<Placement> l : pl.byPos.values()) for (Placement p : l) if (p.content().equals("facade")) index(p);
        if (enabled) { worker = new Thread(this::work, "Facade-prep"); worker.setDaemon(true); worker.start(); }
        pl.getLogger().info("Facade tool " + (enabled ? "enabled" : "DISABLED") + ": prep " + prepScript + ", facade placements indexed for " + bidIndex.size() + " building(s)");
    }
    void stop() {
        stopping = true;
        for (FJob j : new ArrayList<>(jobs.values())) j.cancel("server stopping");
        for (Chunk c : release) pl.unticket(c); release.clear(); if (releaser != null) releaser.cancel();
        for (Prep p : preps.values()) p.cancel();
        if (worker != null) worker.interrupt();
    }
    void index(Placement p) { bidIndex.computeIfAbsent(p.feature(), k -> new LongSet()).add(pos(p.x(), p.y(), p.z())); }
    /** shared ledger strings (block states repeat across hundreds of thousands of placements); main thread */
    final Map<String, String> canonStr = new HashMap<>();
    String canon(String s0) { return canonStr.computeIfAbsent(s0, k -> k); }
    /** a state's getAsString, once per distinct state (equal BlockData = same block state; the key is never mutated) */
    final Map<BlockData, String> canonData = new HashMap<>();
    String canon(BlockData d) { return canonData.computeIfAbsent(d, k -> canon(k.getAsString())); }
    Placement latest(World w, long p) {
        List<Placement> l = pl.byPos.get(p); if (l == null) return null;
        for (int i = l.size() - 1; i >= 0; i--) { Placement q = l.get(i); if (!pl.undone.contains(q.job()) && q.world().equals(w.getName())) return q; }
        return null;
    }
    static int px(long p) { return (int) (p >> 38); }
    static int pz(long p) { return (int) (p << 26 >> 38); }
    static int py(long p) { return (int) (p << 52 >> 52); }
    static String csvq(String s) { return s == null ? "" : "\"" + s.replace("\"", "\"\"") + "\""; }
    /** "k n, k n, ... (+m more)": the n largest counts */
    static String top(Map<String, Integer> m, int n) {
        List<Map.Entry<String, Integer>> l = new ArrayList<>(m.entrySet()); l.sort(Map.Entry.<String, Integer>comparingByValue().reversed());
        StringBuilder b = new StringBuilder(); for (int i = 0; i < Math.min(n, l.size()); i++) b.append(i == 0 ? "" : ", ").append(l.get(i).getKey()).append(' ').append(l.get(i).getValue());
        return (b.length() == 0 ? "none" : b) + (l.size() > n ? " (+" + (l.size() - n) + " more)" : "");
    }
    static int cxOf(long k) { return (int) (k >> 32); }
    static int czOf(long k) { return (int) k; }
    /** serpentine chunk order: rows of chunk z ascending, chunk x ascending on even rows and descending on odd ones (unique per chunk) */
    static long serp(long k) { int x = cxOf(k), z = czOf(k); return ((long) z << 32) + ((z & 1) == 0 ? x : -(long) x); }
    /** a stable permutation of n items by the serpentine order of their chunk (ties keep their order: newest-first undo stays newest-first per cell) */
    static int[] serpentine(int n, java.util.function.IntToLongFunction chunk) {
        long[] s = new long[n]; LongSet d = new LongSet(); for (int i = 0; i < n; i++) d.add(s[i] = serp(chunk.applyAsLong(i)));
        long[] r = d.toArray(); Arrays.sort(r); long[] o = new long[n];
        for (int i = 0; i < n; i++) o[i] = ((long) Arrays.binarySearch(r, s[i]) << 32) | i;
        Arrays.sort(o); int[] p = new int[n]; for (int i = 0; i < n; i++) p[i] = (int) o[i]; return p;
    }
    /** chunk window entry of one job: refs = window items needing it; PASSIVE = counted, never ticketed by this job (a ring chunk: loaded
     *  through its held neighbour's ticket; or an own chunk that cannot be held: edge / not generated) */
    static final byte W_PASSIVE = 0, W_WANT = 1, W_LOADING = 2, W_HELD = 3;
    static final class WC { int refs; byte st = W_PASSIVE; boolean own; Chunk c; }
    /** one window item's chunks: k[0..own) the cells' own chunks, then their 4-neighbour ring; k == null: oversized (own = chunks needed) */
    record Need(long[] k, int own) {}
    static String str(JsonObject o, String k) { return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : null; }
    static boolean air(Material m) { return m == Material.AIR || m == Material.CAVE_AIR; }
    void main(Runnable r) { if (!stopping) Bukkit.getScheduler().runTask(pl, r); }
    static String selLabel(String sel) { return sel == null ? "none" : sel.equals("auto") ? "auto (observed where imagery exists, else STYLE-GENERATED (not observed))"
            : sel.startsWith("style:") ? sel + " STYLE-GENERATED (not observed)" : "package " + sel.substring(8); }
    /** the effective selection: an explicit package/style override, else auto (when enabled) */
    String selOf(UUID u) { String v = u == null ? null : selection.get(u); return v != null ? v : autoEnabled ? "auto" : null; }
    /** chat label of an automatic plan's mode: observed vs approximated */
    static String modeLabel(String mode) { return mode == null ? "auto" : switch (mode) {
        case "evidence" -> "auto: evidence (observed)";
        case "procedural" -> "auto: procedural, STYLE-GENERATED (not observed)";
        case "inferred" -> "auto: model-INFERRED (vision model, uncalibrated, not observed)";
        case "assisted" -> "auto: ASSISTED (manual corner alignment, not automatic)";
        default -> "auto: mixed (observed + STYLE-GENERATED/inferred, not observed)"; }; }

    // ---------------------------------------------------------------- preparation (one worker; identical requests share one process; timeout)
    final class Prep {
        final List<String> args; final String key; final Set<String> waiters = ConcurrentHashMap.newKeySet(); final long enq = System.currentTimeMillis(); volatile long started;
        volatile Process proc; volatile boolean cancelled, running; final CompletableFuture<JsonObject> done = new CompletableFuture<>();
        Prep(List<String> args, String waiter) { this.args = args; this.key = String.join(" ", args); waiters.add(waiter); }
        void cancel() { cancelled = true; queue.remove(this); EggTool.kill(proc); preps.remove(key, this); done.complete(fail("cancelled")); }
    }
    static JsonObject fail(String why) { JsonObject o = new JsonObject(); o.addProperty("status", "provider_failure"); JsonArray r = new JsonArray(); r.add(why); o.add("reasons", r); return o; }
    /** waiter = the job id (or "inspect:<uuid>") that needs this result; identical requests share one process */
    synchronized CompletableFuture<JsonObject> prep(String waiter, String... args) {
        List<String> a = List.of(args); Prep t = preps.get(String.join(" ", a));
        if (t != null && !t.cancelled) { t.waiters.add(waiter); return t.done; }
        Prep n = new Prep(a, waiter); preps.put(n.key, n); queue.add(n); return n.done;
    }
    private void work() {
        while (!stopping) {
            Prep t; try { t = queue.take(); } catch (InterruptedException e) { return; }
            if (t.cancelled) continue;
            t.running = true; t.started = System.currentTimeMillis(); JsonObject res;
            File logf = new File(fdir, "prep-logs" + File.separator + t.key.replaceAll("[^A-Za-z0-9._-]+", "_") + ".log");
            try {
                if (!new File(prepScript).isFile()) res = fail("facade-prep-script not found: " + prepScript);
                else {
                    List<String> cmd = new ArrayList<>(List.of(pl.prepPython, prepScript, pl.cacheDir.getPath())); cmd.addAll(t.args);
                    logf.getParentFile().mkdirs();
                    ProcessBuilder pb = new ProcessBuilder(cmd).redirectErrorStream(true).redirectOutput(logf);
                    pb.environment().put("MORIA_FACADE_PACKAGES", packagesDir.getPath()); pb.environment().putAll(autoEnv);
                    t.proc = pb.start();
                    if (!t.proc.waitFor(timeoutS, TimeUnit.SECONDS)) { EggTool.kill(t.proc); res = fail("provider/timeout failure: facade_prep exceeded " + timeoutS + " s and was stopped");
                        pl.getLogger().warning("facade prep '" + t.key + "' timed out after " + timeoutS + " s"); }
                    else if (t.cancelled) res = fail("cancelled");
                    else { res = statusLine(logf); if (res == null) res = fail("facade_prep printed no JSON status line (exit " + t.proc.exitValue() + ", see " + logf + ")"); }
                }
            } catch (InterruptedException ie) { EggTool.kill(t.proc); t.done.complete(fail("stopping")); return; }
            catch (Exception ex) { res = fail("preparation could not start: " + ex); }
            finally { preps.remove(t.key, t); }
            res.addProperty("_queue_s", (t.started - t.enq) / 1000.0); res.addProperty("_prep_s", (System.currentTimeMillis() - t.started) / 1000.0); res.addProperty("_log", logf.getPath());
            t.done.complete(res);
        }
    }
    /** the LAST stdout line that is a JSON object with a "status" */
    JsonObject statusLine(File f) {
        try { List<String> ls = Files.readAllLines(f.toPath(), StandardCharsets.UTF_8);
            for (int i = ls.size() - 1; i >= 0; i--) { String l = ls.get(i).trim(); if (!l.startsWith("{")) continue;
                try { JsonObject o = gson.fromJson(l, JsonObject.class); if (o != null && o.has("status")) return o; } catch (Exception ignored) {} }
        } catch (IOException ignored) {}
        return null;
    }
    CompletableFuture<JsonObject> identify(String owner, int x, int z) {
        JsonObject c = identified.get(x + "," + z); if (c != null) return CompletableFuture.completedFuture(c);
        return prep(owner, "identify", String.valueOf(x), String.valueOf(z)).thenApply(r -> {
            if ("ready".equals(str(r, "status")) && bidOf(r) != null) identified.put(x + "," + z, r); return r; });
    }
    static String bidOf(JsonObject r) { String b = str(r, "bid"); return b != null ? b : str(r, "building"); }
    static Set<String> members(JsonObject r, String bid) {
        Set<String> m = new LinkedHashSet<>(); m.add(bid);
        if (r != null && r.has("members") && r.get("members").isJsonArray()) for (JsonElement e : r.getAsJsonArray("members")) m.add(e.getAsString());
        return m;
    }

    // ---------------------------------------------------------------- plan (facade-plan-1)
    record Cell(int x, int y, int z, String block, String feature, String role, String evidence) {}
    /** a component instance (comp != null: all-or-nothing) or one plain op; chunks = its chunk keys (usually one). A view: built on demand. */
    record Unit(String comp, String kind, String face, List<Cell> cells, long[] chunks) {}
    static final class IntList { int[] a; int n; IntList() { this(64); } IntList(int cap) { a = new int[Math.max(1, cap)]; } void add(int v) { if (n == a.length) a = Arrays.copyOf(a, n * 2); a[n++] = v; } int get(int i) { return a[i]; } int[] trim() { return Arrays.copyOf(a, n); } }
    /** open-addressing set of positions (no boxing: a whole-resort plan holds ~200k of them) */
    static final class LongSet {
        static final long EMPTY = Long.MIN_VALUE; long[] t = new long[16]; int n;
        LongSet() { Arrays.fill(t, EMPTY); }
        static int mix(long v) { v *= 0x9E3779B97F4A7C15L; return (int) (v ^ (v >>> 29)); }
        boolean add(long v) {
            if (2 * (n + 1) > t.length) { long[] o = t; t = new long[o.length * 2]; Arrays.fill(t, EMPTY); n = 0; for (long q : o) if (q != EMPTY) add(q); }
            int m = t.length - 1, i = mix(v) & m;
            while (t[i] != EMPTY) { if (t[i] == v) return false; i = (i + 1) & m; }
            t[i] = v; n++; return true;
        }
        boolean contains(long v) { int m = t.length - 1, i = mix(v) & m; while (t[i] != EMPTY) { if (t[i] == v) return true; i = (i + 1) & m; } return false; }
        int size() { return n; }
        long[] toArray() { long[] o = new long[n]; int k = 0; for (long q : t) if (q != EMPTY) o[k++] = q; return o; }
    }
    static final class Plan {
        String path, key, bid, status; JsonObject head = new JsonObject(); JsonArray reasons = new JsonArray(); Set<String> members = new LinkedHashSet<>();
        final Set<Long> chunks = new LinkedHashSet<>(); final LongSet positions = new LongSet();
        // flat storage (a whole-resort plan is ~200k cells: a few large arrays instead of ~1M small objects the young GC would copy)
        String[] strs = new String[0]; int[] cx = new int[0], cy = cx, cz = cx, cb = cx, cf = cx, cr = cx, ce = cx;   // cells: coords + string-table indices
        int nUnits; int[] uStart = new int[0], uLen = uStart, uComp = uStart, uKind = uStart, uFace = uStart;            // units: cell range + component strings (-1 = none)
        String str0(int i) { return i < 0 ? null : strs[i]; }
        Cell cell(int i) { return new Cell(cx[i], cy[i], cz[i], strs[cb[i]], strs[cf[i]], strs[cr[i]], strs[ce[i]]); }
        Unit unit(int u) {
            int s0 = uStart[u], n = uLen[u];
            List<Cell> cs = new AbstractList<>() { public Cell get(int k) { return cell(s0 + k); } public int size() { return n; } };
            long[] ch = new long[n]; int m = 0;
            for (int k = s0; k < s0 + n; k++) { long q = key(cx[k] >> 4, cz[k] >> 4); boolean seen = false; for (int j = 0; j < m; j++) if (ch[j] == q) { seen = true; break; } if (!seen) ch[m++] = q; }
            return new Unit(str0(uComp[u]), str0(uKind[u]), str0(uFace[u]), cs, Arrays.copyOf(ch, m));
        }
        final List<double[]> env = new ArrayList<>(), holes = new ArrayList<>(); int ylo = Integer.MIN_VALUE, yhi = Integer.MAX_VALUE; int ops, dropped; final List<String> errors = new ArrayList<>();
        int bx0, bz0, bw, bh; BitSet envBits, stripBits;                  // rasters over the envelope's box (built off the main thread)
        /** the compiler's rule (facade_compile: contains_xy(envelope, x+0.5, z+0.5) and ylo <= y <= yhi): the cell centre inside
         *  polygon_mc or any parts_mc piece, and not inside any holes_mc ring. The compiler's envelope already includes the allowed wall cells, so no tolerance. */
        boolean inEnvelope(int x, int y, int z) { return y >= ylo && y <= yhi && inEnvelopeXZ(x, z); }
        boolean inBox(int x, int z) { return x >= bx0 && z >= bz0 && x < bx0 + bw && z < bz0 + bh; }
        boolean inEnvelopeXZ(int x, int z) {
            if (envBits != null) return inBox(x, z) && envBits.get((z - bz0) * bw + (x - bx0));
            for (double[] h : holes) if (ShellToolPlugin.inPoly(h, x + 0.5, z + 0.5)) return false;     // holes_mc: enclosed neighbours / courtyards
            for (double[] e : env) if (ShellToolPlugin.inPoly(e, x + 0.5, z + 0.5)) return true;
            return false;
        }
        /** scanline rasters with exactly inPoly's even-odd rule at cell centres; plugin null = no Strip raster */
        void raster(ShellToolPlugin plg) {
            if (env.isEmpty()) return;
            double mnx = Double.MAX_VALUE, mnz = Double.MAX_VALUE, mxx = -Double.MAX_VALUE, mxz = -Double.MAX_VALUE;
            for (double[] p : env) for (int i = 0; i < p.length; i += 2) { mnx = Math.min(mnx, p[i]); mxx = Math.max(mxx, p[i]); mnz = Math.min(mnz, p[i + 1]); mxz = Math.max(mxz, p[i + 1]); }
            int x0 = (int) Math.floor(mnx) - 1, z0 = (int) Math.floor(mnz) - 1, w0 = (int) Math.ceil(mxx) - x0 + 2, h0 = (int) Math.ceil(mxz) - z0 + 2;
            if ((long) w0 * h0 > 16_000_000L) return;                        // absurd box: keep the exact per-cell test
            bx0 = x0; bz0 = z0; bw = w0; bh = h0;
            BitSet in = new BitSet(bw * bh), out = new BitSet(bw * bh);
            for (double[] p : env) fill(p, in); for (double[] h : holes) fill(h, out); in.andNot(out);
            if (plg != null) { BitSet st = new BitSet(bw * bh);
                for (int cx = bx0 >> 4; cx <= (bx0 + bw - 1) >> 4; cx++) for (int cz = bz0 >> 4; cz <= (bz0 + bh - 1) >> 4; cz++) {
                    if (!plg.chunkInStrip(cx, cz)) continue;
                    for (int x = Math.max(cx << 4, bx0); x <= Math.min((cx << 4) + 15, bx0 + bw - 1); x++)
                        for (int z = Math.max(cz << 4, bz0); z <= Math.min((cz << 4) + 15, bz0 + bh - 1); z++) if (plg.inStrip(x, z)) st.set((z - bz0) * bw + (x - bx0)); }
                stripBits = st; }
            envBits = in;
        }
        private void fill(double[] p, BitSet b) {
            int n = p.length / 2; double[] xs = new double[n];
            for (int r = 0; r < bh; r++) {
                double zc = bz0 + r + 0.5; int k = 0;
                for (int i = 0, j = n - 1; i < n; j = i++) { double zi = p[2 * i + 1], zj = p[2 * j + 1];
                    if ((zi > zc) != (zj > zc)) xs[k++] = (p[2 * j] - p[2 * i]) * (zc - zi) / (zj - zi) + p[2 * i]; }
                Arrays.sort(xs, 0, k);
                for (int q = 0; q + 1 < k; q += 2) {                          // centre x+0.5 strictly between a crossing pair
                    int a = Math.max((int) Math.floor(xs[q] - 0.5) + 1, bx0), c = Math.min((int) Math.ceil(xs[q + 1] - 0.5) - 1, bx0 + bw - 1);
                    if (a <= c) b.set(r * bw + (a - bx0), r * bw + (c - bx0) + 1);
                }
            }
        }
        int maxOpsException() { JsonObject l = head.has("limits") && head.get("limits").isJsonObject() ? head.getAsJsonObject("limits") : null;
            if (l == null || !l.has("exception") || l.get("exception").isJsonNull() || !l.has("max_ops")) return -1;
            return Math.min(400_000, l.get("max_ops").getAsInt()); }
        boolean style() { JsonObject s = head.has("selection") && head.get("selection").isJsonObject() ? head.getAsJsonObject("selection") : null; return s != null && "style".equals(str(s, "kind")); }
    }
    static double[] poly(JsonArray a) {
        double[] p = new double[2 * a.size()];
        for (int i = 0; i < a.size(); i++) { p[2 * i] = a.get(i).getAsJsonArray().get(0).getAsDouble(); p[2 * i + 1] = a.get(i).getAsJsonArray().get(1).getAsDouble(); }
        return p;
    }
    /** async: the plan named by a prep status line (path, or plan_key under buildings/<bid>/), parsed once per file version.
     *  Streamed: the ops array (up to 400k) is never held as a JSON tree or one big string, and repeated strings are shared, so loading a
     *  whole-resort plan does not flood the young generation (long GC pauses stop the server thread too). */
    Plan loadPlan(JsonObject line, String bid) throws IOException {
        String p = str(line, "plan"); if (p == null) p = str(line, "plan_path");
        File f = p != null ? (new File(p).isAbsolute() ? new File(p) : new File(pl.cacheDir, p).isFile() ? new File(pl.cacheDir, p) : new File(new File(fdir, "buildings" + File.separator + bid), p))
                : str(line, "plan_key") != null ? new File(fdir, "buildings" + File.separator + bid + File.separator + "plan-" + str(line, "plan_key") + ".json") : null;
        if (f == null || !f.isFile()) return null;
        String ck = f.getPath() + "@" + f.lastModified() + "/" + f.length(); Plan c = planCache.get(ck); if (c != null) return c;
        Plan pl0 = new Plan(); pl0.path = f.getPath(); String fn = f.getName(); JsonObject o = pl0.head;
        Map<String, Integer> sidx = new HashMap<>(); List<String> stab = new ArrayList<>(); int[] bad = {0};
        java.util.function.ToIntFunction<String> si = q -> sidx.computeIfAbsent(q, k -> { stab.add(k); return stab.size() - 1; });
        IntList rx = new IntList(), ry = new IntList(), rz = new IntList(), rb = new IntList(), rf = new IntList(), rr = new IntList(), re = new IntList(), rc = new IntList();
        try (JsonReader r = new JsonReader(new BufferedReader(new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8), 1 << 16))) {
            r.beginObject();
            while (r.hasNext()) {
                String name = r.nextName();
                if (!name.equals("ops")) { o.add(name, JsonParser.parseReader(r)); continue; }
                r.beginArray(); List<String> v = new ArrayList<>(10);
                while (r.hasNext()) {
                    v.clear(); r.beginArray();
                    while (r.hasNext()) { JsonToken tk = r.peek(); if (tk == JsonToken.NULL) { r.nextNull(); v.add(null); } else if (tk == JsonToken.STRING || tk == JsonToken.NUMBER) v.add(r.nextString()); else { r.skipValue(); v.add(null); } }
                    r.endArray(); pl0.ops++;
                    try {
                        if (v.size() < 7 || !"replace".equals(v.get(3)) || v.get(4) == null || v.get(5) == null) { bad[0]++; continue; }
                        int x = Integer.parseInt(v.get(0)), y = Integer.parseInt(v.get(1)), z = Integer.parseInt(v.get(2));
                        rx.add(x); ry.add(y); rz.add(z); rb.add(si.applyAsInt(v.get(4))); rf.add(si.applyAsInt(v.get(5))); rr.add(si.applyAsInt(Objects.toString(v.get(6), "")));
                        re.add(si.applyAsInt(v.size() > 8 && v.get(8) != null ? v.get(8) : "unknown")); rc.add(v.size() > 7 && v.get(7) != null ? si.applyAsInt(v.get(7)) : -1);
                    } catch (NumberFormatException ex) { bad[0]++; }
                }
                r.endArray();
            }
            r.endObject();
        }
        pl0.key = str(line, "plan_key") != null ? str(line, "plan_key") : fn.startsWith("plan-") && fn.endsWith(".json") ? fn.substring(5, fn.length() - 5) : fn;
        if (str(o, "plan_key") != null) pl0.key = str(o, "plan_key");
        if (!"facade-plan-1".equals(str(o, "version"))) throw new IOException("plan version '" + str(o, "version") + "' is not facade-plan-1");
        pl0.bid = Objects.toString(str(o, "building"), bid); pl0.status = Objects.toString(str(o, "status"), "provider_failure"); pl0.reasons = o.has("reasons") && o.get("reasons").isJsonArray() ? o.getAsJsonArray("reasons") : new JsonArray();
        pl0.members.add(pl0.bid); if (o.has("members")) for (JsonElement e : o.getAsJsonArray("members")) pl0.members.add(e.getAsString());
        if (o.has("envelope") && o.get("envelope").isJsonObject()) { JsonObject e = o.getAsJsonObject("envelope");
            if (e.has("polygon_mc")) pl0.env.add(poly(e.getAsJsonArray("polygon_mc")));
            if (e.has("parts_mc")) for (JsonElement q : e.getAsJsonArray("parts_mc")) pl0.env.add(poly(q.getAsJsonArray()));
            if (e.has("holes_mc")) for (JsonElement q : e.getAsJsonArray("holes_mc")) pl0.holes.add(poly(q.getAsJsonArray()));
            if (e.has("y")) { pl0.ylo = e.getAsJsonArray("y").get(0).getAsInt(); pl0.yhi = e.getAsJsonArray("y").get(1).getAsInt(); } }
        if (o.has("chunks")) for (JsonElement e : o.getAsJsonArray("chunks")) pl0.chunks.add(key(e.getAsJsonArray().get(0).getAsInt(), e.getAsJsonArray().get(1).getAsInt()));
        Map<String, JsonObject> comps = new HashMap<>();
        if (o.has("components")) for (JsonElement e : o.getAsJsonArray("components")) { JsonObject co = e.getAsJsonObject(); comps.put(str(co, "id"), co); }
        // units in op order: a plain op is one unit; a component's ops (wherever they are) form one unit at its first op
        Map<Integer, IntList> byComp = new LinkedHashMap<>(); IntList order = new IntList();   // order: raw index >= 0 (plain), -(compIdx+1) (component)
        pl0.dropped = bad[0];
        for (int i = 0; i < rx.n; i++) {
            if (!pl0.members.contains(stab.get(rf.get(i))) || !pl0.positions.add(pos(rx.get(i), ry.get(i), rz.get(i)))) { pl0.dropped++; continue; }   // foreign feature / one op per cell
            int comp = rc.get(i);
            if (comp < 0) order.add(i);
            else { IntList l = byComp.get(comp); if (l == null) { l = new IntList(); byComp.put(comp, l); order.add(-(comp + 1)); } l.add(i); }
        }
        if (pl0.dropped > 0) pl0.errors.add(pl0.dropped + " op(s) with a non-'replace' expect, a foreign feature or a duplicate cell: dropped (plan contract violation)");
        int nc = rx.n - pl0.dropped + 0; IntList ox = new IntList(), oy = new IntList(), oz = new IntList(), ob = new IntList(), of = new IntList(), or = new IntList(), oe = new IntList();
        IntList us = new IntList(), ul = new IntList(), uc = new IntList(), uk = new IntList(), uf = new IntList();
        for (int k = 0; k < order.n; k++) {
            int e = order.get(k); IntList cells; int comp;
            if (e >= 0) { cells = new IntList(); cells.add(e); comp = -1; } else { comp = -e - 1; cells = byComp.get(comp); }
            us.add(ox.n); ul.add(cells.n); uc.add(comp);
            JsonObject co = comp < 0 ? null : comps.get(stab.get(comp));
            uk.add(co != null && str(co, "kind") != null ? si.applyAsInt(str(co, "kind")) : -1); uf.add(co != null && str(co, "face") != null ? si.applyAsInt(str(co, "face")) : -1);
            for (int j = 0; j < cells.n; j++) { int i = cells.get(j);
                ox.add(rx.get(i)); oy.add(ry.get(i)); oz.add(rz.get(i)); ob.add(rb.get(i)); of.add(rf.get(i)); or.add(rr.get(i)); oe.add(re.get(i));
                pl0.chunks.add(key(rx.get(i) >> 4, rz.get(i) >> 4)); }
        }
        pl0.cx = ox.trim(); pl0.cy = oy.trim(); pl0.cz = oz.trim(); pl0.cb = ob.trim(); pl0.cf = of.trim(); pl0.cr = or.trim(); pl0.ce = oe.trim();
        pl0.nUnits = us.n; pl0.uStart = us.trim(); pl0.uLen = ul.trim(); pl0.uComp = uc.trim(); pl0.uKind = uk.trim(); pl0.uFace = uf.trim();
        pl0.strs = stab.toArray(new String[0]);
        pl0.raster(pl.loaded ? pl : null);
        planCache.put(ck, pl0); return pl0;
    }

    // ---------------------------------------------------------------- commands
    /** who acts (a player, or a uuid via sim), where to report, and the target block (ray trace or '@ x y z') */
    record Actor(UUID u, CommandSender out, int[] target, boolean admin) {}
    private boolean say(CommandSender s, String m) { s.sendMessage("§b" + TAG + m); return true; }
    void msg(CommandSender s, String text) { main(() -> s.sendMessage(text)); }

    @EventHandler public void onJoin(PlayerJoinEvent e) { selection.remove(e.getPlayer().getUniqueId()); }

    @Override public boolean onCommand(CommandSender s, Command c, String label, String[] a) {
        if (!s.hasPermission(ShellToolPlugin.USE) && !s.hasPermission(ShellToolPlugin.ADMIN)) { s.sendMessage("§c" + TAG + "No permission."); return true; }
        boolean admin = s.hasPermission(ShellToolPlugin.ADMIN);
        try {
            if (a.length > 0 && a[0].equalsIgnoreCase("sim")) {               // sim <uuid> <sub...> @ x y z
                if (!admin) return say(s, "Admin only.");
                int at = Arrays.asList(a).indexOf("@");
                if (a.length < 3 || (at >= 0 && at + 3 >= a.length)) return say(s, "/facadetool sim <uuid> <subcommand...> [@ <x> <y> <z>]");
                int[] t = at < 0 ? null : new int[]{Integer.parseInt(a[at + 1]), Integer.parseInt(a[at + 2]), Integer.parseInt(a[at + 3])};
                String[] sub = Arrays.copyOfRange(a, 2, at < 0 ? a.length : at);
                return dispatch(new Actor(UUID.fromString(a[1]), s, t, true), sub);
            }
            UUID u = s instanceof Player p ? p.getUniqueId() : null; int[] t = null;
            if (s instanceof Player p) { Block b = p.getTargetBlockExact(160); if (b != null && b.getWorld().getName().equals(pl.worldName)) t = new int[]{b.getX(), b.getY(), b.getZ()}; }
            return dispatch(new Actor(u, s, t, admin), a);
        } catch (IllegalArgumentException ex) { return say(s, "Invalid argument: " + ex.getMessage()); }
    }
    boolean dispatch(Actor a, String[] args) {
        CommandSender s = a.out(); String sub = args.length == 0 ? "help" : args[0].toLowerCase(Locale.ROOT);
        boolean confirm = Arrays.stream(args).skip(1).anyMatch(x -> x.equalsIgnoreCase("confirm"));
        boolean whole = Arrays.stream(args).skip(1).anyMatch(x -> x.equalsIgnoreCase("whole"));
        switch (sub) {
            case "help" -> {
                say(s, "/facadetool inspect | select <auto|package|style:<id>|none> | preview [chunk|whole] [refresh] | apply [chunk] | apply whole confirm");
                say(s, "  adopt [confirm|show] | status | cancel | undo [job] [confirm]  -  target = the block you look at (<= 160 blocks)");
                say(s, "  default selection: auto" + (autoEnabled ? "" : " (DISABLED: facade-auto-enabled false)") + " - imagery-derived where available (observed), else STYLE-GENERATED (not observed) with fallback " + fallback + ".");
                if (a.admin()) say(s, "  admin: /facadetool sim <uuid> <subcommand...> @ <x> <y> <z>");
            }
            case "select" -> {
                if (a.u() == null) return say(s, "Players only (console: /facadetool sim <uuid> select ...).");
                if (args.length < 2) return say(s, "Selection: " + selLabel(selOf(a.u())) + ". Use /facadetool select <auto|package-id|style:<id>|none>");
                String v = args[1];
                if (v.equalsIgnoreCase("none") || v.equalsIgnoreCase("auto")) { selection.remove(a.u());
                    return say(s, autoEnabled ? "Selection: auto (the default): imagery-derived facade where evidence exists, else STYLE-GENERATED (not observed)."
                            : "Selection cleared (automatic facades are disabled: preview/apply need a package or style)."); }
                boolean st = v.startsWith("style:"); String id = st ? v.substring(6) : v;
                if (!id.matches("[a-z0-9][a-z0-9._-]{0,79}")) return say(s, "Invalid id '" + id + "' (lowercase letters, digits, . _ -).");
                selection.put(a.u(), (st ? "style:" : "package:") + id);
                say(s, "Selected " + selLabel(selection.get(a.u())) + (st ? ". Styles are template-generated, not observed." : "") + " (in memory until logout/restart).");
            }
            case "inspect" -> inspect(a);
            case "preview" -> submit(a, Kind.PREVIEW, whole, Arrays.stream(args).skip(1).anyMatch(x -> x.equalsIgnoreCase("refresh")));
            case "apply" -> {
                if (whole && !confirm) return say(s, "'apply whole' needs a whole preview first, then '/facadetool apply whole confirm'.");
                submit(a, Kind.APPLY, whole, confirm);
            }
            case "adopt" -> { if (args.length > 1 && args[1].equalsIgnoreCase("show")) adoptShow(a); else submit(a, Kind.ADOPT, true, confirm); }
            case "status" -> {
                if (a.u() != null) say(s, "Selection: " + selLabel(selOf(a.u())));
                StringBuilder js = new StringBuilder(); for (FJob j : jobs.values()) js.append(" [").append(j.id).append(" ").append(j.kind).append(j.whole ? " whole " : " chunk ").append(j.bid == null ? "?" : j.bid).append(" ").append(j.stage).append("]");
                say(s, "Facade " + (enabled ? "enabled" : "DISABLED") + " | jobs " + jobs.size() + "/" + maxJobs + js + " | prep queue " + queue.size() + ", running " + preps.values().stream().filter(p -> p.running).count()
                        + " | facade buildings indexed " + bidIndex.size() + " | locks held " + pl.regionLocks.values().stream().filter(v -> v.startsWith("facade:")).count());
            }
            case "cancel" -> {
                int n = 0, q = 0;
                List<String> cancelledIds = new ArrayList<>();
                for (FJob j : new ArrayList<>(jobs.values())) if (a.admin() || Objects.equals(a.u(), j.owner)) { j.cancel("cancelled by " + s.getName()); cancelledIds.add(j.id); n++; }
                Set<String> gone = new HashSet<>(cancelledIds); gone.add("inspect:" + (a.u() == null ? "console" : a.u()));
                for (Prep p : new ArrayList<>(preps.values())) { p.waiters.removeAll(gone); if (p.waiters.isEmpty()) { p.cancel(); q++; } }   // still needed by another job: kept
                say(s, n + " Facade job(s) and " + q + " preparation(s) cancelled. Written blocks stay in the ledger (undoable).");
            }
            case "undo" -> { String job = null; for (int i = 1; i < args.length; i++) if (!args[i].equalsIgnoreCase("confirm")) job = args[i];
                undo(a, job, confirm); }
            case "simfail" -> {                                                // admin test hook: atomic rollback of a component mid-write
                if (!a.admin() || args.length < 2) return say(s, "/facadetool sim <uuid> simfail <n>");
                failNext = Integer.parseInt(args[1]); say(s, "simfail: the next component with more than " + failNext + " cell(s) to write fails at write #" + failNext + " (one shot).");
            }
            default -> say(s, "Unknown subcommand. /facadetool help");
        }
        return true;
    }
    void inspect(Actor a) {
        CommandSender s = a.out();
        if (!enabled) { say(s, "The Facade tool is disabled (facade-enabled: false)."); return; }
        if (a.target() == null) { say(s, "Look at a block of the building (within 160 blocks)."); return; }
        int x = a.target()[0], z = a.target()[2];
        say(s, "Identifying the building at " + x + " " + a.target()[1] + " " + z + " ...");
        identify("inspect:" + (a.u() == null ? "console" : a.u()), x, z).thenAccept(r -> main(() -> {
            String st = str(r, "status"), bid = bidOf(r);
            if (!"ready".equals(st) || bid == null) { say(s, "Inspect " + x + " " + z + ": " + st + (r.has("reasons") ? " - " + ShellToolPlugin.shortWhy(r.get("reasons").toString()) : "")); return; }
            Set<String> mem = members(r, bid); int liveN = 0; for (String m : mem) { LongSet q = bidIndex.get(m); if (q != null) liveN += q.size(); }
            say(s, "Building " + bid + (mem.size() > 1 ? " (" + mem.size() + " members)" : "") + (str(r, "name") != null ? " \"" + str(r, "name") + "\"" : "")
                    + " | parts " + (r.has("parts") ? r.get("parts").toString() : "?") + " | packages " + (r.has("packages") ? r.get("packages").toString() : "[]")
                    + " | styles " + (r.has("styles") ? r.get("styles").toString() : "[]"));
            say(s, "  evidence " + (r.has("evidence") ? ShellToolPlugin.shortWhy(r.get("evidence").toString()) : "?") + " | facade cells recorded " + liveN
                    + " | your selection " + (a.u() == null ? "-" : selLabel(selOf(a.u()))));
        }));
    }

    /** /facadetool undo: same rules as the shared undo (owner/admin, newest-first dependents, locked chunks, after-state only), but restored
     *  under the tick budget as a Facade job (holding its chunks locked while it writes) */
    void undo(Actor a, String jobArg, boolean confirm) {
        CommandSender s = a.out(); String owner = s instanceof Player p ? p.getUniqueId().toString() : null; String job = jobArg;
        if (job == null) for (String j : pl.ledgerJobs.keySet()) if (!pl.undone.contains(j) && pl.facadeJobs.contains(j) && (owner == null || owner.equals(pl.jobOwner.get(j)))) job = j;
        if (job == null || !pl.ledgerJobs.containsKey(job) || !pl.facadeJobs.contains(job)) { say(s, "No undoable job" + (jobArg == null ? "" : " '" + jobArg + "'") + "."); return; }
        if (owner != null && !owner.equals(pl.jobOwner.get(job)) && !s.hasPermission(ShellToolPlugin.ADMIN)) { say(s, "That job is not yours."); return; }
        if (pl.undone.contains(job)) { say(s, "Job " + job + " was already undone."); return; }
        List<String> live = new ArrayList<>(); for (String d : pl.dependents.getOrDefault(job, Set.of())) if (!pl.undone.contains(d)) live.add(d);
        if (!live.isEmpty()) { say(s, "Job " + job + " has later detail updates on its blocks (job(s) " + String.join(", ", live) + "). Undo those first, newest first; nothing changed."); return; }
        List<Placement> ps = pl.ledgerJobs.get(job); Set<Long> ch = new LinkedHashSet<>(); for (Placement p : ps) ch.add(key(p.x() >> 4, p.z() >> 4));
        for (long k : ch) if (pl.activeChunks.containsKey(k) || pl.regionLocks.containsKey(k)) {
            say(s, "A job is running in that chunk" + (pl.regionLocks.containsKey(k) ? " (" + pl.lockHolder(k) + ")" : "") + "; try again after it finishes."); return; }
        if (jobs.containsKey(job)) { say(s, "Job " + job + " is still running."); return; }
        if (jobs.size() >= maxJobs) { say(s, "Busy: " + jobs.size() + "/" + maxJobs + " Facade job(s) running (" + jobs.keySet() + ")."); return; }
        Placement p0 = ps.get(0); World w = Bukkit.getWorld(p0.world());
        String id = String.format("%tY%<tm%<td-%<tH%<tM%<tS-f%d", new Date(), pl.seq.incrementAndGet());
        FJob j = new FJob(id, a, w, p0.x(), p0.y(), p0.z(), Kind.UNDO, false, confirm, null); jobs.put(id, j); monitorStart(id);
        j.undoOf = job; j.ups = ShellToolPlugin.reversed(ps); j.scope.addAll(ch); j.status = "ready"; j.bid = p0.feature();   // newest first
        for (Placement p : ps) { j.members.add(p.feature()); j.undoPos.add(pos(p.x(), p.y(), p.z())); }                    // this building's own live cells are not "attached work"
        event(j, "queued");
        j.safe(() -> { if (confirm && !j.lock()) return; j.load(); });
    }
    /** /facadetool adopt show: the requester's latest dry-run cells as client-side particles (only that player sees them) for 20 s, a flash
     *  every 0.5 s of at most 1000 cells (a rotating sample when there are more), each just outside an exposed face; main thread, loaded chunks only */
    void adoptShow(Actor a) {
        CommandSender s = a.out();
        if (!(s instanceof Player p)) { say(s, "Players only."); return; }
        AdoptDry d = adoptDry.get(p.getUniqueId().toString());
        if (d == null || d.cells().length == 0) { say(s, "No adopt dry run with cells to show: run '/facadetool adopt' first."); return; }
        BukkitTask old = shows.remove(p.getUniqueId()); if (old != null) old.cancel();
        long[] c = d.cells(); int per = Math.min(c.length, 1000); int[] run = {0};
        org.bukkit.Particle.DustOptions dust = new org.bukkit.Particle.DustOptions(org.bukkit.Color.AQUA, 1.3f);
        shows.put(p.getUniqueId(), Bukkit.getScheduler().runTaskTimer(pl, () -> {
            if (!p.isOnline() || !p.getWorld().getName().equals(d.world()) || run[0] >= 40) { BukkitTask t = shows.remove(p.getUniqueId()); if (t != null) t.cancel(); return; }
            World w = p.getWorld(); int off = run[0]++ * per;
            for (int i = 0; i < per; i++) { long q = c[(off + i) % c.length]; int x = px(q), y = py(q), z = pz(q);
                if (!w.isChunkLoaded(x >> 4, z >> 4)) continue;
                double ox = 0.5, oy = 0.5, oz = 0.5;
                for (BlockFace f : FACES) { int nx = x + f.getModX(), nz = z + f.getModZ();
                    if (w.isChunkLoaded(nx >> 4, nz >> 4) && !w.getBlockAt(nx, y + f.getModY(), nz).getType().isOccluding()) { ox += f.getModX() * 0.6; oy += f.getModY() * 0.6; oz += f.getModZ() * 0.6; break; } }
                p.spawnParticle(org.bukkit.Particle.REDSTONE, x + ox, y + oy, z + oz, 1, 0, 0, 0, 0, dust);
            }
        }, 0, 10));
        say(s, "Outlining your last adopt dry run's " + c.length + " cell(s) for 20 s (only you see it" + (c.length > per ? "; " + per + " sampled per flash" : "") + ").");
    }
    void submit(Actor a, Kind kind, boolean whole, boolean confirm) {
        CommandSender s = a.out();
        if (!enabled) { say(s, "The Facade tool is disabled (facade-enabled: false)."); return; }
        if (!pl.loaded) { say(s, "Data not loaded yet (Strip exclusion unknown) - refused."); return; }
        if (a.target() == null) { say(s, "Look at a block of the building (within 160 blocks), or use sim ... @ x y z."); return; }
        String sel = a.u() == null ? (autoEnabled ? "auto" : null) : selOf(a.u());
        if (kind != Kind.ADOPT && sel == null) { say(s, "Nothing selected (automatic facades are disabled): /facadetool select <package-id|style:<id>> first."); return; }
        if (jobs.size() >= maxJobs) { say(s, "Busy: " + jobs.size() + "/" + maxJobs + " Facade job(s) running (" + jobs.keySet() + ")."); return; }
        World w = Bukkit.getWorld(pl.worldName); int[] t = a.target();
        String id = String.format("%tY%<tm%<td-%<tH%<tM%<tS-f%d", new Date(), pl.seq.incrementAndGet());
        FJob j = new FJob(id, a, w, t[0], t[1], t[2], kind, whole, confirm, sel); jobs.put(id, j); monitorStart(id);
        if (pl.chunkInStrip(j.cx, j.cz)) { j.status = "protected"; j.reasons.add("chunk " + j.cx + "," + j.cz + " is inside the Strip exclusion"); j.finish(); return; }
        say(s, "Job " + id + " " + j.what() + ": target " + t[0] + " " + t[1] + " " + t[2] + " (chunk " + j.cx + "," + j.cz + ")" + (sel != null && kind != Kind.ADOPT ? " | " + selLabel(sel) : ""));
        event(j, "queued");
        j.run();
    }

    // ---------------------------------------------------------------- job
    final class FJob {
        final String id; final UUID owner; final CommandSender who; final World w; final int ix, iy, iz, cx, cz; final Kind kind; final boolean whole, confirm; final String sel;
        volatile String stage = "identifying", status = "provider_failure", bid; volatile boolean done, cancelled;
        final List<String> reasons = new CopyOnWriteArrayList<>(), notes = new CopyOnWriteArrayList<>(), examples = new ArrayList<>();
        Set<String> members = new LinkedHashSet<>(); volatile Plan plan; volatile JsonObject idLine, planLine;
        final Set<Long> scope = new LinkedHashSet<>(), locked = new HashSet<>();
        /** sliding chunk window (main thread): items gi..pf-1 of the ordered stream are admitted (their Needs in wq); win = every chunk they
         *  need + loads still in flight (win.size() <= maxLoaded always; peak reported); free = chunks whose last window item finished */
        final LinkedHashMap<Long, WC> win = new LinkedHashMap<>(); final ArrayDeque<Need> wq = new ArrayDeque<>(); final List<Long> free = new ArrayList<>();
        final Set<Long> ungen = new HashSet<>(); int gi, pf, loading, peak, fwait; Need nx;
        final IntList work = new IntList(); final List<Object[]> retire = new ArrayList<>(); int idx, ridx, stall;
        int phase, si; long[] snap = new long[0]; final LongSet seenR = new LongSet(); Pend pend;
        final Map<Long, Boolean> envCol = new HashMap<>(), stripCol = new HashMap<>();        // per column, for the job
        final Map<Long, List<Object[]>> entCache = new HashMap<>(); final Map<Long, Set<Long>> tileCache = new HashMap<>();   // per chunk, for one tick
        long maxPlaceTickNs, maxStepNs, stepStart, maxStepExGcNs, maxGcInStepNs;
        Map<Long, Boolean> diskFull = Map.of(); final Set<Long> edge = new HashSet<>(), requested = new HashSet<>();   // chunks FULL on disk (load phase); scope chunks never loaded (edge)
        final LongSet undoPos = new LongSet();
        String undoOf; List<Placement> ups = List.of(); int restorable, restored, modified, notLoaded, keptAttached;   // UNDO
        int placed, present, upgraded, skippedCells, deferredCells, compPlaced, compSkipped, compDeferred, retired, retireRefused, obsoleteOutside, inScopeOps;
        final Map<String, Integer> skippedBy = new TreeMap<>(), deferredBy = new TreeMap<>();
        final Map<String, int[]> byEvidence = new TreeMap<>();                // evidence -> placed|would, present, skipped, deferred
        // adoption
        List<Object[]> cands = List.of(); String candSource, candSha, adoptKey; int adopted, alreadyOwned; final Map<String, Integer> ambiguousBy = new TreeMap<>();
        final Map<String, Integer> adoptByBlock = new HashMap<>(), adoptBySource = new HashMap<>(); final LongSet adoptSet = new LongSet(); LongSet dry; int dryN;
        final int[] box = {Integer.MAX_VALUE, Integer.MAX_VALUE, Integer.MAX_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE}; final List<String> adoptEx = new ArrayList<>();
        final StringBuilder csv = new StringBuilder("x,y,z,block,face_or_part,source,verdict\n");   // every candidate's verdict (adopt | already_owned | refused:<reason>)
        final long t0 = System.currentTimeMillis(); volatile double idPrepS, planPrepS, queueS; long loadMs, placeNs, maxTickNs; BukkitTask task;
        volatile String autoMode; volatile BukkitTask progress;           // automatic facades: plan mode, auto-progress.json poller (async timer)
        FJob(String id, Actor a, World w, int x, int y, int z, Kind kind, boolean whole, boolean confirm, String sel) {
            this.id = id; this.owner = a.u(); this.who = a.out(); this.w = w; this.ix = x; this.iy = y; this.iz = z; this.cx = x >> 4; this.cz = z >> 4;
            this.kind = kind; this.whole = whole; this.confirm = confirm; this.sel = sel;
        }
        boolean writes() { return kind == Kind.APPLY || ((kind == Kind.ADOPT || kind == Kind.UNDO) && confirm); }
        String what() { return kind == Kind.ADOPT ? (confirm ? "ADOPT" : "ADOPT DRY RUN") : kind == Kind.UNDO ? (confirm ? "UNDO" : "UNDO DRY RUN") : kind + (whole ? " whole" : " chunk"); }
        boolean noSel() { return kind == Kind.ADOPT || kind == Kind.UNDO; }
        /** an undo that did not process every cell (kept for attached work, never loaded, or cancelled) leaves the job live */
        boolean partial() { return cancelled || keptAttached > 0 || notLoaded > 0; }
        String ownerKey() { return owner != null ? owner.toString() : "console"; }
        int[] ev(String e) { return byEvidence.computeIfAbsent(e, k -> new int[4]); }

        /** async chain: identify -> (plan | adoption candidates) -> main thread: scope, limits, locks, chunks, placer */
        void run() {
            identify(id, ix, iz).whenComplete((r0, ex) -> safe(() -> {
                if (done) return;
                JsonObject r = ex != null || r0 == null ? fail("identify failed: " + ex) : r0;
                idLine = r; idPrepS = r.has("_prep_s") ? r.get("_prep_s").getAsDouble() : 0;
                String st = Objects.toString(str(r, "status"), "provider_failure"); bid = bidOf(r);
                if (!st.equals("ready") || bid == null) { status = STATUSES.contains(st) ? st : "provider_failure"; addReasons(r); if (bid == null && st.equals("ready")) reasons.add("identify returned no building id"); main(this::finish); return; }
                members = members(r, bid);
                if (kind == Kind.ADOPT) { loadCandidates(false); return; }
                stage = "planning";
                CompletableFuture<JsonObject> pf;
                if (sel.equals("auto")) {
                    List<String> pa = new ArrayList<>(List.of("auto", bid)); for (String m : members) if (!m.equals(bid)) { pa.add("--member"); pa.add(m); }
                    pa.addAll(List.of("--fallback", fallback, "--fallback-style", fallbackStyle, "--seed", String.valueOf(seed))); if (kind == Kind.PREVIEW && confirm) pa.add("--refresh");
                    msg(who, "§b" + TAG + "Automatic facade for " + bid + ": resolving -> fetching evidence -> analyzing -> compiling (off the server thread; progress follows)");
                    progress = pollProgress(this, new File(fdir, "buildings" + File.separator + bid + File.separator + "auto-progress.json"));
                    pf = prep(id, pa.toArray(new String[0]));
                } else { String[] sa = sel.split(":", 2);
                    pf = prep(id, "plan", bid, sa[0].equals("style") ? "--style" : "--package", sa[1], "--seed", String.valueOf(seed)); }
                pf.whenComplete((pr0, ex2) -> safe(() -> {
                    if (progress != null) progress.cancel();
                    if (done) return;
                    JsonObject pr = ex2 != null || pr0 == null ? fail("plan failed: " + ex2) : pr0;
                    planLine = pr; planPrepS = pr.has("_prep_s") ? pr.get("_prep_s").getAsDouble() : 0; queueS = pr.has("_queue_s") ? pr.get("_queue_s").getAsDouble() : 0;
                    String ps = Objects.toString(str(pr, "status"), "provider_failure"); status = STATUSES.contains(ps) ? ps : "provider_failure"; addReasons(pr);
                    try { plan = loadPlan(pr, bid); } catch (Exception e) { status = "provider_failure"; reasons.add("plan unreadable: " + e.getMessage()); }
                    if (plan != null) { status = STATUSES.contains(plan.status) ? plan.status : "provider_failure"; reasons.clear(); for (JsonElement e : plan.reasons) reasons.add(e.getAsString());
                        members.addAll(plan.members); notes.addAll(plan.errors); }
                    else if (status.equals("ready")) { status = "provider_failure"; reasons.add("prep reported ready but no plan file was found (" + pr + ")"); }
                    if (sel.equals("auto")) autoSummary(this, pr);
                    main(() -> safe(this::scopeAndStart));
                }));
            }));
        }
        /** any exception in an async or main-thread step ends the job (provider_failure): locks released, report written */
        void safe(Runnable r) {
            boolean mainT = Bukkit.isPrimaryThread(); long t0 = System.nanoTime(), g0 = mainT ? gcMillis() : 0; if (mainT) stepStart = t0;
            try { r.run(); }
            catch (Throwable t) {
                pl.getLogger().warning("facade job " + id + " failed: " + t); status = "provider_failure"; reasons.add("internal error: " + t);
                if (Bukkit.isPrimaryThread()) finish(); else Bukkit.getScheduler().runTask(pl, this::finish);
            }
            finally { if (mainT) { long d = System.nanoTime() - t0, gc = (gcMillis() - g0) * 1_000_000L; maxStepNs = Math.max(maxStepNs, d); stepStart = 0;
                maxStepExGcNs = Math.max(maxStepExGcNs, Math.max(0, d - gc)); maxGcInStepNs = Math.max(maxGcInStepNs, gc);
                if (d > 3 * (long) (msPerTick * 1e6)) pl.getLogger().info("facade job " + id + " slow main-thread step " + Math.round(d / 1e5) / 10.0 + " ms (of which GC " + gc / 1_000_000 + " ms; stage " + stage + ", phase " + phase + ")"); } }
        }
        void addReasons(JsonObject r) { if (r.has("reasons") && r.get("reasons").isJsonArray()) for (JsonElement e : r.getAsJsonArray("reasons")) reasons.add(e.isJsonPrimitive() ? e.getAsString() : e.toString()); }

        /** main thread: start the time-budgeted job task. Scope, clipping, gates and the retirement scan run in its ticks (setup phases),
         *  then locks, then placement through a sliding chunk window (at most facade-max-loaded-chunks held). No main-thread step is unbounded. */
        void scopeAndStart() {
            if (done) return;
            if (!status.equals("ready")) { finish(); return; }
            stage = "scoping";
            if (whole) scope.addAll(plan.chunks); else scope.add(key(cx, cz));
            snap = snapshot(); phase = whole ? P_SCOPE : P_CLIP; si = 0;
            task = Bukkit.getScheduler().runTaskTimer(pl, () -> safe(this::tick), 1, 1);
        }
        /** positions of this building's facade placements (copied once: the index may change while the setup phases run) */
        long[] snapshot() {
            long[] a = new long[0];
            for (String m : members) { LongSet q = bidIndex.get(m); if (q == null) continue; long[] b0 = q.toArray(); long[] c0 = Arrays.copyOf(a, a.length + b0.length); System.arraycopy(b0, 0, c0, a.length, b0.length); a = c0; }
            return a;
        }
        /** setup phases, each item checked against the tick deadline */
        void setup(long end) {
            while (System.nanoTime() < end) {
                if (phase == P_SCOPE) { if (si >= snap.length) { phase = P_CLIP; si = 0; continue; } long q = snap[si++]; scope.add(key(px(q) >> 4, pz(q) >> 4)); }
                else if (phase == P_CLIP) { if (si >= plan.nUnits) { if (!gates()) return; phase = P_RSCAN; si = 0; continue; } clip(si++); }
                else if (phase == P_RSCAN) { if (si >= snap.length) { afterScan(); return; } scanRetire(snap[si++]); }
                else return;
            }
        }
        void clip(int ui) {
            Unit u = plan.unit(ui);
            int in = 0; for (long k : u.chunks()) if (scope.contains(k)) in++;
            if (in == u.chunks().length) { work.add(ui); inScopeOps += u.cells().size(); return; }
            deferredCells += u.cells().size(); if (u.comp() != null) compDeferred++;
            String why = u.comp() == null ? "outside scope (chunk)" : in == 0 ? "component outside scope" : "component crosses the scope border (all cells must be in scope)";
            deferredBy.merge(why, u.cells().size(), Integer::sum); for (Cell c0 : u.cells()) ev(c0.evidence())[3]++;
        }
        /** limits and the whole-scope preview gate; false = the job ended */
        boolean gates() {
            Plan p = plan; int cap = whole ? Math.max(maxOpsWhole, p.maxOpsException()) : maxOpsChunk;
            if (inScopeOps > cap) { status = "refused_limits"; reasons.add(inScopeOps + " ops in scope > " + (whole ? "facade-max-ops-whole " + maxOpsWhole + (p.maxOpsException() > 0 ? " / package exception " + p.maxOpsException() : "") : "facade-max-ops-chunk " + maxOpsChunk)); finish(); return false; }
            if (whole && kind == Kind.APPLY) {
                Long at = previews.get(ownerKey() + "|" + p.key);
                if (at == null || System.currentTimeMillis() - at > PREVIEW_VALID_MS) { status = "needs_preview"; reasons.add("no whole preview of plan " + p.key + " by you in the last 10 minutes: run '/facadetool preview whole' first"); finish(); return false; }
            }
            for (long k : scope) if (pl.chunkInStrip(cxOf(k), czOf(k))) notes.add("scope chunk " + cxOf(k) + "," + czOf(k) + " touches the Strip: its Strip cells are skipped");
            return true;
        }
        /** retirement (ready plans only): a live facade placement of this building absent from the new plan; scope-limited */
        void scanRetire(long q) {
            if (!seenR.add(q)) return;
            Placement l = latest(w, q);
            if (l == null || !l.content().equals("facade") || l.role().equals("retire") || !members.contains(l.feature()) || plan.positions.contains(q)) return;   // LongSet
            if (scope.contains(key(l.x() >> 4, l.z() >> 4))) retire.add(new Object[]{l, preFacade(q, l)}); else obsoleteOutside++;
        }
        void afterScan() {
            int cap = whole ? Math.max(maxOpsWhole, plan.maxOpsException()) : maxOpsChunk;
            if (inScopeOps + retire.size() > cap) { status = "refused_limits"; reasons.add(inScopeOps + " ops + " + retire.size() + " retirements in scope > "
                    + (whole ? "facade-max-ops-whole " + maxOpsWhole + (plan.maxOpsException() > 0 ? " / package exception " + plan.maxOpsException() : "") : "facade-max-ops-chunk " + maxOpsChunk)); finish(); return; }
            if (obsoleteOutside > 0) notes.add(obsoleteOutside + " obsolete facade cell(s) of earlier plans lie outside this scope and were left in place (apply their chunks or 'whole' to retire them)");
            phase = P_WAIT;
            if (writes() && !lock()) return;
            load();
        }
        /** M1: the state before this building's facade work at a cell: walk back over the consecutive live facade placements (retire rows
         *  are boundaries: they already hold a pre-facade state) and return the oldest one's before-state */
        String preFacade(long q, Placement l) {
            List<Placement> all = pl.byPos.get(q); Placement cur = l; String target = l.before(); int i = all.size() - 1;
            while (i >= 0 && all.get(i) != l) i--;
            for (int k = i - 1; k >= 0; k--) {
                Placement p = all.get(k);
                if (pl.undone.contains(p.job()) || !p.world().equals(w.getName())) continue;
                BlockData cb = pl.egg.parse(cur.before());
                if (!p.content().equals("facade") || p.role().equals("retire") || !members.contains(p.feature()) || cb == null || !ShellToolPlugin.matchesAfter(p.content(), p.after(), cb)) break;
                target = p.before(); cur = p;
            }
            return target;
        }
        /** all scope chunks at once in the shared regionLocks, or refuse */
        boolean lock() {
            for (long k : scope) {
                String h = pl.regionLocks.containsKey(k) ? pl.lockHolder(k) : pl.activeChunks.containsKey(k) ? "a Snowball job" : null;
                if (h != null) { status = "busy"; reasons.add(h + " is running in chunk " + cxOf(k) + "," + czOf(k) + "; nothing written. Try again after it finishes."); finish(); return false; }
            }
            for (long k : scope) { pl.regionLocks.put(k, "facade:" + id); locked.add(k); }
            return true;
        }
        /** async: region-file reads (which chunks are generated) and the chunk order of every item stream; then main: edge chunks, begin.
         *  Nothing is loaded here: the tick's sliding window (slide/front/next/releaseFree) loads and holds at most maxLoaded chunks. */
        void load() {
            stage = "loading chunks"; phase = P_WAIT; long l0 = System.currentTimeMillis(); List<Long> ks = new ArrayList<>(scope);
            File wf = w.getWorldFolder(); Plan p0 = plan; int[] wk = work.trim(); List<Object[]> rt = new ArrayList<>(retire), cd = cands; List<Placement> up = ups;
            Bukkit.getScheduler().runTaskAsynchronously(pl, () -> safe(() -> {      // region-file reads and sorting off the main thread
                Map<Long, Boolean> full = new HashMap<>();
                for (long k : ks) for (int dx = -2; dx <= 2; dx++) for (int dz = -2; dz <= 2; dz++) {
                    long n = key(cxOf(k) + dx, czOf(k) + dz);
                    if (!full.containsKey(n)) full.put(n, !EggTool.offsetAbsent(wf, cxOf(k) + dx, czOf(k) + dz) && EggTool.diskFull(wf, cxOf(k) + dx, czOf(k) + dz));
                }
                int[] pw = serpentine(wk.length, i -> { int s0 = p0.uStart[wk[i]]; return key(p0.cx[s0] >> 4, p0.cz[s0] >> 4); }), ow = new int[wk.length];
                for (int i = 0; i < wk.length; i++) ow[i] = wk[pw[i]];
                int[] pr = serpentine(rt.size(), i -> { Placement l = (Placement) rt.get(i)[0]; return key(l.x() >> 4, l.z() >> 4); });
                int[] pu = serpentine(up.size(), i -> key(up.get(i).x() >> 4, up.get(i).z() >> 4));
                int[] pc = serpentine(cd.size(), i -> key((int) cd.get(i)[0] >> 4, (int) cd.get(i)[2] >> 4));
                List<Object[]> or = new ArrayList<>(rt.size()), oc = new ArrayList<>(cd.size()); List<Placement> ou = new ArrayList<>(up.size());
                for (int i : pr) or.add(rt.get(i)); for (int i : pu) ou.add(up.get(i)); for (int i : pc) oc.add(cd.get(i));
                main(() -> safe(() -> {
                    if (done) return;
                    diskFull = full; System.arraycopy(ow, 0, work.a, 0, ow.length); retire.clear(); retire.addAll(or); ups = ou; cands = oc;
                    for (long k : scope) if (!interior(k)) edge.add(k);           // never loaded, never ticketed: their cells are skipped
                    if (!edge.isEmpty()) notes.add(edge.size() + " scope chunk(s) at the edge of the generated world (a chunk within 2 of them is not generated): not loaded, their cells skipped. Facade never causes generation.");
                    loadMs = System.currentTimeMillis() - l0; phase = P_RUN; begin();
                }));
            }));
        }
        /** a chunk may be held (plugin ticket = entity-ticking level, which needs FULL chunks out to radius 2) only if all of those are
         *  already generated: on disk, or loaded now. Otherwise holding it would make the server generate them. */
        boolean interior(long k) {
            for (int dx = -2; dx <= 2; dx++) for (int dz = -2; dz <= 2; dz++) {
                int x = cxOf(k) + dx, z = czOf(k) + dz;
                if (!diskFull.getOrDefault(key(x, z), false) && !w.isChunkLoaded(x, z)) return false;
            }
            return true;
        }
        // ---- sliding chunk window. Items (the processing order): ADOPT cands | UNDO ups | APPLY/PREVIEW retire then work units, each stream
        //      sorted by chunk (serpentine). An item runs only when its own chunks are held (this job's plugin ticket, via the shared refcount)
        //      and its own + ring chunks are loaded with entities. Prefetch admits items in order while win (own + ring + in-flight loads) fits
        //      maxLoaded and loads at most MAX_LOADS at once; a chunk is released once no admitted item needs it (after the tick's ledger flush).
        //      Oversized rule: a component whose own + ring chunks alone exceed maxLoaded is skipped ("needs N chunks > ..."); anything that
        //      fits is admitted at the latest once the window has drained to it (in-flight loads always complete), so the window never deadlocks.
        int total() { return kind == Kind.ADOPT ? cands.size() : kind == Kind.UNDO ? ups.size() : retire.size() + work.n; }
        boolean held(long k) { WC e = win.get(k); return e != null && e.st == W_HELD; }
        /** a cell's chunks: its own, plus (ring) those of its 4 horizontal neighbours (neighboursLoaded/attached/entities read them): at most 3 */
        Need cellNeed(int x, int z, boolean ring) {
            long o = key(x >> 4, z >> 4); if (!ring) return new Need(new long[]{o}, 1);
            long[] a = new long[3]; a[0] = o; int m = 1;
            for (int d = 0; d < 4; d++) { long q = key((x + (d == 0 ? 1 : d == 1 ? -1 : 0)) >> 4, (z + (d == 2 ? 1 : d == 3 ? -1 : 0)) >> 4);
                boolean s = false; for (int j = 0; j < m; j++) s |= a[j] == q; if (!s) a[m++] = q; }
            return new Need(m == 3 ? a : Arrays.copyOf(a, m), 1);
        }
        Need need(int g) {
            if (kind == Kind.ADOPT) { Object[] c = cands.get(g); return cellNeed((int) c[0], (int) c[2], false); }       // adoptCell reads its own cell only
            if (kind == Kind.UNDO) { Placement p = ups.get(g); return cellNeed(p.x(), p.z(), true); }
            if (g < retire.size()) { Placement p = (Placement) retire.get(g)[0]; return cellNeed(p.x(), p.z(), true); }
            int u = work.get(g - retire.size()), s0 = plan.uStart[u], n = plan.uLen[u];
            if (n == 1) return cellNeed(plan.cx[s0], plan.cz[s0], true);
            LongSet own = new LongSet(), ring = new LongSet();
            for (int i = s0; i < s0 + n; i++) own.add(key(plan.cx[i] >> 4, plan.cz[i] >> 4));
            for (int i = s0; i < s0 + n; i++) for (int d = 0; d < 4; d++) { long q = key((plan.cx[i] + (d == 0 ? 1 : d == 1 ? -1 : 0)) >> 4, (plan.cz[i] + (d == 2 ? 1 : d == 3 ? -1 : 0)) >> 4); if (!own.contains(q)) ring.add(q); }
            if (own.size() + ring.size() > maxLoaded) return new Need(null, own.size() + ring.size());
            long[] a = Arrays.copyOf(own.toArray(), own.size() + ring.size()); System.arraycopy(ring.toArray(), 0, a, own.size(), ring.size());
            return new Need(a, own.size());
        }
        /** admit items in order while their chunks fit the cap, then issue loads for wanted chunks (bounded outstanding) */
        void slide(long end) {
            int tot = total(), n0 = 0;
            while (pf < tot && wq.size() < MAX_WINDOW_ITEMS && n0++ < ADMIT_PER_TICK && System.nanoTime() < end) {
                if (nx == null) nx = need(pf);
                if (nx.k() != null) {
                    int add = 0; for (long k : nx.k()) if (!win.containsKey(k)) add++;
                    if (win.size() + add > maxLoaded) break;                     // full: admitted once earlier items finish and release
                    for (int i = 0; i < nx.k().length; i++) { long k = nx.k()[i]; WC e = win.computeIfAbsent(k, q -> new WC()); e.refs++;
                        if (i < nx.own() && !e.own) { e.own = true; if (e.st == W_PASSIVE && !edge.contains(k) && !ungen.contains(k)) e.st = W_WANT; } }
                }
                wq.add(nx); nx = null; pf++;
            }
            peak = Math.max(peak, win.size());
            for (Map.Entry<Long, WC> en : win.entrySet()) { if (loading >= MAX_LOADS) break; if (en.getValue().st == W_WANT) request(en.getKey(), en.getValue()); }
        }
        /** load one own chunk (never generating) and hold it with this job's share of the plugin ticket, if still needed when it arrives */
        void request(long k, WC e) {
            e.st = W_LOADING; loading++;
            w.getChunkAtAsync(cxOf(k), czOf(k), false).whenComplete((c, ex) -> main(() -> safe(() -> {
                loading--;
                if (done) return;                                                 // cancelled/finished: never ticketed
                if (ex != null) { status = "provider_failure"; reasons.add("chunk load failed: " + ex.getMessage()); finish(); return; }
                if (c == null) { ungen.add(k); e.st = W_PASSIVE; } else if (e.refs > 0) { pl.ticket(c); e.c = c; e.st = W_HELD; }
                if (e.refs == 0) win.remove(k, e);                                // no admitted item needs it any more
            })));
        }
        /** the front item may run: own chunks held (or never holdable: the per-cell checks skip those cells) and own + generated ring chunks
         *  loaded with their entities. An entity load that never completes is waited for at most 60 s; the per-cell checks then decide. */
        boolean front() {
            if (gi >= pf) return false;
            Need n = wq.peekFirst(); if (n.k() == null) return true;
            boolean ok = true;
            for (int i = 0; i < n.k().length; i++) { long k = n.k()[i]; WC e = win.get(k);
                if (e == null) continue;                                          // (only after finish)
                if (e.st == W_WANT || e.st == W_LOADING) return false;              // its load always completes
                if (e.st == W_HELD) ok &= e.c.isEntitiesLoaded();
                else if (w.isChunkLoaded(cxOf(k), czOf(k))) ok &= w.getChunkAt(cxOf(k), czOf(k)).isEntitiesLoaded();
                else if (i >= n.own() && diskFull.getOrDefault(k, false)) ok = false;   // ring: loads through its held neighbour's ticket
            }
            if (ok || ++fwait > 1200) { fwait = 0; return true; }
            return false;
        }
        /** the front item is finished (written and ledgered; flushed at the end of this tick before releaseFree) */
        void next() {
            gi++; Need n = wq.poll();
            if (n != null && n.k() != null) for (long k : n.k()) { WC e = win.get(k); if (e != null && --e.refs == 0) free.add(k); }
        }
        /** release this job's hold on chunks no admitted item needs (other holders keep theirs: shared refcount; never force-unloaded) */
        void releaseFree() {
            for (long k : free) { WC e = win.get(k); if (e == null || e.refs > 0 || e.st == W_LOADING) continue; win.remove(k); if (e.st == W_HELD) pl.unticket(e.c); }
            free.clear();
        }
        void begin() {
            if (done) return;
            if (writes() && kind != Kind.UNDO) {
                JsonObject o = new JsonObject(); o.addProperty("type", "job"); o.addProperty("job", id); o.addProperty("owner", ownerKey()); o.addProperty("world", w.getName());
                o.addProperty("tool", "facade"); o.addProperty("content", kind == Kind.ADOPT ? "facade-adopt" : "facade"); o.addProperty("bid", bid);
                if (plan != null) { o.addProperty("plan_key", plan.key); o.addProperty("selection", sel); }
                o.addProperty("scope", whole ? "whole" : "chunk"); JsonArray ch = new JsonArray(); for (long k : scope) { JsonArray q = new JsonArray(); q.add(cxOf(k)); q.add(czOf(k)); ch.add(q); } o.add("chunks", ch);
                pl.log(o); pl.flush(); pl.jobOwner.put(id, ownerKey()); pl.facadeJobs.add(id);
            }
            stage = kind == Kind.PREVIEW || (kind == Kind.ADOPT && !confirm) ? "checking" : "placing"; event(this, "running");
            if (task == null) task = Bukkit.getScheduler().runTaskTimer(pl, () -> safe(this::tick), 1, 1);
        }
        void tick() {
            long t = System.nanoTime(), lim = (long) (msPerTick * 1e6), end = t + lim; int budget = blocksPerTick;
            if (phase == P_WAIT) return;
            if (phase < P_RUN) { setup(end); return; }
            slide(t + lim / 3);                                                  // window: admit items, prefetch (bounded)
            Set<Long> touched = new HashSet<>();
            while (budget > 0 && System.nanoTime() < end) {
                if (pend == null && gi < total() && !front()) break;            // the next item's chunks are not ready yet
                if (kind == Kind.ADOPT) { if (idx >= cands.size()) break; adoptCell(cands.get(idx++)); next(); budget--; continue; }
                if (kind == Kind.UNDO) { if (idx >= ups.size()) break; Placement up = ups.get(idx);
                    if (!neighboursLoaded(up.x(), up.z()) && ++stall <= 200) break; stall = 0; idx++; undoCell(up, touched); next(); budget--; continue; }
                if (ridx < retire.size()) { Object[] r = retire.get(ridx); Placement rl = (Placement) r[0];
                    if (!neighboursLoaded(rl.x(), rl.z()) && ++stall <= 200) break; stall = 0; ridx++; retireCell(rl, (String) r[1], touched); next(); budget--; continue; }
                if (pend == null) { if (idx >= work.n) break; Need nd = wq.peekFirst();
                    if (nd.k() == null) { skipUnit(plan.unit(work.get(idx++)), "needs " + nd.own() + " chunks (cells + neighbour ring) > facade-max-loaded-chunks " + maxLoaded); next(); budget--; continue; }
                    pend = new Pend(plan.unit(work.get(idx++))); budget -= pend.u.cells().size(); }
                boolean big = pend.u.cells().size() > SPLIT;
                if (!pre(pend, end)) break;                                      // any instance's pre-check resumes next tick (clock checked per cell)
                if (pend.ok && big && kind != Kind.PREVIEW && System.nanoTime() - t > lim / 4) break;   // its atomic write gets a fresh tick
                if (pend.ok) write(pend, touched);
                pend = null; next();
            }
            if (!touched.isEmpty()) pl.preflights.values().removeIf(q -> !Collections.disjoint(q.chunks(), touched));   // cached Snowball crossing checks are stale
            long f0 = System.nanoTime(); if (writes()) pl.flush(); long flushNs = System.nanoTime() - f0;
            releaseFree();                                                       // after the flush: finished items are ledgered on disk
            long dt = System.nanoTime() - t; placeNs += dt; maxPlaceTickNs = Math.max(maxPlaceTickNs, dt);
            if (dt > 3 * lim) pl.getLogger().info("facade job " + id + " slow tick " + Math.round(dt / 1e5) / 10.0 + " ms (" + what() + ", next item " + idx + ", flush " + Math.round(flushNs / 1e5) / 10.0 + " ms)");
            if (kind == Kind.ADOPT ? idx >= cands.size() : kind == Kind.UNDO ? idx >= ups.size() : ridx >= retire.size() && idx >= work.n && pend == null) finish();
        }
        /** null = writable (air/cave_air, or eligible owned: own[0] = owner job); else why the cell is protected. Main thread, live world. */
        String check(Cell c, Block b, BlockData cur, String[] own) { return check(c, b, cur, own, NONE); }
        static final java.util.function.LongPredicate NONE = q -> false;
        /** unitCells: positions of the instance being written (their current state is about to be replaced by this instance) */
        String check(Cell c, Block b, BlockData cur, String[] own, java.util.function.LongPredicate unitCells) {
            own[0] = null;
            if (c.y() < w.getMinHeight() || c.y() >= w.getMaxHeight()) return "outside world height";
            if (!held(key(c.x() >> 4, c.z() >> 4))) return notLoadedWhy(c.x(), c.z());
            if (!plan.inEnvelope(c.x(), c.y(), c.z())) return "envelope";
            if (inStrip(c.x(), c.z())) return "Strip exclusion";
            long p = pos(c.x(), c.y(), c.z());
            if (pl.tracked.contains(p)) return "tracked player edit";
            if (pl.pilotRoads.contains(p)) return "accepted pilot road (protected)";
            Placement l = latest(w, p);
            if (l != null && !OWNABLE.contains(l.content()))
                return l.content().startsWith("egg-") ? "Egg work (protected)" : l.content().equals("roads") || l.content().equals("overpass") ? l.content() + " work (protected)" : "legacy tool work without content (protected)";
            if (l != null && !members.contains(l.feature())) return "owned by another building (" + l.feature() + ")";
            if (!air(cur.getMaterial())) {
                if (l == null) return "not owned (terrain or existing build)";
                if (!ShellToolPlugin.matchesAfter(l.content(), l.after(), cur)) return "changed since its placement (preserved)";
                if (isTile(c.x(), c.y(), c.z())) return "block entity";
                String a = attached(b, unitCells); if (a != null) return a;
                own[0] = l.job();
            }
            return entities(c.x(), c.y(), c.z());
        }
        /** the cell's chunk and the chunks of its 4 horizontal neighbours are loaded with their entities (attached()/entities() read them;
         *  1.18 loads entity sections asynchronously after the chunk, so an empty entity lookup before that is not "no entity").
         *  Missing chunks are requested, never generated. */
        boolean neighboursLoaded(int x, int z) {
            boolean ok = true;
            for (int k = 0; k < 5; k++) { int ncx = (x + (k == 0 ? 1 : k == 1 ? -1 : 0)) >> 4, ncz = (z + (k == 2 ? 1 : k == 3 ? -1 : 0)) >> 4;
                if (!w.isChunkLoaded(ncx, ncz)) { ok = false; if (requested.add(key(ncx, ncz))) w.getChunkAtAsync(ncx, ncz, false); }
                else if (!w.getChunkAt(ncx, ncz).isEntitiesLoaded()) ok = false; }
            return ok;
        }
        String notLoadedWhy(int x, int z) { return edge.contains(key(x >> 4, z >> 4)) ? "edge of the generated world (not loaded: holding it would generate neighbours)" : "chunk not generated"; }
        boolean inStrip(int x, int z) {
            if (plan != null && plan.stripBits != null && plan.inBox(x, z)) return plan.stripBits.get((z - plan.bz0) * plan.bw + (x - plan.bx0));
            return stripCol.computeIfAbsent(key(x, z) * 0x9E3779B97F4A7C15L, q -> pl.inStrip(x, z));   // mixed key: key(x,z) hashes to x^z and collides
        }
        /** block entity at this cell (tile positions of its chunk, collected once per tick) */
        boolean isTile(int x, int y, int z) {               // per block: cost independent of how many block entities its chunk holds
            Block b = w.getBlockAt(x, y, z); return !b.getType().isAir() && b.getState(false) instanceof TileState;
        }
        /** B1: an entity in the cell or touching one of its faces (item frames, paintings: their box lies in the neighbour cell).
         *  Entities of the chunks within 2 blocks, collected once per tick. */
        String entities(int x, int y, int z) {
            for (Entity e : w.getNearbyEntities(new BoundingBox(x - 0.1, y - 0.1, z - 0.1, x + 1.1, y + 1.1, z + 1.1)))   // entity-section lookup, bounded per cell
                return "attached: entity " + e.getType().getKey().getKey() + " in or on the cell";
            return null;
        }
        /** B1: a neighbour that physically depends on this cell (dependsOn: fixed to this face, standing on it, hanging from it, or a
         *  gravity block above it) and is not an eligible owned cell of this building, nor a cell of the instance being written. A
         *  free-standing neighbour (full block, chest, glass ...) is never attached, block entity or not. null = nothing attached. */
        String attached(Block b, java.util.function.LongPredicate unitCells) {
            for (BlockFace f : FACES) {
                int nx = b.getX() + f.getModX(), ny = b.getY() + f.getModY(), nz = b.getZ() + f.getModZ();
                if (ny < w.getMinHeight() || ny >= w.getMaxHeight()) continue;
                Block n = w.getBlockAt(nx, ny, nz); Material m = n.getType();
                if (!dependsOn(n.getBlockData(), f.getOppositeFace())) continue;
                boolean tile = isTile(nx, ny, nz);
                long q = pos(nx, ny, nz);
                if (unitCells.test(q)) continue;
                Placement ln = latest(w, q);
                if (!tile && ln != null && OWNABLE.contains(ln.content()) && members.contains(ln.feature()) && !pl.tracked.contains(q)
                        && ShellToolPlugin.matchesAfter(ln.content(), ln.after(), n.getBlockData())) continue;
                return "attached: " + m.getKey().getKey() + " on the " + f.name().toLowerCase(Locale.ROOT) + " face";
            }
            return null;
        }
        private void skipUnit(Unit u, String why) {
            skippedCells += u.cells().size(); if (u.comp() != null) compSkipped++;
            String k = u.comp() != null ? "component: " + why : why; skippedBy.merge(k, u.cells().size(), Integer::sum);
            for (Cell c0 : u.cells()) ev(c0.evidence())[2]++;
            if (examples.size() < 16) { Cell c0 = u.cells().get(0); examples.add(c0.x() + " " + c0.y() + " " + c0.z() + " " + (u.comp() != null ? u.comp() + "/" + u.kind() + " " : "") + c0.role() + ": " + why); }
        }
        /** one component instance (all-or-nothing) or one plain op being processed: pre-check cursor, cells to write, their targets */
        final class Pend {
            final Unit u; int i, up, wait; boolean ok = true; final IntList todo; final List<BlockData> tgts; final LongSet uc = new LongSet();
            Pend(Unit u) { this.u = u; int n = u.cells().size(); todo = new IntList(n); tgts = new ArrayList<>(n); for (Cell c0 : u.cells()) uc.add(pos(c0.x(), c0.y(), c0.z())); }
        }
        /** pre-check (resumable): false = deadline reached, continue next tick; true = finished (p.ok false = the instance was skipped) */
        boolean pre(Pend p, long deadline) {
            String[] own = new String[1]; List<Cell> cs = p.u.cells();
            for (; p.i < cs.size(); p.i++) {
                if (System.nanoTime() > deadline) return false;
                Cell c0 = cs.get(p.i);
                BlockData tgt = pl.egg.parse(c0.block()); if (tgt == null) { skipUnit(p.u, "invalid block state " + c0.block()); p.ok = false; return true; }
                if (!held(key(c0.x() >> 4, c0.z() >> 4))) { skipUnit(p.u, notLoadedWhy(c0.x(), c0.z())); p.ok = false; return true; }
                if (!neighboursLoaded(c0.x(), c0.z())) { if (++p.wait > 200) { skipUnit(p.u, "neighbour chunk did not load (never generated here)"); p.ok = false; return true; } return false; }
                Block b = w.getBlockAt(c0.x(), c0.y(), c0.z()); BlockData cur = b.getBlockData();
                if (cur.equals(tgt)) continue;                                   // already present
                String why = check(c0, b, cur, own, p.uc::contains); if (why != null) { skipUnit(p.u, why); p.ok = false; return true; }
                if (own[0] != null && !own[0].equals(id)) p.up++;
                p.todo.add(p.i); p.tgts.add(tgt);
            }
            return true;
        }
        /** the instance's writes, all in this tick: each cell re-checked immediately before its write; a failure mid-instance restores the
         *  cells already written in this tick, and only a complete instance is ledgered */
        void write(Pend p, Set<Long> touched) {
            Unit u = p.u;
            if (kind == Kind.PREVIEW) { upgraded += p.up; count(p); return; }
            String[] own = new String[1]; List<Object[]> done0 = new ArrayList<>();                         // block, before, cell, upgradeOf
            for (int i = 0; i < p.todo.n; i++) {
                Cell c0 = u.cells().get(p.todo.get(i)); Block b = w.getBlockAt(c0.x(), c0.y(), c0.z()); BlockData cur = b.getBlockData();
                String why = check(c0, b, cur, own, p.uc::contains);
                if (why == null && u.comp() != null && failNext == i) { failNext = -1; why = "injected re-check failure (simfail test hook)"; }
                if (why != null) { for (int k = done0.size() - 1; k >= 0; k--) ((Block) done0.get(k)[0]).setBlockData((BlockData) done0.get(k)[1], false);
                    skipUnit(u, (done0.isEmpty() ? "" : "rolled back " + done0.size() + " written cell(s): ") + why); return; }
                b.setBlockData(p.tgts.get(i), false); done0.add(new Object[]{b, cur, c0, own[0] != null && !own[0].equals(id) ? own[0] : null});
            }
            for (int i = 0; i < done0.size(); i++) { Object[] d = done0.get(i); Cell c0 = (Cell) d[2];
                index(pl.logPlace(id, w, c0.x(), c0.y(), c0.z(), canon((BlockData) d[1]), canon(p.tgts.get(i)), c0.feature(), c0.role(), "facade", (String) d[3]));
                if (d[3] != null) upgraded++; touched.add(key(c0.x() >> 4, c0.z() >> 4)); }
            count(p);
        }
        private void count(Pend p) {
            int n = p.u.cells().size(); boolean[] wr = new boolean[n]; for (int i = 0; i < p.todo.n; i++) wr[p.todo.get(i)] = true;
            placed += p.todo.n; present += n - p.todo.n; if (p.u.comp() != null) compPlaced++;
            for (int k = 0; k < n; k++) ev(p.u.cells().get(k).evidence())[wr[k] ? 0 : 1]++;
        }
        void retireCell(Placement l, String target, Set<Long> touched) {
            long p = pos(l.x(), l.y(), l.z()); String why = null;
            if (!held(key(l.x() >> 4, l.z() >> 4))) { retireRefused++; skippedBy.merge("retire refused: " + notLoadedWhy(l.x(), l.z()), 1, Integer::sum); return; }
            Block b = w.getBlockAt(l.x(), l.y(), l.z()); BlockData cur = b.getBlockData(), tb = pl.egg.parse(target);
            if (latest(w, p) != l) why = "superseded since planning";
            else if (!ShellToolPlugin.matchesAfter(l.content(), l.after(), cur)) why = "changed since placement (preserved)";
            else if (pl.tracked.contains(p) || isTile(l.x(), l.y(), l.z())) why = "tracked edit / block entity";
            else if (inStrip(l.x(), l.z())) why = "Strip exclusion";
            else if (tb == null) why = "invalid before-state";
            else if ((why = attached(b, NONE)) != null || (why = entities(l.x(), l.y(), l.z())) != null) { }
            if (why != null) { retireRefused++; skippedBy.merge("retire refused: " + why, 1, Integer::sum); return; }
            retired++;
            if (kind == Kind.PREVIEW) return;
            b.setBlockData(tb, false); touched.add(key(l.x() >> 4, l.z() >> 4));
            index(pl.logPlace(id, w, l.x(), l.y(), l.z(), canon(cur.getAsString()), canon(tb.getAsString()), l.feature(), "retire", "facade", l.job().equals(id) ? null : l.job()));
        }

        // ---- adoption: exact, untracked matches of prep-supplied candidates become eligible (before == after, nothing changes in the world)
        void loadCandidates(boolean afterPrep) {
            File f = new File(fdir, "buildings" + File.separator + bid + File.separator + "adopt-candidates.json");
            if (!afterPrep) { stage = "preparing candidates";                // prep "adopt <bid>" (re)writes adopt-candidates.json
                prep(id, "adopt", bid).whenComplete((r, ex) -> safe(() -> {
                    String st = r == null ? null : str(r, "status");
                    if (!"ready".equals(st) && !"nothing_to_do".equals(st)) { addReasons(r == null ? fail("adopt prep failed: " + ex) : r);
                        if (f.isFile()) notes.add("prep 'adopt' returned " + st + ": using the existing " + f.getName() + " (cells are adopted only on an exact current match)"); }
                    loadCandidates(true); })); return; }
            try {
                byte[] raw = Files.readAllBytes(f.toPath()); JsonObject o = gson.fromJson(new String(raw, StandardCharsets.UTF_8), JsonObject.class);
                candSource = str(o, "source"); candSha = str(o, "source_sha");
                StringJoiner sl = new StringJoiner(" + ");                       // per-cell source: a 5th cell element if prep supplies one, else the file's sources
                if (o.has("sources") && o.get("sources").isJsonArray()) for (JsonElement e : o.getAsJsonArray("sources")) { JsonObject so = e.getAsJsonObject(); String sh = str(so, "sha256");
                    sl.add(str(so, "kind") + ":" + new File(Objects.toString(str(so, "path"), "?")).getName() + (sh == null ? "" : " sha " + sh.substring(0, Math.min(12, sh.length())))); }
                String src0 = sl.length() == 0 ? Objects.toString(candSource, "?") : sl.toString();
                List<Object[]> l = new ArrayList<>(); Set<Long> ch = new LinkedHashSet<>();
                for (JsonElement e : o.getAsJsonArray("cells")) { JsonArray a = e.getAsJsonArray(); int x = a.get(0).getAsInt(), y = a.get(1).getAsInt(), z = a.get(2).getAsInt();
                    l.add(new Object[]{x, y, z, a.get(3).getAsString(), a.size() > 4 && a.get(4).isJsonPrimitive() ? a.get(4).getAsString() : src0}); ch.add(key(x >> 4, z >> 4)); }
                String fsha = sha(raw);
                main(() -> safe(() -> {
                    if (done) return;
                    cands = l; scope.addAll(ch);
                    if (l.isEmpty()) { status = "nothing_to_do"; reasons.add("no adoption candidates"); finish(); return; }
                    if (l.size() > maxOpsWhole) { status = "refused_limits"; reasons.add(l.size() + " candidates > facade-max-ops-whole " + maxOpsWhole); finish(); return; }
                    adoptKey = ownerKey() + "|adopt|" + bid + "|" + fsha; candSha = candSha == null ? fsha : candSha;
                    if (confirm) { AdoptDry d = adoptDry.get(ownerKey());     // confirm adopts only the cells your latest dry run listed
                        if (d == null || System.currentTimeMillis() - d.at() > PREVIEW_VALID_MS) { status = "needs_preview"; reasons.add("run '/facadetool adopt' (dry run) on this building first; confirm within 10 minutes"); finish(); return; }
                        if (!d.key().equals(adoptKey)) { status = "needs_preview"; reasons.add("your last dry run was for another building or the recorded shell (candidate file) changed since: run '/facadetool adopt' again"); finish(); return; }
                        dry = new LongSet(); for (long q : d.cells()) dry.add(q); dryN = d.cells().length; }
                    status = "ready";
                    if (writes() && !lock()) return;
                    load();
                }));
            } catch (Exception ex) { status = f.isFile() ? "provider_failure" : "nothing_to_do"; reasons.add(f.isFile() ? "adopt-candidates.json unreadable: " + ex.getMessage() : "no adoption candidates for " + bid + " (" + f + " missing)"); main(this::finish); }
        }
        void adoptCell(Object[] c) {
            int x = (int) c[0], y = (int) c[1], z = (int) c[2]; String exp = (String) c[3]; long p = pos(x, y, z); String why = null;
            if (y < w.getMinHeight() || y >= w.getMaxHeight()) why = "outside world height";
            else if (!held(key(x >> 4, z >> 4))) why = notLoadedWhy(x, z);
            else if (inStrip(x, z)) why = "Strip exclusion";
            else if (pl.tracked.contains(p)) why = "tracked player edit";
            else if (pl.pilotRoads.contains(p)) why = "accepted pilot road (protected)";
            BlockData cur = null;
            if (why == null) { cur = w.getBlockAt(x, y, z).getBlockData(); BlockData eb = pl.egg.parse(exp); Placement l = latest(w, p);
                if (l != null) {
                    if (OWNABLE.contains(l.content()) && members.contains(l.feature()) && ShellToolPlugin.matchesAfter(l.content(), l.after(), cur)) { alreadyOwned++; row(c, "already_owned"); return; }
                    why = "ledger: latest placement is " + (l.content().isEmpty() ? "legacy" : l.content()) + " work of " + l.feature() + (l.after().equals(cur.getAsString()) ? "" : ", changed since");
                }
                else if (eb == null) why = "invalid expected block";
                else if (air(eb.getMaterial())) why = "expected air (nothing to adopt)";
                else if (!cur.equals(eb)) why = "differs from expected (now " + cur.getMaterial().getKey().getKey() + ")";   // exact state, never material alone
                else if (isTile(x, y, z)) why = "block entity";
                else if (dry != null && !dry.contains(p)) why = "not in your dry run (adoptable now: run the dry run again to include it)";
            }
            if (why != null) { ambiguousBy.merge(why, 1, Integer::sum); if (examples.size() < 16) examples.add(x + " " + y + " " + z + ": " + why); row(c, "refused:" + why); return; }
            adopted++; adoptSet.add(p); row(c, "adopt"); if (adoptEx.size() < 3) adoptEx.add(x + " " + y + " " + z);
            adoptByBlock.merge(exp.replaceFirst("\\[.*", "").replace("minecraft:", ""), 1, Integer::sum); adoptBySource.merge((String) c[4], 1, Integer::sum);
            box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y); box[2] = Math.min(box[2], z); box[3] = Math.max(box[3], x); box[4] = Math.max(box[4], y); box[5] = Math.max(box[5], z);
            if (confirm) { String cs0 = canon(cur.getAsString()); pl.logPlace(id, w, x, y, z, cs0, cs0, bid, "adopt", "facade-adopt", null); }
        }
        /** one adopt-cells.csv row: the recorded (expected) block; face_or_part is not supplied by the candidate file yet */
        void row(Object[] c, String verdict) { csv.append(c[0]).append(',').append(c[1]).append(',').append(c[2]).append(',').append(csvq((String) c[3])).append(",,").append(csvq((String) c[4])).append(',').append(csvq(verdict)).append('\n'); }
        /** undo: a cell still at its after-state (Facade tick/connection rules) and not a tracked edit goes back to its before-state */
        void undoCell(Placement p, Set<Long> touched) {
            long q = pos(p.x(), p.y(), p.z());
            if (!held(key(p.x() >> 4, p.z() >> 4))) { notLoaded++; return; }
            Block b = w.getBlockAt(p.x(), p.y(), p.z()); BlockData bd = pl.egg.parse(p.before());
            BlockData cur = b.getBlockData();
            if (pl.tracked.contains(q) || bd == null || !ShellToolPlugin.matchesAfter(p.content(), p.after(), cur)) { modified++; return; }
            String why = null;
            if (!cur.equals(bd)) {                                                     // a restore that changes the world: the same B1 checks as apply and retire
                if (!air(cur.getMaterial())) why = attached(b, (java.util.function.LongPredicate) undoPos::contains);   // this job's own cells are not attached work
                if (why == null) why = entities(p.x(), p.y(), p.z());
            }
            if (why != null) { keptAttached++; if (examples.size() < 16) examples.add(p.x() + " " + p.y() + " " + p.z() + " kept: " + why); return; }
            restorable++;
            if (!confirm) return;
            b.setBlockData(bd, false); restored++; touched.add(key(p.x() >> 4, p.z() >> 4));
            JsonObject o = new JsonObject(); o.addProperty("type", "undo"); o.addProperty("job", undoOf); o.addProperty("x", p.x()); o.addProperty("y", p.y()); o.addProperty("z", p.z()); pl.log(o);
        }

        void cancel(String why) { if (done) return; cancelled = true; notes.add("Cancelled: " + why); if (progress != null) progress.cancel(); finish(); }
        void finish() {
            if (done) return; done = true; stage = "done";
            if (task != null) task.cancel();
            for (WC e : win.values()) if (e.st == W_HELD) release.add(e.c);           // in-flight loads see done and are never ticketed
            win.clear(); wq.clear();
            if (!ungen.isEmpty()) notes.add(ungen.size() + " scope chunk(s) not generated: their cells are skipped (never generated by Facade)");
            if (releaser == null && !release.isEmpty()) releaser = Bukkit.getScheduler().runTaskTimer(pl, FacadeTool.this::releaseSome, 1, 1);
            for (long k : locked) pl.regionLocks.remove(k, "facade:" + id);
            jobs.remove(id, this);
            if (writes()) pl.flush();
            if (kind == Kind.PREVIEW && whole && status.equals("ready") && !cancelled && plan != null) previews.put(ownerKey() + "|" + plan.key, System.currentTimeMillis());
            if (kind == Kind.ADOPT && !confirm && status.equals("ready") && !cancelled && adoptKey != null) adoptDry.put(ownerKey(), new AdoptDry(adoptKey, w.getName(), adoptSet.toArray(), System.currentTimeMillis()));
            if (kind == Kind.UNDO && confirm && status.equals("ready")) {
                if (partial()) notes.add("partial undo: " + restored + " cell(s) restored, " + keptAttached + " kept (attached work), " + notLoaded + " not loaded" + (cancelled ? ", cancelled" : "")
                        + "; job " + undoOf + " stays live: run the undo again once they can be restored");
                else { JsonObject o = new JsonObject(); o.addProperty("type", "undo-done"); o.addProperty("job", undoOf); o.addProperty("restored", restored); o.addProperty("kept_modified", modified + notLoaded);
                    pl.log(o); pl.flush(); pl.undone.add(undoOf); }
            }
            if (monJob != null && monJob.equals(id)) monAfterTicks = 0;
            report();
            event(this, !cancelled && (status.equals("ready") || status.equals("nothing_to_do")) ? "completed" : "failed");      // Egg's status vocabulary
        }
        void report() {
            boolean ap = writes(); Gson g = new GsonBuilder().serializeNulls().setPrettyPrinting().create();
            JsonObject r = new JsonObject(); r.addProperty("job", id); r.addProperty("tool", "facade"); r.addProperty("kind", what()); r.addProperty("status", status); r.add("reasons", g.toJsonTree(reasons));
            r.addProperty("cancelled", cancelled); r.addProperty("target", ix + " " + iy + " " + iz); r.addProperty("building", bid); r.add("members", g.toJsonTree(members));
            r.addProperty("selection", noSel() ? null : selLabel(sel));
            if (sel != null && sel.startsWith("style:") && !noSel()) r.addProperty("label", "STYLE-GENERATED (not observed)");
            if ("auto".equals(sel) && !noSel()) { r.addProperty("label", modeLabel(autoMode)); r.addProperty("auto_mode", autoMode);
                if (planLine != null) for (String k : List.of("faces", "providers", "cost", "analyzer", "autospec", "autospec_cached", "evidence_sha", "ops_by_evidence"))
                    if (planLine.has(k)) r.add("auto_" + k, planLine.get(k)); }
            r.addProperty("scope", kind == Kind.ADOPT ? "building (candidates)" : whole ? "whole" : "chunk " + cx + "," + cz);
            r.addProperty("max_loaded_chunks", peak); r.addProperty("window_cap", maxLoaded);   // peak chunks held by this job incl. neighbour ring + in-flight loads
            JsonArray ch = new JsonArray(); for (long k : scope) { JsonArray q = new JsonArray(); q.add(cxOf(k)); q.add(czOf(k)); ch.add(q); } r.add("chunks", ch);
            JsonObject tm = new JsonObject(); tm.addProperty("identify_prep_s", idPrepS); tm.addProperty("plan_queue_s", queueS); tm.addProperty("plan_prep_s", planPrepS); tm.addProperty("chunk_load_ms", loadMs);
            tm.addProperty("placement_ms", Math.round(placeNs / 1e5) / 10.0); long curStep = stepStart > 0 ? System.nanoTime() - stepStart : 0;
            tm.addProperty("max_tick_ms", Math.round(Math.max(maxStepNs, curStep) / 1e5) / 10.0); tm.addProperty("max_placement_tick_ms", Math.round(maxPlaceTickNs / 1e5) / 10.0);
            tm.addProperty("max_tick_excl_gc_ms", Math.round(maxStepExGcNs / 1e5) / 10.0); tm.addProperty("max_gc_pause_in_step_ms", Math.round(maxGcInStepNs / 1e5) / 10.0);
            if (id.equals(monJob)) tm.addProperty("server_max_tick_interval_ms", Math.round(monDuring / 1e5) / 10.0); tm.addProperty("total_s", (System.currentTimeMillis() - t0) / 1000.0);
            if (plan != null && plan.head.has("timings_s")) tm.add("compile_s (plan)", plan.head.get("timings_s"));
            r.add("timings", tm);
            if (kind == Kind.UNDO) {
                r.addProperty("undo_of", undoOf); r.addProperty("recorded", ups.size()); r.addProperty(confirm ? "restored" : "would_restore", confirm ? restored : restorable);
                r.addProperty("kept_modified", modified); r.addProperty("kept_attached_work", keptAttached); r.addProperty("kept_not_loaded", notLoaded); r.addProperty("partial", partial());
            } else if (kind == Kind.ADOPT) {
                r.addProperty("candidates_source", candSource); r.addProperty("candidates_sha", candSha); r.addProperty("candidates", cands.size());
                r.addProperty(confirm ? "adopted" : "would_adopt", adopted); r.addProperty("already_owned", alreadyOwned); r.add("ambiguous_by_reason", g.toJsonTree(ambiguousBy));
                r.add("adopt_by_block", g.toJsonTree(adoptByBlock)); r.add("adopt_by_source", g.toJsonTree(adoptBySource)); if (adopted > 0) r.add("adopt_box", g.toJsonTree(box));
                if (confirm) r.addProperty("dry_run_cells", dryN); r.addProperty("cells_csv", "facade-reports/" + id + "-adopt-cells.csv");
            } else {
                if (plan != null) { r.addProperty("plan", plan.path); r.addProperty("plan_key", plan.key); r.add("plan_selection", plan.head.get("selection")); r.addProperty("generator", str(plan.head, "generator"));
                    r.add("envelope", plan.head.get("envelope")); r.add("faces", plan.head.get("faces")); r.add("unresolved", plan.head.get("unresolved")); r.add("limits", plan.head.get("limits"));
                    r.addProperty("plan_ops", plan.ops); r.addProperty("plan_chunks", plan.chunks.size()); }
                r.addProperty("ops_in_scope", inScopeOps); r.addProperty(ap ? "placed" : "would_place", placed); r.addProperty("already_present", present); r.addProperty("upgraded_owned", upgraded);
                r.addProperty("skipped", skippedCells); r.add("skipped_by_reason", g.toJsonTree(skippedBy)); r.addProperty("deferred", deferredCells); r.add("deferred_by_reason", g.toJsonTree(deferredBy));
                JsonObject cs = new JsonObject(); cs.addProperty(ap ? "placed" : "would_place", compPlaced); cs.addProperty("skipped", compSkipped); cs.addProperty("deferred", compDeferred); r.add("components", cs);
                r.addProperty(ap ? "retired" : "would_retire", retired); r.addProperty("retire_refused", retireRefused); r.addProperty("obsolete_outside_scope", obsoleteOutside);
                JsonObject ev = new JsonObject(); for (Map.Entry<String, int[]> e : byEvidence.entrySet()) { JsonObject q = new JsonObject(); int[] v = e.getValue();
                    q.addProperty(ap ? "placed" : "would_place", v[0]); q.addProperty("present", v[1]); q.addProperty("skipped", v[2]); q.addProperty("deferred", v[3]); ev.add(e.getKey(), q); }
                r.add("by_evidence", ev);
            }
            r.add("notes", g.toJsonTree(notes)); r.add("examples", g.toJsonTree(examples));
            if (idLine != null) r.add("identify", idLine); if (planLine != null) r.add("prep_status_line", planLine);
            File dir = new File(pl.getDataFolder(), "facade-reports"); File rf = new File(dir, "facade-" + id + ".json");
            String csv0 = kind == Kind.ADOPT && !cands.isEmpty() ? csv.toString() : null;
            Runnable wrr = () -> { dir.mkdirs();
                try (Writer wr = Files.newBufferedWriter(rf.toPath(), StandardCharsets.UTF_8)) { g.toJson(r, wr); }
                catch (IOException ex) { pl.getLogger().warning("facade report write failed: " + ex); }
                if (csv0 != null) try { Files.writeString(new File(dir, id + "-adopt-cells.csv").toPath(), csv0, StandardCharsets.UTF_8); }
                catch (IOException ex) { pl.getLogger().warning("facade adopt cells write failed: " + ex); } };
            if (stopping || !pl.isEnabled()) wrr.run(); else Bukkit.getScheduler().runTaskAsynchronously(pl, wrr);   // the JSON is complete; only the file write is off-thread
            // chat: 1-3 lines
            String head = "§b" + TAG + "Job " + id + (cancelled ? " CANCELLED " : " DONE ") + what() + " " + (bid == null ? "" : bid + " ") + (noSel() ? "" : ("auto".equals(sel) ? modeLabel(autoMode) : selLabel(sel)) + " ") + "- " + status.toUpperCase(Locale.ROOT);
            who.sendMessage(head);
            if (!status.equals("ready")) who.sendMessage("§6  " + ShellToolPlugin.shortWhy(reasons.isEmpty() ? statusHelp(status) : statusHelp(status) + ": " + reasons.get(0)));
            else if (kind == Kind.UNDO) who.sendMessage("§b" + TAG + "Undo " + (confirm ? "" : "DRY RUN ") + "job " + undoOf + ": " + ups.size() + " recorded placements, "
                    + (confirm ? "restored " + restored : "would restore " + restorable) + " to their exact before-state, kept (changed since placement) " + modified
                    + ", kept (attached work) " + keptAttached + (notLoaded > 0 ? ", kept (chunk not loadable without generation) " + notLoaded : "")
                    + (confirm && partial() ? " - PARTIAL: job " + undoOf + " stays live" + (cancelled ? " (cancelled)" : "") : "")
                    + (confirm ? "" : ". Run '/facadetool undo " + undoOf + " confirm' to apply."));
            else if (kind == Kind.ADOPT) {
                int refused = ambiguousBy.values().stream().mapToInt(Integer::intValue).sum();
                who.sendMessage("§b  " + (confirm ? "ADOPTED " + adopted + " of the " + dryN + " cell(s) your dry run listed" + (dryN != adopted ? " (" + (dryN - adopted) + " differ now: see refused / CSV)" : "")
                        : adopted + " verified shell cell(s) WOULD BE ADOPTED") + " | " + cands.size() + " candidates, " + alreadyOwned + " already owned, " + refused + " refused");
                if (adopted > 0) { who.sendMessage("§7  by block: " + top(adoptByBlock, 6)); who.sendMessage("§7  by source: " + top(adoptBySource, 3));
                    who.sendMessage("§7  box: x " + box[0] + ".." + box[3] + ", y " + box[1] + ".." + box[4] + ", z " + box[2] + ".." + box[5] + " | e.g. " + String.join("; ", adoptEx)); }
                if (refused > 0) who.sendMessage("§7  refused: " + top(ambiguousBy, 4));
                who.sendMessage("§7  every cell + verdict: plugins/SummerlinShellTool/facade-reports/" + id + "-adopt-cells.csv" + (confirm || adopted == 0 ? "" : " | '/facadetool adopt show' outlines them for 20 s"));
                who.sendMessage("§7  Rule: a cell is adopted only if it is listed in the recorded shell (candidate source) AND the live block exactly equals the recorded block state"
                        + " AND it has no ledger owner AND it is not a tracked player edit / block entity. Ownership is never inferred from material alone.");
                if (!confirm && adopted > 0) who.sendMessage("§b  Run '/facadetool adopt confirm' within 10 minutes to record exactly these " + adopted + " cell(s); nothing changes in the world.");
            }
            else who.sendMessage("§b  " + placed + (ap ? " placed" : " would place") + " (" + upgraded + " upgraded owned), " + present + " present, " + skippedCells + " skipped, " + deferredCells + " deferred"
                    + (retired + retireRefused > 0 ? ", " + retired + (ap ? " retired" : " would retire") : "") + (obsoleteOutside > 0 ? ", " + obsoleteOutside + " obsolete outside scope" : "")
                    + (kind == Kind.PREVIEW && whole ? " | " + scope.size() + " chunks, " + inScopeOps + " ops: '/facadetool apply whole confirm' within 10 min" : ""));
            if (!byEvidence.isEmpty() && kind != Kind.ADOPT && kind != Kind.UNDO) { StringBuilder eb = new StringBuilder();
                for (Map.Entry<String, int[]> e : byEvidence.entrySet()) eb.append(eb.length() == 0 ? "" : ", ").append(evLabel(e.getKey())).append(' ').append(e.getValue()[0]);
                who.sendMessage("§7  " + (ap ? "placed" : "would place") + " by evidence: " + eb); }
            who.sendMessage("§7  Report: plugins/SummerlinShellTool/facade-reports/facade-" + id + ".json");
            pl.getLogger().info("facade job " + id + " " + what() + " " + bid + " " + status + " placed/would " + placed + " skipped " + skippedCells + " deferred " + deferredCells + " retired " + retired + " adopted " + adopted);
        }
    }
    /** evidence class -> chat label: only `observed` is called observed */
    static String evLabel(String e) { return switch (e) { case "observed" -> "observed"; case "style" -> "STYLE-GENERATED (not observed)"; case "inferred" -> "inferred (not observed)";
        case "measured" -> "measured"; case "reference" -> "reference (person-supplied or person-aligned, not automatic)"; default -> e; }; }
    /** async: chat lines for each new stage written by the prep to auto-progress.json (small file, read off the main thread every 0.5 s) */
    BukkitTask pollProgress(FJob j, File f) {
        String[] last = {null}; double since = j.t0 / 1000.0 - 1;
        return Bukkit.getScheduler().runTaskTimerAsynchronously(pl, () -> {
            if (j.done || !f.isFile() || f.length() > 65536) return;
            try { JsonObject o = gson.fromJson(Files.readString(f.toPath(), StandardCharsets.UTF_8), JsonObject.class);
                if (o == null || !o.has("t") || o.get("t").getAsDouble() < since) return;                 // a previous run's file
                String st = str(o, "stage"), d = str(o, "detail"), k = st + "|" + d;
                if (st == null || k.equals(last[0])) return; last[0] = k; j.stage = "auto: " + st;
                if (st.equals("ready") || st.equals("failed")) return;                                    // the summary follows
                msg(j.who, "§7" + TAG + "auto " + j.id + ": " + st + (d == null || d.isEmpty() ? "" : " - " + ShellToolPlugin.shortWhy(d)));
            } catch (Exception ignored) {}                                                                // mid-rewrite: next poll
        }, 10, 10);
    }
    /** the automatic plan's summary in chat: mode, per-face evidence, timings and cost (worker thread; chat via main) */
    void autoSummary(FJob j, JsonObject pr) {
        j.autoMode = str(pr, "mode");
        StringBuilder sb = new StringBuilder("§b" + TAG + "auto ready for preview: " + modeLabel(j.autoMode));
        if (pr.has("timings_s") && pr.get("timings_s").isJsonObject()) { JsonObject t = pr.getAsJsonObject("timings_s"); sb.append(" | ");
            for (String k : List.of("retrieval_s", "association_s", "analysis_s", "compile_s")) if (t.has(k)) sb.append(k.replace("_s", "")).append(' ').append(t.get(k).getAsString()).append("s ");
            if (t.has("plan_cached")) sb.append("(cached) "); }
        if (pr.has("cost") && pr.get("cost").isJsonObject()) { JsonObject c = pr.getAsJsonObject("cost");
            sb.append("| cost $").append(c.has("usd") ? c.get("usd").getAsString() : "0").append(" (").append(c.has("requests") ? c.get("requests").getAsString() : "0").append(" requests)"); }
        if (pr.has("providers") && pr.get("providers").isJsonObject()) sb.append(" | providers ").append(ShellToolPlugin.shortWhy(pr.get("providers").toString()));
        msg(j.who, sb.toString());
        if (!pr.has("faces") || !pr.get("faces").isJsonObject()) return;
        // observed faces one by one (with their evidence counts); approximated and unresolved faces as totals (a resort has hundreds of faces)
        List<String> obs = new ArrayList<>(); Map<String, int[]> other = new TreeMap<>();      // label -> faces, ops
        for (Map.Entry<String, JsonElement> e : pr.getAsJsonObject("faces").entrySet()) { JsonObject f = e.getValue().getAsJsonObject();
            String pe = Objects.toString(str(f, "plan_evidence"), Objects.toString(str(f, "status"), "?"));
            Map<String, Integer> by = new TreeMap<>(); int n = 0;
            if (f.has("ops_by_evidence") && f.get("ops_by_evidence").isJsonObject()) for (Map.Entry<String, JsonElement> c : f.getAsJsonObject("ops_by_evidence").entrySet()) { by.put(c.getKey(), c.getValue().getAsInt()); n += c.getValue().getAsInt(); }
            if ("observed".equals(pe)) { StringBuilder q = new StringBuilder(e.getKey() + " observed (" + (f.has("images") ? f.get("images").getAsInt() : 0) + " img):");
                for (Map.Entry<String, Integer> c : by.entrySet()) q.append(' ').append(evLabel(c.getKey())).append(' ').append(c.getValue());
                obs.add(q.toString()); }
            else { int[] t = other.computeIfAbsent("style".equals(pe) ? "STYLE-GENERATED (not observed)" : pe, k -> new int[2]); t[0]++; t[1] += n; } }
        for (int i = 0; i < obs.size() && i < 12; i += 3) msg(j.who, "§a  " + String.join(" | ", obs.subList(i, Math.min(Math.min(obs.size(), 12), i + 3))));
        if (obs.size() > 12) msg(j.who, "§a  ... " + (obs.size() - 12) + " more observed face(s) (see the report)");
        StringBuilder ot = new StringBuilder();
        for (Map.Entry<String, int[]> e : other.entrySet()) ot.append(ot.length() == 0 ? "" : " | ").append(e.getKey()).append(": ").append(e.getValue()[0]).append(" face(s)").append(e.getValue()[1] > 0 ? ", " + e.getValue()[1] + " ops" : "");
        if (ot.length() > 0) msg(j.who, "§7  " + ot);
    }
    static String statusHelp(String st) {
        return switch (st) {
            case "unresolved" -> "unresolved: not enough evidence for these faces, nothing placed";
            case "no_geometry" -> "no geometry for this building (footprint/heights missing)";
            case "no_package" -> "no package with that id for this building";
            case "invalid_package" -> "package failed validation, nothing placed";
            case "refused_limits" -> "refused: over the size/op limits";
            case "protected" -> "protected area (Strip), refused";
            case "nothing_to_do" -> "nothing to do";
            case "needs_preview" -> "preview first";
            case "busy" -> "chunks locked by another job";
            default -> "provider failure/timeout";
        };
    }
    static String sha(byte[] b) {
        try { StringBuilder sb = new StringBuilder(); for (byte x : MessageDigest.getInstance("SHA-256").digest(b)) sb.append(String.format("%02x", x)); return sb.toString(); }
        catch (Exception e) { return "?"; }
    }

    /** BlueMap status line (facade-events-file, JSONL; same format as Egg). The plugin never touches BlueMap itself. */
    synchronized void event(FJob j, String status) {
        if (eventsFile == null || eventsFile.isEmpty() || j.kind == Kind.UNDO) return;      // like Egg: undo emits no events
        Set<Long> sc = j.scope.isEmpty() ? Set.of(key(j.cx, j.cz)) : j.scope;
        int x0 = Integer.MAX_VALUE, z0 = Integer.MAX_VALUE, x1 = Integer.MIN_VALUE, z1 = Integer.MIN_VALUE;
        for (long k : sc) { x0 = Math.min(x0, cxOf(k) << 4); z0 = Math.min(z0, czOf(k) << 4); x1 = Math.max(x1, (cxOf(k) << 4) + 15); z1 = Math.max(z1, (czOf(k) << 4) + 15); }
        JsonObject o = new JsonObject(); o.addProperty("tool", "facade"); o.addProperty("id", j.id); o.addProperty("status", status);
        o.addProperty("minX", x0); o.addProperty("minZ", z0); o.addProperty("maxX", x1); o.addProperty("maxZ", z1);
        o.addProperty("label", (j.kind == Kind.ADOPT ? "adopt" : j.sel) + " " + (j.kind == Kind.APPLY || (j.kind == Kind.ADOPT && j.confirm) ? "apply" : "preview"));   // Egg's "<content> <mode>"
        o.addProperty("t", System.currentTimeMillis() / 1000);
        String line = gson.toJson(o);
        Runnable wr0 = () -> { synchronized (eventLock) {
            try { File f = new File(eventsFile); if (f.getParentFile() != null) f.getParentFile().mkdirs();
                try (Writer wr = Files.newBufferedWriter(f.toPath(), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND)) { wr.write(line); wr.write('\n'); } }
            catch (Exception e) { pl.getLogger().warning("facade events file not written: " + e); } } };
        if (stopping || !pl.isEnabled()) wr0.run(); else Bukkit.getScheduler().runTaskAsynchronously(pl, wr0);   // file I/O off the main thread
    }

    @Override public List<String> onTabComplete(CommandSender s, Command c, String l, String[] a) {
        List<String> sel = new ArrayList<>(), jl = new ArrayList<>();
        if (a.length == 2 && a[0].equalsIgnoreCase("select")) {
            String[] ps = packagesDir.list(); if (ps != null) for (String p : ps) if (new File(packagesDir, p).isDirectory()) sel.add(p);
            String[] ss = new File(assetsDir, "styles").list(); if (ss != null) for (String p : ss) if (p.endsWith(".json")) sel.add("style:" + p.substring(0, p.length() - 5)); }
        if (a.length == 2 && a[0].equalsIgnoreCase("undo")) { String me = s instanceof Player p ? p.getUniqueId().toString() : null;
            for (String j : pl.facadeJobs) if (pl.ledgerJobs.containsKey(j) && !pl.undone.contains(j) && (me == null || me.equals(pl.jobOwner.get(j)) || s.hasPermission(ShellToolPlugin.ADMIN))) jl.add(j); }
        return complete(a, sel, jl);
    }
    /** pure completion (selectable ids and undoable jobs supplied by the caller) */
    static List<String> complete(String[] a, List<String> selectable, List<String> undoable) {
        List<String> out = new ArrayList<>(); String a0 = a.length > 0 ? a[0].toLowerCase(Locale.ROOT) : "";
        if (a.length <= 1) out.addAll(List.of("help", "inspect", "select", "preview", "apply", "adopt", "status", "cancel", "undo"));
        else if (a.length == 2) switch (a0) {
            case "select" -> { out.add("auto"); out.add("none"); out.addAll(selectable); }
            case "preview" -> out.addAll(List.of("chunk", "whole", "refresh"));
            case "apply" -> out.addAll(List.of("chunk", "whole"));
            case "adopt" -> out.addAll(List.of("confirm", "show"));
            case "undo" -> { out.add("confirm"); out.addAll(undoable); }
            default -> {} }
        else if (a.length == 3 && ((a0.equals("apply") && a[1].equalsIgnoreCase("whole")) || (a0.equals("undo") && !a[1].equalsIgnoreCase("confirm")))) out.add("confirm");
        else if (a.length == 3 && a0.equals("preview") && !a[1].equalsIgnoreCase("refresh")) out.add("refresh");
        String pre = a.length == 0 ? "" : a[a.length - 1].toLowerCase(Locale.ROOT);
        return out.stream().filter(x -> x.toLowerCase(Locale.ROOT).startsWith(pre)).toList();
    }
}
