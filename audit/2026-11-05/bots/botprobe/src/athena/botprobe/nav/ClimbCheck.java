package athena.botprobe.nav;

import athena.api.bot.Bot;
import java.util.SplittableRandom;
import org.bukkit.Location;

/** Walks a bot to {@code goal} with SurvivalGames' Navigator; {@link #tick} is its controller. */
public final class ClimbCheck {
    private final Navigator nav = new Navigator(new SplittableRandom(1));
    public boolean arrived, failed;

    public ClimbCheck(Location goal) {
        nav.goTo(goal, 1.5);
    }

    public void tick(Bot bot) {
        if (arrived || failed) return;
        var result = nav.tick(bot, false);
        if (result == Navigator.Result.ARRIVED) arrived = true;
        if (result == Navigator.Result.FAILED) failed = true;
    }
}
