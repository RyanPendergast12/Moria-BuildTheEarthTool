package org.bte.summerlin.shelltool;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.scheduler.BukkitTask;

import java.io.File;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.util.*;

/**
 * v0.8 area mode (/shelltool area ...): one preview, one apply and one undo for a rectangle of chunks.
 * The area runner does not place anything itself: it launches the ordinary Snowball chunk job (same prep, writer, protection, ledger,
 * exact undo and tick limits as a throw) for every chunk, in serpentine order, at most area-parallel-jobs at a time (a slot is always
 * left for players), and queues preparation of the next chunks ahead of time. Each child job id is appended to
 * plugins/SummerlinShellTool/areas/<id>.chunks.jsonl, so an area is undone (newest first) and resumed from that file.
 * An apply needs a completed area preview of the same rectangle and content by the same person within 30 minutes.
 * An interrupted apply (cancel, restart) is continued only by '/shelltool area resume <id>': nothing restarts on its own.
 */
final class AreaTool {
    static final List<String> SUBS = List.of("pos1", "pos2", "info", "preview", "apply", "status", "cancel", "resume", "undo", "list");
    static final long GATE_MS = 30 * 60_000L, PROGRESS_MS = 30_000L, BUSY_SKIP_MS = 120_000L, STUCK_MS = 180_000L;
    final ShellToolPlugin pl; final int maxChunks, parallel, lookahead; final File dir;
    final Map<String, int[]> p1 = new HashMap<>(), p2 = new HashMap<>();        // sender key -> block x, z
    final Map<String, Run> lastPreview = new HashMap<>();                        // sender key -> latest completed area preview
    Run active;                                                                  // one area run at a time (server-wide)
    final Gson gson = new GsonBuilder().setPrettyPrinting().create();

    AreaTool(ShellToolPlugin pl) {
        this.pl = pl;
        maxChunks = Math.max(1, pl.getConfig().getInt("area-max-chunks", 1024));
        parallel = Math.max(1, Math.min(pl.getConfig().getInt("area-parallel-jobs", 2), pl.maxJobs - 1));
        lookahead = Math.max(1, Math.min(16, pl.maxPrepQueue / 2));
        dir = new File(pl.getDataFolder(), "areas"); dir.mkdirs();
    }
    static String who(CommandSender s) { return s instanceof Player p ? p.getUniqueId().toString() : "console"; }
    boolean say(CommandSender s, String m) { s.sendMessage("§b[ShellTool] Area: " + m); return true; }

