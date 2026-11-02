package athena.wtprobe;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.WorldCreator;
import org.bukkit.WorldType;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.EntityType;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.block.BlockPhysicsEvent;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerMoveEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerTeleportEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitTask;

import athena.api.Athena;

/**
 * Test harness for running worlds on their own tick threads (feature/world-threads).
 *
 * Driven from the console (see run_wt.py). It creates small flat worlds, moves players between them,
 * freezes the thread that ticks a chosen world, and writes two files to its data folder:
 *
 *   ticks.csv   every 100 ms, from its own thread: each world's full time, so a world that keeps
 *               ticking while another is frozen shows up as a full time that keeps rising
 *   threads.csv per world and event, which threads the events came from: once worlds run on their
 *               own threads, an event of a world must come from that world's thread
 */
public final class WorldThreadsProbe extends JavaPlugin implements Listener {

    private static final String PREFIX = "wt-";

    private final Random random = new Random(1);
    private final Map<String, AtomicLong> eventThreads = new ConcurrentHashMap<String, AtomicLong>();
    private volatile boolean sampling = true;
    private Thread sampler;
    private BukkitTask shuffle;

    @Override
    public void onEnable() {
        getDataFolder().mkdirs();
        getServer().getPluginManager().registerEvents(this, this);
        sampler = new Thread(this::sample, "wtprobe-sampler");
        sampler.setDaemon(true);
        sampler.start();
        getLogger().info("ready");
    }

    @Override
    public void onDisable() {
        sampling = false;
        writeThreads();
    }

    // --- sampling ---------------------------------------------------------------------------------

    private void sample() {
        File file = new File(getDataFolder(), "ticks.csv");
        try (PrintWriter out = new PrintWriter(new FileWriter(file))) {
            out.println("ms,world,full_time");
            while (sampling) {
                long now = System.currentTimeMillis();
                // getWorlds() copies the list; a world unloaded meanwhile still answers its last time
                for (World world : new ArrayList<World>(Bukkit.getWorlds())) {
                    out.println(now + "," + world.getName() + "," + world.getFullTime());
                }
                out.flush();
                Thread.sleep(100L);
            }
        } catch (IOException | InterruptedException e) {
            getLogger().warning("sampler stopped: " + e);
        }
    }

    private void count(World world, String event) {
        String key = world.getName() + "," + event + "," + Thread.currentThread().getName();
        AtomicLong n = eventThreads.get(key);
        if (n == null) {
            AtomicLong fresh = new AtomicLong();
            n = eventThreads.putIfAbsent(key, fresh);
            if (n == null) n = fresh;
        }
        n.incrementAndGet();
    }

    private void writeThreads() {
        File file = new File(getDataFolder(), "threads.csv");
        try (PrintWriter out = new PrintWriter(new FileWriter(file))) {
            out.println("world,event,thread,count");
            for (Map.Entry<String, AtomicLong> e : eventThreads.entrySet()) {
                out.println(e.getKey() + "," + e.getValue().get());
            }
        } catch (IOException e) {
            getLogger().warning("could not write threads.csv: " + e);
        }
    }

    @EventHandler
    public void onMove(PlayerMoveEvent event) {
        count(event.getTo().getWorld(), "move");
    }

    @EventHandler
    public void onDamage(EntityDamageEvent event) {
        count(event.getEntity().getWorld(), "damage");
    }

    @EventHandler
    public void onPhysics(BlockPhysicsEvent event) {
        count(event.getBlock().getWorld(), "physics");
    }

    @EventHandler
    public void onTeleport(PlayerTeleportEvent event) {
        count(event.getFrom().getWorld(), "teleport-from");
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        count(event.getPlayer().getWorld(), "join");
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        count(event.getPlayer().getWorld(), "quit");
    }

