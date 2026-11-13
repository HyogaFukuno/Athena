import jdk.jfr.consumer.*;
import java.nio.file.*;
import java.util.*;
import java.util.function.*;

public class JfrAgg {
    static String fr(RecordedFrame f) { return f.getMethod().getType().getName() + "." + f.getMethod().getName(); }
    static String grp(String t) {
        if (t == null) return "?";
        if (t.startsWith("Netty")) return "Netty";
        if (t.startsWith("Server thread")) return "Server";
        if (t.startsWith("Athena world")) return "World";
        if (t.startsWith("Athena")) return t.replaceAll("[-#]?\\d+$", "");
        return t.replaceAll("[-#]?\\d+$", "");
    }
    public static void main(String[] a) throws Exception {
        String mode = a[1];
        String filter = a.length > 2 ? a[2] : null;   // thread group filter
        String must = a.length > 3 ? a[3] : null;      // frame that must be on stack
        int topN = a.length > 4 ? Integer.parseInt(a[4]) : 60;
        Map<String, long[]> self = new HashMap<>(), total = new HashMap<>(), threads = new TreeMap<>(), callers = new HashMap<>();
        long n = 0;
        Map<String, double[]> alloc = new HashMap<>(), allocSite = new HashMap<>();
        double allocTotal = 0;
        try (RecordingFile rf = new RecordingFile(Paths.get(a[0]))) {
            while (rf.hasMoreEvents()) {
                RecordedEvent e = rf.readEvent();
                String name = e.getEventType().getName();
                if (mode.equals("cpu") && name.equals("jdk.ExecutionSample")) {
                    RecordedThread t = e.getThread("sampledThread");
                    String g = grp(t == null ? null : t.getJavaName());
                    threads.computeIfAbsent(g, k -> new long[1])[0]++;
                    if (filter != null && !filter.equals("-") && !g.equals(filter)) continue;
                    RecordedStackTrace st = e.getStackTrace();
                    if (st == null) continue;
                    List<RecordedFrame> fs = st.getFrames();
                    if (fs.isEmpty()) continue;
                    if (must != null && !must.equals("-")) {
                        boolean ok = false; int idx = -1;
                        for (int i = 0; i < fs.size(); i++) if (fr(fs.get(i)).contains(must)) { ok = true; idx = i; break; }
                        if (!ok) continue;
                        // callers: the frame just above the first match (callee side = idx-1)
                        if (idx > 0) callers.computeIfAbsent("callee: " + fr(fs.get(idx - 1)), k -> new long[1])[0]++;
                        else callers.computeIfAbsent("callee: <self>", k -> new long[1])[0]++;
                        if (idx + 1 < fs.size()) callers.computeIfAbsent("caller: " + fr(fs.get(idx + 1)), k -> new long[1])[0]++;
                    }
                    n++;
                    self.computeIfAbsent(fr(fs.get(0)), k -> new long[1])[0]++;
                    Set<String> seen = new HashSet<>();
                    for (RecordedFrame f : fs) { String s = fr(f); if (seen.add(s)) total.computeIfAbsent(s, k -> new long[1])[0]++; }
                } else if (mode.equals("alloc") && name.equals("jdk.ObjectAllocationSample")) {
                    RecordedThread t = e.getThread("eventThread");
                    String g = grp(t == null ? null : t.getJavaName());
                    double w = e.getLong("weight");
                    if (filter != null && !filter.equals("-") && !g.equals(filter)) continue;
                    RecordedStackTrace st = e.getStackTrace();
                    if (must != null && !must.equals("-")) {
                        boolean ok = false;
                        if (st != null) for (RecordedFrame f : st.getFrames()) if (fr(f).contains(must)) { ok = true; break; }
                        if (!ok) continue;
                    }
                    allocTotal += w;
                    alloc.computeIfAbsent(g + " | " + e.getClass("objectClass").getName(), k -> new double[1])[0] += w;
                    if (st != null) {
                        List<RecordedFrame> fs = st.getFrames();
                        StringBuilder sb = new StringBuilder(e.getClass("objectClass").getName()).append(" <- ");
                        int c = 0;
                        for (RecordedFrame f : fs) {
                            String s = fr(f);
                            if (s.startsWith("java.") || s.startsWith("jdk.") || s.startsWith("it.unimi") || s.startsWith("com.google")) continue;
                            sb.append(s).append(" < "); if (++c == 3) break;
                        }
                        allocSite.computeIfAbsent(sb.toString(), k -> new double[1])[0] += w;
                    }
                }
            }
        }
        if (mode.equals("cpu")) {
            System.out.println("== threads (all samples)"); long all = 0; for (long[] v : threads.values()) all += v[0];
            for (var en : threads.entrySet()) System.out.printf("%6d %5.1f%% %s%n", en.getValue()[0], 100.0 * en.getValue()[0] / all, en.getKey());
            System.out.println("== filtered samples: " + n);
            print("self", self, n, topN); print("total", total, n, topN);
            if (!callers.isEmpty()) print("around", callers, n, 40);
        } else {
            System.out.printf("== alloc total weight %.1f MB%n", allocTotal / 1e6);
            printD("by class", alloc, allocTotal, topN); printD("by site", allocSite, allocTotal, topN);
        }
    }
    static void print(String title, Map<String, long[]> m, long n, int top) {
        System.out.println("== " + title);
        m.entrySet().stream().sorted((x, y) -> Long.compare(y.getValue()[0], x.getValue()[0])).limit(top)
            .forEach(en -> System.out.printf("%6d %5.2f%% %s%n", en.getValue()[0], 100.0 * en.getValue()[0] / n, en.getKey()));
    }
    static void printD(String title, Map<String, double[]> m, double n, int top) {
        System.out.println("== " + title);
        m.entrySet().stream().sorted((x, y) -> Double.compare(y.getValue()[0], x.getValue()[0])).limit(top)
            .forEach(en -> System.out.printf("%8.1fMB %5.2f%% %s%n", en.getValue()[0] / 1e6, 100.0 * en.getValue()[0] / n, en.getKey()));
    }
}
