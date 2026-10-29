import net.buildtheearth.terraminusminus.dataset.IScalarDataset;
import net.buildtheearth.terraminusminus.generator.*;
import net.buildtheearth.terraminusminus.projection.GeographicProjection;
import java.io.*;
import java.util.*;
import java.util.concurrent.CompletableFuture;
/** One JVM for a whole preparation: stdin "fwd lon lat" -> "x z"; "inv x z" -> "lon lat"; "h lon lat" queued, answered
 *  (in order, fetched concurrently) at the next "sync" line as "height". TerraPlusMinus BTE_DEFAULT_SETTINGS = the Vegas world. */
public class GeoServer {
    public static void main(String[] a) throws Exception {
        EarthGeneratorSettings s = EarthGeneratorSettings.parse(EarthGeneratorSettings.BTE_DEFAULT_SETTINGS);
        GeographicProjection p = s.projection();
        IScalarDataset h = null;
        BufferedReader r = new BufferedReader(new InputStreamReader(System.in));
        PrintStream out = new PrintStream(new BufferedOutputStream(new FileOutputStream(FileDescriptor.out)), false);
        System.setOut(System.err);                               // library logging must not mix with answers
        List<CompletableFuture<Double>> pend = new ArrayList<>(); String l;
        while ((l = r.readLine()) != null) {
            String[] t = l.trim().split("\\s+");
            if (t[0].equals("sync")) {
                for (CompletableFuture<Double> f : pend) { double v; try { v = f.get(); } catch (Exception e) { v = Double.NaN; } out.printf("%.3f%n", v); }
                pend.clear(); out.println("ok"); out.flush(); continue;
            }
            if (t.length < 3) continue;
            double u = Double.parseDouble(t[1]), v = Double.parseDouble(t[2]);
            if (t[0].equals("h")) { if (h == null) h = new GeneratorDatasets(s).getCustom(EarthGeneratorPipelines.KEY_DATASET_HEIGHTS); pend.add(h.getAsync(u, v)); continue; }
            double[] o = t[0].equals("fwd") ? p.fromGeo(u, v) : p.toGeo(u, v);
            out.printf("%.6f %.6f%n", o[0], o[1]); if (!r.ready()) out.flush();
        }
        out.flush(); System.exit(0);
    }
}
