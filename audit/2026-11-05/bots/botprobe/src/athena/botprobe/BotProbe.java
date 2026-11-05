package athena.botprobe;

import athena.api.Athena;
import athena.api.bot.Bot;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import org.bukkit.Bukkit;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.World;
import org.bukkit.WorldCreator;
import org.bukkit.WorldType;
import org.bukkit.block.Block;
import org.bukkit.block.BlockFace;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityDamageByEntityEvent;
import org.bukkit.event.entity.EntityShootBowEvent;
import org.bukkit.event.entity.PlayerDeathEvent;
import org.bukkit.event.inventory.InventoryOpenEvent;
import org.bukkit.event.player.PlayerItemConsumeEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerLoginEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.event.player.PlayerRespawnEvent;
import org.spigotmc.event.player.PlayerSpawnLocationEvent;
import org.bukkit.inventory.ItemStack;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Athena - checks athena.api.bot on a bare flat server: each check logs "BOTPROBE PASS name: ..." or
 * "BOTPROBE FAIL name: ...", one after another, then "BOTPROBE DONE pass=N fail=M". A bot named
 * Bot_Show is left walking round the spawn for an observing client (botprobe.py), and the server keeps
 * running until it is stopped.
 */
public final class BotProbe extends JavaPlugin implements Listener {

    private int pass, fail;
    private final List<String> events = new CopyOnWriteArrayList<>();
    private World wt;
    private final ConcurrentHashMap<String, Location> spawnAt = new ConcurrentHashMap<>();

    private void result(String name, boolean ok, String detail) {
        if (ok) pass++; else fail++;
        getLogger().info("BOTPROBE " + (ok ? "PASS " : "FAIL ") + name + ": " + detail);
    }

    private void later(long ticks, Runnable task) {
        Bukkit.getScheduler().runTaskLater(this, task, ticks);
    }

    private static UUID idOf(String name) {
        return UUID.nameUUIDFromBytes(("AthenaBot:" + name).getBytes(StandardCharsets.UTF_8));
    }

    private World main() {
        return Bukkit.getWorlds().get(0);
    }