    // --- commands ---------------------------------------------------------------------------------

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 0) {
            sender.sendMessage("wt create <n> [threaded] | thread <world> on|off | distribute | shuffle <period ticks> <seconds> | freeze <world> <ms> | spawn <world> <n> | status | dump");
            return true;
        }
        switch (args[0]) {
            case "create": create(sender, Integer.parseInt(args[1]), args.length > 2 && args[2].equals("threaded")); break;
            case "thread": thread(sender, args[1], args[2].equals("on")); break;
            case "distribute": distribute(sender); break;
            case "shuffle": shuffle(sender, Integer.parseInt(args[1]), Integer.parseInt(args[2])); break;
            case "freeze": freeze(sender, args[1], Long.parseLong(args[2])); break;
            case "spawn": spawn(sender, args[1], Integer.parseInt(args[2])); break;
            case "status": status(sender); break;
            case "dump": writeThreads(); sender.sendMessage("wt dumped"); break;
            default: sender.sendMessage("wt: unknown " + args[0]);
        }
        return true;
    }

    private List<World> testWorlds() {
        List<World> worlds = new ArrayList<World>();
        for (World world : Bukkit.getWorlds()) {
            if (world.getName().startsWith(PREFIX)) worlds.add(world);
        }
        return worlds;
    }

    private void create(CommandSender sender, int n, boolean threaded) {
        long start = System.nanoTime();
        for (int i = 0; i < n; ++i) {
            String name = PREFIX + i;
            if (Bukkit.getWorld(name) != null) continue;
            World world = new WorldCreator(name).type(WorldType.FLAT).generateStructures(false).createWorld();
            // the spawn chunks stay loaded, so the pigs there keep ticking in a world nobody is in
            world.setKeepSpawnInMemory(true);
            world.setAutoSave(false);
            world.setSpawnLocation(0, 4, 0);
            if (threaded) Athena.startWorldThread(world);
        }
        sender.sendMessage("wt created " + testWorlds().size() + " worlds in " + (System.nanoTime() - start) / 1_000_000L + " ms" + (threaded ? ", each on its own thread" : ""));
    }

    private void thread(CommandSender sender, String name, boolean on) {
        World world = Bukkit.getWorld(name);
        if (world == null) {
            sender.sendMessage("wt: no world " + name);
            return;
        }
        if (on) {
            Athena.startWorldThread(world);
            sender.sendMessage("wt thread " + name + " on");
        } else {
            sender.sendMessage("wt thread " + name + " off: " + Athena.stopWorldThread(world));
        }
    }

    private Location spawnOf(World world) {
        return new Location(world, 0.5 + random.nextInt(9) - 4, 4.0, 0.5 + random.nextInt(9) - 4);
    }

    private void distribute(CommandSender sender) {
        List<World> worlds = testWorlds();
        int i = 0;
        for (Player player : Bukkit.getOnlinePlayers()) {
            player.teleport(spawnOf(worlds.get(i++ % worlds.size())));
        }
        sender.sendMessage("wt distributed " + i + " players over " + worlds.size() + " worlds");
    }

    private void shuffle(CommandSender sender, int periodTicks, int seconds) {
        if (shuffle != null) shuffle.cancel();
        final List<World> worlds = testWorlds();
        final long end = System.currentTimeMillis() + seconds * 1000L;
        final AtomicLong moves = new AtomicLong();
        shuffle = Bukkit.getScheduler().runTaskTimer(this, () -> {
            if (System.currentTimeMillis() >= end) {
                shuffle.cancel();
                shuffle = null;
                getLogger().info("wt shuffle done: " + moves.get() + " moves");
                return;
            }
            for (Player player : Bukkit.getOnlinePlayers()) {
                if (random.nextInt(4) != 0) continue;   // a quarter of the players each period
                World to = worlds.get(random.nextInt(worlds.size()));
                if (to == player.getWorld()) continue;
                player.teleport(spawnOf(to));
                moves.incrementAndGet();
            }
        }, periodTicks, periodTicks);
        sender.sendMessage("wt shuffling every " + periodTicks + " ticks for " + seconds + " s");
    }

    /**
     * Blocks the thread that ticks this world: its own thread if it has one, else the server
     * thread, which stops every world that has none.
     */
    private void freeze(CommandSender sender, String name, final long millis) {
        World world = Bukkit.getWorld(name);
        if (world == null) {
            sender.sendMessage("wt: no world " + name);
            return;
        }
        Runnable block = () -> {
            getLogger().info("wt freezing " + name + " on " + Thread.currentThread().getName() + " for " + millis + " ms at " + System.currentTimeMillis());
            try {
                Thread.sleep(millis);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            getLogger().info("wt froze " + name + " until " + System.currentTimeMillis());
        };
        Athena.runOnWorld(world, block);
        sender.sendMessage("wt freeze queued for " + name);
    }

    private void spawn(CommandSender sender, String name, int n) {
        World world = Bukkit.getWorld(name);
        if (world == null) {
            sender.sendMessage("wt: no world " + name);
            return;
        }
        Athena.runOnWorld(world, () -> {
            for (int i = 0; i < n; ++i) {
                world.spawnEntity(spawnOf(world), EntityType.PIG);
            }
            getLogger().info("wt spawned " + n + " pigs in " + name + " on " + Thread.currentThread().getName());
        });
    }

    private void status(CommandSender sender) {
        for (World world : Bukkit.getWorlds()) {
            // read on the thread that ticks the world
            Athena.runOnWorld(world, () -> getLogger().info("wt status " + world.getName() + " time " + world.getFullTime()
                + " players " + world.getPlayers().size() + " entities " + world.getEntities().size()
                + " chunks " + world.getLoadedChunks().length + " thread " + Thread.currentThread().getName()));
        }
    }
}
