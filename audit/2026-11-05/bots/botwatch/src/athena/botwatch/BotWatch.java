package athena.botwatch;

import athena.api.Athena;
import com.destroystokyo.paper.event.server.ServerTickEndEvent;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.World;
import org.bukkit.entity.Player;
import org.bukkit.entity.Projectile;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityShootBowEvent;
import org.bukkit.event.inventory.InventoryOpenEvent;
import org.bukkit.event.inventory.InventoryType;
import org.bukkit.event.player.PlayerItemConsumeEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.world.WorldUnloadEvent;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Athena - watches SurvivalGames played by bots. Every 5 seconds it logs a "BOTWATCH" line (and a row in
 * botwatch.csv): per match world the players alive (not flying) and, since the last line, the hits between
 * players, chests opened, things eaten, arrows shot, blocks the bots walked, and the longest tick of any
 * thread. Logs "BOTWATCH match over <world> winner=<names>" when a match world unloads. Stops the server
 * after -Dbotwatch.matches=N match worlds have unloaded (default 1).
 */
public final class BotWatch extends JavaPlugin implements Listener {

    private final AtomicInteger hits = new AtomicInteger(), chests = new AtomicInteger(), eats = new AtomicInteger(),
            shots = new AtomicInteger(), joins = new AtomicInteger(), quits = new AtomicInteger();
    private final AtomicLong maxTickMicros = new AtomicLong();
    private final Map<String, Location> last = new ConcurrentHashMap<>();
    private double walked;
    private int matchesOver;
    private int matches;
    private FileWriter csv;
    private long started;
    private final Map<String, String> lastAlive = new ConcurrentHashMap<>();

