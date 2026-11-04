package athena.lampbench;

import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

import org.bukkit.Bukkit;
import athena.api.Athena;
import com.destroystokyo.paper.event.server.ServerTickEndEvent;
import org.bukkit.Chunk;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.WorldCreator;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockRedstoneEvent;
import org.bukkit.event.world.ChunkUnloadEvent;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Athena - how long a world's ticks take when SurvivalGames' End turns a map's time to night
 * (World.setTime(400000)) and its daylight detectors switch the street lamps on.
 * <p>
 * Each round opens the map afresh as SG does: a template world (-Dlampbench.template=false copies the
 * folder and loads it as an ordinary world instead), the square of -Dlampbench.radius blocks around
 * the centre pinned and preloaded. It lets it settle at noon, sets the time to 400000 and records, for the ticks after, the
 * length of each tick (ServerTickEndEvent) and how many lamps switched on or off. The world ticks on the server thread.
 * <p>
 * -Dlampbench.source=DIR (the map), -Dlampbench.rounds=5, -Dlampbench.radius=300, -Dlampbench.ticks=200,
 * -Dlampbench.out=FILE (a CSV; a summary goes to the log). The server stops when it is done.
 */
public final class LampBench extends JavaPlugin implements Listener {

    private static final boolean TEMPLATE = Boolean.parseBoolean(System.getProperty("lampbench.template", "true"));
    private File source;
    private int rounds, radius, ticks;
    private File out;
    private final List<String> rows = new ArrayList<>();
    private World bench;
    private int lampsOn, lampsOff;
    private boolean counting;

    @Override
    public void onEnable() {
        if (System.getProperty("lampbench.source") == null) {
            getLogger().info("no -Dlampbench.source: idle"); // a start that only builds the AOT cache
            return;
        }
        source = new File(System.getProperty("lampbench.source"));
        rounds = Integer.getInteger("lampbench.rounds", 5);
        radius = Integer.getInteger("lampbench.radius", 300); // blocks, as the map's radius in SG
        ticks = Integer.getInteger("lampbench.ticks", 200);
        out = new File(System.getProperty("lampbench.out", "lampbench.csv"));
        rows.add("round,tick,tick_ms,lamps_on,lamps_off");
        getServer().getPluginManager().registerEvents(this, this);
        Bukkit.getScheduler().runTaskLater(this, () -> round(1), 100L);
    }

    @EventHandler
    public void keep(ChunkUnloadEvent e) {
        if (e.getWorld() == bench) e.setCancelled(true);
    }

    @EventHandler
    public void count(BlockRedstoneEvent e) {
        if (!counting || e.getBlock().getWorld() != bench) return;
        Material type = e.getBlock().getType();
        if (type == Material.REDSTONE_LAMP_OFF && e.getNewCurrent() > 0) lampsOn++;
        else if (type == Material.REDSTONE_LAMP_ON && e.getNewCurrent() == 0) lampsOff++;
    }

    private void round(int round) {
        String name = "lampbench_" + round;
        File dir = new File(Bukkit.getWorldContainer(), name);
        long t0 = System.nanoTime();
        if (TEMPLATE) {
            // as SurvivalGames opens a match map: a template world, the map's square pinned and preloaded
            bench = Athena.loadTemplateWorld(name, source);
        } else {
            try {
                copy(source.toPath(), dir.toPath());
            } catch (IOException ex) {
                throw new RuntimeException(ex);
            }
            bench = new WorldCreator(name).createWorld();
        }
        bench.setKeepSpawnInMemory(false);
        bench.setGameRuleValue("doDaylightCycle", "false");
        bench.setGameRuleValue("doMobSpawning", "false");
        bench.setTime(6000L);
        if (TEMPLATE) {
            Location center = new Location(bench, 0.5, 74, 0.5);
            Athena.pinChunks(center, radius);
            int side = 2 * ((radius + 15) >> 4) + 1;
            Athena.preloadChunks(center, radius, side * side).thenAccept(chunks -> {
                getLogger().info("round " + round + ": preloaded " + chunks + " chunks in " + (System.nanoTime() - t0) / 1_000_000L + " ms");
                measure(round, dir);
            });
            return;
        }
        int r = (radius + 15) >> 4;
        for (int x = -r; x <= r; x++) {
            for (int z = -r; z <= r; z++) {
                bench.loadChunk(x, z);
            }
        }
        getLogger().info("round " + round + ": loaded " + bench.getLoadedChunks().length + " chunks in " + (System.nanoTime() - t0) / 1_000_000L + " ms");
        measure(round, dir);
    }

