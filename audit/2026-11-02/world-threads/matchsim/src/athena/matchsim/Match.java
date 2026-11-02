package athena.matchsim;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;
import java.util.UUID;

import athena.api.Athena;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.block.Chest;
import org.bukkit.entity.Player;
import org.bukkit.event.entity.EntityDamageEvent;
import org.bukkit.event.player.PlayerInteractEvent;
import org.bukkit.inventory.Inventory;
import org.bukkit.inventory.ItemStack;

/**
 * One SG-like match in its own copy of the map. Built on the server thread; from {@link #startClock}
 * on, everything here runs on the thread that ticks the match's world (its clock, and the listeners
 * MatchSim hands it), so it needs no locks.
 */
final class Match {
    enum State { PREGAME, LIVE, PREDEATHMATCH, DEATHMATCH, END, CLEANUP, DONE }

    private static final Material[] LOOT = {
            Material.WOOD_SWORD, Material.STONE_SWORD, Material.IRON_SWORD, Material.WOOD_AXE, Material.STONE_AXE,
            Material.BOW, Material.ARROW, Material.FISHING_ROD, Material.FLINT_AND_STEEL,
            Material.LEATHER_HELMET, Material.LEATHER_CHESTPLATE, Material.LEATHER_LEGGINGS, Material.LEATHER_BOOTS,
            Material.GOLD_HELMET, Material.GOLD_CHESTPLATE, Material.CHAINMAIL_LEGGINGS, Material.IRON_BOOTS,
            Material.APPLE, Material.BREAD, Material.COOKED_BEEF, Material.PORK, Material.CARROT_ITEM, Material.BAKED_POTATO,
            Material.MELON, Material.PUMPKIN_PIE, Material.CAKE, Material.GOLD_INGOT, Material.IRON_INGOT, Material.STICK,
            Material.FEATHER, Material.FLINT, Material.WHEAT, Material.EXP_BOTTLE, Material.TNT, Material.BOAT};

    final MatchSim sim;
    final int id;
    final World world;
    private final List<UUID> players = new ArrayList<>();
    private final Set<UUID> alive = new HashSet<>();
    private final Set<UUID> spectators = new HashSet<>();
    private final Set<Long> filledChests = new HashSet<>();
    private final MatchSim.TickGaps gaps = new MatchSim.TickGaps();
    private final Random random;
    private volatile State state = State.PREGAME;
    private int left;
    private long ticks;
    private volatile int aliveCount, spectatorCount;
    private int eliminations;

    Match(MatchSim sim, int id, World world, List<Player> starting) {
        this.sim = sim;
        this.id = id;
        this.world = world;
        this.random = new Random(id);
        for (Player p : starting) {
            players.add(p.getUniqueId());
            alive.add(p.getUniqueId());
        }
        aliveCount = alive.size();
        left = sim.pregameSeconds;
    }

    /** Runs {@code task} on the thread that ticks this match's world. */
    void runOnThread(Runnable task) {
        if (sim.hasWorldThreads) Athena.runOnWorld(world, task);
        else Bukkit.getScheduler().runTask(sim, task);
    }

    void startClock() {
        if (sim.hasWorldTimers) Athena.runOnWorldTimer(world, this::tick, 1L, 1L);
        else Bukkit.getScheduler().runTaskTimer(sim, this::tick, 1L, 1L);
    }

    private void tick() {
        if (state == State.DONE) return;
        gaps.record();
        if (++ticks % 20 != 0) return;
        second();
        List<Player> here = world.getPlayers();
        sim.row(world.getName(), state.name(), gaps, here.size(), aliveCount, spectatorCount, -1);
        String time = String.format("%d:%02d", Math.max(left, 0) / 60, Math.max(left, 0) % 60);
        for (Player p : here) {
            MatchSim.Board board = sim.boards.get(p.getUniqueId());
            if (board == null) continue;
            board.show("&6MCSG &7" + state.name().toLowerCase(), new String[]{
                    "&fTime: &a" + time, "&fPlayers: &a" + aliveCount, "&fSpectators: &a" + spectatorCount,
                    "", "&fMap: &a" + sim.mapName, "&fKills: &a" + eliminations, "&fMatch: &a#" + id, "&fAS: &a1"});
        }
    }

    private void second() {
        switch (state) {
            case PREGAME:
                if (left > 0) {
                    if (left % 5 == 0 || left <= 3) say("&8[&e" + left + "&8] &cseconds until the games begin!");
                    left--;
                    return;
                }
                state = State.LIVE;
                left = sim.liveSeconds;
                say("&3The games have begun!");
                return;
            case LIVE:
                if (aliveCount <= sim.deathmatchAt || left <= 0) {
                    state = State.PREDEATHMATCH;
                    left = sim.predeathmatchSeconds;
                    say("&4Please allow &8[&e10&8]&4 seconds for all players to load the map.!");
                    toDeathmatch();
                    return;
                }
                if (left % 60 == 0) say("&8[&e" + left / 60 + "&8] &cminutes until deathmatch!");
                left--;
                return;
            case PREDEATHMATCH:
                if (aliveCount <= 1) {
                    end();
                    return;
                }
                if (left > 0) {
                    if (left % 10 == 0 || left <= 3) say("&8[&e" + left + "&8] &cseconds until deathmatch!");
                    left--;
                    return;
                }
                state = State.DEATHMATCH;
                left = sim.deathmatchSeconds;
                say("&cFight to the death!");
                return;
            case DEATHMATCH:
                if (aliveCount <= 1 || left <= 0) {
                    end();
                    return;
                }
                left--;
                return;
            case END:
                if (left-- > 0) return;
                state = State.CLEANUP;
                left = sim.cleanupSeconds;
                say("&3The server is cleaning up. You will be returned to the lobby in a moment.");
                return;
            case CLEANUP:
                if (left-- > 0) return;
                state = State.DONE;
                sim.finish(this, new ArrayList<>(world.getPlayers()));
                return;
            default:
        }
    }

