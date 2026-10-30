package athena.bench;

import athena.api.Athena;
import com.destroystokyo.paper.event.server.ServerTickEndEvent;
import com.destroystokyo.paper.event.server.ServerTickStartEvent;
import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryMXBean;
import java.lang.management.ThreadInfo;
import java.lang.management.ThreadMXBean;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Loads a match map the way SurvivalGames does (template world, pinned and preloaded square of 300
 * blocks, side^2/120 chunks a tick), then measures while the bots fly over it.
 *
 * System properties: mapprobe.map (world folder), mapprobe.center (x,y,z), mapprobe.radius (300),
 * mapprobe.bots (how many to wait for), mapprobe.warmup and mapprobe.window (seconds),
 * mapprobe.out (the JSON written at the end). Prints "MAPPROBE READY" once the map is loaded and the
 * heap measured: the harness starts the bots then. Shuts the server down when done.
 */
public final class MapProbe extends JavaPlugin implements Listener {

    private final ThreadMXBean threads = ManagementFactory.getThreadMXBean();
    private final MemoryMXBean memory = ManagementFactory.getMemoryMXBean();
    private final com.sun.management.OperatingSystemMXBean os =
            (com.sun.management.OperatingSystemMXBean) ManagementFactory.getOperatingSystemMXBean();
    private final Map<String, Object> result = new LinkedHashMap<String, Object>();
    private World world;
    private Location center;
    private int bots;
    private int joined;
    private boolean ready;

