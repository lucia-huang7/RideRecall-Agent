package com.kinderuber.llm;

/** Text-only auxiliary tasks. Agent conversations use AgentAiService and native ChatModel messages. */
public interface LlmGateway {
    String chat(String systemPrompt, String userPrompt, double temperature, int maxTokens);
    default String chatRequired(String system, String user, double temperature, int tokens) { return chat(system, user, temperature, tokens); }
    default String chatRequired(String system, String user, double temperature, int tokens, String model) {
        return judgeChat(system, user, temperature, tokens, model);
    }
    default String judgeChat(String system, String user, double temperature, int tokens, String model) { return chatRequired(system, user, temperature, tokens); }
}