    @Override
    public void onEnable() {
        matches = Integer.getInteger("botwatch.matches", 1);
        started = System.currentTimeMillis();
        try {
            csv = new FileWriter(new File(Bukkit.getWorldContainer(), "botwatch.csv"));
            csv.write("seconds,world,alive,bots_online,hits,chests,eats,shots,walked,max_tick_ms\n");
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
        getServer().getPluginManager().registerEvents(this, this);
        Bukkit.getScheduler().runTaskTimer(this, this::report, 100L, 100L);
        // how far the bots walk: sampled every second on the server thread (positions are read racily from
        // world threads; a sample is only a rough distance)
        Bukkit.getScheduler().runTaskTimer(this, () -> {
            for (var bot : Athena.getBots()) {
                Player p = bot.getPlayer();
                Location now = p.getLocation();
                Location before = last.put(p.getName(), now);
                if (before != null && before.getWorld() == now.getWorld()) {
                    double d = before.distance(now);
                    if (d < 15) walked += d;
                }
            }
        }, 20L, 20L);
    }

    @EventHandler
    public void tick(ServerTickEndEvent e) {
        long micros = (long) (e.getTickDuration() * 1000);
        maxTickMicros.accumulateAndGet(micros, Math::max);
    }

    /** -Dbotwatch.protect=NAME: that player takes no damage (a watching client, sgwatcher.py, lasts to the deathmatch). */
    private static final String PROTECT = System.getProperty("botwatch.protect", "");

    @EventHandler(priority = EventPriority.LOWEST)
    public void protect(org.bukkit.event.entity.EntityDamageEvent e) {
        if (!PROTECT.isEmpty() && e.getEntity() instanceof Player p && p.getName().equals(PROTECT)) e.setCancelled(true);
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void hit(EntityDamageByEntityEvent e) {
        var damager = e.getDamager();
        if (damager instanceof Projectile projectile && projectile.getShooter() instanceof Player shooter) damager = shooter;
        if (damager instanceof Player && e.getEntity() instanceof Player) hits.incrementAndGet();
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void open(InventoryOpenEvent e) {
        if (e.getInventory().getType() == InventoryType.CHEST || e.getInventory().getType() == InventoryType.ENDER_CHEST) chests.incrementAndGet();
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void eat(PlayerItemConsumeEvent e) {
        eats.incrementAndGet();
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void shoot(EntityShootBowEvent e) {
        shots.incrementAndGet();
    }

    @EventHandler public void join(PlayerJoinEvent e) { joins.incrementAndGet(); }
    @EventHandler public void quit(PlayerQuitEvent e) { quits.incrementAndGet(); }

    @EventHandler
    public void unload(WorldUnloadEvent e) {
        String name = e.getWorld().getName();
        if (!name.startsWith("sg-")) return;
        getLogger().info("BOTWATCH match over " + name + " last alive: " + lastAlive.getOrDefault(name, "?"));
        if (name.contains("-dm-")) return; // a deathmatch venue: the match goes on
        if (++matchesOver >= matches) {
            Bukkit.getScheduler().runTaskLater(this, () -> {
                getLogger().info("BOTWATCH DONE");
                Bukkit.shutdown();
            }, 40L);
        }
    }

    private void report() {
        long seconds = (System.currentTimeMillis() - started) / 1000;
        int h = hits.getAndSet(0), c = chests.getAndSet(0), ea = eats.getAndSet(0), s = shots.getAndSet(0);
        double tick = maxTickMicros.getAndSet(0) / 1000.0;
        int bots = Athena.getBots().size();
        StringBuilder worlds = new StringBuilder();
        for (World world : Bukkit.getWorlds()) {
            if (!world.getName().startsWith("sg-")) continue;
            int alive = 0;
            StringBuilder names = new StringBuilder();
            for (Player p : world.getPlayers()) {
                if (!p.getAllowFlight()) {
                    alive++;
                    names.append(p.getName()).append(String.format("(%.0f)", p.getHealth())).append(' ');
                }
            }
            lastAlive.put(world.getName(), names.toString().trim());
            worlds.append(world.getName()).append(" alive=").append(alive).append(' ');
            write(seconds + "," + world.getName() + "," + alive + "," + bots + "," + h + "," + c + "," + ea + "," + s + ","
                    + String.format("%.0f", walked) + "," + String.format("%.1f", tick) + "\n");
        }
        getLogger().info(String.format("BOTWATCH t=%ds bots=%d %shits=%d chests=%d eats=%d shots=%d walked=%.0f maxTick=%.1fms joins=%d quits=%d",
                seconds, bots, worlds, h, c, ea, s, walked, tick, joins.get(), quits.get()));
        walked = 0;
        if (Boolean.getBoolean("sgbots.debug")) {
            for (var bot : Athena.getBots()) {
                Player p = bot.getPlayer();
                String inside = "?";
                try {
                    Object handle = p.getClass().getMethod("getHandle").invoke(p);
                    Object world = handle.getClass().getField("world").get(handle);
                    java.util.List<?> entities = (java.util.List<?>) world.getClass().getField("entityList").get(world);
                    inside = "transit=" + handle.getClass().getField("athenaInTransit").getBoolean(handle)
                            + " removed=" + handle.getClass().getField("dead").getBoolean(handle)
                            + " ticksLived=" + handle.getClass().getField("ticksLived").getInt(handle)
                            + " inEntityList=" + entities.contains(handle);
                    var cf = handle.getClass().getDeclaredField("controller");
                    cf.setAccessible(true);
                    Object controller = cf.get(handle);
                    inside += " controller=" + (controller == null ? null : controller.getClass().getSimpleName());
                    if (controller != null) {
                        var tf = controller.getClass().getDeclaredField("tick");
                        tf.setAccessible(true);
                        var mf = controller.getClass().getDeclaredField("mode");
                        mf.setAccessible(true);
                        inside += " brainTick=" + tf.getLong(controller) + " mode=" + mf.get(controller);
                    }
                } catch (ReflectiveOperationException | RuntimeException e) {
                    inside = e.toString();
                }
                StringBuilder around = new StringBuilder(" chunks=");
                int cx = p.getLocation().getBlockX() >> 4, cz = p.getLocation().getBlockZ() >> 4;
                for (int dz = -1; dz <= 1; dz++) {
                    for (int dx = -1; dx <= 1; dx++) around.append(p.getWorld().isChunkLoaded(cx + dx, cz + dz) ? '#' : '.');
                    around.append('/');
                }
                inside += around;
                getLogger().info(String.format("BOTWATCH bot %s world=%s at=%.1f,%.1f,%.1f health=%.1f dead=%s flying=%s valid=%s %s",
                        p.getName(), p.getWorld().getName(), p.getLocation().getX(), p.getLocation().getY(), p.getLocation().getZ(),
                        p.getHealth(), p.isDead(), p.isFlying(), p.isValid(), inside));
            }
        }
    }

    private synchronized void write(String row) {
        try {
            csv.write(row);
            csv.flush();
        } catch (IOException ignored) {
        }
    }
}
