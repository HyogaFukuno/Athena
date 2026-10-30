package net.minecraft.server;

import io.netty.channel.embedded.EmbeddedChannel;
import java.lang.management.ManagementFactory;

/**
 * The bytes one NetworkManager.handle(packet) allocates, through the real pipeline (prepender,
 * encoder, compressor, packet handler) on an EmbeddedChannel, encoding included. A relative-move
 * packet, the kind the entity tracker sends most. Prints RESULT,<bytes per send>.
 */
public class SendAllocProbe {
    public static void main(String[] args) throws Exception {
        DispenserRegistry.register();
        // NetworkManager reads the packet limiter settings: the defaults, without a server
        java.lang.reflect.Field config = com.hpfxd.pandaspigot.config.PandaSpigotConfig.class.getDeclaredField("config");
        config.setAccessible(true);
        java.lang.reflect.Constructor<com.hpfxd.pandaspigot.config.PandaSpigotConfig> make = com.hpfxd.pandaspigot.config.PandaSpigotConfig.class.getDeclaredConstructor();
        make.setAccessible(true);
        config.set(null, make.newInstance());
        com.sun.management.ThreadMXBean mx = (com.sun.management.ThreadMXBean) ManagementFactory.getThreadMXBean();
        NetworkManager manager = new NetworkManager(EnumProtocolDirection.CLIENTBOUND);
        EmbeddedChannel channel = new EmbeddedChannel();
        channel.attr(NetworkManager.attrKeyConnectionState).set(EnumProtocol.PLAY);
        channel.pipeline().addLast("prepender", PacketPrepender.INSTANCE)
                .addLast("encoder", new PacketEncoder(EnumProtocolDirection.CLIENTBOUND))
                .addLast("packet_handler", manager);
        channel.pipeline().addBefore("encoder", "compress", new PacketCompressor(256));
        channel.pipeline().fireChannelActive();
        Packet packet = new PacketPlayOutEntity.PacketPlayOutRelEntityMove(1, (byte) 1, (byte) 0, (byte) 1, true);
        int n = 200_000;
        for (int round = 0; round < 6; ++round) {
            long before = mx.getCurrentThreadAllocatedBytes();
            for (int i = 0; i < n; ++i) {
                manager.handle(packet);
                if ((i & 63) == 63) {
                    channel.flushOutbound();
                    Object out;
                    while ((out = channel.readOutbound()) != null) io.netty.util.ReferenceCountUtil.release(out);
                }
            }
            if (round > 0) System.out.printf("RESULT,%.1f%n", (double) (mx.getCurrentThreadAllocatedBytes() - before) / n);
        }
    }
}