    private void end() {
        state = State.END;
        left = sim.endSeconds;
        say("&aThe games have ended!");
        for (UUID winner : alive) {
            Player p = Bukkit.getPlayer(winner);
            if (p != null) say("&a" + p.getName() + " &ahas won the Survival Games!");
        }
    }

    private void toDeathmatch() {
        int i = 0;
        List<double[]> spawns = sim.deathmatchSpawns.isEmpty() ? sim.spawns : sim.deathmatchSpawns;
        for (Player p : world.getPlayers()) {
            Location to = alive.contains(p.getUniqueId())
                    ? MatchSim.at(world, spawns.get(i++ % spawns.size()))
                    : MatchSim.at(world, spawns.get(0)).add(0, 10, 0);
            Athena.teleportAsync(p, to);
        }
    }

    private void say(String text) {
        String line = MatchSim.color(MatchSim.PREFIX + text);
        for (Player p : world.getPlayers()) p.sendMessage(line);
    }

    boolean fighting() {
        State s = state;
        return s == State.LIVE || s == State.PREDEATHMATCH || s == State.DEATHMATCH;
    }

    boolean isSpectator(Player p) {
        return spectators.contains(p.getUniqueId());
    }

    String describe() {
        return "#" + id + " " + world.getName() + " " + state + " left " + left + "s, alive " + aliveCount + ", spectators "
                + spectatorCount + (sim.hasWorldThreads && Athena.isWorldThreaded(world) ? ", own thread" : "");
    }

    // ---- listeners, on this match's thread ----

    void damage(EntityDamageEvent e, Player victim, Player attacker) {
        if (!fighting() || !alive.contains(victim.getUniqueId())
                || (attacker != null && !alive.contains(attacker.getUniqueId()))) {
            e.setCancelled(true);
            return;
        }
        if (victim.getHealth() - e.getFinalDamage() >= 1.0) return;
        // as SurvivalGames: no death screen; the damage never happens and the player is eliminated
        e.setCancelled(true);
        eliminate(victim, attacker);
    }

    private void eliminate(Player victim, Player killer) {
        Location at = victim.getLocation();
        for (ItemStack item : victim.getInventory().getContents()) {
            if (item != null && item.getType() != Material.AIR) world.dropItemNaturally(at, item);
        }
        for (ItemStack item : victim.getInventory().getArmorContents()) {
            if (item != null && item.getType() != Material.AIR) world.dropItemNaturally(at, item);
        }
        alive.remove(victim.getUniqueId());
        spectators.add(victim.getUniqueId());
        aliveCount = alive.size();
        spectatorCount = spectators.size();
        eliminations++;
        MatchSim.reset(victim);
        spectator(victim);
        world.strikeLightningEffect(at);
        say("&6" + victim.getName() + " &ehas been eliminated" + (killer != null ? " by &6" + killer.getName() : "") + "&e!");
        say("&8[&e" + aliveCount + "&8] &ctributes remain.");
    }

    /** SurvivalGames' PlayerReset.spectator, within this match: hidden from the others, flying, picks up nothing. */
    private void spectator(Player p) {
        for (Player other : world.getPlayers()) {
            if (other != p) other.hidePlayer(p);
        }
        p.setAllowFlight(true);
        p.setFlying(true);
        p.setCanPickupItems(false);
    }

    void quit(Player p) {
        if (alive.remove(p.getUniqueId())) {
            aliveCount = alive.size();
            say("&6" + p.getName() + " &ehas left the game.");
        }
    }

    void interact(PlayerInteractEvent e) {
        Block block = e.getClickedBlock();
        if (isSpectator(e.getPlayer()) || !fighting() && state != State.PREGAME) {
            e.setCancelled(true);
            return;
        }
        Material type = block.getType();
        if (type != Material.CHEST && type != Material.TRAPPED_CHEST) return;
        long key = ((long) block.getX() & 0x3FFFFFFL) << 38 | ((long) block.getZ() & 0x3FFFFFFL) << 12 | (block.getY() & 0xFFFL);
        if (!filledChests.add(key)) return;
        // filled the first time it is opened, as SurvivalGames' chest loot
        Inventory inventory = ((Chest) block.getState()).getBlockInventory();
        inventory.clear();
        int items = 3 + random.nextInt(4);
        for (int i = 0; i < items; i++) {
            Material m = LOOT[random.nextInt(LOOT.length)];
            int amount = m == Material.ARROW ? 4 + random.nextInt(8) : m.getMaxStackSize() > 1 ? 1 + random.nextInt(3) : 1;
            inventory.setItem(random.nextInt(inventory.getSize()), new ItemStack(m, amount));
        }
    }
}
