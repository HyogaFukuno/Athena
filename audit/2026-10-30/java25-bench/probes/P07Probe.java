package net.minecraft.server;

import java.util.ArrayList;
import java.util.List;

/**
 * P07: two of the small costs, in isolation.
 *
 * "packetid": EnumProtocol.getPacketId for a mix of twelve outbound packet classes, as PacketEncoder
 * calls it once per packet sent. Prints RESULT,packetid,<round>,<ns per lookup>.
 *
 * "removal": the tick loop's removal of dead entities, ArrayList.remove(index) inside the walk, for
 * a world of N entities of which K die in one tick (the end of a match: drops and arrows), against a
 * single compacting pass. Prints RESULT,removal,<n>,<k>,<remove-in-walk us>,<compacting us>.
 */
public class P07Probe {
    static volatile Object sink;

    public static void main(String[] args) throws Exception {
        if (args[0].equals("packetid")) {
            Packet[] packets = {
                    new PacketPlayOutEntity.PacketPlayOutRelEntityMove(), new PacketPlayOutEntity.PacketPlayOutEntityLook(), new PacketPlayOutEntity.PacketPlayOutRelEntityMoveLook(),
                    new PacketPlayOutEntityHeadRotation(), new PacketPlayOutEntityVelocity(), new PacketPlayOutEntityMetadata(),
                    new PacketPlayOutKeepAlive(), new PacketPlayOutMapChunk(), new PacketPlayOutBlockChange(),
                    new PacketPlayOutChat(), new PacketPlayOutUpdateTime(), new PacketPlayOutEntityTeleport() };
            int calls = 20_000_000;
            long sum = 0;

            for (int i = 0; i < calls; ++i) {
                sum += EnumProtocol.PLAY.getPacketId(EnumProtocolDirection.CLIENTBOUND, packets[i % packets.length]);
            }
            for (int round = 1; round <= 5; ++round) {
                long start = System.nanoTime();

                for (int i = 0; i < calls; ++i) {
                    sum += EnumProtocol.PLAY.getPacketId(EnumProtocolDirection.CLIENTBOUND, packets[i % packets.length]);
                }
                System.out.printf("RESULT,packetid,%d,%.2f%n", round, (double) (System.nanoTime() - start) / calls);
            }
            sink = sum;
            return;
        }
        for (int[] nk : new int[][] { { 500, 100 }, { 2000, 500 }, { 5000, 1500 } }) {
            int n = nk[0], k = nk[1];
            long a = Long.MAX_VALUE, b = Long.MAX_VALUE;

            for (int rep = 0; rep < 200; ++rep) {
                List<Object> list = fill(n);
                boolean[] dead = deaths(n, k, rep);
                long start = System.nanoTime();

                for (int i = 0; i < list.size(); ++i) {           // as World.tickEntities
                    if (dead[((Integer) list.get(i))]) {
                        list.remove(i--);
                    }
                }
                a = Math.min(a, System.nanoTime() - start);
                sink = list;

                list = fill(n);
                start = System.nanoTime();
                int w = 0;
                for (int i = 0, size = list.size(); i < size; ++i) {  // one compacting pass
                    Object e = list.get(i);
                    if (!dead[((Integer) e)]) {
                        list.set(w++, e);
                    }
                }
                list.subList(w, list.size()).clear();
                b = Math.min(b, System.nanoTime() - start);
                sink = list;
            }
            System.out.printf("RESULT,removal,%d,%d,%.1f,%.1f%n", n, k, a / 1000.0, b / 1000.0);
        }
    }

    private static List<Object> fill(int n) {
        List<Object> list = new ArrayList<Object>(n);
        for (int i = 0; i < n; ++i) list.add(Integer.valueOf(i));
        return list;
    }

    private static boolean[] deaths(int n, int k, int seed) {
        boolean[] dead = new boolean[n];
        java.util.Random random = new java.util.Random(seed);
        for (int c = 0; c < k; ) {
            int i = random.nextInt(n);
            if (!dead[i]) { dead[i] = true; ++c; }
        }
        return dead;
    }
}