    boolean command(CommandSender s, String[] a) {
        String sub = a.length == 0 ? "info" : a[0].toLowerCase(Locale.ROOT), k = who(s);
        switch (sub) {
            case "pos1", "pos2" -> {
                int x, z;
                if (a.length >= 3) { x = Integer.parseInt(a[1]); z = Integer.parseInt(a[2]); }
                else if (s instanceof Player p) { Location l = p.getLocation(); x = l.getBlockX(); z = l.getBlockZ(); }
                else return say(s, "/shelltool area " + sub + " <x> <z> (console)");
                (sub.equals("pos1") ? p1 : p2).put(k, new int[]{x, z});
                return say(s, sub + " set to x " + x + ", z " + z + (p1.containsKey(k) && p2.containsKey(k) ? ". " + describe(k) : ". Now set the other corner."));
            }
            case "info" -> { return say(s, p1.containsKey(k) && p2.containsKey(k) ? describe(k) : "set two corners: stand on one and type /shelltool area pos1, on the opposite one /shelltool area pos2"); }
            case "preview", "apply" -> {
                boolean apply = sub.equals("apply");
                if (apply && Arrays.stream(a).noneMatch(x -> x.equalsIgnoreCase("confirm"))) return say(s, "type '/shelltool area apply confirm' (after an area preview of the same area and content)");
                if (!p1.containsKey(k) || !p2.containsKey(k)) return say(s, "set both corners first (/shelltool area pos1, pos2)");
                ShellToolPlugin.Content ct = content(s, a);
                if (ct == null) return true;
                int[] r = rect(k);
                int n = (r[2] - r[0] + 1) * (r[3] - r[1] + 1);
                if (n > maxChunks) return say(s, "§c" + n + " chunks is more than the limit of " + maxChunks + " (config area-max-chunks). Select a smaller area.");
                if (active != null) return say(s, "area " + active.id + " is still running (" + active.mode + "). Wait, or '/shelltool area cancel'.");
                if (apply) {
                    Run pv = lastPreview.get(k);
                    if (pv == null || !Arrays.equals(pv.r, r) || pv.content != ct || System.currentTimeMillis() - pv.ended > GATE_MS)
                        return say(s, "§eno completed area preview of exactly this area with content " + ct.arg() + " by you in the last 30 minutes. Run '/shelltool area preview " + ct.arg() + "' first, then '/shelltool area apply confirm " + ct.arg() + "'.");
                }
                start(new Run(newId(), s, apply ? ShellToolPlugin.Mode.APPLY : ShellToolPlugin.Mode.PREVIEW, ct, r, Set.of(), List.of()));
                return true;
            }
            case "status" -> {
                if (active != null) return say(s, active.progress());
                Run pv = lastPreview.get(k);
                return say(s, "no area running." + (pv != null ? " Your last area preview: " + pv.id + " (" + pv.total + " chunks, " + ((System.currentTimeMillis() - pv.ended) / 60000) + " min ago)." : ""));
            }
            case "cancel" -> {
                if (active == null) return say(s, "no area running.");
                if (!active.ownerKey.equals(k) && !s.hasPermission(ShellToolPlugin.ADMIN)) return say(s, "§cthat area is not yours.");
                active.cancelled = true;
                return say(s, "cancelling area " + active.id + ": no new chunks start; the " + active.running + " running chunk job(s) finish first. " + (active.mode == ShellToolPlugin.Mode.APPLY ? "Continue later with '/shelltool area resume " + active.id + "', or undo it." : ""));
            }
            case "resume" -> {
                if (a.length < 2) return say(s, "/shelltool area resume <area id>");
                if (active != null) return say(s, "area " + active.id + " is still running.");
                JsonObject h = header(a[1]);
                if (h == null) return say(s, "§eno area '" + a[1] + "'.");
                if (!h.get("owner").getAsString().equals(k) && !s.hasPermission(ShellToolPlugin.ADMIN)) return say(s, "§cthat area is not yours.");
                String st = h.get("status").getAsString();
                if (!h.get("mode").getAsString().equals("APPLY") || !(st.equals("interrupted") || st.equals("cancelled")))
                    return say(s, "§eonly an interrupted or cancelled area APPLY can be resumed (" + a[1] + " is " + h.get("mode").getAsString() + ", " + st + ").");
                int[] r = new int[4]; for (int i = 0; i < 4; i++) r[i] = h.getAsJsonArray("chunks_rect").get(i).getAsInt();
                Set<Long> done = new HashSet<>(); List<String> jobs = new ArrayList<>();
                for (JsonObject c : children(a[1])) { done.add(ShellToolPlugin.key(c.get("cx").getAsInt(), c.get("cz").getAsInt())); if (c.has("job")) jobs.add(c.get("job").getAsString()); }
                start(new Run(a[1], s, ShellToolPlugin.Mode.APPLY, ShellToolPlugin.Content.valueOf(h.get("content").getAsString().toUpperCase(Locale.ROOT)), r, done, jobs));
                return true;
            }
            case "undo" -> {
                if (a.length < 2) return say(s, "/shelltool area undo <area id> [confirm]");
                undo(s, a[1], Arrays.stream(a).anyMatch(x -> x.equalsIgnoreCase("confirm")));
                return true;
            }
            case "list" -> {
                File[] fs = dir.listFiles((d, n) -> n.endsWith(".json"));
                List<JsonObject> hs = new ArrayList<>();
                if (fs != null) for (File f : fs) { JsonObject h = header(f.getName().replace(".json", "")); if (h != null && (h.get("owner").getAsString().equals(k) || s.hasPermission(ShellToolPlugin.ADMIN))) hs.add(h); }
                hs.sort(Comparator.comparing(h -> h.get("id").getAsString()));
                if (hs.isEmpty()) return say(s, "no areas yet.");
                for (JsonObject h : hs.subList(Math.max(0, hs.size() - 10), hs.size()))
                    say(s, h.get("id").getAsString() + " " + h.get("mode").getAsString() + " " + h.get("content").getAsString() + " " + h.get("chunks").getAsInt() + " chunks: " + h.get("status").getAsString());
                return true;
            }
            default -> { return say(s, "/shelltool area pos1|pos2 [x z] | info | preview [shells|roads|both] | apply confirm | status | cancel | resume <id> | undo <id> [confirm] | list"); }
        }
    }
    ShellToolPlugin.Content content(CommandSender s, String[] a) {
        for (int i = 1; i < a.length; i++) if (!a[i].equalsIgnoreCase("confirm")) {
            try { ShellToolPlugin.Content c = ShellToolPlugin.Content.valueOf(a[i].toUpperCase(Locale.ROOT)); if (!c.overpass()) return c; } catch (IllegalArgumentException ignored) {}
            say(s, "content must be shells, roads or both"); return null;
        }
        return s instanceof Player p ? pl.contents.getOrDefault(p.getUniqueId(), ShellToolPlugin.Content.SHELLS).overpass() ? ShellToolPlugin.Content.ROADS
                : pl.contents.getOrDefault(p.getUniqueId(), ShellToolPlugin.Content.SHELLS) : ShellToolPlugin.Content.BOTH;
    }
    /** chunk rectangle cx0, cz0, cx1, cz1 (inclusive) of the sender's corners */
    int[] rect(String k) {
        int[] a = p1.get(k), b = p2.get(k);
        return new int[]{Math.min(a[0], b[0]) >> 4, Math.min(a[1], b[1]) >> 4, Math.max(a[0], b[0]) >> 4, Math.max(a[1], b[1]) >> 4};
    }
    String describe(String k) {
        int[] r = rect(k); int w = r[2] - r[0] + 1, h = r[3] - r[1] + 1, strip = 0;
        for (int cx = r[0]; cx <= r[2]; cx++) for (int cz = r[1]; cz <= r[3]; cz++) if (pl.chunkInStrip(cx, cz)) strip++;
        return "selection " + w + " x " + h + " = " + (w * h) + " chunks (blocks x " + (r[0] << 4) + ".." + ((r[2] << 4) + 15) + ", z " + (r[1] << 4) + ".." + ((r[3] << 4) + 15) + ")"
                + (strip > 0 ? ", " + strip + " in the Strip exclusion (skipped)" : "") + (w * h > maxChunks ? ". §cOver the limit of " + maxChunks + " chunks." : ". Next: /shelltool area preview");
    }
    String newId() { return String.format("area-%tY%<tm%<td-%<tH%<tM%<tS", new Date()); }
    JsonObject header(String id) {
        if (!id.matches("area-[0-9-]+")) return null;
        File f = new File(dir, id + ".json");
        try { return f.exists() ? JsonParser.parseString(Files.readString(f.toPath())).getAsJsonObject() : null; } catch (Exception e) { return null; }
    }
    List<JsonObject> children(String id) {
        List<JsonObject> out = new ArrayList<>(); File f = new File(dir, id + ".chunks.jsonl");
        if (f.exists()) try { for (String l : Files.readAllLines(f.toPath())) if (!l.isBlank()) out.add(JsonParser.parseString(l).getAsJsonObject()); } catch (Exception e) { pl.getLogger().warning("area " + id + ": " + e); }
        return out;
    }
    void start(Run r) {
        active = r; r.writeHeader("running");
        say(r.who, r.mode + " " + r.content.arg() + " area " + r.id + ": " + r.total + " chunks" + (r.done.isEmpty() ? "" : " (" + r.done.size() + " already done, resuming)")
                + ", " + parallel + " chunk job(s) at a time. Progress every 30 s; '/shelltool area status' or 'cancel'.");
        pl.getLogger().info("area " + r.id + " " + r.mode + " " + r.content.arg() + " started by " + r.who.getName() + ": " + r.total + " chunks, rect " + Arrays.toString(r.r));
        r.task = Bukkit.getScheduler().runTaskTimer(pl, r::tick, 1, 5);
    }
    void stop() {
        Run r = active; if (r == null) return;
        for (ShellToolPlugin.Job j : new ArrayList<>(r.jobs)) j.cancel("server stopping");   // their onDone records them (job ids stay undoable)
        r.end("interrupted");
    }

