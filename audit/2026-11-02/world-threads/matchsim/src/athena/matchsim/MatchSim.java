package athena.matchsim;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

import athena.api.Athena;
import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.GameMode;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.configuration.file.YamlConfiguration;
import org.bukkit.entity.Arrow;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.block.Action;
import org.bukkit.event.block.BlockBreakEvent;
import org.bukkit.event.block.BlockPlaceEvent;
import org.bukkit.event.entity.CreatureSpawnEvent;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.entity.FoodLevelChangeEvent;
import org.bukkit.event.player.PlayerChangedWorldEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.weather.WeatherChangeEvent;
import org.bukkit.inventory.ItemStack;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.potion.PotionEffect;
import org.bukkit.scoreboard.DisplaySlot;
import org.bukkit.scoreboard.Objective;
import org.bukkit.scoreboard.Scoreboard;

/**
 * Athena - a stand-in for SurvivalGames on the world-threads branch: runs SG-like matches, several at
 * once, each in its own copy of a map, with nothing but Bukkit and the Athena API. The real plugin is
 * to be rewritten for worlds on their own threads once Athena is done, so the bench must not depend
 * on it; this keeps what loads the server: the template map with its play area pinned and preloaded,
 * 24 players teleported in gradually, chests filled as they are opened, PvP with eliminations that
 * drop the inventory and turn the player into a hidden flying spectator, a deathmatch, a sidebar per
 * player updated every second, and the clean-up that sends everyone back to the lobby, unloads the
 * map and calls System.gc() a second later. Its chat lines are the ones sgbot.py follows.
 *
 * <p>The lobby (the server's first world) runs on the server thread. Each match runs on the thread
 * that ticks its world: its clock is an Athena.runOnWorldTimer task where the server has it (on a
 * plain Athena, Bukkit's scheduler), and its listeners fire there. With {@code threaded: true} each
 * match world gets its own thread (Athena.startWorldThread).
 *
 * <p>Every second each match and the lobby write a row to {@code ticks.csv}: ticks done, the longest
 * gap between two ticks, and how many gaps passed 60 and 100 ms - per world, measured on the thread
 * that ticks it.
 */
public final class MatchSim extends JavaPlugin implements Listener {
    static final String PREFIX = "&8[&6MCSG&8] ";

    // settings (config.yml)
    int perMatch, maxMatches, lobbySeconds, pregameSeconds, liveSeconds, predeathmatchSeconds, deathmatchSeconds,
            endSeconds, cleanupSeconds, deathmatchAt, pinRadius, deathmatchPinRadius, teleportsPerTick;
    boolean threaded, gcAfterMatch;
    File template;
    String mapName;
    List<double[]> spawns = new ArrayList<>(), deathmatchSpawns = new ArrayList<>();
    double[] center;
    int mapRadius;

    /** Whether this server has worlds on their own threads, and the world timer. */
    boolean hasWorldThreads, hasWorldTimers;

    /** Players waiting in the lobby, in order of arrival. Server thread. */
    final Set<UUID> queue = Collections.synchronizedSet(new LinkedHashSet<UUID>());
    final List<Match> matches = new CopyOnWriteArrayList<>();
    final Map<String, Match> byWorld = new ConcurrentHashMap<>();
    final Map<UUID, Board> boards = new ConcurrentHashMap<>();
    private int lobbyLeft = -1;
    private int seq;
    private final TickGaps lobbyGaps = new TickGaps();
    private long lobbyTicks;
    private BufferedWriter csv;
    int matchesStarted, matchesFinished;

