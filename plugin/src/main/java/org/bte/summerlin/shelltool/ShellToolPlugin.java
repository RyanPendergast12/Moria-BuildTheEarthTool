package org.bte.summerlin.shelltool;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.bukkit.Bukkit;
import org.bukkit.Chunk;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.block.TileState;
import org.bukkit.block.data.BlockData;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.TabExecutor;
import org.bukkit.entity.Player;
import org.bukkit.entity.Snowball;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.event.entity.ProjectileHitEvent;
import org.bukkit.event.player.PlayerBucketEmptyEvent;
import org.bukkit.event.player.PlayerBucketFillEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerMoveEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitTask;
import org.bukkit.util.BoundingBox;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Summerlin/Vegas shell + road tool (v0.3). Tool mode: a snowball that hits a BLOCK prepares (or reuses) real geographic
 * data for that block's chunk and generates the selected content (shells, roads or both) inside that chunk only.
 * Shells: air cells only. Roads: replace the ground surface only where the provenance rules hold (see README/spec).
 * Strip exclusion at request time and in the writer; exact before/after ledger; undo keeps later edits.
 * v0.4 pilot: content OVERPASS (off by default, allowlisted crossings only) places a measured, world-fitted bridge deck into air;
 * a crossing that conflicts with any existing/protected block in the chunk is not placed there at all and is reported UNRESOLVED.
 */
public final class ShellToolPlugin extends JavaPlugin implements Listener, TabExecutor {
    static final String USE = "summerlin.shelltool.use", ADMIN = "summerlin.shelltool.admin";
    enum Mode { OFF, PREVIEW, APPLY }
    enum Content { SHELLS, ROADS, BOTH, OVERPASS;
        boolean shells() { return this == SHELLS || this == BOTH; } boolean roads() { return this == ROADS || this == BOTH; } boolean overpass() { return this == OVERPASS; }
        /** prep result for this content also serves request c */
        boolean covers(Content c) { return this == c || (this == BOTH && (c == SHELLS || c == ROADS)); }
        String arg() { return name().toLowerCase(Locale.ROOT); } }
    /** column segment. mode F fixed cells, W wall top-down to the real ground, G ground outline, R road surface cell (y0 = expected terrain) */
    record Seg(int x, int z, int y0, int y1, String block, char mode, String feature, String role, String label) {}
    record Placement(String job, String world, int x, int y, int z, String before, String after, String feature) {}
    static final Set<String> OV_ROLES = Set.of("deck", "deck-soffit", "parapet", "median", "abutment", "pier", "approach-fill", "approach-wall", "approach-surface", "approach-barrier", "retire");
    static final Set<String> OV_WALL_ROLES = Set.of("abutment", "pier", "approach-fill", "approach-wall");            // built top-down to the real ground
    /** derived plans from any other preparation pipeline are re-prepared (old cached geometry cannot hide fixes) */
    static final String PIPELINE = "shelltool_prep v4.3";
    static String contentOf(Seg s) { return s.mode() == 'R' ? "roads" : OV_ROLES.contains(s.role()) ? "overpass" : "shells"; }

    private double[] stripPoly, coverPoly;
    private final Map<Long, List<Seg>> accepted = new HashMap<>();
    private final Set<String> acceptedIds = new HashSet<>();
    private final Set<Long> pilotRoads = new HashSet<>(), tracked = ConcurrentHashMap.newKeySet();
    private volatile boolean loaded;
    private String worldName, prepPython, prepScript; private File cacheDir;
    private int blocksPerTick, maxJobs, applyDelayTicks, maxPrepQueue, prepTimeoutS, prepWorkers, maxPrepareRadius, prefetchRadius;
    private long cooldownMs; private double placeMsPerTick;
    private boolean overpassEnabled, overpassPiers; private double approachGrade; private String overpassMode = "auto"; private List<String> overpassExclude = List.of(); private int minClearance = 4; private List<String> overpassCrossings = List.of();
    private final Set<UUID> allowed = new HashSet<>();
    private final Map<UUID, Mode> modes = new HashMap<>();
    private final Map<UUID, Content> contents = new HashMap<>();
    private final Set<UUID> prefetchOn = new HashSet<>();
    private final Map<UUID, Long> lastThrow = new HashMap<>();
    private final Map<Long, Job> activeChunks = new HashMap<>();
    private final Map<String, List<Placement>> ledgerJobs = new LinkedHashMap<>();
    private final Map<String, String> jobOwner = new HashMap<>();
    private final Set<String> undone = new HashSet<>();
    private final Map<Long, List<Placement>> byPos = new HashMap<>();                 // ownership: every recorded placement per position
    private final Map<String, Set<String>> dependents = new HashMap<>();              // job -> later jobs that upgraded its blocks
    private final Map<String, BlockData> bd = new HashMap<>();
    private final AtomicInteger seq = new AtomicInteger();
    private final AtomicLong prepSeq = new AtomicLong();
    private final Gson gson = new Gson();
    private Writer ledger, trackLog;
    // preparation scheduler: builds (priority 0) before prefetch (1); one task per chunk (deduplicated); bounded queue
    private final PriorityBlockingQueue<PrepTask> prepQueue = new PriorityBlockingQueue<>();
    private final Map<Long, PrepTask> prepByChunk = new ConcurrentHashMap<>();
    private final List<Thread> workers = new ArrayList<>();
    private volatile boolean stopping;

    static long key(int cx, int cz) { return ((long) cx << 32) ^ (cz & 0xffffffffL); }
    static long pos(int x, int y, int z) { return ((long) (x & 0x3FFFFFF) << 38) | ((long) (z & 0x3FFFFFF) << 12) | (y & 0xFFF); }
    BlockData data(String s) { return bd.computeIfAbsent(s, Bukkit::createBlockData); }

    @Override public void onEnable() {
        saveDefaultConfig();
        int port = getConfig().getInt("required-server-port", 25566);
        if (Bukkit.getPort() != port) { getLogger().severe("Server port " + Bukkit.getPort() + " != required-server-port " + port + ": tool disabled."); getServer().getPluginManager().disablePlugin(this); return; }
        worldName = getConfig().getString("world", "world");
        blocksPerTick = getConfig().getInt("blocks-per-tick", 400);
        placeMsPerTick = getConfig().getDouble("place-ms-per-tick", 8.0);
        maxJobs = getConfig().getInt("max-active-jobs", 3);
        cooldownMs = getConfig().getLong("cooldown-ms", 3000);
        applyDelayTicks = getConfig().getInt("apply-delay-ticks", 1);
        maxPrepQueue = getConfig().getInt("max-prep-queue", 32);
        prepTimeoutS = getConfig().getInt("prep-timeout-seconds", 300);
        prepWorkers = Math.max(1, Math.min(4, getConfig().getInt("prep-workers", 2)));
        maxPrepareRadius = Math.max(0, Math.min(6, getConfig().getInt("max-prepare-radius", 3)));
        prefetchRadius = Math.max(0, Math.min(2, getConfig().getInt("prefetch-radius", 1)));
        prepPython = getConfig().getString("prep-python", "C:\\BTE-Migration\\venv\\Scripts\\python.exe");
        prepScript = getConfig().getString("prep-script", "C:\\BTE-Migration\\scripts\\shelltool_prep.py");
        cacheDir = new File(getConfig().getString("cache-dir", new File(getDataFolder(), "cache").getPath()));
        for (String s : getConfig().getStringList("allowed-players")) allowed.add(UUID.fromString(s));
        overpassEnabled = getConfig().getBoolean("overpass-enabled", false);                // OFF by default
        overpassCrossings = getConfig().getStringList("overpass-crossings");                // used in allowlist mode
        overpassMode = getConfig().getString("overpass-mode", "auto").equalsIgnoreCase("allowlist") ? "allowlist" : "auto";   // auto: every eligible crossing
        overpassExclude = getConfig().getStringList("overpass-exclude");
        minClearance = Math.max(3, getConfig().getInt("overpass-min-clearance", 4));
        overpassPiers = getConfig().getBoolean("overpass-piers", false);                    // inferred piers OFF by default
        approachGrade = getConfig().getDouble("overpass-approach-grade", 0.0);              // raised approaches: 0 = not authorized
        if (approachGrade != 0 && (approachGrade < 0.04 || approachGrade > 0.10)) { getLogger().warning("overpass-approach-grade " + approachGrade + " outside 0.04..0.10: approaches disabled"); approachGrade = 0; }
        if (overpassEnabled) getLogger().info("Overpass generation ENABLED: mode " + overpassMode + (overpassMode.equals("allowlist") ? " " + overpassCrossings : " (every eligible crossing)")
                + (overpassExclude.isEmpty() ? "" : ", excluded " + overpassExclude) + ", min clearance " + minClearance + ", piers " + (overpassPiers ? "on" : "off")
                + ", approaches " + (approachGrade > 0 ? "grade " + approachGrade : "off"));
        getCommand("shelltool").setExecutor(this); getCommand("shelltool").setTabCompleter(this);
        getServer().getPluginManager().registerEvents(this, this);
        for (Player p : Bukkit.getOnlinePlayers()) grant(p);
        try {
            loadLedger(); ledger = Files.newBufferedWriter(ledgerPath(), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            loadTracked(); trackLog = Files.newBufferedWriter(trackPath(), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) { getLogger().severe("ledger/tracking unavailable, disabling: " + e); getServer().getPluginManager().disablePlugin(this); return; }
        for (int i = 0; i < prepWorkers; i++) { Thread t = new Thread(this::prepWorker, "ShellTool-prep-" + i); t.setDaemon(true); t.start(); workers.add(t); }
        File data = new File(getDataFolder(), getConfig().getString("data-file", "shells.txt"));
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            try { loadAccepted(data); loaded = true; getLogger().info("Accepted pilot shells: " + acceptedIds.size() + " buildings; protected pilot road cells: " + pilotRoads.size() + "; tracked edits: " + tracked.size() + "; cache: " + cacheDir); }
            catch (Exception e) { getLogger().severe("data load failed (tool stays inactive): " + e); }
        });
    }
    @Override public void onDisable() {
        stopping = true;
        for (Job j : new ArrayList<>(activeChunks.values())) j.cancel("server stopping");
        for (PrepTask t : prepByChunk.values()) t.cancel();
        for (Thread t : workers) t.interrupt();
        try { if (ledger != null) ledger.close(); if (trackLog != null) trackLog.close(); } catch (IOException ignored) {}
    }
    private void grant(Player p) { if (allowed.contains(p.getUniqueId())) p.addAttachment(this, USE, true); }
    @EventHandler public void onJoin(PlayerJoinEvent e) {               // activation is always OFF after login (content is remembered until restart)
        UUID u = e.getPlayer().getUniqueId(); modes.remove(u); prefetchOn.remove(u); grant(e.getPlayer()); }

