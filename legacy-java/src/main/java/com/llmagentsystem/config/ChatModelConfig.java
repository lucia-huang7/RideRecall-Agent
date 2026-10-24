package com.llmagentsystem.config;

import dev.langchain4j.model.anthropic.AnthropicChatModel;
import dev.langchain4j.model.chat.ChatModel;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.*;
import java.net.URI;
import java.time.Duration;

@Configuration
public class ChatModelConfig {
    @Bean
    @Profile("anthropic & !deepseek")
    public ChatModel anthropicChatModel(
            @Value("${langchain4j.anthropic.chat-model.api-key:}") String key,
            @Value("${langchain4j.anthropic.chat-model.base-url:https://api.anthropic.com}") String baseUrl,
            @Value("${langchain4j.anthropic.chat-model.model-name:claude-sonnet-4-6}") String model,
            @Value("${llmagentsystem.llm.timeout-seconds:30}") long timeout) {
        if (!hasKey(key)) return null;
        validateAnthropicModel(baseUrl, model);
        return AnthropicChatModel.builder().apiKey(key.strip()).baseUrl(apiBase(baseUrl)).modelName(model)
                .timeout(Duration.ofSeconds(timeout)).maxRetries(0).logRequests(false).logResponses(false).build();
    }

    @Bean
    @Profile("deepseek")
    public ChatModel deepSeekChatModel(
            @Value("${langchain4j.deepseek.chat-model.api-key:}") String key,
            @Value("${langchain4j.deepseek.chat-model.base-url:https://api.deepseek.com/anthropic}") String baseUrl,
            @Value("${langchain4j.deepseek.chat-model.model-name:deepseek-flash}") String model,
            @Value("${llmagentsystem.llm.timeout-seconds:30}") long timeout) {
        if (!hasKey(key)) return null;
        return AnthropicChatModel.builder().apiKey(key.strip()).baseUrl(apiBase(baseUrl)).modelName(model)
                .thinkingType("disabled").timeout(Duration.ofSeconds(timeout)).maxRetries(0).logRequests(false).logResponses(false).build();
    }

    static boolean hasKey(String key) { return key != null && !key.isBlank() && !key.strip().equals("llmagentsystem-local-placeholder"); }
    static void validateAnthropicModel(String baseUrl, String model) {
        if (model == null || model.isBlank()) throw new IllegalArgumentException("ANTHROPIC_MODEL must not be blank");
        if ("api.anthropic.com".equalsIgnoreCase(URI.create(baseUrl.strip()).getHost())
                && java.util.Set.of("claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20240620", "claude-3-5-sonnet-latest").contains(model))
            throw new IllegalArgumentException("Configured Claude Sonnet 3.5 model is retired; update ANTHROPIC_MODEL");
    }
    static String apiBase(String value) {
        URI uri = URI.create(value.strip());
        if (uri.getHost() == null || !("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())))
            throw new IllegalArgumentException("LLM base URL must be HTTP(S)");
        String base = value.strip().replaceAll("/+$", "");
        // Accept the former official OpenAI root while migrating existing .env files.
        if (uri.getHost().equalsIgnoreCase("api.deepseek.com")
                && (uri.getPath() == null || uri.getPath().isEmpty() || uri.getPath().equals("/") || uri.getPath().equals("/v1")))
            base = uri.getScheme() + "://" + uri.getRawAuthority() + "/anthropic";
        return base.endsWith("/v1") ? base : base + "/v1";
    }
}
