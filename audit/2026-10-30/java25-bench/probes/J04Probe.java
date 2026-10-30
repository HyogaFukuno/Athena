package net.minecraft.server;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import io.netty.channel.embedded.EmbeddedChannel;

/**
 * J04: what reaches the wire when a packet throws part way through writePacketData, and whether a
 * client reading that stream still gets the next packet.
 *
 * A team packet (action 0) with a null among its player names - a plugin's mistake - writes its name,
 * mode, display, prefix, suffix, flags, visibility, colour and player count, then throws on the null.
 * A keep-alive follows it.
 * The stream is then read the way the 1.8 client reads it: frame length, data length (when
 * compressed), packet id, and the packet's own readPacketData.
 */
public class J04Probe {

    public static void main(String[] args) throws Exception {
        DispenserRegistry.register();
        for (int threshold : new int[] { -1, 256 }) {
            EmbeddedChannel channel = new EmbeddedChannel();

            channel.attr(NetworkManager.attrKeyConnectionState).set(EnumProtocol.PLAY);
            channel.pipeline().addLast("prepender", PacketPrepender.INSTANCE)
                    .addLast("encoder", new PacketEncoder(EnumProtocolDirection.CLIENTBOUND));
            if (threshold >= 0) {
                channel.pipeline().addBefore("encoder", "compress", new PacketCompressor(threshold));
            }

            PacketPlayOutScoreboardTeam team = new PacketPlayOutScoreboardTeam();
            java.lang.reflect.Field players = PacketPlayOutScoreboardTeam.class.getDeclaredField("players");

            players.setAccessible(true);
            ((java.util.Collection<String>) players.get(team)).add(null);
            channel.writeOutbound(team);
            ByteBuf bad = drain(channel);
            int badBytes = bad.readableBytes();
            channel.writeOutbound(new PacketPlayOutKeepAlive(42));
            ByteBuf wire = Unpooled.wrappedBuffer(bad, drain(channel));
            int frames = 0, broken = 0;
            String firstPacket = null;
            boolean keepAliveRead = false;

            while (wire.isReadable()) {
                PacketDataSerializer in = new PacketDataSerializer(wire);
                int length = in.readVarIntFromBuffer();
                PacketDataSerializer frame = new PacketDataSerializer(wire.readSlice(length));

                ++frames;
                if (threshold >= 0) {
                    frame.readVarIntFromBuffer(); // data length: 0, all of these are under the threshold
                }
                if (!frame.isReadable()) {
                    continue; // an empty frame: the 1.8 client skips it
                }
                int id = frame.readVarIntFromBuffer();
                Packet packet = EnumProtocol.PLAY.getPacket(EnumProtocolDirection.CLIENTBOUND, id);

                if (firstPacket == null) {
                    firstPacket = packet.getClass().getSimpleName();
                }
                try {
                    packet.readPacketData(frame);
                    if (frame.isReadable()) {
                        throw new IllegalStateException(frame.readableBytes() + " bytes left over");
                    }
                    keepAliveRead |= packet instanceof PacketPlayOutKeepAlive;
                } catch (Exception e) {
                    ++broken;
                    System.out.println("  client fails on " + packet.getClass().getSimpleName() + ": " + e);
                }
            }
            System.out.println("RESULT,threshold=" + threshold + ",bad_packet_bytes=" + badBytes + ",frames=" + frames
                    + ",broken_frames=" + broken + ",first_packet=" + firstPacket + ",keepalive_read=" + keepAliveRead);
            channel.finishAndReleaseAll();
        }
    }

    private static ByteBuf drain(EmbeddedChannel channel) {
        ByteBuf all = Unpooled.buffer();
        ByteBuf next;

        while ((next = channel.readOutbound()) != null) {
            all.writeBytes(next);
            next.release();
        }
        return all;
    }
}
