package net.minecraft.server;

import java.nio.file.Files;
import java.nio.file.Paths;
import org.bukkit.configuration.file.YamlConfiguration;

/**
 * J05: the library paths a dependency update can make faster or slower.
 * "yaml": YamlConfiguration load + saveToString of the live spigot.yml (SnakeYAML).
 * "chat": IChatBaseComponent to JSON and back for a coloured, nested message (Gson), as chat,
 * titles and scoreboard packets do.  Prints RESULT,<mode>,<round>,<us or ns per op>.
 */
public class J05Probe {
    static volatile Object sink;

    public static void main(String[] args) throws Exception {
        if (args[0].equals("yaml")) {
            String text = new String(Files.readAllBytes(Paths.get(args[1])), "UTF-8");
            int ops = 2000;
            for (int i = 0; i < ops; ++i) sink = yaml(text);
            for (int round = 1; round <= 5; ++round) {
                long start = System.nanoTime();
                for (int i = 0; i < ops; ++i) sink = yaml(text);
                System.out.printf("RESULT,yaml,%d,%.2f%n", round, (System.nanoTime() - start) / 1000.0 / ops);
            }
            return;
        }
        ChatComponentText message = new ChatComponentText("[");
        message.getChatModifier().setColor(EnumChatFormat.DARK_GRAY);
        ChatComponentText name = new ChatComponentText("Player_Name");
        name.getChatModifier().setColor(EnumChatFormat.YELLOW).setBold(true);
        message.addSibling(name);
        message.addSibling(new ChatComponentText("] "));
        ChatComponentText body = new ChatComponentText("has been eliminated by Another_Player with a Diamond Sword!");
        body.getChatModifier().setColor(EnumChatFormat.GRAY);
        message.addSibling(body);
        int ops = 1_000_000;
        for (int i = 0; i < ops; ++i) sink = IChatBaseComponent.ChatSerializer.jsonToComponent(IChatBaseComponent.ChatSerializer.componentToJson(message));
        for (int round = 1; round <= 5; ++round) {
            long start = System.nanoTime();
            for (int i = 0; i < ops; ++i) sink = IChatBaseComponent.ChatSerializer.jsonToComponent(IChatBaseComponent.ChatSerializer.componentToJson(message));
            System.out.printf("RESULT,chat,%d,%.1f%n", round, (double) (System.nanoTime() - start) / ops);
        }
    }

    private static String yaml(String text) throws Exception {
        YamlConfiguration config = new YamlConfiguration();
        config.loadFromString(text);
        return config.saveToString();
    }
}