    // the measured window
    private boolean measuring;
    private long[] durations = new long[20 * 300];
    private boolean[] phase80 = new boolean[durations.length];
    private int count;
    private long tickStart;
    private boolean tickPhase80;
    private long windowCpu;
    private long windowCompressorCpu;
    private long windowStart;

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(this, this);
        this.bots = Integer.getInteger("mapprobe.bots", 20);
        getServer().getScheduler().runTaskLater(this, this::load, 40L);
    }

    private long compressorCpu() {
        long total = 0;
        for (ThreadInfo info : threads.getThreadInfo(threads.getAllThreadIds())) {
            if (info != null && info.getThreadName().startsWith("Athena chunk packet compressor")) {
                total += Math.max(0, threads.getThreadCpuTime(info.getThreadId()));
            }
        }
        return total;
    }

    private long heapAfterGc() {
        for (int i = 0; i < 3; ++i) {
            System.gc();
        }
        return memory.getHeapMemoryUsage().getUsed();
    }

    private void load() {
        File folder = new File(System.getProperty("mapprobe.map"));
        String[] c = System.getProperty("mapprobe.center").split(",");
        int radius = Integer.getInteger("mapprobe.radius", 300);
        int side = 2 * ((radius + 15) >> 4) + 1;
        int perTick = Math.max(4, (side * side + 119) / 120);

        this.result.put("map", folder.getName());
        this.result.put("heap_before_load_mb", heapAfterGc() / 1048576.0);
        final long start = System.nanoTime();
        final long cpu = os.getProcessCpuTime();
        final long compressor = compressorCpu();

        this.world = Athena.loadTemplateWorld("probe_" + folder.getName(), folder);
        this.center = new Location(this.world, Double.parseDouble(c[0]), Double.parseDouble(c[1]), Double.parseDouble(c[2]));
        try {
            Object handle = this.world.getClass().getMethod("getHandle").invoke(this.world);
            this.worldTime = java.lang.invoke.MethodHandles.publicLookup().unreflect(handle.getClass().getMethod("getTime")).bindTo(handle);
        } catch (ReflectiveOperationException e) {
            throw new RuntimeException(e);
        }
        Athena.pinChunks(this.center, radius);
        Athena.preloadChunks(this.center, radius, perTick).thenAccept(chunks -> {
            long ms = (System.nanoTime() - start) / 1_000_000L;

            this.result.put("preload_chunks", chunks);
            this.result.put("preload_ms", ms);
            this.result.put("preload_process_cpu_ms", (os.getProcessCpuTime() - cpu) / 1_000_000L);
            // the compressor works ahead of the preload; give it time to drain
            getServer().getScheduler().runTaskLater(this, () -> {
                this.result.put("preload_compressor_cpu_ms", (compressorCpu() - compressor) / 1_000_000L);
                this.result.put("process_cpu_load_to_ready_ms", (os.getProcessCpuTime() - cpu) / 1_000_000L);
                this.result.put("heap_after_preload_mb", heapAfterGc() / 1048576.0);
                this.result.put("loaded_chunks", this.world.getLoadedChunks().length);
                this.ready = true;
                getLogger().info("MAPPROBE READY");
            }, 200L);
        });
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        Player player = event.getPlayer();
        int i = this.joined++;
        Location to = this.center.clone().add(((i % 5) - 2) * 40, 10, ((i / 5) - 2) * 40);

        player.setAllowFlight(true);
        player.setFlying(true);
        getServer().getScheduler().runTaskLater(this, () -> player.teleport(to), 20L);
        if (this.joined == this.bots) {
            long warmup = Long.getLong("mapprobe.warmup", 20L);

            getServer().getScheduler().runTaskLater(this, this::startWindow, warmup * 20L);
        }
    }

    private void startWindow() {
        this.windowStart = System.nanoTime();
        this.windowCpu = os.getProcessCpuTime();
        this.windowCompressorCpu = compressorCpu();
        this.measuring = true;
        long window = Long.getLong("mapprobe.window", 60L);

        getServer().getScheduler().runTaskLater(this, this::finish, window * 20L);
    }

    // World.getTime(): the tick count beacons and daylight detectors key off; getFullTime() is the
    // day time, which stands still in a map with doDaylightCycle off
    private java.lang.invoke.MethodHandle worldTime;

    private long worldTime() {
        try {
            return (long) this.worldTime.invoke();
        } catch (Throwable t) {
            throw new RuntimeException(t);
        }
    }

    @EventHandler
    public void onTickStart(ServerTickStartEvent event) {
        this.tickStart = System.nanoTime();
        this.tickPhase80 = this.worldTime != null && worldTime() % 80L == 0L;
    }

    @EventHandler
    public void onTickEnd(ServerTickEndEvent event) {
        if (this.measuring && this.count < this.durations.length) {
            this.durations[this.count] = System.nanoTime() - this.tickStart;
            this.phase80[this.count] = this.tickPhase80;
            ++this.count;
        }
    }

    private static double ms(long nanos) {
        return Math.round(nanos / 10_000.0) / 100.0;
    }

    private void finish() {
        this.measuring = false;
        double seconds = (System.nanoTime() - this.windowStart) / 1e9;

        this.result.put("window_s", Math.round(seconds * 10) / 10.0);
        this.result.put("players", getServer().getOnlinePlayers().size());
        this.result.put("window_process_cpu_ms_per_s", Math.round((os.getProcessCpuTime() - this.windowCpu) / 1e6 / seconds));
        this.result.put("window_compressor_cpu_ms", (compressorCpu() - this.windowCompressorCpu) / 1_000_000L);
        long[] all = Arrays.copyOf(this.durations, this.count);
        long sum = 0, sum80 = 0, max80 = 0, n80 = 0, sumOther = 0, maxOther = 0;

        for (int i = 0; i < this.count; ++i) {
            sum += all[i];
            if (this.phase80[i]) {
                sum80 += all[i];
                max80 = Math.max(max80, all[i]);
                ++n80;
            } else {
                sumOther += all[i];
                maxOther = Math.max(maxOther, all[i]);
            }
        }
        Arrays.sort(all);
        this.result.put("ticks", this.count);
        this.result.put("tick_mean_ms", ms(sum / Math.max(1, this.count)));
        this.result.put("tick_p50_ms", ms(all[this.count / 2]));
        this.result.put("tick_p95_ms", ms(all[(int) (this.count * 0.95)]));
        this.result.put("tick_p99_ms", ms(all[(int) (this.count * 0.99)]));
        this.result.put("tick_max_ms", ms(all[this.count - 1]));
        this.result.put("ticks_over_50ms", Arrays.stream(all).filter(d -> d > 50_000_000L).count());
        this.result.put("tick80_mean_ms", ms(sum80 / Math.max(1, n80)));
        this.result.put("tick80_max_ms", ms(max80));
        this.result.put("tick_other_mean_ms", ms(sumOther / Math.max(1, this.count - n80)));
        this.result.put("tick_other_max_ms", ms(maxOther));
        this.result.put("tile_entities_ticking", tickingTiles());
        this.result.put("heap_after_window_mb", heapAfterGc() / 1048576.0);

        StringBuilder json = new StringBuilder("{");
        for (Map.Entry<String, Object> e : this.result.entrySet()) {
            Object v = e.getValue();
            json.append(json.length() > 1 ? ", " : "").append('"').append(e.getKey()).append("\": ")
                    .append(v instanceof String ? "\"" + v + "\"" : String.valueOf(v));
        }
        json.append('}');
        try (PrintWriter writer = new PrintWriter(new File(System.getProperty("mapprobe.out")), "UTF-8")) {
            writer.println(json);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
        getLogger().info("MAPPROBE DONE " + json);
        Bukkit.shutdown();
    }

    /** How many tile entities the world ticks, read off World.tileEntityList through reflection. */
    private Object tickingTiles() {
        try {
            Object handle = this.world.getClass().getMethod("getHandle").invoke(this.world);
            Class<?> type = handle.getClass();
            while (type != null) {
                try {
                    java.lang.reflect.Field field = type.getDeclaredField("tileEntityList");
                    return ((java.util.List<?>) field.get(handle)).size();
                } catch (NoSuchFieldException e) {
                    type = type.getSuperclass();
                }
            }
        } catch (ReflectiveOperationException e) {
            return "\"" + e + "\"";
        }
        return -1;
    }
}
