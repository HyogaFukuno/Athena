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
import org.bukkit.event.player.PlayerInitialSpawnEvent;
import org.bukkit.event.player.PlayerChangedWorldEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerRespawnEvent;
import org.bukkit.event.player.PlayerMoveEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerTeleportEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitTask;

import athena.api.Athena;
import athena.api.event.WorldThreadFailureEvent;
import athena.api.event.WorldThreadStallEvent;

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
    /** A world logins are sent to (PlayerInitialSpawnEvent), or null. */
    private volatile String joinWorld;

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
    public void onStall(WorldThreadStallEvent event) {
        getLogger().info("wt stall " + event.getWorld().getName() + " " + event.getStalledMillis() + " ms recovered=" + event.isRecovered()
            + " on " + Thread.currentThread().getName());
    }

    @EventHandler
    public void onFailure(WorldThreadFailureEvent event) {
        getLogger().info("wt failure " + event.getWorld().getName() + " players " + event.getWorld().getPlayers().size()
            + " cause " + event.getCause() + " on " + Thread.currentThread().getName());
    }

    @EventHandler
    public void onInitialSpawn(PlayerInitialSpawnEvent event) {
        String name = joinWorld;
        World world = name == null ? null : Bukkit.getWorld(name);
        if (world != null) event.setSpawnLocation(spawnOf(world));
    }

    @EventHandler
    public void onChangedWorld(PlayerChangedWorldEvent event) {
        // fires when the player has arrived: on the thread that ticks the new world
        count(event.getPlayer().getWorld(), "arrived");
        if (!Athena.isWorldThread(event.getPlayer().getWorld())) {
            getLogger().warning("wt arrived off its world's thread: " + event.getPlayer().getName() + " " + event.getFrom().getName() + " -> "
                + event.getPlayer().getWorld().getName() + " on " + Thread.currentThread().getName());
            new Throwable("wt arrival stack").printStackTrace();
        }
        if (event.getPlayer().getName().startsWith("wtj")) {
            getLogger().info("wt arrived " + event.getPlayer().getName() + " " + event.getFrom().getName() + " -> "
                + event.getPlayer().getWorld().getName() + " on " + Thread.currentThread().getName());
        }
    }

    @EventHandler
    public void onRespawn(PlayerRespawnEvent event) {
        count(event.getRespawnLocation().getWorld(), "respawn-to");
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
        if (command.getName().equals("wtping")) {
            // a player's command runs on the thread that ticks the player's world
            getLogger().info("wtping " + sender.getName() + " in " + (sender instanceof Player ? ((Player) sender).getWorld().getName() : "-")
                + " on " + Thread.currentThread().getName());
            count(sender instanceof Player ? ((Player) sender).getWorld() : Bukkit.getWorlds().get(0), "command");
            return true;
        }
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
            case "joinworld": joinWorld = args[1].equals("off") ? null : args[1]; sender.sendMessage("wt joinworld " + joinWorld); break;
            case "kill": kill(sender, Integer.parseInt(args[1])); break;
            case "check": check(sender); break;
            case "crash": {
                // the next tick of that world throws (a test hook in AthenaWorldThread, set by reflection)
                try {
                    Class<?> type = Class.forName("net.minecraft.server.v1_8_R3.AthenaWorldThread");
                    java.lang.reflect.Field field = type.getDeclaredField("crashForTest");
                    field.setAccessible(true);
                    field.set(null, args[1]);
                    sender.sendMessage("wt crash armed for " + args[1]);
                } catch (ReflectiveOperationException e) {
                    sender.sendMessage("wt crash: " + e);
                }
                break;
            }
            case "preload": {
                // asked from the console (the server thread) for a world that may tick on its own thread
                World world = Bukkit.getWorld(args[1]);
                Athena.preloadChunks(new Location(world, 0, 4, 0), Integer.parseInt(args[2]), 4).whenComplete((n, t) ->
                    getLogger().info("wt preloaded " + n + " chunks in " + world.getName() + " (error " + t + ") on " + Thread.currentThread().getName()));
                break;
            }
            case "gather": {
                // asked from the thread of a world: its players go to the main world a few per tick
                World world = Bukkit.getWorld(args[1]);
                World main = Bukkit.getWorlds().get(0);
                Athena.runOnWorld(world, () -> {
                    List<Player> players = new ArrayList<Player>(world.getPlayers());
                    Athena.teleportGradually(players, player -> spawnOf(main), 2).whenComplete((n, t) ->
                        getLogger().info("wt gathered " + n + " of " + players.size() + " from " + world.getName() + " (error " + t + ") on " + Thread.currentThread().getName()));
                });
                break;
            }
            case "where":
                for (Player player : Bukkit.getOnlinePlayers()) getLogger().info("wt where " + player.getName() + " " + player.getWorld().getName());
                break;
            case "dump": writeThreads(); sender.sendMessage("wt dumped"); break;
            case "hidecross": hideCross(args[1], true); break;
            case "showcross": hideCross(args[1], false); break;
            case "seecheck": seeCheck(); break;
            case "movepigs": movePigs(args[1], args[2], Integer.parseInt(args[3])); break;
            case "hammer": hammer(Integer.parseInt(args[1])); break;
            case "times":
                for (World world : Bukkit.getWorlds()) {
                    Athena.runOnWorld(world, () -> getLogger().info("wt time " + world.getName() + " " + world.getTime() + " storm " + world.hasStorm()
                            + " doMobLoot " + world.getGameRuleValue("doMobLoot") + " pigs " + world.getEntitiesByClass(org.bukkit.entity.Pig.class).size()));
                }
                break;
            case "kickworld": {
                // from the server thread: everyone in that world is kicked (its thread may be stuck)
                int n = 0;
                for (Player player : new ArrayList<Player>(Bukkit.getOnlinePlayers())) {
                    if (player.getWorld().getName().equals(args[1])) { player.kickPlayer("wt kickworld"); n++; }
                }
                getLogger().info("wt kickworld " + args[1] + " kicked " + n + " on " + Thread.currentThread().getName());
                break;
            }
            case "enterunload": {
                // from the server thread: the players of the main world are sent into a world on its own
                // thread, which is unloaded at once, before that thread has taken them in
                World world = Bukkit.getWorld(args[1]);
                World main = Bukkit.getWorlds().get(0);
                int sent = 0;
                for (Player player : new ArrayList<Player>(main.getPlayers())) {
                    if (player.teleport(spawnOf(world))) sent++;
                }
                boolean unloaded = Bukkit.unloadWorld(world, false);
                getLogger().info("wt enterunload sent " + sent + " into " + args[1] + ", unloaded " + unloaded);
                break;
            }
            case "threadcheck": {
                try {
                    Class<?> c = Class.forName("athena.server.AthenaThreadCheck");
                    getLogger().info("wt threadcheck " + c.getMethod("total").invoke(null) + " " + c.getMethod("report").invoke(null));
                } catch (ReflectiveOperationException e) {
                    getLogger().info("wt threadcheck unavailable " + e);
                }
                break;
            }
            default: sender.sendMessage("wt: unknown " + args[0]);
        }
        return true;
    }

    // ---- phase 7: the limits that were lifted ----

    /**
     * From the thread of {@code from}: every online player hides (or shows) every player who is not in
     * {@code from}, so viewers and hidden players sit in worlds ticked by other threads.
     */
    private void hideCross(String from, boolean hide) {
        World world = Bukkit.getWorld(from);
        Athena.runOnWorld(world, () -> {
            int n = 0;
            for (Player viewer : Bukkit.getOnlinePlayers()) {
                for (Player other : Bukkit.getOnlinePlayers()) {
                    if (other == viewer || other.getWorld() == world) continue;
                    if (hide) viewer.hidePlayer(other); else viewer.showPlayer(other);
                    n++;
                }
            }
            getLogger().info("wt " + (hide ? "hidecross" : "showcross") + " " + n + " on " + Thread.currentThread().getName());
        });
    }

    private void seeCheck() {
        int hidden = 0, pairs = 0;
        for (Player viewer : Bukkit.getOnlinePlayers()) {
            for (Player other : Bukkit.getOnlinePlayers()) {
                if (other == viewer) continue;
                pairs++;
                if (!viewer.canSee(other)) hidden++;
            }
        }
        getLogger().info("wt seecheck hidden " + hidden + " of " + pairs);
    }

    /** From the thread of {@code from}: {@code n} of its pigs go to {@code to}'s spawn; two seconds later, where they are. */
    private void movePigs(String from, String to, int n) {
        World a = Bukkit.getWorld(from), b = Bukkit.getWorld(to);
        Athena.runOnWorld(a, () -> {
            List<org.bukkit.entity.Entity> moved = new ArrayList<org.bukkit.entity.Entity>();
            List<java.util.UUID> ids = new ArrayList<java.util.UUID>();
            int refused = 0;
            for (org.bukkit.entity.Pig pig : a.getEntitiesByClass(org.bukkit.entity.Pig.class)) {
                if (moved.size() + refused >= n) break;
                if (pig.teleport(spawnOf(b))) { moved.add(pig); ids.add(pig.getUniqueId()); } else refused++;
            }
            final int asked = moved.size(), no = refused;
            Bukkit.getScheduler().runTaskLater(this, () -> Athena.runOnWorld(b, () -> {
                int arrived = 0, handles = 0;
                for (org.bukkit.entity.Pig pig : b.getEntitiesByClass(org.bukkit.entity.Pig.class)) {
                    if (ids.contains(pig.getUniqueId())) arrived++;
                }
                for (org.bukkit.entity.Entity e : moved) {
                    if (e.isValid() && e.getWorld() == b) handles++;
                }
                getLogger().info("wt movepigs " + from + " -> " + to + ": teleported " + asked + ", refused " + no + ", arrived " + arrived
                        + ", Bukkit entities now in " + to + " " + handles + ", left in " + from + " " + a.getEntitiesByClass(org.bukkit.entity.Pig.class).size()
                        + " (counted on " + Thread.currentThread().getName() + ")");
            }), 40L);
        });
    }

    /**
     * For {@code seconds}: every tick, the server thread and the thread of every test world each make
     * 40 changes to the main scoreboard (teams, entries, scores, an objective's display name).
     */
    private void hammer(int seconds) {
        final org.bukkit.scoreboard.Scoreboard board = Bukkit.getScoreboardManager().getMainScoreboard();
        if (board.getObjective("wt") == null) board.registerNewObjective("wt", "dummy");
        final java.util.concurrent.atomic.AtomicLong ops = new java.util.concurrent.atomic.AtomicLong();
        final java.util.concurrent.atomic.AtomicLong errors = new java.util.concurrent.atomic.AtomicLong();
        final long until = System.currentTimeMillis() + seconds * 1000L;
        List<World> worlds = new ArrayList<World>(testWorlds());
        worlds.add(Bukkit.getWorlds().get(0));
        final java.util.concurrent.atomic.AtomicInteger running = new java.util.concurrent.atomic.AtomicInteger(worlds.size());
        int k = 0;
        for (World world : worlds) {
            final int me = k++;
            final Random r = new Random(me);
            final athena.api.WorldTask[] task = new athena.api.WorldTask[1];
            task[0] = Athena.runOnWorldTimer(world, () -> {
                if (System.currentTimeMillis() > until) {
                    task[0].cancel();
                    if (running.decrementAndGet() == 0) {
                        getLogger().info("wt hammer done: " + ops.get() + " changes, " + errors.get() + " errors, " + board.getTeams().size()
                                + " teams, " + board.getEntries().size() + " entries");
                    }
                    return;
                }
                for (int i = 0; i < 40; i++) {
                    try {
                        String team = "t" + r.nextInt(16), entry = "e" + me + "_" + r.nextInt(50);
                        org.bukkit.scoreboard.Team t = board.getTeam(team);
                        if (t == null) {
                            try { t = board.registerNewTeam(team); } catch (IllegalArgumentException taken) { t = board.getTeam(team); }
                        }
                        switch (r.nextInt(5)) {
                            case 0: if (t != null) t.addEntry(entry); break;
                            case 1: if (t != null) t.removeEntry(entry); break;
                            case 2: board.getObjective("wt").getScore(entry).setScore(r.nextInt(100)); break;
                            case 3: board.resetScores(entry); break;
                            default: if (t != null) t.setPrefix("p" + r.nextInt(10)); for (org.bukkit.scoreboard.Team each : board.getTeams()) each.getSize(); break;
                        }
                        ops.incrementAndGet();
                    } catch (Throwable throwable) {
                        if (errors.incrementAndGet() <= 5) getLogger().warning("wt hammer error on " + Thread.currentThread().getName() + ": " + throwable);
                    }
                }
            }, 1L, 1L);
        }
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
        worlds.add(Bukkit.getWorlds().get(0)); // and back to the main world, which the server thread ticks
        final long end = System.currentTimeMillis() + seconds * 1000L;
        final AtomicLong moves = new AtomicLong();
        final AtomicLong refused = new AtomicLong();
        final AtomicLong early = new AtomicLong();
        shuffle = Bukkit.getScheduler().runTaskTimer(this, () -> {
            if (System.currentTimeMillis() >= end) {
                shuffle.cancel();
                shuffle = null;
                getLogger().info("wt shuffle done: " + moves.get() + " moves, " + refused.get() + " refused, " + early.get() + " completed before the player was there");
                return;
            }
            for (Player player : Bukkit.getOnlinePlayers()) {
                if (random.nextInt(4) != 0) continue;   // a quarter of the players each period
                World to = worlds.get(random.nextInt(worlds.size()));
                if (to == player.getWorld()) continue;
                // the player's own world's thread moves it (Athena.teleportAsync), whichever thread that is
                Athena.teleportAsync(player, spawnOf(to)).thenAccept(ok -> {
                    if (ok) moves.incrementAndGet(); else refused.incrementAndGet();
                    // the future completes once the player is there, on the thread that ticks it now
                    if (ok && (player.getWorld() != to || !Athena.isWorldThread(to))) early.incrementAndGet();
                });
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

    /** Kills n players, each on the thread that ticks its world; the bots respawn. */
    private void kill(CommandSender sender, int n) {
        List<Player> players = new ArrayList<Player>(Bukkit.getOnlinePlayers());
        java.util.Collections.shuffle(players, random);
        for (Player player : players.subList(0, Math.min(n, players.size()))) {
            Athena.runOnWorld(player.getWorld(), () -> {
                if (!Athena.isWorldThread(player.getWorld())) return; // it moved meanwhile
                player.setHealth(0.0D);
            });
        }
        sender.sendMessage("wt killed " + Math.min(n, players.size()));
    }

    /**
     * Every world's player list, read on the thread that ticks it: each online player must be in
     * exactly one world, and no world may hold one that is gone.
     */
    private void check(CommandSender sender) {
        final Map<String, List<String>> seen = new ConcurrentHashMap<String, List<String>>();
        final List<World> worlds = new ArrayList<World>(Bukkit.getWorlds());
        final java.util.concurrent.atomic.AtomicInteger left = new java.util.concurrent.atomic.AtomicInteger(worlds.size());
        for (World world : worlds) {
            Athena.runOnWorld(world, () -> {
                for (Player player : world.getPlayers()) {
                    seen.computeIfAbsent(player.getName(), k -> java.util.Collections.synchronizedList(new ArrayList<String>())).add(world.getName());
                }
                if (left.decrementAndGet() == 0) {
                    Bukkit.getScheduler().runTask(this, () -> {
                        int online = 0, missing = 0, twice = 0, ghosts = 0;
                        for (Player player : Bukkit.getOnlinePlayers()) {
                            ++online;
                            List<String> in = seen.get(player.getName());
                            if (in == null) ++missing; else if (in.size() > 1) ++twice;
                        }
                        for (String name : seen.keySet()) if (Bukkit.getPlayerExact(name) == null) ++ghosts;
                        getLogger().info("wt check online " + online + " missing " + missing + " twice " + twice + " ghosts " + ghosts);
                    });
                }
            });
        }
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
