import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * P06: twenty logins arriving together, each blocking 50 ms in a pre-login handler (a database read),
 * on the pools LoginListener and PacketPlayInChat use (newCachedThreadPool) against one virtual
 * thread per task. Reports the platform threads the burst added, the peak, and the time to finish
 * all twenty; ten bursts, a new pool per process.  Usage: P06Probe cached|virtual
 */
public class P06Probe {
    public static void main(String[] args) throws Exception {
        ThreadMXBean threads = ManagementFactory.getThreadMXBean();
        boolean virtual = args[0].equals("virtual");
        ExecutorService pool = virtual
                ? Executors.newThreadPerTaskExecutor(Thread.ofVirtual().name("User Authenticator #", 1).factory())
                : Executors.newCachedThreadPool(r -> new Thread(r, "User Authenticator"));
        int before = threads.getThreadCount();
        threads.resetPeakThreadCount();
        long worst = 0;
        for (int burst = 0; burst < 10; ++burst) {
            CountDownLatch done = new CountDownLatch(20);
            long start = System.nanoTime();
            for (int i = 0; i < 20; ++i) {
                pool.execute(() -> {
                    try {
                        Thread.sleep(50);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    }
                    done.countDown();
                });
            }
            done.await();
            worst = Math.max(worst, System.nanoTime() - start);
            Thread.sleep(200);
        }
        System.out.printf("RESULT,%s,threads_added_after=%d,peak_added=%d,worst_burst_ms=%.1f%n", args[0],
                threads.getThreadCount() - before, threads.getPeakThreadCount() - before, worst / 1e6);
        pool.shutdownNow();
    }
}