    void undo(CommandSender s, String id, boolean confirm) {
        JsonObject h = header(id);
        if (h == null) { say(s, "§eno area '" + id + "'."); return; }
        if (!h.get("owner").getAsString().equals(who(s)) && !s.hasPermission(ShellToolPlugin.ADMIN)) { say(s, "§cthat area is not yours."); return; }
        if (active != null && active.id.equals(id)) { say(s, "§earea " + id + " is still running; cancel it first."); return; }
        List<String> jobs = new ArrayList<>();
        for (JsonObject c : children(id)) if (c.has("job") && pl.ledgerJobs.containsKey(c.get("job").getAsString()) && !pl.undone.contains(c.get("job").getAsString())) jobs.add(c.get("job").getAsString());
        Collections.reverse(jobs);                                                 // newest first
        if (jobs.isEmpty()) { say(s, "area " + id + " has no undoable chunk jobs (nothing was placed, or it was already undone)."); return; }
        say(s, (confirm ? "undoing" : "undo DRY RUN of") + " area " + id + ": " + jobs.size() + " chunk job(s), newest first...");
        int[] tot = {0, 0, 0}; Map<String, Integer> refused = new TreeMap<>();
        Iterator<String> it = jobs.iterator();
        Runnable[] next = new Runnable[1];
        next[0] = () -> {
            if (!it.hasNext()) {
                say(s, "undo " + (confirm ? "" : "DRY RUN ") + "area " + id + ": " + jobs.size() + " chunk job(s), " + (confirm ? "restored " : "would restore ") + tot[0]
                        + " block(s) to their exact before-state, kept (changed since placement) " + tot[1] + (refused.isEmpty() ? "" : ", " + tot[2] + " job(s) refused: " + refused)
                        + (confirm ? "" : ". Run '/shelltool area undo " + id + " confirm' to apply."));
                if (confirm && refused.isEmpty()) { h.addProperty("status", "undone"); write(id, h); }
                return;
            }
            String j = it.next();
            pl.undo(s, j, confirm, "shell", (why, res) -> {
                if (why != null) { tot[2]++; refused.merge(why.length() > 80 ? why.substring(0, 80) : why, 1, Integer::sum); }
                else { tot[0] += res[0]; tot[1] += res[1]; }
                Bukkit.getScheduler().runTask(pl, next[0]);
            });
        };
        next[0].run();
    }
    void write(String id, JsonObject h) {
        try { Files.writeString(new File(dir, id + ".json").toPath(), gson.toJson(h), StandardCharsets.UTF_8); } catch (IOException e) { pl.getLogger().warning("area header write failed: " + e); }
    }

