package net.minecraft.server;

import com.sun.management.ThreadMXBean;
import java.io.File;
import java.lang.management.ManagementFactory;
import java.lang.reflect.*;
import java.util.*;
import java.util.zip.CRC32;

/** Fresh JVM per variant, identical heap/flags. Loading and warm-up are outside the measurements. */
public class AllocationProbe {
    private static volatile Object sink;
    private static final ThreadMXBean ALLOCATION = (ThreadMXBean) ManagementFactory.getThreadMXBean();
    private static final long THREAD = Thread.currentThread().threadId();
    private static String mapProfile;
    private static TileEntity tile;
    private static Method snapshot;
    private static boolean ownedSnapshot;

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        ALLOCATION.setThreadAllocatedMemoryEnabled(true);
        DispenserRegistry.register();
        if (args[0].startsWith("maps")) {
            mapProfile = args[0];
            tile = new TileEntityChest();
            for (int i = 0; i < 5; ++i) newChunks();
            for (int run = 1; run <= 5; ++run) measure(mapProfile, run, AllocationProbe::newChunks);
            return;
        }
        WorldServer world = AthenaAuditFixtures.world();
        AthenaAuditFixtures.set(world, World.class, "worldProvider", WorldProvider.byDimension(0));
        File directory = new File(args[1]);
        AthenaSectionStore store = AthenaSectionStore.open(directory);
        if (store == null) throw new IllegalStateException("valid prebuilt store required");
        Field keysField = AthenaSectionStore.class.getDeclaredField("keys");
        keysField.setAccessible(true);
        long[] keys = (long[]) keysField.get(store);
        List<Chunk> chunks = new ArrayList<>();
        try { snapshot = AthenaDeferredSectionData.class.getDeclaredMethod("attach", NBTTagCompound.class, ChunkSection.class); ownedSnapshot = true; }
        catch (NoSuchMethodException oldVersion) { snapshot = AthenaDeferredSectionData.class.getDeclaredMethod("attach", NBTTagCompound.class, char[].class); }
        try (LoaderClose loader = new LoaderClose(directory)) {
            int limit = args.length > 2 ? Integer.parseInt(args[2]) : keys.length;
            for (long key : keys) {
                if (chunks.size() >= limit) break;
                Object[] loaded = loader.value.loadChunk(world, (int) (key >> 32), (int) key);
                if (loaded == null) throw new IllegalStateException("missing chunk");
                chunks.add((Chunk) loaded[0]);
            }
            System.out.println("META,chunks=" + chunks.size() + ",ownedSnapshot=" + ownedSnapshot);
            if (args[0].equals("compress")) {
                // Retain the same extracted payloads, excluding extraction from the compression benchmark.
                List<PacketPlayOutMapChunk.ChunkMap> maps = new ArrayList<>();
                for (Chunk c : chunks) maps.add(PacketPlayOutMapChunk.getExtractedData(c, true, true, 0xFFFF));
                CRC32 crc = new CRC32();
                for (int i = 0; i < maps.size(); ++i) {
                    byte[] frame = new AthenaChunkPacketCache(chunks.get(i).locX, chunks.get(i).locZ, maps.get(i)).frame(0x21, 256);
                    if (frame != null) crc.update(frame);
                }
                System.out.printf("META,compressedCRC32=%08x%n", crc.getValue());
                Runnable task = () -> {
                    for (int i = 0; i < maps.size(); ++i) sink = new AthenaChunkPacketCache(chunks.get(i).locX, chunks.get(i).locZ, maps.get(i)).frame(0x21, 256);
                };
                for (int i = 0; i < 2; ++i) task.run();
                for (int run = 1; run <= 3; ++run) measure("compress", run, task);
            } else if (args[0].equals("save")) {
                Runnable task = () -> {
                    for (Chunk c : chunks) for (ChunkSection section : c.getSections()) {
                        if (section == null) continue;
                        NBTTagCompound tags = new NBTTagCompound();
                        try { snapshot.invoke(null, tags, ownedSnapshot ? section : section.getIdArray()); }
                        catch (ReflectiveOperationException e) { throw new IllegalStateException(e); }
                        sink = tags;
                    }
                };
                for (int i = 0; i < 5; ++i) task.run();
                for (int run = 1; run <= 3; ++run) measure("save", run, task);
            } else throw new IllegalArgumentException("maps | compress | save");
        }
    }

    private static void newChunks() {
        Chunk[] chunks = new Chunk[1200];
        for (int i = 0; i < chunks.length; ++i) {
            Chunk chunk = new Chunk(null, i, 0);
            chunks[i] = chunk;
            int tiles = mapProfile.equals("maps-dense") ? 64 : mapProfile.equals("maps-mixed") && i % 10 == 0 ? 8 : 0;
            for (int key = 0; key < tiles; ++key) chunk.tileEntities.put(key, tile);
            if (mapProfile.equals("maps-dense") || mapProfile.equals("maps-mixed") && i % 100 == 0) {
                for (Class<?> type : new Class<?>[] {EntityAnimal.class, EntityMonster.class, EntityWaterAnimal.class, EntityAmbient.class}) chunk.entityCount.addTo(type, 1);
            }
        }
        sink = chunks;
    }

    private static void measure(String mode, int run, Runnable task) {
        long bytes = ALLOCATION.getThreadAllocatedBytes(THREAD), start = System.nanoTime();
        task.run();
        long nanos = System.nanoTime() - start;
        bytes = ALLOCATION.getThreadAllocatedBytes(THREAD) - bytes;
        System.out.printf("RESULT,%s,%d,%.3f,%d%n", mode, run, nanos / 1e6, bytes);
    }

    private static class LoaderClose implements AutoCloseable {
        final AthenaTemplateChunkLoader value;
        LoaderClose(File directory) { value = new AthenaTemplateChunkLoader(directory); }
        public void close() { value.close(); }
    }
}
