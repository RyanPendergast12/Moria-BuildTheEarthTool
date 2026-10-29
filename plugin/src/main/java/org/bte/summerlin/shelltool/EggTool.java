package org.bte.summerlin.shelltool;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.bte.summerlin.shelltool.ShellToolPlugin.Mode;
import org.bte.summerlin.shelltool.ShellToolPlugin.Placement;
import org.bukkit.Bukkit;
import org.bukkit.Chunk;
import org.bukkit.ChunkSnapshot;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.Tag;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.block.BlockState;
import org.bukkit.block.TileState;
import org.bukkit.block.data.BlockData;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.TabExecutor;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.entity.Egg;
import org.bukkit.entity.Entity;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.ProjectileHitEvent;
import org.bukkit.event.player.PlayerEggThrowEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.world.ChunkLoadEvent;
import org.bukkit.scheduler.BukkitTask;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;

import static org.bte.summerlin.shelltool.ShellToolPlugin.key;
import static org.bte.summerlin.shelltool.ShellToolPlugin.pos;

/**
 * Egg tool (v0.5, docs/egg/ARCHITECTURE.md): a thrown egg that hits a BLOCK selects one tile of 4x4 chunks (64x64 blocks), prepares
 * (or reuses) cached terrain/vegetation/agriculture plans for it and previews or applies them inside that tile only, through the
 * plugin's ledger format (exact before/after, undoable). Every op is re-checked against the live world immediately before writing.
 */
final class EggTool implements Listener, TabExecutor {
    static final String EGG_PIPELINE = "egg_prep v0.5.0";
    static final List<String> KINDS = List.of("terrain", "vegetation", "agriculture");
    enum EContent { TERRAIN, VEGETATION, AGRICULTURE, ALL;
        List<String> list() { return this == ALL ? KINDS : List.of(arg()); }
        boolean covers(EContent c) { return this == ALL || this == c; }
        String arg() { return name().toLowerCase(Locale.ROOT); } }
    /** subsurface materials a 'base' op may change below base_y (§6) */
    static final Set<Material> NATURAL = EnumSet.of(Material.SAND, Material.RED_SAND, Material.SANDSTONE, Material.RED_SANDSTONE, Material.SMOOTH_SANDSTONE,
            Material.TERRACOTTA, Material.DIRT, Material.COARSE_DIRT, Material.GRASS_BLOCK, Material.GRAVEL, Material.STONE, Material.ANDESITE, Material.DIORITE,
            Material.GRANITE, Material.TUFF, Material.CLAY, Material.PODZOL);
    static boolean natural(Material m) { return NATURAL.contains(m) || (m.name().endsWith("_TERRACOTTA") && !m.name().contains("GLAZED")); }
    /** expect: a air, b base, e egg, R retire (restore `retire` to its before-state) */
    record Op(int x, int y, int z, char expect, String block, String feature, String role, String content, Placement retire) {}
    /** context.json (§5), arrays [64][64] indexed [z-z0][x-x0]; cover = base_cover as a list of states from base_y+1 upward (null = none) */
    record Ctx(Integer[][] baseY, String[][] baseBlock, int[][] protect, String[][][] cover) {}
    static Ctx readCtx(File f) {
        try {
            JsonObject cx = new Gson().fromJson(Files.readString(f.toPath()), JsonObject.class);
            JsonArray by = cx.getAsJsonArray("base_y"), bb = cx.getAsJsonArray("base_block"), pr = cx.getAsJsonArray("protect"), bc = cx.has("base_cover") ? cx.getAsJsonArray("base_cover") : null;
            Integer[][] y = new Integer[64][64]; String[][] b = new String[64][64]; int[][] p = new int[64][64]; String[][][] cv = new String[64][64][];
            for (int r = 0; r < 64; r++) for (int c = 0; c < 64; c++) {
                JsonElement e = by.get(r).getAsJsonArray().get(c), g = bb.get(r).getAsJsonArray().get(c), v = bc == null ? null : bc.get(r).getAsJsonArray().get(c);
                y[r][c] = e.isJsonNull() ? null : e.getAsInt(); b[r][c] = g.isJsonNull() ? null : g.getAsString(); p[r][c] = pr.get(r).getAsJsonArray().get(c).getAsInt();
                if (v != null && v.isJsonPrimitive()) cv[r][c] = new String[]{v.getAsString()};
                else if (v != null && v.isJsonArray()) { JsonArray a = v.getAsJsonArray(); cv[r][c] = new String[a.size()]; for (int i = 0; i < a.size(); i++) cv[r][c][i] = a.get(i).isJsonNull() ? null : a.get(i).getAsString(); }
            }
            return new Ctx(y, b, p, cv);
        } catch (Exception ex) { return null; }
    }

