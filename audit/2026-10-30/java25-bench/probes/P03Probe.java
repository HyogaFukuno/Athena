package org.bukkit.plugin;

import org.bukkit.Location;
import org.bukkit.TestServer;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerMoveEvent;
import org.bukkit.event.player.PlayerToggleSneakEvent;
import org.bukkit.plugin.java.JavaPluginLoader;

/**
 * P03: the cost of SimplePluginManager.callEvent for a PlayerMoveEvent that four plugins listen to,
 * each with one public @EventHandler (the common shape), through the real registration path
 * (registerEvents -> JavaPluginLoader.createRegisteredListeners). Timings are off, as in production.
 * Prints RESULT,<round>,<ns per callEvent>.
 */
public class P03Probe {

    public static final class MoveListener implements Listener {
        long sum;

        @EventHandler
        public void onMove(PlayerMoveEvent event) {
            this.sum += (long) event.getTo().getX();
        }
    }

    public static final class S0 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 1;
        }
    }

    public static final class S1 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 2;
        }
    }

    public static final class S2 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 3;
        }
    }

    public static final class S3 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 4;
        }
    }

    public static final class S4 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 5;
        }
    }

    public static final class S5 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 6;
        }
    }

    public static final class S6 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 7;
        }
    }

    public static final class S7 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 8;
        }
    }

    public static final class S8 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 9;
        }
    }

    public static final class S9 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 10;
        }
    }

    public static final class S10 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 11;
        }
    }

    public static final class S11 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 12;
        }
    }

    public static final class S12 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 13;
        }
    }

    public static final class S13 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 14;
        }
    }

    public static final class S14 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 15;
        }
    }

    public static final class S15 implements Listener {
        long hits;

        @EventHandler
        public void on(PlayerToggleSneakEvent event) {
            this.hits += 16;
        }
    }

    public static void main(String[] args) {
        // "poly": sixteen other listener classes, on another event, called through the same
        // callEvent path while warming up - a server has dozens of listener methods, not one
        boolean poly = args.length > 0 && args[0].equals("poly");
        PluginManager pm = TestServer.getInstance().getPluginManager();
        final JavaPluginLoader loader = new JavaPluginLoader(TestServer.getInstance());
        MoveListener[] listeners = new MoveListener[4];

        for (int i = 0; i < listeners.length; ++i) {
            Plugin plugin = new TestPlugin("P" + i) {
                @Override
                public PluginLoader getPluginLoader() {
                    return loader;
                }

                @Override
                public boolean isEnabled() {
                    return true;
                }
            };
            pm.registerEvents(listeners[i] = new MoveListener(), plugin);
        }

        PlayerMoveEvent event = new PlayerMoveEvent(null, new Location(null, 0, 64, 0), new Location(null, 1, 64, 1));
        PlayerToggleSneakEvent sneak = new PlayerToggleSneakEvent(null, true);
        int calls = 5_000_000;

        if (poly) {
            Plugin other = new TestPlugin("other") {
                @Override
                public PluginLoader getPluginLoader() {
                    return loader;
                }

                @Override
                public boolean isEnabled() {
                    return true;
                }
            };
            Listener[] extra = { new S0(), new S1(), new S2(), new S3(), new S4(), new S5(), new S6(), new S7(),
                    new S8(), new S9(), new S10(), new S11(), new S12(), new S13(), new S14(), new S15() };
            for (Listener l : extra) pm.registerEvents(l, other);
        }
        for (int i = 0; i < 3 * calls; ++i) {
            pm.callEvent(event);
            if (poly) pm.callEvent(sneak);
        }
        for (int round = 1; round <= 5; ++round) {
            long start = System.nanoTime();

            for (int i = 0; i < calls; ++i) {
                pm.callEvent(event);
            }
            long ns = System.nanoTime() - start;
            System.out.printf("RESULT,%s,%d,%.2f%n", poly ? "poly" : "mono", round, (double) ns / calls);
        }
        long sum = 0;
        for (MoveListener l : listeners) sum += l.sum;
        if (sum != 4L * 8 * calls) throw new IllegalStateException("listeners saw " + sum);
    }
}