    private int roundNo, measured;
    private File dir;

    private void measure(int round, File dir) {
        if (round == 1) census("census");
        this.roundNo = round;
        this.dir = dir;
        // settle at noon, then turn the time to night at the start of a tick; that tick is tick 1
        Bukkit.getScheduler().runTaskLater(this, () -> {
            measured = 0;
            lampsOn = lampsOff = 0;
            counting = true;
            bench.setTime(400000L);
        }, 60L);
    }

    /** Each tick's own length, as the server measured it, with the lamps that switched in it. */
    @EventHandler
    public void tickEnd(ServerTickEndEvent e) {
        if (!counting) return;
        rows.add(roundNo + "," + (++measured) + "," + String.format("%.2f", e.getTickDuration()) + "," + lampsOn + "," + lampsOff);
        lampsOn = lampsOff = 0;
        if (measured >= ticks) {
            counting = false;
            int r = roundNo;
            File d = dir;
            // then back to noon: the lamps switch off again (four ticks later, as vanilla, then queued)
            Bukkit.getScheduler().runTask(this, () -> {
                census("after night " + r);
                bench.setTime(6000L);
                Bukkit.getScheduler().runTaskLater(this, () -> finish(r, d), 200L);
            });
        }
    }

    private void finish(int round, File dir) {
        census("after day " + round);
        World world = bench;
        bench = null;
        Bukkit.unloadWorld(world, false);
        if (!TEMPLATE) delete(dir.toPath());
        if (round < rounds) {
            Bukkit.getScheduler().runTaskLater(this, () -> round(round + 1), 40L);
            return;
        }
        try (PrintWriter w = new PrintWriter(out, "UTF-8")) {
            for (String row : rows) w.println(row);
        } catch (IOException ex) {
            throw new RuntimeException(ex);
        }
        getLogger().info("done: " + out);
        Bukkit.shutdown();
    }

    /** Counts the lamps and daylight detectors in the loaded chunks. */
    private void census(String label) {
        int off = 0, on = 0, detectors = 0, inverted = 0, torches = 0;
        for (Chunk chunk : bench.getLoadedChunks()) {
            for (int x = 0; x < 16; x++) for (int z = 0; z < 16; z++) for (int y = 0; y < 256; y++) {
                Material m = chunk.getBlock(x, y, z).getType();
                switch (m) {
                    case REDSTONE_LAMP_OFF: off++; break;
                    case REDSTONE_LAMP_ON: on++; break;
                    case DAYLIGHT_DETECTOR: detectors++; break;
                    case DAYLIGHT_DETECTOR_INVERTED: inverted++; break;
                    case REDSTONE_TORCH_ON: case REDSTONE_TORCH_OFF: torches++; break;
                    default:
                }
            }
        }
        getLogger().info(label + ": lamps off " + off + ", lamps on " + on + ", daylight detectors " + detectors
                + ", inverted " + inverted + ", redstone torches " + torches);
    }

    private static void copy(Path from, Path to) throws IOException {
        try (Stream<Path> walk = Files.walk(from)) {
            for (Path p : (Iterable<Path>) walk::iterator) {
                String rel = from.relativize(p).toString();
                if (rel.equals("session.lock") || rel.equals("uid.dat")) continue;
                Path dest = to.resolve(rel);
                if (Files.isDirectory(p)) Files.createDirectories(dest);
                else Files.copy(p, dest, StandardCopyOption.REPLACE_EXISTING);
            }
        }
    }

    private static void delete(Path dir) {
        try (Stream<Path> walk = Files.walk(dir)) {
            walk.sorted(Comparator.reverseOrder()).forEach(p -> p.toFile().delete());
        } catch (IOException ignored) {
        }
    }
}