    final class Run {
        final String id, ownerKey; final UUID owner; final CommandSender who; final ShellToolPlugin.Mode mode; final ShellToolPlugin.Content content; final int[] r;
        final List<int[]> chunks = new ArrayList<>(); final Set<Long> done; final List<String> jobIds; final Set<ShellToolPlugin.Job> jobs = new HashSet<>();
        final Map<String, int[]> totals = new TreeMap<>(); final Map<String, Integer> statuses = new TreeMap<>();
        final int total; int next, pref, running, finished, strip, busy, ungen, stuck; long t0 = System.currentTimeMillis(), lastMsg = t0, waitSince, ended;
        volatile boolean prefBusy; boolean cancelled, over; BukkitTask task; Writer log;
        Run(String id, CommandSender who, ShellToolPlugin.Mode mode, ShellToolPlugin.Content content, int[] r, Set<Long> done, List<String> jobIds) {
            this.id = id; this.who = who; this.ownerKey = AreaTool.who(who); this.owner = who instanceof Player p ? p.getUniqueId() : null;
            this.mode = mode; this.content = content; this.r = r; this.done = new HashSet<>(done); this.jobIds = new ArrayList<>(jobIds);
            for (int cz = r[1]; cz <= r[3]; cz++) {                                  // serpentine: neighbouring chunks run one after another
                boolean rev = ((cz - r[1]) & 1) == 1;
                for (int i = 0; i <= r[2] - r[0]; i++) chunks.add(new int[]{rev ? r[2] - i : r[0] + i, cz});
            }
            total = chunks.size();
            try { log = Files.newBufferedWriter(new File(dir, id + ".chunks.jsonl").toPath(), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND); }
            catch (IOException e) { throw new IllegalStateException(e); }
        }
        void tick() {
            if (over) return;
            long now = System.currentTimeMillis(); World w = Bukkit.getWorld(pl.worldName);
            while (!cancelled && running < parallel && next < total) {
                int[] c = chunks.get(next); long k = ShellToolPlugin.key(c[0], c[1]);
                if (done.contains(k)) { next++; continue; }
                if (pl.chunkInStrip(c[0], c[1])) { strip++; record(c, null, "protected (Strip)"); next++; continue; }
                if (!w.isChunkGenerated(c[0], c[1])) { ungen++; record(c, null, "not generated (outside the built world): skipped, never generated"); next++; continue; }
                if (pl.regionLocks.containsKey(k) || pl.activeChunks.containsKey(k)) {     // another tool or a player is working there: wait, then skip
                    if (waitSince == 0) waitSince = now;
                    if (now - waitSince > BUSY_SKIP_MS) { busy++; record(c, null, "busy for 2 min (" + (pl.regionLocks.containsKey(k) ? pl.lockHolder(k) : "a Snowball job") + "): skipped"); next++; waitSince = 0; continue; }
                    break;
                }
                if (pl.activeChunks.size() >= pl.maxJobs) break;
                waitSince = 0; next++; running++;
                jobs.add(pl.launch(who, w, (c[0] << 4) + 8, 0, (c[1] << 4) + 8, mode, content, owner, true, this::child));
            }
            for (ShellToolPlugin.Job j : new ArrayList<>(jobs))                  // watchdog: one stuck chunk must not hold up the area
                if (!j.stage.equals("preparing") && !j.stage.equals("queued") && j.stageSince > 0 && now - j.stageSince > STUCK_MS) { stuck++; j.cancel("area watchdog: no progress in stage '" + j.stage + "' for 3 min"); }
            prefetch();
            if (now - lastMsg >= PROGRESS_MS) { lastMsg = now; say(who, progress()); }
            if (running == 0 && (cancelled || next >= total)) end(cancelled ? "cancelled" : "done");
        }
        /** queue preparation of the next chunks (prefetch priority) so chunk jobs rarely wait for data */
        void prefetch() {
            if (prefBusy || cancelled) return;
            int from = Math.max(pref, next), to = Math.min(total, next + lookahead);
            if (from >= to) return;
            prefBusy = true;
            Bukkit.getScheduler().runTaskAsynchronously(pl, () -> {
                int i = from;
                try { for (; i < to; i++) { int[] c = chunks.get(i);
                    if (pl.chunkInStrip(c[0], c[1])) continue;
                    ShellToolPlugin.Content need = pl.missing(pl.readManifest(c[0], c[1]), content);
                    if (need != null && pl.requestPrep(c[0], c[1], need, 1, owner) == null) break; } }   // queue full: try again next tick
                finally { int p = i; Bukkit.getScheduler().runTask(pl, () -> { pref = Math.max(pref, p); prefBusy = false; }); }
            });
        }
        void child(ShellToolPlugin.Job j) {
            jobs.remove(j); running--; finished++;
            for (Map.Entry<String, int[]> e : j.byContent.entrySet()) { int[] t = totals.computeIfAbsent(e.getKey(), x -> new int[4]); for (int i = 0; i < 4; i++) t[i] += e.getValue()[i]; }
            if (content.shells()) statuses.merge("shells " + j.shellStatus, 1, Integer::sum);
            if (content.roads()) statuses.merge("roads " + j.roadStatus, 1, Integer::sum);
            if (mode == ShellToolPlugin.Mode.APPLY) jobIds.add(j.id);
            JsonObject o = new JsonObject(); o.addProperty("cx", j.cx); o.addProperty("cz", j.cz); o.addProperty("job", j.id);
            o.addProperty(mode == ShellToolPlugin.Mode.APPLY ? "placed" : "would_place", mode == ShellToolPlugin.Mode.APPLY ? j.placed : j.wouldPlace);
            o.addProperty("present", j.present); o.addProperty("skipped", j.conflicts); o.addProperty("shells", j.shellStatus); o.addProperty("roads", j.roadStatus);
            line(o);
        }
        void record(int[] c, String job, String why) { JsonObject o = new JsonObject(); o.addProperty("cx", c[0]); o.addProperty("cz", c[1]); o.addProperty("note", why); line(o); }
        void line(JsonObject o) { try { log.write(new Gson().toJson(o) + "\n"); log.flush(); } catch (IOException e) { pl.getLogger().warning("area log write failed: " + e); } }
        String counts() {
            StringBuilder sb = new StringBuilder();
            for (Map.Entry<String, int[]> e : totals.entrySet()) sb.append(sb.length() == 0 ? "" : ", ").append(e.getKey()).append(" ").append(e.getValue()[0]);
            return sb.length() == 0 ? "nothing yet" : sb.toString();
        }
        String progress() {
            int doneN = done.size() + finished + strip + busy + ungen; long el = (System.currentTimeMillis() - t0) / 1000;
            long eta = finished > 0 ? el * (total - doneN) / Math.max(1, finished) : -1;
            return id + " " + mode + " " + content.arg() + ": " + doneN + "/" + total + " chunks (" + (100 * doneN / Math.max(1, total)) + "%), "
                    + (mode == ShellToolPlugin.Mode.APPLY ? "placed" : "would place") + ": " + counts() + " | " + el / 60 + " min" + (eta >= 0 ? ", about " + Math.max(1, eta / 60) + " min left" : "")
                    + (cancelled ? " | CANCELLING" : "");
        }
        void end(String status) {
            if (over) return; over = true; ended = System.currentTimeMillis();
            if (task != null) task.cancel();
            try { log.close(); } catch (IOException ignored) {}
            writeHeader(status);
            if (active == this) active = null;
            int skipped = 0, present = 0; for (int[] t : totals.values()) { present += t[1]; skipped += t[2]; }
            say(who, "§a" + status.toUpperCase(Locale.ROOT) + "§b " + mode + " " + content.arg() + " area " + id + ": " + (done.size() + finished) + "/" + total + " chunks in " + (ended - t0) / 60000 + " min. "
                    + (mode == ShellToolPlugin.Mode.APPLY ? "Placed" : "Would place") + ": " + counts() + "; already present " + present + ", skipped (protected/conflicts) " + skipped
                    + (strip > 0 ? "; " + strip + " Strip chunk(s) skipped" : "") + (busy > 0 ? "; " + busy + " busy chunk(s) skipped" : "") + (ungen > 0 ? "; " + ungen + " ungenerated chunk(s) skipped" : "")
                    + (stuck > 0 ? "; §c" + stuck + " chunk job(s) stopped by the watchdog (see the .chunks.jsonl; resume or re-throw them)§b" : "") + ".");
            if (!statuses.isEmpty()) say(who, "chunk results: " + statuses);
            if (mode == ShellToolPlugin.Mode.PREVIEW && status.equals("done") && stuck == 0) { lastPreview.put(ownerKey, this); say(who, "To build it: '/shelltool area apply confirm' within 30 minutes (same area and content)."); }
            if (mode == ShellToolPlugin.Mode.APPLY) say(who, status.equals("done") ? "Undo all of it with '/shelltool area undo " + id + "' (dry run first)." : "Continue with '/shelltool area resume " + id + "' or undo with '/shelltool area undo " + id + "'.");
            say(who, "§7Report: plugins/SummerlinShellTool/areas/" + id + ".json (+ .chunks.jsonl)");
            pl.getLogger().info("area " + id + " " + status + ": " + progress());
        }
        void writeHeader(String status) {
            JsonObject h = new JsonObject(); h.addProperty("id", id); h.addProperty("owner", ownerKey); h.addProperty("mode", mode.toString()); h.addProperty("content", content.arg());
            h.add("chunks_rect", gson.toJsonTree(r)); h.addProperty("chunks", total); h.addProperty("status", status);
            h.addProperty("started", t0); if (ended > 0) h.addProperty("ended", ended);
            h.addProperty("chunks_finished", done.size() + finished); h.addProperty("strip_skipped", strip); h.addProperty("busy_skipped", busy); h.addProperty("ungenerated_skipped", ungen); h.addProperty("watchdog_stopped", stuck);
            h.add("totals_by_content [placed|would, present, protected, upgraded]", gson.toJsonTree(totals)); h.add("chunk_results", gson.toJsonTree(statuses));
            write(id, h);
        }
    }
}
