package athena.bench;

import com.destroystokyo.paper.event.server.ServerTickStartEvent;
import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.plugin.java.JavaPlugin;

/** Records System.nanoTime() and currentTimeMillis() at the start of every tick; writes them on disable. */
public final class TickProbe extends JavaPlugin implements Listener {
    private final long[] nanos = new long[20 * 60 * 30];
    private final long[] millis = new long[nanos.length];
    private int count;

    @Override
    public void onEnable() {
        getServer().getPluginManager().registerEvents(this, this);
    }

    @EventHandler
    public void onTick(ServerTickStartEvent event) {
        if (count < nanos.length) {
            nanos[count] = System.nanoTime();
            millis[count] = System.currentTimeMillis();
            ++count;
        }
    }

    @Override
    public void onDisable() {
        File out = new File(System.getProperty("tickprobe.out", "tickprobe.csv"));
        try (PrintWriter writer = new PrintWriter(out, "UTF-8")) {
            writer.println("tick,nanos,millis");
            for (int i = 0; i < count; ++i) {
                writer.println(i + "," + nanos[i] + "," + millis[i]);
            }
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }
}
