package net.minecraft.server;

import java.io.File;

public final class StoreOpenProbe {
    public static void main(String[] args) throws Exception {
        for (String path : args) {
            File dir = new File(path);
            long t0 = System.nanoTime();
            AthenaSectionStore store = AthenaSectionStore.open(dir);
            long t1 = System.nanoTime();
            System.out.printf("%s open=%.1f ms store=%s%n", dir.getName(), (t1 - t0) / 1e6, store != null);
        }
    }
}