    @Override
    public void onEnable() {
        saveDefaultConfigFile();
        FileConfiguration c = getConfig();
        perMatch = c.getInt("players-per-match", 24);
        maxMatches = c.getInt("max-matches", 1);
        threaded = c.getBoolean("threaded", false);
        gcAfterMatch = c.getBoolean("gc-after-match", true);
        lobbySeconds = c.getInt("durations.lobby", 20);
        pregameSeconds = c.getInt("durations.pre-game", 15);
        liveSeconds = c.getInt("durations.live-game", 300);
        predeathmatchSeconds = c.getInt("durations.pre-deathmatch", 30);
        deathmatchSeconds = c.getInt("durations.deathmatch", 120);
        endSeconds = c.getInt("durations.end-game", 15);
        cleanupSeconds = c.getInt("durations.clean-up", 5);
        deathmatchAt = c.getInt("deathmatch-at", 3);
        pinRadius = c.getInt("pin-radius", 300);
        deathmatchPinRadius = c.getInt("deathmatch-pin-radius", 48);
        teleportsPerTick = c.getInt("teleports-per-tick", 4);
        template = new File(c.getString("maps-folder"), "");
        loadMap(new File(c.getString("map-file")));

        hasWorldThreads = hasMethod("startWorldThread");
        hasWorldTimers = hasMethod("runOnWorldTimer");
        if (threaded && !hasWorldThreads) {
            getLogger().warning("threaded: true, but this server has no world threads; matches share the server thread");
            threaded = false;
        }
        getLogger().info("players-per-match " + perMatch + ", max-matches " + maxMatches + ", threaded " + threaded
                + ", world timers " + hasWorldTimers + ", map " + mapName);
        try {
            csv = new BufferedWriter(new FileWriter(new File(getDataFolder(), "ticks.csv")));
            csv.write("unix_ms,world,state,ticks,max_gap_ms,gaps_over_60,gaps_over_100,players,alive,spectators,thread_check\n");
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        Bukkit.getPluginManager().registerEvents(this, this);
        World lobby = lobby();
        lobby.setStorm(false);
        lobby.setThundering(false);
        lobby.setGameRuleValue("doMobSpawning", "false");
        lobby.setGameRuleValue("doDaylightCycle", "false");
        Bukkit.getScheduler().runTaskTimer(this, this::lobbyTick, 1L, 1L);
    }

    @Override
    public void onDisable() {
        try {
            synchronized (this) {
                csv.close();
            }
        } catch (IOException ignored) {
        }
    }

    private void saveDefaultConfigFile() {
        getDataFolder().mkdirs();
        if (!new File(getDataFolder(), "config.yml").exists()) {
            throw new IllegalStateException("MatchSim needs plugins/MatchSim/config.yml (the bench writes it)");
        }
    }

    private static boolean hasMethod(String name) {
        for (java.lang.reflect.Method method : Athena.class.getMethods()) {
            if (method.getName().equals(name)) return true;
        }
        return false;
    }

    /** An SG map yml: world, spawns, deathmatch-spawns, center, radius ("x, y, z, yaw, pitch"). */
    private void loadMap(File file) {
        YamlConfiguration y = YamlConfiguration.loadConfiguration(file);
        mapName = y.getString("name");
        template = new File(template, y.getString("world"));
        for (String s : y.getStringList("spawns")) spawns.add(parse(s));
        for (String s : y.getStringList("deathmatch-spawns")) deathmatchSpawns.add(parse(s));
        center = parse(y.getString("center"));
        mapRadius = y.getInt("radius", pinRadius);
        if (spawns.size() < perMatch) {
            throw new IllegalStateException(mapName + " has " + spawns.size() + " spawns, fewer than " + perMatch);
        }
    }

    private static double[] parse(String s) {
        String[] p = s.split(",");
        double[] out = new double[5];
        for (int i = 0; i < p.length && i < 5; i++) out[i] = Double.parseDouble(p[i].trim());
        return out;
    }

    static Location at(World world, double[] p) {
        return new Location(world, p[0], p[1], p[2], (float) p[3], (float) p[4]);
    }

    static String color(String s) {
        return ChatColor.translateAlternateColorCodes('&', s);
    }

    World lobby() {
        return Bukkit.getWorlds().get(0);
    }

    Location lobbySpawn() {
        return lobby().getSpawnLocation().add(0.5, 0, 0.5);
    }

    // ---- the lobby: server thread ----

    private void lobbyTick() {
        lobbyGaps.record();
        if (++lobbyTicks % 20 != 0) return;
        lobbySecond();
        List<Player> inLobby = lobby().getPlayers();
        row("lobby", lobbyLeft >= 0 ? "countdown" : "waiting", lobbyGaps, inLobby.size(), queue.size(), 0, threadCheckTotal());
        for (Player p : inLobby) {
            Board board = boards.get(p.getUniqueId());
            if (board != null) {
                board.show("&eLobby", new String[]{
                        "&fWaiting: &a" + queue.size(), "&fMatches: &a" + matches.size(),
                        "&fPlayed: &a" + matchesFinished, "", "&fMap: &a" + mapName, "&fAS: &a1"});
            }
        }
    }

    private void lobbySecond() {
        // players who left or are not in the lobby any more drop out of the queue
        List<Player> waiting = new ArrayList<>();
        synchronized (queue) {
            queue.removeIf(id -> {
                Player p = Bukkit.getPlayer(id);
                return p == null || p.getWorld() != lobby();
            });
            for (UUID id : queue) waiting.add(Bukkit.getPlayer(id));
        }
        if (waiting.size() < perMatch || matches.size() >= maxMatches) {
            lobbyLeft = -1;
            return;
        }
        List<Player> players = waiting.subList(0, perMatch);
        if (lobbyLeft < 0) lobbyLeft = lobbySeconds;
        if (lobbyLeft > 0) {
            if (lobbyLeft % 5 == 0 || lobbyLeft <= 3) {
                for (Player p : players) p.sendMessage(color(PREFIX + "&8[&e" + lobbyLeft + "&8] &aseconds until lobby ends!"));
            }
            lobbyLeft--;
            return;
        }
        lobbyLeft = -1;
        startMatch(new ArrayList<>(players));
    }

    private void startMatch(List<Player> players) {
        int id = ++seq;
        String name = "sg-" + mapName.toLowerCase() + "-" + id;
        long started = System.nanoTime();
        World world = Athena.loadTemplateWorld(name, template);
        world.setSpawnFlags(false, false);
        world.setGameRuleValue("doMobSpawning", "false");
        world.setGameRuleValue("doFireTick", "false");
        world.setGameRuleValue("randomTickSpeed", "0");
        world.setStorm(false);
        world.setThundering(false);
        world.setPVP(true);

        // as SurvivalGames: the play area is pinned and preloaded, and so is the deathmatch arena
        Location middle = new Location(world, center[0], center[1], center[2]);
        Athena.pinChunks(middle, mapRadius);
        int perTick = Math.max(4, (int) Math.ceil(Math.pow(2 * ((mapRadius + 15) >> 4) + 1, 2) / 200.0));
        Athena.preloadChunks(middle, mapRadius, perTick);
        if (!deathmatchSpawns.isEmpty()) {
            double minX = Double.MAX_VALUE, maxX = -Double.MAX_VALUE, minZ = Double.MAX_VALUE, maxZ = -Double.MAX_VALUE;
            for (double[] s : deathmatchSpawns) {
                minX = Math.min(minX, s[0]); maxX = Math.max(maxX, s[0]); minZ = Math.min(minZ, s[2]); maxZ = Math.max(maxZ, s[2]);
            }
            int r = (int) Math.ceil(Math.max(maxX - minX, maxZ - minZ) / 2) + deathmatchPinRadius;
            Location arena = new Location(world, (minX + maxX) / 2, center[1], (minZ + maxZ) / 2);
            Athena.addPinnedChunks(arena, r);
            Athena.preloadChunks(arena, r, Math.max(4, perTick / 4));
        }

        Match match = new Match(this, id, world, players);
        matches.add(match);
        byWorld.put(world.getName(), match);
        matchesStarted++;
        if (threaded) Athena.startWorldThread(world);
        getLogger().info("Match " + id + " (" + name + ") loaded in " + (System.nanoTime() - started) / 1_000_000
                + " ms, " + players.size() + " players, " + (threaded ? "own thread" : "server thread"));

        List<Location> destinations = new ArrayList<>();
        for (int i = 0; i < players.size(); i++) {
            Player p = players.get(i);
            queue.remove(p.getUniqueId());
            p.sendMessage(color(PREFIX + "&2Map: &a" + mapName + " &2by &a" + "Team_Elite"));
            destinations.add(at(world, spawns.get(i)));
        }
        Athena.teleportGradually(players, destinations, teleportsPerTick);
        match.startClock();
    }

    /** From a match's thread, when its clean-up is over: send its players back and unload the map. */
    void finish(Match match, List<Player> players) {
        Bukkit.getScheduler().runTask(this, () -> {
            Athena.teleportGradually(players, p -> lobbySpawn(), teleportsPerTick);
            unloadLater(match, 0);
        });
    }

    private void unloadLater(Match match, int attempt) {
        Bukkit.getScheduler().runTaskLater(this, () -> {
            World world = match.world;
            if (Bukkit.unloadWorld(world, false)) {
                matches.remove(match);
                byWorld.remove(world.getName());
                matchesFinished++;
                getLogger().info("Match " + match.id + " unloaded");
                if (gcAfterMatch) {
                    // as SurvivalGames: the old map would otherwise stay in the old generation until a full GC
                    Bukkit.getScheduler().runTaskLater(this, System::gc, 20L);
                }
            } else if (attempt < 30) {
                if (attempt >= 5) {
                    for (Player p : new ArrayList<>(world.getPlayers())) Athena.teleportAsync(p, lobbySpawn());
                }
                unloadLater(match, attempt + 1);
            } else {
                getLogger().warning("Match " + match.id + ": could not unload " + world.getName());
            }
        }, 20L);
    }

    // ---- players ----

    @EventHandler
    public void onJoin(PlayerJoinEvent e) {
        Player p = e.getPlayer();
        e.setJoinMessage(null);
        Scoreboard sb = Bukkit.getScoreboardManager().getNewScoreboard();
        Objective o = sb.registerNewObjective("mcsg", "dummy");
        o.setDisplaySlot(DisplaySlot.SIDEBAR);
        p.setScoreboard(sb);
        boards.put(p.getUniqueId(), new Board(sb, o));
        if (p.getWorld() != lobby()) {
            Athena.teleportAsync(p, lobbySpawn());
            return;
        }
        toLobby(p);
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent e) {
        e.setQuitMessage(null);
        Player p = e.getPlayer();
        queue.remove(p.getUniqueId());
        boards.remove(p.getUniqueId());
        Match match = byWorld.get(p.getWorld().getName());
        if (match != null) match.quit(p);
    }

    @EventHandler
    public void onChangedWorld(PlayerChangedWorldEvent e) {
        Player p = e.getPlayer();
        if (p.getWorld() == lobby()) {
            toLobby(p);
        }
    }

    /** On the server thread, for a player in the lobby: reset, visible to everyone there, queued. */
    private void toLobby(Player p) {
        reset(p);
        for (Player other : lobby().getPlayers()) {
            if (other == p) continue;
            other.showPlayer(p);
            p.showPlayer(other);
        }
        queue.add(p.getUniqueId());
        p.sendMessage(color(PREFIX + "&2Waiting in the lobby&8: [&e" + queue.size() + "&8/&e" + perMatch + "&8]"));
    }

    /** SurvivalGames' PlayerReset.reset, for what loads the server. */
    static void reset(Player player) {
        player.closeInventory();
        player.setGameMode(GameMode.SURVIVAL);
        player.setAllowFlight(true);   // the bots fly (the bench server allows it, as SG's bench did)
        player.setCanPickupItems(true);
        player.getInventory().clear();
        player.getInventory().setArmorContents(new ItemStack[4]);
        player.setItemOnCursor(null);
        player.updateInventory();
        player.setMaxHealth(20.0);
        player.setHealth(20.0);
        player.setFoodLevel(20);
        player.setSaturation(5f);
        player.setExhaustion(0f);
        player.setLevel(0);
        player.setExp(0f);
        for (PotionEffect effect : player.getActivePotionEffects()) player.removePotionEffect(effect.getType());
        player.setFireTicks(0);
        player.setFallDistance(0f);
    }

    // ---- what happens in the worlds: each on its own thread ----

    @EventHandler(priority = EventPriority.HIGHEST, ignoreCancelled = true)
    public void onDamage(EntityDamageEvent e) {
        if (!(e.getEntity() instanceof Player)) return;
        Player victim = (Player) e.getEntity();
        Match match = byWorld.get(victim.getWorld().getName());
        if (match == null) {
            e.setCancelled(true);   // the lobby
            return;
        }
        Player attacker = null;
        if (e instanceof EntityDamageByEntityEvent) {
            org.bukkit.entity.Entity damager = ((EntityDamageByEntityEvent) e).getDamager();
            if (damager instanceof Player) attacker = (Player) damager;
            else if (damager instanceof Arrow && ((Arrow) damager).getShooter() instanceof Player) attacker = (Player) ((Arrow) damager).getShooter();
        }
        match.damage(e, victim, attacker);
    }

    @EventHandler(ignoreCancelled = true)
    public void onInteract(PlayerInteractEvent e) {
        if (e.getAction() != Action.RIGHT_CLICK_BLOCK || e.getClickedBlock() == null) return;
        Match match = byWorld.get(e.getPlayer().getWorld().getName());
        if (match == null) {
            e.setCancelled(true);
            return;
        }
        match.interact(e);
    }

    @EventHandler(ignoreCancelled = true)
    public void onBreak(BlockBreakEvent e) {
        Match match = byWorld.get(e.getBlock().getWorld().getName());
        Material type = e.getBlock().getType();
        boolean breakable = type == Material.LEAVES || type == Material.LEAVES_2 || type == Material.WEB || type == Material.LONG_GRASS;
        if (match == null || !breakable || !match.fighting() || match.isSpectator(e.getPlayer())) e.setCancelled(true);
    }

    @EventHandler(ignoreCancelled = true)
    public void onPlace(BlockPlaceEvent e) {
        Match match = byWorld.get(e.getBlock().getWorld().getName());
        if (match == null || !match.fighting() || e.getBlock().getType() != Material.CAKE_BLOCK) e.setCancelled(true);
    }

    @EventHandler(ignoreCancelled = true)
    public void onFood(FoodLevelChangeEvent e) {
        Match match = byWorld.get(e.getEntity().getWorld().getName());
        if (match == null || !match.fighting()) e.setCancelled(true);
    }

    @EventHandler(ignoreCancelled = true)
    public void onSpawn(CreatureSpawnEvent e) {
        if (e.getSpawnReason() != CreatureSpawnEvent.SpawnReason.CUSTOM) e.setCancelled(true);
    }

    @EventHandler(ignoreCancelled = true)
    public void onWeather(WeatherChangeEvent e) {
        if (e.toWeatherState()) e.setCancelled(true);
    }

    // ---- the record ----

    synchronized void row(String world, String state, TickGaps gaps, int players, int alive, int spectators, long threadCheck) {
        try {
            csv.write(System.currentTimeMillis() + "," + world + "," + state + "," + gaps.ticks + "," + gaps.maxMillis() + ","
                    + gaps.over60 + "," + gaps.over100 + "," + players + "," + alive + "," + spectators + "," + threadCheck + "\n");
            if (System.currentTimeMillis() / 1000 % 5 == 0) csv.flush();
        } catch (IOException ignored) {
        }
        gaps.reset();
    }

    /** athena.server.AthenaThreadCheck.total(), where the server has it (-Dathena.threadCheck=true); else -1. */
    static long threadCheckTotal() {
        try {
            Class<?> c = Class.forName("athena.server.AthenaThreadCheck");
            if (!c.getField("ENABLED").getBoolean(null)) return -1;
            return (Long) c.getMethod("total").invoke(null);
        } catch (Throwable t) {
            return -1;
        }
    }

    @SuppressWarnings("unchecked")
    static List<String> threadCheckReport() {
        try {
            return (List<String>) Class.forName("athena.server.AthenaThreadCheck").getMethod("report").invoke(null);
        } catch (Throwable t) {
            return Collections.singletonList("no thread check on this server");
        }
    }

    // ---- commands (console): sim status | sim freeze <match id|lobby> <ms> | sim threadcheck ----

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 0 || args[0].equals("status")) {
            sender.sendMessage("matches started " + matchesStarted + ", finished " + matchesFinished + ", queue " + queue.size()
                    + ", online " + Bukkit.getOnlinePlayers().size() + ", thread check " + threadCheckTotal());
            for (Match m : matches) sender.sendMessage("  " + m.describe());
            return true;
        }
        if (args[0].equals("freeze") && args.length == 3) {
            final long ms = Long.parseLong(args[2]);
            Runnable sleep = () -> {
                getLogger().info("freezing " + args[1] + " for " + ms + " ms on " + Thread.currentThread().getName());
                try {
                    Thread.sleep(ms);
                } catch (InterruptedException ignored) {
                }
            };
            if (args[1].equals("lobby")) {
                Bukkit.getScheduler().runTask(this, sleep);
            } else if (args[1].startsWith("m")) {
                // "m2": the second of the matches running now, whatever its id
                int k = Integer.parseInt(args[1].substring(1));
                if (k >= 1 && k <= matches.size()) matches.get(k - 1).runOnThread(sleep);
                else getLogger().warning("no running match " + args[1]);
            } else {
                int id = Integer.parseInt(args[1]);
                boolean found = false;
                for (Match m : matches) {
                    if (m.id == id) { m.runOnThread(sleep); found = true; }
                }
                if (!found) getLogger().warning("no running match " + id);
            }
            return true;
        }
        if (args[0].equals("threadcheck")) {
            sender.sendMessage("thread check total " + threadCheckTotal());
            for (String line : threadCheckReport()) sender.sendMessage("  " + line);
            return true;
        }
        return false;
    }

    // ---- the sidebar: one Bukkit scoreboard per player, only changed lines sent (as SG's FastBoard) ----

    static final class Board {
        final Scoreboard scoreboard;
        final Objective objective;
        String title;
        final String[] sent = new String[10];

        Board(Scoreboard scoreboard, Objective objective) {
            this.scoreboard = scoreboard;
            this.objective = objective;
        }

        /** On the thread that owns the player. */
        void show(String newTitle, String[] lines) {
            if (!newTitle.equals(title)) {
                title = newTitle;
                objective.setDisplayName(color(newTitle));
            }
            for (int i = 0; i < sent.length; i++) {
                String line = i < lines.length ? color(lines[i]) + ChatColor.values()[i] : null;   // unique, even if empty
                if (line != null && line.length() > 40) line = line.substring(0, 40);
                if (line == null ? sent[i] == null : line.equals(sent[i])) continue;
                if (sent[i] != null) scoreboard.resetScores(sent[i]);
                if (line != null) objective.getScore(line).setScore(sent.length - i);
                sent[i] = line;
            }
        }
    }

    /** Gaps between ticks of one world, measured on the thread that ticks it. */
    static final class TickGaps {
        private long last;
        long ticks, maxNanos, over60, over100;

        void record() {
            long now = System.nanoTime();
            if (last != 0) {
                long gap = now - last;
                if (gap > maxNanos) maxNanos = gap;
                if (gap > 60_000_000L) over60++;
                if (gap > 100_000_000L) over100++;
            }
            last = now;
            ticks++;
        }

        long maxMillis() {
            return maxNanos / 1_000_000L;
        }

        void reset() {
            ticks = maxNanos = over60 = over100 = 0;
        }
    }
}