    // ---------------------------------------------------------------- data
    private void loadAccepted(File f) throws IOException {
        List<String> pal = new ArrayList<>(), fid = new ArrayList<>();
        try (BufferedReader r = Files.newBufferedReader(f.toPath(), StandardCharsets.UTF_8)) {
            String l;
            while ((l = r.readLine()) != null) {
                if (l.startsWith("B ")) {
                    String[] t = l.split(" "); int x = Integer.parseInt(t[1]), y = Integer.parseInt(t[2]), z = Integer.parseInt(t[3]);
                    accepted.computeIfAbsent(key(x >> 4, z >> 4), k -> new ArrayList<>()).add(new Seg(x, z, y, y, pal.get(Integer.parseInt(t[4])), 'F', fid.get(Integer.parseInt(t[5])), "accepted-pilot", "accepted pilot shell (plan_v1)"));
                } else if (l.startsWith("R ")) { String[] t = l.split(" "); pilotRoads.add(pos(Integer.parseInt(t[1]), Integer.parseInt(t[2]), Integer.parseInt(t[3]))); }
                else if (l.startsWith("P ")) pal.add(l.split(" ", 3)[2]);
                else if (l.startsWith("F ")) { String[] t = l.split(" ", 3)[2].split("\t", -1); fid.add(t[0]); if (Integer.parseInt(t[3]) > 0) acceptedIds.add(t[0]); }
                else if (l.startsWith("POLY ")) {
                    String[] t = l.split(" "); double[] p = new double[t.length - 2];
                    for (int i = 2; i < t.length; i++) p[i - 2] = Double.parseDouble(t[i]);
                    if (t[1].equals("strip")) stripPoly = p; else if (t[1].equals("index_coverage")) coverPoly = p;
                }
            }
        }
        if (stripPoly == null) throw new IOException("no Strip polygon in data file - refusing to run without the exclusion");
    }
    static boolean inPoly(double[] p, double x, double z) {
        if (p == null) return false; boolean in = false; int n = p.length / 2;
        for (int i = 0, j = n - 1; i < n; j = i++) {
            double xi = p[2 * i], zi = p[2 * i + 1], xj = p[2 * j], zj = p[2 * j + 1];
            if ((zi > z) != (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) in = !in;
        }
        return in;
    }
    /** the job that owns this block: the latest live (not undone) tool placement here, and only if the block is still EXACTLY what
     *  the tool left. null = not tool-owned or changed since (a player edit / other work): never eligible for replacement. */
    String ownerOf(World w, int x, int y, int z, BlockData cur) {
        List<Placement> l = byPos.get(pos(x, y, z)); if (l == null) return null;
        for (int i = l.size() - 1; i >= 0; i--) { Placement p = l.get(i);
            if (undone.contains(p.job()) || !p.world().equals(w.getName())) continue;
            return p.after().equals(cur.getAsString()) ? p.job() : null; }
        return null;
    }
    /** protected-build rule for one crossing cell column: "x y z role: why", or null when the cells may be built */
    String cellConflict(World w, int x, int z, int y0, int y1, BlockData target, String role, char mode, Set<Long> ent) {
        if (inStrip(x, z)) return x + " " + y0 + " " + z + " " + role + ": Strip exclusion";
        if (mode == 'F') {
            for (int y = y0; y <= y1; y++) { Block b = w.getBlockAt(x, y, z);
                if (toolTouched(x, y, z)) continue;          // the tool built here before: a later edit is preserved cell-by-cell by the writer
                String why = null;
                if (tracked.contains(pos(x, y, z))) why = "tracked player edit";
                else if (ent.contains(pos(x, y, z))) why = "entity present";
                else if (!b.getType().isAir() && !b.getBlockData().equals(target)
                        && (ownerOf(w, x, y, z, b.getBlockData()) == null || b.getState() instanceof TileState)
                        && !(role.equals("approach-surface") && GENERATOR_SURFACE.contains(b.getType()) && w.getBlockAt(x, y + 1, z).getType().isAir()))
                    why = "existing block " + b.getType().getKey().getKey();
                if (why != null) return x + " " + y + " " + z + " " + role + ": " + why;
            }
        } else {
            for (int y = y1; y >= y0 - 3; y--) { Block b = w.getBlockAt(x, y, z);
                if (tracked.contains(pos(x, y, z))) return x + " " + y + " " + z + " " + role + ": tracked player edit";
                if (ent.contains(pos(x, y, z))) return x + " " + y + " " + z + " " + role + ": entity present";
                if (!b.getType().isAir()) {
                    if (b.getBlockData().equals(target) || y <= y0) break;
                    if (y <= y1 && ownerOf(w, x, y, z, b.getBlockData()) != null && !(b.getState() instanceof TileState)) continue;   // own earlier work: upgraded in place
                    return x + " " + y + " " + z + " " + role + ": column blocked by " + b.getType().getKey().getKey() + " above the expected ground";
                }
            }
        }
        return null;
    }
    // ---- whole-crossing preflight: every block of a crossing is checked (all its chunks) before ANY of it is placed
    record Preflight(boolean ok, String summary, Set<Long> chunks, long at) {}
    final Map<String, Preflight> preflights = new ConcurrentHashMap<>();
    final Map<String, CompletableFuture<Preflight>> preflightRunning = new ConcurrentHashMap<>();
    CompletableFuture<Preflight> preflight(String key, String file) {
        Preflight p = preflights.get(key);
        if (p != null && System.currentTimeMillis() - p.at() < 600_000) return CompletableFuture.completedFuture(p);
        return preflightRunning.computeIfAbsent(key, k -> {
            CompletableFuture<Preflight> res = new CompletableFuture<>();
            Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
                Map<Long, List<JsonArray>> byChunk = new HashMap<>();
                try { for (JsonElement e : gson.fromJson(Files.readString(Path.of(file)), JsonObject.class).getAsJsonArray("cells")) { JsonArray a = e.getAsJsonArray();
                        byChunk.computeIfAbsent(key(a.get(0).getAsInt() >> 4, a.get(1).getAsInt() >> 4), q -> new ArrayList<>()).add(a); } }
                catch (Exception ex) { Preflight f = new Preflight(false, "crossing plan unreadable: " + ex.getMessage(), Set.of(), System.currentTimeMillis()); preflightRunning.remove(k); res.complete(f); return; }
                List<String> bad = Collections.synchronizedList(new ArrayList<>());
                Bukkit.getScheduler().runTask(this, () -> {
                    World w = Bukkit.getWorld(worldName); List<CompletableFuture<Void>> fs = new ArrayList<>();
                    for (Map.Entry<Long, List<JsonArray>> en : byChunk.entrySet())
                        fs.add(w.getChunkAtAsync((int) (en.getKey() >> 32), (int) (long) en.getKey()).thenAccept(c -> {
                            for (JsonArray a : en.getValue()) {
                                String why = cellConflict(w, a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt(), a.get(3).getAsInt(), data(a.get(4).getAsString()),
                                        a.get(5).getAsString(), OV_WALL_ROLES.contains(a.get(5).getAsString()) ? 'W' : 'F', Set.of());
                                if (why != null) bad.add(why);
                            } }));
                    CompletableFuture.allOf(fs.toArray(new CompletableFuture[0])).whenComplete((v, ex) -> {
                        Preflight f = new Preflight(ex == null && bad.isEmpty(), ex != null ? "preflight failed: " + ex.getMessage()
                                : bad.isEmpty() ? "whole crossing clear (" + byChunk.size() + " chunks)"
                                : "crossing skipped: " + bad.size() + " protected/conflicting cell(s) across its " + byChunk.size() + " chunks, e.g. " + String.join("; ", bad.subList(0, Math.min(3, bad.size()))),
                                byChunk.keySet(), System.currentTimeMillis());
                        preflights.put(k, f); preflightRunning.remove(k); res.complete(f);
                    });
                });
            });
            return res;
        });
    }
    boolean toolTouched(int x, int y, int z) { List<Placement> l = byPos.get(pos(x, y, z)); if (l != null) for (Placement p : l) if (!undone.contains(p.job())) return true; return false; }
    boolean ovOn(Content c) { return c.overpass() || (overpassEnabled && c.roads()); }      // roads/both include enabled crossings
    String describe(Content c) {
        String ov = overpassEnabled ? " + enabled overpasses" : "";
        return switch (c) { case SHELLS -> "building shells"; case ROADS -> "roads" + ov; case BOTH -> "building shells + roads" + ov;
            case OVERPASS -> "SPECIALIST: only eligible crossings (no ordinary roads or shells)"; };
    }
    String modeLine(UUID u) {
        Mode m = modes.getOrDefault(u, Mode.OFF); Content c = contents.getOrDefault(u, Content.SHELLS);
        return "Mode " + (m == Mode.OFF ? "OFF (snowballs are ordinary)" : m == Mode.PREVIEW ? "PREVIEW (reports only, places nothing)" : "BUILD (places blocks)")
                + " | content " + c.arg() + " = " + describe(c);
    }
    String setMode(UUID u, Mode m) { if (m == Mode.OFF) { modes.remove(u); prefetchOn.remove(u); } else modes.put(u, m); return modeLine(u); }
    boolean inStrip(int x, int z) { return inPoly(stripPoly, x + 0.5, z + 0.5); }
    boolean chunkInStrip(int cx, int cz) {
        for (int dx = 0; dx <= 16; dx += 8) for (int dz = 0; dz <= 16; dz += 8) if (inPoly(stripPoly, (cx << 4) + dx, (cz << 4) + dz)) return true;
        return false;
    }

    // ---------------------------------------------------------------- ledger + edit tracking (tracking starts at install; historical edits are NOT known)
    private Path ledgerPath() { getDataFolder().mkdirs(); return getDataFolder().toPath().resolve("ledger.jsonl"); }
    private Path trackPath() { getDataFolder().mkdirs(); return getDataFolder().toPath().resolve("tracked-edits.txt"); }
    private void loadLedger() throws IOException {
        Path p = ledgerPath(); if (!Files.exists(p)) return;
        for (String l : Files.readAllLines(p, StandardCharsets.UTF_8)) {
            if (l.isBlank()) continue;
            JsonObject o = gson.fromJson(l, JsonObject.class); String t = o.get("type").getAsString(), job = o.get("job").getAsString();
            if (t.equals("place")) { Placement pl = new Placement(job, o.get("world").getAsString(),
                    o.get("x").getAsInt(), o.get("y").getAsInt(), o.get("z").getAsInt(), o.get("before").getAsString(), o.get("after").getAsString(), o.get("feature").getAsString());
                ledgerJobs.computeIfAbsent(job, k -> new ArrayList<>()).add(pl); byPos.computeIfAbsent(pos(pl.x(), pl.y(), pl.z()), k -> new ArrayList<>()).add(pl);
                if (o.has("upgrade_of")) dependents.computeIfAbsent(o.get("upgrade_of").getAsString(), k -> new TreeSet<>()).add(job); }
            else if (t.equals("job")) jobOwner.put(job, o.get("owner").getAsString());
            else if (t.equals("undo-done")) undone.add(job);
        }
    }
    private void loadTracked() throws IOException {
        Path p = trackPath(); if (!Files.exists(p)) return;
        for (String l : Files.readAllLines(p, StandardCharsets.UTF_8)) {
            String[] t = l.split(" "); if (t.length >= 4 && t[0].equals(worldName)) tracked.add(pos(Integer.parseInt(t[1]), Integer.parseInt(t[2]), Integer.parseInt(t[3])));
        }
    }
    private void track(Block b) {
        if (!b.getWorld().getName().equals(worldName)) return;
        long ck = key(b.getX() >> 4, b.getZ() >> 4); preflights.values().removeIf(p -> p.chunks().contains(ck));
        if (tracked.add(pos(b.getX(), b.getY(), b.getZ())))
            try { trackLog.write(worldName + " " + b.getX() + " " + b.getY() + " " + b.getZ() + " " + System.currentTimeMillis() + "\n"); trackLog.flush(); } catch (IOException ignored) {}
    }
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true) public void onPlace(BlockPlaceEvent e) { track(e.getBlock()); }
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true) public void onBreak(BlockBreakEvent e) { track(e.getBlock()); }
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true) public void onBucket(PlayerBucketEmptyEvent e) { track(e.getBlock()); }
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true) public void onBucketFill(PlayerBucketFillEvent e) { track(e.getBlock()); }
    private void log(JsonObject o) {
        o.addProperty("t", System.currentTimeMillis());
        try { ledger.write(gson.toJson(o)); ledger.write('\n'); } catch (IOException e) { getLogger().severe("ledger write failed: " + e); }
    }
    private void flush() { try { ledger.flush(); } catch (IOException e) { getLogger().severe("ledger flush failed: " + e); } }

    // ---------------------------------------------------------------- preparation scheduler
    final class PrepTask implements Comparable<PrepTask> {
        final int cx, cz; volatile int prio; volatile Content content; final long seqNo = prepSeq.incrementAndGet(), enq = System.currentTimeMillis();
        volatile long started; volatile UUID owner; volatile Process proc; volatile boolean cancelled, running;
        final CompletableFuture<Void> done = new CompletableFuture<>();
        PrepTask(int cx, int cz, Content c, int prio, UUID owner) { this.cx = cx; this.cz = cz; this.content = c; this.prio = prio; this.owner = owner; }
        public int compareTo(PrepTask o) { return prio != o.prio ? Integer.compare(prio, o.prio) : Long.compare(seqNo, o.seqNo); }
        void cancel() { cancelled = true; prepQueue.remove(this); Process p = proc; if (p != null) p.destroyForcibly(); prepByChunk.remove(key(cx, cz), this); done.complete(null); }
    }
    /** queue (or join) preparation of a chunk; prio 0 = build request, 1 = prefetch. null if refused (queue full / protected). */
    synchronized PrepTask requestPrep(int cx, int cz, Content c, int prio, UUID owner) {
        if (chunkInStrip(cx, cz)) return null;
        long k = key(cx, cz); PrepTask t = prepByChunk.get(k);
        if (t != null && !t.cancelled) {
            boolean needMore = !t.content.covers(c);
            if (!t.running) {
                if (needMore && !c.overpass() && !t.content.overpass()) t.content = Content.BOTH;
                else if (needMore) return t;                                    // different kind: the job retries after this one finishes
                if (prio < t.prio) { prepQueue.remove(t); t.prio = prio; t.owner = owner; prepQueue.add(t); }
                return t;
            }
            if (!needMore) return t;
        }
        if (prio > 0 && prepQueue.size() >= maxPrepQueue) return null;
        PrepTask n = new PrepTask(cx, cz, c, prio, owner); prepByChunk.put(k, n); prepQueue.add(n); return n;
    }
    private void prepWorker() {
        while (!stopping) {
            PrepTask t;
            try { t = prepQueue.take(); } catch (InterruptedException e) { return; }
            if (t.cancelled) continue;
            t.running = true; t.started = System.currentTimeMillis();
            List<String> cmd = new ArrayList<>(List.of(prepPython, prepScript, cacheDir.getPath(), String.valueOf(t.cx), String.valueOf(t.cz), "--content", t.content.arg()));
            if (ovOn(t.content)) {
                if (overpassMode.equals("auto")) cmd.add("--overpass-auto"); else { cmd.add("--overpass-allow"); cmd.add(String.join(",", overpassCrossings)); }
                if (!overpassExclude.isEmpty()) { cmd.add("--overpass-exclude"); cmd.add(String.join(",", overpassExclude)); }
                cmd.add("--min-clearance"); cmd.add(String.valueOf(minClearance));
                if (overpassPiers) cmd.add("--piers");
                if (approachGrade > 0) { cmd.add("--approach-grade"); cmd.add(String.valueOf(approachGrade)); } }
            ProcessBuilder pb = new ProcessBuilder(cmd).redirectErrorStream(true);
            File logf = new File(cacheDir, "prep-logs" + File.separator + t.cx + "_" + t.cz + ".log"); logf.getParentFile().mkdirs(); pb.redirectOutput(logf);
            try {
                t.proc = pb.start();
                if (!t.proc.waitFor(prepTimeoutS, TimeUnit.SECONDS)) { t.proc.destroyForcibly(); getLogger().warning("prep " + t.cx + "," + t.cz + " timed out after " + prepTimeoutS + " s"); }
            } catch (InterruptedException ie) { if (t.proc != null) t.proc.destroyForcibly(); return; }
            catch (Exception ex) { getLogger().warning("prep could not start: " + ex); }
            finally { prepByChunk.remove(key(t.cx, t.cz), t); t.done.complete(null); }
        }
    }
    JsonObject readManifest(int cx, int cz) {
        File f = new File(cacheDir, "chunks" + File.separator + cx + "_" + cz + ".json");
        try { if (!f.exists()) return null; JsonObject m = gson.fromJson(Files.readString(f.toPath()), JsonObject.class);
            if (!m.has("shells") && m.has("status")) {                       // v0.2 manifest: shells only (migration, read-only)
                JsonObject s = new JsonObject(); s.add("status", m.get("status")); if (m.has("buildings")) s.add("buildings", m.get("buildings"));
                if (m.has("message")) s.add("message", m.get("message")); if (m.has("retry")) s.add("retry", m.get("retry")); m.add("shells", s);
            }
            return m; } catch (Exception e) { return null; }
    }
    static boolean sectionReady(JsonObject m, String sec) {
        if (m == null || !m.has(sec) || !m.get(sec).isJsonObject()) return false;
        JsonObject s = m.getAsJsonObject(sec);
        return !(s.has("retry") && s.get("retry").getAsBoolean()) && !s.get("status").getAsString().endsWith("failure");
    }
    Content missing(JsonObject m, Content want) {
        if (m != null && !(m.has("pipeline") && PIPELINE.equals(m.get("pipeline").getAsString()))) m = null;   // older derived plan: re-prepare
        if (m != null && ovOn(want) && (!m.has("approach_grade") || m.get("approach_grade").getAsDouble() != approachGrade
                || !m.has("overpass_mode") || !overpassMode.equals(m.get("overpass_mode").getAsString()))) m = null;   // approach authorization / mode changed
        if (want.overpass()) return sectionReady(m, "overpass") ? null : Content.OVERPASS;
        boolean ns = want.shells() && !sectionReady(m, "shells"),
                nr = want.roads() && (!sectionReady(m, "roads") || (overpassEnabled && !sectionReady(m, "overpass")));
        return ns && nr ? Content.BOTH : ns ? Content.SHELLS : nr ? Content.ROADS : null;
    }

    // ---------------------------------------------------------------- snowball + prefetch
    @EventHandler public void onHit(ProjectileHitEvent e) {
        if (!(e.getEntity() instanceof Snowball sb) || !(sb.getShooter() instanceof Player p)) return;
        if (modes.getOrDefault(p.getUniqueId(), Mode.OFF) == Mode.OFF) return;
        if (!p.hasPermission(USE)) { modes.remove(p.getUniqueId()); return; }
        Block b = e.getHitBlock();
        if (b == null) { p.sendMessage("§e[ShellTool] Hit an entity, not a block - nothing done. A BLOCK hit is required."); return; }
        handleThrow(p.getUniqueId(), p, b.getWorld(), b.getX(), b.getY(), b.getZ());
    }
    /** the snowball path: the thrower's CURRENT mode and content (also used by the admin test hook simthrow) */
    void handleThrow(UUID u, CommandSender who, World w, int x, int y, int z) {
        Mode m = modes.getOrDefault(u, Mode.OFF); if (m == Mode.OFF) return;
        long now = System.currentTimeMillis();
        if (now - lastThrow.getOrDefault(u, 0L) < cooldownMs) { who.sendMessage("§e[ShellTool] Cooldown - wait a moment."); return; }
        lastThrow.put(u, now);
        submit(who, w, x, y, z, m, contents.getOrDefault(u, Content.SHELLS));
    }
    @EventHandler public void onMove(PlayerMoveEvent e) {
        if (e.getTo() == null || prefetchOn.isEmpty() || !prefetchOn.contains(e.getPlayer().getUniqueId())) return;
        int fx = e.getFrom().getBlockX() >> 4, fz = e.getFrom().getBlockZ() >> 4, tx = e.getTo().getBlockX() >> 4, tz = e.getTo().getBlockZ() >> 4;
        if (fx == tx && fz == tz) return;
        if (!e.getTo().getWorld().getName().equals(worldName)) return;
        prepareAround(e.getPlayer(), tx, tz, prefetchRadius, false);
    }
    int prepareAround(Player p, int cx, int cz, int r, boolean verbose) {
        Content c = contents.getOrDefault(p.getUniqueId(), Content.SHELLS); UUID u = p.getUniqueId();
        List<int[]> cs = new ArrayList<>();
        for (int dx = -r; dx <= r; dx++) for (int dz = -r; dz <= r; dz++) cs.add(new int[]{cx + dx, cz + dz});
        cs.sort(Comparator.comparingInt(a -> Math.abs(a[0] - cx) + Math.abs(a[1] - cz)));
        int[] q = {0, 0, 0};
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            for (int[] a : cs) {
                if (chunkInStrip(a[0], a[1])) { q[2]++; continue; }
                Content need = missing(readManifest(a[0], a[1]), c);
                if (need == null) continue;
                if (requestPrep(a[0], a[1], need, 1, u) != null) q[0]++; else q[1]++;
            }
            if (verbose) msg(p, "§b[ShellTool] Prepare radius " + r + " (" + c.arg() + "): queued " + q[0] + " chunk(s), already prepared " + (cs.size() - q[0] - q[1] - q[2])
                    + (q[1] > 0 ? ", " + q[1] + " refused (prefetch queue full, max " + maxPrepQueue + ")" : "") + (q[2] > 0 ? ", " + q[2] + " PROTECTED (Strip) skipped" : "") + ". Nothing is placed.");
        });
        return cs.size();
    }

    void submit(CommandSender who, World w, int x, int y, int z, Mode mode, Content content) {
        if (!loaded) { who.sendMessage("§c[ShellTool] Data not loaded yet."); return; }
        if (w == null || !w.getName().equals(worldName)) { who.sendMessage("§c[ShellTool] Denied: only world '" + worldName + "' is allowed."); return; }
        int cx = x >> 4, cz = z >> 4; long k = key(cx, cz);                 // arithmetic shift = floor division (negatives correct)
        if (chunkInStrip(cx, cz)) { who.sendMessage("§c[ShellTool] PROTECTED: chunk " + cx + "," + cz + " is inside the Strip exclusion. Refused - nothing fetched or written."); return; }
        if (activeChunks.containsKey(k)) { who.sendMessage("§e[ShellTool] A job is already running for chunk " + cx + "," + cz + "."); return; }
        if (activeChunks.size() >= maxJobs) { who.sendMessage("§e[ShellTool] Queue full (" + maxJobs + " active jobs)."); return; }
        String id = String.format("%tY%<tm%<td-%<tH%<tM%<tS-%d", new Date(), seq.incrementAndGet());
        Job j = new Job(id, who, w, x, y, z, cx, cz, mode, content); activeChunks.put(k, j);
        who.sendMessage("§b[ShellTool] Job " + id + " (" + mode + ", " + content.arg() + "): impact block " + x + " " + y + " " + z + " -> chunk " + cx + "," + cz
                + " (blocks x " + (cx << 4) + ".." + ((cx << 4) + 15) + ", z " + (cz << 4) + ".." + ((cz << 4) + 15) + ")");
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> j.prepareOrLoad(0));
    }
    private void msg(CommandSender s, String text) { Bukkit.getScheduler().runTask(this, () -> s.sendMessage(text)); }

    // ---------------------------------------------------------------- job
    final class Job {
        final String id; final CommandSender who; final World w; final int ix, iy, iz, cx, cz; final Mode mode; final Content content;
        final List<Seg> segs = new ArrayList<>();
        final Map<String, int[]> perFeature = new TreeMap<>();              // in-chunk segs, placed/would, present, skipped
        final Map<String, String> featureInfo = new TreeMap<>();
        final Map<String, Integer> conflictsByReason = new TreeMap<>(), labels = new TreeMap<>();
        final List<String> conflictExamples = new ArrayList<>(), notes = new ArrayList<>(), unsupported = new ArrayList<>();
        int placed, wouldPlace, present, conflicts, idx; volatile String stage = "queued";
        long t0 = System.currentTimeMillis(), queueWaitMs, prepMs, placeNs, maxTickNs; JsonObject manifest; String shellStatus = "-", roadStatus = "-", overpassStatus = "-";
        final JsonArray crossings = new JsonArray(); final Map<String, String> crossingUnresolved = new TreeMap<>();
        final List<String> details = new ArrayList<>(); final Map<String, int[]> byContent = new TreeMap<>();
        final Map<String, String> crossFiles = new TreeMap<>(), skippedCrossings = new TreeMap<>();   // [placed|would, present, protected, upgraded]
        int[] bc(Seg s) { return byContent.computeIfAbsent(contentOf(s), k -> new int[4]); }
        void pres(Seg s, int[] pf) { present++; pf[2]++; bc(s)[1]++; }
        BukkitTask task; Chunk chunk; volatile boolean done;
        Job(String id, CommandSender who, World w, int ix, int iy, int iz, int cx, int cz, Mode mode, Content content) {
            this.id = id; this.who = who; this.w = w; this.ix = ix; this.iy = iy; this.iz = iz; this.cx = cx; this.cz = cz; this.mode = mode; this.content = content;
        }
        /** async: use cached data or queue preparation (priority 0) and continue automatically when it finishes */
        void prepareOrLoad(int attempt) {
            if (done) return;
            JsonObject m = readManifest(cx, cz); Content need = missing(m, content);
            if (need != null && attempt > 0 && m != null && (!content.shells() || m.has("shells")) && (!content.roads() || m.has("roads")) && (!ovOn(content) || m.has("overpass"))) need = null;  // prepared once: report its result (incl. failures), no re-fetch loop
            if (need != null && attempt < 2) {
                PrepTask t = requestPrep(cx, cz, need, 0, who instanceof Player p ? p.getUniqueId() : null);
                if (t == null) { notes.add("Preparation refused (protected or queue full)."); Bukkit.getScheduler().runTask(ShellToolPlugin.this, this::finish); return; }
                stage = "preparing";
                msg(who, "§b[ShellTool] Chunk " + cx + "," + cz + ": " + (m == null ? "data NOT FETCHED YET" : "data incomplete/retry (" + need.arg() + ")")
                        + " - preparing (queue position " + (t.running ? "running" : String.valueOf(1 + queuePos(t))) + "). The build continues automatically.");
                long q0 = System.currentTimeMillis();
                t.done.thenRun(() -> {
                    queueWaitMs += Math.max(0, (t.started > 0 ? t.started : System.currentTimeMillis()) - q0); prepMs += System.currentTimeMillis() - Math.max(q0, t.started);
                    if (t.cancelled || done) { if (!done) { notes.add("Preparation cancelled."); Bukkit.getScheduler().runTask(ShellToolPlugin.this, this::finish); } return; }
                    msg(who, "§b[ShellTool] Chunk " + cx + "," + cz + " preparation finished (" + (System.currentTimeMillis() - q0) / 1000 + " s incl. queue).");
                    prepareOrLoad(attempt + 1);
                });
                return;
            }
            stage = "planning"; manifest = m;
            if (m == null) { notes.add("No prepared data (see cache prep-logs)."); Bukkit.getScheduler().runTask(ShellToolPlugin.this, this::finish); return; }
            if (content.roads()) loadRoads(m);                              // roads first: they only touch the surface, shells only air above it
            if (content.shells()) loadShells(m);
            if (ovOn(content)) loadOverpass(m);
            for (Seg s : segs) perFeature.computeIfAbsent(s.feature(), f -> new int[4])[0]++;
            Runnable go = () -> Bukkit.getScheduler().runTask(ShellToolPlugin.this, () -> {
                if (segs.isEmpty()) { finish(); return; }
                stage = "placing"; w.getChunkAtAsync(cx, cz).thenAccept(this::start);
            });
            if (crossFiles.isEmpty() || !ovOn(content)) { go.run(); return; }
            stage = "crossing preflight";                    // all blocks of each crossing, in all its chunks, before any is placed
            Map<String, CompletableFuture<Preflight>> pf = new TreeMap<>();
            for (Map.Entry<String, String> e : crossFiles.entrySet()) pf.put(e.getKey(), preflight(e.getKey(), e.getValue()));
            CompletableFuture.allOf(pf.values().toArray(new CompletableFuture[0])).thenRun(() -> Bukkit.getScheduler().runTask(ShellToolPlugin.this, () -> {
                for (Map.Entry<String, CompletableFuture<Preflight>> e : pf.entrySet()) {
                    Preflight r = e.getValue().join();
                    if (r.ok()) { notes.add(e.getKey() + ": " + r.summary()); continue; }
                    int n = 0; for (Iterator<Seg> it = segs.iterator(); it.hasNext(); ) if (it.next().feature().equals(e.getKey())) { it.remove(); n++; }
                    int[] f = perFeature.get(e.getKey()); if (f != null) f[3] += n;
                    crossingUnresolved.put(e.getKey(), e.getKey() + " " + r.summary() + " - nothing of this crossing placed"); notes.add(e.getKey() + " " + r.summary());
                }
                go.run();
            }));
        }
        private void loadShells(JsonObject m) {
            JsonObject s = m.has("shells") && m.get("shells").isJsonObject() ? m.getAsJsonObject("shells") : null;
            shellStatus = s == null ? "not prepared" : s.get("status").getAsString();
            if (s != null && s.has("message") && !s.get("message").isJsonNull()) notes.add("shells: " + s.get("message").getAsString());
            if (s != null && s.has("buildings")) for (JsonElement el : s.getAsJsonArray("buildings")) {
                JsonObject b = el.getAsJsonObject(); String fid = b.get("id").getAsString(), src = b.get("source").getAsString();
                if (src.equals("accepted-pilot") || acceptedIds.contains(fid)) { featureInfo.put(fid, "accepted pilot shell (plan_v1)"); continue; }
                try {
                    JsonObject r = gson.fromJson(Files.readString(new File(cacheDir, "buildings" + File.separator + fid + ".json").toPath()), JsonObject.class);
                    String st = r.get("status").getAsString(), nm = r.has("name") && !r.get("name").isJsonNull() ? r.get("name").getAsString() : "";
                    Set<String> other = new TreeSet<>();
                    for (JsonElement se : r.getAsJsonArray("segments")) {
                        JsonArray a = se.getAsJsonArray(); int x = a.get(0).getAsInt(), z = a.get(1).getAsInt();
                        if ((x >> 4) != cx || (z >> 4) != cz) { other.add((x >> 4) + "," + (z >> 4)); continue; }     // write limit: impact chunk only
                        segs.add(new Seg(x, z, a.get(2).getAsInt(), a.get(3).getAsInt(), a.get(4).getAsString(), a.get(5).getAsString().charAt(0), fid, a.get(6).getAsString(),
                                "shell [" + (r.has("height_source") ? r.get("height_source").getAsString() : "?") + "]"));
                    }
                    featureInfo.put(fid, (nm.isEmpty() ? "" : nm + ": ") + st + (r.has("height_source") ? " [height " + r.get("height_source").getAsString() + "]" : "")
                            + (r.has("message") ? " - " + r.get("message").getAsString() : "") + (other.isEmpty() ? " | complete within this chunk" : " | CONTINUES in chunk(s) " + String.join(" ", other)));
                } catch (Exception ex) { featureInfo.put(fid, "cache read error: " + ex.getMessage()); }
            }
            if (!"protected".equals(shellStatus)) for (Seg s2 : accepted.getOrDefault(key(cx, cz), List.of())) segs.add(s2);
        }
        private void loadRoads(JsonObject m) {
            JsonObject s = m.has("roads") && m.get("roads").isJsonObject() ? m.getAsJsonObject("roads") : null;
            roadStatus = s == null ? "not prepared" : s.get("status").getAsString();
            if (s != null && s.has("message") && !s.get("message").isJsonNull()) notes.add("roads: " + s.get("message").getAsString());
            if (s == null || !roadStatus.equals("ready")) return;
            try {
                JsonObject r = gson.fromJson(Files.readString(new File(cacheDir, "roads" + File.separator + cx + "_" + cz + ".json").toPath()), JsonObject.class);
                for (JsonElement fe : r.getAsJsonArray("features")) {
                    JsonObject f = fe.getAsJsonObject(); String nm = f.has("name") && !f.get("name").isJsonNull() ? f.get("name").getAsString() + " " : "";
                    featureInfo.put(f.get("id").getAsString(), nm + "(" + f.get("highway").getAsString() + ") width " + f.get("width_m").getAsString() + " m: " + f.get("width_source").getAsString()
                            + "; sidewalks " + f.get("sidewalks").getAsString() + "; markings " + f.getAsJsonArray("markings").toString());
                }
                for (JsonElement ue : r.getAsJsonArray("unsupported")) { JsonObject u = ue.getAsJsonObject(); unsupported.add(u.get("id").getAsString() + " " + u.get("highway").getAsString() + ": " + u.get("reason").getAsString()); }
                for (JsonElement ce : r.getAsJsonArray("cells")) {
                    JsonArray a = ce.getAsJsonArray(); int x = a.get(0).getAsInt(), z = a.get(1).getAsInt();
                    if ((x >> 4) != cx || (z >> 4) != cz) continue;
                    segs.add(new Seg(x, z, a.get(2).getAsInt(), a.get(2).getAsInt(), a.get(3).getAsString(), 'R', a.get(5).getAsString(), a.get(4).getAsString(), a.get(6).getAsString()));
                }
            } catch (Exception ex) { notes.add("road cache read error: " + ex.getMessage()); }
        }
        private void loadOverpass(JsonObject m) {
            JsonObject s = m.has("overpass") && m.get("overpass").isJsonObject() ? m.getAsJsonObject("overpass") : null;
            overpassStatus = s == null ? "not prepared" : s.get("status").getAsString();
            if (s != null && s.has("message") && !s.get("message").isJsonNull()) notes.add("overpass: " + s.get("message").getAsString());
            try {
                File f = new File(cacheDir, "overpass" + File.separator + cx + "_" + cz + ".json"); if (!f.exists()) return;
                JsonObject r = gson.fromJson(Files.readString(f.toPath()), JsonObject.class);
                for (JsonElement ce : r.getAsJsonArray("crossings")) {
                    JsonObject c = ce.getAsJsonObject(); crossings.add(c); String k = c.get("key").getAsString();
                    if (c.has("cells_file") && c.has("cells_here") && c.get("cells_here").getAsInt() > 0) crossFiles.put(k, c.get("cells_file").getAsString());
                    if (c.has("status") && !c.get("status").getAsString().equals("ready") && c.has("reasons") && c.getAsJsonArray("reasons").size() > 0)
                        skippedCrossings.put(k, c.get("status").getAsString() + ": " + c.getAsJsonArray("reasons").get(0).getAsString());
                    featureInfo.put(k, "crossing " + c.get("status").getAsString() + (c.has("deck") ? " | deck Y " + c.getAsJsonObject("deck").get("y_min") + "-" + c.getAsJsonObject("deck").get("y_max")
                            + " = world lower road Y + measured separation (vertical adjustment, not absolute elevation)" : "") + (c.has("reasons") ? " " + c.get("reasons") : ""));
                    if (c.has("deck_ends")) for (JsonElement de : c.getAsJsonArray("deck_ends")) { JsonObject d = de.getAsJsonObject();
                        details.add(k + " DECK END " + d.get("way").getAsString() + "/" + d.get("end").getAsString() + " x " + d.get("x") + " z " + d.get("z") + " Y " + d.get("deck_y") + ": " + d.get("status").getAsString()); }
                    if (c.has("approaches")) for (JsonElement ae : c.getAsJsonArray("approaches")) { JsonObject ap = ae.getAsJsonObject();
                        if (!"connected".equals(ap.get("status").getAsString())) details.add(k + " APPROACH " + ap.get("way").getAsString() + "/" + ap.get("end").getAsString() + ": " + ap.get("note").getAsString()); }
                    if (c.has("piers")) notes.add(k + " piers: " + c.getAsJsonObject("piers").get("status").getAsString());
                    if (c.has("provisional")) notes.add(k + " PROVISIONAL construction choices: " + c.get("provisional"));
                    if (c.has("outstanding")) for (JsonElement o : c.getAsJsonArray("outstanding")) notes.add(k + " OUTSTANDING: " + o.getAsString());
                }
                if (!"ready".equals(overpassStatus)) return;
                for (JsonElement ce : r.getAsJsonArray("cells")) {
                    JsonArray a = ce.getAsJsonArray(); int x = a.get(0).getAsInt(), z = a.get(1).getAsInt();
                    if ((x >> 4) != cx || (z >> 4) != cz) continue;                                  // write limit: impact chunk only
                    String role = a.get(5).getAsString();
                    segs.add(new Seg(x, z, a.get(2).getAsInt(), a.get(3).getAsInt(), a.get(4).getAsString(), OV_WALL_ROLES.contains(role) ? 'W' : 'F', a.get(6).getAsString(), role, a.get(7).getAsString()));
                }
            } catch (Exception ex) { notes.add("overpass cache read error: " + ex.getMessage()); }
        }
        /** main thread, chunk loaded: a crossing is placed in this chunk only if NONE of its cells conflicts (all-or-nothing per chunk) */
        private void precheckOverpass() {
            Set<Long> ent = new HashSet<>();
            for (org.bukkit.entity.Entity e : chunk.getEntities()) { Location l = e.getLocation(); for (int dy = 0; dy <= Math.ceil(e.getHeight()); dy++) ent.add(pos(l.getBlockX(), l.getBlockY() + dy, l.getBlockZ())); }
            Map<String, List<String>> bad = new TreeMap<>();
            for (Seg s : segs) {
                if (!OV_ROLES.contains(s.role())) continue;
                String why = cellConflict(w, s.x(), s.z(), s.y0(), s.y1(), data(s.block()), s.role(), s.mode(), ent);
                if (why != null) bad.computeIfAbsent(s.feature(), k -> new ArrayList<>()).add(why);
            }
            for (Map.Entry<String, List<String>> e : bad.entrySet()) {
                int[] n = {0}; segs.removeIf(s -> { boolean r = s.feature().equals(e.getKey()); if (r) n[0]++; return r; });
                int[] pf = perFeature.get(e.getKey()); if (pf != null) pf[3] += n[0];
                conflicts += e.getValue().size(); byContent.computeIfAbsent("overpass", k -> new int[4])[2] += e.getValue().size(); conflictsByReason.merge("overpass crossing UNRESOLVED in this chunk (conflict)", e.getValue().size(), Integer::sum);
                String msg = e.getKey() + " UNRESOLVED in chunk " + cx + "," + cz + ": " + e.getValue().size() + " conflicting cell(s); NOTHING of this crossing placed here. e.g. " + String.join("; ", e.getValue().subList(0, Math.min(5, e.getValue().size())));
                crossingUnresolved.put(e.getKey(), msg); notes.add(msg); conflictExamples.addAll(e.getValue().subList(0, Math.min(5, e.getValue().size())));
            }
            if (!bad.isEmpty() && segs.isEmpty()) overpassStatus = "unresolved (conflict)";
        }
        /** blocks this tool placed for a crossing under an EARLIER plan, absent from its current plan, still exactly as placed and not a
         *  tracked player edit: restored to the exact recorded before-state (a ledgered upgrade of the original job, undoable). */
        private void addRetirements() {
            Set<String> keys = new HashSet<>(); Set<Long> planned = new HashSet<>();
            for (Seg s : segs) if (OV_ROLES.contains(s.role())) { keys.add(s.feature()); for (int y = s.y0() - (s.mode() == 'W' ? 3 : 0); y <= s.y1(); y++) planned.add(pos(s.x(), y, s.z())); }
            if (keys.isEmpty()) return;
            List<Seg> add = new ArrayList<>();
            for (Map.Entry<Long, List<Placement>> e : byPos.entrySet()) {
                List<Placement> l = e.getValue(); Placement last = null;
                for (int i = l.size() - 1; i >= 0; i--) if (!undone.contains(l.get(i).job())) { last = l.get(i); break; }
                if (last == null || !keys.contains(last.feature()) || (last.x() >> 4) != cx || (last.z() >> 4) != cz || planned.contains(e.getKey())) continue;
                if (last.after().startsWith("minecraft:air") || last.after().startsWith("minecraft:cave_air")) continue;   // already absent (e.g. an earlier retirement): nothing to retire
                if (!last.world().equals(w.getName()) || tracked.contains(e.getKey())) continue;
                Block b = w.getBlockAt(last.x(), last.y(), last.z());
                if (!b.getBlockData().getAsString().equals(last.after()) || b.getState() instanceof TileState) continue;
                add.add(new Seg(last.x(), last.z(), last.y(), last.y(), last.before(), 'X', last.feature(), "retire", "block of an earlier plan of this crossing, absent from its current plan: restored to its exact before-state"));
            }
            for (Seg s : add) { segs.add(s); perFeature.computeIfAbsent(s.feature(), f -> new int[4])[0]++; }
            if (!add.isEmpty()) notes.add(add.size() + " block(s) of an earlier plan of this crossing retired (restored exactly; undoable)");
        }
        int queuePos(PrepTask t) { int n = 0; for (PrepTask o : prepQueue) if (o.compareTo(t) < 0) n++; return n; }
        void start(Chunk c) {
            if (done) return;
            chunk = c; c.addPluginChunkTicket(ShellToolPlugin.this);
            if (ovOn(content)) { precheckOverpass(); addRetirements(); if (segs.isEmpty()) { finish(); return; } }
            if (mode == Mode.APPLY) { JsonObject o = new JsonObject(); o.addProperty("type", "job"); o.addProperty("job", id);
                String owner = who instanceof Player p ? p.getUniqueId().toString() : "console";
                o.addProperty("owner", owner); o.addProperty("world", w.getName()); o.addProperty("cx", cx); o.addProperty("cz", cz); o.addProperty("content", content.arg()); log(o); flush(); jobOwner.put(id, owner); }
            task = Bukkit.getScheduler().runTaskTimer(ShellToolPlugin.this, this::tick, Math.max(1, applyDelayTicks), 1);
        }
        private void skip(Seg s, int y, String reason) {
            conflicts++; perFeature.get(s.feature())[3]++; bc(s)[2]++; conflictsByReason.merge(reason, 1, Integer::sum);
            if (conflictExamples.size() < 12) conflictExamples.add(s.x() + " " + y + " " + s.z() + " " + reason + " (" + s.feature() + ")");
        }
        /** upgradeOf: the job that owns the replaced block (controlled detail upgrade of tool-generated work), or null */
        private void write(Seg s, Block blk, BlockData cur, BlockData target, int y, int[] pf, String upgradeOf) {
            labels.merge(s.role() + ": " + s.label(), 1, Integer::sum); int[] c = bc(s); c[0]++; if (upgradeOf != null) c[3]++;
            if (mode == Mode.PREVIEW) { wouldPlace++; pf[1]++; return; }
            String before = cur.getAsString(); blk.setBlockData(target, false); placed++; pf[1]++;
            JsonObject o = new JsonObject(); o.addProperty("type", "place"); o.addProperty("job", id); o.addProperty("world", w.getName());
            o.addProperty("x", s.x()); o.addProperty("y", y); o.addProperty("z", s.z()); o.addProperty("before", before); o.addProperty("after", target.getAsString());
            o.addProperty("feature", s.feature()); o.addProperty("role", s.role()); o.addProperty("content", contentOf(s));
            if (upgradeOf != null) { o.addProperty("upgrade_of", upgradeOf); dependents.computeIfAbsent(upgradeOf, k -> new TreeSet<>()).add(id); }
            log(o);
            Placement pl = new Placement(id, w.getName(), s.x(), y, s.z(), before, target.getAsString(), s.feature());
            ledgerJobs.computeIfAbsent(id, k -> new ArrayList<>()).add(pl); byPos.computeIfAbsent(pos(s.x(), y, s.z()), k -> new ArrayList<>()).add(pl);
        }
        /** shell cell: AIR/CAVE_AIR only, no entity. 0 written, 1 present, 2 non-air stop, 3 blocked */
        private int airCell(Seg s, int y, BlockData target, int[] pf) {
            if (inStrip(s.x(), s.z())) { skip(s, y, "Strip exclusion"); return 3; }
            if (y < w.getMinHeight() || y >= w.getMaxHeight()) return 3;
            Block blk = w.getBlockAt(s.x(), y, s.z()); BlockData cur = blk.getBlockData();
            if (cur.equals(target)) return 1;
            Material m = cur.getMaterial(); String own = null;
            if (m != Material.AIR && m != Material.CAVE_AIR) {
                // crossing detail upgrade: only inside the segment's own volume, only a tool-owned block still exactly as generated,
                // never a tracked player edit or block entity. Shells stay strictly air-only.
                if (!OV_ROLES.contains(s.role()) || y < s.y0() || y > s.y1()) return 2;
                own = ownerOf(w, s.x(), y, s.z(), cur);
                boolean terrainAtTarget = s.role().equals("approach-surface") && GENERATOR_SURFACE.contains(m) && w.getBlockAt(s.x(), y + 1, s.z()).getType().isAir();
                if ((own == null && !terrainAtTarget) || tracked.contains(pos(s.x(), y, s.z())) || blk.getState() instanceof TileState) return 2;
            }
            if (!w.getNearbyEntities(BoundingBox.of(blk)).isEmpty()) { skip(s, y, "entity present"); return 3; }
            write(s, blk, cur, target, y, pf, own); return 0;
        }
        private void roadCell(Seg s, BlockData target, int[] pf) {
            int ey = s.y0();
            if (inStrip(s.x(), s.z())) { skip(s, ey, "Strip exclusion"); return; }
            int ys = Integer.MIN_VALUE;
            for (int y = ey + 2; y >= ey - 2; y--) if (!w.getBlockAt(s.x(), y, s.z()).getType().isAir()) { ys = y; break; }
            if (ys == Integer.MIN_VALUE) { skip(s, ey, "no surface within 2 blocks of expected terrain"); return; }
            Block blk = w.getBlockAt(s.x(), ys, s.z()); BlockData cur = blk.getBlockData();
            if (cur.equals(target)) { pres(s, pf); return; }
            if (!cur.getMaterial().isSolid() || cur.getMaterial().isOccluding() == false && !GENERATOR_SURFACE.contains(cur.getMaterial())) {
                skip(s, ys, "vegetation/object on the surface (" + cur.getMaterial().getKey().getKey() + "), not cleared"); return; }
            if (Math.abs(ys - ey) > 1) { skip(s, ys, "surface not at expected terrain height (fill/structure?)"); return; }
            String own = ownerOf(w, s.x(), ys, s.z(), cur);                  // tool-generated surface still exactly as generated
            if (!GENERATOR_SURFACE.contains(cur.getMaterial()) && own == null) { skip(s, ys, "surface " + cur.getMaterial().getKey().getKey() + " is not generator terrain or unchanged tool work (protected)"); return; }
            if (pilotRoads.contains(pos(s.x(), ys, s.z()))) { skip(s, ys, "accepted pilot road (protected)"); return; }
            if (tracked.contains(pos(s.x(), ys, s.z())) || tracked.contains(pos(s.x(), ys + 1, s.z()))) { skip(s, ys, "tracked player edit (protected)"); return; }
            Block above = w.getBlockAt(s.x(), ys + 1, s.z()); Material am = above.getType();
            if (am != Material.AIR && am != Material.CAVE_AIR) { skip(s, ys, "object/vegetation above surface (" + am.getKey().getKey() + "), not cleared"); return; }
            if (blk.getState() instanceof TileState) { skip(s, ys, "block entity"); return; }
            if (!w.getNearbyEntities(BoundingBox.of(above).expand(0, 0.9, 0)).isEmpty()) { skip(s, ys, "entity on surface"); return; }
            write(s, blk, cur, target, ys, pf, own);
        }
        void tick() {
            long t = System.nanoTime(), lim = (long) (placeMsPerTick * 1e6); int budget = blocksPerTick;
            while (idx < segs.size() && budget > 0 && System.nanoTime() - t < lim) {
                Seg s = segs.get(idx++); BlockData target = data(s.block()); int[] pf = perFeature.get(s.feature());
                if (s.mode() == 'R') { roadCell(s, target, pf); budget--; }
                else if (s.mode() == 'X') {                    // retire: re-checked in the same tick it is written
                    Block blk = w.getBlockAt(s.x(), s.y0(), s.z()); BlockData cur = blk.getBlockData(); String own = ownerOf(w, s.x(), s.y0(), s.z(), cur);
                    if (own != null && !tracked.contains(pos(s.x(), s.y0(), s.z())) && !(blk.getState() instanceof TileState)) write(s, blk, cur, target, s.y0(), pf, own);
                    else skip(s, s.y0(), "retire refused: block changed since placement (preserved)");
                    budget--; }
                else if (s.mode() == 'F') {
                    for (int y = s.y0(); y <= s.y1(); y++, budget--) { int r = airCell(s, y, target, pf); if (r == 1) { pres(s, pf); } else if (r == 2) skip(s, y, "non-air block (preserved)"); }
                } else if (s.mode() == 'W') {
                    for (int y = s.y1(); y >= s.y0() - 3; y--, budget--) {
                        int r = airCell(s, y, target, pf);
                        if (r == 1) { if (y >= s.y0()) { pres(s, pf); continue; } break; }
                        if (r == 2) { if (y > s.y0()) skip(s, y, "non-air block (preserved)"); break; }
                        if (r == 3) break;
                    }
                } else {
                    for (int y = s.y0() + 2; y >= s.y0() - 3; y--, budget--) {
                        Block b = w.getBlockAt(s.x(), y, s.z()); if (b.getType().isAir()) continue;
                        if (b.getBlockData().equals(target)) { pres(s, pf); break; }
                        if (y + 1 <= s.y0() + 2) { int r = airCell(s, y + 1, target, pf); if (r == 1) { pres(s, pf); } else if (r == 2) skip(s, y + 1, "non-air block (preserved)"); }
                        break;
                    }
                }
            }
            long dt = System.nanoTime() - t; placeNs += dt; maxTickNs = Math.max(maxTickNs, dt);
            if (mode == Mode.APPLY) flush();
            if (idx >= segs.size()) finish();
        }
        void cancel(String why) { if (!done) { notes.add("Cancelled: " + why); who.sendMessage("§e[ShellTool] Job " + id + " cancelled: " + why + " (" + placed + " placed blocks are in the ledger and undoable)."); finish(); } }
        void finish() {
            if (done) return; done = true; stage = "done";
            if (task != null) task.cancel();
            if (chunk != null) chunk.removePluginChunkTicket(ShellToolPlugin.this);
            activeChunks.remove(key(cx, cz));
            report();
        }
        void report() {
            JsonObject r = new JsonObject(); r.addProperty("job", id); r.addProperty("mode", mode.toString()); r.addProperty("content", content.arg());
            r.addProperty("impact", ix + " " + iy + " " + iz); r.addProperty("chunk", cx + "," + cz);
            r.addProperty("chunk_block_bounds", "x " + (cx << 4) + ".." + ((cx << 4) + 15) + ", z " + (cz << 4) + ".." + ((cz << 4) + 15) + ", all heights");
            r.addProperty("shells_status", shellStatus); r.addProperty("roads_status", roadStatus); r.addProperty("overpass_status", overpassStatus);
            if (ovOn(content)) { r.add("overpass_crossings", crossings); r.add("overpass_unresolved_in_chunk", gson.toJsonTree(crossingUnresolved)); r.add("overpass_skipped_nearby", gson.toJsonTree(skippedCrossings)); }
            r.add("by_content", gson.toJsonTree(byContent));
            if (manifest != null && manifest.has("data_source")) r.add("data_source", manifest.get("data_source"));
            JsonObject tm = new JsonObject(); tm.addProperty("queue_wait_s", queueWaitMs / 1000.0); tm.addProperty("preparation_s", prepMs / 1000.0);
            if (manifest != null && manifest.has("timings_s")) tm.add("prep_stages_s (last preparation)", manifest.get("timings_s"));
            tm.addProperty("placement_ms", Math.round(placeNs / 1e5) / 10.0); tm.addProperty("max_tick_ms", Math.round(maxTickNs / 1e5) / 10.0); tm.addProperty("total_s", (System.currentTimeMillis() - t0) / 1000.0);
            r.add("timings", tm); r.add("notes", gson.toJsonTree(notes)); r.add("unsupported", gson.toJsonTree(unsupported)); r.add("crossing_details", gson.toJsonTree(details));
            r.addProperty("cells_in_chunk", segs.size()); r.addProperty(mode == Mode.APPLY ? "placed" : "would_place", mode == Mode.APPLY ? placed : wouldPlace);
            r.addProperty("already_present", present); r.addProperty("skipped_protected_or_conflicting", conflicts);
            r.add("skipped_by_reason", gson.toJsonTree(conflictsByReason)); r.add("skip_examples", gson.toJsonTree(conflictExamples)); r.add("placed_by_role_and_provenance", gson.toJsonTree(labels));
            List<Map<String, Object>> bl = new ArrayList<>();
            Set<String> all = new TreeSet<>(featureInfo.keySet()); all.addAll(perFeature.keySet());
            for (String f : all) {
                int[] c = perFeature.getOrDefault(f, new int[4]); Map<String, Object> m = new LinkedHashMap<>();
                m.put("feature", f); m.put("info", featureInfo.getOrDefault(f, acceptedIds.contains(f) ? "accepted pilot shell (plan_v1)" : "")); m.put("cells_here", c[0]);
                m.put(mode == Mode.APPLY ? "placed" : "would_place", c[1]); m.put("already_present", c[2]); m.put("skipped", c[3]); bl.add(m);
            }
            r.add("features", gson.toJsonTree(bl));
            File dir = new File(getDataFolder(), "reports"); dir.mkdirs();
            try (Writer wr = Files.newBufferedWriter(new File(dir, "job-" + id + ".json").toPath(), StandardCharsets.UTF_8)) { new GsonBuilder().setPrettyPrinting().create().toJson(r, wr); }
            catch (IOException ex) { getLogger().warning("report write failed: " + ex); }
            StringBuilder sb = new StringBuilder();
            for (String c : List.of("shells", "roads", "overpass")) {
                if (!(c.equals("shells") ? content.shells() : c.equals("roads") ? content.roads() : ovOn(content))) continue;
                int[] v = byContent.getOrDefault(c, new int[4]); String st = c.equals("shells") ? shellStatus : c.equals("roads") ? roadStatus : overpassStatus;
                sb.append(sb.length() == 0 ? "" : " | ").append(c).append(" ").append(st).append(": ").append(v[0]).append(mode == Mode.APPLY ? " placed" : " would place")
                  .append(", ").append(v[1]).append(" present, ").append(v[2]).append(" protected").append(v[3] > 0 ? ", " + v[3] + " upgraded" : "");
            }
            who.sendMessage("§b[ShellTool] Job " + id + " DONE " + mode + " " + content.arg() + " chunk " + cx + "," + cz + " (prep " + prepMs / 1000 + " s)");
            who.sendMessage("§b  " + sb);
            int nu = unsupported.size() + crossingUnresolved.size();
            if (nu > 0) who.sendMessage("§6  " + nu + " unsupported/unresolved: " + shortWhy(!crossingUnresolved.isEmpty() ? crossingUnresolved.values().iterator().next() : unsupported.get(0)));
            if (!skippedCrossings.isEmpty() && crossingUnresolved.isEmpty()) who.sendMessage("§7  bridge not generated: " + shortWhy(skippedCrossings.values().iterator().next()));
            who.sendMessage("§7  Report: plugins/SummerlinShellTool/reports/job-" + id + ".json");
            getLogger().info("job " + id + " " + gson.toJson(r));
        }
    }
    static String shortWhy(String t) { return t.length() > 110 ? t.substring(0, 107) + "..." : t; }
    static final Set<Material> GENERATOR_SURFACE = EnumSet.of(Material.SAND, Material.RED_SAND, Material.GRASS_BLOCK, Material.DIRT, Material.COARSE_DIRT,
            Material.GRAVEL, Material.STONE, Material.SANDSTONE, Material.RED_SANDSTONE, Material.TERRACOTTA, Material.PODZOL, Material.GRAY_CONCRETE_POWDER, Material.BRICKS, Material.MOSS_BLOCK);

    // ---------------------------------------------------------------- undo
    private void undo(CommandSender s, String jobArg, boolean confirm) {
        String owner = s instanceof Player p ? p.getUniqueId().toString() : null;
        String job = jobArg;
        if (job == null) for (String j : ledgerJobs.keySet()) if (!undone.contains(j) && (owner == null || owner.equals(jobOwner.get(j)))) job = j;
        if (job == null || !ledgerJobs.containsKey(job)) { s.sendMessage("§e[ShellTool] No undoable job" + (jobArg == null ? "" : " '" + jobArg + "'") + "."); return; }
        if (owner != null && !owner.equals(jobOwner.get(job)) && !s.hasPermission(ADMIN)) { s.sendMessage("§c[ShellTool] That job is not yours."); return; }
        if (undone.contains(job)) { s.sendMessage("§e[ShellTool] Job " + job + " was already undone."); return; }
        List<String> live = new ArrayList<>(); for (String d : dependents.getOrDefault(job, Set.of())) if (!undone.contains(d)) live.add(d);
        if (!live.isEmpty()) { s.sendMessage("§e[ShellTool] Job " + job + " has later detail updates on its blocks (job(s) " + String.join(", ", live) + "). Undo those first, newest first; nothing changed."); return; }
        final String jb = job; List<Placement> ps = ledgerJobs.get(job); World w = Bukkit.getWorld(ps.get(0).world());
        Set<Long> chunks = new HashSet<>(); for (Placement p : ps) chunks.add(key(p.x() >> 4, p.z() >> 4));
        for (long k : chunks) if (activeChunks.containsKey(k)) { s.sendMessage("§e[ShellTool] A job is running in that chunk; try again after it finishes."); return; }
        List<Chunk> held = new ArrayList<>(); int[] restorable = {0}, modified = {0}, restored = {0};
        Runnable run = () -> {
            for (Placement p : ps) {
                Block b = w.getBlockAt(p.x(), p.y(), p.z());
                if (!b.getBlockData().getAsString().equals(p.after())) { modified[0]++; continue; }
                restorable[0]++;
                if (confirm) { b.setBlockData(Bukkit.createBlockData(p.before()), false); restored[0]++;
                    JsonObject o = new JsonObject(); o.addProperty("type", "undo"); o.addProperty("job", jb); o.addProperty("x", p.x()); o.addProperty("y", p.y()); o.addProperty("z", p.z()); log(o); }
            }
            if (confirm) { JsonObject o = new JsonObject(); o.addProperty("type", "undo-done"); o.addProperty("job", jb); o.addProperty("restored", restored[0]); o.addProperty("kept_modified", modified[0]); log(o); flush(); undone.add(jb); }
            for (Chunk c : held) c.removePluginChunkTicket(this);
            s.sendMessage("§b[ShellTool] Undo " + (confirm ? "" : "DRY RUN ") + "job " + jb + ": " + ps.size() + " recorded placements, "
                    + (confirm ? "restored " + restored[0] : "would restore " + restorable[0]) + " to their exact before-state, kept (changed since placement) " + modified[0]
                    + (confirm ? "" : ". Run '/shelltool undo " + jb + " confirm' to apply."));
        };
        List<CompletableFuture<Chunk>> fs = new ArrayList<>();
        for (long k : chunks) fs.add(w.getChunkAtAsync((int) (k >> 32), (int) k).thenApply(c -> { c.addPluginChunkTicket(this); held.add(c); return c; }));
        CompletableFuture.allOf(fs.toArray(new CompletableFuture[0])).thenRun(() -> Bukkit.getScheduler().runTask(this, run));
    }

    // ---------------------------------------------------------------- commands
    @Override public boolean onCommand(CommandSender s, Command c, String label, String[] a) {
        if (!s.hasPermission(USE) && !s.hasPermission(ADMIN)) { s.sendMessage("§c[ShellTool] No permission."); return true; }
        String sub = a.length == 0 ? "status" : a[0].toLowerCase(Locale.ROOT);
        UUID u = s instanceof Player p ? p.getUniqueId() : null;
        switch (sub) {
            case "on" -> { if (u == null) return say(s, "Players only."); say(s, setMode(u, Mode.APPLY) + ". Next snowball block hit builds in that chunk only."); }
            case "preview" -> { if (u == null) return say(s, "Players only."); say(s, setMode(u, Mode.PREVIEW)); }
            case "off" -> { if (u == null) return say(s, "Players only."); say(s, setMode(u, Mode.OFF) + ". Prefetch off."); }
            case "content" -> {
                if (u == null) return say(s, "Players only.");
                if (a.length < 2) return say(s, "content is " + contents.getOrDefault(u, Content.SHELLS).arg() + ". Use /shelltool content shells|roads|both" + (overpassEnabled ? "|overpass" : ""));
                try { Content ct = Content.valueOf(a[1].toUpperCase(Locale.ROOT));
                    if (ct.overpass() && !overpassEnabled) return say(s, "Overpass generation is disabled on this server (config overpass-enabled: false).");
                    contents.put(u, ct); say(s, "Content set. " + modeLine(u) + " (activation is separate: /shelltool preview | on | off)"); }
                catch (IllegalArgumentException ex) { say(s, "Use /shelltool content shells|roads|both"); } }
            case "prepare" -> {
                if (!(s instanceof Player p)) return say(s, "Players only.");
                int r = a.length > 1 ? Math.min(maxPrepareRadius, Math.max(0, Integer.parseInt(a[1]))) : 1;
                int n = prepareAround(p, p.getLocation().getBlockX() >> 4, p.getLocation().getBlockZ() >> 4, r, true);
                say(s, "Preparing up to " + n + " chunk(s) around you (radius " + r + ", max " + maxPrepareRadius + ") in the background. Nothing will be placed."); }
            case "prefetch" -> {
                if (u == null) return say(s, "Players only.");
                boolean on = a.length > 1 && a[1].equalsIgnoreCase("on"); if (on) prefetchOn.add(u); else prefetchOn.remove(u);
                say(s, "Background prefetch " + (on ? "ON (radius " + prefetchRadius + " around you as you move; builds always go first)" : "OFF")); }
            case "status" -> {
                String loc = ""; if (s instanceof Player p) { Location l = p.getLocation(); boolean cov = inPoly(coverPoly, l.getX(), l.getZ());
                    loc = " | here: chunk " + (l.getBlockX() >> 4) + "," + (l.getBlockZ() >> 4) + (chunkInStrip(l.getBlockX() >> 4, l.getBlockZ() >> 4) ? " PROTECTED (Strip)" : "")
                        + ", local OSM index " + (cov ? "covers this area" : "does NOT cover this area (Overpass fallback)") + ", cache " + cacheState(l.getBlockX() >> 4, l.getBlockZ() >> 4); }
                StringBuilder js = new StringBuilder(); for (Job j : activeChunks.values()) js.append(" [").append(j.id).append(" ").append(j.cx).append(",").append(j.cz).append(" ").append(j.stage).append("]");
                if (u != null) say(s, modeLine(u));
                say(s, "prefetch " + (u != null && prefetchOn.contains(u) ? "on" : "off")
                        + ", data " + (loaded ? "loaded" : "NOT loaded")
                    + ", overpass " + (overpassEnabled ? "ENABLED (" + overpassMode + (approachGrade > 0 ? ", approaches " + approachGrade : "") + ")" : "disabled")
                        + " | jobs " + activeChunks.size() + "/" + maxJobs + js + " | prep queue " + prepQueue.size() + "/" + maxPrepQueue + ", running " + prepByChunk.values().stream().filter(t -> t.running).count() + "/" + prepWorkers
                        + " | ledger jobs " + ledgerJobs.size() + " (" + undone.size() + " undone), placements " + ledgerJobs.values().stream().mapToInt(List::size).sum() + ", tracked edits " + tracked.size() + loc); }
            case "cancel" -> {
                int n = 0, q = 0;
                for (Job j : new ArrayList<>(activeChunks.values())) if (j.who == s || s.hasPermission(ADMIN)) { j.cancel("cancelled by " + s.getName()); n++; }
                for (PrepTask t : new ArrayList<>(prepByChunk.values())) if (s.hasPermission(ADMIN) || (u != null && u.equals(t.owner))) { t.cancel(); q++; }
                say(s, n + " job(s) and " + q + " preparation task(s) cancelled."); }
            case "undo" -> { String job = null; boolean confirm = false;
                for (int i = 1; i < a.length; i++) if (a[i].equalsIgnoreCase("confirm")) confirm = true; else job = a[i];
                undo(s, job, confirm); }
            case "sim" -> {
                if (!s.hasPermission(ADMIN)) return say(s, "Admin only.");
                if (a.length < 5) return say(s, "/shelltool sim <x> <y> <z> <preview|apply> [shells|roads|both]");
                Content ct = a.length > 5 ? Content.valueOf(a[5].toUpperCase(Locale.ROOT)) : Content.SHELLS;
                if (ct.overpass() && !overpassEnabled) return say(s, "Overpass generation is disabled on this server.");
                submit(s, Bukkit.getWorld(worldName), Integer.parseInt(a[1]), Integer.parseInt(a[2]), Integer.parseInt(a[3]), a[4].equalsIgnoreCase("apply") ? Mode.APPLY : Mode.PREVIEW, ct); }
            case "simset" -> {             // admin test hook: set a player's mode/content through the same code as their commands
                if (!s.hasPermission(ADMIN) || a.length < 4) return say(s, "/shelltool simset <uuid> <off|preview|on> <content>");
                UUID t = UUID.fromString(a[1]); Content ct = Content.valueOf(a[3].toUpperCase(Locale.ROOT));
                if (ct.overpass() && !overpassEnabled) return say(s, "Overpass generation is disabled on this server.");
                contents.put(t, ct); say(s, "simset " + t + ": " + setMode(t, a[2].equalsIgnoreCase("on") ? Mode.APPLY : a[2].equalsIgnoreCase("preview") ? Mode.PREVIEW : Mode.OFF)); }
            case "simthrow" -> {           // admin test hook: a snowball block hit by that player's CURRENT mode/content (console simulation, not a real throw)
                if (!s.hasPermission(ADMIN) || a.length < 5) return say(s, "/shelltool simthrow <uuid> <x> <y> <z>");
                UUID t = UUID.fromString(a[1]); if (!allowed.contains(t)) return say(s, "simthrow: " + t + " is not an allowed player");
                if (modes.getOrDefault(t, Mode.OFF) == Mode.OFF) return say(s, "simthrow: " + t + " has the tool OFF - nothing done (an ordinary snowball)");
                handleThrow(t, s, Bukkit.getWorld(worldName), Integer.parseInt(a[2]), Integer.parseInt(a[3]), Integer.parseInt(a[4])); }
            case "simprepare" -> {          // admin/console test hook: queue prefetch-priority preparation for a chunk
                if (!s.hasPermission(ADMIN) || a.length < 4) return say(s, "/shelltool simprepare <cx> <cz> <shells|roads|both>");
                Content pc = Content.valueOf(a[3].toUpperCase(Locale.ROOT)); if (pc.overpass() && !overpassEnabled) return say(s, "Overpass generation is disabled on this server.");
                PrepTask t = requestPrep(Integer.parseInt(a[1]), Integer.parseInt(a[2]), pc, 1, null);
                say(s, t == null ? "prepare refused (protected or queue full)" : "prepare queued for chunk " + a[1] + "," + a[2]); }
            default -> say(s, "/shelltool on|off|preview|content <shells|roads|both>|prepare [radius]|prefetch <on|off>|status|cancel|undo [job] [confirm]");
        }
        return true;
    }
    private String cacheState(int cx, int cz) {
        JsonObject m = readManifest(cx, cz); if (m == null) return "not prepared";
        return "shells " + (m.has("shells") && m.get("shells").isJsonObject() ? m.getAsJsonObject("shells").get("status").getAsString() : "-")
                + ", roads " + (m.has("roads") && m.get("roads").isJsonObject() ? m.getAsJsonObject("roads").get("status").getAsString() : "-")
                + ", overpass " + (m.has("overpass") && m.get("overpass").isJsonObject() ? m.getAsJsonObject("overpass").get("status").getAsString() : "-");
    }
    private boolean say(CommandSender s, String m) { s.sendMessage("§b[ShellTool] " + m); return true; }
    @Override public List<String> onTabComplete(CommandSender s, Command c, String l, String[] a) {
        if (a.length == 1) return List.of("on", "off", "preview", "content", "prepare", "prefetch", "status", "cancel", "undo");
        if (a.length == 2 && a[0].equalsIgnoreCase("content")) return overpassEnabled ? List.of("shells", "roads", "both", "overpass") : List.of("shells", "roads", "both");
        if (a.length == 2 && a[0].equalsIgnoreCase("prefetch")) return List.of("on", "off");
        return List.of();
    }
}
