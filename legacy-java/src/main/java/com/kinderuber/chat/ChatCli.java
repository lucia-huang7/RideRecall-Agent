package com.kinderuber.chat;

import com.kinderuber.api.dto.ChatRequest;
import org.springframework.boot.*;
import org.springframework.stereotype.Component;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

@Component
public class ChatCli implements ApplicationRunner {
    private final ChatService chats;
    public ChatCli(ChatService chats) { this.chats = chats; }
    @Override public void run(ApplicationArguments arguments) throws Exception {
        if (!arguments.containsOption("cli")) return;
        String conversation = UUID.randomUUID().toString();
        System.out.println("KinderUber CLI（/new 新会话，/quit 退出）");
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        for (String line; (line = input.readLine()) != null;) {
            if (line.strip().equals("/quit")) break;
            if (line.strip().equals("/new")) { conversation = UUID.randomUUID().toString(); continue; }
            if (line.isBlank()) continue;
            try { System.out.println(chats.chat(new ChatRequest(line, "cli", conversation, UUID.randomUUID().toString()), null).response()); }
            catch (Exception ex) { System.out.println("本轮失败：" + ex.getMessage()); }
        }
    }
}
