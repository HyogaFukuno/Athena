package athena.sgwatch;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.PrintWriter;
import java.lang.reflect.Method;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

import athena.api.Athena;
import org.bukkit.Bukkit;
import org.bukkit.World;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.plugin.Plugin;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Test aid for SurvivalGames' rooms (feature/rooms) on Athena's world threads: the bench's MatchSim
 * wrote ticks.csv itself; with the real SurvivalGames this plugin does it from outside.
 *
 * Every tick of the lobby (the server thread) and of each room's match world (on the thread that ticks
 * it, Athena.runOnWorldTimer) is timed. Once a second a thread of its own writes one row per world:
 *
 *   unix_ms,world,state,ticks,max_gap_ms,gaps_over_60,gaps_over_100
 *
 * with the room's state as MatchSim named them (LOBBY, PREGAME, LIVE, PREDEATHMATCH, DEATHMATCH, END,
 * CLEANUP), read from SurvivalGames by reflection. "sgwatch freeze <room|lobby> <ms>" blocks the thread
 * that ticks a room's match world (or the server thread) for a while.
 */
public final class SgWatch extends JavaPlugin {

    /** One world's ticks since the last row. */
    private static final class Ticks {
        private long last;
        private int ticks;
        private long maxGap;
        private int over60;
        private int over100;
        volatile String state = "?";
        volatile boolean gone;

        synchronized void tick(long now) {
            if (last != 0L) {
                long gap = (now - last) / 1_000_000L;
                if (gap > maxGap) maxGap = gap;
                if (gap > 60) over60++;
                if (gap > 100) over100++;
            }
            last = now;
            ticks++;
        }

        /** The row since the last one, and a fresh count. */
        synchronized String row(long unixMs, String world) {
            String row = unixMs + "," + world + "," + state + "," + ticks + "," + maxGap + "," + over60 + "," + over100;
            ticks = 0;
            maxGap = 0;
            over60 = 0;
            over100 = 0;
            return row;
        }
    }

    private final Map<String, Ticks> worlds = new ConcurrentHashMap<String, Ticks>();
    private volatile boolean running = true;
    private Plugin sg;

    @Override
    public void onEnable() {
        getDataFolder().mkdirs();
        sg = Bukkit.getPluginManager().getPlugin("SurvivalGames");
        Ticks lobby = new Ticks();
        lobby.state = "waiting";
        worlds.put("lobby", lobby);
        Bukkit.getScheduler().runTaskTimer(this, () -> lobby.tick(System.nanoTime()), 1L, 1L);
        // once a second on the server thread: each room's state, and a timer for each new match world
        Bukkit.getScheduler().runTaskTimer(this, this::followRooms, 20L, 20L);
        Thread writer = new Thread(this::write, "sgwatch-writer");
        writer.setDaemon(true);
        writer.start();
        getLogger().info("ready");
    }

    @Override
    public void onDisable() {
        running = false;
    }

    private void followRooms() {
        try {
            for (Object room : (Iterable<?>) call(sg, "rooms")) {
                int id = (Integer) call(room, "id");
                Object state = call(call(call(room, "game"), "state"), "state");
                Optional<?> arena = (Optional<?>) call(call(room, "arenas"), "current");
                if (!arena.isPresent()) continue;
                World world = (World) call(arena.get(), "world");
                Ticks ticks = worlds.get(world.getName());
                if (ticks == null) {
                    Ticks fresh = new Ticks();
                    ticks = worlds.putIfAbsent(world.getName(), fresh);
                    if (ticks == null) {
                        ticks = fresh;
                        final Ticks timed = fresh;
                        // the timer runs on the thread that ticks the world, whichever it is, and stops with the world
                        Athena.runOnWorldTimer(world, () -> timed.tick(System.nanoTime()), 1L, 1L);
                        getLogger().info("sgwatch watching " + world.getName() + " (room " + id + ")");
                    }
                }
                ticks.state = name(((Enum<?>) state).name());
            }
            for (Map.Entry<String, Ticks> entry : worlds.entrySet()) {
                if (!entry.getKey().equals("lobby") && Bukkit.getWorld(entry.getKey()) == null) entry.getValue().gone = true;
            }
        } catch (ReflectiveOperationException e) {
            getLogger().warning("sgwatch cannot read the rooms: " + e);
        }
    }

    private static String name(String state) {
        switch (state) {
            case "PRE_GAME": return "PREGAME";
            case "LIVE_GAME": return "LIVE";
            case "PRE_DEATHMATCH": return "PREDEATHMATCH";
            case "END_GAME": return "END";
            case "CLEAN_UP": return "CLEANUP";
            default: return state;
        }
    }

    /** A public method of the object's class or of an interface it implements, made accessible (SurvivalGames' classes). */
    private static Object call(Object target, String method) throws ReflectiveOperationException {
        for (Class<?> type = target.getClass(); type != null; type = type.getSuperclass()) {
            for (Class<?> face : type.getInterfaces()) {
                try {
                    return face.getMethod(method).invoke(target);
                } catch (NoSuchMethodException ignored) {
                }
            }
        }
        Method m = target.getClass().getMethod(method);
        m.setAccessible(true);
        return m.invoke(target);
    }

    private void write() {
        File file = new File(getDataFolder(), "ticks.csv");
        try (PrintWriter out = new PrintWriter(new FileWriter(file))) {
            out.println("unix_ms,world,state,ticks,max_gap_ms,gaps_over_60,gaps_over_100");
            while (running) {
                Thread.sleep(1000L);
                long now = System.currentTimeMillis();
                for (Map.Entry<String, Ticks> entry : worlds.entrySet()) {
                    out.println(entry.getValue().row(now, entry.getKey()));
                    if (entry.getValue().gone) worlds.remove(entry.getKey());
                }
                out.flush();
            }
        } catch (IOException | InterruptedException e) {
            getLogger().warning("sgwatch writer stopped: " + e);
        }
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 3 && args[0].equals("freeze")) {
            final long ms = Long.parseLong(args[2]);
            final String what = args[1];
            Runnable block = () -> {
                getLogger().info("sgwatch freezing " + what + " on " + Thread.currentThread().getName() + " for " + ms + " ms at " + System.currentTimeMillis());
                try {
                    Thread.sleep(ms);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                getLogger().info("sgwatch froze " + what + " until " + System.currentTimeMillis());
            };
            if (what.equals("lobby")) {
                Bukkit.getScheduler().runTask(this, block);
                return true;
            }
            try {
                for (Object room : (Iterable<?>) call(sg, "rooms")) {
                    if ((Integer) call(room, "id") != Integer.parseInt(what)) continue;
                    Optional<?> arena = (Optional<?>) call(call(room, "arenas"), "current");
                    if (!arena.isPresent()) {
                        sender.sendMessage("sgwatch: room " + what + " has no match world now");
                        return true;
                    }
                    Athena.runOnWorld((World) call(arena.get(), "world"), block);
                    return true;
                }
                sender.sendMessage("sgwatch: no room " + what);
            } catch (ReflectiveOperationException e) {
                sender.sendMessage("sgwatch: " + e);
            }
            return true;
        }
        sender.sendMessage("sgwatch freeze <room|lobby> <ms>");
        return true;
    }
}