    final ShellToolPlugin pl;
    final boolean enabled, capture, generateMissing; final String prepScript, eventsFile, baselineWorld;
    final File capDir;                                                        // first-generation baseline of chunks generated after install
    final Map<Long, CompletableFuture<Void>> capturing = new ConcurrentHashMap<>();
    final Set<Long> notFullBefore = ConcurrentHashMap.newKeySet();             // chunks Egg saw as not FULL (isChunkGenerated false) right before requesting them
    final Map<Long, Boolean> absentBefore = new ConcurrentHashMap<>();          // protected regions: was the chunk absent from its region file before Egg requested it
    // converted/purchased-world regions: their proto chunks hold converted builds, so only chunks ABSENT before the load are captured there
    final Set<String> protRegions = new HashSet<>(), startRegions = new HashSet<>(); boolean protFailSafe; String protSource = "none";
    final ThreadPoolExecutor capExec = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new LinkedBlockingQueue<>(4096),
            r -> { Thread t = new Thread(r, "Egg-baseline-capture"); t.setDaemon(true); return t; });
    final int workers, maxJobs, timeoutS, maxCut, maxFill, maxOps, blocksPerTick, maxQueue; final long seed, cooldownMs; final double msPerTick;
    final File eggDir;
    final Map<UUID, Mode> modes = new HashMap<>();
    final Map<UUID, EContent> contents = new HashMap<>();
    final Map<UUID, Long> lastThrow = new HashMap<>();
    final Map<Long, EggJob> jobs = new LinkedHashMap<>();                     // tile key -> running job (main thread)
    final Map<Long, Set<Long>> tileIndex = new HashMap<>();                   // tile key -> positions of Egg placements (retirement union rule)
    final PriorityBlockingQueue<Prep> queue = new PriorityBlockingQueue<>();
    final Map<Long, Prep> preps = new ConcurrentHashMap<>();                  // one task per tile
    final List<Thread> threads = new ArrayList<>();
    final AtomicLong prepSeq = new AtomicLong();
    final Gson gson = new Gson();
    final Map<String, Optional<BlockData>> bd = new HashMap<>();
    volatile boolean stopping;

    EggTool(ShellToolPlugin pl) {
        this.pl = pl; FileConfiguration c = pl.getConfig();
        enabled = c.getBoolean("egg-enabled", true);
        prepScript = c.getString("egg-prep-script", "C:\\BTE-Migration\\repo\\Moria-BuildTheEarthTool\\prep\\egg_prep.py");
        eventsFile = c.getString("egg-events-file", ""); baselineWorld = c.getString("egg-baseline-world", "");
        workers = Math.max(1, Math.min(2, c.getInt("egg-prep-workers", 1)));
        maxJobs = Math.max(1, c.getInt("egg-max-jobs", 2)); timeoutS = c.getInt("egg-prep-timeout-seconds", 600);
        maxCut = c.getInt("egg-max-cut", 2); maxFill = c.getInt("egg-max-fill", 2); maxOps = c.getInt("egg-max-ops-per-tile", 12000);
        blocksPerTick = c.getInt("egg-blocks-per-tick", 300); msPerTick = c.getDouble("egg-place-ms-per-tick", 6.0);
        seed = c.getLong("egg-seed", 1); cooldownMs = c.getLong("egg-cooldown-ms", 3000); maxQueue = c.getInt("egg-max-prep-queue", 16);
        String[] rs = new File(new File(Bukkit.getWorldContainer(), pl.worldName), "region").list();
        if (rs != null) for (String n : rs) if (n.endsWith(".mca")) startRegions.add(n);
        String pr = c.getString("egg-capture-protected-regions", "egg-converted-regions.txt");
        if (pr != null && !pr.isBlank()) {
            File lf = new File(pr).isAbsolute() ? new File(pr) : new File(pl.getDataFolder(), pr); protSource = lf.getPath();
            try { for (String l : Files.readAllLines(lf.toPath(), StandardCharsets.UTF_8)) { int h = l.indexOf('#'); String n = (h < 0 ? l : l.substring(0, h)).trim(); if (!n.isEmpty()) protRegions.add(new File(n).getName()); } }
            catch (Exception ex) { protFailSafe = true; pl.getLogger().warning("egg-capture-protected-regions list " + lf + " missing/unreadable (" + ex + "): FAIL-SAFE - no baseline capture in any of the "
                    + startRegions.size() + " region(s) that existed at start"); }
        }
        capture = c.getBoolean("egg-capture-baseline", true); generateMissing = c.getBoolean("egg-generate-missing", true); capDir = new File(pl.getDataFolder(), "egg-baseline");
        eggDir = new File(pl.cacheDir, "egg");
        for (List<Placement> l : pl.byPos.values()) for (Placement p : l) if (p.content().startsWith("egg-")) index(p);
        if (enabled) for (int i = 0; i < workers; i++) { Thread t = new Thread(this::worker, "Egg-prep-" + i); t.setDaemon(true); t.start(); threads.add(t); }
        pl.getLogger().info("Egg tool " + (enabled ? "enabled" : "DISABLED") + ": pipeline " + EGG_PIPELINE + ", prep " + prepScript + ", egg placements indexed in " + tileIndex.size() + " tile(s)");
    }
    void stop() {
        stopping = true; capExec.shutdown();
        for (EggJob j : new ArrayList<>(jobs.values())) j.cancel("server stopping");
        for (Prep t : preps.values()) t.cancel();
        for (Thread t : threads) t.interrupt();
    }
    void index(Placement p) { tileIndex.computeIfAbsent(tileKey(p.x(), p.z()), k -> new HashSet<>()).add(pos(p.x(), p.y(), p.z())); }
    static long tileKey(int x, int z) { return key(Math.floorDiv(x >> 4, 4), Math.floorDiv(z >> 4, 4)); }
    static String bounds(int tx, int tz) { return "x " + 64 * tx + ".." + (64 * tx + 63) + ", z " + 64 * tz + ".." + (64 * tz + 63); }
    boolean tileInStrip(int tx, int tz) { for (int dx = 0; dx < 4; dx++) for (int dz = 0; dz < 4; dz++) if (pl.chunkInStrip(4 * tx + dx, 4 * tz + dz)) return true; return false; }
    BlockData parse(String s) {
        if (s == null) return null;
        return bd.computeIfAbsent(s, q -> { try { return Optional.of(Bukkit.createBlockData(q)); } catch (Exception e) { return Optional.empty(); } }).orElse(null);
    }
    static String str(JsonObject o, String k) { return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : null; }
    void msg(CommandSender s, String text) { Bukkit.getScheduler().runTask(pl, () -> s.sendMessage(text)); }
    void main(Runnable r) { if (!stopping) Bukkit.getScheduler().runTask(pl, r); }
    private boolean say(CommandSender s, String m) { s.sendMessage("§b[Egg] " + m); return true; }

    // ---------------------------------------------------------------- events
    @EventHandler public void onJoin(PlayerJoinEvent e) { modes.remove(e.getPlayer().getUniqueId()); }      // Egg mode is OFF after login
    /** the one hatching decision (event handler and simhatch): suppress while the thrower's Egg mode is ON or PREVIEW */
    boolean suppressHatch(UUID u) { return modes.getOrDefault(u, Mode.OFF) != Mode.OFF; }
    @EventHandler public void onEggThrow(PlayerEggThrowEvent e) {
        if (suppressHatch(e.getPlayer().getUniqueId())) { e.setHatching(false); e.setNumHatches((byte) 0); } }
    @EventHandler public void onHit(ProjectileHitEvent e) {
        if (!(e.getEntity() instanceof Egg eg) || !(eg.getShooter() instanceof Player p)) return;
        if (modes.getOrDefault(p.getUniqueId(), Mode.OFF) == Mode.OFF) return;
        if (!p.hasPermission(ShellToolPlugin.USE)) { modes.remove(p.getUniqueId()); return; }
        Block b = e.getHitBlock();
        hit(p.getUniqueId(), p, b == null ? null : b.getWorld(), b == null ? null : new int[]{b.getX(), b.getY(), b.getZ()});
    }
    /** the egg hit path (real hits and the simthrow/simentityhit hooks). xyz null = an entity was hit */
    void hit(UUID u, CommandSender who, World w, int[] xyz) {
        Mode m = modes.getOrDefault(u, Mode.OFF); if (m == Mode.OFF) return;
        if (xyz == null) { who.sendMessage("§e[Egg] Hit an entity, not a block - nothing done. A BLOCK hit selects the tile."); return; }
        long now = System.currentTimeMillis();
        if (now - lastThrow.getOrDefault(u, 0L) < cooldownMs) { who.sendMessage("§e[Egg] Cooldown - wait a moment."); return; }
        lastThrow.put(u, now);
        submit(u, who, w, xyz[0], xyz[1], xyz[2], m, contents.getOrDefault(u, EContent.ALL));
    }
    void submit(UUID u, CommandSender who, World w, int x, int y, int z, Mode mode, EContent content) {
        if (!enabled) { who.sendMessage("§c[Egg] The Egg tool is disabled (egg-enabled: false)."); return; }
        if (!pl.loaded) { who.sendMessage("§c[Egg] Data not loaded yet (Strip exclusion unknown) - refused."); return; }
        if (w == null || !w.getName().equals(pl.worldName)) { who.sendMessage("§c[Egg] Denied: only world '" + pl.worldName + "' is allowed."); return; }
        int tx = Math.floorDiv(x >> 4, 4), tz = Math.floorDiv(z >> 4, 4); long tk = key(tx, tz);
        if (tileInStrip(tx, tz)) { who.sendMessage("§c[Egg] PROTECTED: tile " + tx + "," + tz + " (" + bounds(tx, tz) + ") touches the Strip exclusion. Refused - nothing fetched or written."); return; }
        EggJob run = jobs.get(tk);
        if (run != null) { who.sendMessage("§e[Egg] Job " + run.id + " is already running for tile " + tx + "," + tz + " (" + run.stage + ")."); return; }
        for (int dx = 0; dx < 4; dx++) for (int dz = 0; dz < 4; dz++) { long k = key(4 * tx + dx, 4 * tz + dz);
            if (pl.regionLocks.containsKey(k) || pl.activeChunks.containsKey(k)) { who.sendMessage("§e[Egg] Tile " + tx + "," + tz + " is busy: a ShellTool job is running in chunk " + (4 * tx + dx) + "," + (4 * tz + dz) + ". Try again after it finishes."); return; } }
        if (jobs.size() >= maxJobs) { who.sendMessage("§e[Egg] Queue full (" + maxJobs + " active Egg jobs)."); return; }
        String id = String.format("%tY%<tm%<td-%<tH%<tM%<tS-e%d", new Date(), pl.seq.incrementAndGet());
        EggJob j = new EggJob(id, u, who, w, x, y, z, tx, tz, mode, content); jobs.put(tk, j);
        for (int dx = 0; dx < 4; dx++) for (int dz = 0; dz < 4; dz++) pl.regionLocks.put(key(4 * tx + dx, 4 * tz + dz), id);     // all 16 at once (main thread)
        who.sendMessage("§b[Egg] Job " + id + " (" + mode + ", " + content.arg() + "): tile " + tx + "," + tz + " (" + bounds(tx, tz) + ")");
        event(j, "queued");
        Bukkit.getScheduler().runTaskAsynchronously(pl, () -> j.prepareOrLoad(0));
    }

    // ---------------------------------------------------------------- baseline capture (provenance for chunks generated after install)
    /** a NEW chunk of the configured world that was not FULL before this load (absent or only a proto chunk on disk; converted/purchased
     *  chunks are stored FULL and never captured): snapshot now (main thread, before any other plugin), derive base_y/base_block/base_cover
     *  per column async. First capture wins. */
    @EventHandler(priority = EventPriority.LOWEST) public void onChunkLoad(ChunkLoadEvent e) {
        int cx = e.getChunk().getX(), cz = e.getChunk().getZ(); long k = key(cx, cz);
        boolean seenNotFull = notFullBefore.remove(k); Boolean wasAbsent = absentBefore.remove(k);
        if (!capture || stopping || !e.isNewChunk() || !e.getWorld().getName().equals(pl.worldName)) return;
        Chunk c = e.getChunk(); int minY = e.getWorld().getMinHeight(), maxY = e.getWorld().getMaxHeight();
        File f = new File(capDir, cx + "_" + cz + ".json"); if (f.exists()) return;
        int rule = regionRule(cx, cz);
        if (rule == 2) return;                                                 // fail-safe: a region that existed at start, no list
        if (rule == 1 && !(wasAbsent != null ? wasAbsent : offsetAbsent(e.getWorld().getWorldFolder(), cx, cz))) return;   // converted region: proto/full never
        if (rule == 0 && !seenNotFull && diskFull(e.getWorld().getWorldFolder(), cx, cz)) return;   // loaded by other means: trust only a non-FULL on-disk status
        ChunkSnapshot snap = c.getChunkSnapshot(true, false, false); CompletableFuture<Void> fut = new CompletableFuture<>();
        capturing.put(k, fut);
        try { capExec.execute(() -> { try { writeCapture(snap, f, cx, cz, minY, maxY); } catch (Exception ex) { pl.getLogger().warning("egg baseline capture " + cx + "," + cz + " failed: " + ex); }
                finally { capturing.remove(k, fut); fut.complete(null); } }); }
        catch (RejectedExecutionException ex) { capturing.remove(k, fut); fut.complete(null); pl.getLogger().warning("egg baseline capture queue full (4096) or stopping: chunk " + cx + "," + cz + " NOT captured"); }
    }
    static String regionName(int cx, int cz) { return "r." + (cx >> 5) + "." + (cz >> 5) + ".mca"; }
    /** 0 = normal rule (not FULL before), 1 = listed converted region (absent before only), 2 = never (fail-safe, region existed at start) */
    int regionRule(int cx, int cz) {
        String n = regionName(cx, cz);
        if (protFailSafe && startRegions.contains(n)) return 2;
        return protRegions.contains(n) ? 1 : 0;
    }
    /** true if the chunk has no entry in its region file's offset table (no file / no header = absent; I/O doubt = present) */
    static boolean offsetAbsent(File worldFolder, int cx, int cz) {
        File r = new File(worldFolder, "region" + File.separator + regionName(cx, cz));
        if (!r.exists()) return true;
        try (RandomAccessFile f = new RandomAccessFile(r, "r")) { if (f.length() < 8192) return true; f.seek(4L * ((cx & 31) + (cz & 31) * 32)); return f.readInt() == 0; }
        catch (IOException ex) { return false; }
    }
    String captureState() {
        return !capture ? "capture off" : "capture on, " + (protFailSafe ? "FAIL-SAFE (list " + protSource + " missing): no capture in " + startRegions.size() + " pre-existing region(s)"
                : protRegions.size() + " protected converted region(s) (absent chunks only)" + (protRegions.isEmpty() ? " - list " + protSource : ""));
    }
    /** true if the chunk is stored in its region file with Status full. Absent / proto = false. Unreadable or unknown = true (no capture). */
    static boolean diskFull(File worldFolder, int cx, int cz) {
        File r = new File(worldFolder, "region" + File.separator + "r." + (cx >> 5) + "." + (cz >> 5) + ".mca");
        if (!r.exists()) return false;
        try (RandomAccessFile f = new RandomAccessFile(r, "r")) {
            if (f.length() < 8192) return false;                               // no complete header: nothing stored yet
            f.seek(4L * ((cx & 31) + (cz & 31) * 32)); int e = f.readInt(); if (e == 0) return false;
            f.seek((long) (e >>> 8) * 4096); int len = f.readInt(); int type = f.readByte();
            if (len <= 1 || len > (1 << 24) || (type & 0x80) != 0) return true;   // external (.mcc) or odd entry: assume stored
            byte[] buf = new byte[len - 1]; f.readFully(buf);
            InputStream in = type == 1 ? new java.util.zip.GZIPInputStream(new ByteArrayInputStream(buf)) : type == 2 ? new java.util.zip.InflaterInputStream(new ByteArrayInputStream(buf)) : new ByteArrayInputStream(buf);
            String st = nbtString(in.readAllBytes(), "Status");
            return st == null || st.equals("full") || st.equals("minecraft:full");
        } catch (Exception ex) { return true; }
    }
    /** first NBT string tag with this name (TAG_String = 8, u16 name length, name, u16 value length, value) */
    static String nbtString(byte[] b, String name) {
        byte[] key = name.getBytes(StandardCharsets.UTF_8);
        outer: for (int i = 0; i + 5 + key.length <= b.length; i++) {
            if (b[i] != 8 || b[i + 1] != 0 || b[i + 2] != key.length) continue;
            for (int j = 0; j < key.length; j++) if (b[i + 3 + j] != key[j]) continue outer;
            int q = i + 3 + key.length, n = ((b[q] & 0xff) << 8) | (b[q + 1] & 0xff);
            return q + 2 + n <= b.length ? new String(b, q + 2, n, StandardCharsets.UTF_8) : null;
        }
        return null;
    }
    private void writeCapture(ChunkSnapshot s, File f, int cx, int cz, int minY, int maxY) throws IOException {
        Integer[][] by = new Integer[16][16]; String[][] bb = new String[16][16], cov = new String[16][16];
        for (int z = 0; z < 16; z++) for (int x = 0; x < 16; x++) {
            for (int y = Math.min(maxY - 1, s.getHighestBlockYAt(x, z)); y >= minY; y--) {     // from the top non-air block down to the first NATURAL one
                BlockData d = s.getBlockData(x, y, z); if (!natural(d.getMaterial())) continue;
                by[z][x] = y; bb[z][x] = d.getAsString();
                if (y + 1 < maxY) { BlockData a = s.getBlockData(x, y + 1, z); if (!a.getMaterial().isAir()) cov[z][x] = a.getAsString(); }
                break;
            }
        }
        JsonObject o = new JsonObject(); o.addProperty("version", "egg-baseline-1"); JsonArray ch = new JsonArray(); ch.add(cx); ch.add(cz); o.add("chunk", ch);
        o.addProperty("captured", java.time.Instant.now().toString()); o.addProperty("source", "new-chunk"); o.addProperty("world", pl.worldName);
        Gson g = new GsonBuilder().serializeNulls().create(); o.add("base_y", g.toJsonTree(by)); o.add("base_block", g.toJsonTree(bb)); o.add("base_cover", g.toJsonTree(cov));
        capDir.mkdirs(); if (f.exists()) return;
        Path tmp = Files.createTempFile(capDir.toPath(), cx + "_" + cz, ".tmp");
        try { Files.writeString(tmp, g.toJson(o), StandardCharsets.UTF_8); Files.move(tmp, f.toPath()); }   // no REPLACE_EXISTING: never overwrites an existing capture
        catch (IOException ex) { Files.deleteIfExists(tmp); if (!f.exists()) throw ex; }
    }
    /** main thread: load chunks ONE AT A TIME (at most one generation in flight), never while stopping or after abort();
     *  list entries are null for chunks not generated when gen is false */
    CompletableFuture<List<Chunk>> loadSeq(World w, List<Long> keys, boolean gen, BooleanSupplier abort) {
        CompletableFuture<List<Chunk>> r = new CompletableFuture<>(); step(w, keys, 0, gen, abort, new ArrayList<>(), r); return r;
    }
    private void step(World w, List<Long> keys, int i, boolean gen, BooleanSupplier abort, List<Chunk> out, CompletableFuture<List<Chunk>> r) {
        if (stopping || abort.getAsBoolean()) { r.completeExceptionally(new CancellationException("stopping or cancelled")); return; }
        if (i >= keys.size()) { r.complete(out); return; }
        long k = keys.get(i);
        if (gen && !w.isChunkGenerated((int) (k >> 32), (int) k)) notFullBefore.add(k);   // FULL-only check (CraftWorld 1.18.2), main thread, BEFORE generation
        if (gen && regionRule((int) (k >> 32), (int) k) == 1) absentBefore.put(k, offsetAbsent(w.getWorldFolder(), (int) (k >> 32), (int) k));
        w.getChunkAtAsync((int) (k >> 32), (int) k, gen).whenComplete((c, ex) -> {
            if (ex != null) { r.completeExceptionally(ex); return; }
            out.add(c);
            if (stopping) { r.completeExceptionally(new CancellationException("stopping")); return; }
            try { Bukkit.getScheduler().runTask(pl, () -> step(w, keys, i + 1, gen, abort, out, r)); } catch (Exception e) { r.completeExceptionally(e); }
        });
    }
    static List<Long> tileChunks(int tx, int tz) { List<Long> l = new ArrayList<>(); for (int dx = 0; dx < 4; dx++) for (int dz = 0; dz < 4; dz++) l.add(key(4 * tx + dx, 4 * tz + dz)); return l; }
    /** main thread: load all 16 tile chunks (generating missing ones if egg-generate-missing); completes once any baseline capture they triggered is on disk */
    CompletableFuture<Void> loadTile(World w, int tx, int tz, BooleanSupplier abort) {
        CompletableFuture<Void> r = new CompletableFuture<>();
        loadSeq(w, tileChunks(tx, tz), generateMissing, abort).whenComplete((v, ex) -> {
            List<CompletableFuture<Void>> caps = new ArrayList<>();
            for (long k : tileChunks(tx, tz)) { CompletableFuture<Void> c = capturing.get(k); if (c != null) caps.add(c); }
            CompletableFuture.allOf(caps.toArray(new CompletableFuture[0])).whenComplete((v2, ex2) -> { if (ex != null) r.completeExceptionally(ex); else r.complete(null); });
        });
        return r;
    }

    // ---------------------------------------------------------------- preparation (dedicated queue; throws prio 0 before /eggtool prepare prio 1)
    final class Prep implements Comparable<Prep> {
        final int tx, tz; final long seqNo = prepSeq.incrementAndGet(); volatile int prio; volatile EContent content; volatile UUID owner;
        volatile long started; volatile Process proc; volatile boolean cancelled, running; volatile String failure; Prep prev;
        final CompletableFuture<Void> done = new CompletableFuture<>();
        Prep(int tx, int tz, EContent c, int prio, UUID owner) { this.tx = tx; this.tz = tz; this.content = c; this.prio = prio; this.owner = owner; }
        public int compareTo(Prep o) { return prio != o.prio ? Integer.compare(prio, o.prio) : Long.compare(seqNo, o.seqNo); }
        void cancel() { cancelled = true; queue.remove(this); kill(proc); preps.remove(key(tx, tz), this); done.complete(null); }
    }
    /** the venv python.exe is a launcher: kill the real interpreter (descendants) first, then the launcher */
    static void kill(Process p) { if (p == null) return; p.descendants().forEach(ProcessHandle::destroyForcibly); p.destroyForcibly(); }
    /** queue (or join) preparation of a tile; content merges to 'all' while queued. null = refused (Strip or queue full). */
    synchronized Prep requestPrep(int tx, int tz, EContent c, int prio, UUID owner) {
        if (tileInStrip(tx, tz)) return null;
        long k = key(tx, tz); Prep t = preps.get(k);
        if (t != null && !t.cancelled) {
            if (!t.running) {
                if (!t.content.covers(c)) t.content = EContent.ALL;
                if (prio < t.prio) { queue.remove(t); t.prio = prio; t.owner = owner; queue.add(t); }
                return t;
            }
            if (t.content.covers(c)) return t;
        }
        if (prio > 0 && queue.size() >= maxQueue) return null;
        Prep n = new Prep(tx, tz, c, prio, owner); if (t != null && t.running) n.prev = t;       // never two preps of one tile at once
        preps.put(k, n); queue.add(n); return n;
    }
    private void worker() {
        while (!stopping) {
            Prep t;
            try { t = queue.take(); if (t.prev != null) t.prev.done.get(); } catch (InterruptedException e) { return; } catch (ExecutionException e) { continue; }
            if (t.cancelled) continue;
            t.running = true; t.started = System.currentTimeMillis();
            List<String> cmd = List.of(pl.prepPython, prepScript, pl.cacheDir.getPath(), String.valueOf(t.tx), String.valueOf(t.tz), "--content", t.content.arg(),
                    "--seed", String.valueOf(seed), "--max-cut", String.valueOf(maxCut), "--max-fill", String.valueOf(maxFill), "--max-ops", String.valueOf(maxOps));
            ProcessBuilder pb = new ProcessBuilder(cmd).redirectErrorStream(true);
            pb.environment().put("MORIA_EGG_CAPTURED", capDir.getAbsolutePath());
            if (!baselineWorld.isEmpty()) pb.environment().put("MORIA_BASELINE_WORLD", baselineWorld);
            File logf = new File(eggDir, "prep-logs" + File.separator + t.tx + "_" + t.tz + ".log"); logf.getParentFile().mkdirs(); pb.redirectOutput(logf);
            try {
                t.proc = pb.start();
                if (!t.proc.waitFor(timeoutS, TimeUnit.SECONDS)) { kill(t.proc); t.failure = "provider/timeout failure: preparation exceeded " + timeoutS + " s and was stopped";
                    pl.getLogger().warning("egg prep " + t.tx + "," + t.tz + " timed out after " + timeoutS + " s"); }
                else if (t.proc.exitValue() != 0 && !t.cancelled) t.failure = "provider failure: egg_prep exited with code " + t.proc.exitValue() + " (see " + logf + ")";
            } catch (InterruptedException ie) { kill(t.proc); return; }
            catch (Exception ex) { t.failure = "preparation could not start: " + ex; pl.getLogger().warning("egg prep could not start: " + ex); }
            finally { preps.remove(key(t.tx, t.tz), t); t.done.complete(null); }
        }
    }
    File tileDir(int tx, int tz) { return new File(eggDir, "tiles" + File.separator + tx + "_" + tz); }
    JsonObject readManifest(int tx, int tz) {
        File f = new File(tileDir(tx, tz), "manifest.json");
        try { return f.exists() ? gson.fromJson(Files.readString(f.toPath()), JsonObject.class) : null; } catch (Exception e) { return null; }
    }
    static boolean current(JsonObject m) { return m != null && EGG_PIPELINE.equals(str(m, "pipeline")); }
    static boolean sectionOk(JsonObject m, String c) {
        if (!m.has(c) || !m.get(c).isJsonObject()) return false;
        JsonObject s = m.getAsJsonObject(c); String st = str(s, "status");
        return st != null && !st.equals("provider_failure") && !(s.has("retry") && s.get("retry").getAsBoolean());
    }
    /** what still has to be prepared for `want`, or null when the cached manifest is current and has every section */
    static EContent missing(JsonObject m, EContent want) {
        if (!current(m)) return want;
        List<String> miss = new ArrayList<>(); for (String c : want.list()) if (!sectionOk(m, c)) miss.add(c);
        return miss.isEmpty() ? null : miss.size() == 1 ? EContent.valueOf(miss.get(0).toUpperCase(Locale.ROOT)) : EContent.ALL;
    }
    String cacheState(int tx, int tz) {
        JsonObject m = readManifest(tx, tz); if (m == null) return "not prepared";
        if (!current(m)) return "stale (pipeline " + str(m, "pipeline") + ", will re-prepare)";
        StringBuilder sb = new StringBuilder("prepared " + Objects.toString(str(m, "prepared"), "?"));
        for (String c : KINDS) sb.append(", ").append(c).append(" ").append(m.has(c) && m.get(c).isJsonObject() ? Objects.toString(str(m.getAsJsonObject(c), "status"), "?") : "-");
        return sb.toString();
    }

    /** B2: may this Egg placement be restored without burying or undermining later work? null = yes, else why it is kept.
     *  Every non-air cell in [y-1, y+40] (except the cell itself) must be Egg-owned by this/an older Egg job (or currentJob), the recorded
     *  base_cover, or natural ground below the cell; no tracked edit and no live non-Egg ledger placement may be in that window. */
    String restoreBlocked(World w, Placement p, String currentJob, Map<String, Integer> order, Map<Long, Optional<Ctx>> ctxs) {
        int x = p.x(), y = p.y(), z = p.z(), tx = Math.floorDiv(x >> 4, 4), tz = Math.floorDiv(z >> 4, 4);
        Ctx c = ctxs.computeIfAbsent(key(tx, tz), k -> Optional.ofNullable(readCtx(new File(tileDir(tx, tz), "context.json")))).orElse(null);
        Integer by = c == null ? null : c.baseY()[z - 64 * tz][x - 64 * tx]; String[] cov = c == null ? null : c.cover()[z - 64 * tz][x - 64 * tx];
        int po = order.getOrDefault(p.job(), Integer.MAX_VALUE);
        for (int yy = y - 1; yy <= y + 40; yy++) {
            if (yy == y || yy < w.getMinHeight() || yy >= w.getMaxHeight()) continue;
            long q = pos(x, yy, z);
            if (pl.tracked.contains(q)) return "tracked edit in column";
            List<Placement> l = pl.byPos.get(q);
            if (l != null) for (Placement r : l) if (!pl.undone.contains(r.job()) && !r.content().startsWith("egg-")) return "ShellTool work in column";
            BlockData d = w.getBlockAt(x, yy, z).getBlockData(); if (d.getMaterial().isAir()) continue;
            String own = pl.ownerOf(w, x, yy, z, d);
            if (own != null && pl.eggJobs.contains(own) && (own.equals(currentJob) || order.getOrDefault(own, Integer.MAX_VALUE) <= po)) continue;
            if (coverMatch(cov, by, yy, d) || (yy < y && natural(d.getMaterial()))) continue;
            return "later work depends on this column";
        }
        return null;
    }
    boolean coverMatch(String[] cov, Integer by, int y, BlockData d) {
        if (cov == null || by == null) return false; int i = y - by - 1; if (i < 0 || i >= cov.length || cov[i] == null) return false;
        BlockData c = parse(cov[i]); return c != null && (d.equals(c) || ShellToolPlugin.sameModuloTicks(c.getAsString(), d));
    }

    // ---------------------------------------------------------------- job
    static final class Cnt { String status = "not prepared"; JsonElement reasons, sources; int ops, outside, placed, would, present, skipped, deferred, retired; boolean incomplete;
        final Map<String, Integer> skippedBy = new TreeMap<>(); boolean usable() { return status.equals("ready") || status.equals("nothing_to_do"); } }
    final class EggJob {
        final String id; final UUID owner; final CommandSender who; final World w; final int ix, iy, iz, tx, tz, x0, z0; final Mode mode; final EContent content;
        volatile boolean done, cancelled; volatile String stage = "queued"; volatile String prepFailure;
        final long t0 = System.currentTimeMillis(); volatile long queueWaitMs, prepMs, planMs, loadMs; volatile boolean tileLoaded; long placeNs, maxTickNs;
        final Map<String, Cnt> per = new LinkedHashMap<>();
        final Map<String, JsonObject> feats = new LinkedHashMap<>();          // content|id -> plan feature
        final Map<String, int[]> fc = new HashMap<>();                         // content|id -> cells, placed/would, present, skipped, deferred
        final List<Op> ops = new ArrayList<>();
        final List<String> notes = new CopyOnWriteArrayList<>(), errors = new CopyOnWriteArrayList<>(), examples = new ArrayList<>();   // notes/errors: any thread
        volatile JsonObject manifest; Integer[][] baseY; String[][] baseBlock; int[][] protect; String[][][] cover;                   // per/feats/fc/ops/ctx: main thread only
        final Map<Long, Boolean> terrCache = new HashMap<>(); final Set<Long> touched = new HashSet<>();
        final List<Chunk> held = new ArrayList<>(); final Set<Long> ents = new HashSet<>(), tiles = new HashSet<>();
        final Map<Long, String> colCache = new HashMap<>(); final Map<String, Boolean> remoteOk = new HashMap<>();
        Map<String, Integer> jobOrder = Map.of(); final Map<Long, Optional<Ctx>> ctxOwn = new HashMap<>();
        BukkitTask task; int idx; final Set<Long> brokenCols = new HashSet<>();     // columns with a skipped op: later ops there are skipped too
        EggJob(String id, UUID owner, CommandSender who, World w, int ix, int iy, int iz, int tx, int tz, Mode mode, EContent content) {
            this.id = id; this.owner = owner; this.who = who; this.w = w; this.ix = ix; this.iy = iy; this.iz = iz; this.tx = tx; this.tz = tz;
            this.x0 = 64 * tx; this.z0 = 64 * tz; this.mode = mode; this.content = content;
            for (String c : content.list()) per.put(c, new Cnt());
        }
        /** async: reuse the cached manifest or prepare once (priority 0), then report whatever the preparation produced */
        void prepareOrLoad(int attempt) {
            if (done) return;
            JsonObject m = readManifest(tx, tz); EContent need = missing(m, content);
            if (need != null && attempt < 1 && !tileLoaded) {
                stage = "loading tile"; long l0 = System.currentTimeMillis();
                main(() -> loadTile(w, tx, tz, () -> done).whenComplete((v, ex) -> {
                    loadMs = System.currentTimeMillis() - l0; tileLoaded = true;
                    if (ex != null) errors.add("tile chunk load failed before preparation: " + ex.getMessage());
                    Bukkit.getScheduler().runTaskAsynchronously(pl, () -> prepareOrLoad(attempt));
                }));
                return;
            }
            if (need != null && attempt < 1) {
                Prep t = requestPrep(tx, tz, need, 0, owner);
                if (t == null) { notes.add("Preparation refused (protected or queue full)."); main(this::finish); return; }
                stage = "preparing"; long q0 = System.currentTimeMillis();
                msg(who, "§b[Egg] Tile " + tx + "," + tz + ": " + (m == null ? "not prepared yet" : !current(m) ? "cache from an older pipeline" : "cache incomplete (" + need.arg() + ")")
                        + " - preparing (" + (t.running ? "running" : "queue position " + (1 + queuePos(t))) + "). The job continues automatically.");
                t.done.thenRun(() -> {
                    long st = t.started > 0 ? t.started : System.currentTimeMillis();
                    queueWaitMs = Math.max(0, st - q0); prepMs = System.currentTimeMillis() - Math.max(q0, st);
                    if (t.failure != null) { prepFailure = t.failure; errors.add(t.failure); }
                    if (t.cancelled || done) { if (!done) { notes.add("Preparation cancelled."); main(this::finish); } return; }
                    Bukkit.getScheduler().runTaskAsynchronously(pl, () -> prepareOrLoad(attempt + 1));
                });
                return;
            }
            stage = "loading plans"; long p0 = System.currentTimeMillis();
            if (m != null && !current(m)) { errors.add("manifest pipeline '" + str(m, "pipeline") + "' != " + EGG_PIPELINE + " after preparation: not used"); m = null; }
            manifest = m; Runnable install = loadPlans(m); planMs = System.currentTimeMillis() - p0;
            main(() -> { if (done) return; install.run(); start(); });          // job collections are only touched on the main thread
        }
        /** async: read manifest sections, plans and context into local structures; the returned installer runs on the main thread */
        private Runnable loadPlans(JsonObject m) {
            File dir = tileDir(tx, tz);
            Map<String, Cnt> np = new LinkedHashMap<>(); Map<String, JsonObject> lf = new LinkedHashMap<>(); Map<String, int[]> lfc = new HashMap<>(); List<Op> lops = new ArrayList<>();
            for (String c : content.list()) {
                Cnt k = new Cnt(); np.put(c, k);
                JsonObject s = m != null && m.has(c) && m.get(c).isJsonObject() ? m.getAsJsonObject(c) : null;
                if (s == null) { if (prepFailure != null) { k.status = "provider_failure"; JsonArray r = new JsonArray(); r.add(prepFailure); k.reasons = r; } continue; }
                k.status = Objects.toString(str(s, "status"), "unknown"); k.reasons = s.get("reasons"); k.sources = s.has("sources") ? s.get("sources") : m.get("sources");
                if (s.has("complete") && !s.get("complete").getAsBoolean()) k.incomplete = true;
                if (!k.usable()) continue;
                try {
                    String pf = str(s, "plan"); File f = pf == null ? new File(dir, c + ".json") : new File(pf).isAbsolute() ? new File(pf) : new File(dir, pf);
                    if (!f.exists() && k.status.equals("nothing_to_do")) continue;
                    JsonObject p = gson.fromJson(Files.readString(f.toPath()), JsonObject.class);
                    String ps = Objects.toString(str(p, "status"), "unknown");
                    if (p.has("reasons")) k.reasons = p.get("reasons");
                    if (p.has("sources")) k.sources = p.get("sources");
                    if (p.has("complete") && !p.get("complete").getAsBoolean()) k.incomplete = true;
                    if (p.has("features")) for (JsonElement fe : p.getAsJsonArray("features")) { JsonObject fo = fe.getAsJsonObject(); lf.put(c + "|" + str(fo, "id"), fo); }
                    k.status = ps;
                    if (!ps.equals("ready")) continue;
                    List<Op> mine = new ArrayList<>();
                    for (JsonElement oe : p.getAsJsonArray("ops")) {
                        JsonArray a = oe.getAsJsonArray(); int x = a.get(0).getAsInt(), y = a.get(1).getAsInt(), z = a.get(2).getAsInt(); String ex = a.get(3).getAsString();
                        if (x < x0 || x > x0 + 63 || z < z0 || z > z0 + 63 || !(ex.equals("air") || ex.equals("base") || ex.equals("egg"))) { k.outside++; continue; }   // contract violation: never written
                        mine.add(new Op(x, y, z, ex.charAt(0), a.get(4).getAsString(), a.get(5).getAsString(), a.get(6).getAsString(), c, null));
                    }
                    if (k.outside > 0) errors.add(c + ": " + k.outside + " op(s) outside the tile or with an invalid expect - dropped (plan contract violation)");
                    if (mine.size() > maxOps) { k.status = "refused_limits"; JsonArray r = new JsonArray(); r.add(mine.size() + " ops > egg-max-ops-per-tile " + maxOps); k.reasons = r; continue; }
                    k.ops = mine.size(); lops.addAll(mine);
                    for (Op o : mine) lfc.computeIfAbsent(c + "|" + o.feature(), q -> new int[5])[0]++;
                } catch (Exception ex) { k.status = "plan_unreadable"; errors.add(c + " plan unreadable: " + ex.getMessage()); }
            }
            Ctx cx = null;
            if (np.values().stream().anyMatch(Cnt::usable)) { cx = readCtx(new File(dir, "context.json")); if (cx == null) errors.add("context.json missing or unreadable: every column ineligible, nothing retired"); }
            Ctx fcx = cx;
            return () -> { per.putAll(np); feats.putAll(lf); fc.putAll(lfc); ops.addAll(lops);
                if (fcx != null) { baseY = fcx.baseY(); baseBlock = fcx.baseBlock(); protect = fcx.protect(); cover = fcx.cover(); } };
        }
        int queuePos(Prep t) { int n = 0; for (Prep o : queue) if (o.compareTo(t) < 0) n++; return n; }

        /** main thread: hold all 16 tile chunks (+ already generated anchor chunks of partial features, read only), then place under the tick budget */
        void start() {
            if (done) return;
            if (per.values().stream().noneMatch(Cnt::usable)) { finish(); return; }
            stage = "loading chunks";
            List<Long> tile = tileChunks(tx, tz); Set<Long> anchors = new LinkedHashSet<>();
            for (JsonObject f : feats.values()) { int[] a = anchor(f); if (a != null && !tile.contains(key(a[0] >> 4, a[2] >> 4))) anchors.add(key(a[0] >> 4, a[2] >> 4)); }
            loadSeq(w, tile, generateMissing, () -> done).thenCompose(tc -> {
                for (int i = 0; i < tc.size(); i++) if (tc.get(i) == null) throw new CompletionException(new IllegalStateException("tile chunk " + (int) (tile.get(i) >> 32) + "," + (int) (long) tile.get(i) + " not generated (egg-generate-missing: false)"));
                hold(tc); return loadSeq(w, new ArrayList<>(anchors), false, () -> done);
            }).whenComplete((ac, ex) -> main(() -> {
                if (ex != null) { if (!done) { errors.add("chunk load failed: " + (ex.getCause() != null ? ex.getCause().getMessage() : ex.getMessage())); finish(); } return; }
                hold(ac); begin();
            }));
        }
        private void hold(List<Chunk> cs) { for (Chunk c : cs) if (c != null && !done) { c.addPluginChunkTicket(pl); held.add(c); } }
        void begin() {
            if (done) return;
            for (Chunk c : held) {
                if (Math.floorDiv(c.getX(), 4) != tx || Math.floorDiv(c.getZ(), 4) != tz) continue;
                for (Entity e : c.getEntities()) { Location l = e.getLocation(); for (int dy = 0; dy <= Math.ceil(e.getHeight()); dy++) ents.add(pos(l.getBlockX(), l.getBlockY() + dy, l.getBlockZ())); }
                for (BlockState s : c.getTileEntities()) if (s instanceof TileState) tiles.add(pos(s.getX(), s.getY(), s.getZ()));
            }
            if (baseY != null) addRetirements(); else if (per.values().stream().anyMatch(Cnt::usable)) notes.add("No context: nothing retired.");
            if (mode == Mode.APPLY) {
                String ow = owner != null ? owner.toString() : "console";
                JsonObject o = new JsonObject(); o.addProperty("type", "job"); o.addProperty("job", id); o.addProperty("owner", ow); o.addProperty("world", w.getName());
                JsonArray t = new JsonArray(); t.add(tx); t.add(tz); o.add("tile", t); o.addProperty("content", "egg-" + content.arg()); o.addProperty("tool", "egg");
                pl.log(o); pl.flush(); pl.jobOwner.put(id, ow); pl.eggJobs.add(id);
            }
            stage = "placing"; event(this, "running");
            task = Bukkit.getScheduler().runTaskTimer(pl, this::tick, 1, 1);
        }
        /** union rule (§6): every live Egg placement of an applied content anywhere in the tile that the new plan no longer holds is restored first */
        private void addRetirements() {
            List<Op> ret = new ArrayList<>(); Set<Long> idx = tileIndex.getOrDefault(key(tx, tz), Set.of());
            for (Map.Entry<String, Cnt> e : per.entrySet()) {
                String c = e.getKey(); if (!e.getValue().usable()) continue;    // missing/failed data is never "empty land": nothing retired
                if (e.getValue().incomplete) { notes.add(c + ": retirement skipped: incomplete plan"); continue; }
                Map<Long, Op> planned = new HashMap<>(); for (Op o : ops) if (o.content().equals(c)) planned.put(pos(o.x(), o.y(), o.z()), o);
                for (long p : idx) {
                    Placement l = latest(p);
                    if (l == null || !l.content().equals("egg-" + c) || l.role().equals("retire")) continue;
                    Op o = planned.get(p); BlockData tb = o == null ? null : parse(o.block());
                    if (o != null && (o.expect() == 'e' || o.block().equals(l.after()) || (tb != null && ShellToolPlugin.sameModuloTicks(l.after(), tb)))) continue;
                    ret.add(new Op(l.x(), l.y(), l.z(), 'R', l.before(), l.feature(), "retire", c, l));
                }
            }
            Map<String, Integer> order = new HashMap<>(); for (String j : pl.ledgerJobs.keySet()) order.put(j, order.size());
            Map<Placement, Integer> at = new IdentityHashMap<>(); Set<String> js = new HashSet<>(); for (Op o : ret) js.add(o.retire().job());
            for (String j : js) { List<Placement> l = pl.ledgerJobs.get(j); for (int i = 0; i < l.size(); i++) at.put(l.get(i), i); }
            ret.sort(Comparator.comparingInt((Op o) -> order.getOrDefault(o.retire().job(), 0)).thenComparingInt(o -> at.getOrDefault(o.retire(), 0)).reversed());   // newest first, like undo
            jobOrder = order;
            for (Op o : ret) fc.computeIfAbsent(o.content() + "|" + o.feature(), q -> new int[5])[0]++;
            ops.addAll(0, ret);
            if (!ret.isEmpty()) notes.add(ret.size() + " Egg block(s) of earlier plans absent from the current plan: retired first (restored exactly; undoable)");
        }
        Placement latest(long p) {
            List<Placement> l = pl.byPos.get(p); if (l == null) return null;
            for (int i = l.size() - 1; i >= 0; i--) { Placement q = l.get(i); if (!pl.undone.contains(q.job()) && q.world().equals(w.getName())) return q; }
            return null;
        }
        void tick() {
            long t = System.nanoTime(), lim = (long) (msPerTick * 1e6); int budget = blocksPerTick;
            colCache.clear(); terrCache.clear();                                // column checks are cached for this tick only
            while (idx < ops.size() && budget-- > 0 && System.nanoTime() - t < lim) apply(ops.get(idx++));
            if (!touched.isEmpty()) { pl.preflights.values().removeIf(q -> !Collections.disjoint(q.chunks(), touched)); touched.clear(); }   // cached Snowball crossing checks are stale
            long dt = System.nanoTime() - t; placeNs += dt; maxTickNs = Math.max(maxTickNs, dt);
            if (mode == Mode.APPLY) pl.flush();
            if (idx >= ops.size()) finish();
        }
        private void skip(Op o, Cnt k, int[] f, String why) {
            k.skipped++; f[3]++; k.skippedBy.merge(why, 1, Integer::sum); brokenCols.add(key(o.x(), o.z()));
            if (examples.size() < 12) examples.add(o.x() + " " + o.y() + " " + o.z() + " " + o.content() + "/" + o.role() + ": " + why + " (" + o.feature() + ")");
        }
        /** one op, re-checked against the live world immediately before it is written */
        private void apply(Op o) {
            Cnt k = per.get(o.content()); int[] f = fc.computeIfAbsent(o.content() + "|" + o.feature(), q -> new int[5]);
            if (o.y() < w.getMinHeight() || o.y() >= w.getMaxHeight()) { skip(o, k, f, "outside world height"); return; }
            long p = pos(o.x(), o.y(), o.z()); Block b = w.getBlockAt(o.x(), o.y(), o.z()); BlockData cur = b.getBlockData();
            if (o.expect() == 'R') {
                Placement l = o.retire();
                if (latest(p) != l) { skip(o, k, f, "retire: superseded since planning"); return; }
                if (!cur.getAsString().equals(l.after()) && !ShellToolPlugin.sameModuloTicks(l.after(), cur)) { skip(o, k, f, "retire refused: block changed since placement (preserved)"); return; }
                if (pl.tracked.contains(p) || tiles.contains(p)) { skip(o, k, f, "retire refused: tracked edit / block entity"); return; }
                if (pl.inStrip(o.x(), o.z())) { skip(o, k, f, "retire refused: Strip exclusion"); return; }
                if (ents.contains(p)) { skip(o, k, f, "retire refused: entity present"); return; }
                if ((protect[o.z() - z0][o.x() - x0] & ~allowedBits(o)) != 0) { skip(o, k, f, "retire refused: protected column (mask " + protect[o.z() - z0][o.x() - x0] + ")"); return; }
                if (restoreBlocked(w, l, id, jobOrder, ctxOwn) != null) { skip(o, k, f, "kept: later work depends on this column"); return; }
                BlockData tb = parse(l.before()); if (tb == null) { skip(o, k, f, "retire refused: invalid before-state"); return; }
                write(o, b, cur, tb, l.job().equals(id) ? null : l.job(), k, f); return;
            }
            if (brokenCols.contains(key(o.x(), o.z()))) { skip(o, k, f, "column incomplete: earlier op skipped"); return; }   // no crop on unprepared soil, no floating layers
            BlockData tgt = parse(o.block()); if (tgt == null) { skip(o, k, f, "invalid block state"); return; }
            String own = pl.ownerOf(w, o.x(), o.y(), o.z(), cur); boolean eggOwn = own != null && pl.eggJobs.contains(own);
            if (cur.equals(tgt) || (eggOwn && ShellToolPlugin.sameModuloTicks(tgt.getAsString(), cur))) { k.present++; f[2]++; return; }
            String why = column(o.x(), o.z()); if (why != null) { skip(o, k, f, why); return; }
            int bits = protect[o.z() - z0][o.x() - x0];
            if ((bits & ~allowedBits(o)) != 0) { skip(o, k, f, "protected column (mask " + bits + ")"); return; }
            if (!o.content().equals("terrain") && terrainTouched(o.x(), o.z())) { skip(o, k, f, "surface changed by Egg terrain; replan"); return; }   // plants/crops never float over a cut
            if (o.role().endsWith("-remote") && !remote(o)) { k.deferred++; f[4]++; return; }
            if (pl.tracked.contains(p)) { skip(o, k, f, "tracked player edit"); return; }
            if (tiles.contains(p)) { skip(o, k, f, "block entity"); return; }
            if (ents.contains(p)) { skip(o, k, f, "entity present"); return; }
            Placement last = latest(p);
            if (last != null && !last.content().startsWith("egg-")) { skip(o, k, f, "ShellTool work (never written by Egg)"); return; }
            switch (o.expect()) {
                case 'a' -> { if (cur.getMaterial() != Material.AIR && cur.getMaterial() != Material.CAVE_AIR) { skip(o, k, f, "not air (preserved)"); return; }
                    int by = baseY[o.z() - z0][o.x() - x0];
                    if (o.content().equals("terrain") ? o.y() <= by || o.y() > by + maxFill : o.y() <= by) { skip(o, k, f, "air op outside its allowed range above base_y"); return; } }
                case 'b' -> {
                    if (eggOwn && !last.role().equals("retire")) { skip(o, k, f, "changed by an earlier Egg job (not baseline)"); return; }
                    int by = baseY[o.z() - z0][o.x() - x0];
                    if (o.y() > by || o.y() < by - maxCut) { skip(o, k, f, "base op outside base_y-maxCut..base_y"); return; }
                    if (o.y() == by ? !cur.equals(parse(baseBlock[o.z() - z0][o.x() - x0])) : !natural(cur.getMaterial())) { skip(o, k, f, o.y() == by ? "surface differs from baseline" : "not natural terrain (preserved)"); return; }
                }
                default -> { if (!eggOwn) { skip(o, k, f, "no longer as an Egg job placed it (preserved)"); return; } }
            }
            write(o, b, cur, tgt, own != null && !own.equals(id) ? own : null, k, f);
        }
        private void write(Op o, Block b, BlockData cur, BlockData tgt, String upgradeOf, Cnt k, int[] f) {
            if (o.expect() == 'R') k.retired++; else { if (mode == Mode.APPLY) k.placed++; else k.would++; f[1]++; }
            if (mode == Mode.PREVIEW) return;                                    // preview: nothing written, nothing ledgered
            b.setBlockData(tgt, false); touched.add(key(o.x() >> 4, o.z() >> 4));
            index(pl.logPlace(id, w, o.x(), o.y(), o.z(), cur.getAsString(), tgt.getAsString(), o.feature(), o.role(), "egg-" + o.content(), upgradeOf));
        }
        /** §5 column check (Strip, base_y, nothing foreign in the window, surface still at baseline or Egg-owned); protect bits are per op */
        String column(int x, int z) {
            String r = colCache.computeIfAbsent(key(x, z), q -> { String s = column0(x, z); return s == null ? "" : s; });
            return r.isEmpty() ? null : r;
        }
        private String column0(int x, int z) {
            if (baseY == null) return "no context (provenance unknown)";
            if (pl.inStrip(x, z)) return "Strip exclusion";
            Integer by = baseY[z - z0][x - x0]; if (by == null) return "no baseline (base_y unknown)";
            for (int y = by - maxCut - 1; y <= by + 40; y++) { long p = pos(x, y, z);
                if (pl.tracked.contains(p)) return "tracked player edit in column";
                if (tiles.contains(p)) return "block entity in column";
                if (ents.contains(p)) return "entity in column";
                List<Placement> l = pl.byPos.get(p);
                if (l != null) for (Placement q : l) if (!pl.undone.contains(q.job()) && !q.content().startsWith("egg-")) return "ShellTool placement in column";
            }
            String[] cov = cover[z - z0][x - x0];
            for (int y = by + 1; y <= by + 40 && y < w.getMaxHeight(); y++) {    // above the ground: air, the recorded base_cover, or Egg work only (untracked builds count)
                BlockData d = w.getBlockAt(x, y, z).getBlockData(); if (d.getMaterial().isAir() || coverMatch(cov, by, y, d)) continue;
                String own = pl.ownerOf(w, x, y, z, d); if (own == null || !pl.eggJobs.contains(own)) return "column changed above the ground since baseline";
            }
            BlockData cur = w.getBlockAt(x, by, z).getBlockData(), bb = parse(baseBlock[z - z0][x - x0]);
            if (bb == null || !cur.equals(bb)) { String own = pl.ownerOf(w, x, by, z, cur); if (own == null || !pl.eggJobs.contains(own)) return "surface changed since baseline"; }
            return null;
        }
        int allowedBits(Op o) { return o.content().equals("vegetation") && "mapped".equals(str(feats.get(o.content() + "|" + o.feature()), "confidence")) ? 32 | 64 : 0; }
        /** M6: a live Egg terrain placement anywhere in the column window: vegetation/agriculture plans assumed the baseline surface */
        boolean terrainTouched(int x, int z) {
            return terrCache.computeIfAbsent(key(x, z), q -> { int by = baseY[z - z0][x - x0];
                for (int y = by - maxCut - 1; y <= by + 40; y++) { Placement l = latest(pos(x, y, z)); if (l != null && l.content().equals("egg-terrain") && !l.role().equals("retire")) return true; }
                return false; });
        }
        /** feature anchor [x,y,z] floored, or null (y may be null in plans: then x/z only, y = MIN_VALUE) */
        int[] anchor(JsonObject f) {
            if (f == null || !f.has("anchor") || !f.get("anchor").isJsonArray()) return null; JsonArray a = f.getAsJsonArray("anchor");
            if (a.size() < 3 || a.get(0).isJsonNull() || a.get(2).isJsonNull()) return null;
            return new int[]{(int) Math.floor(a.get(0).getAsDouble()), a.get(1).isJsonNull() ? Integer.MIN_VALUE : (int) Math.floor(a.get(1).getAsDouble()), (int) Math.floor(a.get(2).getAsDouble())};
        }
        /** '*-remote' ops: only when the feature's anchor (outside the tile, read only) already exists in the planned state */
        private boolean remote(Op o) {
            return remoteOk.computeIfAbsent(o.content() + "|" + o.feature(), q -> {
                JsonObject f = feats.get(q); int[] a = anchor(f); if (a == null) return false; int ax = a[0], ay = a[1], az = a[2];
                if (!w.isChunkLoaded(ax >> 4, az >> 4) || ay < w.getMinHeight() || ay >= w.getMaxHeight()) return false;
                BlockData cur = w.getBlockAt(ax, ay, az).getBlockData(); String want = str(f, "anchor_block");
                if (want != null) { BlockData wb = parse(want); return wb != null && (cur.equals(wb) || ShellToolPlugin.sameModuloTicks(wb.getAsString(), cur)); }
                return Tag.LOGS.isTagged(cur.getMaterial());                    // no anchor_block given: the trunk must be standing
            });
        }
        void cancel(String why) {
            if (done) return; cancelled = true; notes.add("Cancelled: " + why);
            who.sendMessage("§e[Egg] Job " + id + " cancelled: " + why + (mode == Mode.APPLY ? " (written blocks are in the ledger and undoable)." : "."));
            finish();
        }
        void finish() {
            if (done) return; done = true; stage = "done";
            if (task != null) task.cancel();
            for (Chunk c : held) c.removePluginChunkTicket(pl); held.clear();
            jobs.remove(key(tx, tz), this);
            for (int dx = 0; dx < 4; dx++) for (int dz = 0; dz < 4; dz++) pl.regionLocks.remove(key(4 * tx + dx, 4 * tz + dz), id);
            if (mode == Mode.APPLY) pl.flush();
            report();
            event(this, cancelled || per.values().stream().noneMatch(Cnt::usable) ? "failed" : "completed");
        }
        void report() {
            boolean ap = mode == Mode.APPLY;
            JsonObject r = new JsonObject(); r.addProperty("job", id); r.addProperty("tool", "egg"); r.addProperty("mode", mode.toString()); r.addProperty("content", content.arg());
            r.addProperty("cancelled", cancelled); r.addProperty("impact", ix + " " + iy + " " + iz);
            JsonArray t = new JsonArray(); t.add(tx); t.add(tz); r.add("tile", t);
            JsonObject b = new JsonObject();
            b.add("chunks", gson.toJsonTree(Map.of("minX", 4 * tx, "minZ", 4 * tz, "maxX", 4 * tx + 3, "maxZ", 4 * tz + 3)));
            b.add("blocks", gson.toJsonTree(Map.of("minX", x0, "minZ", z0, "maxX", x0 + 63, "maxZ", z0 + 63)));
            r.add("bounds", b);
            r.addProperty("pipeline", manifest != null ? str(manifest, "pipeline") : null); r.addProperty("prepared", manifest != null ? str(manifest, "prepared") : null);
            JsonObject cs = new JsonObject();
            for (Map.Entry<String, Cnt> e : per.entrySet()) { Cnt k = e.getValue(); JsonObject o = new JsonObject();
                o.addProperty("status", k.status); o.add("reasons", k.reasons); o.add("sources", k.sources); o.addProperty("ops", k.ops); o.addProperty("ops_outside_tile", k.outside);
                o.addProperty(ap ? "placed" : "would_place", ap ? k.placed : k.would); o.addProperty("already_present", k.present); o.addProperty("skipped", k.skipped);
                o.add("skipped_by_reason", gson.toJsonTree(k.skippedBy)); o.addProperty("deferred", k.deferred); o.addProperty(ap ? "retired" : "would_retire", k.retired);
                cs.add(e.getKey(), o); }
            r.add("contents", cs);
            JsonArray fa = new JsonArray(); Set<String> all = new TreeSet<>(feats.keySet()); all.addAll(fc.keySet());
            for (String q : all) { JsonObject fo = feats.containsKey(q) ? feats.get(q).deepCopy() : new JsonObject(); int[] c = fc.getOrDefault(q, new int[5]);
                fo.addProperty("content", q.substring(0, q.indexOf('|'))); if (!fo.has("id")) fo.addProperty("id", q.substring(q.indexOf('|') + 1));
                fo.addProperty("cells", c[0]); fo.addProperty(ap ? "placed" : "would_place", c[1]); fo.addProperty("already_present", c[2]); fo.addProperty("skipped", c[3]); fo.addProperty("deferred", c[4]);
                fa.add(fo); }
            r.add("features", fa);
            JsonObject tm = new JsonObject(); tm.addProperty("tile_load_and_capture_ms", loadMs); tm.addProperty("queue_wait_s", queueWaitMs / 1000.0); tm.addProperty("preparation_s", prepMs / 1000.0); tm.addProperty("plan_load_ms", planMs);
            if (manifest != null && manifest.has("timings")) tm.add("prep_stages (last preparation)", manifest.get("timings"));
            tm.addProperty("placement_ms", Math.round(placeNs / 1e5) / 10.0); tm.addProperty("max_tick_ms", Math.round(maxTickNs / 1e5) / 10.0); tm.addProperty("total_s", (System.currentTimeMillis() - t0) / 1000.0);
            r.add("timings", tm); r.add("notes", gson.toJsonTree(notes)); r.add("errors", gson.toJsonTree(errors)); r.add("skip_examples", gson.toJsonTree(examples));
            File dir = new File(pl.getDataFolder(), "egg-reports"); dir.mkdirs();
            try (Writer wr = Files.newBufferedWriter(new File(dir, "egg-" + id + ".json").toPath(), StandardCharsets.UTF_8)) { new GsonBuilder().serializeNulls().setPrettyPrinting().create().toJson(r, wr); }
            catch (IOException ex) { pl.getLogger().warning("egg report write failed: " + ex); }
            StringBuilder sb = new StringBuilder();
            for (Map.Entry<String, Cnt> e : per.entrySet()) { Cnt k = e.getValue(); sb.append(sb.length() == 0 ? "" : " | ").append(e.getKey()).append(" ").append(k.status);
                if (k.usable()) sb.append(": ").append(ap ? k.placed + " placed" : k.would + " would place").append(", ").append(k.present).append(" present, ").append(k.skipped).append(" skipped")
                        .append(k.deferred > 0 ? ", " + k.deferred + " deferred" : "").append(k.retired > 0 ? ", " + k.retired + (ap ? " retired" : " would retire") : ""); }
            who.sendMessage("§b[Egg] Job " + id + (cancelled ? " CANCELLED " : " DONE ") + mode + " " + content.arg() + " tile " + tx + "," + tz + " (" + bounds(tx, tz) + ")");
            who.sendMessage("§b  " + sb + (errors.isEmpty() ? "" : " §6| " + ShellToolPlugin.shortWhy(errors.get(0))));
            who.sendMessage("§7  Report: plugins/SummerlinShellTool/egg-reports/egg-" + id + ".json");
            pl.getLogger().info("egg job " + id + " " + gson.toJson(r));
        }
    }

    /** BlueMap status line (egg-events-file, JSONL). The plugin never touches BlueMap itself. */
    synchronized void event(EggJob j, String status) {
        if (eventsFile == null || eventsFile.isEmpty()) return;
        JsonObject o = new JsonObject(); o.addProperty("tool", "egg"); o.addProperty("id", j.id); o.addProperty("status", status);
        o.addProperty("minX", j.x0); o.addProperty("minZ", j.z0); o.addProperty("maxX", j.x0 + 63); o.addProperty("maxZ", j.z0 + 63);
        o.addProperty("label", j.content.arg() + " " + j.mode.toString().toLowerCase(Locale.ROOT)); o.addProperty("t", System.currentTimeMillis() / 1000);
        try (Writer wr = Files.newBufferedWriter(Path.of(eventsFile), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND)) { wr.write(gson.toJson(o)); wr.write('\n'); }
        catch (Exception e) { pl.getLogger().warning("egg events file not written: " + e); }
    }

    // ---------------------------------------------------------------- commands
    String modeLine(UUID u) {
        Mode m = modes.getOrDefault(u, Mode.OFF);
        return "Egg mode " + (m == Mode.OFF ? "OFF (eggs are vanilla)" : m == Mode.PREVIEW ? "PREVIEW (reports only, places nothing)" : "ON (places blocks)")
                + " | content " + contents.getOrDefault(u, EContent.ALL).arg();
    }
    String setMode(UUID u, Mode m) { if (m == Mode.OFF) modes.remove(u); else modes.put(u, m); return modeLine(u); }
    static Mode modeArg(String s) { return s.equalsIgnoreCase("on") ? Mode.APPLY : s.equalsIgnoreCase("preview") ? Mode.PREVIEW : Mode.OFF; }
    static EContent contentArg(String s) { return EContent.valueOf(s.toUpperCase(Locale.ROOT)); }

    @Override public boolean onCommand(CommandSender s, Command c, String label, String[] a) {
        if (!s.hasPermission(ShellToolPlugin.USE) && !s.hasPermission(ShellToolPlugin.ADMIN)) { s.sendMessage("§c[Egg] No permission."); return true; }
        String sub = a.length == 0 ? "status" : a[0].toLowerCase(Locale.ROOT);
        UUID u = s instanceof Player p ? p.getUniqueId() : null;
        boolean admin = s.hasPermission(ShellToolPlugin.ADMIN);
        try {
            switch (sub) {
                case "on", "preview", "off" -> {
                    if (u == null) return say(s, "Players only.");
                    if (!enabled && !sub.equals("off")) return say(s, "The Egg tool is disabled on this server (egg-enabled: false).");
                    say(s, setMode(u, modeArg(sub)) + (sub.equals("off") ? "" : ". Throw an egg at a BLOCK: it selects that 64x64 tile; eggs do not hatch while Egg mode is on."));
                }
                case "content" -> {
                    if (u == null) return say(s, "Players only.");
                    if (a.length < 2) return say(s, "content is " + contents.getOrDefault(u, EContent.ALL).arg() + ". Use /eggtool content terrain|vegetation|agriculture|all");
                    contents.put(u, contentArg(a[1])); say(s, "Content set. " + modeLine(u));
                }
                case "status" -> {
                    if (u != null) say(s, modeLine(u));
                    StringBuilder js = new StringBuilder(); for (EggJob j : jobs.values()) js.append(" [").append(j.id).append(" tile ").append(j.tx).append(",").append(j.tz).append(" ").append(j.stage).append("]");
                    String here = "";
                    if (s instanceof Player p) { Location l = p.getLocation(); int tx = Math.floorDiv(l.getBlockX() >> 4, 4), tz = Math.floorDiv(l.getBlockZ() >> 4, 4);
                        here = " | here: tile " + tx + "," + tz + " (" + bounds(tx, tz) + ")" + (pl.loaded && tileInStrip(tx, tz) ? " PROTECTED (Strip)" : "") + ", cache " + cacheState(tx, tz); }
                    say(s, "Egg " + (enabled ? "enabled" : "DISABLED") + " | jobs " + jobs.size() + "/" + maxJobs + js + " | prep queue " + queue.size() + "/" + maxQueue
                            + ", running " + preps.values().stream().filter(t -> t.running).count() + "/" + workers + " | " + captureState() + here);
                }
                case "cancel" -> {
                    int n = 0, q = 0;
                    for (EggJob j : new ArrayList<>(jobs.values())) if (admin || (u != null && u.equals(j.owner))) { j.cancel("cancelled by " + s.getName()); n++; }
                    for (Prep t : new ArrayList<>(preps.values())) if (admin || (u != null && u.equals(t.owner))) { t.cancel(); q++; }
                    say(s, n + " Egg job(s) and " + q + " preparation task(s) cancelled.");
                }
                case "prepare" -> {
                    if (!(s instanceof Player p)) return say(s, "Players only (console: /eggtool simprepare).");
                    Location l = p.getLocation(); prepare(s, l.getWorld(), Math.floorDiv(l.getBlockX() >> 4, 4), Math.floorDiv(l.getBlockZ() >> 4, 4), contents.getOrDefault(u, EContent.ALL), u);
                }
                case "undo" -> { String job = null; boolean confirm = false;
                    for (int i = 1; i < a.length; i++) if (a[i].equalsIgnoreCase("confirm")) confirm = true; else job = a[i];
                    pl.undo(s, job, confirm, true); }
                // ---- admin/console test hooks: each runs the same code as the real interaction
                case "simset" -> {
                    if (!admin || a.length < 4) return say(s, "/eggtool simset <uuid> <off|preview|on> <terrain|vegetation|agriculture|all>");
                    UUID t = UUID.fromString(a[1]); contents.put(t, contentArg(a[3]));
                    if (!enabled && modeArg(a[2]) != Mode.OFF) return say(s, "The Egg tool is disabled (egg-enabled: false).");
                    say(s, "simset " + t + ": " + setMode(t, modeArg(a[2])));
                }
                case "simthrow" -> {
                    if (!admin || a.length < 5) return say(s, "/eggtool simthrow <uuid> <x> <y> <z>");
                    UUID t = UUID.fromString(a[1]);
                    if (modes.getOrDefault(t, Mode.OFF) == Mode.OFF) return say(s, "simthrow: " + t + " has Egg mode OFF - nothing done (a vanilla egg)");
                    hit(t, s, Bukkit.getWorld(pl.worldName), new int[]{Integer.parseInt(a[2]), Integer.parseInt(a[3]), Integer.parseInt(a[4])});
                }
                case "simentityhit" -> {
                    if (!admin || a.length < 2) return say(s, "/eggtool simentityhit <uuid>");
                    UUID t = UUID.fromString(a[1]);
                    if (modes.getOrDefault(t, Mode.OFF) == Mode.OFF) return say(s, "simentityhit: " + t + " has Egg mode OFF - nothing done (a vanilla egg)");
                    hit(t, s, null, null);
                }
                case "simhatch" -> {
                    if (!admin || a.length < 2) return say(s, "/eggtool simhatch <uuid>");
                    UUID t = UUID.fromString(a[1]);
                    say(s, "simhatch " + t + ": " + modeLine(t) + " -> " + (suppressHatch(t) ? "setHatching(false), 0 hatches" : "vanilla (event untouched)"));
                }
                case "simprepare" -> {
                    if (!admin || a.length < 4) return say(s, "/eggtool simprepare <tx> <tz> <terrain|vegetation|agriculture|all>");
                    prepare(s, Bukkit.getWorld(pl.worldName), Integer.parseInt(a[1]), Integer.parseInt(a[2]), contentArg(a[3]), u);
                }
                default -> say(s, "/eggtool on|off|preview|content <terrain|vegetation|agriculture|all>|prepare|status|cancel|undo [job] [confirm]");
            }
        } catch (IllegalArgumentException ex) { say(s, "Invalid argument: " + ex.getMessage()); }
        return true;
    }
    /** background-priority preparation of one tile (nothing is placed) */
    void prepare(CommandSender s, World w, int tx, int tz, EContent c, UUID u) {
        if (!enabled) { say(s, "The Egg tool is disabled (egg-enabled: false)."); return; }
        if (!pl.loaded) { say(s, "Data not loaded yet (Strip exclusion unknown) - refused."); return; }
        if (w == null || !w.getName().equals(pl.worldName)) { say(s, "Denied: only world '" + pl.worldName + "' is allowed."); return; }
        if (tileInStrip(tx, tz)) { say(s, "PROTECTED: tile " + tx + "," + tz + " touches the Strip exclusion. Nothing fetched."); return; }
        loadTile(w, tx, tz, () -> false).whenComplete((v, ex) -> Bukkit.getScheduler().runTaskAsynchronously(pl, () -> {
            EContent need = missing(readManifest(tx, tz), c);
            if (need == null) { msg(s, "§b[Egg] Tile " + tx + "," + tz + " (" + c.arg() + ") is already prepared and current. Nothing is placed."); return; }
            Prep t = requestPrep(tx, tz, need, 1, u);
            msg(s, "§b[Egg] " + (t == null ? "Prepare refused (Egg prep queue full, max " + maxQueue + ")." : "Tile " + tx + "," + tz + " (" + bounds(tx, tz) + ") queued for preparation (" + need.arg() + "). Nothing is placed."));
        }));
    }
    @Override public List<String> onTabComplete(CommandSender s, Command c, String l, String[] a) {
        if (a.length == 1) return List.of("on", "off", "preview", "content", "prepare", "status", "cancel", "undo");
        if (a.length == 2 && a[0].equalsIgnoreCase("content")) return List.of("terrain", "vegetation", "agriculture", "all");
        return List.of();
    }
}
