package net.minecraft.server;

import com.sun.management.ThreadMXBean;
import java.io.File;
import java.lang.management.ManagementFactory;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.zip.CRC32;

/** Standalone probes; run each variant in fresh JVMs with its classes first on the classpath. */
public final class NextStepsProbe {
    private static volatile Object sink;

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        ThreadMXBean bean = (ThreadMXBean) ManagementFactory.getThreadMXBean();
        if (!bean.isThreadAllocatedMemorySupported()) throw new IllegalStateException("allocation accounting unavailable");
        bean.setThreadAllocatedMemoryEnabled(true);
        long thread = Thread.currentThread().threadId();
        bean.getThreadAllocatedBytes(thread);
        System.nanoTime();
        if (args[0].equals("bootstrap")) {
            long allocated = bean.getThreadAllocatedBytes(thread), started = System.nanoTime();
            DispenserRegistry.register();
            long elapsed = System.nanoTime() - started, bytes = bean.getThreadAllocatedBytes(thread) - allocated;
            int states = 0;
            for (IBlockData ignored : Block.BLOCK_STATE_IDS) ++states;
            System.out.printf("RESULT,bootstrap,1,%.3f,%d,%d%n", elapsed / 1e6, bytes, states);
            return;
        }
        if (!args[0].equals("packet")) throw new IllegalArgumentException("bootstrap | packet <template>");
        DispenserRegistry.register();
        WorldServer world = AthenaAuditFixtures.world();
        AthenaAuditFixtures.set(world, World.class, "worldProvider", WorldProvider.byDimension(0));
        File template = new File(args[1]);
        AthenaSectionStore store = AthenaSectionStore.open(template);
        if (store == null) throw new IllegalStateException("an existing valid store is required");
        Field keysField = AthenaSectionStore.class.getDeclaredField("keys");
        keysField.setAccessible(true);
        long[] keys = (long[]) keysField.get(store);
        List<Chunk> chunks = new ArrayList<>();
        int mappedLights = 0, includedMappedLights = 0;
        AthenaTemplateChunkLoader loader = new AthenaTemplateChunkLoader(template);
        try {
            for (long key : keys) {
                int x = (int) (key >> 32), z = (int) key;
                Object[] loaded = loader.loadChunk(world, x, z);
                if (loaded == null || !store.hasSlim(x, z)) throw new IllegalStateException("missing chunk/slim " + x + "," + z);
                Chunk chunk = (Chunk) loaded[0];
                chunks.add(chunk);
                for (ChunkSection section : chunk.getSections()) {
                    if (section == null) continue;
                    int count = (section.getEmittedLightArray().isMapped() ? 1 : 0)
                            + (section.getSkyLightArray().isMapped() ? 1 : 0);
                    mappedLights += count;
                    if (!section.isEmpty()) includedMappedLights += count;
                }
            }
            // Verify all packet bytes once, outside both warm-up and measurement.
            CRC32 crc = new CRC32();
            for (Chunk chunk : chunks) {
                PacketPlayOutMapChunk.ChunkMap data = PacketPlayOutMapChunk.getExtractedData(chunk, true, true, 0xFFFF);
                crc.update(data.dataSize); crc.update(data.dataSize >>> 8); crc.update(data.data);
            }
            System.out.printf("META,chunks=%d,mappedLights=%d,includedMappedLights=%d,crc32=%08x%n",
                    chunks.size(), mappedLights, includedMappedLights, crc.getValue());
            for (int run = 0; run < 5; ++run) extract(chunks);
            for (int run = 1; run <= 3; ++run) {
                long allocated = bean.getThreadAllocatedBytes(thread), started = System.nanoTime();
                extract(chunks);
                long elapsed = System.nanoTime() - started, bytes = bean.getThreadAllocatedBytes(thread) - allocated;
                System.out.printf("RESULT,packet,%d,%.3f,%d,%d%n", run, elapsed / 1e6, bytes, chunks.size());
            }
        } finally {
            loader.close();
        }
    }

    private static void extract(List<Chunk> chunks) {
        for (Chunk chunk : chunks) {
            sink = PacketPlayOutMapChunk.getExtractedData(chunk, true, true, 0xFFFF);
        }
    }
}