    private Location at(double x, double z) {
        return new Location(main(), x + 0.5, 4, z + 0.5, -90f, 0f); // yaw -90: facing +X
    }

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(this, this);
        later(40L, this::run);
    }

    // ---- events the checks look for
    @EventHandler public void login(PlayerLoginEvent e) { events.add("login " + e.getPlayer().getName() + " " + e.getHostname()); }
    @EventHandler public void join(PlayerJoinEvent e) { events.add("join " + e.getPlayer().getName()); }
    @EventHandler public void quit(PlayerQuitEvent e) { events.add("quit " + e.getPlayer().getName()); }
    @EventHandler public void death(PlayerDeathEvent e) { events.add("death " + e.getEntity().getName()); }
    @EventHandler public void respawn(PlayerRespawnEvent e) { events.add("respawn " + e.getPlayer().getName()); }
    @EventHandler public void consume(PlayerItemConsumeEvent e) { events.add("consume " + e.getPlayer().getName() + " " + e.getItem().getType()); }
    @EventHandler public void shoot(EntityShootBowEvent e) { events.add("shoot " + e.getEntity().getName() + " " + e.getForce()); }
    @EventHandler public void open(InventoryOpenEvent e) { events.add("open " + e.getPlayer().getName() + " " + e.getInventory().getType()); }
    @EventHandler public void hit(EntityDamageByEntityEvent e) {
        events.add("hit " + e.getDamager().getName() + " " + e.getEntity().getName() + " " + e.getFinalDamage());
    }
    private final java.util.Set<String> frozen = ConcurrentHashMap.newKeySet();
    @EventHandler public void move(org.bukkit.event.player.PlayerMoveEvent e) {
        String name = e.getPlayer().getName();
        if (!events.contains("move " + name)) events.add("move " + name);
        if (frozen.contains(name) && (e.getFrom().getX() != e.getTo().getX() || e.getFrom().getZ() != e.getTo().getZ())) e.setCancelled(true);
    }
    @EventHandler public void spawnLocation(PlayerSpawnLocationEvent e) {
        Location to = spawnAt.remove(e.getPlayer().getName());
        if (to != null) e.setSpawnLocation(to);
    }

    private boolean saw(String event) {
        return events.contains(event);
    }

    private boolean sawPrefix(String prefix) {
        for (String e : events) if (e.startsWith(prefix)) return true;
        return false;
    }

    // ---- the checks, in order
    private final List<Consumer<Runnable>> checks = new ArrayList<>();

    private void run() {
        checks.add(this::login);
        checks.add(this::walk);
        checks.add(this::jump);
        checks.add(this::fall);
        checks.add(this::fight);
        checks.add(this::eat);
        checks.add(this::bow);
        checks.add(this::chest);
        checks.add(this::dieAndRespawn);
        checks.add(this::controllerThrows);
        checks.add(this::moveEvent);
        checks.add(this::worldThread);
        checks.add(this::spawnIntoThreadedWorld);
        checks.add(this::removeAndKick);
        checks.add(this::show);
        next(0);
    }

    private void next(int i) {
        if (i >= checks.size()) {
            getLogger().info("BOTPROBE DONE pass=" + pass + " fail=" + fail);
            return;
        }
        try {
            checks.get(i).accept(() -> later(5L, () -> next(i + 1)));
        } catch (Throwable t) {
            result("check " + i, false, "threw " + t);
            later(5L, () -> next(i + 1));
        }
    }

    private CompletableFuture<Bot> spawn(String name) {
        return Athena.spawnBot(name, idOf(name), null, null);
    }

    /** Spawns {@code name} and hands it to {@code then} on the server thread, teleported to {@code where}. */
    private void spawnAt(String name, Location where, Consumer<Bot> then) {
        spawnAt(name, where, 5L, then);
    }

    /** A player can't be hurt for 60 ticks after it joins or respawns (EntityPlayer.invulnerableTicks): wait past that. */
    private static final long PAST_SPAWN_PROTECTION = 65L;

    private void spawnAt(String name, Location where, long wait, Consumer<Bot> then) {
        spawn(name).whenComplete((bot, error) -> Bukkit.getScheduler().runTask(this, () -> {
            if (error != null) {
                result("spawn " + name, false, "failed: " + error);
                return;
            }
            bot.getPlayer().teleport(where);
            later(wait, () -> then.accept(bot));
        }));
    }

    private void login(Runnable done) {
        boolean[] mainThread = new boolean[1];
        spawn("Bot_Login").whenComplete((bot, error) -> {
            mainThread[0] = Bukkit.isPrimaryThread();
            Bukkit.getScheduler().runTask(this, () -> {
                if (error != null) {
                    result("login", false, "failed: " + error);
                    done.run();
                    return;
                }
                Player p = Bukkit.getPlayerExact("Bot_Login");
                result("login", p != null && p == bot.getPlayer() && Athena.isBot(p) && Athena.getBot(p) == bot && bot.isOnline()
                                && Athena.getBots().contains(bot) && mainThread[0],
                        "online=" + (p != null) + " isBot=" + (p != null && Athena.isBot(p)) + " completedOnServerThread=" + mainThread[0]
                                + " uuid=" + bot.getPlayer().getUniqueId().equals(idOf("Bot_Login")));
                result("login events", saw("login Bot_Login bot") && saw("join Bot_Login"), events.toString());
                // the same name again is refused
                spawn("Bot_Login").whenComplete((again, error2) -> Bukkit.getScheduler().runTask(this, () -> {
                    result("duplicate name refused", error2 != null, String.valueOf(error2));
                    spawn("not a name!").whenComplete((bad, error3) -> Bukkit.getScheduler().runTask(this, () -> {
                        result("bad name refused", error3 != null, String.valueOf(error3));
                        bot.remove();
                        done.run();
                    }));
                }));
            });
        });
    }

    private void walk(Runnable done) {
        spawnAt("Bot_Walk", at(100, 0), bot -> {
            Location start = bot.getPlayer().getLocation();
            bot.setMovement(1f, 0f);
            later(40L, () -> {
                double walked = bot.getPlayer().getLocation().distance(start);
                Location mid = bot.getPlayer().getLocation();
                boolean sprintOk = bot.setSprinting(true);
                later(40L, () -> {
                    double sprinted = bot.getPlayer().getLocation().distance(mid);
                    bot.setMovement(0f, 0f);
                    // 4.317 m/s walking, 5.612 sprinting: 8.6 and 11.2 blocks in 40 ticks, less the start-up
                    result("walk", walked > 7.0 && walked < 9.5, String.format("%.2f blocks in 40 ticks", walked));
                    result("sprint", sprintOk && sprinted > 10.0 && sprinted < 12.0 && sprinted > walked * 1.2,
                            String.format("%.2f blocks in 40 ticks, sprinting=%s", sprinted, bot.getPlayer().isSprinting()));
                    double dz = Math.abs(bot.getPlayer().getLocation().getZ() - start.getZ());
                    result("walk straight", dz < 0.05, String.format("drifted %.3f sideways", dz));
                    later(10L, () -> {
                        Location stop = bot.getPlayer().getLocation();
                        later(10L, () -> {
                            double slid = bot.getPlayer().getLocation().distance(stop);
                            result("stop", slid < 0.05, String.format("moved %.3f after letting go", slid));
                            // stops sprinting when it stops going forward
                            result("sprint stops", !bot.getPlayer().isSprinting(), "sprinting=" + bot.getPlayer().isSprinting());
                            bot.remove();
                            done.run();
                        });
                    });
                });
            });
        });
    }

    private void jump(Runnable done) {
        spawnAt("Bot_Jump", at(120, 0), bot -> {
            double ground = bot.getPlayer().getLocation().getY();
            double[] top = {ground};
            boolean[] onGround = {bot.getPlayer().isOnGround()};
            bot.setJumping(true);
            int task = Bukkit.getScheduler().runTaskTimer(this, () -> top[0] = Math.max(top[0], bot.getPlayer().getLocation().getY()), 1L, 1L).getTaskId();
            later(12L, () -> {
                bot.setJumping(false);
                later(20L, () -> {
                    Bukkit.getScheduler().cancelTask(task);
                    double rise = top[0] - ground;
                    double back = bot.getPlayer().getLocation().getY() - ground;
                    result("jump", onGround[0] && rise > 1.1 && rise < 1.3 && Math.abs(back) < 0.01, String.format("rose %.3f, back at %.3f", rise, back));
                    bot.remove();
                    done.run();
                });
            });
        });
    }

    private void fall(Runnable done) {
        spawnAt("Bot_Fall", at(140, 0), bot -> {
            bot.getPlayer().teleport(at(140, 0).add(0, 10, 0));
            double before = bot.getPlayer().getHealth();
            later(40L, () -> {
                double after = bot.getPlayer().getHealth();
                double y = bot.getPlayer().getLocation().getY();
                // 10 blocks: 7 damage
                result("fall damage", Math.abs(before - after - 7.0) < 0.6 && Math.abs(y - 4.0) < 0.01, String.format("health %.1f -> %.1f, landed at y %.2f", before, after, y));
                bot.remove();
                done.run();
            });
        });
    }

    private void fight(Runnable done) {
        spawnAt("Bot_A", at(160, 0), a -> spawnAt("Bot_B", at(162, 0), PAST_SPAWN_PROTECTION, b -> {
            a.lookAt(b.getPlayer().getEyeLocation());
            float yaw = a.getPlayer().getLocation().getYaw();
            double hb = b.getPlayer().getHealth();
            Location bAt = b.getPlayer().getLocation();
            boolean hit = a.attack(b.getPlayer());
            later(10L, () -> {
                double pushed = b.getPlayer().getLocation().getX() - bAt.getX();
                result("attack", hit && b.getPlayer().getHealth() < hb && sawPrefix("hit Bot_A Bot_B"),
                        String.format("hit=%s health %.1f -> %.1f, yaw %.1f", hit, hb, b.getPlayer().getHealth(), yaw));
                result("knockback", pushed > 0.3, String.format("pushed %.2f away", pushed));
                b.getPlayer().teleport(at(166, 0));
                later(3L, () -> {
                    result("out of reach", !a.attack(b.getPlayer()), "reach to a bot 4 blocks away refused");
                    // a wall between them: 2 blocks away, but no hit
                    b.getPlayer().teleport(at(162, 0));
                    for (int y = 4; y <= 6; y++) for (int z = -2; z <= 2; z++) main().getBlockAt(161, y, z).setType(Material.STONE);
                    later(2L, () -> {
                        result("through a wall", !a.attack(b.getPlayer()), "a hit through a wall refused");
                        for (int y = 4; y <= 6; y++) for (int z = -2; z <= 2; z++) main().getBlockAt(161, y, z).setType(Material.AIR);
                    });
                    // a sword does more
                    b.getPlayer().teleport(at(162, 0));
                    a.getPlayer().getInventory().setItem(0, new ItemStack(Material.DIAMOND_SWORD));
                    a.selectSlot(0);
                    later(20L, () -> { // past B's no-damage ticks
                        double h2 = b.getPlayer().getHealth();
                        a.attack(b.getPlayer());
                        later(2L, () -> {
                            result("sword", h2 - b.getPlayer().getHealth() > 6.5, String.format("health %.1f -> %.1f", h2, b.getPlayer().getHealth()));
                            a.remove();
                            b.remove();
                            done.run();
                        });
                    });
                });
            });
        }));
    }

    private void eat(Runnable done) {
        spawnAt("Bot_Eat", at(180, 0), bot -> {
            Player p = bot.getPlayer();
            p.setFoodLevel(10);
            p.getInventory().setItem(3, new ItemStack(Material.COOKED_BEEF, 2));
            boolean slot = bot.selectSlot(3);
            boolean used = bot.useItem();
            boolean using = bot.isUsingItem();
            later(40L, () -> {
                result("eat", slot && used && using && p.getFoodLevel() == 18 && saw("consume Bot_Eat COOKED_BEEF")
                                && p.getInventory().getItem(3).getAmount() == 1,
                        "used=" + used + " using=" + using + " food " + p.getFoodLevel() + " left " + p.getInventory().getItem(3));
                bot.remove();
                done.run();
            });
        });
    }

    private void bow(Runnable done) {
        spawnAt("Bot_Bow", at(200, 0), bot -> {
            Player p = bot.getPlayer();
            p.getInventory().setItem(0, new ItemStack(Material.BOW));
            p.getInventory().setItem(9, new ItemStack(Material.ARROW, 5));
            bot.selectSlot(0);
            bot.look(-90f, -10f);
            boolean used = bot.useItem();
            later(20L, () -> {
                bot.releaseItem();
                later(2L, () -> {
                    result("bow", used && sawPrefix("shoot Bot_Bow 1.0") && p.getInventory().getItem(9).getAmount() == 4,
                            "used=" + used + " arrows " + p.getInventory().getItem(9) + " " + events.stream().filter(e -> e.startsWith("shoot")).toList());
                    bot.remove();
                    done.run();
                });
            });
        });
    }

    private void chest(Runnable done) {
        spawnAt("Bot_Chest", at(220, 0), bot -> {
            Block block = main().getBlockAt(222, 4, 0);
            block.setType(Material.CHEST);
            boolean clicked = bot.interact(block, BlockFace.WEST);
            later(2L, () -> {
                result("chest", clicked && saw("open Bot_Chest CHEST")
                                && bot.getPlayer().getOpenInventory().getTopInventory().getType() == org.bukkit.event.inventory.InventoryType.CHEST,
                        "clicked=" + clicked + " open " + bot.getPlayer().getOpenInventory().getTopInventory().getType());
                bot.getPlayer().closeInventory();
                result("too far", !bot.interact(main().getBlockAt(240, 4, 0), BlockFace.UP), "a block 18 away refused");
                // a wall between the bot and the chest
                for (int y = 4; y <= 6; y++) for (int z = -2; z <= 2; z++) main().getBlockAt(221, y, z).setType(Material.STONE);
                result("chest through a wall", !bot.canClick(block) && !bot.interact(block, BlockFace.WEST), "a chest behind a wall refused");
                for (int y = 4; y <= 6; y++) for (int z = -2; z <= 2; z++) main().getBlockAt(221, y, z).setType(Material.AIR);
                block.setType(Material.AIR);
                bot.remove();
                done.run();
            });
        });
    }

    private void dieAndRespawn(Runnable done) {
        spawnAt("Bot_Die", at(240, 0), PAST_SPAWN_PROTECTION, bot -> {
            bot.getPlayer().damage(100.0);
            later(30L, () -> {
                boolean died = saw("death Bot_Die") && bot.getPlayer().isDead();
                boolean early = bot.respawn();
                later(5L, () -> {
                    result("die and respawn", died && early && saw("respawn Bot_Die") && bot.getPlayer().getHealth() == 20.0 && bot.isOnline()
                                    && !bot.getPlayer().isDead(),
                            "died=" + died + " respawned=" + early + " health " + bot.getPlayer().getHealth());
                    result("respawn when alive", !bot.respawn(), "refused");
                    // it moves again after the respawn
                    Location from = bot.getPlayer().getLocation();
                    bot.setMovement(1f, 0f);
                    later(20L, () -> {
                        result("moves after respawn", bot.getPlayer().getLocation().distance(from) > 3.0,
                                String.format("%.2f", bot.getPlayer().getLocation().distance(from)));
                        bot.remove();
                        done.run();
                    });
                });
            });
        });
    }

    private void controllerThrows(Runnable done) {
        spawnAt("Bot_Throw", at(260, 0), bot -> {
            int[] ticks = {0};
            bot.setMovement(1f, 0f);
            bot.setController(b -> {
                if (++ticks[0] == 3) throw new IllegalStateException("a controller's mistake (expected)");
            });
            later(10L, () -> {
                result("controller throws", ticks[0] >= 9 && bot.isOnline() && bot.getForward() == 0f,
                        "ticks=" + ticks[0] + " forward " + bot.getForward() + " (stopped by the throw)");
                bot.remove();
                done.run();
            });
        });
    }

    private void moveEvent(Runnable done) {
        spawnAt("Bot_Frozen", at(300, 0), bot -> {
            Location start = bot.getPlayer().getLocation();
            frozen.add("Bot_Frozen");
            bot.setMovement(1f, 0f);
            later(30L, () -> {
                double held = bot.getPlayer().getLocation().distance(start);
                frozen.remove("Bot_Frozen");
                later(20L, () -> {
                    double moved = bot.getPlayer().getLocation().distance(start);
                    result("PlayerMoveEvent", saw("move Bot_Frozen") && held < 0.1 && moved > 2.0,
                            String.format("held at %.3f while cancelled, then %.2f", held, moved));
                    bot.remove();
                    done.run();
                });
            });
        });
    }

    private void worldThread(Runnable done) {
        WorldCreator creator = new WorldCreator("botprobe_wt").type(WorldType.FLAT);
        wt = Bukkit.createWorld(creator);
        Athena.startWorldThread(wt);
        spawnAt("Bot_Thread", at(0, 20), bot -> {
            Athena.teleportAsync(bot.getPlayer(), new Location(wt, 10.5, 4, 10.5, -90f, 0f)).thenAccept(arrived -> {
                boolean onThread = Athena.isWorldThread(wt);
                Location start = bot.getPlayer().getLocation();
                boolean[] allOnThread = {true};
                int[] ticks = {0};
                bot.setController(b -> {
                    ticks[0]++;
                    if (!Athena.isWorldThread(wt)) allOnThread[0] = false;
                    b.setMovement(1f, 0f);
                });
                Athena.runOnWorldTimer(wt, () -> {
                    double moved = bot.getPlayer().getLocation().distance(start);
                    Bukkit.getScheduler().runTask(this, () -> {
                        result("world thread", arrived && onThread && allOnThread[0] && ticks[0] >= 30 && moved > 5.0,
                                "arrived=" + arrived + " completedOnWorldThread=" + onThread + " controllerOnWorldThread=" + allOnThread[0]
                                        + " ticks=" + ticks[0] + String.format(" moved %.2f", moved));
                    });
                    Athena.runOnWorld(wt, () -> {
                        bot.remove();
                        Bukkit.getScheduler().runTask(this, done);
                    });
                }, 40L, 0L);
            });
        });
    }

    private void spawnIntoThreadedWorld(Runnable done) {
        spawnAt.put("Bot_Direct", new Location(wt, -10.5, 4, -10.5));
        spawn("Bot_Direct").whenComplete((bot, error) -> {
            boolean onThread = Athena.isWorldThread(wt);
            boolean inWorld = bot != null && bot.getPlayer().getWorld() == wt;
            Bukkit.getScheduler().runTask(this, () -> {
                result("spawn into a threaded world", error == null && onThread && inWorld,
                        "error=" + error + " completedOnWorldThread=" + onThread + " inWorld=" + inWorld);
                if (bot == null) {
                    done.run();
                    return;
                }
                Athena.runOnWorld(wt, () -> {
                    bot.remove();
                    Bukkit.getScheduler().runTask(this, done);
                });
            });
        });
    }

    private void removeAndKick(Runnable done) {
        spawnAt("Bot_Remove", at(280, 0), bot -> spawnAt("Bot_Kick", at(282, 0), kicked -> {
            bot.remove();
            kicked.getPlayer().kickPlayer("bye");
            later(2L, () -> {
                result("remove", saw("quit Bot_Remove") && !bot.isOnline() && Bukkit.getPlayerExact("Bot_Remove") == null && !Athena.getBots().contains(bot),
                        "online=" + bot.isOnline());
                result("kick", saw("quit Bot_Kick") && !kicked.isOnline() && Bukkit.getPlayerExact("Bot_Kick") == null,
                        "online=" + kicked.isOnline());
                result("no bots left", Athena.getBots().isEmpty(), "bots " + Athena.getBots());
                done.run();
            });
        }));
    }

    /** Leaves three bots for botprobe.py: Bot_Show walking round the spawn, and two more to fill the server past max-players. */
    private void show(Runnable done) {
        Location home = main().getSpawnLocation();
        getLogger().info("BOTPROBE spawn " + home);
        spawnAt("Bot_Show", home.clone().add(3, 0, 0), bot -> {
            int[] t = {0};
            bot.setController(b -> {
                t[0]++;
                b.look((t[0] * 3) % 360, 0f);
                b.setMovement(1f, 0f);
                if (t[0] % 40 == 0) b.swing();
            });
            spawnAt("Bot_Fill1", home.clone().add(-3, 0, 0), f1 -> spawnAt("Bot_Fill2", home.clone().add(-3, 0, 3), f2 -> {
                getLogger().info("BOTPROBE players online " + Bukkit.getOnlinePlayers().size() + ", max " + Bukkit.getMaxPlayers());
                done.run();
            }));
        });
    }
}
