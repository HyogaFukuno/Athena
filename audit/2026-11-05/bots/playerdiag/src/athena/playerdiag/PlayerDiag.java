package athena.playerdiag;

import athena.api.Athena;
import java.lang.reflect.Field;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Athena - logs, every 5 seconds, the server's side of each real player (not bots) in a match world:
 * "PLAYERDIAG name world=... at=... transit=... inWorld=... inChunkMap=... queued=... inFlight=...".
 * For a player whose client shows nothing after a teleport into a deathmatch venue. Read only.
 */
public final class PlayerDiag extends JavaPlugin {
    @Override
    public void onEnable() {
        Bukkit.getScheduler().runTaskTimer(this, this::report, 100L, 100L);
    }

    private static Object field(Object o, String name) throws ReflectiveOperationException {
        for (Class<?> c = o.getClass(); c != null; c = c.getSuperclass()) {
            try {
                Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(o);
            } catch (NoSuchFieldException ignored) {
            }
        }
        throw new NoSuchFieldException(name);
    }

    private void report() {
        for (Player p : Bukkit.getOnlinePlayers()) {
            if (Athena.isBot(p) || !p.getWorld().getName().startsWith("sg-")) continue;
            String line;
            try {
                Object h = p.getClass().getMethod("getHandle").invoke(p);
                Object world = field(h, "world");
                Object chunkMap = world.getClass().getMethod("getPlayerChunkMap").invoke(world);
                line = String.format("world=%s bukkitWorld=%s at=%.1f,%.1f,%.1f transit=%s removed=%s inWorldEntities=%s inWorldPlayers=%s inChunkMap=%s queued=%d inFlight=%s viewDistance=%s gamemode=%s flying=%s",
                        world.getClass().getMethod("getWorld").invoke(world) instanceof org.bukkit.World w ? w.getName() : "?",
                        p.getWorld().getName(), p.getLocation().getX(), p.getLocation().getY(), p.getLocation().getZ(),
                        field(h, "athenaInTransit"), field(h, "dead"),
                        ((java.util.List<?>) field(world, "entityList")).contains(h),
                        ((java.util.List<?>) field(world, "players")).contains(h),
                        ((java.util.List<?>) field(chunkMap, "managedPlayers")).contains(h),
                        ((java.util.List<?>) field(h, "chunkCoordIntPairQueue")).size(),
                        field(h, "chunksInFlight"), field(h, "viewDistance"), p.getGameMode(), p.isFlying());
            } catch (ReflectiveOperationException | RuntimeException e) {
                line = "failed: " + e;
            }
            getLogger().info("PLAYERDIAG " + p.getName() + " " + line);
        }
    }
}
